# Soccer Real Madrid: instalacion del backend

El sistema tiene dos repositorios: [backend](https://github.com/rubendariotoconasromero-collab/canchitas_backend)
y [frontend](https://github.com/rubendariotoconasromero-collab/canchitas_frontend).
Usa versiones publicadas para la misma entrega. Esta guia cubre Windows con MySQL
o Laragon; para el frontend consulta su `README.md`.

## Requisitos

- Python 3.12 o superior, con el lanzador `py` de Windows.
- MySQL iniciado, con una cuenta autorizada para crear y utilizar la base.
- Git y acceso a los repositorios.
- Node 20.19 o superior en la rama 20, 22.12 o superior en la rama 22, o 24+;
  npm debe estar disponible para el frontend.

La instalacion del controlador `mysqlclient` puede requerir herramientas de
compilacion de C++ si no hay un paquete precompilado para tu version de Python.
Ante un error de instalacion, verifica la version de Python y el mensaje completo.

## 1. Obtener los dos proyectos

En PowerShell o Git Bash, dentro de una carpeta de trabajo:

```bash
git clone https://github.com/rubendariotoconasromero-collab/canchitas_backend.git backend
git clone https://github.com/rubendariotoconasromero-collab/canchitas_frontend.git frontend
```

Comprueba que las versiones correspondan a la misma entrega. Los archivos
`compatibilidad.json` deben indicar el mismo identificador en ambos repositorios;
los comprobadores lo comparan si las carpetas son vecinas. Esta entrega es
`srm-2026-10-instalacion-1` y requiere, entre otras, `caja.0004` y `reservas.0006`.
El identificador debe actualizarse en ambos repositorios para futuras entregas
coordinadas; no sustituye las pruebas. Si el codigo se
descargo en ZIP, verifica que incluya `catalogo/management/commands/populate_business.py`
y las carpetas de migraciones. La configuracion y las bases de datos no se copian
al clonar.

## 2. Preparar Python y dependencias

Entra a `backend`. Comprueba que `py -3 --version` muestre 3.12 o superior y crea
el entorno virtual. Ejecuta los comandos con su Python directamente; asi no
dependen de la activacion ni de la politica de PowerShell.

PowerShell:

```powershell
cd backend
py -3 --version
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\prepare.ps1
```

El preparador crea `.venv` si falta, instala las dependencias y crea `.env` solo
si no existe. Para elegir otro Python: `-Python C:\ruta\python.exe` al final del
comando. Si prefieres hacerlo manualmente, usa `py -3 -m venv .venv` y ejecuta
`pip` e `init_env.py` con `.\.venv\Scripts\python.exe`.

Git Bash:

```bash
cd backend
py -3 --version
py -3 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe scripts/init_env.py
```

`init_env.py` crea `.env` una sola vez con una clave de desarrollo unica. Si el
archivo ya existe, lo conserva. Edita `DB_NAME`, `DB_USER`, `DB_PASSWORD`,
`DB_HOST` y `DB_PORT` para tu MySQL. `.env` es privado; no lo compartas ni lo
agregues a Git. La version anterior de este proyecto incluyo una clave en Git:
antes de produccion debe reemplazarse de forma coordinada; conservar y proteger
la clave anterior al restaurar una instalacion hasta planificar esa sustitucion.

Para revisar Python, dependencias y comandos antes de configurar la base:

```powershell
.\.venv\Scripts\python.exe scripts\check_install.py --skip-db
```

En Git Bash usa `./.venv/Scripts/python.exe scripts/check_install.py --skip-db`.
Si el comprobador dice que falta Django, instala dependencias con ese mismo Python.

## 3. Crear la base y migrar

Crea una base vacia con el nombre configurado en `.env`, por ejemplo `canchitas_db`.
Con el cliente MySQL disponible en la terminal de Laragon:

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS canchitas_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

MySQL pedira la contrasena; si tu instalacion local no tiene una, presiona Enter.
Usa la cuenta y los permisos adecuados a tu equipo. No uses este paso para
sobrescribir una base existente.

PowerShell:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe scripts\check_install.py
```

Git Bash:

```bash
./.venv/Scripts/python.exe manage.py migrate
./.venv/Scripts/python.exe scripts/check_install.py
```

Si la conexion falla, comprueba que MySQL este iniciado y que la base, usuario,
contrasena y puerto coincidan con `.env`. Si hay migraciones pendientes, ejecuta
`migrate` con el mismo Python del entorno virtual.

## 4. Poblacion inicial para una base nueva

PowerShell:

```powershell
.\.venv\Scripts\python.exe manage.py seed_initial_data --dry-run
.\.venv\Scripts\python.exe manage.py seed_initial_data --credentials-file "$env:USERPROFILE\credenciales-soccer.json"
```

Git Bash:

```bash
./.venv/Scripts/python.exe manage.py seed_initial_data --dry-run
./.venv/Scripts/python.exe manage.py seed_initial_data --credentials-file "$HOME/credenciales-soccer.json"
```

El archivo de credenciales debe ser nuevo; si ya existe, elige otro nombre.
Contiene las claves generadas para las cuentas creadas y debe mantenerse privado.
Repetir la poblacion conserva las claves existentes; no genera una clave nueva
para una cuenta que ya existia. El alias `seed_initial_data` usa `populate_business`.

La carga crea dos canchas, horarios, tarifas, deportes, politicas y usuarios. No
crea clientes, reservas, pagos, cajas ni turnos. Las canchas quedan sin publicar.
Si el comando no existe, comprueba que hayas clonado esta version completa y que
la terminal este en `backend`; no ejecutes otro comando de una copia antigua.
La carga no restaura una instalacion existente y no requiere `--reset`.

## 5. Iniciar el backend

PowerShell: `.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000`

Git Bash: `./.venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000`

Mantener esta terminal abierta. En otra terminal, seguir la guia del frontend.
La API de desarrollo responde en `http://127.0.0.1:8000/api/health/`.

## Imagenes y datos existentes

Una base nueva no contiene las relaciones con las fotografias aunque algunos archivos
historicos esten presentes en `media/`. Para publicar, configurar y publicar el
negocio, registrar las fotos de cada cancha y cumplir las validaciones de publicacion.
El contenido web necesita sus propias imagenes y tres slides publicados. El backend
ignora los nuevos archivos subidos; los archivos historicos ya versionados se
conservan mientras se decide su destino. No son una copia de seguridad de la base.

Para trasladar una instalacion existente, usar un respaldo confiable de MySQL y
de `media` tomado en la misma ventana de mantenimiento. Restaurar primero en un
entorno aislado, conservar rutas y revisar las credenciales y la clave privada.
No ejecutar la poblacion inicial ni un reset sobre datos existentes sin un plan
de migracion y un respaldo comprobado. Los comandos `backup_business` y
`verify_backup` ayudan a crear y verificar respaldos; `manage.py help` muestra
sus opciones. Verificar tambien que el servidor entregue `/media`.

## Produccion y comprobaciones

`runserver` y el proxy de Angular son para desarrollo. Produccion requiere servidor
HTTPS y de aplicaciones, `config.settings_production`, Redis compartido, secretos
propios, `collectstatic`, y un proxy que sirva `/api`, `/media` y la aplicacion
Angular con soporte para rutas directas. `python manage.py check --deploy` ayuda
a revisar la configuracion. No compartir `.env` ni los archivos de credenciales.
Consulta [despliegue y restauracion](docs/despliegue.md) para un ejemplo de proxy
y pasos para restaurar una base y sus archivos en un destino aislado.

Para pruebas sin navegador, ejecuta `python -m unittest scripts.test_install_scripts`,
`manage.py check`, `manage.py makemigrations
--check --dry-run` y las pruebas Django sobre una base de pruebas aislada. El
frontend incluye comprobaciones `npm run test:*` y `npm run build`.
