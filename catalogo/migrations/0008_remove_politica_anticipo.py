from django.db import migrations


def archive(apps, schema_editor):
    # Refresh immediately before removal, including policies created after 0007.
    alias = schema_editor.connection.alias
    Policy = apps.get_model('catalogo', 'PoliticaReserva')
    Backup = apps.get_model('catalogo', 'RespaldoAnticipo')
    for policy in Policy.objects.using(alias).all().iterator():
        Backup.objects.using(alias).update_or_create(
            politica_id=policy.pk,
            defaults={'sucursal_id': policy.sucursal_id, 'version': policy.version,
                      'porcentaje_anticipo': policy.porcentaje_anticipo},
        )


def restore(apps, schema_editor):
    alias = schema_editor.connection.alias
    Policy = apps.get_model('catalogo', 'PoliticaReserva')
    Backup = apps.get_model('catalogo', 'RespaldoAnticipo')
    for row in Backup.objects.using(alias).all().iterator():
        Policy.objects.using(alias).filter(pk=row.politica_id, sucursal_id=row.sucursal_id,
                                         version=row.version).update(porcentaje_anticipo=row.porcentaje_anticipo)


class Migration(migrations.Migration):
    dependencies = [('catalogo', '0007_respaldo_anticipo')]
    operations = [
        migrations.RunPython(archive, restore),
        migrations.RemoveConstraint(model_name='politicareserva', name='politica_anticipo_valido'),
        migrations.RemoveField(model_name='politicareserva', name='porcentaje_anticipo'),
    ]
