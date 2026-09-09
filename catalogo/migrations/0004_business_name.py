from django.db import migrations


def rename_default_business(apps, schema_editor):
    Business = apps.get_model('catalogo', 'Negocio')
    Business.objects.using(schema_editor.connection.alias).filter(
        nombre_comercial='Canchitas',
    ).update(nombre_comercial='Soccer Real Madrid')


class Migration(migrations.Migration):
    dependencies = [('catalogo', '0003_horariocancha_horario_intervalo_valido_and_more')]
    operations = [migrations.RunPython(rename_default_business, migrations.RunPython.noop)]
