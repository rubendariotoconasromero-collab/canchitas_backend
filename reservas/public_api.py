import hashlib
import hmac
import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from uuid import UUID
from config.codigos import identificador_correlativo
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from catalogo.models import Negocio, Cancha, CanchaDeporte
from catalogo.public_content import image_url, web_data
from users.models import Sucursal
from .models import SolicitudReserva, DetalleReserva, BloqueoCancha
from .serializers import IntervalSerializer, StrictSerializer
from . import services


def public_courts():
    return Cancha.objects.filter(activa=True, publicada=True, sucursal__estado=True,
        sucursal__negocio__publicado=True).select_related('sucursal__negocio').prefetch_related('fotos', 'deportes__deporte', 'deportes__tarifas')


def public_links():
    return CanchaDeporte.objects.filter(cancha__in=public_courts(), activo=True, deporte__activo=True).select_related('cancha__sucursal', 'deporte')


class PublicThrottle(SimpleRateThrottle):
    scope = 'public_read'
    def get_rate(self):
        return getattr(settings, 'PUBLIC_API_RATES', {}).get(self.scope, '120/min')
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class WriteThrottle(PublicThrottle):
    scope = 'public_write'
    def get_rate(self):
        return getattr(settings, 'PUBLIC_API_RATES', {}).get(self.scope, '10/min')


class PublicView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicThrottle]
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response


def branch_data(branch):
    return {'id': branch.pk, 'nombre': branch.nombre, 'direccion': branch.direccion, 'telefono': branch.telefono,
            'latitud': branch.latitud, 'longitud': branch.longitud, 'zona_horaria': branch.zona_horaria}


def court_data(court, request):
    links = [link for link in court.deportes.all() if link.activo and link.deporte.activo]
    rates = [rate.precio_hora for link in links for rate in link.tarifas.all() if rate.activa and
             rate.vigente_desde <= timezone.localdate() and
             (not rate.vigente_hasta or rate.vigente_hasta >= timezone.localdate())]
    return {'id': court.pk, 'nombre': court.nombre, 'descripcion': court.descripcion_publica,
        'sucursal': branch_data(court.sucursal), 'superficie': court.superficie,
        'ancho_metros': court.ancho_metros, 'largo_metros': court.largo_metros, 'area_m2': court.area_m2,
        'techada': court.techada, 'iluminacion': court.iluminacion, 'capacidad': court.capacidad_personas,
        'minutos_minimos': court.duracion_minima_minutos, 'precio_desde': str(min(rates)) if rates else None,
        'deportes': [{'id': link.pk, 'nombre': link.deporte.nombre} for link in links],
        'fotos': [{'url': request.build_absolute_uri(photo.archivo.url), 'descripcion': photo.texto_alternativo}
                  for photo in court.fotos.all() if photo.publicada and photo.archivo]}


class PublicBusiness(PublicView):
    def get(self, request):
        business = get_object_or_404(Negocio, publicado=True)
        return Response({'nombre': business.nombre_comercial, 'descripcion': business.descripcion_publica,
            'telefono': business.telefono, 'correo': business.correo_publico, 'logo': image_url(business, request) or business.logo_url,
            'portada': business.portada_url, 'sitio_web': business.sitio_web, 'moneda': business.moneda,
            'sucursales': [branch_data(branch) for branch in business.sucursales.filter(estado=True)], 'web': web_data(business, request)})


class PublicCourts(PublicView):
    def get(self, request, pk=None):
        courts = public_courts()
        if pk is not None:
            return Response(court_data(get_object_or_404(courts, pk=pk), request))
        branch, search = request.query_params.get('sucursal'), request.query_params.get('buscar', '')[:120]
        if branch:
            try: courts = courts.filter(sucursal_id=int(branch))
            except ValueError: raise serializers.ValidationError('Sucursal invalida.')
        return Response([court_data(court, request) for court in courts.filter(nombre__icontains=search)[:100]])


class PublicInterval(IntervalSerializer):
    cancha_deporte = serializers.PrimaryKeyRelatedField(queryset=public_links())
    def validate(self, data):
        return self.interval(data, data['cancha_deporte'].cancha.sucursal.zona_horaria)


