from django.db import migrations, models


def archive(apps, schema_editor):
    alias = schema_editor.connection.alias
    Policy = apps.get_model('catalogo', 'PoliticaReserva')
    Backup = apps.get_model('catalogo', 'RespaldoAnticipo')
    for policy in Policy.objects.using(alias).all().iterator():
        Backup.objects.using(alias).update_or_create(
            politica_id=policy.pk,
            defaults={'sucursal_id': policy.sucursal_id, 'version': policy.version,
                      'porcentaje_anticipo': policy.porcentaje_anticipo},
        )


class Migration(migrations.Migration):
    dependencies = [('catalogo', '0006_remove_cancha_anticipacion_minutos')]
    operations = [
        migrations.CreateModel(
            name='RespaldoAnticipo',
            fields=[
                ('politica_id', models.PositiveBigIntegerField(primary_key=True, editable=False, serialize=False)),
                ('sucursal_id', models.PositiveBigIntegerField(editable=False)),
                ('version', models.PositiveIntegerField(editable=False)),
                ('porcentaje_anticipo', models.DecimalField(max_digits=5, decimal_places=2, null=True, editable=False)),
            ],
        ),
        migrations.RunPython(archive, migrations.RunPython.noop),
    ]
