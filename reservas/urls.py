from rest_framework.routers import DefaultRouter
from .views import ClienteViewSet, ReservaViewSet, BloqueoViewSet

router = DefaultRouter()
router.register('clientes', ClienteViewSet, basename='cliente')
router.register('reservas', ReservaViewSet, basename='reserva')
router.register('bloqueos', BloqueoViewSet, basename='bloqueo')
urlpatterns = router.urls
