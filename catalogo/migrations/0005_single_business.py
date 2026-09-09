from django.db import migrations, models


def verify_single_business(apps, schema_editor):
    Business = apps.get_model('catalogo', 'Negocio')
    records = Business.objects.using(schema_editor.connection.alias)
    if records.count() > 1:
        raise RuntimeError('Hay varios negocios. Revisar los datos antes de aplicar la restriccion; no se eliminara ninguno automaticamente.')
    if not records.exists():
        records.create(nombre_comercial='Soccer Real Madrid')


class Migration(migrations.Migration):
    dependencies = [('catalogo', '0004_business_name')]
    operations = [
        migrations.RunPython(verify_single_business, migrations.RunPython.noop),
        migrations.AddField(model_name='negocio', name='singleton',
                            field=models.BooleanField(default=True, unique=True, editable=False)),
        migrations.AddConstraint(model_name='negocio', constraint=models.CheckConstraint(
            condition=models.Q(singleton=True), name='negocio_unico')),
    ]
