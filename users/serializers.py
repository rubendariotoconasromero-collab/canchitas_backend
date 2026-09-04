from django.contrib.auth import authenticate, password_validation
from rest_framework import serializers

from .models import Permiso, Rol, RolPermiso, RolPermisoUsuario, Sucursal, User


class SucursalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sucursal
        fields = ['id', 'nombre', 'direccion', 'telefono', 'estado']


class PermisoSerializer(serializers.ModelSerializer):
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
        ]
        read_only_fields = ['id', 'date_joined']

    def validate_password(self, value):
        if value:
            password_validation.validate_password(value)
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get('password'):
            raise serializers.ValidationError({'password': ['El password es obligatorio.']})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
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
        return value


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
        return value
