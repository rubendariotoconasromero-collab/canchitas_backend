from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

from django.db import close_old_connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from config.codigos import correlativo, identificador_correlativo
from catalogo.models import Cancha
from caja import services as cash
from caja.models import ReciboPago
from caja.serializers import ReceiptSerializer
from caja.tests import CashFixtures
from .models import Reserva, PagoReserva, SolicitudReserva
from .serializers import ReservaSerializer
from .test_public import PublicFixtures
from .tests import Fixtures
from . import services


class CodeFormatTests(SimpleTestCase):
    def test_padding_is_minimum_not_a_limit(self):
        self.assertEqual(correlativo('RES', 1), 'RES-000001')
        self.assertEqual(correlativo('RES', 999999), 'RES-999999')
        self.assertEqual(correlativo('RES', 1000000), 'RES-1000000')
        self.assertEqual(correlativo('RES', 9223372036854775807), 'RES-9223372036854775807')
        self.assertEqual(correlativo('SRM', 1, 8), 'SRM-00000001')
        self.assertEqual(correlativo('SRM', 100000000, 8), 'SRM-100000000')
        self.assertIsNone(correlativo('RES', None))
        for value in (0, -1, True, '1'):
            with self.assertRaises(ValueError): correlativo('RES', value)

    def test_each_entity_has_its_own_prefix_and_preserves_technical_ids(self):
        for model, prefix in [(Reserva, 'RES'), (SolicitudReserva, 'SOL'), (PagoReserva, 'PAG'), (Cancha, 'CAN')]:
            row = model(pk=1000000)
            self.assertEqual(row.codigo_correlativo, prefix + '-1000000')
            self.assertEqual(row.pk, 1000000)
            with self.assertRaises(AttributeError): row.codigo_correlativo = 'OTRO-000001'
        reservation = Reserva(pk=1)
        technical = reservation.codigo
        self.assertIsInstance(technical, UUID)
        self.assertEqual(reservation.codigo_correlativo, 'RES-000001')
        self.assertEqual(reservation.codigo, technical)
        self.assertEqual(ReciboPago(pk=1).codigo_correlativo, 'SRM-00000001')

    def test_tracking_code_parser_rejects_invalid_or_overflowed_values(self):
        self.assertEqual(identificador_correlativo('SOL', ' sol-1000000 '), 1000000)
        self.assertEqual(identificador_correlativo('SOL', 'SOL-000001'), 1)
        for code in ('RES-000001', 'SOL-000000', 'SOL-1', 'SOL-0000001', 'SOL--000001',
                     'SOL-9223372036854775808', 'SOL-00000x'):
            with self.assertRaises(ValueError): identificador_correlativo('SOL', code)


