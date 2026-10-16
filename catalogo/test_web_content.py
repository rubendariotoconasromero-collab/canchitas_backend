from io import BytesIO
from tempfile import TemporaryDirectory
from PIL import Image
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from users.models import User, Permiso, Rol, RolPermiso, RolPermisoUsuario
from .models import Negocio, SlideWeb, ConfiguracionWeb, SeccionWeb, Deporte
from .web_content import initialize_web


class WebContentTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = override_settings(MEDIA_ROOT=temp.name, PUBLIC_API_RATES={'public_read': '10000/min'})
        settings.enable()
        self.addCleanup(settings.disable)
        self.business, _ = Negocio.objects.get_or_create(singleton=True)
        self.business.nombre_comercial = 'Soccer Real Madrid'
        self.business.publicado = True
        self.business.save()
        self.config = initialize_web(self.business)
        self.root = User.objects.create_superuser('web-admin@example.test', 'password')
        self.client = APIClient()
        self.client.force_authenticate(self.root)

    def image(self, fmt='PNG'):
        stream = BytesIO()
        Image.new('RGB', (20, 20), 'green').save(stream, format=fmt)
        return SimpleUploadedFile('photo.png', stream.getvalue(), content_type='image/png')

    def patch(self, endpoint, pk, data, fmt='json'):
        return self.client.patch(f'/api/catalogo/{endpoint}/{pk}/', data, format=fmt)

    def publish_slides(self):
        for slide in self.business.slides_web.all():
            response = self.patch('slides-web', slide.pk, {'archivo': self.image(), 'texto_alternativo': 'Cancha deportiva', 'publicada': True}, 'multipart')
            self.assertEqual(response.status_code, 200, response.data)

    def test_initial_content_is_idempotent_and_draft(self):
        initialize_web(self.business)
        self.assertEqual(self.business.slides_web.count(), 3)
        self.assertEqual(self.business.secciones_web.count(), 3)
        self.assertFalse(self.config.publicada)
        self.assertFalse(self.business.slides_web.filter(publicada=True).exists())

    def test_publication_needs_three_images_and_cannot_remove_one(self):
        response = self.patch('configuracion-web', self.config.pk, {'publicada': True})
        self.assertEqual(response.status_code, 400)
        self.publish_slides()
        self.assertEqual(self.patch('configuracion-web', self.config.pk, {'publicada': True}).status_code, 200)
        slide = self.business.slides_web.first()
        self.assertEqual(self.patch('slides-web', slide.pk, {'publicada': False}).status_code, 400)
        slide.refresh_from_db()
        self.assertTrue(slide.publicada)
        self.assertEqual(self.client.delete(f'/api/catalogo/slides-web/{slide.pk}/').status_code, 400)
        self.assertEqual(self.patch('configuracion-web', self.config.pk, {'publicada': False}).status_code, 200)
        self.assertEqual(self.patch('slides-web', slide.pk, {'publicada': False}).status_code, 200)

    def test_uploaded_images_require_alt_and_supported_format(self):
        slide = self.business.slides_web.first()
        self.assertEqual(self.patch('slides-web', slide.pk, {'archivo': self.image()}, 'multipart').status_code, 400)
        self.assertEqual(self.patch('slides-web', slide.pk, {'archivo': self.image('GIF'), 'texto_alternativo': 'Cancha'}, 'multipart').status_code, 400)
        self.assertEqual(self.patch('slides-web', slide.pk, {'publicada': True}).status_code, 400)
        sport = Deporte.objects.create(nombre='Futbol imagen')
        self.assertEqual(self.patch('deportes', sport.pk, {'archivo': self.image()}, 'multipart').status_code, 400)
        self.assertEqual(self.patch('deportes', sport.pk, {'archivo': self.image(), 'texto_alternativo': 'Partido'}, 'multipart').status_code, 200)

    def test_order_is_server_controlled_and_movable(self):
        ids = list(self.business.slides_web.values_list('pk', flat=True))
        self.assertEqual(self.patch('slides-web', ids[0], {'orden': 20}).status_code, 400)
        self.assertEqual(self.patch('slides-web', ids[0], {'negocio': self.business.pk}).status_code, 400)
        response = self.client.post(f'/api/catalogo/slides-web/{ids[1]}/mover/', {'direccion': 'subir'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row['id'] for row in response.data], [ids[1], ids[0], ids[2]])
        self.assertEqual([row['orden'] for row in response.data], [0, 1, 2])
        self.assertEqual(self.client.post(f'/api/catalogo/slides-web/{ids[1]}/mover/', {'direccion': 'otro'}, format='json').status_code, 400)

    def test_unique_sections_and_valid_contact_social_urls(self):
        response = self.client.post('/api/catalogo/secciones-web/', {'tipo': 'NOSOTROS', 'titulo': 'Duplicado'}, format='json')
        self.assertEqual(response.status_code, 400)
        for url in ['http://instagram.com/soccer', 'https://instagram.com.evil.test/soccer', 'https://user@instagram.com/soccer']:
            self.assertEqual(self.client.post('/api/catalogo/redes-web/', {'plataforma': 'INSTAGRAM', 'url': url}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/catalogo/redes-web/', {'plataforma': 'INSTAGRAM', 'url': 'https://www.instagram.com/soccer/'}, format='json').status_code, 201)
        self.assertEqual(self.client.post('/api/catalogo/contactos-web/', {'tipo': 'CORREO', 'etiqueta': 'Contacto', 'valor': 'invalid'}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/catalogo/contactos-web/', {'tipo': 'WHATSAPP', 'etiqueta': 'Reservas', 'valor': '+59170123456'}, format='json').status_code, 201)

    def test_public_api_only_exposes_published_content(self):
        anonymous = APIClient()
        self.assertIsNone(anonymous.get('/api/publico/negocio/').data['web'])
        self.publish_slides()
        self.patch('configuracion-web', self.config.pk, {'publicada': True, 'lema': 'Juega en equipo'})
        section = self.business.secciones_web.first()
        self.patch('secciones-web', section.pk, {'contenido': 'Nuestro negocio', 'publicada': True})
        self.client.post('/api/catalogo/slides-web/', {'titulo': 'Privado'}, format='json')
        data = anonymous.get('/api/publico/negocio/').data['web']
        self.assertEqual(len(data['slides']), 3)
        self.assertEqual(len(data['secciones']), 1)
        self.assertTrue(data['slides'][0]['imagen'].startswith('http://testserver/media/'))
        self.assertNotIn('negocio', data['slides'][0])
        self.assertNotIn('created_at', data)

    def test_local_users_cannot_manage_public_content(self):
        local = User.objects.create_user('web-local@example.test', 'password', nombre='Recepcion')
        role = Rol.objects.create(nombre='Web local')
        permission, _ = Permiso.objects.get_or_create(nombre='contenido_web.ver')
        link = RolPermiso.objects.create(rol=role, permiso=permission)
        RolPermisoUsuario.objects.create(user=local, rol_permiso=link)
        self.client.force_authenticate(local)
        self.assertEqual(self.client.get('/api/catalogo/slides-web/').status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/catalogo/slides-web/').status_code, [401, 403])
        self.client.force_authenticate(self.root)
        self.assertEqual(self.client.post('/api/catalogo/configuracion-web/', {}, format='json').status_code, 405)

    def test_global_editor_requires_permission_for_each_action(self):
        editor = User.objects.create_user('web-editor@example.test', 'password', nombre='Editor')
        role = Rol.objects.create(nombre='Editor global')
        for code in ['alcance.global', 'contenido_web.ver', 'contenido_web.editar']:
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            link = RolPermiso.objects.create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.create(user=editor, rol_permiso=link)
        self.client.force_authenticate(editor)
        self.assertEqual(self.client.get('/api/catalogo/slides-web/').status_code, 200)
        slide = self.business.slides_web.first()
        self.assertEqual(self.patch('slides-web', slide.pk, {'titulo': 'Actualizado'}).status_code, 200)
        self.assertEqual(self.client.post('/api/catalogo/slides-web/', {'titulo': 'Nuevo'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/catalogo/slides-web/{slide.pk}/').status_code, 403)

    def test_hidden_sections_do_not_leave_broken_slider_links(self):
        self.publish_slides()
        slide = self.business.slides_web.first()
        self.patch('slides-web', slide.pk, {'destino': 'CONTACTO'})
        self.patch('configuracion-web', self.config.pk, {'publicada': True, 'mostrar_contacto': False})
        data = APIClient().get('/api/publico/negocio/').data['web']
        self.assertEqual(data['slides'][0]['texto_boton'], '')
        self.assertEqual(data['contactos'], [])
