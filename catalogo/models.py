from uuid import uuid4
from pathlib import Path
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from config.codigos import correlativo


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Negocio(Timestamped):
    singleton = models.BooleanField(default=True, unique=True, editable=False)
    nombre_comercial = models.CharField(max_length=150)
    descripcion_publica = models.TextField(blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    correo_publico = models.EmailField(blank=True)
    logo_url = models.URLField(blank=True)
    portada_url = models.URLField(blank=True)
    archivo = models.ImageField(upload_to='web/logos/', blank=True)
    sitio_web = models.URLField(blank=True)
    moneda = models.CharField(max_length=3, default='BOB', choices=[('BOB', 'Bolivianos')])
    publicado = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(singleton=True), name='negocio_unico')]

    def __str__(self):
        return self.nombre_comercial


class Deporte(Timestamped):
    nombre = models.CharField(max_length=100, unique=True)
    activo = models.BooleanField(default=True)
    descripcion_publica = models.TextField(blank=True)
    archivo = models.ImageField(upload_to='web/deportes/', blank=True)
    texto_alternativo = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['nombre']


class Cancha(Timestamped):
    @property
    def codigo_correlativo(self):
        return correlativo('CAN', self.pk)

    sucursal = models.ForeignKey('users.Sucursal', on_delete=models.PROTECT, related_name='canchas')
    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=120)
    descripcion_publica = models.TextField(blank=True)
    superficie = models.CharField(max_length=100, blank=True)
    ancho_metros = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True,
                                      validators=[MinValueValidator(Decimal('0.01'))])
    largo_metros = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True,
                                      validators=[MinValueValidator(Decimal('0.01'))])
    techada = models.BooleanField(default=False)
    iluminacion = models.BooleanField(default=False)
    capacidad_personas = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1)])
    duracion_minima_minutos = models.PositiveIntegerField(default=60, validators=[MinValueValidator(1)])
    activa = models.BooleanField(default=True)
    publicada = models.BooleanField(default=False)

    class Meta:
        ordering = ['sucursal_id', 'nombre']
        constraints = [models.UniqueConstraint(fields=['sucursal', 'codigo'], name='cancha_codigo_sucursal'),
                       models.CheckConstraint(condition=models.Q(duracion_minima_minutos__gt=0), name='cancha_minimo_positivo'),
                       models.CheckConstraint(
                           condition=(models.Q(ancho_metros__isnull=True, largo_metros__isnull=True) |
                                      models.Q(ancho_metros__isnull=False, largo_metros__isnull=False,
                                               ancho_metros__gt=0, largo_metros__gt=0)),
                           name='cancha_dimensiones_validas')]

    @property
    def area_m2(self):
        if self.ancho_metros is None or self.largo_metros is None:
            return None
        return self.ancho_metros * self.largo_metros


class CanchaDeporte(Timestamped):
    cancha = models.ForeignKey(Cancha, on_delete=models.PROTECT, related_name='deportes')
    deporte = models.ForeignKey(Deporte, on_delete=models.PROTECT)
    activo = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cancha', 'deporte'], name='cancha_deporte_unico')]


def photo_path(instance, filename):
    return f'canchas/{instance.cancha_id}/{uuid4().hex}{Path(filename).suffix.lower()}'


class FotoCancha(Timestamped):
    cancha = models.ForeignKey(Cancha, on_delete=models.PROTECT, related_name='fotos')
    archivo = models.ImageField(upload_to=photo_path)
    texto_alternativo = models.CharField(max_length=200)
    orden = models.PositiveIntegerField(default=0)
    publicada = models.BooleanField(default=True)

    class Meta:
        ordering = ['orden', 'id']


class HorarioCancha(Timestamped):
    cancha = models.ForeignKey(Cancha, on_delete=models.PROTECT, related_name='horarios')
    dia_semana = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(7)])
    apertura = models.TimeField()
    cierre = models.TimeField()
    cruza_medianoche = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['dia_semana', 'apertura']
        constraints = [models.CheckConstraint(condition=models.Q(dia_semana__range=(1, 7)), name='horario_dia_valido'),
                       models.CheckConstraint(condition=(models.Q(cruza_medianoche=False, cierre__gt=models.F('apertura')) |
                                                         models.Q(cruza_medianoche=True, cierre__lte=models.F('apertura'))),
                                              name='horario_intervalo_valido')]


class TarifaCancha(Timestamped):
    cancha_deporte = models.ForeignKey(CanchaDeporte, on_delete=models.PROTECT, related_name='tarifas')
    nombre = models.CharField(max_length=100)
    dia_semana = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(7)])
    hora_desde = models.TimeField()
    hora_hasta = models.TimeField()
    cruza_medianoche = models.BooleanField(default=False)
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)
    precio_hora = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['dia_semana', 'hora_desde', 'vigente_desde']
        constraints = [models.CheckConstraint(condition=models.Q(precio_hora__gt=0), name='tarifa_precio_positivo'),
                       models.CheckConstraint(condition=models.Q(dia_semana__range=(1, 7)), name='tarifa_dia_valido'),
                       models.CheckConstraint(condition=(models.Q(cruza_medianoche=False, hora_hasta__gt=models.F('hora_desde')) |
                                                         models.Q(cruza_medianoche=True, hora_hasta__lte=models.F('hora_desde'))),
                                              name='tarifa_intervalo_valido'),
                       models.CheckConstraint(condition=models.Q(vigente_hasta__isnull=True) | models.Q(vigente_hasta__gte=models.F('vigente_desde')),
                                              name='tarifa_vigencia_valida')]


