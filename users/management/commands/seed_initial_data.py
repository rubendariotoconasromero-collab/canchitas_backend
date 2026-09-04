from django.core.management.base import BaseCommand

from users.models import Permiso, Rol, RolPermiso, RolPermisoUsuario, Sucursal, User


class Command(BaseCommand):
    help = 'Crea datos iniciales para el modulo de usuarios.'

    def handle(self, *args, **options):
        sucursal, _ = Sucursal.objects.get_or_create(
            nombre='Sucursal Central',
            defaults={'direccion': 'Oficina principal', 'telefono': ''},
        )

        permisos_data = [
            ('acceso_dashboard', 'Acceso al panel principal'),
            ('acceso_usuarios', 'Gestion de usuarios del sistema'),
            ('acceso_roles', 'Gestion de roles'),
            ('acceso_permisos', 'Gestion de permisos'),
            ('acceso_sucursales', 'Gestion de sucursales'),
            ('acceso_reportes', 'Acceso a reportes'),
        ]
        permisos = {}
        for nombre, descripcion in permisos_data:
            permiso, _ = Permiso.objects.get_or_create(
                nombre=nombre,
                defaults={'descripcion': descripcion, 'estado': True},
            )
            permisos[nombre] = permiso

        roles_config = {
            'Administrador': {
                'descripcion': 'Acceso total al sistema',
                'permisos': list(permisos.keys()),
            },
            'Operador': {
                'descripcion': 'Operacion diaria de usuarios y dashboard',
                'permisos': ['acceso_dashboard'],
            },
        }

        admin_rol_permiso_ids = []
        for nombre_rol, config in roles_config.items():
            rol, _ = Rol.objects.get_or_create(
                nombre=nombre_rol,
                defaults={'descripcion': config['descripcion'], 'estado': True},
            )
            for permiso_nombre in config['permisos']:
                rp, _ = RolPermiso.objects.get_or_create(rol=rol, permiso=permisos[permiso_nombre])
                if nombre_rol == 'Administrador':
                    admin_rol_permiso_ids.append(rp.id)

        admin, created = User.objects.get_or_create(
            correo='admin@canchitas.com',
            defaults={
                'nombre': 'Administrador',
                'sucursal': sucursal,
                'estado': True,
                'is_active': True,
                'is_staff': True,
                'is_superuser': True,
            },
        )
        if created:
            admin.set_password('Admin@1234')
            admin.save()

        for rol_permiso_id in admin_rol_permiso_ids:
            RolPermisoUsuario.objects.get_or_create(user=admin, rol_permiso_id=rol_permiso_id)

        self.stdout.write(self.style.SUCCESS('Modulo de usuarios seeded.'))
        self.stdout.write('Admin: admin@canchitas.com / Admin@1234')
