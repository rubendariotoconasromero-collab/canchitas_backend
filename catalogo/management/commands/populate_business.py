from collections import Counter
from datetime import date, time
from decimal import Decimal
import json
import secrets
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from catalogo.models import (Negocio, Cancha, Deporte, CanchaDeporte, FotoCancha,
                            HorarioCancha, TarifaCancha, PoliticaReserva, RespaldoAnticipo)
from reservas.models import (Cliente, Reserva, DetalleReserva, HistorialReserva, BloqueoCancha, PagoReserva, SolicitudReserva)
from caja.models import Caja, SesionCaja, MovimientoCaja, ReciboPago, MedioPago
from users.models import Sucursal, User, Rol, Permiso, RolPermiso, RolPermisoUsuario
from users.access import PERMISSIONS

# Initial estimates, not certified measurements or confirmed business prices.
COURTS = [
    ('SRM-01', 'La Cantera', 24, 38, 10, 90, ['Futbol 5', 'Futbol infantil']),
    ('SRM-02', 'Bernabeu', 40, 60, 14, 130, ['Futbol 7', 'Futbol 6']),
]
ROLES = {
    'Administrador': sorted(PERMISSIONS),
    'Recepcion': ['dashboard.ver', 'canchas.ver', 'deportes.ver', 'horarios.ver',
                     'tarifas.ver', 'politicas.ver', 'sucursales.ver',
                     'clientes.ver', 'clientes.crear', 'clientes.editar',
                     'reservas.ver', 'reservas.crear', 'reservas.reprogramar',
                     'reservas.cancelar', 'reservas.iniciar', 'reservas.finalizar',
                     'reservas.alquilar_inmediato', 'bloqueos.ver', 'bloqueos.crear',
                     'bloqueos.liberar', 'caja.ver', 'caja.cobrar'],
    'Cajero': ['dashboard.ver', 'canchas.ver', 'clientes.ver', 'reservas.ver',
               'caja.ver', 'caja.abrir', 'caja.cobrar', 'caja.movimientos', 'caja.cerrar'],
}
ROLES['Recepcion'] += ['solicitudes.ver', 'solicitudes.aceptar', 'solicitudes.rechazar']
ROLES['Recepcion'] += ['reportes.ver', 'reportes.reservas', 'reportes.cobros', 'reportes.ocupacion', 'avisos.ver']
ROLES['Cajero'] += ['reportes.ver', 'reportes.cobros', 'reportes.cierres', 'avisos.ver']
ACCOUNTS = [
    ('recepcion@gmail.com', 'Recepcion', 'Recepcion'),
    ('cajero1@gmail.com', 'Cajero 1', 'Cajero'),
    ('cajero2@gmail.com', 'Cajero 2', 'Cajero'),
]


