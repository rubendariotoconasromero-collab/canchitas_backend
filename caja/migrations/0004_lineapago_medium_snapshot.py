from django.db import migrations, models


def snapshot_medium(apps, schema_editor):
    database = schema_editor.connection.alias
    line_model = apps.get_model('caja', 'LineaPago')
    for line in line_model.objects.using(database).select_related('medio').iterator():
        line.medio_nombre = line.medio.nombre[:60]
        line.medio_efectivo = line.medio.efectivo
        line.save(using=database, update_fields=['medio_nombre', 'medio_efectivo'])


class Migration(migrations.Migration):
    dependencies = [('caja', '0003_mixed_payment_lines')]

    operations = [
        migrations.AddField(
            model_name='lineapago',
            name='medio_nombre',
            field=models.CharField(default='', editable=False, max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='lineapago',
            name='medio_efectivo',
            field=models.BooleanField(default=False, editable=False),
            preserve_default=False,
        ),
        migrations.RunPython(snapshot_medium, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='lineapago',
            name='medio_nombre',
            field=models.CharField(editable=False, max_length=60),
        ),
        migrations.AlterField(
            model_name='lineapago',
            name='medio_efectivo',
            field=models.BooleanField(editable=False),
        ),
    ]
