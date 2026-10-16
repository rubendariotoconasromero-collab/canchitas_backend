from django.db import migrations


def populate(apps, schema_editor):
    db = schema_editor.connection.alias
    Business = apps.get_model('catalogo', 'Negocio')
    Config = apps.get_model('catalogo', 'ConfiguracionWeb')
    Slide = apps.get_model('catalogo', 'SlideWeb')
    Section = apps.get_model('catalogo', 'SeccionWeb')
    for business in Business.objects.using(db).all():
        Config.objects.using(db).get_or_create(negocio=business)
        for order, title in enumerate(['Tu proximo partido', 'El espacio para tu equipo', 'Vive el deporte']):
            Slide.objects.using(db).create(negocio=business, titulo=title, orden=order)
        for order, (kind, title) in enumerate([('NOSOTROS', 'Sobre nosotros'), ('INSTALACIONES', 'Nuestras instalaciones'), ('SERVICIOS', 'Nuestros servicios')]):
            Section.objects.using(db).get_or_create(negocio=business, tipo=kind, defaults={'titulo': title, 'orden': order})


class Migration(migrations.Migration):
    dependencies = [('catalogo', '0010_deporte_archivo_deporte_descripcion_publica_and_more')]
    operations = [migrations.RunPython(populate, migrations.RunPython.noop)]
