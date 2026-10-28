from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from users.models import Sucursal
from users.access import is_global, effective_permissions
from catalogo.models import Negocio
from reservas import services as booking
from reservas.models import PagoReserva, Reserva
from .models import Caja, SesionCaja, MovimientoCaja, MedioPago, ReciboPago, LineaPago
from auditoria import services as audit


def box_lock(user, pk):
    box = Caja.objects.select_related('sucursal').get(pk=pk)
    booking.scope(user, box.sucursal_id)
    # All money writers lock branch first, matching the reservation/catalog protocol.
    Sucursal.objects.select_for_update().get(pk=box.sucursal_id)
    box = Caja.objects.select_for_update().select_related('sucursal').get(pk=pk)
    if not box.activa or not box.sucursal.estado:
        raise ValidationError('La caja o sucursal esta inactiva.')
    return box


def session_lock(user, pk, owner=False):
    initial = SesionCaja.objects.get(pk=pk)
    box = box_lock(user, initial.caja_id)
    session = SesionCaja.objects.select_for_update().get(pk=pk)
    if session.cierre or box.sesion_actual_id != session.pk:
        raise booking.Conflict('El turno de caja esta cerrado.')
    if owner and session.responsable_id != user.pk and not is_global(user):
        raise PermissionDenied('Solo el responsable o un administrador global puede operar este turno.')
    return box, session


def summary(session):
    expected = session.monto_inicial
    totals = {}
    for movement in session.movimientos.all():
        if movement.efectivo:
            expected += -movement.importe if movement.tipo == 'RETIRO' else movement.importe
    for line in LineaPago.objects.filter(movimiento__sesion=session):
        label = line.medio_nombre
        totals[label] = totals.get(label, Decimal('0')) + line.importe
    return {'esperado': str(expected), 'cobros_por_medio': {key: str(value) for key, value in totals.items()}}


@transaction.atomic
def open_session(user, box_id, amount):
    box = box_lock(user, box_id)
    if box.sesion_actual_id:
        raise booking.Conflict('Esta caja ya tiene un turno abierto.')
    session = SesionCaja.objects.create(caja=box, responsable=user, monto_inicial=amount)
    box.sesion_actual = session
    box.save(update_fields=['sesion_actual', 'updated_at'])
    audit.changed(user, session, {}, 'abrir', box.sucursal_id)
    return session


@transaction.atomic
def manual_movement(user, session_id, kind, amount, reason):
    _, session = session_lock(user, session_id, owner=True)
    if kind == 'RETIRO' and amount > Decimal(summary(session)['esperado']):
        raise ValidationError('El retiro supera el efectivo disponible.')
    movement = MovimientoCaja.objects.create(sesion=session, tipo=kind, importe=amount, motivo=reason, actor=user)
    audit.changed(user, movement, {}, 'registrar', session.caja.sucursal_id)
    return movement


@transaction.atomic
def close_session(user, session_id, counted, notes):
    box, session = session_lock(user, session_id, owner=True)
    before = audit.snapshot(session)
    expected = Decimal(summary(session)['esperado'])
    if counted != expected and not notes.strip():
        raise ValidationError('Registra una observacion para justificar la diferencia de cierre.')
    session.cierre = timezone.now()
    session.cerrado_por = user
    session.efectivo_esperado = expected
    session.efectivo_contado = counted
    session.diferencia = counted - expected
    session.observaciones = notes
    session.save()
    box.sesion_actual = None
    box.save(update_fields=['sesion_actual', 'updated_at'])
    audit.changed(user, session, before, 'cerrar', box.sucursal_id)
    return session


