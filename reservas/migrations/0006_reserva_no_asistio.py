from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('reservas', '0005_historial_publico')]
    operations = [migrations.AlterField(
        model_name='reserva', name='estado',
        field=models.CharField(max_length=15, default='PENDIENTE', choices=[
            ('PENDIENTE', 'Pendiente de pago'), ('CONFIRMADA', 'Confirmada'),
            ('EN_CURSO', 'En curso'), ('FINALIZADA', 'Finalizada'),
            ('NO_ASISTIO', 'Cliente no asistio'), ('CANCELADA', 'Cancelada'), ('EXPIRADA', 'Expirada')]),
    )]
