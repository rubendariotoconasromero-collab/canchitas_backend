from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from users.access import effective_permissions, is_global
from users.models import Sucursal
from catalogo.models import Cancha, CanchaDeporte, PoliticaReserva
from .models import Cliente, Reserva, DetalleReserva, BloqueoCancha, HistorialReserva, PagoReserva


class Conflict(APIException):
    status_code = 409
    default_detail = 'El horario ya no esta disponible.'


def scope(user, branch_id):
    if not is_global(user) and user.sucursal_id != branch_id:
        raise PermissionDenied('No puedes operar en otra sucursal.')


def lock_court(user, court_id):
    court = Cancha.objects.select_related('sucursal').get(pk=court_id)
    scope(user, court.sucursal_id)
    Sucursal.objects.select_for_update().get(pk=court.sucursal_id)
    return Cancha.objects.select_for_update().select_related('sucursal').get(pk=court_id)


def occupied(now):
    return Q(reserva__estado__in=[Reserva.Estado.CONFIRMADA, Reserva.Estado.EN_CURSO, Reserva.Estado.FINALIZADA, Reserva.Estado.NO_ASISTIO]) | Q(
        reserva__estado=Reserva.Estado.PENDIENTE, reserva__retencion_hasta__gt=now)


def check_available(court, start, end, exclude=None):
    details = DetalleReserva.objects.filter(
        cancha_deporte__cancha=court, inicio__lt=end, fin__gt=start).filter(occupied(timezone.now()))
    if exclude:
        details = details.exclude(reserva_id=exclude)
    if details.exists() or BloqueoCancha.objects.filter(cancha=court, activo=True, inicio__lt=end, fin__gt=start).exists():
        raise Conflict()


def window(day, start, end, overnight, zone):
    a = datetime.combine(day, start, tzinfo=zone).astimezone(utc_timezone.utc)
    b = datetime.combine(day + timedelta(days=int(overnight)), end, tzinfo=zone).astimezone(utc_timezone.utc)
    return a, b


