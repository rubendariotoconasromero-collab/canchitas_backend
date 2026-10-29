from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from catalogo.models import FotoCancha, TarifaCancha
from users.models import User, Permiso, Rol, RolPermiso, RolPermisoUsuario
from .tests import Fixtures
from .models import SolicitudReserva, Reserva, Cliente, PagoReserva
from . import services


class PublicFixtures(Fixtures):
    def setUp(self):
        super().setUp()
        cache.clear()
        business = self.branch.negocio
        business.publicado = True
        business.save()
        self.court.publicada = True
        self.court.save()
        FotoCancha.objects.create(cancha=self.court, archivo='canchas/public-test.jpg', texto_alternativo='Cancha', publicada=True)
        self.public = APIClient()

    def payload(self, **extra):
        return {'cancha_deporte': self.link.pk, 'inicio': self.start.isoformat(), 'fin': self.end.isoformat(),
                'nombre': 'Maria Fernandez', 'telefono': '70123456', 'correo': 'maria@gmail.com', 'notas': '',
                'condiciones_aceptadas': True, 'total_esperado': '100.00', 'condiciones_esperadas':self.policy.condiciones,
                'horas_cancelacion_esperadas':self.policy.horas_limite_cancelacion, 'clave': str(uuid4()), **extra}

    def send(self, **extra):
        return self.public.post('/api/publico/solicitudes/', self.payload(**extra), format='json')

    def accept(self, row, **extra):
        return self.client.post(f'/api/reservas/solicitudes/{row.pk}/aceptar/', {'total_esperado': '100.00',
            'condiciones_esperadas':self.policy.condiciones, 'horas_cancelacion_esperadas':self.policy.horas_limite_cancelacion, **extra}, format='json')

    def track(self, response, **extra):
        return self.public.post('/api/publico/seguimiento/', {'codigo':response.data['codigo'], 'token':response.data['token'], **extra}, format='json')

    def operator(self, branch, codes):
        user = User.objects.create_user(f'{uuid4()}@gmail.com', 'test', sucursal=branch)
        role = Rol.objects.create(nombre=str(uuid4()))
        for code in codes:
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            rp = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=user, rol_permiso=rp)
        return user


