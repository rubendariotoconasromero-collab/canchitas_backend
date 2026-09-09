from django.test import TestCase
from rest_framework.test import APIClient
from .models import Negocio
from django.db import IntegrityError, transaction
from users.models import User


class BrandingTests(TestCase):
    def test_business_can_only_be_read_and_updated(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser('singleton@example.test', 'Test-Password-123'))
        business = Negocio.objects.get()
        self.assertEqual(client.post('/api/catalogo/negocios/', {'nombre_comercial':'Otro'}).status_code, 405)
        self.assertEqual(client.delete(f'/api/catalogo/negocios/{business.pk}/').status_code, 405)
        self.assertEqual(client.patch(f'/api/catalogo/negocios/{business.pk}/', {'nombre_comercial':'Actualizado'}).status_code, 200)
        self.assertEqual(Negocio.objects.count(), 1)

    def test_database_rejects_second_business_and_false_singleton(self):
        for value in (True, False):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Negocio.objects.create(nombre_comercial='Otro', singleton=value)

    def test_public_branding_returns_only_name_even_with_stale_token(self):
        business = Negocio.objects.order_by('pk').first()
        business.nombre_comercial = 'Soccer Real Madrid'
        business.save()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token expired')
        response = client.get('/api/publico/marca/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'nombre_comercial': 'Soccer Real Madrid'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        business.nombre_comercial = 'Nuevo nombre'
        business.save()
        self.assertEqual(client.get('/api/publico/marca/').json(), {'nombre_comercial': 'Nuevo nombre'})

    def test_branding_is_read_only(self):
        self.assertEqual(APIClient().post('/api/publico/marca/', {}).status_code, 405)
