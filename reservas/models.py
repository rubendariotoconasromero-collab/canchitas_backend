from uuid import uuid4
from django.conf import settings
from django.db import models
from catalogo.models import Timestamped


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
    origen = models.CharField(max_length=15, choices=[('RECEPCION', 'Recepcion'), ('INMEDIATO', 'Alquiler inmediato')])
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
    reserva = models.OneToOneField(Reserva, on_delete=models.PROTECT, related_name='pago')
    referencia = models.UUIDField(unique=True)
    importe = models.DecimalField(max_digits=12, decimal_places=2)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(importe__gt=0), name='pago_reserva_positivo')]
