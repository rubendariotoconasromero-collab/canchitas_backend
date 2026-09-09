from datetime import date
from io import BytesIO
from tempfile import TemporaryDirectory
from PIL import Image
from django.test import TestCase, TransactionTestCase, override_settings
from django.db import close_old_connections, connection
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from users.models import User, Sucursal, Rol, Permiso, RolPermiso, RolPermisoUsuario
from .models import Negocio, Deporte, Cancha, CanchaDeporte, PoliticaReserva, HorarioCancha, TarifaCancha


class CatalogTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.temp.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.business, _ = Negocio.objects.get_or_create(singleton=True, defaults={'nombre_comercial':'Prueba'})
        self.branch = Sucursal.objects.create(nombre='Prueba A', negocio=self.business, slug_publico='prueba-a')
        self.other = Sucursal.objects.create(nombre='Prueba B', negocio=self.business, slug_publico='prueba-b')
        self.root = User.objects.create_superuser('catalog@example.test','Test-Password-123')
        self.client = APIClient()
        self.client.force_authenticate(self.root)
        self.sport = Deporte.objects.create(nombre='Futbol de prueba')
        self.court = Cancha.objects.create(sucursal=self.branch,codigo='A',nombre='Cancha A',
                                           descripcion_publica='Cancha cubierta',superficie='Cesped')
        self.link = CanchaDeporte.objects.create(cancha=self.court,deporte=self.sport)

    def post(self, endpoint, data, fmt='json'):
        return self.client.post(f'/api/catalogo/{endpoint}/',data,format=fmt)

    def patch(self, endpoint, pk, data):
        return self.client.patch(f'/api/catalogo/{endpoint}/{pk}/',data,format='json')

    def photo(self):
        image = BytesIO()
        Image.new('RGB',(40,40),'green').save(image,format='PNG')
        return SimpleUploadedFile('cancha.png',image.getvalue(),content_type='image/png')

    def schedule(self, **changes):
        data = {'cancha':self.court.pk,'dia_semana':1,'apertura':'18:00','cierre':'22:00',
                'cruza_medianoche':False,'activo':True}
        data.update(changes)
        return self.post('horarios',data)

    def rate(self, **changes):
        data = {'cancha_deporte':self.link.pk,'nombre':'Noche','dia_semana':1,
                'hora_desde':'18:00','hora_hasta':'22:00','cruza_medianoche':False,
                'vigente_desde':'2020-01-01','vigente_hasta':None,'precio_hora':'100.00','activa':True}
        data.update(changes)
        return self.post('tarifas',data)

    def test_default_values_and_bob_only(self):
        response = self.client.get(f'/api/catalogo/canchas/{self.court.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('anticipacion_minutos', response.data)
        self.assertEqual(self.court.duracion_minima_minutos,60)
        self.assertEqual(self.patch('negocios',self.business.pk,{'moneda':'USD'}).status_code,400)
        response = self.post('politicas',{'sucursal':self.branch.pk})
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(response.data['horas_limite_cancelacion'],5)
        self.assertIsNone(response.data['porcentaje_anticipo'])
        self.assertEqual(self.patch('canchas',self.court.pk,{'duracion_minima_minutos':90}).status_code,200)
        self.assertEqual(self.patch('canchas',self.court.pk,{'duracion_minima_minutos':0}).status_code,400)

    def test_schedule_overlap_and_midnight(self):
        self.assertEqual(self.schedule(apertura='22:00',cierre='02:00',cruza_medianoche=True).status_code,201)
        self.assertEqual(self.schedule(dia_semana=2,apertura='01:00',cierre='03:00').status_code,400)
        self.assertEqual(self.schedule(dia_semana=2,apertura='02:00',cierre='03:00').status_code,201)
        self.assertEqual(self.schedule(dia_semana=4,apertura='23:00',cierre='01:00').status_code,400)
        self.assertEqual(self.schedule(dia_semana=8).status_code,400)
        self.assertEqual(self.schedule(dia_semana=5,apertura='18:00',cierre='20:00',cruza_medianoche=True).status_code,400)

    def test_week_boundary_and_inactive_schedule(self):
        self.assertEqual(self.schedule(dia_semana=7,apertura='23:00',cierre='02:00',cruza_medianoche=True).status_code,201)
        self.assertEqual(self.schedule(dia_semana=1,apertura='01:00',cierre='03:00').status_code,400)
        self.assertEqual(self.schedule(dia_semana=1,apertura='01:00',cierre='03:00',activo=False).status_code,201)

    def test_rates_overlap_only_with_intersecting_validity(self):
        response = self.rate(vigente_hasta='2026-01-31')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(self.rate(hora_desde='21:00',hora_hasta='23:00',vigente_desde='2026-01-01').status_code,400)
        self.assertEqual(self.rate(vigente_desde='2026-02-01').status_code,201)
        self.assertEqual(self.rate(dia_semana=4,precio_hora='0').status_code,400)
        self.assertEqual(self.rate(dia_semana=4,vigente_desde='2026-02-01',vigente_hasta='2026-01-01').status_code,400)

    def test_rate_overnight_adjacent_date(self):
        self.assertEqual(self.rate(dia_semana=1,hora_desde='22:00',hora_hasta='02:00',cruza_medianoche=True,
                                   vigente_desde='2026-09-07',vigente_hasta='2026-09-07').status_code,201)
        self.assertEqual(self.rate(dia_semana=2,hora_desde='01:00',hora_hasta='03:00',
                                   vigente_desde='2026-09-08',vigente_hasta='2026-09-08').status_code,400)
        self.assertEqual(self.rate(dia_semana=2,hora_desde='01:00',hora_hasta='03:00',
                                   vigente_desde='2026-09-15',vigente_hasta='2026-09-15').status_code,201)

    def test_policy_is_updated_in_place(self):
        a = self.post('politicas',{'sucursal':self.branch.pk,'horas_limite_cancelacion':5})
        b = self.post('politicas',{'sucursal':self.branch.pk,'horas_limite_cancelacion':8})
        self.assertEqual((a.status_code,b.status_code),(201,400))
        old = PoliticaReserva.objects.get(pk=a.data['id'])
        self.assertTrue(old.activa)
        self.assertEqual(old.horas_limite_cancelacion,5)
        self.assertEqual(self.patch('politicas',old.pk,{'horas_limite_cancelacion':9}).status_code,200)
        old.refresh_from_db()
        self.assertEqual(old.horas_limite_cancelacion,9)
        self.assertEqual(PoliticaReserva.objects.filter(sucursal=self.branch).count(),1)
        self.assertEqual(self.client.delete(f'/api/catalogo/politicas/{old.pk}/').status_code,405)
        self.assertEqual(self.post('politicas',{'sucursal':self.branch.pk,'porcentaje_anticipo':101}).status_code,400)

    def test_publication_requires_complete_configuration(self):
        self.assertEqual(self.patch('canchas',self.court.pk,{'publicada':True}).status_code,400)
        self.court.refresh_from_db()
        self.assertFalse(self.court.publicada)
        self.assertEqual(self.post('fotos',{'cancha':self.court.pk,'archivo':self.photo(),
                                           'texto_alternativo':'Cancha vista frontal'},'multipart').status_code,201)
        self.schedule()
        self.rate(hora_hasta='20:00')
        self.post('politicas',{'sucursal':self.branch.pk})
        self.assertEqual(self.patch('canchas',self.court.pk,{'publicada':True}).status_code,400)
        self.rate(hora_desde='20:00')
        published = self.patch('canchas',self.court.pk,{'publicada':True})
        self.assertEqual(published.status_code,200,published.data)
        self.assertTrue(published.data['publicada'])
        rate = TarifaCancha.objects.filter(cancha_deporte=self.link).first()
        self.client.delete(f'/api/catalogo/tarifas/{rate.pk}/')
        self.court.refresh_from_db()
        self.assertFalse(self.court.publicada)

    def test_invalid_photo_rejected(self):
        bad = SimpleUploadedFile('test.png',b'not an image',content_type='image/png')
        self.assertEqual(self.post('fotos',{'cancha':self.court.pk,'archivo':bad,
                                           'texto_alternativo':'bad'},'multipart').status_code,400)

    def test_no_destructive_delete_or_transfer(self):
        self.assertEqual(self.patch('canchas',self.court.pk,{'sucursal':self.other.pk}).status_code,400)
        self.assertEqual(self.client.delete(f'/api/catalogo/canchas/{self.court.pk}/').status_code,200)
        self.court.refresh_from_db()
        self.assertFalse(self.court.activa)

    def test_branch_and_permission_enforcement(self):
        operator = User.objects.create_user('local@example.test','Test-Password-123',sucursal=self.branch)
        role = Rol.objects.create(nombre='Local catalogo')
        for code in ('canchas.ver','canchas.crear','canchas.editar','horarios.crear','politicas.crear','deportes.editar'):
            permission = Permiso.objects.get(nombre=code)
            link = RolPermiso.objects.create(rol=role,permiso=permission)
            RolPermisoUsuario.objects.create(user=operator,rol_permiso=link)
        foreign = Cancha.objects.create(sucursal=self.other,codigo='B',nombre='Otra cancha')
        self.client.force_authenticate(operator)
        response = self.client.get('/api/catalogo/canchas/')
        self.assertEqual([row['id'] for row in response.data],[self.court.pk])
        self.assertEqual(self.client.get(f'/api/catalogo/canchas/{foreign.pk}/').status_code,404)
        self.assertEqual(self.schedule(cancha=foreign.pk).status_code,403)
        self.assertEqual(self.post('politicas',{'sucursal':self.other.pk}).status_code,403)
        self.assertEqual(self.patch('deportes',self.sport.pk,{'nombre':'Changed'}).status_code,403)
        self.assertEqual(self.post('canchas',{'sucursal':self.other.pk,'codigo':'C','nombre':'X'}).status_code,403)

    def test_branch_fields_and_unique_code(self):
        self.assertEqual(self.patch('sucursales',self.branch.pk,{'zona_horaria':'Not/AZone'}).status_code,400)
        self.assertEqual(self.patch('sucursales',self.branch.pk,{'latitud':91}).status_code,400)
        self.assertEqual(self.post('canchas',{'sucursal':self.branch.pk,'codigo':'A','nombre':'Duplicate'}).status_code,400)


class CatalogConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'mysql':
            self.skipTest('Estas pruebas verifican bloqueos MySQL.')
        business, _ = Negocio.objects.get_or_create(singleton=True, defaults={'nombre_comercial':'Concurrente'})
        self.branch = Sucursal.objects.create(nombre='Concurrente',negocio=business)
        self.court = Cancha.objects.create(sucursal=self.branch,codigo='C',nombre='Concurrente')
        self.root = User.objects.create_superuser('concurrent@example.test','Testing-123-Password')

    def simultaneous(self, endpoint, payload):
        barrier = Barrier(2)
        def submit():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.root)
                barrier.wait(timeout=10)
                return client.post('/api/catalogo/' + endpoint + '/',payload,format='json').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(submit) for _ in range(2)]
            return sorted(task.result(timeout=30) for task in tasks)

    def test_simultaneous_overlapping_hours_only_one_succeeds(self):
        result = self.simultaneous('horarios',{'cancha':self.court.pk,'dia_semana':1,
                                              'apertura':'18:00','cierre':'20:00'})
        self.assertEqual(result,[201,400])
        self.assertEqual(HorarioCancha.objects.count(),1)

    def test_simultaneous_policy_creation_has_one_winner(self):
        self.assertEqual(self.simultaneous('politicas',{'sucursal':self.branch.pk}),[201,400])
        self.assertEqual(PoliticaReserva.objects.filter(sucursal=self.branch,activa=True).count(),1)
        self.assertEqual(PoliticaReserva.objects.filter(sucursal=self.branch).count(),1)
