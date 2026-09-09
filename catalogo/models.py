from uuid import uuid4
from pathlib import Path
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


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

    class Meta:
        ordering = ['nombre']


class Cancha(Timestamped):
    sucursal = models.ForeignKey('users.Sucursal', on_delete=models.PROTECT, related_name='canchas')
    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=120)
    descripcion_publica = models.TextField(blank=True)
    superficie = models.CharField(max_length=100, blank=True)
    techada = models.BooleanField(default=False)
    iluminacion = models.BooleanField(default=False)
    capacidad_personas = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1)])
    duracion_minima_minutos = models.PositiveIntegerField(default=60, validators=[MinValueValidator(1)])
    activa = models.BooleanField(default=True)
    publicada = models.BooleanField(default=False)

    class Meta:
        ordering = ['sucursal_id', 'nombre']
        constraints = [models.UniqueConstraint(fields=['sucursal', 'codigo'], name='cancha_codigo_sucursal'),
                       models.CheckConstraint(condition=models.Q(duracion_minima_minutos__gt=0), name='cancha_minimo_positivo')]


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
    sucursal = models.ForeignKey('users.Sucursal', on_delete=models.PROTECT, related_name='politicas')
    version = models.PositiveIntegerField()
    horas_limite_cancelacion = models.PositiveIntegerField(default=5)
    porcentaje_anticipo = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                             validators=[MinValueValidator(0), MaxValueValidator(100)])
    minutos_retencion = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    condiciones = models.TextField(blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['sucursal', 'version'], name='politica_version_unica'),
                       models.CheckConstraint(condition=models.Q(porcentaje_anticipo__isnull=True) | models.Q(porcentaje_anticipo__gt=0, porcentaje_anticipo__lte=100),
                                              name='politica_anticipo_valido')]
