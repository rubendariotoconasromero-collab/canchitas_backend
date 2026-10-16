from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from caja.tests import CashFixtures
from caja import services
from reservas.models import Reserva
from users.models import User, Rol, Permiso, RolPermiso, RolPermisoUsuario
from users.models import Sucursal
from rest_framework.test import APIClient


class DashboardTests(CashFixtures, TestCase):
    def operator(self, *permissions):
        user = User.objects.create_user('dashboard@example.test', 'test', nombre='Operador', sucursal=self.branch)
        role = Rol.objects.create(nombre='Dashboard de prueba')
        for name in permissions:
            permission, _ = Permiso.objects.get_or_create(nombre=name)
            assignment = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=user, rol_permiso=assignment)
        self.client.force_authenticate(user)
        return user

    def get_summary(self, **params):
        return self.client.get('/api/dashboard/resumen/', {'sucursal':self.branch.pk, **params})

    def test_requires_dashboard_permission_and_limits_branches(self):
        self.operator('reservas.ver')
        self.assertEqual(self.get_summary().status_code, 403)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.get_summary(sucursal='invalid').status_code, 400)

    def test_consultation_cannot_read_cash_or_reservations(self):
        self.operator('dashboard.ver')
        response = self.get_summary()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['caja'])
        self.assertIsNone(response.data['reservas'])
        self.assertEqual(len(response.data['sucursales']), 1)
        self.assertEqual(self.get_summary(sucursal=self.other.pk).status_code, 404)

    def test_reception_only_receives_permitted_sections(self):
        self.operator('dashboard.ver', 'reservas.ver', 'reservas.crear')
        response = self.get_summary()
        self.assertEqual(response.data['perfil'], 'recepcion')
        self.assertIsNone(response.data['caja'])
        self.assertEqual(response.data['reservas']['pendientes_vigentes'], 1)

    def test_cashier_sees_own_collections_not_other_operators(self):
        services.charge(self.user, self.payment())
        operator = self.operator('dashboard.ver', 'caja.ver', 'caja.abrir')
        response = self.get_summary()
        self.assertEqual(response.data['perfil'], 'cajero')
        self.assertEqual(response.data['caja']['turnos'], [])
        self.assertIsNone(response.data['caja']['administracion'])
        self.assertEqual(response.data['caja']['mis_cobros_turnos_abiertos']['importe'], '0.00')
        self.session.responsable = operator
        self.session.save()
        response = self.get_summary()
        self.assertEqual(response.data['caja']['turnos'][0]['efectivo_esperado'], '150.00')
        self.assertEqual(response.data['caja']['mis_cobros_turnos_abiertos']['importe'], '0.00')

    def test_cashier_collections_and_closed_sessions(self):
        services.charge(self.user, self.payment())
        # Give the existing fixture operator a local cashier profile.
        self.user.is_superuser = False
        self.user.sucursal = self.branch
        self.user.save()
        role = Rol.objects.create(nombre='Cajero de prueba')
        for name in ['dashboard.ver', 'caja.ver', 'caja.abrir', 'caja.cerrar']:
            permission, _ = Permiso.objects.get_or_create(nombre=name)
            relation = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=self.user, rol_permiso=relation)
        response = self.get_summary()
        cash = response.data['caja']
        self.assertEqual(cash['mis_cobros_turnos_abiertos']['importe'], '100.00')
        self.assertEqual(cash['mis_cobros_turnos_abiertos']['por_medio'][0]['medio'], 'Efectivo')
        services.close_session(self.user, self.session.pk, Decimal('150'), '')
        response = self.get_summary()
        self.assertEqual(response.data['caja']['mis_cobros_turnos_abiertos']['cantidad'], 0)

    def test_admin_counts_paid_money_and_qr_does_not_inflate_cash(self):
        services.charge(self.user, self.payment(medio=self.qr.pk, recibido=Decimal('100'), verificado=True, referencia='QR-123'))
        response = self.get_summary()
        self.assertEqual(response.status_code, 200, response.data)
        cash = response.data['caja']
        self.assertEqual(cash['administracion']['cobros_hoy']['importe'], '100.00')
        self.assertEqual(cash['turnos'][0]['efectivo_esperado'], '50.00')
        self.assertEqual(cash['administracion']['cobros_hoy']['por_medio'][0]['medio'], 'QR')
        services.close_session(self.user, self.session.pk, Decimal('49'), 'Diferencia')
        response = self.get_summary()
        self.assertEqual(response.data['caja']['administracion']['cierres_con_diferencia'], 1)
        self.assertEqual(response.data['caja']['administracion']['diferencia_neta'], '-1.00')

    def test_expired_holds_excluded_without_mutating_reservations(self):
        self.reservation.retencion_hasta = timezone.now() - timedelta(seconds=1)
        self.reservation.save()
        response = self.get_summary()
        self.assertEqual(response.data['reservas']['pendientes_vigentes'], 0)
        self.assertEqual(response.data['reservas']['proximas'], [])
        self.assertEqual(Reserva.objects.get(pk=self.reservation.pk).estado, 'PENDIENTE')

    def test_empty_branch_has_real_zero_values_and_no_boxes_created(self):
        response = self.get_summary(sucursal=self.other.pk)
        self.assertEqual(response.data['caja']['cajas_disponibles'], 0)
        self.assertEqual(response.data['caja']['administracion']['cobros_hoy']['importe'], '0.00')
        self.assertEqual(response.data['reservas']['total_dia'], 0)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_day_counts_use_local_time_and_mark_expired_holds(self):
        now = datetime(2026, 10, 11, 16, tzinfo=datetime_timezone.utc)
        detail = self.reservation.detalle
        detail.inicio = now + timedelta(hours=1)
        detail.fin = now + timedelta(hours=2)
        detail.save()
        self.reservation.retencion_hasta = now - timedelta(seconds=1)
        self.reservation.save()
        with patch('users.dashboard.timezone.now', return_value=now):
            response = self.get_summary()
        self.assertEqual(response.data['reservas']['total_dia'], 1)
        self.assertEqual(response.data['reservas']['por_estado']['EXPIRADA'], 1)
        self.assertEqual(response.data['reservas']['por_estado']['PENDIENTE'], 0)

    def test_canceled_paid_reservations_keep_actual_collection_totals(self):
        services.charge(self.user, self.payment())
        from reservas.services import transition
        transition(self.user, self.reservation.pk, 'cancelar', 'Cambio de planes')
        response = self.get_summary()
        self.assertEqual(response.data['caja']['administracion']['cobros_hoy']['importe'], '100.00')

    def test_receipts_before_local_midnight_are_not_counted_today(self):
        receipt = services.charge(self.user, self.payment())
        now = datetime(2026, 10, 11, 16, tzinfo=datetime_timezone.utc)
        type(receipt).objects.filter(pk=receipt.pk).update(created_at=now.replace(hour=3))
        with patch('users.dashboard.timezone.now', return_value=now):
            response = self.get_summary()
        self.assertEqual(response.data['caja']['administracion']['cobros_hoy']['cantidad'], 0)

    def test_mixed_role_keeps_reservation_and_cash_sections(self):
        self.operator('dashboard.ver', 'caja.ver', 'caja.abrir', 'reservas.ver', 'reservas.crear')
        response = self.get_summary()
        self.assertEqual(response.data['perfil'], 'cajero')
        self.assertIsNotNone(response.data['caja'])
        self.assertIsNotNone(response.data['reservas'])

    def test_receipt_access_requires_ownership_and_branch(self):
        receipt = services.charge(self.user, self.payment())
        operator = self.operator('dashboard.ver', 'caja.ver', 'caja.abrir')
        self.assertEqual(self.client.get(f'/api/caja/recibos/{receipt.pk}/').status_code, 404)
        receipt.pago.registrado_por = operator
        receipt.pago.save(update_fields=['registrado_por'])
        self.assertEqual(self.client.get(f'/api/caja/recibos/{receipt.pk}/').status_code, 200)
        self.reservation.sucursal = self.other
        self.reservation.save()
        self.assertEqual(self.client.get(f'/api/caja/recibos/{receipt.pk}/').status_code, 404)

    def test_reception_payments_in_another_operators_turn_are_personal(self):
        operator = self.operator('dashboard.ver', 'reservas.ver', 'reservas.crear', 'caja.ver', 'caja.cobrar')
        receipt = services.charge(operator, self.payment())
        response = self.get_summary()
        cash = response.data['caja']
        self.assertEqual(response.data['perfil'], 'recepcion')
        self.assertEqual(cash['turnos'], [])
        self.assertEqual(cash['mis_cobros_turnos_abiertos']['importe'], '100.00')
        self.assertEqual(cash['ultimos_cobros'][0]['id'], receipt.pk)
        self.assertIsNone(cash['administracion'])
        self.assertEqual(response.data['reservas']['pendientes_vigentes'], 0)


class SeededDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        actor = User.objects.create_superuser('dashboard-seed@example.test', 'test')
        cls.branch, _ = Sucursal.objects.get_or_create(nombre='Sucursal Central')
        call_command('populate_business', branch_id=cls.branch.pk, actor_id=actor.pk, reset_access=True, stdout=StringIO())

    def test_actual_seeded_roles_and_destination_endpoints(self):
        client = APIClient()
        for email, profile in [('admin@gmail.com','administrador'), ('recepcion@gmail.com','recepcion'),
                               ('cajero1@gmail.com','cajero'), ('cajero2@gmail.com','cajero')]:
            with self.subTest(email=email):
                client.force_authenticate(User.objects.get(correo=email))
                response = client.get('/api/dashboard/resumen/')
                self.assertEqual(response.status_code, 200, response.data)
                data = response.data
                self.assertEqual(data['perfil'], profile)
                self.assertIsNotNone(data['reservas'])
                self.assertIsNotNone(data['caja'])
                self.assertEqual(data['reservas']['total_dia'], 0)
                self.assertEqual(data['caja']['cajas_disponibles'], 0)
                self.assertEqual(data['caja']['mis_cobros_turnos_abiertos']['cantidad'], 0)
                self.assertEqual(data['caja']['administracion'] is not None, profile=='administrador')
                for path in ['/api/caja/cajas/opciones/', '/api/reservas/reservas/opciones/',
                             '/api/reservas/reservas/agenda/?sucursal='+str(self.branch.pk)+'&fecha=2026-10-11',
                             '/api/reservas/clientes/?sucursal='+str(self.branch.pk)]:
                    result = client.get(path)
                    self.assertEqual(result.status_code, 200, (email, path, result.data))
                self.assertEqual(client.get('/api/usuarios/').status_code, 200 if profile=='administrador' else 403)
