from datetime import date, datetime, time
from decimal import Decimal
from django.db import connection
from .models import EventoAuditoria


# Explicit allowlists keep credentials, request bodies and personal contact data out.
FIELDS = {
    'User': ('nombre', 'estado', 'is_active', 'sucursal_id'),
    'Rol': ('nombre', 'estado'), 'Permiso': ('nombre', 'estado'),
    'Cancha': ('nombre', 'activa', 'publicada', 'superficie', 'ancho_metros', 'largo_metros', 'duracion_minima_minutos'),
    'TarifaCancha': ('nombre', 'precio_hora', 'dia_semana', 'hora_desde', 'hora_hasta', 'cruza_medianoche', 'vigente_desde', 'vigente_hasta', 'activa'),
    'PoliticaReserva': ('version', 'horas_limite_cancelacion', 'minutos_retencion', 'activa'),
    'Caja': ('nombre', 'activa'),
    'SesionCaja': ('caja_id', 'responsable_id', 'monto_inicial', 'cierre', 'efectivo_esperado', 'efectivo_contado', 'diferencia'),
    'MovimientoCaja': ('sesion_id', 'tipo', 'importe', 'efectivo'),
    'PagoReserva': ('reserva_id', 'importe', 'registrado_por_id'),
    'LineaPago': ('recibo_id', 'medio_id', 'movimiento_id', 'importe', 'recibido', 'cambio', 'referencia_externa', 'verificado'),
}


def snapshot(obj):
    if obj is None:
        return {}
    values = {field: getattr(obj, field) for field in FIELDS.get(type(obj).__name__, ())}
    if type(obj).__name__ == 'User':
        values['accesos'] = sorted(obj.roles_permisos_usuario.values_list('rol_permiso_id', flat=True))
    elif type(obj).__name__ == 'Rol':
        values['permisos'] = sorted(obj.roles_permisos.values_list('permiso_id', flat=True))
    return {key: str(value) if isinstance(value, (Decimal, date, datetime, time)) else value
            for key, value in values.items()}


def record(actor, entity, object_id, action, branch_id=None, before=None, after=None):
    if not connection.in_atomic_block:
        raise RuntimeError('La auditoria debe guardarse dentro de la transaccion de negocio.')
    return EventoAuditoria.objects.create(entidad=entity, objeto_id=str(object_id), accion=action,
        actor=actor, actor_nombre=actor.nombre if actor else '', sucursal_id=branch_id,
        anterior=before or {}, nuevo=after or {})


def changed(actor, obj, before, action='editar', branch_id=None, force=False):
    after = snapshot(obj)
    if (before != after or force) and type(obj).__name__ in FIELDS:
        if branch_id is None:
            branch_id = getattr(obj, 'sucursal_id', None)
            if not branch_id and hasattr(obj, 'cancha_deporte'):
                branch_id = obj.cancha_deporte.cancha.sucursal_id
        record(actor, type(obj).__name__, obj.pk, action, branch_id, before, after)
