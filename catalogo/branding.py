from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Negocio


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def branding(request):
    # This installation represents one business; never expose private catalog fields.
    name = Negocio.objects.order_by('pk').values_list('nombre_comercial', flat=True).first()
    response = Response({'nombre_comercial': name or 'Soccer Real Madrid'})
    response['Cache-Control'] = 'no-store'
    return response
