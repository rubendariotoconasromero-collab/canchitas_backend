from datetime import timedelta
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from reservas.models import Reserva, SolicitudReserva
from caja.models import SesionCaja
from .services import authorize, branches, branch_for, read_snapshot


@api_view(['GET'])
def notices(request):
    permissions = authorize(request.user, 'avisos.ver')
    if set(request.query_params) - {'sucursal'}:
        raise ValidationError('Filtros no admitidos.')
    ids = list(branches(request.user).values_list('pk', flat=True))
    if request.query_params.get('sucursal'):
        ids = [branch_for(request.user, request.query_params['sucursal']).pk]
    now, rows, counts = timezone.now(), [], {}

    def append(qs, kind, transform):
        counts[kind] = qs.count()
        rows.extend(transform(item) for item in qs[:20])

    with read_snapshot():
        if 'solicitudes.ver' in permissions:
            qs = SolicitudReserva.objects.filter(cancha_deporte__cancha__sucursal_id__in=ids,
                estado='PENDIENTE', inicio__gt=now).select_related('cancha_deporte__cancha').order_by('inicio', 'pk')
            append(qs, 'solicitudes', lambda s: {'tipo': 'solicitud', 'id': s.pk, 'titulo': 'Solicitud web pendiente de revision',
                'fecha': s.inicio.isoformat(), 'ruta': '/solicitudes', 'params': {'sucursal': s.cancha_deporte.cancha.sucursal_id, 'solicitud': s.pk}})
        if 'reservas.ver' in permissions:
            qs = Reserva.objects.filter(sucursal_id__in=ids, estado='PENDIENTE', retencion_hasta__gt=now,
                detalle__fin__gt=now).select_related('detalle').order_by('retencion_hasta', 'pk')
            append(qs, 'pendientes', lambda r: {'tipo': 'pendiente', 'id': r.pk, 'titulo': 'Reserva pendiente de pago',
                'fecha': r.retencion_hasta.isoformat(), 'ruta': '/agenda',
                'params': {'sucursal': r.sucursal_id, 'reserva': r.pk, 'fecha': r.detalle.inicio.astimezone(timezone.get_default_timezone()).date().isoformat()}})
            qs = Reserva.objects.filter(sucursal_id__in=ids, estado__in=['PENDIENTE', 'EXPIRADA'],
                retencion_hasta__gt=now-timedelta(hours=24), retencion_hasta__lte=now).select_related('detalle').order_by('-retencion_hasta', '-pk')
            append(qs, 'vencidas', lambda r: {'tipo': 'vencida', 'id': r.pk, 'titulo': 'Plazo de pago vencido',
                'fecha': r.retencion_hasta.isoformat(), 'ruta': '/agenda',
                'params': {'sucursal': r.sucursal_id, 'reserva': r.pk, 'fecha': r.detalle.inicio.astimezone(timezone.get_default_timezone()).date().isoformat()}})
        if 'reportes.ver' in permissions and 'reportes.cierres' in permissions:
            qs = SesionCaja.objects.filter(caja__sucursal_id__in=ids, cierre__gte=now-timedelta(days=7)).exclude(diferencia=0)
            if 'alcance.global' not in permissions:
                qs = qs.filter(responsable=request.user)
            qs = qs.select_related('caja').order_by('-cierre', '-pk')
            append(qs, 'cierres', lambda s: {'tipo': 'cierre', 'id': s.pk, 'titulo': 'Cierre de turno con diferencia',
                'fecha': s.cierre.isoformat(), 'ruta': '/reportes',
                'params': {'tipo': 'cierres', 'sucursal': s.caja.sucursal_id,
                           'desde': s.cierre.astimezone(timezone.get_default_timezone()).date().isoformat(),
                           'hasta': s.cierre.astimezone(timezone.get_default_timezone()).date().isoformat()}})
        for row in rows:
            row['params'] = {key: str(value) for key, value in row['params'].items()}
        return Response({'count': sum(counts.values()), 'results': rows, 'por_tipo': counts,
                         'limitado': sum(counts.values()) > len(rows), 'actualizado_en': now.isoformat()}, headers={'Cache-Control': 'no-store'})
