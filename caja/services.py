from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from users.models import Sucursal
from users.access import is_global, effective_permissions
from catalogo.models import Negocio
from reservas import services as booking
from reservas.models import PagoReserva, Reserva
from .models import Caja, SesionCaja, MovimientoCaja, MedioPago, ReciboPago


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
    for receipt in ReciboPago.objects.filter(movimiento__sesion=session).select_related('medio', 'pago'):
        label = receipt.datos['medio']
        totals[label] = totals.get(label, Decimal('0')) + receipt.pago.importe
    return {'esperado': str(expected), 'cobros_por_medio': {key: str(value) for key, value in totals.items()}}


@transaction.atomic
def open_session(user, box_id, amount):
    box = box_lock(user, box_id)
    if box.sesion_actual_id:
        raise booking.Conflict('Esta caja ya tiene un turno abierto.')
    session = SesionCaja.objects.create(caja=box, responsable=user, monto_inicial=amount)
    box.sesion_actual = session
    box.save(update_fields=['sesion_actual', 'updated_at'])
    return session


@transaction.atomic
def manual_movement(user, session_id, kind, amount, reason):
    _, session = session_lock(user, session_id, owner=True)
    if kind == 'RETIRO' and amount > Decimal(summary(session)['esperado']):
        raise ValidationError('El retiro supera el efectivo disponible.')
    return MovimientoCaja.objects.create(sesion=session, tipo=kind, importe=amount, motivo=reason, actor=user)


@transaction.atomic
def close_session(user, session_id, counted, notes):
    box, session = session_lock(user, session_id, owner=True)
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
    return session


@transaction.atomic
def charge(user, data):
    request = {key: str(value) for key, value in data.items() if key != 'clave'}
    initial = SesionCaja.objects.get(pk=data['sesion'])
    booking.scope(user, initial.caja.sucursal_id)
    # Serialize even replays before checking the unique key, including after closing.
    box_lock(user, initial.caja_id)
    previous = ReciboPago.objects.filter(clave=data['clave']).select_related('pago__reserva').first()
    if previous:
        booking.scope(user, previous.pago.reserva.sucursal_id)
        if previous.solicitud != request:
            raise booking.Conflict('La clave de cobro ya se utilizo con otros datos.')
        return previous
    _, session = session_lock(user, initial.pk)
    initial_reservation = Reserva.objects.get(pk=data['reserva'])
    if initial_reservation.sucursal_id != session.caja.sucursal_id:
        raise ValidationError('Reserva y caja deben pertenecer a la misma sucursal.')
    reservation = booking.lock_reservation(user, data['reserva'])
    if reservation.estado != Reserva.Estado.PENDIENTE or reservation.retencion_hasta <= timezone.now():
        raise booking.Conflict('La reserva no tiene una retencion vigente pendiente de pago.')
    if PagoReserva.objects.filter(reserva=reservation).exists():
        raise booking.Conflict('La reserva ya tiene un pago registrado.')
    if data['importe'] != reservation.total:
        raise ValidationError('Se debe cobrar exactamente el total de la reserva.')
    medium = MedioPago.objects.select_for_update().get(pk=data['medio'])
    if not medium.activo:
        raise ValidationError('El medio de pago esta inactivo.')
    received = data['recibido']
    if medium.efectivo:
        if received < reservation.total:
            raise ValidationError('El efectivo recibido no cubre el total.')
    elif received != reservation.total or not data['verificado'] or not data['referencia'].strip():
        raise ValidationError('Verifica el pago QR/transferencia, registra su referencia y el importe exacto.')
    if not medium.efectivo and ReciboPago.objects.filter(medio=medium, referencia_externa=data['referencia']).exists():
        raise booking.Conflict('Esta referencia electronica ya se registro en otro cobro.')
    booking.check_available(reservation.detalle.cancha_deporte.cancha, reservation.detalle.inicio, reservation.detalle.fin, reservation.pk)
    payment = PagoReserva.objects.create(reserva=reservation, referencia=data['clave'], importe=reservation.total, registrado_por=user)
    movement = MovimientoCaja.objects.create(sesion=session, tipo='COBRO', importe=reservation.total,
        efectivo=medium.efectivo, motivo='Cobro de reserva ' + str(reservation.codigo), actor=user)
    business = Negocio.objects.first()
    receipt = ReciboPago.objects.create(pago=payment, movimiento=movement, medio=medium,
        clave=data['clave'], solicitud=request, referencia_externa=data['referencia'], recibido=received,
        cambio=received - reservation.total if medium.efectivo else Decimal('0'), datos={
            'negocio': business.nombre_comercial if business else 'Soccer Real Madrid',
            'sucursal': session.caja.sucursal.nombre, 'caja': session.caja.nombre, 'operador': user.nombre,
            'cliente': reservation.cliente_snapshot, 'codigo_reserva': str(reservation.codigo),
            'cancha': reservation.detalle.cancha_nombre, 'deporte': reservation.detalle.deporte_nombre,
            'inicio': reservation.detalle.inicio.isoformat(), 'fin': reservation.detalle.fin.isoformat(),
            'zona_horaria': reservation.detalle.zona_horaria, 'importe': str(reservation.total),
            'medio': medium.nombre, 'moneda': 'BOB',
        })
    booking.transition(user, reservation.pk, 'confirmar')
    if data.get('iniciar'):
        if 'reservas.iniciar' not in effective_permissions(user):
            raise PermissionDenied('No tienes permiso para iniciar el servicio.')
        booking.transition(user, reservation.pk, 'iniciar')
    return receipt