class PublicRequestInput(PublicInterval):
    nombre = serializers.CharField(max_length=160)
    telefono = serializers.RegexField(r'^(?=(?:\D*\d){7})\+?[0-9 ()-]{7,40}$', max_length=40)
    correo = serializers.EmailField(required=False, allow_blank=True)
    notas = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    condiciones_aceptadas = serializers.BooleanField()
    total_esperado = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    condiciones_esperadas = serializers.CharField(allow_blank=True, trim_whitespace=False)
    horas_cancelacion_esperadas = serializers.IntegerField(min_value=0)
    clave = serializers.UUIDField()
    def validate(self, data):
        if not data['condiciones_aceptadas']:
            raise serializers.ValidationError('Debes aceptar las condiciones del servicio.')
        return super().validate(data)


def public_quote(value):
    return {key: value[key] for key in ['inicio', 'fin', 'moneda', 'minutos', 'horas_cancelacion', 'condiciones']} | {
        'total': str(value['total']),
        'plazo_pago_minutos': value['retencion_minutos'],
        'desglose': [{key: part[key] for key in ['nombre', 'minutos', 'importe']} for part in value['desglose']]}


class PublicQuote(PublicView):
    throttle_classes = [WriteThrottle]
    def post(self, request):
        serializer = PublicInterval(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(public_quote(services.quote(data['cancha_deporte'], data['inicio'], data['fin'])))


class PublicAvailability(PublicView):
    def get(self, request, pk):
        court = get_object_or_404(public_courts().prefetch_related('horarios'), pk=pk)
        try: day = datetime.strptime(request.query_params['fecha'], '%Y-%m-%d').date()
        except (KeyError, ValueError): raise serializers.ValidationError('Indica fecha YYYY-MM-DD.')
        zone = ZoneInfo(court.sucursal.zona_horaria)
        start, end = datetime.combine(day, time.min, zone), datetime.combine(day + timedelta(days=1), time.min, zone)
        now = timezone.now()
        busy = list(DetalleReserva.objects.filter(cancha_deporte__cancha=court, inicio__lt=end, fin__gt=start)
                    .filter(services.occupied(now)).values_list('inicio', 'fin'))
        busy += list(BloqueoCancha.objects.filter(cancha=court, activo=True, inicio__lt=end, fin__gt=start).values_list('inicio', 'fin'))
        windows = []
        for anchor in [day - timedelta(days=1), day]:
            for opening in court.horarios.all():
                if opening.activo and opening.dia_semana == anchor.isoweekday():
                    a, b = services.window(anchor, opening.apertura, opening.cierre, opening.cruza_medianoche, zone)
                    a, b = max(a, start, now.replace(second=0, microsecond=0) + timedelta(minutes=1)), min(b, end)
                    if a < b: windows.append((a, b))
        merged = []
        for a, b in sorted(windows):
            if merged and a <= merged[-1][1]: merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else: merged.append((a, b))
        available = []
        for a, b in merged:
            cursor = a
            for x, y in sorted(busy):
                if y <= cursor or x >= b: continue
                if x > cursor: available.append((cursor, min(x, b)))
                cursor = max(cursor, y)
                if cursor >= b: break
            if cursor < b: available.append((cursor, b))
        return Response({'fecha': day, 'zona_horaria': court.sucursal.zona_horaria,
            'intervalos': [{'inicio': a, 'fin': b} for a, b in available if (b-a).total_seconds() >= court.duracion_minima_minutos * 60]})


def tracking_token(row):
    return hmac.new(settings.SECRET_KEY.encode(), f'solicitud:{row.codigo}:{row.clave}'.encode(), hashlib.sha256).hexdigest()


def request_state(row):
    return 'VENCIDA' if row.estado == 'PENDIENTE' and row.inicio <= timezone.now() else row.estado


def tracking_data(row):
    reservation = row.reserva
    state = None if not reservation else ('EXPIRADA' if reservation.estado == 'PENDIENTE' and reservation.retencion_hasta <= timezone.now() else reservation.estado)
    return {'codigo': str(row.codigo), 'codigo_correlativo': row.codigo_correlativo,
        'codigo_reserva': reservation.codigo_correlativo if reservation else None,
        'estado': request_state(row), 'cancha': row.cancha_deporte.cancha.nombre,
        'deporte': row.cancha_deporte.deporte.nombre, 'inicio': row.inicio, 'fin': row.fin,
        'zona_horaria': row.cancha_deporte.cancha.sucursal.zona_horaria, 'total': str(reservation.total if reservation else row.total_cotizado),
        'moneda': 'BOB', 'estado_reserva': state, 'plazo_pago': reservation.retencion_hasta if state == 'PENDIENTE' else None,
        'motivo': row.motivo if row.estado == 'RECHAZADA' else '', 'seguimiento_hasta': row.seguimiento_hasta,
        'condiciones': reservation.condiciones if reservation else row.condiciones_snapshot.get('condiciones', ''),
        'horas_cancelacion': reservation.horas_cancelacion if reservation else row.condiciones_snapshot.get('horas_cancelacion')}


def fingerprint(data):
    values = {key: str(value.pk) if key == 'cancha_deporte' else value.isoformat() if hasattr(value, 'isoformat') else str(value)
              for key, value in data.items() if key not in ['clave', 'fecha', 'hora_inicio', 'hora_fin', 'cruza_medianoche']}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class PublicRequests(PublicView):
    throttle_classes = [WriteThrottle]
    def post(self, request):
        serializer = PublicRequestInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from .public_history import link_request_if_present, attach
        owner = link_request_if_present(request)
        try:
            with transaction.atomic():
                # Serialize identical submissions on the same court without creating an occupancy.
                Sucursal.objects.select_for_update().get(pk=data['cancha_deporte'].cancha.sucursal_id)
                court = Cancha.objects.select_for_update().get(pk=data['cancha_deporte'].cancha_id)
                if not public_courts().filter(pk=court.pk).exists(): raise serializers.ValidationError('La cancha ya no esta publicada.')
                existing = SolicitudReserva.objects.filter(clave=data['clave']).first()
                if existing:
                    if existing.huella != fingerprint(data): raise services.Conflict('La clave de envio ya se uso para otra solicitud.')
                    if existing.seguimiento_hasta <= timezone.now(): raise services.Conflict('El seguimiento de esta solicitud ha vencido.')
                    attach(owner, existing)
                    return Response(tracking_data(existing) | {'token': tracking_token(existing)})
                link = get_object_or_404(public_links(), pk=data['cancha_deporte'].pk)
                quote = services.quote(link, data['inicio'], data['fin'])
                if quote['total'] != data['total_esperado'] or quote['condiciones'] != data['condiciones_esperadas'] or quote['horas_cancelacion'] != data['horas_cancelacion_esperadas']:
                    raise services.Conflict('El precio o las condiciones cambiaron. Vuelve a cotizar antes de enviar.')
                row = SolicitudReserva(cancha_deporte=link, inicio=data['inicio'], fin=data['fin'], clave=data['clave'],
                    huella=fingerprint(data), nombre=data['nombre'], telefono=data['telefono'], correo=data.get('correo', ''),
                    notas=data.get('notas', ''), total_cotizado=quote['total'], seguimiento_hasta=data['fin'] + timedelta(days=7),
                    condiciones_snapshot={key: quote[key] for key in ['condiciones', 'horas_cancelacion', 'politica_version']})
                row.seguimiento_hash = hashlib.sha256(tracking_token(row).encode()).hexdigest()
                row.save()
                attach(owner, row)
                return Response(tracking_data(row) | {'token': tracking_token(row)}, status=201)
        except IntegrityError:
            raise services.Conflict('Envio duplicado. Reintenta con la misma clave y datos.')


class TrackingInput(StrictSerializer):
    codigo = serializers.CharField(max_length=64)
    token = serializers.RegexField(r'^[a-f0-9]{64}$')

    def validate_codigo(self, value):
        try:
            if value.upper().startswith('SOL-'):
                return {'pk': identificador_correlativo('SOL', value)}
            return {'codigo': UUID(value)}
        except (ValueError, TypeError, AttributeError):
            raise serializers.ValidationError('Indica un codigo de solicitud valido.')


class PublicTracking(PublicView):
    throttle_classes = [WriteThrottle]
    def post(self, request):
        serializer = TrackingInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        row = SolicitudReserva.objects.select_related('cancha_deporte__cancha__sucursal', 'cancha_deporte__deporte', 'reserva').filter(**data['codigo']).first()
        if not row or row.seguimiento_hasta <= timezone.now() or not hmac.compare_digest(row.seguimiento_hash, hashlib.sha256(data['token'].encode()).hexdigest()):
            from rest_framework.exceptions import NotFound
            raise NotFound('Seguimiento no disponible. Revisa el codigo y la clave o contacta al negocio.')
        return Response(tracking_data(row))
