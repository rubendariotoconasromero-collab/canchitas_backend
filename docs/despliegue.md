# Despliegue y restauracion

Esta guia describe una instalacion publica; adaptar rutas, dominio y servicios al
servidor elegido. Antes de operar, probar el procedimiento en un entorno aislado.
La instalacion local de Windows y Laragon esta en [README.md](../README.md).

## Preparacion

1. Publicar y obtener versiones compatibles de ambos repositorios. Comprobar que
   sus archivos `compatibilidad.json` coincidan.
2. Crear una base MySQL y una cuenta con permisos adecuados. Mantener `.env`,
   credenciales y respaldos fuera de Git y de las carpetas publicas.
3. Instalar las dependencias del backend, establecer una clave `SECRET_KEY` unica,
   `ALLOWED_HOSTS` con el dominio real, `DJANGO_SETTINGS_MODULE=config.settings_production`
   y `REDIS_CACHE_URL` para un Redis compartido y protegido. El backend no instala
   ni configura el servicio Redis por si solo.
4. Ejecutar migraciones, `collectstatic` y `check --deploy` con el Python del
   entorno virtual. Compilar el frontend con `npm ci` y `npm run build`.
5. Ejecutar Django mediante un servidor WSGI o ASGI apropiado al sistema operativo,
   escuchando solo en una interfaz privada. `runserver` no es para produccion.
6. Configurar el servidor HTTPS y comprobar los servicios, respaldos y permisos.

Para un unico dominio que sirva frontend y API, los origenes CORS pueden quedar
vacios. Si hay origenes adicionales, configurarlos expresamente con HTTPS. El
directorio `MEDIA_ROOT` debe conservar los archivos cargados y no debe sustituirse
al actualizar el codigo. Las rutas publicas de media deben contener solo archivos
autorizados para publicarse.

## Ejemplo de proxy Nginx

Este ejemplo supone Linux, Nginx, un backend privado en `127.0.0.1:8000`, un
frontend Angular compilado y certificados ya instalados. Sustituir dominio, rutas
y certificados antes de usarlo. Otros servidores, incluido Apache o IIS, requieren
una configuracion equivalente; no hay que copiar estas rutas literalmente.

```nginx
server {
    listen 443 ssl;
    server_name ejemplo.com;
    ssl_certificate /ruta/privada/fullchain.pem;
    ssl_certificate_key /ruta/privada/privkey.pem;

    root /srv/soccer/frontend/dist/frontend/browser;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location /static/ { alias /srv/soccer/backend/staticfiles/; }
    location /media/ { alias /srv/soccer/media/; }
    location / { try_files $uri $uri/ /index.html; }
}
```

El proxy debe reemplazar los encabezados reenviados y el backend debe ser
inaccesible directamente desde internet. Solo entonces configurar
`TRUST_HTTPS_PROXY=True`, `LOGIN_TRUST_PROXY=True` y `LOGIN_PROXY_COUNT=1`.
Ese numero debe corresponder a la infraestructura real. Configurar la redireccion
HTTP a HTTPS y HSTS despues de verificar todos los dominios. Comprobar que
`MEDIA_ROOT`, `STATIC_ROOT` y la ruta compilada coincidan con los alias del proxy.
La configuracion de ejemplo del frontend solo funciona con `ng serve`; no se
incluye en el sitio compilado.

## Restaurar una copia existente

1. Detener las escrituras del sistema origen durante el respaldo. Crear una copia
   con `backup_business` y verificarla con `verify_backup`. Trasladar la carpeta
   completa mediante un medio privado; conservar tambien la configuracion secreta
   necesaria para el destino por un canal protegido.
2. Preparar en el destino una base **nueva y vacia**, un directorio nuevo para
   media y la misma version del codigo. No utilizar `seed_initial_data` ni `--reset`
   para restaurar una base existente.
3. En una carpeta privada, descomprimir `base.sql.gz` a un archivo SQL nuevo. Por
   ejemplo, con Python en Windows, sustituyendo las rutas:

   ```powershell
   python -c "import gzip,shutil; src=gzip.open(r'C:\ruta-privada\respaldo\base.sql.gz','rb'); dst=open(r'C:\ruta-privada\base-restauracion.sql','xb'); shutil.copyfileobj(src,dst); dst.close(); src.close()"
   ```

4. Abrir el cliente `mysql -u USUARIO -p NOMBRE_BASE_NUEVA` y, dentro del cliente,
   ejecutar `source C:/ruta-privada/base-restauracion.sql;`. Utilizar un usuario con
   permisos solo sobre el destino de restauracion.
5. Extraer `media.zip` en el directorio nuevo para media conservando sus rutas,
   por ejemplo con `Expand-Archive -LiteralPath C:\ruta-privada\respaldo\media.zip
   -DestinationPath C:\ruta-privada\media-restaurada` en PowerShell. Configurar
   `MEDIA_ROOT` para esa carpeta.
6. Configurar la conexion y la clave privada correspondiente; comprobar migraciones,
   registros, fotografias y acceso en el entorno aislado antes de ponerlo en servicio.
   Coordinar cualquier cambio de clave con el responsable: puede afectar sesiones
   y mecanismos de seguimiento. Gestionar de forma segura el SQL descomprimido,
   ya que contiene datos privados.

`verify_backup` restaura en una base temporal y comprueba hashes, cantidad de
tablas y archivos; no comprueba por si solo todos los datos de negocio. Realizar
una revision operativa y conservar evidencia del resultado antes de una migracion
real.
