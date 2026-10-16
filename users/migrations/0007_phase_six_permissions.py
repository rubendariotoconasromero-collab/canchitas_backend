from django.db import migrations


def populate(apps, schema_editor):
    db = schema_editor.connection.alias
    Permission, Role = apps.get_model('users', 'Permiso'), apps.get_model('users', 'Rol')
    Link, Assignment = apps.get_model('users', 'RolPermiso'), apps.get_model('users', 'RolPermisoUsuario')
    codes = ['reportes.ver', 'reportes.reservas', 'reportes.cobros', 'reportes.cierres',
             'reportes.ocupacion', 'reportes.exportar', 'auditoria.ver', 'avisos.ver']
    grants = {'Administrador': codes,
              'Recepcion': ['reportes.ver', 'reportes.reservas', 'reportes.cobros', 'reportes.ocupacion', 'avisos.ver'],
              'Recepcionista': ['reportes.ver', 'reportes.reservas', 'reportes.cobros', 'reportes.ocupacion', 'avisos.ver'],
              'Cajero': ['reportes.ver', 'reportes.cobros', 'reportes.cierres', 'avisos.ver']}
    for code in codes:
        permission, _ = Permission.objects.using(db).get_or_create(nombre=code,
            defaults={'estado': True, 'descripcion': code})
        for name, allowed in grants.items():
            if code not in allowed:
                continue
            for role in Role.objects.using(db).filter(nombre=name, estado=True):
                users = list(Assignment.objects.using(db).filter(rol_permiso__rol=role)
                             .values_list('user_id', flat=True).distinct())
                link, _ = Link.objects.using(db).get_or_create(rol=role, permiso=permission)
                for user in users:
                    Assignment.objects.using(db).get_or_create(user_id=user, rol_permiso=link)


class Migration(migrations.Migration):
    dependencies = [('users', '0006_web_content_permissions')]
    operations = [migrations.RunPython(populate, migrations.RunPython.noop)]
