from django.conf import settings
from django.db import models
from catalogo.models import Timestamped
from config.codigos import correlativo


class Caja(Timestamped):
    sucursal = models.ForeignKey('users.Sucursal', on_delete=models.PROTECT)
    nombre = models.CharField(max_length=100)
    activa = models.BooleanField(default=True)
    sesion_actual = models.OneToOneField('SesionCaja', null=True, blank=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['sucursal', 'nombre'], name='caja_nombre_sucursal')]


class MedioPago(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=60)
    efectivo = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)


class SesionCaja(models.Model):
    caja = models.ForeignKey(Caja, on_delete=models.PROTECT, related_name='sesiones')
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    apertura = models.DateTimeField(auto_now_add=True)
    monto_inicial = models.DecimalField(max_digits=12, decimal_places=2)
    cierre = models.DateTimeField(null=True)
    cerrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    efectivo_esperado = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    efectivo_contado = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    diferencia = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        ordering = ['-pk']
        constraints = [models.CheckConstraint(condition=models.Q(monto_inicial__gte=0), name='caja_inicial_no_negativo')]


class MovimientoCaja(models.Model):
    sesion = models.ForeignKey(SesionCaja, on_delete=models.PROTECT, related_name='movimientos')
    tipo = models.CharField(max_length=10, choices=[('COBRO','Cobro'),('INGRESO','Ingreso'),('RETIRO','Retiro')])
    importe = models.DecimalField(max_digits=12, decimal_places=2)
    efectivo = models.BooleanField(default=True)
    motivo = models.CharField(max_length=250)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-pk']
        constraints = [models.CheckConstraint(condition=models.Q(importe__gt=0), name='caja_movimiento_positivo')]


class ReciboPago(models.Model):
    @property
    def codigo_correlativo(self):
        # Keep the existing receipt prefix and minimum width for historical receipts.
        return correlativo('SRM', self.pk, 8)

    pago = models.OneToOneField('reservas.PagoReserva', on_delete=models.PROTECT, related_name='recibo')
    movimiento = models.OneToOneField(MovimientoCaja, on_delete=models.PROTECT)
    medio = models.ForeignKey(MedioPago, on_delete=models.PROTECT)
    clave = models.UUIDField(unique=True)
    solicitud = models.JSONField()
    datos = models.JSONField()
    referencia_externa = models.CharField(max_length=120, blank=True)
    recibido = models.DecimalField(max_digits=14, decimal_places=2)
    cambio = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-pk']


class LineaPago(models.Model):
    recibo = models.ForeignKey(ReciboPago, on_delete=models.PROTECT, related_name='lineas')
    medio = models.ForeignKey(MedioPago, on_delete=models.PROTECT)
    movimiento = models.OneToOneField(MovimientoCaja, on_delete=models.PROTECT, related_name='linea_pago')
    importe = models.DecimalField(max_digits=12, decimal_places=2)
    recibido = models.DecimalField(max_digits=12, decimal_places=2)
    cambio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    referencia_externa = models.CharField(max_length=120, blank=True)
    verificado = models.BooleanField(default=False)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(importe__gt=0), name='linea_pago_importe_positivo'),
            models.CheckConstraint(condition=models.Q(recibido__gte=models.F('importe')), name='linea_pago_recibido_suficiente'),
            models.CheckConstraint(condition=models.Q(cambio__gte=0), name='linea_pago_cambio_no_negativo'),
        ]
