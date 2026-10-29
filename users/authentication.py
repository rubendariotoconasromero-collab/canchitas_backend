from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class ActiveTokenAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if not user.estado:
            raise AuthenticationFailed('Cuenta desactivada.')
        if token.created + timedelta(seconds=settings.AUTH_TOKEN_MAX_AGE_SECONDS) <= timezone.now():
            token.delete()
            raise AuthenticationFailed('La sesion ha vencido. Vuelve a ingresar.')
        return user, token
