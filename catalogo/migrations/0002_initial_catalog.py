from django.db import migrations


def initialize(apps, schema_editor):
    db = schema_editor.connection.alias
    Business = apps.get_model('catalogo', 'Negocio')
    Branch = apps.get_model('users', 'Sucursal')
    Policy = apps.get_model('catalogo', 'PoliticaReserva')
    Permission = apps.get_model('users', 'Permiso')
    business, _ = Business.objects.using(db).get_or_create(nombre_comercial='Canchitas', defaults={'moneda': 'BOB'})
    for branch in Branch.objects.using(db).all():
        if not branch.negocio_id:
            branch.negocio_id = business.pk
        if not branch.slug_publico:
            branch.slug_publico = f'sucursal-{branch.pk}'
        branch.save(using=db)
        Policy.objects.using(db).get_or_create(sucursal_id=branch.pk, version=1,
                                             defaults={'horas_limite_cancelacion': 5, 'activa': True})
    for resource in ('negocio', 'sucursales', 'deportes', 'canchas', 'horarios', 'tarifas', 'politicas'):
        for action in ('ver', 'crear', 'editar', 'desactivar'):
            Permission.objects.using(db).get_or_create(nombre=f'{resource}.{action}',
                                                       defaults={'descripcion': f'{action} {resource}', 'estado': True})


class Migration(migrations.Migration):
    dependencies = [('catalogo', '0001_initial'),
                    ('users', '0003_sucursal_latitud_sucursal_longitud_sucursal_negocio_and_more')]
    operations = [migrations.RunPython(initialize, migrations.RunPython.noop)]