class Command(BaseCommand):
    help = 'Prepara dos canchas, accesos y configuracion operativa sin operaciones ni cajas.'

    def add_arguments(self, parser):
        parser.add_argument('--branch-id', type=int)
        parser.add_argument('--actor-id', type=int)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--reset-access', action='store_true', help='Restablece los cuatro accesos iniciales con claves nuevas (solo desarrollo).')
        parser.add_argument('--reset', action='store_true', help='BORRA operaciones, canchas y usuarios no superadministradores.')
        parser.add_argument('--backup-file', help='Respaldo dumpdata previo obligatorio para --reset.')
        parser.add_argument('--credentials-file', help='Archivo privado NUEVO para claves generadas; no subir a Git.')

    def handle(self, *args, **options):
        self.counts = Counter()
        credentials = []
        credentials_file = Path(options['credentials_file']) if options['credentials_file'] else None
        if credentials_file and (credentials_file.exists() or not credentials_file.parent.is_dir()):
            raise CommandError('El archivo de credenciales debe ser nuevo y su directorio debe existir.')
        if options['reset'] and not options['dry_run']:
            backup = Path(options['backup_file'] or '')
            if not backup.is_file() or backup.stat().st_size == 0:
                raise CommandError('--reset requiere --backup-file con un respaldo previo no vacio.')
        with transaction.atomic():
            # Use the same branch-first locking order as reservations and cash services.
            list(Sucursal.objects.select_for_update().order_by('pk'))
            if options['reset']:
                self.reset_data()
            elif Cancha.objects.exclude(codigo__in=[c[0] for c in COURTS]).exists():
                raise CommandError('Hay otras canchas. Usa --reset con respaldo para dejar exactamente dos.')
            business, created = Negocio.objects.get_or_create(singleton=True, defaults={
                'nombre_comercial': 'Soccer Real Madrid', 'moneda': 'BOB',
                'descripcion_publica': 'Alquiler de canchas de cesped sintetico para encuentros deportivos.'})
            self.record('negocios', created)
            from catalogo.web_content import initialize_web
            initialize_web(business)
            if business.nombre_comercial == 'Canchitas':
                business.nombre_comercial = 'Soccer Real Madrid'
                business.save(update_fields=['nombre_comercial', 'updated_at'])
            if options['branch_id'] is not None:
                branch = Sucursal.objects.filter(pk=options['branch_id']).first()
                if not branch:
                    raise CommandError('La sucursal indicada no existe.')
            else:
                branch, created = Sucursal.objects.get_or_create(nombre='Sucursal Central', defaults={
                    'negocio': business, 'slug_publico': 'sucursal-central', 'zona_horaria': 'America/La_Paz'})
                self.record('sucursales', created)
            if not branch.estado:
                raise CommandError('La sucursal esta inactiva.')
            if not branch.negocio_id:
                branch.negocio = business
                branch.save(update_fields=['negocio', 'updated_at'])
            if Cancha.objects.exclude(sucursal=branch).exists():
                raise CommandError('Hay canchas en otra sucursal; se requiere limpieza con respaldo.')
            self.create_access(branch, options['actor_id'], credentials, options['reset_access'])
            policy = PoliticaReserva.objects.filter(sucursal=branch, activa=True).first()
            if not policy:
                policy = PoliticaReserva.objects.create(sucursal=branch, version=1, minutos_retencion=15,
                    horas_limite_cancelacion=5, condiciones='Pago total para confirmar. Cancelaciones sin reembolso.')
                self.record('politicas', True)
            elif not policy.minutos_retencion:
                policy.minutos_retencion = 15
                policy.save(update_fields=['minutos_retencion', 'updated_at'])
            for code, name, width, length, capacity, price, sports in COURTS:
                court, created = Cancha.objects.get_or_create(sucursal=branch, codigo=code, defaults={
                    'nombre': name, 'descripcion_publica': 'Cancha de cesped sintetico con iluminacion nocturna.',
                    'superficie': 'Cesped sintetico', 'ancho_metros': width, 'largo_metros': length,
                    'capacidad_personas': capacity, 'iluminacion': True, 'duracion_minima_minutos': 60,
                    'activa': True, 'publicada': False})
                self.record('canchas', created)
                if not court.horarios.exists():
                    for weekday in range(1, 8):
                        HorarioCancha.objects.create(cancha=court, dia_semana=weekday, apertura=time(8), cierre=time(23))
                        self.record('horarios', True)
                for sport_name in sports:
                    sport = Deporte.objects.filter(nombre__iexact=sport_name).first()
                    if not sport:
                        sport = Deporte.objects.create(nombre=sport_name)
                        self.record('deportes', True)
                    link, created = CanchaDeporte.objects.get_or_create(cancha=court, deporte=sport)
                    self.record('asociaciones deportivas', created)
                    if link.tarifas.exists():
                        continue
                    for weekday in range(1, 8):
                        for label, opening, closing, amount in [('Diurna', 8, 18, price), ('Nocturna', 18, 23, price + 30)]:
                            TarifaCancha.objects.create(cancha_deporte=link, nombre=label, dia_semana=weekday,
                                hora_desde=time(opening), hora_hasta=time(closing), vigente_desde=date(2026, 1, 1),
                                precio_hora=Decimal(amount))
                            self.record('tarifas', True)
            for code, name, cash in [('EFECTIVO', 'Efectivo', True), ('TRANSFERENCIA_QR', 'Transferencia QR', False)]:
                _, created = MedioPago.objects.get_or_create(codigo=code, defaults={'nombre': name, 'efectivo': cash})
                self.record('medios de pago (catalogo)', created)
            if options['dry_run']:
                transaction.set_rollback(True)
            elif credentials_file:
                with credentials_file.open('x', encoding='utf-8') as stream:
                    json.dump(credentials, stream, indent=2, ensure_ascii=True)
        self.stdout.write('Simulacion sin guardar:' if options['dry_run'] else 'Poblacion operativa preparada:')
        for label, count in sorted(self.counts.items()):
            self.stdout.write(f'  {label}: {count}')
        if not options['dry_run'] and credentials and not credentials_file:
            self.stdout.write('Credenciales generadas (privadas; guardalas ahora y cambialas antes de produccion):')
            for account in credentials:
                self.stdout.write(f"  {account['correo']}: {account['password']}")
        self.stdout.write('No se crean clientes, reservas, bloqueos, cobros, pagos, cajas ni turnos.')

    def create_access(self, branch, actor_id, credentials, reset_access=False):
        permissions = {}
        for code in sorted(PERMISSIONS):
            permissions[code], _ = Permiso.objects.get_or_create(nombre=code, defaults={'descripcion': code})
        links = {}
        for name, codes in ROLES.items():
            role, created = Rol.objects.get_or_create(nombre=name, defaults={'descripcion': f'Funciones de {name.lower()}.'})
            self.record('roles', created)
            links[name] = [RolPermiso.objects.get_or_create(rol=role, permiso=permissions[code])[0] for code in codes]
        admins = User.objects.filter(is_superuser=True, is_active=True, estado=True).order_by('pk')
        admin = admins.filter(pk=actor_id).first() if actor_id else admins.first()
        if actor_id and not admin:
            raise CommandError('El administrador indicado no existe o esta inactivo.')
        if not admin:
            admin = self.create_user('admin@gmail.com', 'Administrador', branch, credentials, superuser=True)
        elif reset_access:
            self.reset_user_access(admin, 'admin@gmail.com', credentials)
        for link in links['Administrador']:
            RolPermisoUsuario.objects.get_or_create(user=admin, rol_permiso=link)
        for email, name, role in ACCOUNTS:
            legacy = email.replace('@gmail.com', '@soccerrealmadrid.com')
            user = User.objects.filter(correo=email).first() or User.objects.filter(correo=legacy).first()
            if not user:
                user = self.create_user(email, name, branch, credentials)
                for link in links[role]:
                    RolPermisoUsuario.objects.create(user=user, rol_permiso=link)
            elif reset_access:
                self.reset_user_access(user, email, credentials)
                for link in links[role]:
                    RolPermisoUsuario.objects.get_or_create(user=user, rol_permiso=link)

    def reset_user_access(self, user, email, credentials):
        if User.objects.exclude(pk=user.pk).filter(correo=email).exists() or User.objects.exclude(pk=user.pk).filter(username=email).exists():
            raise CommandError(f'El correo {email} ya pertenece a otro usuario.')
        password = secrets.token_urlsafe(12)
        user.correo = email
        user.username = email
        user.set_password(password)
        user.save(update_fields=['correo', 'username', 'password'])
        credentials.append({'correo': email, 'password': password, 'nombre': user.nombre})
        self.counts['accesos actualizados'] += 1

    def create_user(self, email, name, branch, credentials, superuser=False):
        password = secrets.token_urlsafe(12)
        user = User.objects.create_user(email, password, nombre=name, sucursal=branch,
            estado=True, is_active=True, is_staff=superuser, is_superuser=superuser)
        credentials.append({'correo': email, 'password': password, 'nombre': name})
        self.record('usuarios', True)
        return user

    def reset_data(self):
        from rest_framework.authtoken.models import Token
        from auditoria.models import EventoAuditoria
        Caja.objects.update(sesion_actual=None)
        for model in (EventoAuditoria, SolicitudReserva, ReciboPago, MovimientoCaja, SesionCaja, Caja, PagoReserva,
                      HistorialReserva, DetalleReserva, BloqueoCancha, Reserva, Cliente,
                      FotoCancha, TarifaCancha, HorarioCancha, CanchaDeporte, Cancha,
                      Deporte, PoliticaReserva, RespaldoAnticipo):
            count = model.objects.count()
            model.objects.all().delete()
            self.counts[f'eliminados {model.__name__}'] += count
        Token.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        Rol.objects.all().delete()

    def record(self, label, created):
        if created:
            self.counts[label] += 1
