from decimal import Decimal
from uuid import uuid4
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.test import TestCase, TransactionTestCase
from django.db import close_old_connections
from django.utils import timezone
from rest_framework.exceptions import APIException
from reservas.tests import Fixtures
from reservas import services as booking
from reservas.models import PagoReserva
from users.models import User
from .models import Caja, MedioPago, MovimientoCaja, ReciboPago
from . import services


class CashFixtures(Fixtures):
    def setUp(self):
        super().setUp()
        self.box = Caja.objects.create(sucursal=self.branch, nombre='Principal')
        self.cash, _ = MedioPago.objects.get_or_create(codigo='EFECTIVO', defaults={'nombre':'Efectivo','efectivo':True})
        self.qr, _ = MedioPago.objects.get_or_create(codigo='QR', defaults={'nombre':'QR','efectivo':False})
        self.session = services.open_session(self.user, self.box.pk, Decimal('50.00'))
        self.reservation = booking.retain(self.user, self.data())

    def payment(self, **changes):
        data = {'sesion':self.session.pk,'reserva':self.reservation.pk,'medio':self.cash.pk,
                'clave':uuid4(),'importe':Decimal('100.00'),'recibido':Decimal('120.00'),
                'referencia':'','verificado':False,'iniciar':False}
        data.update(changes)
        return data


