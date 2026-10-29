import hashlib
import secrets
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db.models import Case, When, Value, CharField, F, Q
from django.db.models.functions import Coalesce
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from .models import SolicitudReserva, VisitantePublico, SolicitudVisitante
from .public_api import PublicView, WriteThrottle, TrackingInput, tracking_data


GROUPS = {
    'pendientes': ['EN_REVISION', 'PENDIENTE_PAGO', 'ACEPTADA'],
    'confirmadas': ['CONFIRMADA', 'EN_CURSO'],
    'historial': ['FINALIZADA', 'NO_ASISTIO', 'CANCELADA', 'RECHAZADA', 'VENCIDA', 'EXPIRADA'],
}


def check_csrf(request):
    raw = request._request
    middleware = CsrfViewMiddleware(lambda r: None)
    middleware.process_request(raw)
    if middleware.process_view(raw, lambda r: None, (), {}):
        raise PermissionDenied('La sesion de solicitudes debe actualizarse antes de continuar.')


def visitor(request):
    key = request.COOKIES.get(settings.PUBLIC_HISTORY_COOKIE, '')
    if not key or len(key) > 100:
        return None
    digest = hashlib.sha256(key.encode()).hexdigest()
    return VisitantePublico.objects.filter(clave_hash=digest, acceso_hasta__gt=timezone.now()).first()


def link_request_if_present(request):
    if request.COOKIES.get(settings.PUBLIC_HISTORY_COOKIE) or request.headers.get('X-CSRFToken'):
        check_csrf(request)
    owner = visitor(request)
    if request.headers.get('X-CSRFToken') and not owner:
        raise PermissionDenied('Permite las cookies del negocio y actualiza la pagina antes de enviar tu solicitud.')
    return owner


def attach(owner, row):
    if owner:
        SolicitudVisitante.objects.get_or_create(visitante=owner, solicitud=row)


def rows_for(owner):
    now = timezone.now()
    return SolicitudReserva.objects.filter(visitantes__visitante=owner).select_related(
        'cancha_deporte__cancha__sucursal__negocio', 'cancha_deporte__deporte', 'reserva__detalle').annotate(
        inicio_historial=Coalesce('reserva__detalle__inicio', 'inicio'),
        estado_publico=Case(
            When(reserva__estado='PENDIENTE', reserva__retencion_hasta__lte=now, then=Value('EXPIRADA')),
            When(reserva__estado='PENDIENTE', then=Value('PENDIENTE_PAGO')),
            When(reserva__isnull=False, then=F('reserva__estado')),
            When(estado='PENDIENTE', inicio__lte=now, then=Value('VENCIDA')),
            When(estado='PENDIENTE', then=Value('EN_REVISION')),
            default=F('estado'), output_field=CharField()))


def history_data(row):
    data = tracking_data(row)
    link, court = row.cancha_deporte, row.cancha_deporte.cancha
    if row.reserva:
        detail = row.reserva.detalle
        data.update(cancha=detail.cancha_nombre, deporte=detail.deporte_nombre,
                    inicio=detail.inicio, fin=detail.fin, zona_horaria=detail.zona_horaria)
    return data | {'estado_publico': row.estado_publico, 'creado_at': row.created_at,
        'sucursal': court.sucursal.nombre, 'cancha_id': court.pk, 'cancha_deporte_id': link.pk,
        'cancha_disponible': bool(court.activa and court.publicada and court.sucursal.estado and
            court.sucursal.negocio and court.sucursal.negocio.publicado and link.activo and link.deporte.activo)}


class HistoryContext(PublicView):
    throttle_classes = [WriteThrottle]

    def get(self, request):
        owner = visitor(request)
        key = None
        if not owner:
            key = secrets.token_urlsafe(32)
            owner = VisitantePublico.objects.create(clave_hash=hashlib.sha256(key.encode()).hexdigest(),
                acceso_hasta=timezone.now() + timedelta(days=settings.PUBLIC_HISTORY_DAYS))
        response = Response({'csrf': get_token(request._request), 'acceso_hasta': owner.acceso_hasta})
        if key:
            response.set_cookie(settings.PUBLIC_HISTORY_COOKIE, key, max_age=settings.PUBLIC_HISTORY_DAYS * 86400,
                httponly=True, secure=settings.PUBLIC_HISTORY_COOKIE_SECURE, samesite='Lax', path='/api/publico/')
        return response


