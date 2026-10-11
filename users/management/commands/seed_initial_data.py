from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Alias para la poblacion operativa inicial sin reservas ni cajas.'

    def add_arguments(self, parser):
        parser.add_argument('--credentials-file')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        call_command('populate_business', credentials_file=options['credentials_file'],
                     dry_run=options['dry_run'], stdout=self.stdout)
