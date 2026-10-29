import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def mysql_command(program, database):
    executable = shutil.which(program)
    if not executable:
        raise CommandError(f'No se encontro {program} en PATH.')
    config = settings.DATABASES['default']
    args = [executable, '--host=' + str(config.get('HOST') or '127.0.0.1'),
            '--port=' + str(config.get('PORT') or '3306'), '--user=' + str(config['USER'])]
    env = os.environ.copy()
    env['MYSQL_PWD'] = str(config.get('PASSWORD') or '')
    return args, env


class Command(BaseCommand):
    help = 'Crea un respaldo de MySQL y media en una carpeta nueva durante mantenimiento.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)
        parser.add_argument('--maintenance-confirmed', action='store_true')

    def handle(self, *args, **options):
        if not options['maintenance_confirmed']:
            raise CommandError('Deten las escrituras de usuarios y confirma con --maintenance-confirmed.')
        if connection.vendor != 'mysql':
            raise CommandError('Este respaldo requiere MySQL.')
        destination = Path(options['output']).resolve()
        media = Path(settings.MEDIA_ROOT).resolve()
        if not media.is_dir():
            raise CommandError('La carpeta de archivos media no existe o no esta disponible.')
        if destination == media or media in destination.parents:
            raise CommandError('La carpeta de respaldo no puede estar dentro de media.')
        if destination.exists() or not destination.parent.is_dir():
            raise CommandError('La carpeta de respaldo debe ser nueva y su directorio padre debe existir.')
        command, env = mysql_command('mysqldump', settings.DATABASES['default']['NAME'])
        destination.mkdir()
        sql = destination / 'base.sql.gz'
        archive = destination / 'media.zip'
        database = settings.DATABASES['default']['NAME']
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s', [database])
            tables = cursor.fetchone()[0]
        try:
            with tempfile.TemporaryFile() as errors, gzip.open(sql, 'wb') as output:
                process = subprocess.Popen(command + ['--single-transaction', '--quick', '--routines', '--triggers',
                    '--no-tablespaces', '--', database], stdout=subprocess.PIPE, stderr=errors, env=env)
                shutil.copyfileobj(process.stdout, output)
                process.stdout.close()
                result = process.wait()
                if result:
                    errors.seek(0)
                    raise CommandError('Fallo mysqldump: ' + errors.read().decode(errors='replace')[:1000])
            count = 0
            with ZipFile(archive, 'w', ZIP_DEFLATED, allowZip64=True) as target:
                if media.exists():
                    for item in media.rglob('*'):
                        if item.is_symlink():
                            raise CommandError('Media contiene un enlace simbolico; revisalo antes de respaldar.')
                        if item.is_file():
                            target.write(item, item.relative_to(media).as_posix())
                            count += 1
            manifest = {'version': 1, 'fecha': timezone.now().isoformat(), 'tablas': tables,
                        'archivos_media': count, 'base_sha256': digest(sql), 'media_sha256': digest(archive)}
            (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        except Exception:
            self.stderr.write(f'Respaldo incompleto en {destination}; no usarlo para restaurar.')
            raise
        self.stdout.write(self.style.SUCCESS(f'Respaldo creado en {destination}. Tablas: {tables}; archivos: {count}.'))
