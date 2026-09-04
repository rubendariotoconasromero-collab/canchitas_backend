from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class Sucursal(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    direccion = models.CharField(max_length=255, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    estado = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sucursales'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, correo, password=None, **extra_fields):
        if not correo:
            raise ValueError('El correo es obligatorio.')
        correo = self.normalize_email(correo)
        user = self.model(correo=correo, username=correo, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, correo, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('estado', True)
        extra_fields.setdefault('nombre', 'Administrador')
        return self.create_user(correo, password, **extra_fields)


class User(AbstractUser):
    username = models.CharField(max_length=150, unique=True, blank=True)
    nombre = models.CharField(max_length=255)
    correo = models.EmailField(unique=True)
    estado = models.BooleanField(default=True)
    sucursal = models.ForeignKey(Sucursal, null=True, blank=True, on_delete=models.SET_NULL, related_name='usuarios')

    USERNAME_FIELD = 'correo'
    REQUIRED_FIELDS = ['nombre']

    objects = UserManager()

    class Meta:
        db_table = 'users'
        ordering = ['-id']

    def save(self, *args, **kwargs):
        if not self.username:
            self.username = self.correo
        super().save(*args, **kwargs)

    def __str__(self):
        return self.correo


class Rol(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True)
    estado = models.BooleanField(default=True)
    permisos = models.ManyToManyField('Permiso', through='RolPermiso', related_name='roles')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'roles'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Permiso(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True)
    estado = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'permisos'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class RolPermiso(models.Model):
    rol = models.ForeignKey(Rol, on_delete=models.CASCADE, related_name='roles_permisos')
    permiso = models.ForeignKey(Permiso, on_delete=models.CASCADE, related_name='roles_permisos')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rol_permiso'
        constraints = [
            models.UniqueConstraint(fields=['rol', 'permiso'], name='unique_rol_permiso'),
        ]

    def __str__(self):
        return f'{self.rol} - {self.permiso}'


class RolPermisoUsuario(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='roles_permisos_usuario')
    rol_permiso = models.ForeignKey(RolPermiso, on_delete=models.CASCADE, related_name='usuarios_asignados')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rol_permiso_usuario'
        constraints = [
            models.UniqueConstraint(fields=['user', 'rol_permiso'], name='unique_user_rol_permiso'),
        ]

    def __str__(self):
        return f'{self.user} - {self.rol_permiso}'
