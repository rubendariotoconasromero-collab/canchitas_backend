from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Permiso, Rol, RolPermiso, RolPermisoUsuario, Sucursal, User
from .serializers import (
    LoginSerializer,
    PermisoSerializer,
    RolPermisoSerializer,
    RolSerializer,
    SucursalSerializer,
    SyncAsignacionesSerializer,
    SyncRolPermisosSerializer,
    UserSerializer,
)


@api_view(['GET'])
@permission_classes([AllowAny])
def health(request):
    return Response({
        'app': 'canchitas',
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
    })


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        user_data = UserSerializer(user).data
        return Response({'token': token.key, 'user': user_data})


class LogoutView(APIView):
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response({'message': 'Sesion cerrada correctamente.'})


class MeView(APIView):
    def get(self, request):
        return Response({'user': UserSerializer(request.user).data})


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    queryset = User.objects.select_related('sucursal').prefetch_related(
        'roles_permisos_usuario__rol_permiso__rol',
        'roles_permisos_usuario__rol_permiso__permiso',
    ).all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            'data': serializer.data,
            'message': 'Usuario creado correctamente.',
        }, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({'data': serializer.data, 'message': 'Usuario actualizado correctamente.'})

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        user.estado = not user.estado
        user.is_active = user.estado
        user.save(update_fields=['estado', 'is_active'])
        estado = 'activado' if user.estado else 'desactivado'
        return Response({'data': UserSerializer(user).data, 'message': f'Usuario {estado} correctamente.'})

    @action(detail=True, methods=['get'])
    def asignaciones(self, request, pk=None):
        self.get_object()
        ids = RolPermisoUsuario.objects.filter(user_id=pk).values_list('rol_permiso_id', flat=True)
        return Response({'data': list(ids)})

    @action(detail=True, methods=['post'], url_path='asignaciones/sync')
    def sync_asignaciones(self, request, pk=None):
        user = self.get_object()
        serializer = SyncAsignacionesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data['rol_permiso_ids']
        RolPermisoUsuario.objects.filter(user=user).delete()
        RolPermisoUsuario.objects.bulk_create([
            RolPermisoUsuario(user=user, rol_permiso_id=rol_permiso_id)
            for rol_permiso_id in ids
        ])
        return Response({'message': 'Asignaciones sincronizadas correctamente.'})


class RolViewSet(viewsets.ModelViewSet):
    queryset = Rol.objects.prefetch_related('roles_permisos__permiso').all()
    serializer_class = RolSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            'data': serializer.data,
            'message': 'Rol creado correctamente.',
        }, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({'data': serializer.data, 'message': 'Rol actualizado correctamente.'})

    def destroy(self, request, *args, **kwargs):
        rol = self.get_object()
        rol.estado = not rol.estado
        rol.save(update_fields=['estado'])
        estado = 'activado' if rol.estado else 'desactivado'
        return Response({'data': RolSerializer(rol).data, 'message': f'Rol {estado} correctamente.'})

    @action(detail=True, methods=['get'])
    def permisos(self, request, pk=None):
        self.get_object()
        ids = RolPermiso.objects.filter(rol_id=pk).values_list('permiso_id', flat=True)
        return Response({'data': list(ids)})

    @action(detail=True, methods=['post'], url_path='permisos/sync')
    def sync_permisos(self, request, pk=None):
        rol = self.get_object()
        serializer = SyncRolPermisosSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data['permiso_ids']
        RolPermiso.objects.filter(rol=rol).delete()
        RolPermiso.objects.bulk_create([
            RolPermiso(rol=rol, permiso_id=permiso_id)
            for permiso_id in ids
        ])
        return Response({'message': 'Permisos del rol sincronizados correctamente.'})


class PermisoViewSet(viewsets.ModelViewSet):
    queryset = Permiso.objects.all()
    serializer_class = PermisoSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            'data': serializer.data,
            'message': 'Permiso creado correctamente.',
        }, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({'data': serializer.data, 'message': 'Permiso actualizado correctamente.'})

    def destroy(self, request, *args, **kwargs):
        permiso = self.get_object()
        permiso.estado = not permiso.estado
        permiso.save(update_fields=['estado'])
        estado = 'activado' if permiso.estado else 'desactivado'
        return Response({'data': PermisoSerializer(permiso).data, 'message': f'Permiso {estado} correctamente.'})


class RolPermisoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RolPermiso.objects.select_related('rol', 'permiso').all()
    serializer_class = RolPermisoSerializer


class SucursalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Sucursal.objects.filter(estado=True)
    serializer_class = SucursalSerializer
