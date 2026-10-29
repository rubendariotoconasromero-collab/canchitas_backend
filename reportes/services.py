from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from users.access import effective_permissions
from users.models import Sucursal, User
from catalogo.models import Cancha, Deporte
from caja.models import Caja, MedioPago, ReciboPago, SesionCaja
from reservas.models import Reserva

TYPES = ('reservas', 'cobros', 'cierres', 'ocupacion')


def authorize(user, *codes):
    allowed = effective_permissions(user)
    if not set(codes).issubset(allowed):
        raise PermissionDenied('No tienes permiso para consultar esta informacion.')
    return allowed


def branches(user):
    qs = Sucursal.objects.filter(estado=True)
    if 'alcance.global' not in effective_permissions(user):
        qs = qs.filter(pk=user.sucursal_id)
    return qs


def branch_for(user, value):
    if not value or not str(value).isdigit():
        raise ValidationError({'sucursal': 'Selecciona una sucursal.'})
    branch = branches(user).filter(pk=int(value)).first()
    if not branch:
        raise PermissionDenied('Sucursal no autorizada o inactiva.')
    return branch


def positive_int(value, key, maximum=None):
    try:
        result = int(value)
        if str(result) != str(value) or result < 1 or (maximum and result > maximum):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValidationError({key: 'Valor entero positivo invalido.'})


def period(params, branch):
    try:
        start, end = date.fromisoformat(params['desde']), date.fromisoformat(params['hasta'])
        if end < start or (end - start).days + 1 > settings.REPORT_MAX_DAYS:
            raise ValueError
        zone = ZoneInfo(branch.zona_horaria)
        return datetime.combine(start, time.min, zone), datetime.combine(end + timedelta(days=1), time.min, zone)
    except (KeyError, ValueError, OverflowError):
        raise ValidationError({'fechas': f'Indica un rango valido de hasta {settings.REPORT_MAX_DAYS} dias.'})


@contextmanager
def read_snapshot():
    # MySQL's default READ COMMITTED can disagree between separate summary/list queries.
    # One bounded materialization plus a repeatable read transaction avoids writer locks.
    if connection.vendor == 'mysql' and not connection.in_atomic_block:
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    with transaction.atomic():
        yield


def money(value):
    return str(Decimal(value).quantize(Decimal('0.01')))


def local(value, branch):
    return value.astimezone(ZoneInfo(branch.zona_horaria)).isoformat() if value else ''


def state(reservation, now):
    if reservation.estado == 'PENDIENTE' and reservation.retencion_hasta <= now:
        return 'EXPIRADA'
    return reservation.estado


def bounded(qs):
    rows = list(qs[:settings.REPORT_MAX_ROWS + 1])
    if len(rows) > settings.REPORT_MAX_ROWS:
        raise ValidationError(f'El reporte supera {settings.REPORT_MAX_ROWS} registros. Reduce el periodo o aplica filtros.')
    return rows


