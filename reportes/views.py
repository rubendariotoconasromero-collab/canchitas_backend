import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from django.db import transaction
from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from users.access import effective_permissions
from catalogo.models import Cancha, Deporte
from caja.models import Caja, MedioPago, SesionCaja
from users.models import User
from auditoria.services import record
from .exports import columns_for, NUMERIC, HEADERS, safe_cell, make_xlsx, make_pdf
from .services import authorize, branches, branch_for, read_snapshot, build, paginate, TYPES


@api_view(['GET'])
def options(request):
    allowed = authorize(request.user, 'reportes.ver')
    if not any('reportes.' + kind in allowed for kind in TYPES):
        raise PermissionDenied('No tienes reportes habilitados.')
    choices = list(branches(request.user).values('id', 'nombre'))
    selected = request.query_params.get('sucursal') or (str(choices[0]['id']) if choices else '')
    branch = branch_for(request.user, selected)
    global_scope = 'alcance.global' in effective_permissions(request.user)
    operators = User.objects.filter(estado=True, is_active=True)
    operators = operators.filter(pk=request.user.pk) if not global_scope else operators.filter(
        pk__in=User.objects.filter(sucursal=branch).values('pk').union(
            User.objects.filter(pagoreserva__reserva__sucursal=branch).values('pk'),
            SesionCaja.objects.filter(caja__sucursal=branch).values('responsable_id')))
    return Response({'sucursales': choices, 'sucursal': branch.pk,
        'canchas': list(Cancha.objects.filter(sucursal=branch).values('id', 'nombre')),
        'deportes': list(Deporte.objects.all().values('id', 'nombre')),
        'cajas': list(Caja.objects.filter(sucursal=branch).values('id', 'nombre')),
        'medios': list(MedioPago.objects.all().values('id', 'nombre')),
        'operadores': list(operators.values('id', 'nombre'))}, headers={'Cache-Control': 'no-store'})


@api_view(['GET'])
def report(request, kind):
    with read_snapshot():
        rows, summary, meta = build(request.user, kind, request.query_params)
        return Response({**paginate(rows, request.query_params), 'resumen': summary, 'meta': meta}, headers={'Cache-Control': 'no-store'})


def export_meta(request, meta, file_format):
    zone = ZoneInfo(meta['zona_horaria'])
    generated = datetime.fromisoformat(meta['generado_en'])
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=zone)
    meta['generado_local'] = generated.astimezone(zone).strftime('%d/%m/%Y %H:%M')
    meta['generado_por'] = request.user.nombre

    selected_filters = []
    filter_models = {
        'cancha': ('Cancha', Cancha, {'sucursal_id': meta['sucursal']}, 'nombre'),
        'deporte': ('Deporte', Deporte, {}, 'nombre'),
        'caja': ('Caja', Caja, {'sucursal_id': meta['sucursal']}, 'nombre'),
        'medio': ('Medio de pago', MedioPago, {}, 'nombre'),
        'operador': ('Operador', User, {}, 'nombre'),
    }
    for key, (label, model, scope, field) in filter_models.items():
        value = request.query_params.get(key)
        if value:
            name = model.objects.filter(pk=int(value), **scope).values_list(field, flat=True).first()
            selected_filters.append(f'{label}: {name or value}')
    states = {'PENDIENTE': 'Pendiente de pago', 'CONFIRMADA': 'Confirmada', 'EN_CURSO': 'En curso',
              'FINALIZADA': 'Finalizada', 'NO_ASISTIO': 'Cliente no asistio', 'CANCELADA': 'Cancelada'}
    if request.query_params.get('estado'):
        value = request.query_params['estado']
        selected_filters.append('Estado: ' + states.get(value, value))
    if request.query_params.get('q'):
        selected_filters.append('Busqueda: ' + request.query_params['q'].strip())
    meta['filtros_texto'] = '; '.join(selected_filters)
    meta['formato'] = file_format
    return meta


def export_response(request, kind, file_format):
    authorize(request.user, 'reportes.ver', 'reportes.exportar', 'reportes.' + kind)
    with read_snapshot():
        rows, summary, meta = build(request.user, kind, request.query_params)
        meta = export_meta(request, meta, file_format)
    if file_format in {'xlsx', 'pdf'}:
        try:
            if file_format == 'xlsx':
                content = make_xlsx(kind, rows, summary, meta)
                content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            else:
                content = make_pdf(kind, rows, summary, meta)
                content_type = 'application/pdf'
        except ModuleNotFoundError as error:
            package = (error.name or '').split('.')[0]
            package_names = {'openpyxl': 'openpyxl', 'reportlab': 'reportlab'}
            if package not in package_names:
                raise
            return Response({'detail': f'Falta instalar {package_names[package]}. Activa el entorno virtual y ejecuta: pip install -r requirements.txt'}, status=503)
    else:
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        columns = columns_for(kind, meta)
        writer.writerow([HEADERS[column] for column in columns])
        for row in rows:
            writer.writerow([row.get(column, '') if column in NUMERIC else safe_cell(row.get(column, '')) for column in columns])
        content = '\ufeff' + output.getvalue()
        content_type = 'text/csv; charset=utf-8'
    with transaction.atomic():
        record(request.user, 'Reporte', kind, 'exportar', meta['sucursal'], after={
            'tipo': kind, 'desde': meta['desde'], 'hasta': meta['hasta'], 'registros': len(rows),
            'formato': file_format, 'filtros': {k: v for k, v in request.query_params.items() if k not in ('q', 'page', 'page_size')}})
    extension = {'xlsx': 'xlsx', 'pdf': 'pdf', 'csv': 'csv'}[file_format]
    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="{kind}-{meta["desde"]}-{meta["hasta"]}.{extension}"'
    response['Cache-Control'] = 'no-store'
    return response


@api_view(['GET'])
def export(request, kind):
    return export_response(request, kind, 'csv')


@api_view(['GET'])
def export_xlsx(request, kind):
    return export_response(request, kind, 'xlsx')


@api_view(['GET'])
def export_pdf(request, kind):
    return export_response(request, kind, 'pdf')
