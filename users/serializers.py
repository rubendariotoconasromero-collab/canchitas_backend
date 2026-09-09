from django.contrib.auth import authenticate, password_validation
from rest_framework import serializers
from rest_framework.authtoken.models import Token

from .access import PERMISSIONS, LEGACY_READ, effective_permissions, is_global

from .models import Permiso, Rol, RolPermiso, RolPermisoUsuario, Sucursal, User


class SucursalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sucursal
        fields = ['id', 'nombre', 'direccion', 'telefono', 'estado']


class PermisoSerializer(serializers.ModelSerializer):
    def validate_nombre(self, value):
        if self.instance and self.instance.nombre in PERMISSIONS | LEGACY_READ.keys():
            if value != self.instance.nombre:
                raise serializers.ValidationError('El codigo de un permiso del sistema no se puede renombrar.')
        return value

    class Meta:
        model = Permiso
        fields = ['id', 'nombre', 'descripcion', 'estado']


class RolSerializer(serializers.ModelSerializer):
    permisos = serializers.SerializerMethodField()

    class Meta:
        model = Rol
        fields = ['id', 'nombre', 'descripcion', 'estado', 'permisos']

    def get_permisos(self, obj):
        permisos = []
        for rol_permiso in obj.roles_permisos.select_related('permiso').all():
            permiso = rol_permiso.permiso
            permisos.append({
                'id': permiso.id,
                'nombre': permiso.nombre,
                'descripcion': permiso.descripcion,
                'estado': permiso.estado,
                'pivot': {'id': rol_permiso.id},
            })
        return permisos


class RolPermisoSerializer(serializers.ModelSerializer):
    rol = RolSerializer(read_only=True)
    permiso = PermisoSerializer(read_only=True)

    class Meta:
        model = RolPermiso
        fields = ['id', 'rol', 'permiso']


class RolPermisoUsuarioSerializer(serializers.ModelSerializer):
    rol_permiso = RolPermisoSerializer(read_only=True)

    class Meta:
        model = RolPermisoUsuario
        fields = ['id', 'rol_permiso']


class UserSerializer(serializers.ModelSerializer):
    permisos_efectivos = serializers.SerializerMethodField()
    alcance_global = serializers.SerializerMethodField()
    sucursal = SucursalSerializer(read_only=True)
    sucursal_id = serializers.PrimaryKeyRelatedField(
        queryset=Sucursal.objects.all(),
        source='sucursal',
        allow_null=True,
        required=False,
        write_only=True,
    )
    roles_permisos = RolPermisoUsuarioSerializer(source='roles_permisos_usuario', many=True, read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            'id',
            'nombre',
            'correo',
            'password',
            'estado',
            'sucursal',
            'sucursal_id',
            'roles_permisos',
            'date_joined',
            'permisos_efectivos',
            'alcance_global',
        ]
        read_only_fields = ['id', 'date_joined']

    def get_permisos_efectivos(self, obj):
        return sorted(effective_permissions(obj))

    def get_alcance_global(self, obj):
        return is_global(obj)

    def validate_password(self, value):
        if value:
            password_validation.validate_password(value)
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get('password'):
            raise serializers.ValidationError({'password': ['El password es obligatorio.']})
        request = self.context.get('request')
        if request:
            actor = request.user
            if not is_global(actor):
                branch = attrs.get('sucursal', self.instance.sucursal if self.instance else actor.sucursal)
                if not branch or branch.id != actor.sucursal_id or not branch.estado:
                    raise serializers.ValidationError({'sucursal_id': ['Solo puedes operar en tu sucursal activa.']})
                attrs['sucursal'] = branch
            elif 'sucursal' in attrs and attrs['sucursal'] and not attrs['sucursal'].estado:
                raise serializers.ValidationError({'sucursal_id': ['La sucursal esta inactiva.']})
            if self.instance and 'estado' in attrs and attrs['estado'] != self.instance.estado:
                if 'usuarios.cambiar_estado' not in effective_permissions(actor):
                    raise serializers.ValidationError({'estado': ['No tienes permiso para cambiar el estado.']})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        validated_data['is_active'] = validated_data.get('estado', True)
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.is_active = instance.estado
        instance.save()
        if password or not instance.estado:
            Token.objects.filter(user=instance).delete()
        return instance


class LoginSerializer(serializers.Serializer):
    correo = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(username=attrs['correo'], password=attrs['password'])
        if not user:
            raise serializers.ValidationError({'correo': ['Las credenciales proporcionadas son incorrectas.']})
        if not user.estado:
            raise serializers.ValidationError({'correo': ['Tu cuenta esta desactivada. Contacta al administrador.']})
        attrs['user'] = user
        return attrs


class SyncAsignacionesSerializer(serializers.Serializer):
    rol_permiso_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
        required=True,
    )

    def validate_rol_permiso_ids(self, value):
        existing = set(RolPermiso.objects.filter(id__in=value).values_list('id', flat=True))
        missing = sorted(set(value) - existing)
        if missing:
            raise serializers.ValidationError(f'Asignaciones inexistentes: {missing}')
        return sorted(set(value))


class SyncRolPermisosSerializer(serializers.Serializer):
    permiso_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
        required=True,
    )

    def validate_permiso_ids(self, value):
        existing = set(Permiso.objects.filter(id__in=value).values_list('id', flat=True))
        missing = sorted(set(value) - existing)
        if missing:
            raise serializers.ValidationError(f'Permisos inexistentes: {missing}')
        return sorted(set(value))