class PublicReservationTests(PublicFixtures, TestCase):
    def test_public_request_to_mixed_payment_service_close_and_report(self):
        from caja.models import Caja, MedioPago
        sent = self.send()
        self.assertEqual(sent.status_code, 201, sent.data)
        request = SolicitudReserva.objects.get()
        accepted = self.accept(request)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        reservation = accepted.data['reserva']['id']
        box = Caja.objects.create(sucursal=self.branch, nombre='Punto de cobro')
        opened = self.client.post(f'/api/caja/cajas/{box.pk}/abrir/', {'monto_inicial': '50.00'}, format='json')
        self.assertEqual(opened.status_code, 201, opened.data)
        cash, _ = MedioPago.objects.get_or_create(codigo='EFECTIVO', defaults={'nombre': 'Efectivo', 'efectivo': True})
        qr, _ = MedioPago.objects.get_or_create(codigo='TRANSFERENCIA_QR', defaults={'nombre': 'Transferencia QR', 'efectivo': False})
        cash.activo = qr.activo = True
        cash.save(update_fields=['activo'])
        qr.save(update_fields=['activo'])
        payload = {'sesion': opened.data['id'], 'reserva': reservation, 'clave': str(uuid4()), 'importe': '100.00', 'pagos': [
            {'medio': cash.pk, 'importe': '40.00', 'recibido': '40.00'},
            {'medio': qr.pk, 'importe': '60.00', 'recibido': '60.00', 'referencia': 'INTEGRACION-QR', 'verificado': True},
        ]}
        paid = self.client.post('/api/caja/recibos/', payload, format='json')
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertEqual(self.track(sent).data['estado_reserva'], 'CONFIRMADA')
        with patch('reservas.services.timezone.now', return_value=self.start):
            started = self.client.post(f'/api/reservas/reservas/{reservation}/iniciar/', {}, format='json')
            self.assertEqual(started.status_code, 200, started.data)
            ended = self.client.post(f'/api/reservas/reservas/{reservation}/finalizar/', {}, format='json')
            self.assertEqual(ended.status_code, 200, ended.data)
        closed = self.client.post(f'/api/caja/sesiones/{opened.data["id"]}/cerrar/', {'efectivo_contado': '90.00'}, format='json')
        self.assertEqual(closed.status_code, 200, closed.data)
        day = timezone.localdate().isoformat()
        report = self.client.get('/api/reportes/cobros/', {'sucursal': self.branch.pk, 'desde': day, 'hasta': day, 'medio': qr.pk})
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data['resumen']['medio_seleccionado'], '60.00')
        self.assertEqual(report.data['resumen']['cobrado'], '100.00')

    def test_public_catalog_excludes_unpublished_and_private_fields(self):
        response = self.public.get('/api/publico/canchas/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['fotos'][0]['descripcion'], 'Cancha')
        self.assertNotIn('created_at', response.data[0])
        self.court.publicada = False
        self.court.save()
        self.assertEqual(self.public.get('/api/publico/canchas/').data, [])
        self.assertEqual(self.public.get(f'/api/publico/canchas/{self.court.pk}/').status_code, 404)
        self.assertEqual(self.send().status_code, 400)
        business = self.branch.negocio
        business.publicado = False
        business.save()
        self.assertEqual(self.public.get('/api/publico/negocio/').status_code, 404)

    def test_quote_uses_server_price_and_rejects_privileged_fields(self):
        interval = {'cancha_deporte':self.link.pk, 'inicio':self.start.isoformat(), 'fin':self.end.isoformat()}
        response = self.public.post('/api/publico/cotizar/', interval, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total'], '100.00')
        self.assertNotIn('tarifa_id', response.data['desglose'][0])
        self.assertEqual(self.send(total_esperado='1.00').status_code, 409)
        for key, value in [('sin_cliente',True),('cliente',self.customer.pk),('estado','ACEPTADA'),('origen','INMEDIATO')]:
            self.assertEqual(self.send(**{key:value}).status_code, 400)
        self.assertEqual(self.send(condiciones_aceptadas=False).status_code, 400)
        self.assertEqual(self.send(telefono='abc').status_code, 400)

    def test_submission_does_not_occupy_or_create_customer_and_is_idempotent(self):
        data = self.payload()
        first = self.public.post('/api/publico/solicitudes/', data, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        repeat = self.public.post('/api/publico/solicitudes/', data, format='json')
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertEqual(first.data, repeat.data)
        self.assertEqual(SolicitudReserva.objects.count(), 1)
        self.assertEqual(Reserva.objects.count(), 0)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(services.quote(self.link,self.start,self.end)['total'], Decimal('100'))
        self.assertEqual(self.public.post('/api/publico/solicitudes/', {**data,'nombre':'Otro nombre'},format='json').status_code,409)
        row = SolicitudReserva.objects.get()
        self.assertNotEqual(row.seguimiento_hash,first.data['token'])
        self.assertEqual(row.condiciones_snapshot['politica_version'],self.policy.version)
        self.assertEqual(row.condiciones_snapshot['horas_cancelacion'],self.policy.horas_limite_cancelacion)

    def test_tracking_is_private_expires_and_does_not_expose_customer(self):
        response = self.send()
        tracking = self.track(response)
        self.assertEqual(tracking.status_code, 200, tracking.data)
        for key in ['nombre','telefono','correo','cliente','historial','token','seguimiento_hash','clave']:
            self.assertNotIn(key, tracking.data)
        self.assertEqual(tracking['Cache-Control'],'no-store')
        self.assertEqual(self.track(response,token='0'*64).status_code,404)
        SolicitudReserva.objects.update(seguimiento_hasta=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.track(response).status_code,404)

    def test_acceptance_creates_web_reservation_once_and_tracking_distinguishes_payment(self):
        response = self.send()
        row = SolicitudReserva.objects.get()
        accepted = self.accept(row)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data['reserva']['estado'],'PENDIENTE')
        self.assertEqual(accepted.data['reserva']['origen'],'WEB')
        self.assertEqual(self.accept(row).status_code,200)
        self.assertEqual(Reserva.objects.count(),1)
        self.assertEqual(Cliente.objects.count(),2)
        self.assertEqual(PagoReserva.objects.count(),0)
        tracking = self.track(response)
        self.assertEqual(tracking.data['estado'],'ACEPTADA')
        self.assertEqual(tracking.data['estado_reserva'],'PENDIENTE')
        self.assertIsNotNone(tracking.data['plazo_pago'])
        Reserva.objects.update(retencion_hasta=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.track(response).data['estado_reserva'],'EXPIRADA')
        self.assertIsNone(self.track(response).data['plazo_pago'])

    def test_operator_scope_and_customer_creation_permission(self):
        self.send()
        row = SolicitudReserva.objects.get()
        denied = self.operator(self.other,['solicitudes.ver','solicitudes.aceptar'])
        self.client.force_authenticate(denied)
        self.assertEqual(self.client.get('/api/reservas/solicitudes/').data,[])
        self.assertEqual(self.accept(row).status_code,404)
        operator = self.operator(self.branch,['solicitudes.ver','solicitudes.aceptar'])
        self.client.force_authenticate(operator)
        self.assertEqual(self.accept(row).status_code,403)
        options = self.client.get('/api/reservas/solicitudes/opciones/')
        self.assertEqual([b['id'] for b in options.data],[self.branch.pk])
        self.assertEqual(self.accept(row,cliente=self.customer.pk).status_code,200)
        self.assertEqual(Cliente.objects.count(),1)

    def test_conflicting_requests_price_changes_and_rollback(self):
        self.send()
        first = SolicitudReserva.objects.get()
        self.send()
        second = SolicitudReserva.objects.exclude(pk=first.pk).get()
        TarifaCancha.objects.update(precio_hora='150.00')
        self.assertEqual(self.accept(first).status_code,409)
        quote = self.client.post(f'/api/reservas/solicitudes/{first.pk}/cotizar/',{},format='json')
        self.assertEqual(quote.data['total'],'150.00')
        self.assertEqual(self.accept(first,total_esperado='150.00').status_code,200)
        self.assertEqual(self.accept(second,total_esperado='150.00').status_code,409)
        self.assertEqual(Cliente.objects.count(),2)
        self.assertEqual(Reserva.objects.count(),1)
        second.refresh_from_db()
        self.assertEqual(second.estado,'PENDIENTE')

    def test_reject_is_idempotent_even_when_court_unpublished_and_expired_cannot_accept(self):
        response = self.send()
        row = SolicitudReserva.objects.get()
        self.court.publicada = False
        self.court.save()
        url = f'/api/reservas/solicitudes/{row.pk}/rechazar/'
        self.assertEqual(self.client.post(url,{'motivo':'No disponible'},format='json').status_code,200)
        self.assertEqual(self.client.post(url,{'motivo':'No disponible'},format='json').status_code,200)
        self.assertEqual(self.track(response).data['motivo'],'No disponible')
        self.assertEqual(Reserva.objects.count(),0)
        SolicitudReserva.objects.filter(pk=row.pk).update(estado='PENDIENTE',inicio=timezone.now()-timedelta(minutes=1))
        self.assertEqual(self.accept(row).status_code,409)
        self.assertEqual(self.client.get('/api/reservas/solicitudes/',{'estado':'VENCIDA'}).data[0]['id'],row.pk)

    def test_availability_is_anonymous_and_ignores_unaccepted_requests(self):
        self.send()
        day = timezone.localtime(self.start).date().isoformat()
        url = f'/api/publico/canchas/{self.court.pk}/disponibilidad/'
        before = self.public.get(url,{'fecha':day})
        self.assertEqual(before.status_code,200,before.data)
        self.assertTrue(before.data['intervalos'])
        self.accept(SolicitudReserva.objects.get())
        after = self.public.get(url,{'fecha':day})
        self.assertEqual(after.status_code,200)
        self.assertGreater(len(after.data['intervalos']),len(before.data['intervalos']))
        for span in after.data['intervalos']: self.assertEqual(set(span),{'inicio','fin'})
        self.assertEqual(self.public.get(url,{'fecha':'invalid'}).status_code,400)

    def test_full_payment_then_cancellation_keeps_the_receipt(self):
        from caja.models import Caja, MedioPago, ReciboPago
        from caja import services as cash
        response = self.send()
        result = self.accept(SolicitudReserva.objects.get())
        reservation = Reserva.objects.get(pk=result.data['reserva']['id'])
        box = Caja.objects.create(sucursal=self.branch,nombre='Principal')
        session = cash.open_session(self.user,box.pk,Decimal('0'))
        method, _ = MedioPago.objects.get_or_create(codigo='EFECTIVO',defaults={'nombre':'Efectivo','efectivo':True})
        receipt = cash.charge(self.user,{'sesion':session.pk,'reserva':reservation.pk,'medio':method.pk,'clave':uuid4(),
            'importe':Decimal('100'),'recibido':Decimal('100'),'referencia':'','verificado':False,'iniciar':False})
        self.assertEqual(self.track(response).data['estado_reserva'],'CONFIRMADA')
        services.transition(self.user,reservation.pk,'cancelar','Cambio de planes')
        self.assertEqual(self.track(response).data['estado_reserva'],'CANCELADA')
        self.assertTrue(ReciboPago.objects.filter(pk=receipt.pk).exists())
        self.assertEqual(PagoReserva.objects.count(),1)

    def test_write_throttle_is_enforced(self):
        from django.test import override_settings
        with override_settings(PUBLIC_API_RATES={'public_write':'1/min'}):
            self.assertEqual(self.send().status_code,201)
            self.assertEqual(self.send().status_code,429)

    def test_policy_changes_require_a_new_quote_and_preserve_original_consent(self):
        response = self.send()
        row = SolicitudReserva.objects.get()
        initial = row.condiciones_snapshot.copy()
        original = self.policy.condiciones
        self.policy.condiciones = 'Condiciones actualizadas'
        self.policy.save()
        self.assertEqual(self.send(condiciones_esperadas=original).status_code,409)
        self.assertEqual(self.accept(row,condiciones_esperadas=original).status_code,409)
        self.assertEqual(self.accept(row).status_code,200)
        row.refresh_from_db()
        self.assertEqual(row.condiciones_snapshot,initial)
        self.assertEqual(self.track(response).data['condiciones'],'Condiciones actualizadas')


class PublicConcurrencyTests(PublicFixtures, TransactionTestCase):
    def race(self, rows):
        if connection.vendor != 'mysql': self.skipTest('Requiere MySQL y bloqueos de fila.')
        barrier = Barrier(2)
        def worker(pk):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=self.user.pk))
                barrier.wait(timeout=10)
                return client.post(f'/api/reservas/solicitudes/{pk}/aceptar/', {'total_esperado':'100.00',
                    'condiciones_esperadas':self.policy.condiciones,'horas_cancelacion_esperadas':self.policy.horas_limite_cancelacion},format='json').status_code
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(worker,rows))

    def test_two_acceptances_of_the_same_request_create_one_reservation_and_customer(self):
        self.send()
        row = SolicitudReserva.objects.get()
        self.assertEqual(self.race([row.pk,row.pk]),[200,200])
        self.assertEqual(Reserva.objects.count(),1)
        self.assertEqual(Cliente.objects.count(),2)

    def test_accepting_two_overlapping_requests_succeeds_only_once(self):
        self.send()
        self.send()
        self.assertCountEqual(self.race(list(SolicitudReserva.objects.values_list('pk',flat=True))),[200,409])
        self.assertEqual(Reserva.objects.count(),1)
        self.assertEqual(Cliente.objects.count(),2)
