from django.utils import timezone
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied, ValidationError

from .access import ActionPermission, assigned_permissions, effective_permissions, is_global

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
    permission_classes = [IsAuthenticated, ActionPermission]
    action_permissions = {
        'list': ('usuarios.ver',), 'retrieve': ('usuarios.ver',),
        'create': ('usuarios.crear',), 'update': ('usuarios.editar',),
        'partial_update': ('usuarios.editar',), 'destroy': ('usuarios.cambiar_estado',),
        'asignaciones': ('usuarios.asignar_accesos',),
        'sync_asignaciones': ('usuarios.asignar_accesos',),
    }
    serializer_class = UserSerializer
    queryset = User.objects.select_related('sucursal').prefetch_related(
        'roles_permisos_usuario__rol_permiso__rol',
        'roles_permisos_usuario__rol_permiso__permiso',
    ).all()

    def get_queryset(self):
        qs = super().get_queryset()
        if not is_global(self.request.user):
            qs = qs.filter(sucursal_id=self.request.user.sucursal_id, is_superuser=False).exclude(
                roles_permisos_usuario__rol_permiso__permiso__nombre='alcance.global',
                roles_permisos_usuario__rol_permiso__permiso__estado=True,
                roles_permisos_usuario__rol_permiso__rol__estado=True,
            )
        return qs.distinct()

    def protect_target(self, user, new_state=None):
        if user.is_superuser and not self.request.user.is_superuser:
            raise PermissionDenied('Solo un superusuario puede modificar esta cuenta.')
        if not self.request.user.is_superuser:
            # Password resets must not allow taking over a more privileged account.
            if not assigned_permissions(user).issubset(effective_permissions(self.request.user)):
                raise PermissionDenied('No puedes modificar una cuenta con privilegios superiores a los tuyos.')
        if user.is_superuser and new_state is False:
            others = User.objects.filter(is_superuser=True, estado=True, is_active=True).exclude(pk=user.pk)
            if not others.exists():
                raise ValidationError('No puedes desactivar la ultima cuenta de recuperacion.')

    def perform_update(self, serializer):
        self.protect_target(serializer.instance, serializer.validated_data.get('estado'))
        serializer.save()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            'data': serializer.data,
            'message': 'Usuario creado correctamente.',
        }, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        # Lock recovery accounts first so concurrent deactivations cannot remove all of them.
        list(User.objects.select_for_update().filter(is_superuser=True).order_by('pk'))
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        instance = User.objects.select_for_update().get(pk=instance.pk)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({'data': serializer.data, 'message': 'Usuario actualizado correctamente.'})

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        list(User.objects.select_for_update().filter(is_superuser=True).order_by('pk'))
        user = self.get_object()
        user = User.objects.select_for_update().get(pk=user.pk)
        self.protect_target(user, not user.estado)
        user.estado = not user.estado
        user.is_active = user.estado
        user.save(update_fields=['estado', 'is_active'])
        Token.objects.filter(user=user).delete()
        estado = 'activado' if user.estado else 'desactivado'
        return Response({'data': UserSerializer(user).data, 'message': f'Usuario {estado} correctamente.'})

    @action(detail=True, methods=['get'])
    def asignaciones(self, request, pk=None):
        self.get_object()
        ids = RolPermisoUsuario.objects.filter(user_id=pk).values_list('rol_permiso_id', flat=True)
        return Response({'data': list(ids)})

    @action(detail=True, methods=['post'], url_path='asignaciones/sync')
    @transaction.atomic
    def sync_asignaciones(self, request, pk=None):
        user = self.get_object()
        user = User.objects.select_for_update().get(pk=user.pk)
        self.protect_target(user)
        if user.pk == request.user.pk:
            raise PermissionDenied('No puedes modificar tus propios accesos.')
        serializer = SyncAsignacionesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = set(serializer.validated_data['rol_permiso_ids'])
        requested = list(RolPermiso.objects.select_for_update().filter(pk__in=ids).select_related('rol', 'permiso'))
        if len(requested) != len(ids):
            raise ValidationError('El catalogo cambio; vuelve a cargar los accesos.')
        current = set(RolPermisoUsuario.objects.filter(user=user).values_list('rol_permiso_id', flat=True))
        if not request.user.is_superuser:
            allowed = effective_permissions(request.user)
            from .access import LEGACY_READ
            changed = RolPermiso.objects.filter(pk__in=current ^ ids).select_related('permiso', 'rol')
            for relation in changed:
                code = LEGACY_READ.get(relation.permiso.nombre, relation.permiso.nombre)
                if code not in allowed or code == 'alcance.global':
                    raise PermissionDenied('No puedes delegar ni retirar estos privilegios.')
        for relation in requested:
            if relation.pk not in current and (not relation.rol.estado or not relation.permiso.estado):
                raise ValidationError('No puedes agregar accesos inactivos.')
        RolPermisoUsuario.objects.filter(user=user, rol_permiso_id__in=current - ids).delete()
        RolPermisoUsuario.objects.bulk_create([
            RolPermisoUsuario(user=user, rol_permiso_id=rol_permiso_id)
            for rol_permiso_id in ids - current
        ])
        return Response({'message': 'Asignaciones sincronizadas correctamente.'})


class RolViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    global_only = True
    action_permissions = {
        'list': ('roles.ver', 'usuarios.asignar_accesos'), 'retrieve': ('roles.ver',),
        'create': ('roles.crear',), 'update': ('roles.editar',),
        'partial_update': ('roles.editar',), 'destroy': ('roles.cambiar_estado',),
        'permisos': ('roles.asignar_permisos',), 'sync_permisos': ('roles.asignar_permisos',),
    }

    def get_permissions(self):
        self.global_only = self.action not in ('list', 'retrieve', 'permisos')
        return super().get_permissions()

    def perform_update(self, serializer):
        if 'estado' in serializer.validated_data and serializer.validated_data['estado'] != serializer.instance.estado:
            if 'roles.cambiar_estado' not in effective_permissions(self.request.user):
                raise PermissionDenied('No puedes cambiar el estado del rol.')
        serializer.save()
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
    @transaction.atomic
    def sync_permisos(self, request, pk=None):
        rol = self.get_object()
        rol = Rol.objects.select_for_update().get(pk=rol.pk)
        serializer = SyncRolPermisosSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = set(serializer.validated_data['permiso_ids'])
        current = set(RolPermiso.objects.filter(rol=rol).values_list('permiso_id', flat=True))
        RolPermiso.objects.filter(rol=rol, permiso_id__in=current - ids).delete()
        RolPermiso.objects.bulk_create([
            RolPermiso(rol=rol, permiso_id=permiso_id)
            for permiso_id in ids - current
        ])
        return Response({'message': 'Permisos del rol sincronizados correctamente.'})


class PermisoViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    global_only = True
    action_permissions = {
        'list': ('permisos.ver', 'roles.asignar_permisos'), 'retrieve': ('permisos.ver',),
        'create': ('permisos.crear',), 'update': ('permisos.editar',),
        'partial_update': ('permisos.editar',), 'destroy': ('permisos.cambiar_estado',),
    }

    def get_permissions(self):
        self.global_only = self.action not in ('list', 'retrieve')
        return super().get_permissions()

    def perform_update(self, serializer):
        if 'estado' in serializer.validated_data and serializer.validated_data['estado'] != serializer.instance.estado:
            if 'permisos.cambiar_estado' not in effective_permissions(self.request.user):
                raise PermissionDenied('No puedes cambiar el estado del permiso.')
        serializer.save()
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
    permission_classes = [IsAuthenticated, ActionPermission]
    action_permissions = {'list': ('usuarios.asignar_accesos',), 'retrieve': ('usuarios.asignar_accesos',)}
    queryset = RolPermiso.objects.select_related('rol', 'permiso').all()
    serializer_class = RolPermisoSerializer


class SucursalViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    action_permissions = {
        'list': ('sucursales.ver', 'usuarios.crear', 'usuarios.editar'),
        'retrieve': ('sucursales.ver', 'usuarios.crear', 'usuarios.editar'),
    }

    def get_queryset(self):
        qs = super().get_queryset()
        return qs if is_global(self.request.user) else qs.filter(pk=self.request.user.sucursal_id)
    queryset = Sucursal.objects.filter(estado=True)
    serializer_class = SucursalSerializer
