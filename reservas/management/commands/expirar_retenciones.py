from django.core.management.base import BaseCommand
from django.utils import timezone
from reservas.models import Reserva
from reservas.services import expire


class Command(BaseCommand):
    help = 'Registra el vencimiento de las retenciones; no modifica pagos.'

    def handle(self, *args, **options):
        ids = list(Reserva.objects.filter(estado='PENDIENTE', retencion_hasta__lte=timezone.now()).values_list('pk', flat=True))
        count = sum(expire(pk) for pk in ids)
        self.stdout.write(f'Retenciones vencidas registradas: {count}')
