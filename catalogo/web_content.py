import re
from urllib.parse import urlsplit
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Max
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from users.access import effective_permissions
from .images import validate_image
from .models import Negocio, ConfiguracionWeb, SlideWeb, SeccionWeb, ContactoAdicional, RedSocial
from .views import CatalogViewSet


def initialize_web(business):
    config, _ = ConfiguracionWeb.objects.get_or_create(negocio=business)
    if not business.slides_web.exists():
        for order, title in enumerate(['Tu proximo partido', 'El espacio para tu equipo', 'Vive el deporte']):
            SlideWeb.objects.create(negocio=business, titulo=title, orden=order)
    for order, (kind, title) in enumerate([('NOSOTROS', 'Sobre nosotros'), ('INSTALACIONES', 'Nuestras instalaciones'), ('SERVICIOS', 'Nuestros servicios')]):
        SeccionWeb.objects.get_or_create(negocio=business, tipo=kind, defaults={'titulo': title, 'orden': order})
    return config


def publication_status(business):
    valid = business.slides_web.filter(publicada=True).exclude(archivo='').exclude(texto_alternativo='').count()
    pending = [] if valid >= 3 else ['Publica al menos tres slides con imagen y descripcion alternativa.']
    if not business.publicado:
        pending.append('Publica primero la configuracion general del negocio.')
    return {'slides_publicados': valid, 'pendientes': pending}


