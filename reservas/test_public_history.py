from datetime import timedelta
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import SolicitudReserva, VisitantePublico, SolicitudVisitante, Reserva
from .test_public import PublicFixtures


class PublicHistoryTests(PublicFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.first = self.public
        self.second = APIClient()
        self.context(self.first)
        self.context(self.second)

    def context(self, client):
        response = client.get('/api/publico/historial/contexto/')
        self.assertEqual(response.status_code, 200, response.data)
        client.credentials(HTTP_X_CSRFTOKEN=response.data['csrf'])
        return response

    def listing(self, client=None, **filters):
        return (client or self.first).get('/api/publico/historial/', filters)

    def detail(self, code, client=None):
        return (client or self.first).get(f'/api/publico/historial/{code}/')

    def test_context_cookie_is_private_and_stable_without_exposing_key(self):
        key = self.first.cookies[settings.PUBLIC_HISTORY_COOKIE].value
        response = self.context(self.first)
        self.assertNotIn(settings.PUBLIC_HISTORY_COOKIE, response.cookies)
        self.assertNotIn(key, str(response.data))
        self.assertEqual(VisitantePublico.objects.count(), 2)
        from hashlib import sha256
        self.assertTrue(VisitantePublico.objects.filter(clave_hash=sha256(key.encode()).hexdigest()).exists())
        self.assertFalse(VisitantePublico.objects.filter(clave_hash=key).exists())
        other = APIClient()
        cookie = self.context(other).cookies[settings.PUBLIC_HISTORY_COOKIE]
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertEqual(cookie['path'], '/api/publico/')
        self.assertEqual(cookie['max-age'], settings.PUBLIC_HISTORY_DAYS * 86400)

    def test_new_requests_and_replays_are_associated_once(self):
        data = self.payload()
        first = self.first.post('/api/publico/solicitudes/', data, format='json')
        repeated = self.first.post('/api/publico/solicitudes/', data, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(SolicitudVisitante.objects.count(), 1)
        result = self.listing().data
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['results'][0]['estado_publico'], 'EN_REVISION')
        self.assertEqual(self.listing(self.second).data['count'], 0)
        self.assertEqual(self.detail(first.data['codigo'], self.second).status_code, 404)
        self.assertEqual(self.detail(first.data['codigo_correlativo']).status_code, 200)
        for key in ['token', 'nombre', 'telefono', 'correo', 'clave', 'seguimiento_hash', 'clave_hash', 'historial']:
            self.assertNotIn(key, result['results'][0])
        self.assertEqual(self.listing()['Cache-Control'], 'no-store')

    def test_admin_token_does_not_grant_access_to_another_browser(self):
        response = self.send()
        self.second.force_authenticate(self.user)
        self.assertEqual(self.detail(response.data['codigo'], self.second).status_code, 404)
        self.assertEqual(self.listing(self.second).data['count'], 0)
        plain = APIClient()
        self.assertEqual(plain.get('/api/publico/historial/').status_code, 403)

    def test_live_states_and_tabs_separate_acceptance_from_confirmation(self):
        response = self.send()
        row = SolicitudReserva.objects.get()
        self.assertEqual(self.accept(row).status_code, 200)
        self.assertEqual(self.listing(grupo='pendientes').data['results'][0]['estado_publico'], 'PENDIENTE_PAGO')
        self.assertEqual(self.listing(grupo='confirmadas').data['count'], 0)
        for state, group in [('CONFIRMADA', 'confirmadas'), ('EN_CURSO', 'confirmadas'),
                             ('FINALIZADA', 'historial'), ('NO_ASISTIO', 'historial'), ('CANCELADA', 'historial')]:
            Reserva.objects.filter(pk=row.reserva_id or SolicitudReserva.objects.get(pk=row.pk).reserva_id).update(estado=state)
            self.assertEqual(self.listing(grupo=group).data['results'][0]['estado_publico'], state)
            self.assertEqual(self.detail(response.data['codigo']).data['estado_publico'], state)
        row.refresh_from_db()
        Reserva.objects.filter(pk=row.reserva_id).update(estado='PENDIENTE', retencion_hasta=timezone.now()-timedelta(minutes=1))
        self.assertEqual(self.listing(grupo='historial').data['results'][0]['estado_publico'], 'EXPIRADA')

    def test_history_filters_and_display_use_reservation_snapshots_and_rescheduled_date(self):
        response = self.send()
        row = SolicitudReserva.objects.get()
        self.accept(row)
        row.refresh_from_db()
        detail = row.reserva.detalle
        detail.cancha_nombre = 'Nombre historico'
        detail.inicio += timedelta(days=3)
        detail.fin += timedelta(days=3)
        detail.save()
        day = timezone.localtime(detail.inicio).date().isoformat()
        result = self.listing(q='Nombre historico', desde=day, hasta=day)
        self.assertEqual(result.data['count'], 1)
        self.assertEqual(result.data['results'][0]['cancha'], 'Nombre historico')
        self.assertEqual(self.detail(response.data['codigo']).data['inicio'], detail.inicio)

    def test_missing_browser_cookie_with_csrf_header_does_not_create_unlinked_request(self):
        self.first.cookies.pop(settings.PUBLIC_HISTORY_COOKIE)
        before = SolicitudReserva.objects.count()
        self.assertEqual(self.send().status_code, 403)
        self.assertEqual(SolicitudReserva.objects.count(), before)

    def test_request_rejection_and_expiry_are_historical_without_mutating(self):
        self.send()
        row = SolicitudReserva.objects.get()
        SolicitudReserva.objects.filter(pk=row.pk).update(estado='RECHAZADA', motivo='Horario solicitado no disponible')
        self.assertEqual(self.listing(grupo='historial').data['results'][0]['estado_publico'], 'RECHAZADA')
        SolicitudReserva.objects.filter(pk=row.pk).update(estado='PENDIENTE', inicio=timezone.now()-timedelta(hours=2), fin=timezone.now()-timedelta(hours=1))
        self.assertEqual(self.listing(grupo='historial').data['results'][0]['estado_publico'], 'VENCIDA')
        row.refresh_from_db()
        self.assertEqual(row.estado, 'PENDIENTE')

    def test_history_remains_available_after_shareable_tracking_link_expires(self):
        response = self.send()
        SolicitudReserva.objects.update(seguimiento_hasta=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.track(response).status_code, 404)
        self.assertEqual(self.detail(response.data['codigo']).status_code, 200)
        self.assertEqual(self.listing().data['count'], 1)
        self.assertEqual(self.second.post('/api/publico/historial/importar/', {'codigo':response.data['codigo'], 'token':response.data['token']}, format='json').status_code, 404)

    def test_import_requires_valid_secret_is_idempotent_and_does_not_move_ownership(self):
        response = self.send()
        data = {'codigo': response.data['codigo_correlativo'], 'token': response.data['token']}
        endpoint = '/api/publico/historial/importar/'
        self.assertEqual(self.second.post(endpoint, {**data, 'token': '0'*64}, format='json').status_code, 404)
        for _ in range(2):
            self.assertEqual(self.second.post(endpoint, data, format='json').status_code, 200)
        self.assertEqual(self.listing(self.second).data['count'], 1)
        self.assertEqual(self.listing().data['count'], 1)
        self.assertEqual(SolicitudVisitante.objects.count(), 2)

    def test_removal_and_clear_only_remove_browser_links(self):
        response = self.send()
        code = response.data['codigo_correlativo']
        endpoint = f'/api/publico/historial/{code}/'
        self.assertEqual(self.second.delete(endpoint).status_code, 404)
        self.assertEqual(self.first.delete(endpoint).status_code, 204)
        self.assertEqual(self.listing().data['count'], 0)
        self.assertTrue(SolicitudReserva.objects.exists())
        self.send()
        clear = self.first.delete('/api/publico/historial/')
        self.assertEqual(clear.status_code, 204)
        self.assertEqual(clear.cookies[settings.PUBLIC_HISTORY_COOKIE]['max-age'], 0)
        self.assertEqual(SolicitudReserva.objects.count(), 2)
        self.context(self.first)
        self.assertEqual(self.listing().data['count'], 0)

    def test_expired_or_forged_cookie_cannot_access_history(self):
        self.send()
        self.first.cookies[settings.PUBLIC_HISTORY_COOKIE] = 'forged'
        self.assertEqual(self.listing().status_code, 403)
        self.context(self.first)
        self.assertEqual(self.listing().data['count'], 0)
        VisitantePublico.objects.update(acceso_hasta=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.listing().status_code, 403)
        self.context(self.first)
        self.assertEqual(self.listing().data['count'], 0)

    def test_search_date_sort_pagination_and_invalid_filters(self):
        first = self.send()
        self.send(inicio=(self.start+timedelta(days=1)).isoformat(), fin=(self.end+timedelta(days=1)).isoformat())
        self.assertEqual(self.listing(q=first.data['codigo_correlativo']).data['count'], 1)
        self.assertEqual(self.listing(q='Maria').data['count'], 0, 'Cannot search by client identity')
        self.assertEqual(self.listing(q=self.court.nombre).data['count'], 2)
        self.assertEqual(self.listing(page_size=1, orden='anteriores').data['results'][0]['codigo'], first.data['codigo'])
        self.assertEqual(self.listing(page_size=1, page=99).data['page'], 2)
        day = timezone.localtime(self.start).date().isoformat()
        self.assertEqual(self.listing(desde=day, hasta=day).data['count'], 1)
        for data in [{'grupo':'otros'}, {'estado':'otro'}, {'page':0}, {'page_size':101}, {'orden':'otro'},
                     {'desde':'ayer'}, {'desde':'2026-11-20', 'hasta':'2026-10-01'}]:
            self.assertEqual(self.listing(**data).status_code, 400, data)

    def test_csrf_protects_submission_import_and_unlink_and_rejects_foreign_origin(self):
        response = self.send()
        secure = APIClient(enforce_csrf_checks=True)
        context = secure.get('/api/publico/historial/contexto/')
        self.assertEqual(context.status_code, 200)
        data = {'codigo': response.data['codigo'], 'token':response.data['token']}
        self.assertEqual(secure.post('/api/publico/historial/importar/', data, format='json').status_code, 403)
        self.assertEqual(secure.post('/api/publico/solicitudes/', self.payload(), format='json').status_code, 403)
        self.assertEqual(secure.delete('/api/publico/historial/').status_code, 403)
        secure.credentials(HTTP_X_CSRFTOKEN=context.data['csrf'], HTTP_ORIGIN='https://untrusted.example')
        self.assertEqual(secure.post('/api/publico/historial/importar/', data, format='json').status_code, 403)
        secure.credentials(HTTP_X_CSRFTOKEN=context.data['csrf'])
        self.assertEqual(secure.post('/api/publico/historial/importar/', data, format='json').status_code, 200)
        self.assertEqual(secure.post('/api/publico/solicitudes/', self.payload(), format='json').status_code, 201)
        self.assertEqual(secure.delete('/api/publico/historial/').status_code, 204)

    @override_settings(PUBLIC_HISTORY_COOKIE_SECURE=True, PUBLIC_HISTORY_DAYS=30)
    def test_production_cookie_is_secure_with_configurable_duration(self):
        response = self.context(APIClient())
        cookie = response.cookies[settings.PUBLIC_HISTORY_COOKIE]
        self.assertTrue(cookie['secure'])
        self.assertEqual(cookie['max-age'], 30 * 86400)
