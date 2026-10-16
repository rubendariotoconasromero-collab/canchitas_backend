from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from reservas import services as booking
from users.models import User, Permiso, Rol, RolPermiso, RolPermisoUsuario
from . import services
from .models import Caja
from .tests import CashFixtures


class CashPrivacyTests(CashFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.cashier = self.operator('Cajero', [
            'caja.ver', 'caja.abrir', 'caja.cobrar', 'caja.cerrar', 'caja.movimientos', 'dashboard.ver'])
        self.reception = self.operator('Recepcion', ['caja.cobrar'])
        self.other_cashier = self.operator('Otro', ['caja.ver', 'caja.cobrar'])
        self.own_box = Caja.objects.create(sucursal=self.branch, nombre='Caja local')
        self.own_session = services.open_session(self.cashier, self.own_box.pk, Decimal('20'))

    def operator(self, name, permissions):
        user = User.objects.create_user(name.lower() + '@example.test', 'test', nombre=name, sucursal=self.branch)
        role = Rol.objects.create(nombre='Privacidad ' + name)
        for code in permissions:
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            link = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=user, rol_permiso=link)
        return user

    def another_payment(self, offset, operator, session):
        reservation = booking.retain(self.user, self.data(
            inicio=self.start + timedelta(hours=offset), fin=self.end + timedelta(hours=offset)))
        return services.charge(operator, self.payment(reserva=reservation.pk, sesion=session.pk))

    def test_session_lists_details_movements_and_writers_require_ownership(self):
        self.client.force_authenticate(self.cashier)
        rows = self.client.get('/api/caja/sesiones/').data
        self.assertEqual([r['id'] for r in rows], [self.own_session.pk])
        self.assertEqual(self.client.get('/api/caja/sesiones/', {'caja': self.box.pk}).data, [])
        for suffix in ('', 'movimientos/'):
            self.assertEqual(self.client.get(f'/api/caja/sesiones/{self.session.pk}/{suffix}').status_code, 404)
        for action, data in [('cerrar', {'efectivo_contado': '50'}),
                             ('movimiento', {'tipo': 'INGRESO', 'importe': '10', 'motivo': 'Fondo'})]:
            self.assertEqual(self.client.post(f'/api/caja/sesiones/{self.session.pk}/{action}/', data, format='json').status_code, 404)
        self.assertEqual(self.client.get(f'/api/caja/sesiones/{self.own_session.pk}/movimientos/').status_code, 200)

    def test_closed_foreign_turn_remains_private_and_admin_can_inspect(self):
        services.close_session(self.user, self.session.pk, Decimal('50'), '')
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get(f'/api/caja/sesiones/{self.session.pk}/').status_code, 404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(f'/api/caja/sesiones/{self.session.pk}/').status_code, 200)
        self.assertEqual(len(self.client.get('/api/caja/sesiones/').data), 2)

    def test_box_options_expose_occupancy_but_no_turn_balances(self):
        foreign = Caja.objects.create(sucursal=self.other, nombre='Privada')
        self.client.force_authenticate(self.cashier)
        rows = self.client.get('/api/caja/cajas/opciones/').data['cajas']
        self.assertNotIn(foreign.pk, [r['id'] for r in rows])
        occupied = next(r for r in rows if r['id'] == self.box.pk)
        self.assertEqual(occupied['sesion_actual'], self.session.pk)
        self.assertNotIn('resumen', occupied)
        self.assertNotIn('monto_inicial', occupied)
        self.assertEqual(self.client.post(f'/api/caja/cajas/{self.box.pk}/abrir/', {'monto_inicial': 0}, format='json').status_code, 409)

    def test_receipts_include_own_turn_and_own_charges_only(self):
        received_in_own_turn = services.charge(self.reception, self.payment(sesion=self.own_session.pk))
        own_charge = self.another_payment(1, self.cashier, self.session)
        foreign_charge = self.another_payment(2, self.other_cashier, self.session)
        self.client.force_authenticate(self.cashier)
        rows = self.client.get('/api/caja/recibos/').data
        self.assertEqual({r['id'] for r in rows}, {received_in_own_turn.pk, own_charge.pk})
        rows = self.client.get('/api/caja/recibos/', {'sesion': self.session.pk}).data
        self.assertEqual([r['id'] for r in rows], [own_charge.pk])
        self.assertEqual(self.client.get(f'/api/caja/recibos/{foreign_charge.pk}/').status_code, 404)
        self.assertEqual(self.client.get('/api/caja/recibos/', {'reserva': foreign_charge.pago.reserva_id}).data, [])
        self.client.force_authenticate(self.user)
        self.assertEqual(len(self.client.get('/api/caja/recibos/').data), 3)

    def test_reception_can_charge_foreign_turn_without_reading_its_finances(self):
        self.client.force_authenticate(self.reception)
        data = self.payment()
        response = self.client.post('/api/caja/recibos/', {k: str(v) for k, v in data.items() if k not in ('iniciar', 'verificado')}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.get('/api/caja/sesiones/').status_code, 403)
        self.assertEqual(self.client.get('/api/caja/recibos/').data[0]['id'], response.data['id'])
        self.assertEqual(self.client.get(f"/api/caja/recibos/{response.data['id']}/").status_code, 200)
        self.assertEqual(services.summary(self.session)['esperado'], '150.00')

    def test_charge_replay_cannot_leak_another_operators_receipt(self):
        data = self.payment()
        receipt = services.charge(self.reception, data)
        self.assertEqual(services.charge(self.reception, data).pk, receipt.pk)
        with self.assertRaises(PermissionDenied):
            services.charge(self.cashier, data)
        self.assertEqual(services.charge(self.user, data).pk, receipt.pk)

    def test_dashboard_limits_turns_and_distinguishes_personal_charges(self):
        services.charge(self.reception, self.payment(sesion=self.own_session.pk))
        own = self.another_payment(1, self.cashier, self.session)
        self.another_payment(2, self.other_cashier, self.session)
        self.client.force_authenticate(self.cashier)
        response = self.client.get('/api/dashboard/resumen/')
        self.assertEqual(response.status_code, 200, response.data)
        cash = response.data['caja']
        self.assertIsNone(cash['administracion'])
        self.assertEqual([t['id'] for t in cash['turnos']], [self.own_session.pk])
        self.assertEqual(cash['turnos'][0]['efectivo_esperado'], '120.00')
        self.assertEqual([r['id'] for r in cash['ultimos_cobros']], [own.pk])
        self.assertEqual(cash['mis_cobros_turnos_abiertos']['importe'], '100.00')

    def test_global_configuration_permission_can_load_branches_and_create(self):
        admin = self.operator('Configurador', ['caja.configurar', 'alcance.global'])
        self.client.force_authenticate(admin)
        options = self.client.get('/api/caja/cajas/opciones/')
        self.assertEqual(options.status_code, 200)
        self.assertIn(self.other.pk, [b['id'] for b in options.data['sucursales']])
        response = self.client.post('/api/caja/cajas/', {'nombre': 'Nueva administrativa', 'sucursal': self.other.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
