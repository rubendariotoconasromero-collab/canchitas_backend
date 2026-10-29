from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from users.access import ActionPermission, effective_permissions, is_global
from users.models import Sucursal
from catalogo.models import Cancha, CanchaDeporte
from .models import Cliente, Reserva, BloqueoCancha
from .serializers import ClienteSerializer, ReservaSerializer, BookingInput, MoveInput, ReasonInput, RegularizationInput, BlockInput, BlockSerializer
from . import services


class ScopedView(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]

    def scoped(self, queryset, field='sucursal_id'):
        return queryset if is_global(self.request.user) else queryset.filter(**{field: self.request.user.sucursal_id})


class ClienteViewSet(ScopedView, viewsets.ModelViewSet):
    serializer_class = ClienteSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    action_permissions = {a: (f'clientes.{p}',) for a, p in [('list', 'ver'), ('retrieve', 'ver'), ('create', 'crear'), ('partial_update', 'editar')]}
    action_permissions['opciones'] = ('clientes.ver',)

    @action(detail=False, methods=['get'])
    def opciones(self, request):
        return Response(list(self.scoped(Sucursal.objects.filter(estado=True), 'pk').values('id', 'nombre')))

    def get_queryset(self):
        qs = self.scoped(Cliente.objects.all())
        branch = self.request.query_params.get('sucursal')
        if branch:
            try:
                qs = qs.filter(sucursal_id=int(branch))
            except ValueError:
                raise ValidationError('Sucursal invalida.')
        search = self.request.query_params.get('search', '')[:160]
        return qs.filter(Q(nombre__icontains=search) | Q(telefono__icontains=search) | Q(documento__icontains=search))

    @transaction.atomic
    def perform_create(self, serializer):
        branch = serializer.validated_data['sucursal']
        services.scope(self.request.user, branch.pk)
        branch = Sucursal.objects.select_for_update().get(pk=branch.pk)
        if not branch.estado:
            raise ValidationError('La sucursal esta inactiva.')
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        instance = serializer.instance
        Sucursal.objects.select_for_update().get(pk=instance.sucursal_id)
        serializer.instance = instance = Cliente.objects.select_for_update().get(pk=instance.pk)
        if serializer.validated_data.get('sucursal', instance.sucursal).pk != instance.sucursal_id:
            raise ValidationError('No se puede cambiar la sucursal del cliente.')
        if serializer.validated_data.get('activo', instance.activo) != instance.activo and 'clientes.desactivar' not in effective_permissions(self.request.user):
            raise PermissionDenied('No puedes cambiar el estado del cliente.')
        serializer.save()