class CashTests(CashFixtures, TestCase):
    def test_charge_and_replay_are_atomic_and_confirm_reservation(self):
        data = self.payment()
        receipt = services.charge(self.user, data)
        self.assertEqual(receipt.cambio, Decimal('20'))
        self.assertEqual(services.charge(self.user, data).pk, receipt.pk)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.estado, 'CONFIRMADA')
        self.assertEqual(PagoReserva.objects.count(), 1)
        self.assertEqual(MovimientoCaja.objects.count(), 1)
        self.assertEqual(services.summary(self.session)['esperado'], '150.00')
        with self.assertRaises(APIException): services.charge(self.user, self.payment())
        altered = {**data,'recibido':Decimal('130')}
        with self.assertRaises(APIException): services.charge(self.user, altered)

    def test_partial_and_unverified_electronic_payments_are_rejected(self):
        for changes in [{'importe':Decimal('90')},{'importe':Decimal('110')},
                        {'recibido':Decimal('80')},{'medio':self.qr.pk}]:
            with self.assertRaises(APIException): services.charge(self.user, self.payment(**changes))
        receipt = services.charge(self.user, self.payment(medio=self.qr.pk, recibido=Decimal('100'), verificado=True, referencia='OP-123'))
        self.assertEqual(receipt.cambio, Decimal('0'))
        self.assertEqual(services.summary(self.session)['esperado'], '50.00')

    def test_closed_turn_rejects_new_operations_but_allows_exact_replay(self):
        data = self.payment()
        receipt = services.charge(self.user, data)
        services.close_session(self.user, self.session.pk, Decimal('150'), '')
        self.assertEqual(services.charge(self.user, data).pk, receipt.pk)
        with self.assertRaises(APIException): services.manual_movement(self.user, self.session.pk, 'INGRESO', Decimal('10'), 'Ingreso')
        with self.assertRaises(APIException): services.charge(self.user, self.payment())
        self.assertIsNone(Caja.objects.get(pk=self.box.pk).sesion_actual_id)

    def test_cash_close_and_cancellation_keep_money(self):
        services.charge(self.user, self.payment())
        booking.transition(self.user, self.reservation.pk, 'cancelar', 'Cambio')
        services.manual_movement(self.user, self.session.pk, 'INGRESO', Decimal('20'), 'Fondo adicional')
        services.manual_movement(self.user, self.session.pk, 'RETIRO', Decimal('10'), 'Retiro autorizado')
        with self.assertRaises(APIException): services.close_session(self.user, self.session.pk, Decimal('159'), '')
        result = services.close_session(self.user, self.session.pk, Decimal('159'), 'Diferencia de conteo')
        self.assertEqual(result.efectivo_esperado, Decimal('160'))
        self.assertEqual(result.diferencia, Decimal('-1'))
        self.assertEqual(PagoReserva.objects.count(), 1)

    def test_expired_and_conflicting_intervals_roll_back_every_money_record(self):
        self.reservation.retencion_hasta = timezone.now() - timedelta(seconds=1)
        self.reservation.save()
        with self.assertRaises(APIException): services.charge(self.user, self.payment())
        self.assertEqual(PagoReserva.objects.count(), 0)
        self.assertEqual(ReciboPago.objects.count(), 0)

    def test_opening_and_session_ownership(self):
        with self.assertRaises(APIException): services.open_session(self.user, self.box.pk, Decimal('0'))
        other = User.objects.create_user('cash-local@example.test','test',sucursal=self.branch,nombre='Operador')
        with self.assertRaises(APIException): services.close_session(other, self.session.pk, Decimal('50'), '')
        with self.assertRaises(APIException): services.manual_movement(self.user, self.session.pk,'RETIRO',Decimal('60'),'Retiro')

    def test_api_validation_and_branch_access(self):
        self.assertEqual(self.client.post(f'/api/caja/cajas/{self.box.pk}/abrir/',{'monto_inicial':-1},format='json').status_code,400)
        data = self.payment()
        data = {key:str(value) if isinstance(value, (Decimal, type(data['clave']))) else value for key,value in data.items()}
        response = self.client.post('/api/caja/recibos/',data,format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.assertTrue(response.data['numero'].startswith('SRM-'))
        self.client.force_authenticate(User.objects.create_user('cash-denied@example.test','test',sucursal=self.other))
        self.assertEqual(self.client.get('/api/caja/cajas/').status_code,403)

    def test_failed_start_rolls_back_payment(self):
        with self.assertRaises(APIException): services.charge(self.user,self.payment(iniciar=True))
        self.assertEqual(PagoReserva.objects.count(),0)
        self.assertEqual(MovimientoCaja.objects.count(),0)

    def test_immediate_rental_can_be_paid_and_started(self):
        self.reservation = booking.retain(self.user, {'cliente':self.customer,'cancha_deporte':self.link,
            'alquiler_inmediato':True,'duracion_minutos':60})
        receipt = services.charge(self.user,self.payment(iniciar=True))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.estado,'EN_CURSO')
        self.assertEqual(receipt.pago.importe,self.reservation.total)

    def test_immediate_rental_requires_an_open_box(self):
        services.close_session(self.user,self.session.pk,Decimal('50'),'')
        with self.assertRaises(APIException): booking.retain(self.user,{'cliente':self.customer,
            'cancha_deporte':self.link,'alquiler_inmediato':True,'duracion_minutos':60})

    def test_foreign_reservation_does_not_register_money(self):
        self.reservation.sucursal = self.other
        self.reservation.save()
        with self.assertRaises(APIException): services.charge(self.user,self.payment())
        self.assertEqual(PagoReserva.objects.count(),0)

    def test_receipt_snapshot_survives_business_edits(self):
        receipt = services.charge(self.user,self.payment())
        original = receipt.datos.copy()
        self.court.nombre = 'Otro nombre'
        self.court.save()
        self.cash.nombre = 'Efectivo cambiado'
        self.cash.save()
        receipt.refresh_from_db()
        self.assertEqual(receipt.datos,original)
        self.assertIn('Efectivo',services.summary(self.session)['cobros_por_medio'])

    def test_box_configuration_is_global_only(self):
        response = self.client.post('/api/caja/cajas/',{'nombre':'Caja Recepcion','sucursal':self.branch.pk},format='json')
        self.assertEqual(response.status_code,201,response.data)

    def test_electronic_reference_cannot_be_reused(self):
        services.charge(self.user,self.payment(medio=self.qr.pk,recibido=Decimal('100'),verificado=True,referencia='QR-UNICO'))
        self.reservation = booking.retain(self.user,self.data(inicio=self.end,fin=self.end+timedelta(hours=1)))
        with self.assertRaises(APIException): services.charge(self.user,self.payment(medio=self.qr.pk,
            recibido=Decimal('100'),verificado=True,referencia='QR-UNICO'))
        self.assertEqual(PagoReserva.objects.count(),1)

    def test_authorized_operator_is_scoped_and_cannot_configure_or_close_another_turn(self):
        from users.models import Permiso, Rol, RolPermiso, RolPermisoUsuario
        operator = User.objects.create_user('cash-operator@example.test','test',sucursal=self.branch,nombre='Recepcion')
        role = Rol.objects.create(nombre='Recepcion caja')
        for code in ['caja.ver','caja.cobrar','caja.configurar','caja.cerrar']:
            permission,_ = Permiso.objects.get_or_create(nombre=code)
            link = RolPermiso.objects.create(rol=role,permiso=permission)
            RolPermisoUsuario.objects.create(user=operator,rol_permiso=link)
        foreign = Caja.objects.create(sucursal=self.other,nombre='Otra caja')
        self.client.force_authenticate(operator)
        rows = self.client.get('/api/caja/cajas/').data
        self.assertNotIn(foreign.pk,[row['id'] for row in rows])
        self.assertEqual(self.client.get(f'/api/caja/cajas/{foreign.pk}/').status_code,404)
        self.assertEqual(self.client.post('/api/caja/cajas/',{'nombre':'Nueva','sucursal':self.branch.pk},format='json').status_code,403)
        self.assertEqual(self.client.post(f'/api/caja/sesiones/{self.session.pk}/cerrar/',{'efectivo_contado':'50'},format='json').status_code,403)
        data = self.payment()
        data = {key:str(value) if isinstance(value,(Decimal,type(data['clave']))) else value for key,value in data.items()}
        self.assertEqual(self.client.post('/api/caja/recibos/',data,format='json').status_code,201)


