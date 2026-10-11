from rest_framework.routers import DefaultRouter
from .views import CajaViewSet, SessionViewSet, ReceiptViewSet
router = DefaultRouter()
router.register('cajas', CajaViewSet, basename='caja')
router.register('sesiones', SessionViewSet, basename='sesion-caja')
router.register('recibos', ReceiptViewSet, basename='recibo')
urlpatterns = router.urls
