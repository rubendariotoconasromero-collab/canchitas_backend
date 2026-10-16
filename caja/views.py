from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from reservas.views import ScopedView
from users.models import Sucursal
from users.access import is_global
from .models import Caja, MedioPago, SesionCaja, MovimientoCaja, ReciboPago
from .serializers import (CajaSerializer, CajaUpdateSerializer, SessionSerializer, MovementSerializer, ReceiptSerializer,
                          OpenInput, CloseInput, MovementInput, ChargeInput)
from . import services
from auditoria import services as audit


def validated(cls, request):
    obj = cls(data=request.data)
    obj.is_valid(raise_exception=True)
    return obj.validated_data


class CajaViewSet(ScopedView, viewsets.ReadOnlyModelViewSet):
    serializer_class = CajaSerializer
    action_permissions = {'list':('caja.ver','caja.configurar'), 'retrieve':('caja.ver','caja.configurar'),
                          'abrir':('caja.abrir',), 'opciones':('caja.ver','caja.cobrar','caja.configurar'), 'create':('caja.configurar',),
                          'partial_update':('caja.configurar',)}
    def get_permissions(self):
        self.global_only = self.action in ('create', 'partial_update')
        return super().get_permissions()
    def get_queryset(self):
        return self.scoped(Caja.objects.select_related('sucursal').all())
    @action(detail=False, methods=['get'])
    def opciones(self, request):
        return Response({'cajas': CajaSerializer(self.get_queryset().filter(activa=True, sucursal__estado=True), many=True).data,
                         'medios': list(MedioPago.objects.filter(activo=True).values('id','nombre','efectivo')),
                         'sucursales':list(self.scoped(Sucursal.objects.filter(estado=True),'pk').values('id','nombre'))})
    @transaction.atomic
    def create(self, request):
        serializer = CajaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        branch = Sucursal.objects.select_for_update().get(pk=serializer.validated_data['sucursal'].pk)
        if not branch.estado: raise ValidationError('La sucursal esta inactiva.')
        obj = serializer.save()
        audit.changed(request.user, obj, {}, 'crear')
        return Response(serializer.data,status=201)
    @transaction.atomic
    def partial_update(self, request, pk=None):
        box = self.get_object()
        # Match the branch-then-box lock order used by opening and closing turns.
        Sucursal.objects.select_for_update().get(pk=box.sucursal_id)
        box = Caja.objects.select_for_update().get(pk=box.pk)
        before = audit.snapshot(box)
        serializer = CajaUpdateSerializer(box, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get('activa') is False and (
                box.sesion_actual_id or box.sesiones.filter(cierre__isnull=True).exists()):
            raise ValidationError('No se puede desactivar una caja con turno abierto. Cierra el turno primero.')
        serializer.save()
        audit.changed(request.user, box, before)
        return Response(CajaSerializer(box).data)
    @action(detail=True, methods=['post'])
    def abrir(self, request, pk=None):
        box = self.get_object()
        session = services.open_session(request.user, box.pk, validated(OpenInput, request)['monto_inicial'])
        return Response(SessionSerializer(session).data, status=201)


class SessionViewSet(ScopedView, viewsets.ReadOnlyModelViewSet):
    serializer_class = SessionSerializer
    action_permissions = {a: ('caja.ver',) for a in ['list','retrieve','movimientos']}
    action_permissions.update({'cerrar':('caja.cerrar',), 'registrar_movimiento':('caja.movimientos',)})
    def get_queryset(self):
        qs = self.scoped(SesionCaja.objects.select_related('caja__sucursal','responsable').all(), 'caja__sucursal_id')
        if not is_global(self.request.user):
            qs = qs.filter(responsable=self.request.user)
        box = self.request.query_params.get('caja')
        if box:
            if not box.isdigit(): raise ValidationError('Caja invalida.')
            qs = qs.filter(caja_id=int(box))
        return qs
    @action(detail=True, methods=['get'])
    def movimientos(self, request, pk=None):
        return Response(MovementSerializer(self.get_object().movimientos.select_related('actor', 'linea_pago__medio'), many=True).data)
    @action(detail=True, methods=['post'], url_path='movimiento')
    def registrar_movimiento(self, request, pk=None):
        session = self.get_object()
        data = validated(MovementInput, request)
        result = services.manual_movement(request.user, session.pk, data['tipo'], data['importe'], data['motivo'])
        return Response(MovementSerializer(result).data, status=201)
    @action(detail=True, methods=['post'])
    def cerrar(self, request, pk=None):
        session = self.get_object()
        data = validated(CloseInput, request)
        return Response(SessionSerializer(services.close_session(request.user, session.pk,
            data['efectivo_contado'], data['observaciones'])).data)


class ReceiptViewSet(ScopedView, viewsets.ReadOnlyModelViewSet):
    serializer_class = ReceiptSerializer
    action_permissions = {'list':('caja.ver','caja.cobrar'), 'retrieve':('caja.ver','caja.cobrar'), 'create':('caja.cobrar',)}
    def get_queryset(self):
        qs = self.scoped(ReciboPago.objects.select_related('pago__reserva','movimiento').prefetch_related(
            'lineas__medio', 'lineas__movimiento__sesion__responsable'), 'pago__reserva__sucursal_id')
        if not is_global(self.request.user):
            qs = qs.filter(Q(movimiento__sesion__responsable=self.request.user) |
                           Q(pago__registrado_por=self.request.user))
        for key, field in [('sesion','movimiento__sesion_id'),('reserva','pago__reserva_id')]:
            value = self.request.query_params.get(key)
            if value:
                if not value.isdigit(): raise ValidationError('Filtro invalido.')
                qs = qs.filter(**{field:int(value)})
        return qs
    def create(self, request):
        data = validated(ChargeInput, request)
        for key in ['sesion','reserva']:
            data[key] = data[key].pk
        if data.get('pagos') is not None:
            data['pagos'] = [{**line, 'medio': line['medio'].pk} for line in data['pagos']]
        else:
            data['medio'] = data['medio'].pk
        try:
            receipt = services.charge(request.user, data)
        except IntegrityError:
            from reservas.services import Conflict
            raise Conflict('El cobro ya fue registrado o su clave esta en uso. Actualiza la reserva antes de reintentar.')
        return Response(ReceiptSerializer(receipt).data, status=201)
