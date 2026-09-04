from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Permiso, Rol, RolPermiso, RolPermisoUsuario, Sucursal, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ('-id',)
    list_display = ('id', 'correo', 'nombre', 'estado', 'sucursal', 'is_staff')
    search_fields = ('correo', 'nombre')
    fieldsets = (
        (None, {'fields': ('correo', 'password')}),
        ('Datos personales', {'fields': ('nombre', 'sucursal', 'estado')}),
        ('Permisos Django', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Fechas', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('correo', 'nombre', 'password1', 'password2', 'is_staff', 'is_superuser'),
        }),
    )


admin.site.register(Sucursal)
admin.site.register(Rol)
admin.site.register(Permiso)
admin.site.register(RolPermiso)
admin.site.register(RolPermisoUsuario)
