from django.db import migrations

def initialize(apps, schema_editor):
    db = schema_editor.connection.alias
    Medium = apps.get_model('caja', 'MedioPago')
    Permission = apps.get_model('users', 'Permiso')
    for code, name, cash in [('EFECTIVO','Efectivo',True),('QR','QR',False),('TRANSFERENCIA','Transferencia',False)]:
        Medium.objects.using(db).get_or_create(codigo=code, defaults={'nombre':name,'efectivo':cash})
    for code in ['caja.ver','caja.abrir','caja.cobrar','caja.movimientos','caja.cerrar','caja.configurar']:
        Permission.objects.using(db).get_or_create(nombre=code, defaults={'descripcion':code,'estado':True})

class Migration(migrations.Migration):
    dependencies = [('caja','0001_initial'),('users','0004_reservas_permissions')]
    operations = [migrations.RunPython(initialize, migrations.RunPython.noop)]
