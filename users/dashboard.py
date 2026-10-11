from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Case, When, Value, CharField, Count, Sum, Q, F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from users.access import effective_permissions
from users.models import Sucursal
from reservas.models import Reserva
from caja.models import Caja, SesionCaja, ReciboPago
from caja.services import summary


def money(value):
    return format(value or Decimal('0'), '.2f')


def payment_totals(receipts):
    totals = receipts.aggregate(importe=Sum('pago__importe'), cantidad=Count('pk'))
    return {'importe': money(totals['importe']), 'cantidad': totals['cantidad'],
            'por_medio': [{'medio': row['datos__medio'], 'importe': money(row['importe']),
                          'cantidad': row['cantidad']}
                         for row in receipts.order_by().values('datos__medio').annotate(
                             importe=Sum('pago__importe'), cantidad=Count('pk')).order_by('datos__medio')]}


def reservation_rows(queryset):
    return [{'id': row.pk, 'cancha': row.detalle.cancha_nombre,
             'cliente': row.cliente_snapshot.get('nombre', 'Sin cliente registrado'),
             'inicio': row.detalle.inicio.isoformat(), 'fin': row.detalle.fin.isoformat(),
             'estado': row.estado, 'total': money(row.total),
             'retencion_hasta': row.retencion_hasta.isoformat()}
            for row in queryset.select_related('detalle')[:8]]


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    permissions = effective_permissions(request.user)
    if 'dashboard.ver' not in permissions:
        raise PermissionDenied('No tienes acceso al dashboard.')
    global_access = 'alcance.global' in permissions
    branches = Sucursal.objects.filter(estado=True)
    if not global_access:
        branches = branches.filter(pk=request.user.sucursal_id)
    branch_options = list(branches.values('id', 'nombre'))
    branch_id = request.query_params.get('sucursal')
    if branch_id:
        if not branch_id.isdigit():
            raise ValidationError('Sucursal invalida.')
        branch = get_object_or_404(branches, pk=int(branch_id))
    else:
        branch = branches.filter(pk=request.user.sucursal_id).first() or branches.first()
    cashier = bool(permissions & {'caja.abrir', 'caja.cerrar', 'caja.movimientos'}) and 'caja.ver' in permissions
    reception = 'reservas.crear' in permissions
    profile = 'administrador' if global_access else 'cajero' if cashier else 'recepcion' if reception else 'consulta'
    now = timezone.now()
    result = {'perfil': profile, 'actualizado_en': now.isoformat(), 'sucursales': branch_options,
              'sucursal': None, 'reservas': None, 'caja': None}
    if not branch:
        response = Response(result)
        response['Cache-Control'] = 'no-store'
        return response
    zone = ZoneInfo(branch.zona_horaria)
    today = now.astimezone(zone).date()
    start = datetime.combine(today, time.min, zone)
    end = datetime.combine(today + timedelta(days=1), time.min, zone)
    result['sucursal'] = {'id': branch.pk, 'nombre': branch.nombre,
                          'zona_horaria': branch.zona_horaria, 'fecha': today.isoformat()}
    if 'reservas.ver' in permissions:
        reservations = Reserva.objects.filter(sucursal=branch)
        day = reservations.filter(detalle__inicio__lt=end, detalle__fin__gt=start)
        states = day.annotate(estado_actual=Case(
            When(estado='PENDIENTE', retencion_hasta__lte=now, then=Value('EXPIRADA')),
            default='estado', output_field=CharField(),
        )).order_by().values('estado_actual').annotate(cantidad=Count('pk'))
        counts = {code: 0 for code in Reserva.Estado.values}
        counts.update({row['estado_actual']: row['cantidad'] for row in states})
        valid = Q(estado__in=['CONFIRMADA', 'EN_CURSO']) | Q(estado='PENDIENTE', retencion_hasta__gt=now)
        pending = reservations.filter(estado='PENDIENTE', retencion_hasta__gt=now, detalle__fin__gt=now)
        upcoming = reservations.filter(valid, detalle__fin__gt=now,
            detalle__inicio__lt=start + timedelta(days=7)).order_by('detalle__inicio', 'pk')
        result['reservas'] = {'total_dia': sum(counts.values()), 'por_estado': counts,
                              'pendientes_vigentes': pending.count(),
                              'pendientes': reservation_rows(pending.order_by('retencion_hasta', 'pk')),
                              'proximas': reservation_rows(upcoming)}
    if 'caja.ver' in permissions:
        sessions = SesionCaja.objects.filter(caja__sucursal=branch)
        open_sessions = sessions.filter(cierre__isnull=True, caja__sesion_actual_id=F('pk'))
        own = open_sessions.filter(responsable=request.user)
        receipts = ReciboPago.objects.filter(pago__reserva__sucursal=branch)
        personal = receipts.filter(pago__registrado_por=request.user, movimiento__sesion__in=open_sessions)
        displayed = receipts.filter(created_at__gte=start, created_at__lt=end) if global_access else personal
        current = open_sessions if global_access else own
        cash = {'cajas_disponibles': Caja.objects.filter(sucursal=branch, activa=True).count(),
                'turnos_propios': own.count(),
                'turnos': [{'id': s.pk, 'caja': s.caja.nombre, 'responsable': s.responsable.nombre,
                             'apertura': s.apertura.isoformat(), 'monto_inicial': money(s.monto_inicial),
                             'efectivo_esperado': summary(s)['esperado']}
                            for s in current.select_related('caja', 'responsable').prefetch_related('movimientos')],
                'mis_cobros_turnos_abiertos': payment_totals(personal),
                'ultimos_cobros': [{'id': r.pk, 'numero': f'SRM-{r.pk:08d}',
                                    'fecha': r.created_at.isoformat(), 'cliente': r.datos.get('cliente', {}).get('nombre', 'Sin cliente registrado'),
                                    'cancha': r.datos.get('cancha', ''), 'medio': r.datos.get('medio', ''),
                                    'importe': money(r.pago.importe), 'operador': r.datos.get('operador', '')}
                                   for r in displayed.select_related('pago').order_by('-created_at', '-pk')[:8]],
                'administracion': None}
        if global_access:
            closed = sessions.filter(cierre__gte=start, cierre__lt=end)
            cash['administracion'] = {'cobros_hoy': payment_totals(displayed),
                'turnos_abiertos': open_sessions.count(),
                'cierres_con_diferencia': closed.exclude(diferencia=0).count(),
                'diferencia_neta': money(closed.aggregate(total=Sum('diferencia'))['total']),
                'cierres': [{'id': s.pk, 'caja': s.caja.nombre, 'responsable': s.responsable.nombre,
                             'cierre': s.cierre.isoformat(), 'esperado': money(s.efectivo_esperado),
                             'contado': money(s.efectivo_contado), 'diferencia': money(s.diferencia)}
                            for s in closed.select_related('caja', 'responsable').order_by('-cierre')[:8]]}
        result['caja'] = cash
    response = Response(result)
    response['Cache-Control'] = 'no-store'
    return response
