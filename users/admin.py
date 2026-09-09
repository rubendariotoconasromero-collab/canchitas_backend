from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.core.exceptions import ValidationError
from rest_framework.authtoken.models import Token

from .models import Permiso, Rol, RolPermiso, RolPermisoUsuario, Sucursal, User


class TechnicalAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser and request.user.estado

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


class RecoveryUserForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User

    def clean(self):
        data = super().clean()
        roots = list(User.objects.select_for_update().filter(is_superuser=True).order_by('pk'))
        if any(root.pk == self.instance.pk for root in roots):
            remains_active = data.get('estado') and data.get('is_active') and data.get('is_superuser')
            if not remains_active and not any(root.pk != self.instance.pk and root.estado and root.is_active for root in roots):
                raise ValidationError('No puedes desactivar la ultima cuenta de recuperacion.')
        if data.get('estado') != data.get('is_active'):
            raise ValidationError('Estado e is_active deben coincidir.')
        return data


@admin.register(User)
class UserAdmin(TechnicalAdmin, DjangoUserAdmin):
    form = RecoveryUserForm

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change:
            Token.objects.filter(user=obj).delete()

    def response_change(self, request, obj):
        Token.objects.filter(user=obj).delete()
        return super().response_change(request, obj)

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


admin.site.register(Sucursal, TechnicalAdmin)
admin.site.register(Rol, TechnicalAdmin)
admin.site.register(Permiso, TechnicalAdmin)
admin.site.register(RolPermiso, TechnicalAdmin)
admin.site.register(RolPermisoUsuario, TechnicalAdmin)
