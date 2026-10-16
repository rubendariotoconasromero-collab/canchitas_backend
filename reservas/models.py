from uuid import uuid4
from django.conf import settings
from django.db import models
from catalogo.models import Timestamped
from config.codigos import correlativo


class Cliente(Timestamped):
    sucursal = models.ForeignKey('users.Sucursal', on_delete=models.PROTECT)
    nombre = models.CharField(max_length=160)
    telefono = models.CharField(max_length=40)
    correo = models.EmailField(blank=True)
    documento = models.CharField(max_length=50, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre', 'pk']


class Reserva(Timestamped):
    @property
    def codigo_correlativo(self):
        return correlativo('RES', self.pk)

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente de pago'
        CONFIRMADA = 'CONFIRMADA', 'Confirmada'
        EN_CURSO = 'EN_CURSO', 'En curso'
        FINALIZADA = 'FINALIZADA', 'Finalizada'
        CANCELADA = 'CANCELADA', 'Cancelada'
        EXPIRADA = 'EXPIRADA', 'Expirada'

    codigo = models.UUIDField(default=uuid4, unique=True, editable=False)
    sucursal = models.ForeignKey('users.Sucursal', on_delete=models.PROTECT)
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name='reservas')
    cliente_snapshot = models.JSONField()
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PENDIENTE)
    origen = models.CharField(max_length=15, choices=[('RECEPCION', 'Recepcion'), ('INMEDIATO', 'Alquiler inmediato'), ('WEB', 'Solicitud web')])
    total = models.DecimalField(max_digits=12, decimal_places=2)
    moneda = models.CharField(max_length=3, default='BOB', editable=False)
    retencion_hasta = models.DateTimeField()
    horas_cancelacion = models.PositiveIntegerField()
    condiciones = models.TextField(blank=True)
    politica_version = models.PositiveIntegerField()
    notas = models.TextField(blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.CheckConstraint(condition=models.Q(total__gt=0), name='reserva_total_positivo')]
        indexes = [models.Index(fields=['sucursal', 'estado', 'retencion_hasta'])]


class DetalleReserva(models.Model):
    reserva = models.OneToOneField(Reserva, on_delete=models.PROTECT, related_name='detalle')
    cancha_deporte = models.ForeignKey('catalogo.CanchaDeporte', on_delete=models.PROTECT)
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    zona_horaria = models.CharField(max_length=80)
    cancha_nombre = models.CharField(max_length=120)
    deporte_nombre = models.CharField(max_length=100)
    desglose_tarifa = models.JSONField()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(fin__gt=models.F('inicio')), name='reserva_intervalo_valido')]
        indexes = [models.Index(fields=['inicio', 'fin'])]


class BloqueoCancha(Timestamped):
    cancha = models.ForeignKey('catalogo.Cancha', on_delete=models.PROTECT, related_name='bloqueos')
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    motivo = models.CharField(max_length=255)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    liberado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    liberado_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['inicio', 'pk']
        constraints = [models.CheckConstraint(condition=models.Q(fin__gt=models.F('inicio')), name='bloqueo_intervalo_valido')]


class HistorialReserva(models.Model):
    reserva = models.ForeignKey(Reserva, on_delete=models.PROTECT, related_name='historial')
    accion = models.CharField(max_length=30)
    estado_anterior = models.CharField(max_length=15, blank=True)
    estado_nuevo = models.CharField(max_length=15)
    datos = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class PagoReserva(models.Model):
    """Contrato interno para caja; no admite escrituras desde la API de reservas."""
    @property
    def codigo_correlativo(self):
        return correlativo('PAG', self.pk)

    reserva = models.OneToOneField(Reserva, on_delete=models.PROTECT, related_name='pago')
    referencia = models.UUIDField(unique=True)
    importe = models.DecimalField(max_digits=12, decimal_places=2)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(importe__gt=0), name='pago_reserva_positivo')]


class SolicitudReserva(Timestamped):
    @property
    def codigo_correlativo(self):
        return correlativo('SOL', self.pk)

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente de revision'
        ACEPTADA = 'ACEPTADA', 'Aceptada'
        RECHAZADA = 'RECHAZADA', 'Rechazada'
        VENCIDA = 'VENCIDA', 'Vencida'

    codigo = models.UUIDField(default=uuid4, unique=True, editable=False)
    clave = models.UUIDField(unique=True, editable=False)
    huella = models.CharField(max_length=64, editable=False)
    seguimiento_hash = models.CharField(max_length=64, editable=False)
    seguimiento_hasta = models.DateTimeField()
    cancha_deporte = models.ForeignKey('catalogo.CanchaDeporte', on_delete=models.PROTECT)
    inicio = models.DateTimeField()
    fin = models.DateTimeField()
    nombre = models.CharField(max_length=160)
    telefono = models.CharField(max_length=40)
    correo = models.EmailField(blank=True)
    notas = models.CharField(max_length=1000, blank=True)
    total_cotizado = models.DecimalField(max_digits=12, decimal_places=2)
    condiciones_snapshot = models.JSONField(default=dict)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    reserva = models.OneToOneField(Reserva, null=True, blank=True, on_delete=models.PROTECT, related_name='solicitud_web')
    revisado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    revisado_at = models.DateTimeField(null=True, blank=True)
    motivo = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['estado', 'inicio'])]
        constraints = [models.CheckConstraint(condition=models.Q(fin__gt=models.F('inicio')), name='solicitud_intervalo_valido'),
                       models.CheckConstraint(condition=models.Q(total_cotizado__gt=0), name='solicitud_total_positivo')]


class VisitantePublico(models.Model):
    clave_hash = models.CharField(max_length=64, unique=True, editable=False)
    creado_at = models.DateTimeField(auto_now_add=True)
    acceso_hasta = models.DateTimeField(db_index=True)


class SolicitudVisitante(models.Model):
    visitante = models.ForeignKey(VisitantePublico, on_delete=models.CASCADE, related_name='solicitudes')
    solicitud = models.ForeignKey(SolicitudReserva, on_delete=models.CASCADE, related_name='visitantes')
    agregado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['visitante', 'solicitud'], name='solicitud_visitante_unica')]
