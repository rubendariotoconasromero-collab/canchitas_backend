from rest_framework.routers import DefaultRouter
from .views import ClienteViewSet, ReservaViewSet, BloqueoViewSet
from .solicitudes import SolicitudViewSet

router = DefaultRouter()
router.register('clientes', ClienteViewSet, basename='cliente')
router.register('reservas', ReservaViewSet, basename='reserva')
router.register('bloqueos', BloqueoViewSet, basename='bloqueo')
router.register('solicitudes', SolicitudViewSet, basename='solicitud')
urlpatterns = router.urls
