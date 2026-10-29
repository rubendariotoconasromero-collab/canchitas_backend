import gzip
import json
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zipfile import ZipFile, BadZipFile

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from .backup_business import digest, mysql_command


class Command(BaseCommand):
    help = 'Restaura un respaldo en una base temporal nueva y valida las imagenes.'

    def add_arguments(self, parser):
        parser.add_argument('--backup', required=True)

    def handle(self, *args, **options):
        folder = Path(options['backup']).resolve()
        try:
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            sql, archive = folder / 'base.sql.gz', folder / 'media.zip'
            if manifest['version'] != 1 or digest(sql) != manifest['base_sha256'] or digest(archive) != manifest['media_sha256']:
                raise CommandError('El respaldo no coincide con su manifiesto.')
            with ZipFile(archive) as source:
                files = [info for info in source.infolist() if not info.is_dir()]
                if len(files) != manifest['archivos_media'] or source.testzip():
                    raise CommandError('El archivo de imagenes esta incompleto o danado.')
                for info in source.infolist():
                    path = PurePosixPath(info.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename:
                        raise CommandError('El archivo de imagenes contiene una ruta invalida.')
                with tempfile.TemporaryDirectory(prefix='srm_media_') as target:
                    source.extractall(target)
                    if sum(1 for item in Path(target).rglob('*') if item.is_file()) != len(files):
                        raise CommandError('No se pudieron restaurar todas las imagenes.')
        except (OSError, KeyError, ValueError, BadZipFile) as exc:
            raise CommandError(f'No se pudo leer el respaldo: {exc}') from exc

        command, env = mysql_command('mysql', '')
        database = 'test_srm_restore_' + uuid4().hex[:16]
        created = False
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
                created = True
            with tempfile.TemporaryFile() as errors, gzip.open(sql, 'rb') as source:
                process = subprocess.Popen(command + ['--database=' + database], stdin=subprocess.PIPE, stderr=errors, env=env)
                try:
                    try:
                        shutil.copyfileobj(source, process.stdin)
                    except BrokenPipeError:
                        pass
                    except Exception:
                        process.kill()
                        process.wait()
                        raise
                finally:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
                result = process.wait()
                if result:
                    errors.seek(0)
                    raise CommandError('Fallo la restauracion: ' + errors.read().decode(errors='replace')[:1000])
            with connection.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s', [database])
                restored = cursor.fetchone()[0]
            if restored != manifest['tablas']:
                raise CommandError(f'Se restauraron {restored} tablas de {manifest["tablas"]}.')
            self.stdout.write(self.style.SUCCESS(f'Restauracion temporal verificada: {restored} tablas y {len(files)} archivos.'))
        finally:
            if created:
                with connection.cursor() as cursor:
                    cursor.execute(f'DROP DATABASE `{database}`')
