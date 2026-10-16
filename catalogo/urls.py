from rest_framework.routers import DefaultRouter
from .views import (NegocioViewSet, SucursalViewSet, DeporteViewSet, CanchaViewSet,
                    CanchaDeporteViewSet, FotoViewSet, HorarioViewSet, TarifaViewSet, PoliticaViewSet)

router = DefaultRouter()
from .web_content import ConfigWebViewSet, SlideWebViewSet, SectionWebViewSet, ContactWebViewSet, SocialWebViewSet
for prefix, view in [('configuracion-web', ConfigWebViewSet), ('slides-web', SlideWebViewSet),
                     ('secciones-web', SectionWebViewSet), ('contactos-web', ContactWebViewSet), ('redes-web', SocialWebViewSet)]:
    router.register(prefix, view, basename='catalogo-' + prefix)
for prefix, view in [('negocios', NegocioViewSet), ('sucursales', SucursalViewSet),
                     ('deportes', DeporteViewSet), ('canchas', CanchaViewSet),
                     ('cancha-deportes', CanchaDeporteViewSet), ('fotos', FotoViewSet),
                     ('horarios', HorarioViewSet), ('tarifas', TarifaViewSet), ('politicas', PoliticaViewSet)]:
    router.register(prefix, view, basename='catalogo-' + prefix)
urlpatterns = router.urls
