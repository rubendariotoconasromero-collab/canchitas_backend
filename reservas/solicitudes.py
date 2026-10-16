from django.db import transaction
from decimal import Decimal
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from users.access import ActionPermission, effective_permissions, is_global
from .models import SolicitudReserva, Cliente
from .serializers import StrictSerializer, ReservaSerializer, ReasonInput
from .public_api import request_state, public_links, public_quote
from . import services


class SolicitudSerializer(serializers.ModelSerializer):
    codigo_correlativo = serializers.CharField(read_only=True)
    sucursal = serializers.IntegerField(source='cancha_deporte.cancha.sucursal_id')
    cancha = serializers.CharField(source='cancha_deporte.cancha.nombre')
    deporte = serializers.CharField(source='cancha_deporte.deporte.nombre')
    zona_horaria = serializers.CharField(source='cancha_deporte.cancha.sucursal.zona_horaria')
    estado = serializers.SerializerMethodField()
    estado_reserva = serializers.SerializerMethodField()
    def get_estado(self, row): return request_state(row)
    def get_estado_reserva(self, row):
        if not row.reserva_id: return None
        return 'EXPIRADA' if row.reserva.estado == 'PENDIENTE' and row.reserva.retencion_hasta <= timezone.now() else row.reserva.estado
    class Meta:
        model = SolicitudReserva
        fields = ['id', 'codigo', 'codigo_correlativo', 'sucursal', 'cancha', 'deporte', 'zona_horaria', 'inicio', 'fin', 'nombre', 'telefono',
                  'correo', 'notas', 'total_cotizado', 'condiciones_snapshot', 'estado', 'reserva', 'estado_reserva', 'revisado_at', 'motivo', 'created_at']


class AcceptInput(StrictSerializer):
    total_esperado = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    condiciones_esperadas = serializers.CharField(allow_blank=True, trim_whitespace=False)
    horas_cancelacion_esperadas = serializers.IntegerField(min_value=0)
    cliente = serializers.PrimaryKeyRelatedField(queryset=Cliente.objects.filter(activo=True), required=False)


class SolicitudViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    serializer_class = SolicitudSerializer
    action_permissions = {'list': ('solicitudes.ver',), 'retrieve': ('solicitudes.ver',),
        'opciones': ('solicitudes.ver',), 'cotizar': ('solicitudes.aceptar',), 'aceptar': ('solicitudes.aceptar',), 'rechazar': ('solicitudes.rechazar',)}

    @action(detail=False, methods=['get'])
    def opciones(self, request):
        from users.models import Sucursal
        branches = Sucursal.objects.filter(estado=True)
        if not is_global(request.user): branches = branches.filter(pk=request.user.sucursal_id)
        return Response(list(branches.values('id', 'nombre')))

    def get_queryset(self):
        rows = SolicitudReserva.objects.select_related('cancha_deporte__cancha__sucursal', 'cancha_deporte__deporte', 'reserva__pago')
        if not is_global(self.request.user): rows = rows.filter(cancha_deporte__cancha__sucursal_id=self.request.user.sucursal_id)
        branch = self.request.query_params.get('sucursal')
        if branch:
            try: rows = rows.filter(cancha_deporte__cancha__sucursal_id=int(branch))
            except ValueError: raise ValidationError('Sucursal invalida.')
        state = self.request.query_params.get('estado', '')
        if state == 'VENCIDA': rows = rows.filter(Q(estado='VENCIDA') | Q(estado='PENDIENTE', inicio__lte=timezone.now()))
        elif state == 'PENDIENTE': rows = rows.filter(estado=state, inicio__gt=timezone.now())
        elif state:
            if state not in SolicitudReserva.Estado.values: raise ValidationError('Estado invalido.')
            rows = rows.filter(estado=state)
        query = self.request.query_params.get('buscar', '')[:160]
        return rows.filter(Q(nombre__icontains=query) | Q(telefono__icontains=query) | Q(cancha_deporte__cancha__nombre__icontains=query))

    def locked(self):
        initial = self.get_object()
        services.lock_court(self.request.user, initial.cancha_deporte.cancha_id)
        return SolicitudReserva.objects.select_for_update().select_related('cancha_deporte__cancha__sucursal',
            'cancha_deporte__deporte', 'reserva').get(pk=initial.pk)

    def pending(self, row):
        if request_state(row) != 'PENDIENTE': raise services.Conflict('La solicitud ya fue procesada o ha vencido.')
        if not public_links().filter(pk=row.cancha_deporte_id).exists(): raise ValidationError('La cancha o deporte ya no estan disponibles al publico.')

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def cotizar(self, request, pk=None):
        if request.data: raise ValidationError('Esta accion no acepta campos adicionales.')
        row = self.locked()
        self.pending(row)
        return Response(public_quote(services.quote(row.cancha_deporte, row.inicio, row.fin)))

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def aceptar(self, request, pk=None):
        serializer = AcceptInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = self.locked()
        if row.estado == 'ACEPTADA' and row.reserva_id:
            return Response({'solicitud': self.get_serializer(row).data, 'reserva': ReservaSerializer(row.reserva).data})
        self.pending(row)
        data = serializer.validated_data
        quote = services.quote(row.cancha_deporte, row.inicio, row.fin)
        if quote['total'] != data['total_esperado'] or quote['condiciones'] != data['condiciones_esperadas'] or quote['horas_cancelacion'] != data['horas_cancelacion_esperadas']:
            raise services.Conflict('El precio o las condiciones cambiaron. Vuelve a cotizar antes de aceptar.')
        client = data.get('cliente')
        if client:
            client = Cliente.objects.select_for_update().get(pk=client.pk)
            if client.sucursal_id != row.cancha_deporte.cancha.sucursal_id or not client.activo:
                raise ValidationError('Selecciona un cliente activo de la misma sucursal.')
        else:
            if 'clientes.crear' not in effective_permissions(request.user):
                raise PermissionDenied('Selecciona un cliente existente o solicita permiso para registrar clientes.')
            client = Cliente.objects.create(sucursal_id=row.cancha_deporte.cancha.sucursal_id,
                nombre=row.nombre, telefono=row.telefono, correo=row.correo)
        reservation = services.retain(request.user, {'cancha_deporte': row.cancha_deporte, 'cliente': client,
            'inicio': row.inicio, 'fin': row.fin, 'total_esperado': data['total_esperado'], 'notas': row.notas})
        reservation.origen = 'WEB'
        reservation.save(update_fields=['origen'])
        services.audit(reservation, 'ACEPTAR_SOLICITUD', request.user, data={'solicitud': str(row.codigo)})
        row.estado, row.reserva = 'ACEPTADA', reservation
        row.revisado_por, row.revisado_at = request.user, timezone.now()
        row.save(update_fields=['estado', 'reserva', 'revisado_por', 'revisado_at', 'updated_at'])
        return Response({'solicitud': self.get_serializer(row).data, 'reserva': ReservaSerializer(reservation).data})

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def rechazar(self, request, pk=None):
        serializer = ReasonInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = self.locked()
        if row.estado == 'RECHAZADA': return Response(self.get_serializer(row).data)
        if request_state(row) != 'PENDIENTE': raise services.Conflict('La solicitud ya fue procesada o ha vencido.')
        row.estado, row.motivo = 'RECHAZADA', serializer.validated_data['motivo']
        row.revisado_por, row.revisado_at = request.user, timezone.now()
        row.save(update_fields=['estado', 'motivo', 'revisado_por', 'revisado_at', 'updated_at'])
        return Response(self.get_serializer(row).data)