@transaction.atomic
def charge(user, data):
    legacy_request = not data.get('pagos')
    if legacy_request:
        request = {key: str(value) for key, value in data.items() if key != 'clave'}
        raw_lines = [{
            'medio': data['medio'], 'importe': data['importe'], 'recibido': data['recibido'],
            'referencia': data['referencia'], 'verificado': data['verificado'],
        }]
    else:
        raw_lines = data['pagos']
        request = {
            'sesion': str(data['sesion']), 'reserva': str(data['reserva']),
            'importe': str(data['importe']), 'iniciar': str(data.get('iniciar', False)),
            'pagos': [{
                'medio': str(line['medio']), 'importe': str(line['importe']),
                'recibido': str(line['recibido']), 'referencia': line['referencia'],
                'verificado': str(line['verificado']),
            } for line in raw_lines],
        }
    initial = SesionCaja.objects.get(pk=data['sesion'])
    booking.scope(user, initial.caja.sucursal_id)
    # Serialize even replays before checking the unique key, including after closing.
    box_lock(user, initial.caja_id)
    previous = ReciboPago.objects.filter(clave=data['clave']).select_related('pago__reserva', 'movimiento__sesion').first()
    if previous:
        booking.scope(user, previous.pago.reserva.sucursal_id)
        if not is_global(user) and user.pk not in (
                previous.pago.registrado_por_id, previous.movimiento.sesion.responsable_id):
            raise PermissionDenied('No tienes acceso a este cobro.')
        if previous.solicitud != request:
            raise booking.Conflict('La clave de cobro ya se utilizo con otros datos.')
        return previous
    _, session = session_lock(user, initial.pk)
    initial_reservation = Reserva.objects.get(pk=data['reserva'])
    if initial_reservation.sucursal_id != session.caja.sucursal_id:
        raise ValidationError('Reserva y caja deben pertenecer a la misma sucursal.')
    reservation = booking.lock_reservation(user, data['reserva'])
    if reservation.estado != Reserva.Estado.PENDIENTE or reservation.retencion_hasta <= timezone.now():
        raise booking.Conflict('La reserva no esta pendiente de pago o su plazo para pagar ha vencido.')
    if PagoReserva.objects.filter(reserva=reservation).exists():
        raise booking.Conflict('La reserva ya tiene un pago registrado.')
    if data['importe'] != reservation.total:
        raise ValidationError('El importe debe coincidir con el total de la reserva.')
    if not raw_lines or sum((line['importe'] for line in raw_lines), Decimal('0')) != reservation.total:
        raise ValidationError('La suma de las formas de pago debe cubrir exactamente el total de la reserva.')

    medium_ids = sorted({line['medio'].pk if isinstance(line['medio'], MedioPago) else int(line['medio']) for line in raw_lines})
    media = {medium.pk: medium for medium in MedioPago.objects.select_for_update().filter(pk__in=medium_ids).order_by('pk')}
    if len(media) != len(medium_ids) or any(
            not medium.activo and not (legacy_request and medium.codigo == 'QR')
            for medium in media.values()):
        raise ValidationError('Uno o mas medios de pago ya no estan disponibles.')

    lines = []
    seen_references = set()
    for raw_line in raw_lines:
        medium_id = raw_line['medio'].pk if isinstance(raw_line['medio'], MedioPago) else int(raw_line['medio'])
        medium = media[medium_id]
        amount = raw_line['importe']
        received = raw_line['recibido']
        reference = raw_line['referencia'].strip()
        verified = raw_line['verificado']
        if amount <= 0:
            raise ValidationError('Cada importe aplicado debe ser mayor a cero.')
        if medium.efectivo:
            if received < amount:
                raise ValidationError('El efectivo recibido debe cubrir el importe aplicado.')
            change = received - amount
        else:
            if received != amount or not verified or not reference:
                raise ValidationError('Verifica cada transferencia QR, registra su referencia e importe exacto.')
            reference_key = (medium_id, reference.casefold())
            if reference_key in seen_references:
                raise booking.Conflict('No repitas la misma referencia en un cobro.')
            seen_references.add(reference_key)
            if LineaPago.objects.filter(medio__codigo__in=['QR', 'TRANSFERENCIA_QR'],
                    referencia_externa__iexact=reference).exists():
                raise booking.Conflict('Esta referencia electronica ya se registro en otro cobro.')
            change = Decimal('0')
        lines.append({'medio': medium, 'importe': amount, 'recibido': received,
                      'referencia': reference, 'verificado': verified, 'cambio': change})

    booking.check_available(reservation.detalle.cancha_deporte.cancha, reservation.detalle.inicio, reservation.detalle.fin, reservation.pk)
    payment = PagoReserva.objects.create(reserva=reservation, referencia=data['clave'], importe=reservation.total, registrado_por=user)
    movements = []
    for line in lines:
        movement = MovimientoCaja.objects.create(sesion=session, tipo='COBRO', importe=line['importe'],
            efectivo=line['medio'].efectivo, motivo='Cobro de reserva ' + reservation.codigo_correlativo, actor=user)
        movements.append(movement)
    business = Negocio.objects.first()
    medium_totals = {}
    for line in lines:
        medium_totals[line['medio'].nombre] = medium_totals.get(line['medio'].nombre, Decimal('0')) + line['importe']
    label_parts = [f'{name}: Bs {amount:.2f}' for name, amount in medium_totals.items()]
    receipt = ReciboPago.objects.create(pago=payment, movimiento=movements[0], medio=lines[0]['medio'],
        clave=data['clave'], solicitud=request,
        referencia_externa=lines[0]['referencia'] if len(lines) == 1 else '',
        recibido=sum((line['recibido'] for line in lines), Decimal('0')),
        cambio=sum((line['cambio'] for line in lines), Decimal('0')), datos={
            'negocio': business.nombre_comercial if business else 'Soccer Real Madrid',
            'sucursal': session.caja.sucursal.nombre, 'caja': session.caja.nombre, 'operador': user.nombre,
            'cliente': reservation.cliente_snapshot, 'codigo_reserva': str(reservation.codigo),
            'codigo_reserva_correlativo': reservation.codigo_correlativo, 'codigo_pago': payment.codigo_correlativo,
            'cancha': reservation.detalle.cancha_nombre, 'deporte': reservation.detalle.deporte_nombre,
            'inicio': reservation.detalle.inicio.isoformat(), 'fin': reservation.detalle.fin.isoformat(),
            'zona_horaria': reservation.detalle.zona_horaria, 'importe': str(reservation.total),
            'medio': ' · '.join(label_parts), 'moneda': 'BOB',
            'medios': [{'medio': line['medio'].nombre, 'efectivo': line['medio'].efectivo,
                        'importe': str(line['importe']),
                        'recibido': str(line['recibido']), 'cambio': str(line['cambio']),
                        'referencia': line['referencia'], 'verificado': line['verificado']}
                       for line in lines],
        })
    for line, movement in zip(lines, movements):
        detail = LineaPago.objects.create(recibo=receipt, medio=line['medio'], medio_nombre=line['medio'].nombre,
            medio_efectivo=line['medio'].efectivo, movimiento=movement,
            importe=line['importe'], recibido=line['recibido'], cambio=line['cambio'],
            referencia_externa=line['referencia'], verificado=line['verificado'])
        audit.changed(user, detail, {}, 'registrar', reservation.sucursal_id)
    booking.transition(user, reservation.pk, 'confirmar')
    if data.get('iniciar'):
        if 'reservas.iniciar' not in effective_permissions(user):
            raise PermissionDenied('No tienes permiso para iniciar el servicio.')
        booking.transition(user, reservation.pk, 'iniciar')
    audit.changed(user, payment, {}, 'cobrar', reservation.sucursal_id)
    return receipt