def quote(link, start, end, immediate=False, exclude=None):
    court = link.cancha
    if not court.activa or not court.sucursal.estado or not link.activo or not link.deporte.activo:
        raise ValidationError('La cancha, deporte o sucursal estan inactivos.')
    now = timezone.now()
    if start.second or end.second or start.microsecond or end.microsecond:
        raise ValidationError('Usa horarios con precision de minutos.')
    start, end = start.astimezone(utc_timezone.utc), end.astimezone(utc_timezone.utc)
    duration = int((end - start).total_seconds() // 60)
    if duration < court.duracion_minima_minutos or duration > 1440:
        raise ValidationError(f'La duracion debe ser de {court.duracion_minima_minutos} a 1440 minutos.')
    if not immediate and start <= now:
        raise ValidationError('La reserva debe comenzar en el futuro.')
    policy = PoliticaReserva.objects.filter(sucursal=court.sucursal, activa=True).order_by('-version').first()
    if not policy or not policy.minutos_retencion:
        raise ValidationError('Configura una politica activa con un plazo para completar el pago, en minutos.')
    zone = ZoneInfo(court.sucursal.zona_horaria)
    first, last = start.astimezone(zone).date() - timedelta(days=1), end.astimezone(zone).date()
    openings, rates = [], []
    all_openings = list(court.horarios.filter(activo=True))
    all_rates = list(link.tarifas.filter(activa=True, vigente_desde__lte=last).filter(
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=first)))
    day = first
    while day <= last:
        for opening in all_openings:
            if opening.dia_semana == day.isoweekday():
                openings.append(window(day, opening.apertura, opening.cierre, opening.cruza_medianoche, zone))
        for rate in all_rates:
            if rate.dia_semana == day.isoweekday() and rate.vigente_desde <= day and (not rate.vigente_hasta or day <= rate.vigente_hasta):
                a, b = window(day, rate.hora_desde, rate.hora_hasta, rate.cruza_medianoche, zone)
                if a < end and b > start:
                    rates.append((max(a, start), min(b, end), rate))
        day += timedelta(days=1)
    cursor = start
    for a, b in sorted(openings):
        if a <= cursor < b:
            cursor = max(cursor, b)
    if cursor < end:
        raise ValidationError('El intervalo no esta cubierto por los horarios de la cancha.')
    edges = sorted({start, end, *[value for a, b, _ in rates for value in (a, b)]})
    raw, rounded, breakdown = Decimal('0'), Decimal('0'), []
    for a, b in zip(edges, edges[1:]):
        matching = [rate for x, y, rate in rates if x <= a and y >= b]
        if len(matching) != 1:
            raise ValidationError('Falta una tarifa vigente o hay tarifas ambiguas en el intervalo.')
        rate = matching[0]
        minutes = int((b - a).total_seconds() // 60)
        raw += rate.precio_hora * Decimal(minutes) / Decimal(60)
        next_rounded = raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        breakdown.append({'tarifa_id': rate.pk, 'nombre': rate.nombre, 'inicio': a.isoformat(), 'fin': b.isoformat(),
                          'minutos': minutes, 'precio_hora': str(rate.precio_hora), 'importe': str(next_rounded - rounded)})
        rounded = next_rounded
    if not Decimal('0') < rounded <= Decimal('9999999999.99'):
        raise ValidationError('El importe calculado esta fuera del rango permitido.')
    check_available(court, start, end, exclude)
    return {'inicio': start, 'fin': end, 'total': rounded, 'moneda': 'BOB', 'minutos': duration,
            'zona_horaria': court.sucursal.zona_horaria, 'desglose': breakdown,
            'retencion_minutos': policy.minutos_retencion, 'horas_cancelacion': policy.horas_limite_cancelacion,
            'condiciones': policy.condiciones, 'politica_version': policy.version}


def resolve_interval(data):
    if data.get('alquiler_inmediato'):
        start = timezone.now().replace(second=0, microsecond=0)
        return start, start + timedelta(minutes=data['duracion_minutos'])
    return data['inicio'], data['fin']


def audit(reservation, action, actor, previous='', data=None):
    HistorialReserva.objects.create(reserva=reservation, accion=action, actor=actor,
        estado_anterior=previous, estado_nuevo=reservation.estado, datos=data or {})


def detail_values(link, quotation):
    return {'cancha_deporte': link, 'inicio': quotation['inicio'], 'fin': quotation['fin'],
            'zona_horaria': quotation['zona_horaria'], 'cancha_nombre': link.cancha.nombre,
            'deporte_nombre': link.deporte.nombre, 'desglose_tarifa': quotation['desglose']}


@transaction.atomic
def retain(user, data, *, allow_unidentified=False):
    link = data['cancha_deporte']
    court = lock_court(user, link.cancha_id)
    if data.get('alquiler_inmediato'):
        from caja.models import Caja
        if not Caja.objects.filter(sucursal=court.sucursal, activa=True, sesion_actual__isnull=False).exists():
            raise ValidationError('Abre una caja en esta sucursal antes de registrar un alquiler inmediato.')
    link = CanchaDeporte.objects.select_related('cancha__sucursal', 'deporte').get(pk=link.pk)
    anonymous = data.get('sin_cliente', False)
    if anonymous and (not allow_unidentified or 'reservas.crear' not in effective_permissions(user) or 'cliente' in data):
        raise ValidationError('Sin cliente registrado solo esta permitido en reservas internas.')
    if not anonymous and 'cliente' not in data:
        raise ValidationError('Selecciona un cliente.')
    client = None if anonymous else Cliente.objects.get(pk=data['cliente'].pk)
    if client and (client.sucursal_id != court.sucursal_id or not client.activo):
        raise ValidationError('Selecciona un cliente activo de la misma sucursal.')
    start, end = resolve_interval(data)
    quotation = quote(link, start, end, data.get('alquiler_inmediato', False))
    if 'total_esperado' in data and data['total_esperado'] != quotation['total']:
        raise Conflict('La tarifa cambio. Vuelve a cotizar antes de reservar.')
    deadline = min(timezone.now() + timedelta(minutes=quotation['retencion_minutos']),
                   end if data.get('alquiler_inmediato') else start)
    reservation = Reserva.objects.create(sucursal=court.sucursal, cliente=client,
        cliente_snapshot={'nombre': client.nombre, 'telefono': client.telefono, 'correo': client.correo} if client else
            {'nombre': 'Sin cliente registrado', 'telefono': '', 'correo': '', 'sin_cliente': True},
        origen='INMEDIATO' if data.get('alquiler_inmediato') else 'RECEPCION',
        total=quotation['total'], retencion_hasta=deadline, creado_por=user, notas=data.get('notas', ''),
        horas_cancelacion=quotation['horas_cancelacion'], condiciones=quotation['condiciones'],
        politica_version=quotation['politica_version'])
    DetalleReserva.objects.create(reserva=reservation, **detail_values(link, quotation))
    audit(reservation, 'RETENER', user, data={'total': str(reservation.total), 'retencion_hasta': deadline.isoformat()})
    return reservation


def lock_reservation(user, reservation_id):
    initial = Reserva.objects.select_related('detalle__cancha_deporte').get(pk=reservation_id)
    scope(user, initial.sucursal_id)
    lock_court(user, initial.detalle.cancha_deporte.cancha_id)
    return Reserva.objects.select_for_update().select_related('detalle__cancha_deporte__cancha__sucursal',
                                                              'detalle__cancha_deporte__deporte').get(pk=reservation_id)


def has_full_payment(reservation):
    return PagoReserva.objects.filter(reserva=reservation, importe=reservation.total).exists()


@transaction.atomic
def transition(user, reservation_id, action, reason=''):
    reservation = lock_reservation(user, reservation_id)
    state, now = reservation.estado, timezone.now()
    expired = state == Reserva.Estado.PENDIENTE and reservation.retencion_hasta <= now
    if expired:
        raise Conflict('El plazo de pago vencio. El horario ya no esta apartado.')
    if action == 'cancelar':
        if state not in [Reserva.Estado.PENDIENTE, Reserva.Estado.CONFIRMADA]:
            raise ValidationError('Esta reserva no se puede cancelar.')
        if state == Reserva.Estado.CONFIRMADA and now > reservation.detalle.inicio - timedelta(hours=reservation.horas_cancelacion):
            raise ValidationError('Se supero el plazo pactado para cancelar.')
        target = Reserva.Estado.CANCELADA
    elif action == 'confirmar':
        if state != Reserva.Estado.PENDIENTE:
            raise ValidationError('Solo se confirman reservas pendientes de pago.')
        if not has_full_payment(reservation):
            raise ValidationError('Se requiere el pago total registrado por caja.')
        check_available(reservation.detalle.cancha_deporte.cancha, reservation.detalle.inicio, reservation.detalle.fin, reservation.pk)
        target = Reserva.Estado.CONFIRMADA
    elif action == 'iniciar':
        if state != Reserva.Estado.CONFIRMADA or not has_full_payment(reservation):
            raise ValidationError('El servicio requiere una reserva confirmada y pagada por completo.')
        if not reservation.detalle.inicio <= now < reservation.detalle.fin:
            raise ValidationError('El servicio solo puede iniciarse dentro del horario reservado.')
        target = Reserva.Estado.EN_CURSO
    elif action == 'finalizar':
        if state != Reserva.Estado.EN_CURSO:
            raise ValidationError('Solo se finaliza un servicio en curso.')
        target = Reserva.Estado.FINALIZADA
    else:
        raise ValidationError('Accion invalida.')
    reservation.estado = target
    reservation.save(update_fields=['estado', 'updated_at'])
    audit(reservation, action.upper(), user, state, {'motivo': reason, 'sin_reembolso': action == 'cancelar'})
    return reservation


@transaction.atomic
def regularize(user, reservation_id, result, reason):
    reservation = lock_reservation(user, reservation_id)
    if reservation.estado != Reserva.Estado.CONFIRMADA or not has_full_payment(reservation):
        raise ValidationError('Solo se regularizan reservas confirmadas y pagadas.')
    if reservation.detalle.fin > timezone.now():
        raise ValidationError('El horario reservado aun no ha finalizado.')
    if result not in (Reserva.Estado.FINALIZADA, Reserva.Estado.NO_ASISTIO) or not reason.strip():
        raise ValidationError('Selecciona el resultado y registra un motivo.')
    previous = reservation.estado
    reservation.estado = result
    reservation.save(update_fields=['estado', 'updated_at'])
    audit(reservation, 'REGULARIZAR', user, previous, {'motivo': reason.strip(), 'resultado': result})
    return reservation


@transaction.atomic
def reschedule(user, reservation_id, data):
    reservation = lock_reservation(user, reservation_id)
    if reservation.estado != Reserva.Estado.PENDIENTE or reservation.retencion_hasta <= timezone.now() or PagoReserva.objects.filter(reserva=reservation).exists():
        raise ValidationError('Solo se reprograman reservas pendientes de pago con plazo vigente.')
    detail = reservation.detalle
    quotation = quote(detail.cancha_deporte, data['inicio'], data['fin'], exclude=reservation.pk)
    if 'total_esperado' in data and data['total_esperado'] != quotation['total']:
        raise Conflict('La tarifa cambio. Vuelve a cotizar antes de reprogramar.')
    before = {'inicio': detail.inicio.isoformat(), 'fin': detail.fin.isoformat(), 'total': str(reservation.total)}
    for key, value in detail_values(detail.cancha_deporte, quotation).items():
        setattr(detail, key, value)
    detail.save()
    reservation.total = quotation['total']
    reservation.origen = 'RECEPCION'
    # Moving a hold does not restart its countdown or replace the agreed cancellation policy.
    reservation.retencion_hasta = min(reservation.retencion_hasta, quotation['inicio'])
    reservation.save(update_fields=['total', 'retencion_hasta', 'origen', 'updated_at'])
    audit(reservation, 'REPROGRAMAR', user, reservation.estado, {'anterior': before,
          'inicio': detail.inicio.isoformat(), 'fin': detail.fin.isoformat(), 'total': str(reservation.total)})
    return reservation


@transaction.atomic
def create_block(user, data):
    court = lock_court(user, data['cancha'].pk)
    start, end = data['inicio'], data['fin']
    if end <= start or end <= timezone.now():
        raise ValidationError('El bloqueo debe tener un intervalo valido y terminar en el futuro.')
    check_available(court, start, end)
    return BloqueoCancha.objects.create(cancha=court, inicio=start, fin=end, motivo=data['motivo'], creado_por=user)


@transaction.atomic
def release_block(user, block_id):
    initial = BloqueoCancha.objects.get(pk=block_id)
    lock_court(user, initial.cancha_id)
    block = BloqueoCancha.objects.select_for_update().get(pk=block_id)
    if block.activo:
        block.activo = False
        block.liberado_por, block.liberado_at = user, timezone.now()
        block.save(update_fields=['activo', 'liberado_por', 'liberado_at', 'updated_at'])
    return block


@transaction.atomic
def expire(reservation_id):
    initial = Reserva.objects.select_related('detalle__cancha_deporte').get(pk=reservation_id)
    Sucursal.objects.select_for_update().get(pk=initial.sucursal_id)
    Cancha.objects.select_for_update().get(pk=initial.detalle.cancha_deporte.cancha_id)
    reservation = Reserva.objects.select_for_update().get(pk=reservation_id)
    if reservation.estado != Reserva.Estado.PENDIENTE or reservation.retencion_hasta > timezone.now():
        return False
    reservation.estado = Reserva.Estado.EXPIRADA
    reservation.save(update_fields=['estado', 'updated_at'])
    audit(reservation, 'EXPIRAR', None, Reserva.Estado.PENDIENTE)
    return True