class PoliticaReserva(Timestamped):
    pago_total_obligatorio = True
    permite_reembolso = False
    sucursal = models.ForeignKey('users.Sucursal', on_delete=models.PROTECT, related_name='politicas')
    version = models.PositiveIntegerField()
    horas_limite_cancelacion = models.PositiveIntegerField(default=5)
    minutos_retencion = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    condiciones = models.TextField(blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['sucursal', 'version'], name='politica_version_unica')]


class RespaldoAnticipo(models.Model):
    """Archivo de la configuracion retirada; no participa en reglas ni en la API."""

    politica_id = models.PositiveBigIntegerField(primary_key=True, editable=False)
    sucursal_id = models.PositiveBigIntegerField(editable=False)
    version = models.PositiveIntegerField(editable=False)
    porcentaje_anticipo = models.DecimalField(max_digits=5, decimal_places=2, null=True, editable=False)


class ConfiguracionWeb(Timestamped):
    negocio = models.OneToOneField(Negocio, on_delete=models.PROTECT, related_name='web')
    lema = models.CharField(max_length=150, blank=True)
    descripcion_intro = models.CharField(max_length=500, blank=True)
    horario_atencion = models.CharField(max_length=200, blank=True)
    seo_titulo = models.CharField(max_length=160, blank=True)
    seo_descripcion = models.CharField(max_length=320, blank=True)
    mostrar_deportes = models.BooleanField(default=True)
    mostrar_canchas = models.BooleanField(default=True)
    mostrar_contacto = models.BooleanField(default=True)
    publicada = models.BooleanField(default=False)


def web_image_path(instance, filename):
    return f'web/contenido/{uuid4().hex}{Path(filename).suffix.lower()}'


class SlideWeb(Timestamped):
    negocio = models.ForeignKey(Negocio, on_delete=models.PROTECT, related_name='slides_web')
    titulo = models.CharField(max_length=150)
    descripcion = models.CharField(max_length=400, blank=True)
    archivo = models.ImageField(upload_to=web_image_path, blank=True)
    texto_alternativo = models.CharField(max_length=200, blank=True)
    texto_boton = models.CharField(max_length=50, default='Ver canchas')
    destino = models.CharField(max_length=20, default='CANCHAS', choices=[('CANCHAS','Canchas'),('CONTACTO','Contacto'),('SEGUIMIENTO','Seguimiento')])
    orden = models.PositiveIntegerField(default=0)
    publicada = models.BooleanField(default=False)

    class Meta:
        ordering = ['orden','pk']


class SeccionWeb(Timestamped):
    negocio = models.ForeignKey(Negocio, on_delete=models.PROTECT, related_name='secciones_web')
    tipo = models.CharField(max_length=20, choices=[('NOSOTROS','Nosotros'),('INSTALACIONES','Instalaciones'),('SERVICIOS','Servicios')])
    titulo = models.CharField(max_length=150)
    contenido = models.TextField(blank=True)
    archivo = models.ImageField(upload_to=web_image_path, blank=True)
    texto_alternativo = models.CharField(max_length=200, blank=True)
    orden = models.PositiveIntegerField(default=0)
    publicada = models.BooleanField(default=False)

    class Meta:
        ordering = ['orden','pk']
        constraints = [models.UniqueConstraint(fields=['negocio','tipo'],name='seccion_web_tipo_unico')]


class ContactoAdicional(Timestamped):
    negocio = models.ForeignKey(Negocio, on_delete=models.PROTECT, related_name='contactos_web')
    tipo = models.CharField(max_length=20, choices=[('TELEFONO','Telefono'),('WHATSAPP','WhatsApp'),('CORREO','Correo')])
    etiqueta = models.CharField(max_length=80)
    valor = models.CharField(max_length=254)
    orden = models.PositiveIntegerField(default=0)
    publicada = models.BooleanField(default=False)

    class Meta:
        ordering = ['orden','pk']


class RedSocial(Timestamped):
    negocio = models.ForeignKey(Negocio, on_delete=models.PROTECT, related_name='redes_web')
    plataforma = models.CharField(max_length=20, choices=[(x,x.title()) for x in ['FACEBOOK','INSTAGRAM','TIKTOK','YOUTUBE','X']])
    url = models.URLField()
    orden = models.PositiveIntegerField(default=0)
    publicada = models.BooleanField(default=False)

    class Meta:
        ordering = ['orden','pk']
        constraints = [models.UniqueConstraint(fields=['negocio','plataforma'],name='red_web_plataforma_unica')]
