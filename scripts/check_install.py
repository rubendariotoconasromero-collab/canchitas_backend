"""Check an installation without changing its database or showing credentials."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description='Comprueba dependencias, configuracion y migraciones.')
    parser.add_argument('--skip-db', action='store_true', help='Omite conexion a MySQL y migraciones.')
    options = parser.parse_args()
    errors = []
    if sys.version_info < (3, 12):
        errors.append('Python 3.12 o superior es necesario. Instala una version compatible y crea .venv de nuevo.')
    else:
        print(f'Python {sys.version_info.major}.{sys.version_info.minor}: correcto.')
    if sys.prefix == sys.base_prefix:
        print('Aviso: este Python no pertenece a un entorno virtual; usa .venv/Scripts/python.exe.')
    if not (ROOT / '.env').is_file():
        errors.append('Falta backend/.env. Ejecuta scripts/init_env.py y configura MySQL.')
    else:
        print('Archivo .env: presente.')
    try:
        release = json.loads((ROOT / 'compatibilidad.json').read_text(encoding='utf-8'))
        if not isinstance(release.get('entrega'), str) or not release['entrega'].strip():
            raise ValueError('Identificador de entrega invalido')
        for app, migration in release['migraciones_requeridas'].items():
            if not (ROOT / app / 'migrations' / (migration + '.py')).is_file():
                errors.append(f'Falta la migracion {app}.{migration}; revisa la version del backend.')
        frontend_dir = ROOT.parent / 'frontend'
        frontend = frontend_dir / 'compatibilidad.json'
        if frontend.is_file():
            other = json.loads(frontend.read_text(encoding='utf-8'))
            if other.get('entrega') != release['entrega']:
                errors.append('Backend y frontend tienen identificadores de entrega distintos.')
            else:
                print('Entrega compatible: ' + release['entrega'])
        elif frontend_dir.is_dir():
            errors.append('El frontend vecino no incluye compatibilidad.json; revisa su version.')
        else:
            print('Frontend vecino no encontrado; comprueba su identificador de entrega por separado.')
    except (OSError, ValueError, KeyError, TypeError):
        errors.append('Falta o es invalido compatibilidad.json; revisa la version completa del backend.')
    try:
        requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
    except OSError:
        requirements = []
        errors.append('Falta requirements.txt. Descarga la version completa del backend.')
    missing = []
    mismatched = []
    for line in requirements:
        item = line.strip()
        if not item or item.startswith('#'):
            continue
        name = re.split(r'[<=>!~\[]', item, maxsplit=1)[0].strip()
        try:
            installed = importlib.metadata.version(name)
            if '==' in item:
                expected = item.split('==', 1)[1].strip()
                if installed != expected:
                    mismatched.append(f'{name} ({installed}; requiere {expected})')
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        errors.append('Faltan dependencias: ' + ', '.join(missing) + '. Ejecuta python -m pip install -r requirements.txt con el Python de .venv.')
    if mismatched:
        errors.append('Versiones distintas a requirements.txt: ' + ', '.join(mismatched) + '. Ejecuta python -m pip install -r requirements.txt con el Python de .venv.')
    if errors:
        for error in errors:
            print('ERROR: ' + error, file=sys.stderr)
        return 1
    print('Dependencias requeridas: instaladas.')
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        import django
        django.setup()
        from django.core.management import get_commands
        if not {'populate_business', 'seed_initial_data'}.issubset(get_commands()):
            errors.append('Faltan comandos de poblacion; revisa la version completa del backend.')
        else:
            print('Comandos de poblacion: disponibles.')
        if options.skip_db:
            print('MySQL y migraciones: no comprobados (--skip-db).')
        else:
            from django.db import connection
            from django.db.migrations.executor import MigrationExecutor
            connection.ensure_connection()
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if pending:
                errors.append(f'Hay {len(pending)} migraciones pendientes. Ejecuta manage.py migrate.')
            else:
                print('MySQL y migraciones: correctos.')
            connection.close()
    except Exception as exc:
        cause = exc.__cause__ or exc
        code = cause.args[0] if cause.args and isinstance(cause.args[0], int) else None
        if code == 1049:
            errors.append('La base de datos configurada no existe. Creala en MySQL y revisa DB_NAME en .env.')
        elif code in (1044, 1045):
            errors.append('MySQL rechazo el acceso. Revisa DB_USER, DB_PASSWORD y los permisos de la base.')
        elif code in (2002, 2003, 2005, 2006):
            errors.append('No se pudo conectar con MySQL. Comprueba que este iniciado y revisa DB_HOST y DB_PORT.')
        else:
            errors.append('No se pudo comprobar Django o MySQL (' + type(exc).__name__ + '). Revisa .env, el servicio MySQL, la base y sus permisos.')
    for error in errors:
        print('ERROR: ' + error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