class CashBoxAdministrationTests(CashFixtures, TestCase):
    def update(self, **data):
        return self.client.patch(f'/api/caja/cajas/{self.box.pk}/', data, format='json')

    def test_list_includes_inactive_boxes_but_operational_options_do_not(self):
        inactive = Caja.objects.create(sucursal=self.branch, nombre='Auxiliar', activa=False)
        rows = self.client.get('/api/caja/cajas/').data
        self.assertIn(inactive.pk, [row['id'] for row in rows])
        options = self.client.get('/api/caja/cajas/opciones/').data
        self.assertNotIn(inactive.pk, [row['id'] for row in options['cajas']])

    def test_rename_preserves_branch_and_open_turn(self):
        response = self.update(nombre='  Caja Principal  ')
        self.assertEqual(response.status_code, 200, response.data)
        self.box.refresh_from_db()
        self.assertEqual(self.box.nombre, 'Caja Principal')
        self.assertEqual(self.box.sucursal_id, self.branch.pk)
        self.assertEqual(self.box.sesion_actual_id, self.session.pk)

    def test_open_turn_cannot_be_deactivated_even_in_combined_update(self):
        response = self.update(nombre='No guardar', activa=False)
        self.assertEqual(response.status_code, 400, response.data)
        self.box.refresh_from_db()
        self.assertTrue(self.box.activa)
        self.assertEqual(self.box.nombre, 'Principal')

    def test_closed_box_can_be_deactivated_and_reactivated_without_deleting_history(self):
        services.close_session(self.user, self.session.pk, Decimal('50'), '')
        self.assertEqual(self.update(activa=False).status_code, 200)
        self.assertEqual(self.client.post(f'/api/caja/cajas/{self.box.pk}/abrir/', {'monto_inicial':0}, format='json').status_code, 400)
        self.assertEqual(self.update(activa=True).status_code, 200)
        self.assertTrue(self.box.sesiones.filter(pk=self.session.pk).exists())
        self.assertIn(self.client.delete(f'/api/caja/cajas/{self.box.pk}/').status_code, (403, 405))
        self.assertTrue(Caja.objects.filter(pk=self.box.pk).exists())

    def test_invalid_names_and_branch_transfer_are_rejected(self):
        Caja.objects.create(sucursal=self.branch, nombre='Auxiliar')
        for name in ('', '   ', 'x' * 101, 'Auxiliar'):
            self.assertEqual(self.update(nombre=name).status_code, 400, name)
        self.assertEqual(self.update(sucursal=self.other.pk).status_code, 400)
        self.assertEqual(self.update(sesion_actual=None).status_code, 400)
        Caja.objects.create(sucursal=self.other, nombre='Otra sucursal')
        self.assertEqual(self.update(nombre='Otra sucursal').status_code, 200)

    def test_local_user_cannot_update_even_with_configuration_permission(self):
        from users.models import Permiso, Rol, RolPermiso, RolPermisoUsuario
        operator = User.objects.create_user('local-box@example.test', 'test', sucursal=self.branch)
        role = Rol.objects.create(nombre='Configuracion local')
        permission, _ = Permiso.objects.get_or_create(nombre='caja.configurar')
        link = RolPermiso.objects.create(rol=role, permiso=permission)
        RolPermisoUsuario.objects.create(user=operator, rol_permiso=link)
        self.client.force_authenticate(operator)
        self.assertEqual(self.update(nombre='Denegado').status_code, 403)
        self.assertEqual(self.update(activa=False).status_code, 403)


class CashConcurrencyTests(CashFixtures, TransactionTestCase):
    def race(self, tasks):
        barrier = Barrier(2)
        def run(task):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                barrier.wait(timeout=10)
                task(user)
                return True
            except APIException:
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run,tasks))

    def test_parallel_same_charge_has_one_payment(self):
        data = self.payment()
        self.assertEqual(self.race([lambda u:services.charge(u,data),lambda u:services.charge(u,data)]),[True,True])
        self.assertEqual(ReciboPago.objects.count(),1)
        self.assertEqual(MovimientoCaja.objects.count(),1)

    def test_box_deactivation_and_opening_cannot_both_succeed(self):
        from rest_framework.test import APIClient
        services.close_session(self.user, self.session.pk, Decimal('50'), '')

        def deactivate(user):
            client = APIClient()
            client.force_authenticate(user)
            result = client.patch(f'/api/caja/cajas/{self.box.pk}/', {'activa':False}, format='json')
            if result.status_code != 200:
                from rest_framework.exceptions import ValidationError
                raise ValidationError(result.data)

        result = self.race([deactivate, lambda user: services.open_session(user, self.box.pk, Decimal('0'))])
        self.assertEqual(sum(result), 1)
        self.box.refresh_from_db()
        self.assertFalse(not self.box.activa and self.box.sesion_actual_id is not None)

    def test_charge_vs_close_is_consistent(self):
        data = self.payment()
        outcomes = self.race([lambda u:services.charge(u,data),lambda u:services.close_session(u,self.session.pk,Decimal('50'),'Conteo concurrente')])
        self.assertTrue(outcomes[1])
        self.session.refresh_from_db()
        self.assertEqual(self.session.efectivo_esperado,Decimal('150') if outcomes[0] else Decimal('50'))
        self.assertEqual(PagoReserva.objects.count(),int(outcomes[0]))
