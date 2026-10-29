"""Create a local development configuration once, with a unique secret."""
from pathlib import Path
import secrets
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    target = root / '.env'
    example = root / '.env.example'
    if not example.is_file():
        print('No se encontro .env.example. Comprueba que el backend este completo.', file=sys.stderr)
        return 1
    lines = example.read_text(encoding='utf-8').splitlines()
    if sum(line.startswith('SECRET_KEY=') for line in lines) != 1:
        print('La plantilla .env.example no contiene una clave valida.', file=sys.stderr)
        return 1
    secret = secrets.token_urlsafe(50)
    content = '\n'.join('SECRET_KEY=' + secret if line.startswith('SECRET_KEY=') else line for line in lines) + '\n'
    try:
        with target.open('x', encoding='utf-8', newline='\n') as output:
            output.write(content)
    except FileExistsError:
        print('Ya existe backend/.env. Se conserva sin cambios; revisa sus datos de MySQL.')
        return 0
    print('Configuracion local creada en backend/.env con una clave nueva.')
    print('Revisa DB_NAME, DB_USER, DB_PASSWORD, DB_HOST y DB_PORT antes de migrar.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
