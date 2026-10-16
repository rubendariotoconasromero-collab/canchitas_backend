import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.db import close_old_connections
from django.db import transaction
from django.utils import timezone
from caja.tests import CashFixtures
from caja import services as cash
from reservas.models import Reserva, SolicitudReserva
from users.models import User, Rol, Permiso, RolPermiso, RolPermisoUsuario
from auditoria.models import EventoAuditoria
from auditoria import services as audit


class ReportsTests(CashFixtures, TestCase):
    def params(self, service=False, **extra):
        day = (self.start if service else timezone.now()).astimezone(timezone.get_default_timezone()).date().isoformat()
        return {'sucursal': self.branch.pk, 'desde': day, 'hasta': day, **extra}

    def get(self, kind, service=False, **extra):
        return self.client.get('/api/reportes/' + kind + '/', self.params(service, **extra))

    def local_user(self, *codes):
        user = User.objects.create_user('reports-local@example.test', 'test', nombre='Operador local', sucursal=self.branch)
        role = Rol.objects.create(nombre='Reportes local')
        for code in codes:
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            link = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=user, rol_permiso=link)
        return user

    def test_payment_not_triplicated_and_survives_cancellation(self):
        cash.charge(self.user, self.payment())
        Reserva.objects.filter(pk=self.reservation.pk).update(estado='CANCELADA')
        result = self.get('cobros')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['resumen']['cobrado'], '100.00')
        self.assertEqual(result.data['count'], 1)
        self.assertEqual(result.data['results'][0]['estado_reserva'], 'CANCELADA')
        self.assertEqual(self.get('reservas', True).data['resumen']['por_estado']['CANCELADA'], '100.00')

    def test_expired_effective_state_does_not_mutate_reservation(self):
        Reserva.objects.filter(pk=self.reservation.pk).update(retencion_hasta=timezone.now()-timedelta(minutes=1))
        response = self.get('reservas', True, estado='EXPIRADA')
        self.assertEqual(response.data['count'], 1)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.estado, 'PENDIENTE')
        self.assertEqual(self.get('ocupacion', True).data['count'], 0)

    def test_hours_clipped_at_midnight_without_historical_percentage(self):
        cash.charge(self.user, self.payment())
        detail = self.reservation.detalle
        start = timezone.localtime(self.start).replace(hour=23, minute=30)
        detail.inicio, detail.fin = start, start + timedelta(hours=2)
        detail.save()
        response = self.get('ocupacion', True)
        self.assertEqual(response.data['resumen']['horas_confirmadas'], '0.50')
        self.assertNotIn('porcentaje', response.data['resumen'])
        self.assertEqual(self.get('reservas', True).data['results'][0]['total'], '100.00')

    def test_cash_close_stored_values_exclude_qr_and_separate_manual_movements(self):
        cash.charge(self.user, self.payment(medio=self.qr.pk, recibido=Decimal(100), verificado=True, referencia='REPORTE-QR'))
        cash.manual_movement(self.user, self.session.pk, 'INGRESO', Decimal(20), 'Fondo')
        cash.manual_movement(self.user, self.session.pk, 'RETIRO', Decimal(10), 'Retiro')
        cash.close_session(self.user, self.session.pk, Decimal(58), 'Diferencia')
        response = self.get('cierres')
        row = response.data['results'][0]
        self.assertEqual((row['esperado'], row['contado'], row['diferencia']), ('60.00', '58.00', '-2.00'))
        self.assertEqual(row['cobros_electronicos'], '100.00')
        self.assertEqual(row['ingresos_manuales'], '20.00')

    def test_required_permissions_branch_and_ownership(self):
        local = self.local_user('reportes.ver', 'reportes.cobros', 'reportes.cierres', 'avisos.ver')
        cash.charge(self.user, self.payment())
        cash.close_session(self.user, self.session.pk, Decimal(149), 'Conteo')
        self.client.force_authenticate(local)
        self.assertEqual(self.get('cobros').data['count'], 0)
        self.assertEqual(self.get('cierres').data['count'], 0)
        self.assertEqual(self.get('reservas', True).status_code, 403)
        self.assertEqual(self.get('cobros', sucursal=self.other.pk).status_code, 403)
        self.assertEqual(self.get('cobros', operador=self.user.pk).status_code, 400)
        self.assertEqual(self.get('cobros/exportar').status_code, 403)
        self.assertEqual(self.client.get('/api/auditoria/', self.params()).status_code, 403)
        self.assertEqual(self.client.get('/api/avisos/').data['count'], 0)
        # A specialized permission alone never grants access to the reports module.
        RolPermisoUsuario.objects.filter(user=local, rol_permiso__permiso__nombre='reportes.ver').delete()
        self.assertEqual(self.get('cobros').status_code, 403)

    def test_operator_differs_from_session_owner(self):
        local = self.local_user('reportes.ver', 'reportes.cobros', 'caja.cobrar')
        cash.charge(local, self.payment())
        self.client.force_authenticate(local)
        response = self.get('cobros')
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['operador'], local.nombre)
        self.assertEqual(row['responsable'], self.user.nombre)

    def test_export_all_rows_bom_formula_protection_and_audit(self):
        detail = self.reservation.detalle
        detail.cancha_nombre = '=HYPERLINK("x")'
        detail.save()
        response = self.get('reservas/exportar', True, page_size=1)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8-sig')
        rows = list(csv.reader(io.StringIO(content)))
        self.assertTrue(rows[1][3].startswith("'="))
        event = EventoAuditoria.objects.get(entidad='Reporte')
        self.assertEqual(event.nuevo['registros'], 1)
        self.assertEqual(event.accion, 'exportar')

    def test_limits_and_invalid_filters(self):
        for data in [{'hasta': '2020-01-01'}, {'page': 2}, {'page_size': 101},
                     {'desde': 'invalid'}, {'estado': 'INVALIDO'}, {'cancha': 'x'}, {'extra': 1}]:
            self.assertEqual(self.get('reservas', True, **data).status_code, 400, data)
        with override_settings(REPORT_MAX_DAYS=1):
            self.assertEqual(self.get('reservas', True, hasta=(self.start.date()+timedelta(days=2)).isoformat()).status_code, 400)
        with override_settings(REPORT_MAX_ROWS=0):
            self.assertEqual(self.get('reservas/exportar', True).status_code, 400)

    def test_options_scope_and_readonly_audit(self):
        result = self.client.get('/api/reportes/opciones/', {'sucursal': self.branch.pk})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn(self.user.pk, [u['id'] for u in result.data['operadores']])
        response = self.client.get('/api/auditoria/', self.params())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.post('/api/auditoria/', {}).status_code, 405)

    def test_export_includes_all_pages_and_quotes_commas(self):
        from reservas import services as booking
        booking.retain(self.user, self.data(inicio=self.end, fin=self.end+timedelta(hours=1)))
        self.reservation.detalle.cancha_nombre = 'Cancha, Norte'
        self.reservation.detalle.save()
        response = self.get('reservas', True, page_size=1)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 1)
        exported = self.get('reservas/exportar', True, page_size=1)
        rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][3], 'Cancha, Norte')

    def test_audit_assignments_and_credentials_use_safe_changes(self):
        role = Rol.objects.create(nombre='Rol auditado')
        permission, _ = Permiso.objects.get_or_create(nombre='clientes.ver')
        relation = RolPermiso.objects.create(rol=role, permiso=permission)
        local = self.local_user('dashboard.ver')
        url = f'/api/usuarios/{local.pk}/asignaciones/sync/'
        result = self.client.post(url, {'rol_permiso_ids': [relation.pk]}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        event = EventoAuditoria.objects.get(entidad='User', accion='asignar_accesos')
        self.assertEqual(event.nuevo['accesos'], [relation.pk])
        repeated = self.client.post(url, {'rol_permiso_ids': [relation.pk]}, format='json')
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(EventoAuditoria.objects.filter(accion='asignar_accesos').count(), 1)
        result = self.client.patch(f'/api/usuarios/{local.pk}/', {'password': 'new-strong-secret-123'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        event = EventoAuditoria.objects.get(accion='actualizar_credencial')
        self.assertEqual(event.nuevo, {'credencial_actualizada': True})

    def test_revoked_role_removes_reports_and_notice_access(self):
        local = self.local_user('reportes.ver', 'reportes.reservas', 'avisos.ver')
        Rol.objects.filter(nombre='Reportes local').update(estado=False)
        self.client.force_authenticate(local)
        self.assertEqual(self.get('reservas', True).status_code, 403)
        self.assertEqual(self.client.get('/api/avisos/').status_code, 403)

    def test_report_and_notices_have_no_per_record_queries(self):
        from reservas import services as booking
        with CaptureQueriesContext(connection) as before:
            self.get('reservas', True)
        for offset in range(1, 5):
            booking.retain(self.user, self.data(inicio=self.start+timedelta(hours=offset), fin=self.end+timedelta(hours=offset)))
        with CaptureQueriesContext(connection) as after:
            self.get('reservas', True)
        self.assertEqual(len(before), len(after))
        self.assertEqual(self.client.get('/api/avisos/').data['por_tipo']['pendientes'], 5)

    def test_notice_bound_and_foreign_branch_exclusion(self):
        from reservas import services as booking
        local = self.local_user('avisos.ver', 'reservas.ver')
        for index in range(1, 26):
            booking.retain(self.user, self.data(inicio=self.start+timedelta(hours=index),
                                              fin=self.end+timedelta(hours=index)))
        response = self.client.get('/api/avisos/')
        self.assertEqual(response.data['por_tipo']['pendientes'], 26)
        self.assertEqual(response.data['count'], 26)
        self.assertEqual(len(response.data['results']), 20)
        self.assertTrue(response.data['limitado'])
        self.client.force_authenticate(local)
        self.assertEqual(self.client.get('/api/avisos/', {'sucursal': self.other.pk}).status_code, 403)

    def test_tariff_and_policy_audit_and_invalid_court_edit(self):
        from catalogo.models import TarifaCancha
        tariff = TarifaCancha.objects.filter(cancha_deporte=self.link).first()
        result = self.client.patch(f'/api/catalogo/tarifas/{tariff.pk}/', {'precio_hora': '130.00'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(EventoAuditoria.objects.get(entidad='TarifaCancha').nuevo['precio_hora'], '130.00')
        result = self.client.patch(f'/api/catalogo/politicas/{self.policy.pk}/', {
            'sucursal': self.branch.pk, 'minutos_retencion': 20}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(EventoAuditoria.objects.get(entidad='PoliticaReserva').nuevo['minutos_retencion'], 20)
        count = EventoAuditoria.objects.count()
        result = self.client.patch(f'/api/catalogo/canchas/{self.court.pk}/', {'publicada': True}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(EventoAuditoria.objects.count(), count)

    def test_notice_counts_bounded_and_manually_refreshed(self):
        response = self.client.get('/api/avisos/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['por_tipo']['pendientes'], 1)
        Reserva.objects.filter(pk=self.reservation.pk).update(retencion_hasta=timezone.now()-timedelta(seconds=1))
        updated = self.client.get('/api/avisos/').data
        self.assertEqual(updated['por_tipo']['pendientes'], 0)
        self.assertEqual(updated['por_tipo']['vencidas'], 1)

    def test_audit_rolls_back_and_charge_replay_does_not_duplicate(self):
        data = self.payment()
        cash.charge(self.user, data)
        cash.charge(self.user, data)
        self.assertEqual(EventoAuditoria.objects.filter(accion='cobrar').count(), 1)
        count = EventoAuditoria.objects.count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                audit.changed(self.user, self.box, {}, 'crear')
                raise RuntimeError('Abortar')
        self.assertEqual(EventoAuditoria.objects.count(), count)
        with patch('auditoria.services.record', side_effect=RuntimeError('No guardar')):
            with self.assertRaises(RuntimeError):
                cash.manual_movement(self.user, self.session.pk, 'INGRESO', Decimal(1), 'Fallo')
        self.assertFalse(self.session.movimientos.filter(motivo='Fallo').exists())

    def test_user_role_permission_and_court_changes_are_audited_without_secrets(self):
        response = self.client.post('/api/usuarios/', {'correo': 'new-audit@example.test', 'nombre': 'Nuevo',
            'password': 'strong-password-123', 'sucursal': self.branch.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        event = EventoAuditoria.objects.get(entidad='User', accion='crear')
        self.assertNotIn('password', event.nuevo)
        self.assertNotIn('correo', event.nuevo)
        for url, payload, entity in [('/api/roles/', {'nombre': 'Nuevo rol'}, 'Rol'),
                                     ('/api/permisos/', {'nombre': 'nuevo.permiso'}, 'Permiso')]:
            created = self.client.post(url, payload, format='json')
            self.assertEqual(created.status_code, 201, created.data)
            self.assertTrue(EventoAuditoria.objects.filter(entidad=entity, accion='crear').exists())
        result = self.client.patch(f'/api/catalogo/canchas/{self.court.pk}/', {'nombre': 'Actualizada'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(EventoAuditoria.objects.get(entidad='Cancha').anterior['nombre'], 'Cancha reserva')


class ReportSnapshotTests(CashFixtures, TransactionTestCase):
    def test_repeatable_read_is_nonblocking_and_observes_one_snapshot(self):
        from .services import read_snapshot, build
        day = timezone.localtime().date().isoformat()
        params = {'sucursal': str(self.branch.pk), 'desde': day, 'hasta': day}
        data = self.payment()

        def writer():
            close_old_connections()
            try:
                cash.charge(User.objects.get(pk=self.user.pk), data)
            finally:
                close_old_connections()

        with read_snapshot():
            first, _, _ = build(self.user, 'cobros', params)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(writer).result(timeout=15)
            second, _, _ = build(self.user, 'cobros', params)
            self.assertEqual(first, [])
            self.assertEqual(second, [])
        third, summary, _ = build(self.user, 'cobros', params)
        self.assertEqual(len(third), 1)
        self.assertEqual(summary['cobrado'], '100.00')
