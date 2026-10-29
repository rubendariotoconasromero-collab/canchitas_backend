from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from users.models import User, Sucursal
from catalogo.models import Negocio, Cancha, Deporte, CanchaDeporte, HorarioCancha, TarifaCancha, PoliticaReserva
from .models import Cliente, Reserva, PagoReserva, BloqueoCancha
from . import services


class Fixtures:
    def setUp(self):
        business, _ = Negocio.objects.get_or_create(singleton=True, defaults={'nombre_comercial': 'Prueba'})
        self.branch = Sucursal.objects.create(nombre='Reservas A', negocio=business)
        self.other = Sucursal.objects.create(nombre='Reservas B', negocio=business)
        self.user = User.objects.create_superuser('reservas@example.test', 'test-password')
        self.court = Cancha.objects.create(sucursal=self.branch, codigo='R', nombre='Cancha reserva')
        self.link = CanchaDeporte.objects.create(cancha=self.court, deporte=Deporte.objects.create(nombre='Futbol reservas'))
        self.customer = Cliente.objects.create(sucursal=self.branch, nombre='Cliente', telefono='70000000')
        self.policy = PoliticaReserva.objects.create(sucursal=self.branch, version=1, minutos_retencion=15)
        for weekday in range(1, 8):
            HorarioCancha.objects.create(cancha=self.court, dia_semana=weekday, apertura=time(0), cierre=time(0), cruza_medianoche=True)
            TarifaCancha.objects.create(cancha_deporte=self.link, nombre='Base', dia_semana=weekday, hora_desde=time(0),
                hora_hasta=time(0), cruza_medianoche=True, vigente_desde='2020-01-01', precio_hora='100.00')
        self.start = (timezone.now() + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
        self.end = self.start + timedelta(hours=1)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def data(self, **extra):
        return {'cliente': self.customer, 'cancha_deporte': self.link, 'inicio': self.start, 'fin': self.end, **extra}

    def post(self, suffix='', **extra):
        data = {'cliente': self.customer.pk, 'cancha_deporte': self.link.pk, 'inicio': self.start.isoformat(), 'fin': self.end.isoformat(), **extra}
        return self.client.post('/api/reservas/reservas/' + suffix, data, format='json')


class ReservationTests(Fixtures, TestCase):
    def test_calendar_range_limits_and_exclusive_end(self):
        reservation = services.retain(self.user, self.data())
        day = (self.start - timedelta(days=1)).date()
        params = {'sucursal': self.branch.pk, 'fecha': day.isoformat(),
                  'hasta': (day + timedelta(days=42)).isoformat()}
        response = self.client.get('/api/reservas/reservas/agenda/', params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(reservation.pk, [row['id'] for row in response.data['reservas']])
        self.assertEqual(len(response.data['horarios']), 42)
        for count in (0, -1, 43):
            params['hasta'] = (day + timedelta(days=count)).isoformat()
            self.assertEqual(self.client.get('/api/reservas/reservas/agenda/', params).status_code, 400)
        params['hasta'] = day.isoformat()
        params['fecha'] = (day - timedelta(days=1)).isoformat()
        response = self.client.get('/api/reservas/reservas/agenda/', params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['reservas'], [])

    def test_operational_courts_do_not_require_active_sports(self):
        unassigned = Cancha.objects.create(sucursal=self.branch, codigo='N', nombre='Sin deporte')
        inactive = Cancha.objects.create(sucursal=self.branch, codigo='I', nombre='Inactiva', activa=False)
        response = self.client.get('/api/reservas/reservas/opciones/')
        self.assertEqual(response.status_code, 200, response.data)
        ids = {row['id'] for row in response.data['canchas']}
        self.assertTrue({unassigned.pk, inactive.pk, self.court.pk}.issubset(ids))
        rental_courts = {row['cancha'] for row in response.data['canchas_deportes']}
        self.assertNotIn(unassigned.pk, rental_courts)
        self.assertNotIn(inactive.pk, rental_courts)
        for court in [unassigned, inactive]:
            response = self.client.post('/api/reservas/bloqueos/', {'cancha': court.pk,
                'inicio': self.start.isoformat(), 'fin': self.end.isoformat(), 'motivo': 'Mantenimiento'}, format='json')
            self.assertEqual(response.status_code, 201, response.data)

    def test_operational_court_options_respect_branch_scope(self):
        from users.models import Permiso, Rol, RolPermiso, RolPermisoUsuario
        foreign = Cancha.objects.create(sucursal=self.other, codigo='B', nombre='Otra sucursal')
        operator = User.objects.create_user('court-options@example.test', 'test', sucursal=self.branch, nombre='Operador')
        role = Rol.objects.create(nombre='Opciones de canchas')
        for code in ['reservas.ver', 'bloqueos.crear']:
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            rp = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=operator, rol_permiso=rp)
        self.client.force_authenticate(operator)
        response = self.client.get('/api/reservas/reservas/opciones/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(foreign.pk, {row['id'] for row in response.data['canchas']})
        response = self.client.post('/api/reservas/bloqueos/', {'cancha': foreign.pk,
            'inicio': self.start.isoformat(), 'fin': self.end.isoformat(), 'motivo': 'Mantenimiento'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_cancelled_and_expired_holds_have_no_collectable_balance(self):
        reserve = services.retain(self.user, self.data())
        url = f'/api/reservas/reservas/{reserve.pk}/'
        self.assertEqual(self.client.get(url).data['saldo_pendiente'], '100.00')
        services.transition(self.user, reserve.pk, 'cancelar', 'Cambio de planes')
        response = self.client.get(url)
        self.assertEqual(response.data['saldo_pendiente'], '0.00')
        self.assertEqual(response.data['total'], '100.00')
        reserve = services.retain(self.user, self.data())
        Reserva.objects.filter(pk=reserve.pk).update(retencion_hasta=timezone.now()-timedelta(seconds=1))
        url = f'/api/reservas/reservas/{reserve.pk}/'
        response = self.client.get(url)
        self.assertEqual(response.data['estado'], 'EXPIRADA')
        self.assertEqual(response.data['saldo_pendiente'], '0.00')
        services.expire(reserve.pk)
        self.assertEqual(self.client.get(url).data['saldo_pendiente'], '0.00')

    def test_unidentified_client_is_internal_and_explicit(self):
        payload = {'cancha_deporte': self.link.pk, 'sin_cliente': True,
            'inicio': self.start.isoformat(), 'fin': self.end.isoformat()}
        response = self.client.post('/api/reservas/reservas/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['cliente'])
        self.assertEqual(response.data['cliente_snapshot']['nombre'], 'Sin cliente registrado')
        self.assertTrue(response.data['cliente_snapshot']['sin_cliente'])
        self.assertEqual(Cliente.objects.count(), 1)
        reservation = Reserva.objects.get(pk=response.data['id'])
        self.assertEqual(reservation.creado_por_id, self.user.pk)
        self.assertEqual(reservation.total, Decimal('100'))
        self.assertEqual(reservation.estado, 'PENDIENTE')
        mixed = {**payload, 'cliente': self.customer.pk}
        self.assertEqual(self.client.post('/api/reservas/reservas/', mixed, format='json').status_code, 400)
        missing = {k: v for k, v in payload.items() if k != 'sin_cliente'}
        self.assertEqual(self.client.post('/api/reservas/reservas/', missing, format='json').status_code, 400)
        null_client = {**missing, 'cliente': None}
        self.assertEqual(self.client.post('/api/reservas/reservas/', null_client, format='json').status_code, 400)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.post('/api/reservas/reservas/', payload, format='json').status_code, 401)

    def test_unidentified_client_refused_by_default_service_and_without_permission(self):
        data = self.data(sin_cliente=True)
        del data['cliente']
        with self.assertRaises(ValidationError):
            services.retain(self.user, data)
        operator = User.objects.create_user('no-reserva@example.test', 'test', sucursal=self.branch, nombre='Sin permiso')
        with self.assertRaises(ValidationError):
            services.retain(operator, data, allow_unidentified=True)
        self.client.force_authenticate(operator)
        response = self.client.post('/api/reservas/reservas/', {'sin_cliente': True, 'cancha_deporte': self.link.pk,
            'inicio': self.start.isoformat(), 'fin': self.end.isoformat()}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_create_client_then_reserve_in_same_branch(self):
        response = self.client.post('/api/reservas/clientes/', {'sucursal': self.branch.pk,
            'nombre': 'Cliente desde reserva', 'telefono': '70111222', 'documento': '123'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        response = self.post(cliente=response.data['id'])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['cliente_snapshot']['nombre'], 'Cliente desde reserva')
        self.assertFalse(response.data['cliente_snapshot'].get('sin_cliente', False))

    def test_quote_and_snapshot(self):
        response = self.post('cotizar/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total'], '100.00')
        response = self.post(total_esperado='100.00')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['total_pagado'], '0.00')
        reservation = Reserva.objects.get(pk=response.data['id'])
        TarifaCancha.objects.all().update(precio_hora='200.00')
        self.customer.nombre = 'Otro nombre'
        self.customer.save()
        reservation.refresh_from_db()
        self.assertEqual(reservation.total, Decimal('100'))
        self.assertEqual(reservation.cliente_snapshot['nombre'], 'Cliente')
        self.assertEqual(reservation.detalle.desglose_tarifa[0]['importe'], '100.00')

    def test_overlap_adjacent_and_expiration(self):
        reserve = services.retain(self.user, self.data())
        with self.assertRaises(services.Conflict):
            services.retain(self.user, self.data())
        services.retain(self.user, self.data(inicio=self.end, fin=self.end + timedelta(hours=1)))
        Reserva.objects.filter(pk=reserve.pk).update(retencion_hasta=timezone.now() - timedelta(seconds=1))
        self.assertTrue(services.expire(reserve.pk))
        self.assertFalse(services.expire(reserve.pk))
        services.retain(self.user, self.data())

    def test_payment_guard_and_no_client_financial_state(self):
        self.assertEqual(self.post(total='1.00', estado='CONFIRMADA').status_code, 400)
        reserve = services.retain(self.user, self.data())
        with self.assertRaises(ValidationError):
            services.transition(self.user, reserve.pk, 'confirmar')
        receipt = PagoReserva.objects.create(reserva=reserve, referencia=uuid4(), importe='50', registrado_por=self.user)
        with self.assertRaises(ValidationError):
            services.transition(self.user, reserve.pk, 'confirmar')
        receipt.importe = reserve.total
        receipt.save()
        confirmed = services.transition(self.user, reserve.pk, 'confirmar')
        self.assertEqual(confirmed.estado, 'CONFIRMADA')
        cancelled = services.transition(self.user, reserve.pk, 'cancelar', 'Cambio de planes')
        self.assertEqual(cancelled.estado, 'CANCELADA')
        self.assertTrue(PagoReserva.objects.filter(pk=receipt.pk).exists())
        self.assertTrue(cancelled.historial.last().datos['sin_reembolso'])
        response = self.client.get(f'/api/reservas/reservas/{reserve.pk}/')
        self.assertEqual(response.data['saldo_pendiente'], '0.00')
        self.assertEqual(response.data['total_pagado'], '100.00')
        self.assertEqual(response.data['total'], '100.00')

    def test_cancellation_deadline_and_service_states(self):
        reserve = services.retain(self.user, self.data())
        PagoReserva.objects.create(reserva=reserve, referencia=uuid4(), importe=reserve.total, registrado_por=self.user)
        services.transition(self.user, reserve.pk, 'confirmar')
        with patch('reservas.services.timezone.now', return_value=self.start - timedelta(hours=4)):
            with self.assertRaises(ValidationError):
                services.transition(self.user, reserve.pk, 'cancelar')
        with patch('reservas.services.timezone.now', return_value=self.start):
            self.assertEqual(services.transition(self.user, reserve.pk, 'iniciar').estado, 'EN_CURSO')
            self.assertEqual(services.transition(self.user, reserve.pk, 'finalizar').estado, 'FINALIZADA')
        with self.assertRaises(services.Conflict):
            services.retain(self.user, self.data())

    def test_regularization_requires_payment_finished_interval_and_records_outcome(self):
        reserve = services.retain(self.user, self.data())
        PagoReserva.objects.create(reserva=reserve, referencia=uuid4(), importe=reserve.total, registrado_por=self.user)
        services.transition(self.user, reserve.pk, 'confirmar')
        with self.assertRaises(ValidationError):
            services.regularize(self.user, reserve.pk, 'NO_ASISTIO', 'Confirmado por el personal')
        with patch('reservas.services.timezone.now', return_value=self.end + timedelta(minutes=1)):
            url = f'/api/reservas/reservas/{reserve.pk}/regularizar/'
            self.assertEqual(self.client.post(url, {'resultado': 'NO_ASISTIO', 'motivo': ''}, format='json').status_code, 400)
            response = self.client.post(url, {'resultado': 'NO_ASISTIO', 'motivo': 'Confirmado por el personal'}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['estado'], 'NO_ASISTIO')
            event = reserve.historial.last()
            self.assertEqual((event.accion, event.actor_id, event.datos['motivo']), ('REGULARIZAR', self.user.pk, 'Confirmado por el personal'))
            self.assertTrue(PagoReserva.objects.filter(reserva=reserve).exists())
            self.assertEqual(self.client.post(url, {'resultado': 'FINALIZADA', 'motivo': 'otra'}, format='json').status_code, 400)

    def test_regularization_as_completed_and_permission_denial(self):
        reserve = services.retain(self.user, self.data())
        PagoReserva.objects.create(reserva=reserve, referencia=uuid4(), importe=reserve.total, registrado_por=self.user)
        services.transition(self.user, reserve.pk, 'confirmar')
        url = f'/api/reservas/reservas/{reserve.pk}/regularizar/'
        data = {'resultado': 'FINALIZADA', 'motivo': 'Servicio comprobado'}
        with patch('reservas.services.timezone.now', return_value=self.end + timedelta(minutes=1)):
            restricted = User.objects.create_user('limited@example.test', 'test', sucursal=self.branch)
            self.client.force_authenticate(restricted)
            self.assertEqual(self.client.post(url, data, format='json').status_code, 403)
            self.client.force_authenticate(self.user)
            response = self.client.post(url, data, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['estado'], 'FINALIZADA')
            self.assertEqual(reserve.historial.last().datos['resultado'], 'FINALIZADA')

    def test_reschedule_and_blocks(self):
        reserve = services.retain(self.user, self.data())
        old_deadline = reserve.retencion_hasta
        moved = services.reschedule(self.user, reserve.pk, {'inicio': self.end, 'fin': self.end + timedelta(hours=1)})
        self.assertEqual(moved.retencion_hasta, old_deadline)
        block = services.create_block(self.user, {'cancha': self.court, 'inicio': self.start, 'fin': self.end, 'motivo': 'Mantenimiento'})
        with self.assertRaises(services.Conflict):
            services.retain(self.user, self.data())
        services.release_block(self.user, block.pk)
        self.assertFalse(BloqueoCancha.objects.get(pk=block.pk).activo)
        services.retain(self.user, self.data())

    def test_minimum_missing_policy_rates_and_changed_quote(self):
        with self.assertRaises(ValidationError):
            services.retain(self.user, self.data(fin=self.start + timedelta(minutes=30)))
        self.assertEqual(self.post(total_esperado='80.00').status_code, 409)
        self.policy.minutos_retencion = None
        self.policy.save()
        self.assertEqual(self.post().status_code, 400)
        self.policy.minutos_retencion = 15
        self.policy.save()
        TarifaCancha.objects.all().update(activa=False)
        self.assertEqual(self.post().status_code, 400)

    def test_local_date_weekly_agenda_and_invalid_requests(self):
        payload = {'cancha_deporte': self.link.pk, 'fecha': self.start.date().isoformat(), 'hora_inicio': '19:00', 'hora_fin': '20:00'}
        response = self.client.post('/api/reservas/reservas/cotizar/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total'], '100.00')
        response = self.client.get('/api/reservas/reservas/agenda/', {'sucursal': self.branch.pk, 'fecha': self.start.date(), 'dias': 7})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['horarios'])
        self.assertEqual(self.client.get('/api/reservas/reservas/agenda/', {'fecha': 'wrong'}).status_code, 400)
        self.assertEqual(self.post(inicio='2027-01-01T18:00:00', fin='2027-01-01T19:00:00').status_code, 400)

    def test_scope_and_inactive_client(self):
        self.customer.activo = False
        self.customer.save()
        self.assertEqual(self.post().status_code, 400)
        operator = User.objects.create_user('restricted@example.test', 'test', sucursal=self.other, nombre='Operador')
        from users.models import Permiso, Rol, RolPermiso, RolPermisoUsuario
        role = Rol.objects.create(nombre='Reservas restringido')
        for code in ['reservas.ver', 'reservas.crear']:
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            rp = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=operator, rol_permiso=rp)
        self.client.force_authenticate(operator)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get('/api/reservas/reservas/agenda/', {'sucursal': self.branch.pk, 'fecha': self.start.date()}).status_code, 404)

    def test_midnight_rates_and_decimal_tranches(self):
        local = timezone.localtime(self.start).replace(hour=23, minute=30)
        value = services.quote(self.link, local, local + timedelta(minutes=90))
        self.assertEqual(value['total'], Decimal('150.00'))
        self.assertEqual(sum(Decimal(part['importe']) for part in value['desglose']), value['total'])
        self.assertEqual(len(value['desglose']), 2)
        TarifaCancha.objects.all().delete()
        day = local.date()
        for a, b, price in [(time(18), time(19), '100.01'), (time(19), time(20), '200.02')]:
            TarifaCancha.objects.create(cancha_deporte=self.link, nombre='Tramo', dia_semana=day.isoweekday(),
                hora_desde=a, hora_hasta=b, vigente_desde=day, precio_hora=price)
        begin = local.replace(hour=18, minute=30)
        value = services.quote(self.link, begin, begin + timedelta(hours=1))
        self.assertEqual(value['total'], Decimal('150.02'))
        self.assertEqual(sum(Decimal(part['importe']) for part in value['desglose']), value['total'])

    def test_reprogramming_conflict_preserves_old_contract(self):
        reserve = services.retain(self.user, self.data())
        services.create_block(self.user, {'cancha': self.court, 'inicio': self.end,
            'fin': self.end + timedelta(hours=2), 'motivo': 'Mantenimiento'})
        with self.assertRaises(services.Conflict):
            services.reschedule(self.user, reserve.pk, {'inicio': self.end, 'fin': self.end + timedelta(hours=1)})
        reserve.refresh_from_db()
        self.assertEqual(reserve.detalle.inicio, self.start)
        self.assertEqual(reserve.historial.count(), 1)

    def test_shared_court_sports_conflict_and_tariff_gaps(self):
        services.retain(self.user, self.data())
        second = CanchaDeporte.objects.create(cancha=self.court, deporte=Deporte.objects.create(nombre='Otro deporte'))
        for rate in self.link.tarifas.all():
            rate.pk = None
            rate.cancha_deporte = second
            rate.save()
        with self.assertRaises(services.Conflict):
            services.retain(self.user, self.data(cancha_deporte=second))
        TarifaCancha.objects.filter(cancha_deporte=self.link).update(activa=False)
        with self.assertRaises(ValidationError):
            services.quote(self.link, self.end, self.end + timedelta(hours=1))

    def test_immediate_client_management_and_move_quote_api(self):
        response = self.client.post('/api/reservas/clientes/', {'sucursal': self.branch.pk,
            'nombre': 'Nuevo', 'telefono': '70123456'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data['id']
        self.assertEqual(self.client.patch(f'/api/reservas/clientes/{pk}/', {'sucursal': self.other.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f'/api/reservas/clientes/{pk}/', {'activo': False}, format='json').status_code, 200)
        self.assertIn(self.client.delete(f'/api/reservas/clientes/{pk}/').status_code, [403, 405])
        self.assertTrue(Cliente.objects.filter(pk=pk).exists())
        from caja.models import Caja
        from caja.services import open_session
        box = Caja.objects.create(sucursal=self.branch, nombre='Recepcion')
        open_session(self.user, box.pk, Decimal('0'))
        response = self.client.post('/api/reservas/reservas/', {'cancha_deporte': self.link.pk,
            'cliente': self.customer.pk, 'alquiler_inmediato': True, 'duracion_minutos': 60}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['origen'], 'INMEDIATO')
        reserve = services.retain(self.user, self.data())
        response = self.client.post(f'/api/reservas/reservas/{reserve.pk}/cotizar-reprogramacion/',
            {'inicio': self.end.isoformat(), 'fin': (self.end + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total'], '100.00')


class ConcurrentReservationTests(Fixtures, TransactionTestCase):
    def test_two_operators_cannot_hold_the_same_interval(self):
        if connection.vendor != 'mysql':
            self.skipTest('Requiere bloqueo de filas MySQL.')
        barrier = Barrier(2)
        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    services.retain(User.objects.get(pk=self.user.pk), self.data())
                    return 'created'
                except services.Conflict:
                    return 'conflict'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertCountEqual(results, ['created', 'conflict'])
        self.assertEqual(Reserva.objects.count(), 1)

    def test_hold_and_maintenance_block_are_mutually_exclusive(self):
        if connection.vendor != 'mysql':
            self.skipTest('Requiere bloqueo de filas MySQL.')
        barrier = Barrier(2)
        def worker(kind):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                barrier.wait(timeout=10)
                try:
                    if kind == 'hold':
                        services.retain(user, self.data())
                    else:
                        services.create_block(user, {'cancha': self.court, 'inicio': self.start, 'fin': self.end, 'motivo': 'Reparacion'})
                    return 'created'
                except services.Conflict:
                    return 'conflict'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, ['hold', 'block']))
        self.assertCountEqual(results, ['created', 'conflict'])
        self.assertEqual(Reserva.objects.count() + BloqueoCancha.objects.count(), 1)
