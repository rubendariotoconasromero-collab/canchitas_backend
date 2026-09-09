from django.test import TestCase
from django.contrib import admin
from django.test import RequestFactory
from unittest.mock import patch
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token

from .access import effective_permissions
from .models import User, Sucursal, Rol, Permiso, RolPermiso, RolPermisoUsuario


class AccessTests(TestCase):
    def setUp(self):
        self.branch = Sucursal.objects.create(nombre='Prueba A')
        self.other = Sucursal.objects.create(nombre='Prueba B')
        self.root = User.objects.create_superuser('root@example.test', 'Testing-Password-123')
        self.operator = self.make_user('operator', self.branch)
        self.target = self.make_user('target', self.branch)
        self.foreign = self.make_user('foreign', self.other)
        self.client = APIClient()
        self.login(self.operator)

    def make_user(self, name, branch=None):
        return User.objects.create_user(f'{name}@example.test', 'Testing-Password-123',
                                        nombre=name, sucursal=branch)

    def login(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        return token.key

    def grant(self, user, *codes):
        role, _ = Rol.objects.get_or_create(nombre=f'Role-{user.pk}')
        links = []
        for code in codes:
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            link, _ = RolPermiso.objects.get_or_create(rol=role, permiso=permission)
            RolPermisoUsuario.objects.get_or_create(user=user, rol_permiso=link)
            links.append(link)
        return links

    def test_authenticated_does_not_mean_authorized(self):
        for url in ['usuarios/', 'roles/', 'permisos/', 'sucursales/', 'rol-permisos/']:
            self.assertEqual(self.client.get('/api/' + url).status_code, 403)
        self.assertEqual(self.client.post('/api/usuarios/', {}).status_code, 403)

    def test_branch_scope_and_read_only(self):
        self.grant(self.operator, 'usuarios.ver')
        response = self.client.get('/api/usuarios/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['id'] for row in response.data}, {self.operator.pk, self.target.pk})
        self.assertEqual(self.client.get(f'/api/usuarios/{self.foreign.pk}/').status_code, 404)
        self.assertEqual(self.client.patch(f'/api/usuarios/{self.target.pk}/', {'nombre': 'X'}).status_code, 403)

    def test_no_branch_is_not_global_and_staff_is_not_admin(self):
        self.grant(self.operator, 'usuarios.ver')
        self.operator.sucursal = None
        self.operator.is_staff = True
        self.operator.save()
        self.assertEqual(self.client.get('/api/usuarios/').status_code, 403)

    def test_inactive_role_permission_and_branch_take_effect(self):
        link = self.grant(self.operator, 'usuarios.ver')[0]
        link.rol.estado = False
        link.rol.save()
        self.assertEqual(self.client.get('/api/usuarios/').status_code, 403)
        link.rol.estado = True
        link.rol.save()
        link.permiso.estado = False
        link.permiso.save()
        self.assertEqual(self.client.get('/api/usuarios/').status_code, 403)
        link.permiso.estado = True
        link.permiso.save()
        self.branch.estado = False
        self.branch.save()
        self.assertEqual(self.client.get('/api/usuarios/').status_code, 403)

    def test_legacy_is_read_only(self):
        self.grant(self.operator, 'acceso_usuarios')
        self.assertIn('usuarios.ver', effective_permissions(self.operator))
        self.assertNotIn('usuarios.editar', effective_permissions(self.operator))

    def test_user_write_cannot_move_branch_or_change_state_without_permission(self):
        self.grant(self.operator, 'usuarios.editar', 'usuarios.crear')
        url = f'/api/usuarios/{self.target.pk}/'
        self.assertEqual(self.client.patch(url, {'sucursal_id': self.other.pk}).status_code, 400)
        self.assertEqual(self.client.patch(url, {'estado': False}).status_code, 400)
        self.assertEqual(self.client.patch(url, {'nombre': 'Updated'}).status_code, 200)
        self.assertEqual(self.client.post('/api/usuarios/', {
            'nombre': 'X', 'correo': 'x@example.test', 'password': 'Another-Password-123',
            'sucursal_id': self.other.pk,
        }).status_code, 400)

    def test_cannot_edit_global_or_superuser_locally(self):
        self.grant(self.operator, 'usuarios.editar', 'usuarios.cambiar_estado')
        self.grant(self.target, 'alcance.global')
        for user in [self.target, self.root]:
            self.assertEqual(self.client.patch(f'/api/usuarios/{user.pk}/', {'password': 'New-Password-987'}).status_code, 404)

    def test_editor_cannot_take_over_a_more_privileged_peer(self):
        self.grant(self.operator, 'usuarios.editar', 'usuarios.cambiar_estado')
        self.grant(self.target, 'usuarios.asignar_accesos')
        url = f'/api/usuarios/{self.target.pk}/'
        self.assertEqual(self.client.patch(url, {'password': 'New-Password-987'}).status_code, 403)
        self.assertEqual(self.client.patch(url, {'correo': 'takeover@example.test'}).status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)

        self.target.estado = False
        self.target.is_active = False
        self.target.save()
        self.assertEqual(self.client.patch(url, {'estado': True, 'password': 'New-Password-987'}).status_code, 403)

    def test_cannot_delegate_unowned_or_self_permissions(self):
        self.grant(self.operator, 'usuarios.asignar_accesos')
        link = self.grant(self.target, 'usuarios.editar')[0]
        self.assertEqual(self.client.post(f'/api/usuarios/{self.operator.pk}/asignaciones/sync/',
                                         {'rol_permiso_ids': []}, format='json').status_code, 403)
        self.assertEqual(self.client.post(f'/api/usuarios/{self.target.pk}/asignaciones/sync/',
                                         {'rol_permiso_ids': []}, format='json').status_code, 403)
        self.assertTrue(RolPermisoUsuario.objects.filter(user=self.target, rol_permiso=link).exists())

    def test_role_changes_require_global_scope(self):
        self.grant(self.operator, 'roles.crear', 'roles.editar', 'roles.asignar_permisos')
        self.assertEqual(self.client.post('/api/roles/', {'nombre': 'Escalation'}).status_code, 403)

    def test_sync_preserves_ids_and_assignments_and_deduplicates(self):
        self.login(self.root)
        a, b = self.grant(self.target, 'usuarios.ver', 'usuarios.editar')
        assignment = RolPermisoUsuario.objects.get(user=self.target, rol_permiso=a)
        url = f'/api/roles/{a.rol_id}/permisos/sync/'
        response = self.client.post(url, {'permiso_ids': [a.permiso_id, a.permiso_id]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(RolPermiso.objects.filter(pk=a.pk).exists())
        self.assertTrue(RolPermisoUsuario.objects.filter(pk=assignment.pk).exists())
        self.assertFalse(RolPermiso.objects.filter(pk=b.pk).exists())
        url = f'/api/usuarios/{self.target.pk}/asignaciones/sync/'
        self.assertEqual(self.client.post(url, {'rol_permiso_ids': [a.pk, a.pk]}, format='json').status_code, 200)
        self.assertTrue(RolPermisoUsuario.objects.filter(pk=assignment.pk).exists())
        self.assertEqual(self.client.post(url, {'rol_permiso_ids': [999999]}, format='json').status_code, 400)
        self.assertTrue(RolPermisoUsuario.objects.filter(pk=assignment.pk).exists())

    def test_deactivation_and_password_change_revoke_tokens(self):
        self.login(self.root)
        token, _ = Token.objects.get_or_create(user=self.target)
        url = f'/api/usuarios/{self.target.pk}/'
        self.assertEqual(self.client.patch(url, {'estado': False}).status_code, 200)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertFalse(Token.objects.filter(pk=token.key).exists())
        self.client.patch(url, {'estado': True})
        token, _ = Token.objects.get_or_create(user=self.target)
        self.assertEqual(self.client.patch(url, {'password': 'Changed-Password-123'}).status_code, 200)
        self.assertFalse(Token.objects.filter(pk=token.key).exists())

    def test_last_recovery_account_is_protected(self):
        self.login(self.root)
        url = f'/api/usuarios/{self.root.pk}/'
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.client.patch(url, {'estado': False}).status_code, 400)

    def test_token_rejected_when_estado_inactive(self):
        self.operator.estado = False
        self.operator.save()
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_logout_revokes_token_and_me_reports_permissions(self):
        self.grant(self.operator, 'usuarios.ver')
        self.assertIn('usuarios.ver', self.client.get('/api/me/').data['user']['permisos_efectivos'])
        self.assertEqual(self.client.post('/api/logout/').status_code, 200)
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_system_permission_code_is_immutable(self):
        self.login(self.root)
        permission = Permiso.objects.get(nombre='usuarios.ver')
        response = self.client.patch(f'/api/permisos/{permission.pk}/', {'nombre': 'alcance.global.new'})
        self.assertEqual(response.status_code, 400)

    def test_admin_staff_cannot_bypass_business_permissions(self):
        self.operator.is_staff = True
        self.operator.save()
        request = RequestFactory().get('/admin/users/user/')
        request.user = self.operator
        self.assertFalse(admin.site._registry[User].has_change_permission(request, self.target))
        self.assertFalse(admin.site._registry[Rol].has_change_permission(request))
        request.user = self.root
        self.assertFalse(admin.site._registry[User].has_delete_permission(request, self.root))

    def test_password_change_outside_api_revokes_token(self):
        token, _ = Token.objects.get_or_create(user=self.target)
        self.target.set_password('Changed-Outside-Api-123')
        self.target.save()
        self.assertFalse(Token.objects.filter(pk=token.key).exists())

    def test_sync_failure_rolls_back_removals(self):
        self.login(self.root)
        old = self.grant(self.target, 'usuarios.ver')[0]
        new = Permiso.objects.get(nombre='usuarios.editar')
        with patch.object(RolPermiso.objects, 'bulk_create', side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(f'/api/roles/{old.rol_id}/permisos/sync/',
                                 {'permiso_ids': [new.pk]}, format='json')
        self.assertTrue(RolPermiso.objects.filter(pk=old.pk).exists())
        self.assertTrue(RolPermisoUsuario.objects.filter(user=self.target, rol_permiso=old).exists())
