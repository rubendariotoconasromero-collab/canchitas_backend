from .models import ConfiguracionWeb, Deporte


def image_url(obj, request):
    return request.build_absolute_uri(obj.archivo.url) if obj.archivo else ''


def web_data(business, request):
    config = ConfiguracionWeb.objects.filter(negocio=business, publicada=True).first()
    if not config:
        return None
    from reservas.public_api import public_links
    sports = Deporte.objects.filter(pk__in=public_links().values('deporte_id'), activo=True).order_by('nombre')
    return {
        'lema': config.lema, 'descripcion_intro': config.descripcion_intro, 'horario_atencion': config.horario_atencion,
        'seo_titulo': config.seo_titulo, 'seo_descripcion': config.seo_descripcion,
        'mostrar_deportes': config.mostrar_deportes, 'mostrar_canchas': config.mostrar_canchas, 'mostrar_contacto': config.mostrar_contacto,
        'slides': [{'id': s.pk, 'titulo': s.titulo, 'descripcion': s.descripcion, 'imagen': image_url(s, request),
                    'alt': s.texto_alternativo, 'texto_boton': s.texto_boton if (s.destino == 'SEGUIMIENTO' or
                        (s.destino == 'CANCHAS' and config.mostrar_canchas) or (s.destino == 'CONTACTO' and config.mostrar_contacto)) else '', 'destino': s.destino}
                   for s in business.slides_web.filter(publicada=True).exclude(archivo='')],
        'secciones': [{'id': s.pk, 'tipo': s.tipo, 'titulo': s.titulo, 'contenido': s.contenido,
                       'imagen': image_url(s, request), 'alt': s.texto_alternativo}
                      for s in business.secciones_web.filter(publicada=True)],
        'deportes': [{'id': s.pk, 'nombre': s.nombre, 'descripcion': s.descripcion_publica,
                      'imagen': image_url(s, request), 'alt': s.texto_alternativo} for s in sports] if config.mostrar_deportes else [],
        'contactos': [{'id': c.pk, 'tipo': c.tipo, 'etiqueta': c.etiqueta, 'valor': c.valor}
                      for c in business.contactos_web.filter(publicada=True)] if config.mostrar_contacto else [],
        'redes': [{'id': r.pk, 'plataforma': r.plataforma, 'url': r.url} for r in business.redes_web.filter(publicada=True)]
    }