def build(user, kind, params):
    if kind not in TYPES:
        raise ValidationError('Reporte desconocido.')
    allowed = authorize(user, 'reportes.ver', 'reportes.' + kind)
    global_scope = 'alcance.global' in allowed
    branch = branch_for(user, params.get('sucursal'))
    start, end = period(params, branch)
    valid_keys = {'sucursal', 'desde', 'hasta', 'page', 'page_size', 'q'} | {
        'reservas': {'cancha', 'deporte', 'estado'}, 'ocupacion': {'cancha', 'deporte'},
        'cobros': {'caja', 'medio', 'operador', 'cancha', 'deporte'}, 'cierres': {'caja', 'operador'},
    }[kind]
    if set(params) - valid_keys:
        raise ValidationError('El reporte contiene filtros no admitidos.')
    selected = {}
    for key, model, scope in [('cancha', Cancha, {'sucursal': branch}), ('deporte', Deporte, {}),
                              ('caja', Caja, {'sucursal': branch}), ('medio', MedioPago, {}),
                              ('operador', User, {} if global_scope else {'pk': user.pk})]:
        if params.get(key):
            pk = positive_int(params[key], key)
            if not model.objects.filter(**scope).filter(pk=pk).exists():
                raise ValidationError({key: 'Seleccion no disponible.'})
            selected[key] = pk
    now = timezone.now()
    query = params.get('q', '').strip().casefold()
    if len(query) > 160:
        raise ValidationError({'q': 'La busqueda admite hasta 160 caracteres.'})
    rows = []
    if kind in ('reservas', 'ocupacion'):
        qs = Reserva.objects.filter(sucursal=branch, detalle__inicio__lt=end, detalle__fin__gt=start)
        if 'cancha' in selected:
            qs = qs.filter(detalle__cancha_deporte__cancha_id=selected['cancha'])
        if 'deporte' in selected:
            qs = qs.filter(detalle__cancha_deporte__deporte_id=selected['deporte'])
        requested_state = params.get('estado', '')
        if requested_state and requested_state not in Reserva.Estado.values:
            raise ValidationError({'estado': 'Estado invalido.'})
        reservations = bounded(qs.select_related('detalle__cancha_deporte').order_by('detalle__inicio', 'pk'))
        for r in reservations:
            effective_state = state(r, now)
            detail = r.detalle
            if requested_state and effective_state != requested_state:
                continue
            label = r.cliente_snapshot.get('nombre', 'Sin cliente registrado')
            if query and query not in (' '.join([r.codigo_correlativo, str(r.codigo), detail.cancha_nombre, detail.deporte_nombre, label])).casefold():
                continue
            seconds = Decimal(str((min(detail.fin, end) - max(detail.inicio, start)).total_seconds()))
            rows.append({'id': r.pk, 'codigo': r.codigo_correlativo, 'codigo_tecnico': str(r.codigo), 'inicio': local(detail.inicio, branch),
                'fin': local(detail.fin, branch), 'cancha': detail.cancha_nombre, 'deporte': detail.deporte_nombre,
                'cliente': label, 'estado': effective_state, 'origen': r.origen, 'total': money(r.total),
                'horas': money(seconds / Decimal(3600)), '_seconds': seconds,
                '_court': detail.cancha_deporte.cancha_id, '_sport': detail.cancha_deporte.deporte_id})
        if kind == 'ocupacion':
            groups = {}
            for row in rows:
                status = row['estado']
                if status not in ('CONFIRMADA', 'EN_CURSO', 'FINALIZADA', 'NO_ASISTIO', 'PENDIENTE'):
                    continue
                key = (row['_court'], row['_sport'], row['cancha'], row['deporte'])
                group = groups.setdefault(key, {'id': f'{key[0]}-{key[1]}-{len(groups)}', 'cancha': key[2],
                    'deporte': key[3], 'reservas': 0, '_confirmed': Decimal(0), '_pending': Decimal(0)})
                group['reservas'] += 1
                group['_pending' if status == 'PENDIENTE' else '_confirmed'] += row['_seconds']
            rows = [{**{k: v for k, v in g.items() if not k.startswith('_')},
                     'horas_confirmadas': money(g['_confirmed'] / 3600),
                     'horas_pendientes': money(g['_pending'] / 3600)} for g in groups.values()]
            # Round only after adding all seconds, not each reservation.
            summary = {'horas_confirmadas': money(sum((g['_confirmed'] for g in groups.values()), Decimal(0)) / 3600),
                       'horas_pendientes': money(sum((g['_pending'] for g in groups.values()), Decimal(0)) / 3600)}
        else:
            totals = {status: money(sum((Decimal(row['total']) for row in rows if row['estado'] == status), Decimal(0)))
                      for status in Reserva.Estado.values}
            summary = {'reservas': len(rows), 'importe_reservas': money(sum((Decimal(row['total']) for row in rows), Decimal(0))),
                       'por_estado': totals}
            rows = [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows]
    elif kind == 'cobros':
        qs = ReciboPago.objects.filter(pago__reserva__sucursal=branch, pago__created_at__gte=start, pago__created_at__lt=end)
        if not global_scope:
            qs = qs.filter(pago__registrado_por=user)
        for key, field in [('caja', 'movimiento__sesion__caja_id'), ('medio', 'lineas__medio_id'), ('operador', 'pago__registrado_por_id'),
                           ('cancha', 'pago__reserva__detalle__cancha_deporte__cancha_id'),
                           ('deporte', 'pago__reserva__detalle__cancha_deporte__deporte_id')]:
            if key in selected:
                qs = qs.filter(**{field: selected[key]})
        if 'medio' in selected:
            qs = qs.distinct()
        for receipt in bounded(qs.select_related('pago__reserva', 'pago__registrado_por',
                'movimiento__sesion__responsable').prefetch_related('lineas__movimiento__sesion__responsable').order_by('pago__created_at', 'pk')):
            p, data = receipt.pago, receipt.datos
            payment_lines = list(receipt.lineas.all())
            turn = payment_lines[0].movimiento.sesion
            by_medium = {}
            for line in payment_lines:
                by_medium[line.medio_nombre] = by_medium.get(line.medio_nombre, Decimal(0)) + line.importe
            medium_label = ' · '.join(f'{name}: Bs {money(amount)}' for name, amount in by_medium.items())
            row = {'id': receipt.pk, 'reserva': p.reserva_id, 'codigo': p.reserva.codigo_correlativo,
                'codigo_tecnico': str(p.reserva.codigo), 'codigo_pago': p.codigo_correlativo, 'numero': receipt.codigo_correlativo,
                'fecha': local(p.created_at, branch), 'cancha': data.get('cancha', ''),
                'cliente': data.get('cliente', {}).get('nombre', 'Sin cliente registrado'),
                'caja': data.get('caja', ''), 'turno': turn.pk,
                'operador': data.get('operador', p.registrado_por.nombre), 'responsable': turn.responsable.nombre,
                'medio': medium_label, 'importe': money(p.importe), 'estado_reserva': state(p.reserva, now),
                '_por_medio': by_medium,
                '_efectivo': sum((line.importe for line in payment_lines if line.medio_efectivo), Decimal(0))}
            if 'medio' in selected:
                row['importe_medio'] = money(sum((line.importe for line in payment_lines if line.medio_id == selected['medio']), Decimal(0)))
            if not query or query in ' '.join(str(v) for v in row.values()).casefold():
                rows.append(row)
        by_medium = {}
        for row in rows:
            for name, amount in row['_por_medio'].items():
                by_medium[name] = by_medium.get(name, Decimal(0)) + amount
        summary = {'cobros': len(rows), 'cobrado': money(sum((Decimal(row['importe']) for row in rows), Decimal(0))),
                   'por_medio': {k: money(v) for k, v in by_medium.items()},
                   'efectivo': money(sum((r['_efectivo'] for r in rows), Decimal(0)))}
        if 'medio' in selected:
            summary['medio_seleccionado'] = money(sum((Decimal(r['importe_medio']) for r in rows), Decimal(0)))
        rows = [{key: value for key, value in row.items() if not key.startswith('_')} for row in rows]
    else:
        qs = SesionCaja.objects.filter(caja__sucursal=branch, cierre__gte=start, cierre__lt=end)
        if not global_scope:
            qs = qs.filter(responsable=user)
        for key, field in [('caja', 'caja_id'), ('operador', 'responsable_id')]:
            if key in selected:
                qs = qs.filter(**{field: selected[key]})
        for turn in bounded(qs.select_related('caja', 'responsable', 'cerrado_por').prefetch_related('movimientos').order_by('cierre', 'pk')):
            row = {'id': turn.pk, 'caja': turn.caja.nombre, 'responsable': turn.responsable.nombre,
                   'apertura': local(turn.apertura, branch), 'cierre': local(turn.cierre, branch),
                   'cerrado_por': turn.cerrado_por.nombre if turn.cerrado_por else '',
                   'monto_inicial': money(turn.monto_inicial), 'esperado': money(turn.efectivo_esperado),
                   'contado': money(turn.efectivo_contado), 'diferencia': money(turn.diferencia)}
            movements = list(turn.movimientos.all())
            for name, predicate in [('ingresos_manuales', lambda m: m.tipo == 'INGRESO'),
                                    ('retiros', lambda m: m.tipo == 'RETIRO'),
                                    ('cobros_efectivo', lambda m: m.tipo == 'COBRO' and m.efectivo),
                                    ('cobros_electronicos', lambda m: m.tipo == 'COBRO' and not m.efectivo)]:
                row[name] = money(sum((m.importe for m in movements if predicate(m)), Decimal(0)))
            if not query or query in ' '.join(str(v) for v in row.values()).casefold():
                rows.append(row)
        summary = {'turnos': len(rows), 'con_diferencia': sum(Decimal(r['diferencia']) != 0 for r in rows),
                   **{key: money(sum((Decimal(r[key]) for r in rows), Decimal(0))) for key in ('esperado', 'contado', 'diferencia')}}
    return rows, summary, {'sucursal': branch.pk, 'sucursal_nombre': branch.nombre, 'zona_horaria': branch.zona_horaria,
        'desde': params['desde'], 'hasta': params['hasta'], 'generado_en': now.isoformat(),
        'filtros': {key: value for key, value in params.items() if key not in ('page', 'page_size')},
        'alcance': 'sucursal' if global_scope or kind in ('reservas', 'ocupacion') else 'propio',
        'criterio': {'reservas': 'Servicios que intersectan el periodo; importe completo y estado actual.',
                    'cobros': 'Pagos registrados en el periodo, incluidos los de reservas canceladas. Sin reembolsos.' + (' El total incluye el cobro completo; el importe del medio seleccionado muestra solo su parte.' if 'medio' in selected else ''),
                    'cierres': 'Turnos cerrados en el periodo; importes de efectivo guardados al cerrar.',
                    'ocupacion': 'Horas contratadas dentro del periodo. No mide uso real ni porcentaje historico.'}[kind]}


def paginate(rows, params):
    page = positive_int(params.get('page', '1'), 'page')
    size = positive_int(params.get('page_size', '25'), 'page_size', 100)
    pages = max(1, (len(rows) + size - 1) // size)
    if page > pages:
        raise ValidationError({'page': 'La pagina solicitada ya no existe.'})
    return {'count': len(rows), 'page': page, 'page_size': size, 'results': rows[(page - 1) * size:page * size]}
