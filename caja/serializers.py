from decimal import Decimal
from rest_framework import serializers
from reservas.models import Reserva
from .models import Caja, MedioPago, SesionCaja, MovimientoCaja, ReciboPago
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
        receipt = getattr(obj, 'recibopago', None)
        return receipt.datos['medio'] if receipt else 'Efectivo'


class ReceiptSerializer(serializers.ModelSerializer):
    numero = serializers.SerializerMethodField()
    reserva = serializers.IntegerField(source='pago.reserva_id')
    sesion = serializers.IntegerField(source='movimiento.sesion_id')
    class Meta:
        model = ReciboPago
        exclude = ['solicitud', 'clave']
    def get_numero(self, obj):
        return f'SRM-{obj.pk:08d}'


class OpenInput(serializers.Serializer):
    monto_inicial = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)

class CloseInput(serializers.Serializer):
    efectivo_contado = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    observaciones = serializers.CharField(max_length=2000, allow_blank=True, default='')

class MovementInput(serializers.Serializer):
    tipo = serializers.ChoiceField(choices=['INGRESO','RETIRO'])
    importe = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    motivo = serializers.CharField(max_length=250)

class ChargeInput(serializers.Serializer):
    sesion = serializers.PrimaryKeyRelatedField(queryset=SesionCaja.objects.all())
    reserva = serializers.PrimaryKeyRelatedField(queryset=Reserva.objects.all())
    medio = serializers.PrimaryKeyRelatedField(queryset=MedioPago.objects.all())
    clave = serializers.UUIDField()
    importe = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    recibido = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    referencia = serializers.CharField(max_length=120, allow_blank=True, default='')
    verificado = serializers.BooleanField(default=False)
    iniciar = serializers.BooleanField(default=False)
