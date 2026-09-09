from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from rest_framework import serializers
from users.models import Sucursal
from .models import Negocio, Deporte, Cancha, CanchaDeporte, FotoCancha, HorarioCancha, TarifaCancha, PoliticaReserva
from .rules import interval, overlaps, publication_errors


class NegocioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Negocio
        fields = '__all__'

    def validate(self, attrs):
        data = {field.name: getattr(self.instance, field.name) for field in self.Meta.model._meta.fields} if self.instance else {}
        data.update(attrs)
        if data.get('publicado') and not all(data.get(key) for key in ('nombre_comercial', 'descripcion_publica', 'telefono')):
            raise serializers.ValidationError('Completa nombre, descripcion y telefono antes de publicar.')
        return attrs


class SucursalCatalogoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sucursal
        fields = '__all__'
        extra_kwargs = {'negocio': {'required': True, 'allow_null': False},
                        'slug_publico': {'required': True, 'allow_null': False, 'allow_blank': False}}

    def validate_zona_horaria(self, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise serializers.ValidationError('Zona horaria IANA invalida.')
        return value

    def validate_latitud(self, value):
        if value is not None and not -90 <= value <= 90:
            raise serializers.ValidationError('Latitud entre -90 y 90.')
        return value

    def validate_longitud(self, value):
        if value is not None and not -180 <= value <= 180:
            raise serializers.ValidationError('Longitud entre -180 y 180.')
        return value


class DeporteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Deporte
        fields = '__all__'


class CanchaSerializer(serializers.ModelSerializer):
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    pendientes_publicacion = serializers.SerializerMethodField()

    class Meta:
        model = Cancha
        fields = '__all__'

    def get_pendientes_publicacion(self, obj):
        return publication_errors(obj)

    def validate(self, attrs):
        if self.instance and 'sucursal' in attrs and attrs['sucursal'].pk != self.instance.sucursal_id:
            raise serializers.ValidationError('No puedes trasladar una cancha a otra sucursal.')
        if attrs.get('publicada') and not self.instance:
            raise serializers.ValidationError('Crea la cancha como borrador y completa su configuracion antes de publicar.')
        return attrs


class CanchaDeporteSerializer(serializers.ModelSerializer):
    deporte_nombre = serializers.CharField(source='deporte.nombre', read_only=True)

    class Meta:
        model = CanchaDeporte
        fields = '__all__'


class FotoSerializer(serializers.ModelSerializer):
    publicada = serializers.BooleanField(default=True)
    class Meta:
        model = FotoCancha
        fields = '__all__'

    def validate_archivo(self, value):
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError('La imagen no puede superar 5 MB.')
        if value.image.format not in ('JPEG', 'PNG', 'WEBP'):
            raise serializers.ValidationError('Usa JPEG, PNG o WebP.')
        if value.image.width * value.image.height > 25_000_000:
            raise serializers.ValidationError('La imagen supera 25 megapixeles.')
        from pathlib import Path
        suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[value.image.format]
        value.name = Path(value.name).stem + suffix
        return value


class IntervalSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        fields = [f.name for f in self.Meta.model._meta.fields]
        data = {name: getattr(self.instance, name) for name in fields} if self.instance else {}
        data.update(attrs)
        data.setdefault('cruza_medianoche', False)
        tariff = self.Meta.model is TarifaCancha
        active = 'activa' if tariff else 'activo'
        data.setdefault(active, True)
        if tariff:
            data.setdefault('vigente_hasta', None)
            if data['vigente_hasta'] and data['vigente_hasta'] < data['vigente_desde']:
                raise serializers.ValidationError('La vigencia final debe ser posterior o igual a la inicial.')
        keys = ('hora_desde', 'hora_hasta') if tariff else ('apertura', 'cierre')
        interval(data[keys[0]], data[keys[1]], data['cruza_medianoche'])
        owner = 'cancha_deporte' if tariff else 'cancha'
        others = self.Meta.model.objects.filter(**{owner: data[owner], active: True})
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if data[active]:
            for other in others.values():
                if overlaps(data, other, tariff):
                    raise serializers.ValidationError('El intervalo se superpone con otro registro activo.')
        return attrs


class HorarioSerializer(IntervalSerializer):
    class Meta:
        model = HorarioCancha
        fields = '__all__'


class TarifaSerializer(IntervalSerializer):
    class Meta:
        model = TarifaCancha
        fields = '__all__'


class PoliticaSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        if self.instance and 'sucursal' not in attrs:
            attrs['sucursal'] = self.instance.sucursal
        return attrs

    class Meta:
        model = PoliticaReserva
        fields = '__all__'
        read_only_fields = ['version', 'activa']
        validators = []

    def validate_porcentaje_anticipo(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError('El anticipo debe ser mayor que cero o quedar pendiente.')
        return value
