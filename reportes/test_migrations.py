from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PhaseSixMigrationTests(TransactionTestCase):
    def test_upgrade_existing_roles_preserves_operations_and_custom_permissions(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('users', '0006_web_content_permissions')])
        old = executor.loader.project_state([('users', '0006_web_content_permissions')]).apps
        User, Role, Permission = [old.get_model('users', name) for name in ('User', 'Rol', 'Permiso')]
        Link, Assignment = [old.get_model('users', name) for name in ('RolPermiso', 'RolPermisoUsuario')]
        users = {}
        for name in ('Administrador', 'Recepcion', 'Cajero', 'Personalizado'):
            role, _ = Role.objects.get_or_create(nombre=name)
            Link.objects.filter(rol=role, permiso__nombre__in=['reportes.reservas', 'reportes.cobros',
                'reportes.cierres', 'reportes.ocupacion', 'reportes.exportar', 'auditoria.ver', 'avisos.ver']).delete()
            permission, _ = Permission.objects.get_or_create(nombre='dashboard.ver')
            link, _ = Link.objects.get_or_create(rol=role, permiso=permission)
            user = User.objects.create(correo=name+'@example.test', username=name+'@example.test', nombre=name)
            Assignment.objects.create(user=user, rol_permiso=link)
            users[name] = user.pk
        before = Assignment.objects.count()
        from users.models import Sucursal
        from caja.models import Caja
        branch = Sucursal.objects.create(nombre='Sucursal existente')
        box = Caja.objects.create(sucursal=branch, nombre='Caja existente')
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        from users.models import RolPermisoUsuario
        for name, pk in users.items():
            codes = set(RolPermisoUsuario.objects.filter(user_id=pk).values_list('rol_permiso__permiso__nombre', flat=True))
            self.assertIn('dashboard.ver', codes)
            if name == 'Administrador':
                self.assertIn('reportes.exportar', codes)
                self.assertIn('auditoria.ver', codes)
            elif name == 'Recepcion':
                self.assertIn('reportes.reservas', codes)
                self.assertNotIn('reportes.exportar', codes)
            elif name == 'Cajero':
                self.assertIn('reportes.cierres', codes)
                self.assertNotIn('reportes.reservas', codes)
            else:
                self.assertEqual(codes, {'dashboard.ver'})
        self.assertGreater(RolPermisoUsuario.objects.count(), before)
        self.assertEqual(Caja.objects.get(pk=box.pk).nombre, 'Caja existente')

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()