class HistoryView(PublicView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            check_csrf(request)
        self.owner = visitor(request)
        if not self.owner:
            raise PermissionDenied('Tu historial no esta disponible en este navegador. Actualiza la pagina.')


def positive_int(params, key, default, maximum):
    try:
        value = int(params.get(key, default))
        if not 1 <= value <= maximum:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise serializers.ValidationError({key: ['Valor invalido.']})


def code_lookup(code):
    return TrackingInput().validate_codigo(code)


class PublicHistory(HistoryView):
    def get(self, request):
        rows = rows_for(self.owner)
        group = request.query_params.get('grupo', 'todas')
        if group != 'todas':
            if group not in GROUPS:
                raise serializers.ValidationError('Grupo de solicitudes invalido.')
            rows = rows.filter(estado_publico__in=GROUPS[group])
        state = request.query_params.get('estado', '')
        if state:
            if state not in {s for values in GROUPS.values() for s in values}:
                raise serializers.ValidationError('Estado invalido.')
            rows = rows.filter(estado_publico=state)
        query = request.query_params.get('q', '').strip()[:120]
        if query:
            criterion = (Q(cancha_deporte__cancha__nombre__icontains=query) | Q(cancha_deporte__deporte__nombre__icontains=query) |
                         Q(reserva__detalle__cancha_nombre__icontains=query) | Q(reserva__detalle__deporte_nombre__icontains=query))
            try:
                criterion |= Q(**code_lookup(query))
            except serializers.ValidationError:
                pass
            rows = rows.filter(criterion)
        for key, operator in [('desde', 'gte'), ('hasta', 'lt')]:
            value = request.query_params.get(key)
            if value:
                try:
                    day = date.fromisoformat(value)
                    if key == 'hasta':
                        day += timedelta(days=1)
                    boundary = timezone.make_aware(datetime.combine(day, time.min))
                except (ValueError, OverflowError):
                    raise serializers.ValidationError({key: ['Fecha invalida.']})
                rows = rows.filter(**{'inicio_historial__' + operator: boundary})
        if request.query_params.get('desde', '') > request.query_params.get('hasta', '9999-12-31'):
            raise serializers.ValidationError('El periodo de fechas es invalido.')
        order = request.query_params.get('orden', 'recientes')
        if order not in ('recientes', 'anteriores'):
            raise serializers.ValidationError('Orden invalido.')
        rows = rows.order_by('-created_at', '-pk') if order == 'recientes' else rows.order_by('created_at', 'pk')
        size = positive_int(request.query_params, 'page_size', 25, 100)
        count = rows.count()
        page = min(positive_int(request.query_params, 'page', 1, 1000000), max(1, (count + size - 1) // size))
        return Response({'count': count, 'page': page, 'page_size': size, 'acceso_hasta': self.owner.acceso_hasta,
            'results': [history_data(row) for row in rows[(page - 1) * size:page * size]], 'actualizado_at': timezone.now()})

    def delete(self, request):
        self.owner.delete()
        response = Response(status=204)
        response.delete_cookie(settings.PUBLIC_HISTORY_COOKIE, path='/api/publico/', samesite='Lax')
        return response


class HistoryDetail(HistoryView):
    def get_row(self, code):
        row = rows_for(self.owner).filter(**code_lookup(code)).first()
        if not row:
            raise NotFound('Solicitud no disponible en este historial.')
        return row

    def get(self, request, code):
        return Response(history_data(self.get_row(code)))

    def delete(self, request, code):
        row = self.get_row(code)
        SolicitudVisitante.objects.filter(visitante=self.owner, solicitud=row).delete()
        return Response(status=204)


class HistoryImport(HistoryView):
    throttle_classes = [WriteThrottle]

    def post(self, request):
        data = TrackingInput(data=request.data)
        data.is_valid(raise_exception=True)
        row = SolicitudReserva.objects.filter(**data.validated_data['codigo']).first()
        if not row or row.seguimiento_hasta <= timezone.now() or not secrets.compare_digest(
                row.seguimiento_hash, hashlib.sha256(data.validated_data['token'].encode()).hexdigest()):
            raise NotFound('No se pudo incorporar la solicitud. Revisa el codigo y la clave o contacta al negocio.')
        attach(self.owner, row)
        return Response(history_data(rows_for(self.owner).get(pk=row.pk)))
