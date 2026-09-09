from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    LoginView,
    LogoutView,
    MeView,
    PermisoViewSet,
    RolPermisoViewSet,
    RolViewSet,
    SucursalViewSet,
    UserViewSet,
    health,
)

router = DefaultRouter()
router.register('usuarios', UserViewSet, basename='usuarios')
router.register('roles', RolViewSet, basename='roles')
router.register('permisos', PermisoViewSet, basename='permisos')
router.register('rol-permisos', RolPermisoViewSet, basename='rol-permisos')
router.register('sucursales', SucursalViewSet, basename='sucursales')

urlpatterns = [
    path('health/', health),
    path('health', health),
    path('login/', LoginView.as_view()),
    path('login', LoginView.as_view()),
    path('logout/', LogoutView.as_view()),
    path('logout', LogoutView.as_view()),
    path('me/', MeView.as_view()),
    path('me', MeView.as_view()),
    path('', include(router.urls)),
]
