from datetime import date, timedelta
from rest_framework.exceptions import ValidationError


def minutes(value):
    return value.hour * 60 + value.minute


def interval(start, end, overnight):
    if start.second or end.second or start.microsecond or end.microsecond:
        raise ValidationError('Usa horarios con precision de minutos.')
    a, b = minutes(start), minutes(end)
    if overnight:
        b += 1440
    if not 0 < b - a <= 1440:
        raise ValidationError('El horario debe durar entre un minuto y 24 horas; revisa el cruce de medianoche.')
    return a, b


def overlaps(a, b, tariff=False):
    start_key, end_key = ('hora_desde', 'hora_hasta') if tariff else ('apertura', 'cierre')
    x, y = interval(a[start_key], a[end_key], a['cruza_medianoche'])
    u, v = interval(b[start_key], b[end_key], b['cruza_medianoche'])
    for week in (-7, 0, 7):
        delta = b['dia_semana'] - a['dia_semana'] + week
        if x < v + delta * 1440 and y > u + delta * 1440:
            if not tariff:
                return True
            # Compare the dates on which each weekly interval actually starts.
            lower = max(a['vigente_desde'], b['vigente_desde'] - timedelta(days=delta))
            upper = min(a['vigente_hasta'] or date(9998, 1, 1),
                        (b['vigente_hasta'] or date(9998, 1, 1)) - timedelta(days=delta))
            first = lower + timedelta(days=(a['dia_semana'] - lower.isoweekday()) % 7)
            if first <= upper:
                return True
    return False


def publication_errors(court):
    errors = []
    if not court.activa or not court.sucursal.estado:
        errors.append('La cancha y la sucursal deben estar activas.')
    if not court.descripcion_publica.strip() or not court.superficie.strip():
        errors.append('Completa descripcion y superficie.')
    if not court.fotos.filter(publicada=True).exists():
        errors.append('Agrega al menos una fotografia publicada.')
    if not court.horarios.filter(activo=True).exists():
        errors.append('Configura un horario activo.')
    sports = court.deportes.filter(activo=True, deporte__activo=True)
    if not sports.exists():
        errors.append('Asigna al menos un deporte activo.')
    from django.utils import timezone
    from django.db.models import Q
    from zoneinfo import ZoneInfo
    today = timezone.localdate(timezone=ZoneInfo(court.sucursal.zona_horaria))
    for sport in sports:
        rates = list(sport.tarifas.filter(activa=True, vigente_desde__lte=today).filter(
            Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=today)))
        windows = []
        for rate in rates:
            a, b = interval(rate.hora_desde, rate.hora_hasta, rate.cruza_medianoche)
            for shift in (-10080, 0, 10080):
                windows.append((a + (rate.dia_semana - 1) * 1440 + shift,
                                b + (rate.dia_semana - 1) * 1440 + shift))
        for opening in court.horarios.filter(activo=True):
            a, b = interval(opening.apertura, opening.cierre, opening.cruza_medianoche)
            cursor, end = a + (opening.dia_semana - 1) * 1440, b + (opening.dia_semana - 1) * 1440
            for start, finish in sorted(windows):
                if start <= cursor < finish:
                    cursor = finish
            if cursor < end:
                errors.append(f'Las tarifas vigentes de {sport.deporte.nombre} no cubren todos los horarios.')
                break
    if not court.sucursal.politicas.filter(activa=True).exists():
        errors.append('Configura una politica para la sucursal.')
    return errors
