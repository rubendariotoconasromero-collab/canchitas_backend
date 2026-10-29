"""Production settings; serve the application behind a configured HTTPS proxy."""
import os
from django.core.exceptions import ImproperlyConfigured
from .settings import *  # noqa: F403

DEBUG = False
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure-'):  # noqa: F405
    raise ImproperlyConfigured('Configura una SECRET_KEY segura para produccion.')
if not ALLOWED_HOSTS or any(host in ('', '*', 'localhost', '127.0.0.1') for host in ALLOWED_HOSTS):  # noqa: F405
    raise ImproperlyConfigured('Configura ALLOWED_HOSTS con los dominios de produccion.')
CORS_ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',') if origin.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if origin.strip()]
if any(not origin.startswith('https://') for origin in CORS_ALLOWED_ORIGINS + CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured('Los origenes autorizados en produccion deben usar HTTPS.')
redis_url = os.getenv('REDIS_CACHE_URL', '')
if not redis_url:
    raise ImproperlyConfigured('Configura REDIS_CACHE_URL para compartir los limites de acceso.')
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': redis_url}}
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
PUBLIC_HISTORY_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
STATIC_ROOT = BASE_DIR / 'staticfiles'  # noqa: F405
# Only enable this when the proxy strips and sets the header itself.
if os.getenv('TRUST_HTTPS_PROXY', 'False') == 'True':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
