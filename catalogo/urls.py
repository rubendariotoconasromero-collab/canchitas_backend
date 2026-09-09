from rest_framework.routers import DefaultRouter
from .views import (NegocioViewSet, SucursalViewSet, DeporteViewSet, CanchaViewSet,
                    CanchaDeporteViewSet, FotoViewSet, HorarioViewSet, TarifaViewSet, PoliticaViewSet)

router = DefaultRouter()
for prefix, view in [('negocios', NegocioViewSet), ('sucursales', SucursalViewSet),
                     ('deportes', DeporteViewSet), ('canchas', CanchaViewSet),
                     ('cancha-deportes', CanchaDeporteViewSet), ('fotos', FotoViewSet),
                     ('horarios', HorarioViewSet), ('tarifas', TarifaViewSet), ('politicas', PoliticaViewSet)]:
    router.register(prefix, view, basename='catalogo-' + prefix)
urlpatterns = router.urls
