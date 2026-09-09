from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


class ActiveTokenAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if not user.estado:
            raise AuthenticationFailed('Cuenta desactivada.')
        return user, token
