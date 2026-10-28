from decimal import Decimal
from rest_framework import serializers
from reservas.models import Reserva
from .models import Caja, MedioPago, SesionCaja, MovimientoCaja, ReciboPago, LineaPago
from .services import summary


class CajaSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    zona_horaria = serializers.CharField(source='sucursal.zona_horaria', read_only=True)
    class Meta:
        model = Caja
        fields = ['id','nombre','sucursal','sucursal_nombre','zona_horaria','activa','sesion_actual']
        read_only_fields = ['sesion_actual']


class CajaUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Caja
        fields = ['nombre', 'activa']

    def to_internal_value(self, data):
        if set(data) - set(self.fields):
            raise serializers.ValidationError({'non_field_errors': ['Solo se puede modificar el nombre y el estado de la caja.']})
        return super().to_internal_value(data)

    def validate_nombre(self, value):
        if Caja.objects.filter(sucursal_id=self.instance.sucursal_id, nombre=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError('Ya existe una caja con este nombre en la sucursal.')
        return value


class SessionSerializer(serializers.ModelSerializer):
    caja_nombre = serializers.CharField(source='caja.nombre')
    sucursal = serializers.IntegerField(source='caja.sucursal_id')
    responsable_nombre = serializers.CharField(source='responsable.nombre')
    resumen = serializers.SerializerMethodField()
    class Meta:
        model = SesionCaja
        fields = '__all__'
    def get_resumen(self, obj):
        return summary(obj)


class MovementSerializer(serializers.ModelSerializer):
    actor_nombre = serializers.CharField(source='actor.nombre')
    medio = serializers.SerializerMethodField()
    class Meta:
        model = MovimientoCaja
        fields = '__all__'
    def get_medio(self, obj):
        line = getattr(obj, 'linea_pago', None)
        if line:
            return line.medio_nombre
        return 'Efectivo'


class PaymentLineSerializer(serializers.ModelSerializer):
    medio_nombre = serializers.CharField(read_only=True)
    efectivo = serializers.BooleanField(source='medio_efectivo', read_only=True)
    sesion = serializers.IntegerField(source='movimiento.sesion_id', read_only=True)

    class Meta:
        model = LineaPago
        fields = ['id', 'medio', 'medio_nombre', 'efectivo', 'importe', 'recibido', 'cambio',
                  'referencia_externa', 'verificado', 'sesion']


class ReceiptSerializer(serializers.ModelSerializer):
    codigo_correlativo = serializers.CharField(read_only=True)
    codigo_pago = serializers.CharField(source='pago.codigo_correlativo', read_only=True)
    codigo_reserva = serializers.CharField(source='pago.reserva.codigo_correlativo', read_only=True)
    numero = serializers.SerializerMethodField()
    reserva = serializers.IntegerField(source='pago.reserva_id')
    sesion = serializers.IntegerField(source='movimiento.sesion_id')
    lineas = PaymentLineSerializer(many=True, read_only=True)
    class Meta:
        model = ReciboPago
        exclude = ['solicitud', 'clave']
    def get_numero(self, obj):
        return obj.codigo_correlativo


class OpenInput(serializers.Serializer):
    monto_inicial = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)

class CloseInput(serializers.Serializer):
    efectivo_contado = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    observaciones = serializers.CharField(max_length=2000, allow_blank=True, default='')

class MovementInput(serializers.Serializer):
    tipo = serializers.ChoiceField(choices=['INGRESO','RETIRO'])
    importe = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    motivo = serializers.CharField(max_length=250)


class ChargeLineInput(serializers.Serializer):
    medio = serializers.PrimaryKeyRelatedField(queryset=MedioPago.objects.all())
    importe = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    recibido = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    referencia = serializers.CharField(max_length=120, allow_blank=True, default='')
    verificado = serializers.BooleanField(default=False)


class ChargeInput(serializers.Serializer):
    sesion = serializers.PrimaryKeyRelatedField(queryset=SesionCaja.objects.all())
    reserva = serializers.PrimaryKeyRelatedField(queryset=Reserva.objects.all())
    clave = serializers.UUIDField()
    importe = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    iniciar = serializers.BooleanField(default=False)
    pagos = ChargeLineInput(many=True, required=False, allow_empty=False)
    medio = serializers.PrimaryKeyRelatedField(queryset=MedioPago.objects.all(), required=False)
    recibido = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    referencia = serializers.CharField(max_length=120, allow_blank=True, default='', required=False)
    verificado = serializers.BooleanField(default=False, required=False)

    def validate(self, attrs):
        if attrs.get('pagos') is not None:
            if attrs.get('medio') or 'recibido' in attrs or attrs.get('referencia') or attrs.get('verificado'):
                raise serializers.ValidationError('Usa pagos o los campos de medio anteriores, no ambos formatos.')
        elif 'medio' not in attrs or 'recibido' not in attrs:
            raise serializers.ValidationError({'pagos': 'Registra al menos una forma de pago.'})
        return attrs