class ContentSerializer(serializers.ModelSerializer):
    validate_archivo = staticmethod(validate_image)

    def to_internal_value(self, data):
        forbidden = set(data) & {'negocio', 'orden'}
        if forbidden:
            raise ValidationError({key: 'Este valor se administra automaticamente.' for key in forbidden})
        return super().to_internal_value(data)

    def validate(self, attrs):
        current = lambda key, default='': attrs.get(key, getattr(self.instance, key, default))
        if current('archivo') and not str(current('texto_alternativo')).strip():
            raise ValidationError({'texto_alternativo': 'Describe el contenido de la imagen.'})
        if self.Meta.model is SlideWeb and current('publicada', False) and not current('archivo'):
            raise ValidationError({'archivo': 'Sube una imagen antes de publicar el slide.'})
        business = Negocio.objects.first()
        unique = 'tipo' if self.Meta.model is SeccionWeb else 'plataforma' if self.Meta.model is RedSocial else None
        if unique and self.Meta.model.objects.filter(negocio=business, **{unique: current(unique)}).exclude(pk=self.instance.pk if self.instance else None).exists():
            raise ValidationError({unique: 'Ya existe un registro de este tipo. Edita el existente.'})
        if self.Meta.model is SeccionWeb and current('publicada', False) and not str(current('contenido')).strip():
            raise ValidationError({'contenido': 'Completa el contenido antes de publicar.'})
        if self.Meta.model is ContactoAdicional:
            value = current('valor')
            if current('tipo') == 'CORREO':
                try: validate_email(value)
                except DjangoValidationError: raise ValidationError({'valor': 'Correo invalido.'})
            elif not re.fullmatch(r'\+?[0-9 ()-]{7,40}', value) or not 7 <= len(re.sub(r'\D', '', value)) <= 15:
                raise ValidationError({'valor': 'Introduce un telefono valido, con codigo de pais para WhatsApp.'})
        if self.Meta.model is RedSocial:
            domains = {'FACEBOOK': {'facebook.com', 'www.facebook.com'}, 'INSTAGRAM': {'instagram.com', 'www.instagram.com'},
                       'TIKTOK': {'tiktok.com', 'www.tiktok.com'}, 'YOUTUBE': {'youtube.com', 'www.youtube.com', 'youtu.be'},
                       'X': {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'}}
            url = urlsplit(current('url'))
            if url.scheme != 'https' or url.hostname not in domains.get(current('plataforma'), set()) or url.username or url.password or url.netloc != url.hostname:
                raise ValidationError({'url': 'Usa un enlace HTTPS oficial de la red social seleccionada.'})
        return attrs


class ConfigSerializer(ContentSerializer):
    class Meta:
        model = ConfiguracionWeb
        fields = '__all__'
        read_only_fields = ['negocio', 'id', 'created_at', 'updated_at']
        validators = []


class SlideSerializer(ContentSerializer):
    class Meta:
        model = SlideWeb
        fields = '__all__'
        read_only_fields = ['negocio', 'orden', 'id', 'created_at', 'updated_at']
        validators = []


class SectionSerializer(ContentSerializer):
    class Meta:
        model = SeccionWeb
        fields = '__all__'
        read_only_fields = ['negocio', 'orden', 'id', 'created_at', 'updated_at']
        validators = []


class ContactSerializer(ContentSerializer):
    class Meta:
        model = ContactoAdicional
        fields = '__all__'
        read_only_fields = ['negocio', 'orden', 'id', 'created_at', 'updated_at']
        validators = []


class SocialSerializer(ContentSerializer):
    class Meta:
        model = RedSocial
        fields = '__all__'
        read_only_fields = ['negocio', 'orden', 'id', 'created_at', 'updated_at']
        validators = []


class WebContentViewSet(CatalogViewSet):
    resource = 'contenido_web'
    global_only = True
    branch_lookup = None
    state_field = 'publicada'

    @property
    def action_permissions(self):
        return {**super().action_permissions, 'mover': ('contenido_web.editar',), 'publicacion': ('contenido_web.ver',)}

    def lock_scope(self):
        super().lock_scope()
        self.business = Negocio.objects.select_for_update().first()
        if not self.business:
            raise ValidationError('Configura el negocio primero.')

    def get_queryset(self):
        qs = self.queryset.filter(negocio=Negocio.objects.first())
        return qs.order_by('orden', 'pk') if hasattr(qs.model, 'orden') else qs.order_by('pk')

    def refresh_publication(self):
        config = ConfiguracionWeb.objects.filter(negocio=self.business, publicada=True).first()
        if config:
            errors = publication_status(self.business)['pendientes']
            if errors: raise ValidationError({'publicada': errors})

    def save_record(self, serializer):
        if serializer.instance and serializer.validated_data.get('publicada') is False and serializer.instance.publicada:
            if 'contenido_web.desactivar' not in effective_permissions(self.request.user):
                raise PermissionDenied('No tienes permiso para ocultar contenido.')
        values = {'negocio': self.business}
        if not serializer.instance and hasattr(serializer.Meta.model, 'orden'):
            last = self.get_queryset().aggregate(n=Max('orden'))['n']
            values['orden'] = 0 if last is None else last + 1
        obj = serializer.save(**values)
        self.refresh_publication()
        return obj

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def mover(self, request, *args, **kwargs):
        direction = request.data.get('direccion')
        if direction not in ('subir', 'bajar'):
            raise ValidationError({'direccion': 'Usa subir o bajar.'})
        self.lock_scope()
        obj = self.get_object()
        if not hasattr(obj, 'orden'): raise ValidationError('Este registro no tiene orden.')
        rows = list(self.get_queryset().select_for_update())
        index = next(i for i, row in enumerate(rows) if row.pk == obj.pk)
        target = index + (-1 if direction == 'subir' else 1)
        if 0 <= target < len(rows): rows[index], rows[target] = rows[target], rows[index]
        for position, row in enumerate(rows): row.orden = position
        self.queryset.model.objects.bulk_update(rows, ['orden'])
        return Response(self.get_serializer(rows, many=True).data)


class ConfigWebViewSet(WebContentViewSet):
    queryset = ConfiguracionWeb.objects.all()
    serializer_class = ConfigSerializer
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    @action(detail=False, methods=['get'])
    def publicacion(self, request):
        business = Negocio.objects.first()
        if not business: raise ValidationError('Configura el negocio primero.')
        return Response(publication_status(business))


class SlideWebViewSet(WebContentViewSet):
    queryset = SlideWeb.objects.all()
    serializer_class = SlideSerializer


class SectionWebViewSet(WebContentViewSet):
    queryset = SeccionWeb.objects.all()
    serializer_class = SectionSerializer


class ContactWebViewSet(WebContentViewSet):
    queryset = ContactoAdicional.objects.all()
    serializer_class = ContactSerializer


class SocialWebViewSet(WebContentViewSet):
    queryset = RedSocial.objects.all()
    serializer_class = SocialSerializer
