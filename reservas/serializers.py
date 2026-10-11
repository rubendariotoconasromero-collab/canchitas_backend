from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from rest_framework import serializers
from catalogo.models import Cancha, CanchaDeporte
from .models import Cliente, Reserva, DetalleReserva, HistorialReserva, BloqueoCancha


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - {k for k, v in self.fields.items() if not v.read_only}
        if unknown:
            raise serializers.ValidationError({k: 'Campo no permitido.' for k in unknown})
        return super().to_internal_value(data)


class ClienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = ['id', 'sucursal', 'nombre', 'telefono', 'correo', 'documento', 'activo']
        read_only_fields = ['id']


class IntervalSerializer(StrictSerializer):
    inicio = serializers.DateTimeField(required=False)
    fin = serializers.DateTimeField(required=False)
    fecha = serializers.DateField(required=False)
    hora_inicio = serializers.TimeField(required=False)
    hora_fin = serializers.TimeField(required=False)
    cruza_medianoche = serializers.BooleanField(default=False)

    def interval(self, data, zone):
        local = 'fecha' in data or 'hora_inicio' in data or 'hora_fin' in data
        if local:
            if 'inicio' in data or 'fin' in data or not all(k in data for k in ['fecha', 'hora_inicio', 'hora_fin']):
                raise serializers.ValidationError('Indica fecha y ambos horarios, sin mezclar formatos.')
            tz = ZoneInfo(zone)
            for key, time_key, day_offset in [('inicio', 'hora_inicio', 0), ('fin', 'hora_fin', int(data['cruza_medianoche']))]:
                naive = datetime.combine(data['fecha'] + timedelta(days=day_offset), data[time_key])
                aware = naive.replace(tzinfo=tz)
                # Reject nonexistent or ambiguous local times instead of silently shifting them.
                from datetime import timezone as utc
                if aware.astimezone(utc.utc).astimezone(tz).replace(tzinfo=None) != naive or aware.utcoffset() != aware.replace(fold=1).utcoffset():
                    raise serializers.ValidationError('Horario local ambiguo o inexistente.')
                data[key] = aware
        elif not all(k in data for k in ['inicio', 'fin']):
            raise serializers.ValidationError('Indica inicio y fin.')
        else:
            from django.utils.dateparse import parse_datetime
            for key in ['inicio', 'fin']:
                value = self.initial_data[key]
                parsed = parse_datetime(value) if isinstance(value, str) else value
                if not parsed or timezone.is_naive(parsed):
                    raise serializers.ValidationError('Los horarios ISO deben incluir zona horaria.')
        if data['fin'] <= data['inicio']:
            raise serializers.ValidationError('El fin debe ser posterior al inicio.')
        return data


class BookingInput(IntervalSerializer):
    cancha_deporte = serializers.PrimaryKeyRelatedField(queryset=CanchaDeporte.objects.select_related('cancha__sucursal', 'deporte'))
    cliente = serializers.PrimaryKeyRelatedField(queryset=Cliente.objects.all(), required=False)
    sin_cliente = serializers.BooleanField(default=False)
    alquiler_inmediato = serializers.BooleanField(default=False)
    duracion_minutos = serializers.IntegerField(min_value=1, max_value=1440, required=False)
    notas = serializers.CharField(max_length=2000, allow_blank=True, required=False)
    total_esperado = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    def validate(self, data):
        if data['sin_cliente'] and 'cliente' in data:
            raise serializers.ValidationError('Selecciona un cliente o Sin cliente registrado, no ambos.')
        if data['alquiler_inmediato']:
            if 'duracion_minutos' not in data or any(k in data for k in ['inicio', 'fin', 'fecha', 'hora_inicio', 'hora_fin']):
                raise serializers.ValidationError('El alquiler inmediato requiere solo duracion en minutos.')
            return data
        return self.interval(data, data['cancha_deporte'].cancha.sucursal.zona_horaria)


class MoveInput(IntervalSerializer):
    total_esperado = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    def validate(self, data):
        return self.interval(data, self.context['zona_horaria'])


class ReasonInput(StrictSerializer):
    motivo = serializers.CharField(max_length=255)


class BlockInput(IntervalSerializer):
    cancha = serializers.PrimaryKeyRelatedField(queryset=Cancha.objects.select_related('sucursal'))
    motivo = serializers.CharField(max_length=255)

    def validate(self, data):
        return self.interval(data, data['cancha'].sucursal.zona_horaria)


class DetailSerializer(serializers.ModelSerializer):
    cancha = serializers.IntegerField(source='cancha_deporte.cancha_id')
    class Meta:
        model = DetalleReserva
        fields = ['cancha', 'cancha_deporte', 'inicio', 'fin', 'zona_horaria', 'cancha_nombre', 'deporte_nombre', 'desglose_tarifa']


class HistorySerializer(serializers.ModelSerializer):
    actor_nombre = serializers.CharField(source='actor.nombre', default=None)
    class Meta:
        model = HistorialReserva
        fields = ['accion', 'estado_anterior', 'estado_nuevo', 'datos', 'actor_nombre', 'created_at']


class ReservaSerializer(serializers.ModelSerializer):
    detalle = DetailSerializer(read_only=True)
    historial = HistorySerializer(many=True, read_only=True)
    estado = serializers.SerializerMethodField()
    total_pagado = serializers.SerializerMethodField()
    saldo_pendiente = serializers.SerializerMethodField()
    class Meta:
        model = Reserva
        fields = ['id', 'codigo', 'sucursal', 'cliente', 'cliente_snapshot', 'estado', 'origen', 'total', 'moneda',
                  'retencion_hasta', 'horas_cancelacion', 'condiciones', 'notas', 'detalle', 'historial', 'total_pagado', 'saldo_pendiente']

    def get_estado(self, obj):
        return 'EXPIRADA' if obj.estado == 'PENDIENTE' and obj.retencion_hasta <= timezone.now() else obj.estado

    def get_total_pagado(self, obj):
        return str(obj.pago.importe) if hasattr(obj, 'pago') else '0.00'

    def get_saldo_pendiente(self, obj):
        if self.get_estado(obj) in [Reserva.Estado.CANCELADA, Reserva.Estado.EXPIRADA]:
            return '0.00'
        return str(max(obj.total - (obj.pago.importe if hasattr(obj, 'pago') else 0), 0))


class BlockSerializer(serializers.ModelSerializer):
    cancha_nombre = serializers.CharField(source='cancha.nombre')
    class Meta:
        model = BloqueoCancha
        fields = ['id', 'cancha', 'cancha_nombre', 'inicio', 'fin', 'motivo', 'activo', 'liberado_at']