class ReservaViewSet(ScopedView, viewsets.ReadOnlyModelViewSet):
    serializer_class = ReservaSerializer
    action_permissions = {a: ('reservas.ver',) for a in ['list', 'retrieve', 'agenda', 'opciones']}
    action_permissions.update({a: (f'reservas.{p}',) for a, p in [('create', 'crear'), ('cotizar', 'crear'), ('cancelar', 'cancelar'), ('reprogramar', 'reprogramar'), ('confirmar', 'confirmar'), ('iniciar', 'iniciar'), ('finalizar', 'finalizar')]})
    action_permissions['cotizar_reprogramacion'] = ('reservas.reprogramar',)
    action_permissions['regularizar'] = ('reservas.finalizar',)

    def get_queryset(self):
        return self.scoped(Reserva.objects.select_related('detalle__cancha_deporte__cancha', 'pago').prefetch_related('historial__actor'))

    def input(self, request):
        serializer = BookingInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.scope(request.user, data['cancha_deporte'].cancha.sucursal_id)
        if data['alquiler_inmediato'] and 'reservas.alquilar_inmediato' not in effective_permissions(request.user):
            raise PermissionDenied('No puedes alquilar al momento.')
        return data

    def create(self, request):
        data = self.input(request)
        if 'cliente' not in data and not data['sin_cliente']:
            raise ValidationError({'cliente': 'Selecciona un cliente o Sin cliente registrado.'})
        reservation = services.retain(request.user, data, allow_unidentified=True)
        return Response(self.get_serializer(reservation).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def cotizar(self, request):
        data = self.input(request)
        services.lock_court(request.user, data['cancha_deporte'].cancha_id)
        link = CanchaDeporte.objects.select_related('cancha__sucursal', 'deporte').get(pk=data['cancha_deporte'].pk)
        start, end = services.resolve_interval(data)
        value = services.quote(link, start, end, data['alquiler_inmediato'])
        value['total'] = str(value['total'])
        return Response(value)

    @action(detail=False, methods=['get'])
    def opciones(self, request):
        branches = self.scoped(Sucursal.objects.filter(estado=True), 'pk')
        links = CanchaDeporte.objects.filter(cancha__sucursal__in=branches, cancha__activa=True, activo=True, deporte__activo=True).select_related('cancha__sucursal', 'deporte')
        return Response({'sucursales': list(branches.values('id', 'nombre', 'zona_horaria')),
            'canchas': list(Cancha.objects.filter(sucursal__in=branches).values('id', 'sucursal', 'nombre', 'activa')),
            'canchas_deportes': [{'id': x.pk, 'cancha': x.cancha_id, 'sucursal': x.cancha.sucursal_id,
                'nombre': f'{x.cancha.nombre} / {x.deporte.nombre}', 'minutos': x.cancha.duracion_minima_minutos} for x in links]})

    @action(detail=False, methods=['get'])
    def agenda(self, request):
        try:
            branch_id = int(request.query_params['sucursal'])
            day = datetime.strptime(request.query_params['fecha'], '%Y-%m-%d').date()
            if 'hasta' in request.query_params:
                last = datetime.strptime(request.query_params['hasta'], '%Y-%m-%d').date()
                days = (last - day).days
            else:
                days = int(request.query_params.get('dias', '1'))
                if days not in [1, 7]:
                    raise ValueError()
            if not 1 <= days <= 42:
                raise ValueError()
        except (KeyError, ValueError):
            raise ValidationError('Indica sucursal y fecha YYYY-MM-DD; dias 1 o 7, o hasta (fin exclusivo, maximo 42 dias).')
        branch = get_object_or_404(self.scoped(Sucursal.objects.all(), 'pk'), pk=branch_id)
        zone = ZoneInfo(branch.zona_horaria)
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day + timedelta(days=days), time.min, zone)
        courts = Cancha.objects.filter(sucursal=branch).order_by('nombre')
        court_id = request.query_params.get('cancha')
        if court_id:
            try:
                courts = courts.filter(pk=int(court_id))
            except ValueError:
                raise ValidationError('Cancha invalida.')
        reserves = self.get_queryset().filter(detalle__cancha_deporte__cancha__in=courts, detalle__inicio__lt=end, detalle__fin__gt=start)
        blocks = BloqueoCancha.objects.filter(cancha__in=courts, activo=True, inicio__lt=end, fin__gt=start)
        windows = []
        for court in courts.prefetch_related('horarios'):
            for offset in range(-1, days):
                anchor = day + timedelta(days=offset)
                for opening in court.horarios.all():
                    if opening.activo and opening.dia_semana == anchor.isoweekday():
                        a, b = services.window(anchor, opening.apertura, opening.cierre, opening.cruza_medianoche, zone)
                        if a < end and b > start:
                            windows.append({'cancha': court.pk, 'inicio': max(a, start), 'fin': min(b, end)})
        return Response({'zona_horaria': branch.zona_horaria, 'canchas': list(courts.values('id', 'nombre', 'activa')),
            'horarios': windows, 'reservas': self.get_serializer(reserves, many=True).data, 'bloqueos': BlockSerializer(blocks, many=True).data})

    def change(self, request, action):
        obj = self.get_object()
        reason = ''
        if action == 'cancelar':
            serializer = ReasonInput(data=request.data)
            serializer.is_valid(raise_exception=True)
            reason = serializer.validated_data['motivo']
        elif request.data:
            raise ValidationError('Esta accion no acepta campos adicionales.')
        return Response(self.get_serializer(services.transition(request.user, obj.pk, action, reason)).data)

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        return self.change(request, 'cancelar')

    @action(detail=True, methods=['post'])
    def confirmar(self, request, pk=None):
        return self.change(request, 'confirmar')

    @action(detail=True, methods=['post'])
    def iniciar(self, request, pk=None):
        return self.change(request, 'iniciar')

    @action(detail=True, methods=['post'])
    def finalizar(self, request, pk=None):
        return self.change(request, 'finalizar')

    @action(detail=True, methods=['post'])
    def regularizar(self, request, pk=None):
        obj = self.get_object()
        serializer = RegularizationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(self.get_serializer(services.regularize(request.user, obj.pk,
            serializer.validated_data['resultado'], serializer.validated_data['motivo'])).data)

    @action(detail=True, methods=['post'])
    def reprogramar(self, request, pk=None):
        obj = self.get_object()
        serializer = MoveInput(data=request.data, context={'zona_horaria': obj.detalle.zona_horaria})
        serializer.is_valid(raise_exception=True)
        return Response(self.get_serializer(services.reschedule(request.user, obj.pk, serializer.validated_data)).data)

    @action(detail=True, methods=['post'], url_path='cotizar-reprogramacion')
    @transaction.atomic
    def cotizar_reprogramacion(self, request, pk=None):
        obj = services.lock_reservation(request.user, self.get_object().pk)
        if obj.estado != 'PENDIENTE' or obj.retencion_hasta <= timezone.now() or hasattr(obj, 'pago'):
            raise ValidationError('Solo se reprograman reservas pendientes de pago con plazo vigente.')
        serializer = MoveInput(data=request.data, context={'zona_horaria': obj.detalle.zona_horaria})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        value = services.quote(obj.detalle.cancha_deporte, data['inicio'], data['fin'], exclude=obj.pk)
        value['total'] = str(value['total'])
        return Response(value)


class BloqueoViewSet(ScopedView, viewsets.ReadOnlyModelViewSet):
    serializer_class = BlockSerializer
    action_permissions = {'list': ('bloqueos.ver',), 'retrieve': ('bloqueos.ver',), 'create': ('bloqueos.crear',), 'liberar': ('bloqueos.liberar',)}

    def get_queryset(self):
        return self.scoped(BloqueoCancha.objects.select_related('cancha'), 'cancha__sucursal_id')

    def create(self, request):
        serializer = BlockInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(self.get_serializer(services.create_block(request.user, serializer.validated_data)).data, status=201)

    @action(detail=True, methods=['post'])
    def liberar(self, request, pk=None):
        if request.data:
            raise ValidationError('Esta accion no acepta campos adicionales.')
        return Response(self.get_serializer(services.release_block(request.user, self.get_object().pk)).data)
