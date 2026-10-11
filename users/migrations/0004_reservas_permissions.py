from django.db import migrations


def create_permissions(apps, schema_editor):
    Permission = apps.get_model('users', 'Permiso')
    codes = ['clientes.ver', 'clientes.crear', 'clientes.editar', 'clientes.desactivar',
             'reservas.ver', 'reservas.crear', 'reservas.alquilar_inmediato',
             'reservas.reprogramar', 'reservas.cancelar', 'reservas.confirmar',
             'reservas.iniciar', 'reservas.finalizar', 'bloqueos.ver', 'bloqueos.crear', 'bloqueos.liberar']
    for code in codes:
        Permission.objects.get_or_create(nombre=code, defaults={'descripcion': code.replace('.', ': ')})


class Migration(migrations.Migration):
    dependencies = [('users', '0003_sucursal_latitud_sucursal_longitud_sucursal_negocio_and_more')]
    operations = [migrations.RunPython(create_permissions, migrations.RunPython.noop)]
