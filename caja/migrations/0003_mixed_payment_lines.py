import django.db.models.deletion
from django.db import migrations, models


def migrate_receipts(apps, schema_editor):
    db = schema_editor.connection.alias
    Receipt = apps.get_model('caja', 'ReciboPago')
    Line = apps.get_model('caja', 'LineaPago')
    Medium = apps.get_model('caja', 'MedioPago')

    for receipt in Receipt.objects.using(db).select_related('pago', 'medio', 'movimiento').iterator():
        Line.objects.using(db).create(
            recibo_id=receipt.pk,
            medio_id=receipt.medio_id,
            movimiento_id=receipt.movimiento_id,
            importe=receipt.movimiento.importe,
            recibido=receipt.recibido,
            cambio=receipt.cambio,
            referencia_externa=receipt.referencia_externa,
            verificado=not receipt.medio.efectivo,
        )

    Medium.objects.using(db).filter(codigo__in=['QR', 'TRANSFERENCIA']).update(activo=False)
    Medium.objects.using(db).get_or_create(
        codigo='TRANSFERENCIA_QR',
        defaults={'nombre': 'Transferencia QR', 'efectivo': False, 'activo': True},
    )


class Migration(migrations.Migration):
    dependencies = [('caja', '0002_initial_cash')]

    operations = [
        migrations.AlterField(
            model_name='recibopago',
            name='recibido',
            field=models.DecimalField(decimal_places=2, max_digits=14),
        ),
        migrations.AlterField(
            model_name='recibopago',
            name='cambio',
            field=models.DecimalField(decimal_places=2, max_digits=14),
        ),
        migrations.CreateModel(
            name='LineaPago',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('importe', models.DecimalField(decimal_places=2, max_digits=12)),
                ('recibido', models.DecimalField(decimal_places=2, max_digits=12)),
                ('cambio', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('referencia_externa', models.CharField(blank=True, max_length=120)),
                ('verificado', models.BooleanField(default=False)),
                ('medio', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='caja.mediopago')),
                ('movimiento', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='linea_pago', to='caja.movimientocaja')),
                ('recibo', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lineas', to='caja.recibopago')),
            ],
            options={'ordering': ['pk']},
        ),
        migrations.AddConstraint(
            model_name='lineapago',
            constraint=models.CheckConstraint(condition=models.Q(('importe__gt', 0)), name='linea_pago_importe_positivo'),
        ),
        migrations.AddConstraint(
            model_name='lineapago',
            constraint=models.CheckConstraint(condition=models.Q(('recibido__gte', models.F('importe'))), name='linea_pago_recibido_suficiente'),
        ),
        migrations.AddConstraint(
            model_name='lineapago',
            constraint=models.CheckConstraint(condition=models.Q(('cambio__gte', 0)), name='linea_pago_cambio_no_negativo'),
        ),
        migrations.RunPython(migrate_receipts, migrations.RunPython.noop),
    ]
