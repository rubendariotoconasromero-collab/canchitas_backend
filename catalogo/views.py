from django.db import transaction
from django.db.models import Max
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from users.access import ActionPermission, is_global, effective_permissions
from users.models import Sucursal
from .models import Negocio, Deporte, Cancha, CanchaDeporte, FotoCancha, HorarioCancha, TarifaCancha, PoliticaReserva
from .serializers import (NegocioSerializer, SucursalCatalogoSerializer, DeporteSerializer,
                          CanchaSerializer, CanchaDeporteSerializer, FotoSerializer,
                          HorarioSerializer, TarifaSerializer, PoliticaSerializer)
from .rules import publication_errors


class CatalogViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    resource = 'canchas'
    branch_lookup = 'sucursal_id'
    state_field = 'activa'
    global_only = False

    @property
    def action_permissions(self):
        return {'list': (self.resource + '.ver',), 'retrieve': (self.resource + '.ver',),
                'create': (self.resource + '.crear',), 'update': (self.resource + '.editar',),
                'partial_update': (self.resource + '.editar',), 'destroy': (self.resource + '.desactivar',)}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.branch_lookup and not is_global(self.request.user):
            qs = qs.filter(**{self.branch_lookup: self.request.user.sucursal_id})
        for key in ('sucursal', 'cancha', 'cancha_deporte'):
            value = self.request.query_params.get(key)
            if value and key in {f.name for f in qs.model._meta.fields}:
                if not value.isdigit():
                    raise ValidationError('Filtro invalido.')
                qs = qs.filter(**{key + '_id': int(value)})
        return qs.order_by('pk')

    def lock_scope(self):
        qs = Sucursal.objects.select_for_update().order_by('pk')
        if not is_global(self.request.user):
            qs = qs.filter(pk=self.request.user.sucursal_id)
        # All catalog writers take branch locks before validating intervals/publication.
        return list(qs)

    def validate_scope(self, serializer):
        data = serializer.validated_data
        instance = serializer.instance
        for field in ('sucursal', 'cancha', 'cancha_deporte'):
            if field not in data:
                continue
            obj = data[field]
            if instance and field in ('cancha', 'cancha_deporte') and getattr(instance, field + '_id') != obj.pk:
                raise ValidationError('No puedes cambiar el propietario de este registro.')
            branch = obj if field == 'sucursal' else (obj.sucursal if field == 'cancha' else obj.cancha.sucursal)
            if not is_global(self.request.user) and branch.pk != self.request.user.sucursal_id:
                raise PermissionDenied('No puedes guardar datos en otra sucursal.')
            if not branch.estado:
                raise ValidationError('La sucursal esta inactiva.')

    def refresh_publication(self):
        qs = Cancha.objects.filter(publicada=True).select_related('sucursal')
        if not is_global(self.request.user):
            qs = qs.filter(sucursal_id=self.request.user.sucursal_id)
        for court in qs:
            if publication_errors(court):
                Cancha.objects.filter(pk=court.pk).update(publicada=False)

    def save_record(self, serializer):
        self.validate_scope(serializer)
        if serializer.instance and serializer.validated_data.get(self.state_field) is False:
            if getattr(serializer.instance, self.state_field) and self.resource + '.desactivar' not in effective_permissions(self.request.user):
                raise PermissionDenied('No tienes permiso para desactivar este registro.')
        obj = serializer.save()
        if isinstance(obj, Cancha) and obj.publicada:
            errors = publication_errors(obj)
            if errors:
                raise ValidationError({'publicada': errors})
        self.refresh_publication()
        obj.refresh_from_db()
        return obj

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self.lock_scope()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.save_record(serializer)
        return Response(serializer.data, status=201)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self.lock_scope()
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=kwargs.pop('partial', False))
        serializer.is_valid(raise_exception=True)
        self.save_record(serializer)
        return Response(serializer.data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self.lock_scope()
        obj = self.get_object()
        setattr(obj, self.state_field, False)
        obj.save(update_fields=[self.state_field, 'updated_at'])
        self.refresh_publication()
        return Response(self.get_serializer(obj).data)


class NegocioViewSet(CatalogViewSet):
    http_method_names = ['get', 'put', 'patch', 'head', 'options']
    queryset = Negocio.objects.all()
    serializer_class = NegocioSerializer
    resource = 'negocio'
    branch_lookup = None
    global_only = True
    state_field = 'publicado'


class SucursalViewSet(CatalogViewSet):
    queryset = Sucursal.objects.select_related('negocio').all()
    serializer_class = SucursalCatalogoSerializer
    resource = 'sucursales'
    branch_lookup = 'pk'
    state_field = 'estado'

    def get_permissions(self):
        self.global_only = self.action not in ('list', 'retrieve')
        return super().get_permissions()


class DeporteViewSet(CatalogViewSet):
    queryset = Deporte.objects.all()
    serializer_class = DeporteSerializer
    resource = 'deportes'
    branch_lookup = None
    state_field = 'activo'

    def get_permissions(self):
        self.global_only = self.action not in ('list', 'retrieve')
        return super().get_permissions()


class CanchaViewSet(CatalogViewSet):
    queryset = Cancha.objects.select_related('sucursal').all()
    serializer_class = CanchaSerializer


class CanchaDeporteViewSet(CatalogViewSet):
    queryset = CanchaDeporte.objects.select_related('cancha__sucursal', 'deporte').all()
    serializer_class = CanchaDeporteSerializer
    branch_lookup = 'cancha__sucursal_id'
    state_field = 'activo'


class FotoViewSet(CatalogViewSet):
    queryset = FotoCancha.objects.select_related('cancha__sucursal').all()
    serializer_class = FotoSerializer
    branch_lookup = 'cancha__sucursal_id'
    state_field = 'publicada'


class HorarioViewSet(CatalogViewSet):
    queryset = HorarioCancha.objects.select_related('cancha__sucursal').all()
    serializer_class = HorarioSerializer
    branch_lookup = 'cancha__sucursal_id'
    resource = 'horarios'
    state_field = 'activo'


class TarifaViewSet(CatalogViewSet):
    queryset = TarifaCancha.objects.select_related('cancha_deporte__cancha__sucursal').all()
    serializer_class = TarifaSerializer
    branch_lookup = 'cancha_deporte__cancha__sucursal_id'
    resource = 'tarifas'

    def get_queryset(self):
        qs = super().get_queryset()
        court = self.request.query_params.get('cancha')
        if court:
            if not court.isdigit():
                raise ValidationError('Cancha invalida.')
            qs = qs.filter(cancha_deporte__cancha_id=int(court))
        return qs


class PoliticaViewSet(CatalogViewSet):
    queryset = PoliticaReserva.objects.select_related('sucursal').filter(activa=True)
    serializer_class = PoliticaSerializer
    resource = 'politicas'
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def save_record(self, serializer):
        self.validate_scope(serializer)
        branch = serializer.validated_data['sucursal']
        if serializer.instance:
            if branch.pk != serializer.instance.sucursal_id:
                raise ValidationError('No puedes cambiar la sucursal de esta configuracion.')
            return serializer.save()
        if PoliticaReserva.objects.filter(sucursal=branch, activa=True).exists():
            raise ValidationError('Esta sucursal ya tiene una configuracion. Edita la existente.')
        version = (PoliticaReserva.objects.filter(sucursal=branch).aggregate(n=Max('version'))['n'] or 0) + 1
        return serializer.save(version=version, activa=True)