class OperationalCodeTests(CashFixtures, TestCase):
    def test_existing_records_and_replay_keep_codes_and_security_keys(self):
        code, technical = self.reservation.codigo_correlativo, self.reservation.codigo
        data = self.payment()
        receipt = cash.charge(self.user, data)
        self.assertEqual(receipt.pago.referencia, data['clave'])
        serialized = ReceiptSerializer(receipt).data
        self.assertEqual(serialized['numero'], f'SRM-{receipt.pk:08d}')
        self.assertEqual(serialized['codigo_pago'], receipt.pago.codigo_correlativo)
        self.assertEqual(serialized['codigo_reserva'], code)
        self.assertEqual(serialized['datos']['codigo_reserva'], str(technical))
        self.assertNotIn('clave', serialized)
        self.assertEqual(cash.charge(self.user, data).codigo_correlativo, receipt.codigo_correlativo)
        services.transition(self.user, self.reservation.pk, 'cancelar', 'Cambio de plan')
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.codigo_correlativo, code)
        self.assertEqual(self.reservation.codigo, technical)
        self.assertEqual(ReservaSerializer(self.reservation).data['codigo'], str(technical))
        self.assertEqual(ReservaSerializer(self.reservation).data['codigo_correlativo'], code)

    def test_old_receipt_snapshots_are_not_rewritten(self):
        receipt = cash.charge(self.user, self.payment())
        receipt.datos.pop('codigo_reserva_correlativo')
        receipt.datos.pop('codigo_pago')
        receipt.save(update_fields=['datos'])
        original = receipt.datos.copy()
        result = self.client.get(f'/api/caja/recibos/{receipt.pk}/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['codigo_reserva'], self.reservation.codigo_correlativo)
        receipt.refresh_from_db()
        self.assertEqual(receipt.datos, original)

    def test_reports_search_and_export_visible_codes(self):
        receipt = cash.charge(self.user, self.payment())
        day = timezone.localtime(self.start).date().isoformat()
        params = {'sucursal': self.branch.pk, 'desde': day, 'hasta': day, 'q': self.reservation.codigo_correlativo}
        report = self.client.get('/api/reportes/reservas/', params)
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data['count'], 1)
        self.assertEqual(report.data['results'][0]['codigo'], self.reservation.codigo_correlativo)
        self.assertEqual(report.data['results'][0]['codigo_tecnico'], str(self.reservation.codigo))
        exported = self.client.get('/api/reportes/reservas/exportar/', params)
        self.assertEqual(exported.status_code, 200)
        self.assertIn(self.reservation.codigo_correlativo, exported.content.decode('utf-8-sig'))
        today = timezone.localtime().date().isoformat()
        params.update(desde=today, hasta=today, q=receipt.pago.codigo_correlativo)
        report = self.client.get('/api/reportes/cobros/', params)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data['results'][0]['numero'], receipt.codigo_correlativo)

    def test_court_code_is_automatic_and_original_reference_is_kept(self):
        result = self.client.get(f'/api/catalogo/canchas/{self.court.pk}/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['codigo_correlativo'], self.court.codigo_correlativo)
        self.assertEqual(result.data['codigo'], 'R')
        result = self.client.patch(f'/api/catalogo/canchas/{self.court.pk}/', {'codigo_correlativo': 'CAN-000001'}, format='json')
        self.assertEqual(result.status_code, 400)


class PublicCodeTests(PublicFixtures, TestCase):
    def test_new_tracking_code_and_old_uuid_both_require_the_secret(self):
        sent = self.send()
        self.assertEqual(sent.status_code, 201, sent.data)
        row = SolicitudReserva.objects.get()
        self.assertEqual(sent.data['codigo_correlativo'], row.codigo_correlativo)
        self.assertEqual(sent.data['codigo'], str(row.codigo))
        tracked = self.track(sent, codigo=row.codigo_correlativo)
        self.assertEqual(tracked.status_code, 200, tracked.data)
        self.assertEqual(tracked.data['codigo_correlativo'], row.codigo_correlativo)
        self.assertEqual(self.track(sent).status_code, 200, 'Old UUID links remain valid')
        self.assertEqual(self.track(sent, codigo=row.codigo_correlativo, token='0'*64).status_code, 404)
        self.assertEqual(self.track(sent, codigo='SOL-9223372036854775808').status_code, 400)
        original = row.codigo_correlativo
        self.assertEqual(self.accept(row).status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.codigo_correlativo, original)
        self.assertEqual(self.track(sent, codigo=original).data['codigo_reserva'], row.reserva.codigo_correlativo)


class ConcurrentCodeTests(Fixtures, TransactionTestCase):
    def test_simultaneous_creations_have_unique_increasing_codes(self):
        barrier = Barrier(3)
        def create(offset):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                start = self.start + timedelta(hours=offset*2)
                row = services.retain(self.user, self.data(inicio=start, fin=start+timedelta(hours=1)))
                return row.pk, row.codigo_correlativo
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=3) as pool:
            rows = list(pool.map(create, range(3)))
        self.assertEqual(len({code for _, code in rows}), 3)
        for pk, code in rows:
            self.assertEqual(code, correlativo('RES', pk))

    def test_database_sequence_crosses_six_digits_without_reuse(self):
        Cancha.objects.create(pk=999999, sucursal=self.branch, codigo='LIMITE', nombre='Limite')
        next_court = Cancha.objects.create(sucursal=self.branch, codigo='SIGUIENTE', nombre='Siguiente')
        self.assertEqual(next_court.codigo_correlativo, 'CAN-1000000')
        next_court.delete()
        newer = Cancha.objects.create(sucursal=self.branch, codigo='POSTERIOR', nombre='Posterior')
        self.assertEqual(newer.codigo_correlativo, 'CAN-1000001')
