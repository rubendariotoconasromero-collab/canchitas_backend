from django.db import migrations


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model('users', 'Permiso')
    Role = apps.get_model('users', 'Rol')
    Link = apps.get_model('users', 'RolPermiso')
    codes = [
        'dashboard.ver', 'usuarios.ver', 'usuarios.crear', 'usuarios.editar',
        'usuarios.cambiar_estado', 'usuarios.asignar_accesos', 'roles.ver',
        'roles.crear', 'roles.editar', 'roles.cambiar_estado', 'roles.asignar_permisos',
        'permisos.ver', 'permisos.crear', 'permisos.editar', 'permisos.cambiar_estado',
        'sucursales.ver', 'reportes.ver', 'alcance.global', 'reservas.ver',
        'reservas.crear', 'reservas.alquilar_inmediato', 'caja.cobrar',
    ]
    db = schema_editor.connection.alias
    permissions = {}
    for code in codes:
        permissions[code], _ = Permission.objects.using(db).get_or_create(
            nombre=code, defaults={'descripcion': code, 'estado': True},
        )
    role, created = Role.objects.using(db).get_or_create(
        nombre='Recepcion', defaults={'descripcion': 'Reservas y cobros en su sucursal', 'estado': True},
    )
    # Never change an existing role or grant new assignments to existing users.
    if created:
        for code in ['dashboard.ver', 'reservas.ver', 'reservas.crear',
                     'reservas.alquilar_inmediato', 'caja.cobrar']:
            Link.objects.using(db).get_or_create(rol=role, permiso=permissions[code])


class Migration(migrations.Migration):
    dependencies = [('users', '0001_initial')]
    operations = [migrations.RunPython(seed_permissions, migrations.RunPython.noop)]
