from types import SimpleNamespace
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from reportes.services import authorize, branches, branch_for, period, paginate, bounded, read_snapshot, positive_int
from .models import EventoAuditoria


@api_view(['GET'])
def events(request):
    authorize(request.user, 'auditoria.ver', 'alcance.global')
    params = request.query_params
    if set(params) - {'desde', 'hasta', 'sucursal', 'entidad', 'accion', 'actor', 'q', 'page', 'page_size'}:
        raise ValidationError('Filtros no admitidos.')
    branch = branch_for(request.user, params['sucursal']) if params.get('sucursal') else None
    start, end = period(params, branch or SimpleNamespace(zona_horaria='America/La_Paz'))
    qs = EventoAuditoria.objects.filter(created_at__gte=start, created_at__lt=end)
    if branch:
        qs = qs.filter(sucursal=branch)
    for key in ('entidad', 'accion'):
        if params.get(key):
            qs = qs.filter(**{key: params[key]})
    if params.get('actor'):
        qs = qs.filter(actor_id=positive_int(params['actor'], 'actor'))
    query = params.get('q', '').strip()
    if len(query) > 160:
        raise ValidationError('La busqueda admite hasta 160 caracteres.')
    if query:
        qs = qs.filter(Q(actor_nombre__icontains=query) | Q(entidad__icontains=query) | Q(objeto_id__icontains=query))
    with read_snapshot():
        rows = [{'id': e.pk, 'fecha': e.created_at.isoformat(), 'entidad': e.entidad, 'objeto_id': e.objeto_id,
                 'accion': e.accion, 'actor': e.actor_nombre, 'sucursal': e.sucursal.nombre if e.sucursal else 'Global',
                 'anterior': e.anterior, 'nuevo': e.nuevo}
                for e in bounded(qs.select_related('sucursal').order_by('-created_at', '-pk'))]
        return Response({**paginate(rows, params), 'sucursales': list(branches(request.user).values('id', 'nombre'))}, headers={'Cache-Control': 'no-store'})
