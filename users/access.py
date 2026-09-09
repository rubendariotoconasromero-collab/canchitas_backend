from rest_framework.permissions import BasePermission


PERMISSIONS = {
    'dashboard.ver', 'usuarios.ver', 'usuarios.crear', 'usuarios.editar',
    'usuarios.cambiar_estado', 'usuarios.asignar_accesos', 'roles.ver',
    'roles.crear', 'roles.editar', 'roles.cambiar_estado', 'roles.asignar_permisos',
    'permisos.ver', 'permisos.crear', 'permisos.editar', 'permisos.cambiar_estado',
    'sucursales.ver', 'reportes.ver', 'alcance.global',
    'reservas.ver', 'reservas.crear', 'reservas.alquilar_inmediato', 'caja.cobrar',
}
LEGACY_READ = {
    'acceso_dashboard': 'dashboard.ver', 'acceso_usuarios': 'usuarios.ver',
    'acceso_roles': 'roles.ver', 'acceso_permisos': 'permisos.ver',
    'acceso_sucursales': 'sucursales.ver', 'acceso_reportes': 'reportes.ver',
}

PERMISSIONS.update(f'{resource}.{action}' for resource in
                   ('negocio', 'sucursales', 'deportes', 'canchas', 'horarios', 'tarifas', 'politicas')
                   for action in ('ver', 'crear', 'editar', 'desactivar'))


def effective_permissions(user):
    if not user or not user.is_authenticated or not user.estado or not user.is_active:
        return set()
    if user.is_superuser:
        return set(PERMISSIONS)
    names = set(user.roles_permisos_usuario.filter(
        rol_permiso__rol__estado=True, rol_permiso__permiso__estado=True,
    ).values_list('rol_permiso__permiso__nombre', flat=True))
    result = (names & PERMISSIONS) | {LEGACY_READ[n] for n in names if n in LEGACY_READ}
    if 'alcance.global' not in result and (not user.sucursal_id or not user.sucursal.estado):
        return set()
    return result


def is_global(user):
    return 'alcance.global' in effective_permissions(user)


def assigned_permissions(user):
    names = set(user.roles_permisos_usuario.values_list('rol_permiso__permiso__nombre', flat=True))
    return (names & PERMISSIONS) | {LEGACY_READ[n] for n in names if n in LEGACY_READ}


class ActionPermission(BasePermission):
    message = 'No tienes permiso para esta accion o sucursal.'

    def has_permission(self, request, view):
        allowed = effective_permissions(request.user)
        required = view.action_permissions.get(view.action, ())
        if not required or not any(code in allowed for code in required):
            return False
        if getattr(view, 'global_only', False) and 'alcance.global' not in allowed:
            return False
        return 'alcance.global' in allowed or bool(
            request.user.sucursal_id and request.user.sucursal.estado
        )
