from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('catalogo', '0005_single_business')]
    operations = [migrations.RemoveField(model_name='cancha', name='anticipacion_minutos')]
