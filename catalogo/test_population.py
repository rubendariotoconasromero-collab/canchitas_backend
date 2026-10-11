from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path
import json
from datetime import time

from django.core.management import call_command, CommandError
from django.test import TestCase
from users.models import User, Sucursal, Rol
from users.access import effective_permissions
from catalogo.models import Cancha, HorarioCancha, TarifaCancha, PoliticaReserva
from reservas.models import Cliente, Reserva, BloqueoCancha, PagoReserva
from caja.models import Caja, SesionCaja, MovimientoCaja, ReciboPago


class PopulationTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser('population@example.test', 'test-password')
        self.branch, _ = Sucursal.objects.get_or_create(nombre='Sucursal Central')
        self.output = StringIO()

    def run_command(self, **options):
        call_command('populate_business', branch_id=self.branch.pk, actor_id=self.actor.pk,
                     stdout=self.output, **options)

    def assert_no_operations(self):
        for model in (Cliente, Reserva, BloqueoCancha, PagoReserva, Caja, SesionCaja, MovimientoCaja, ReciboPago):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_repeatable_operational_population_and_role_scope(self):
        self.run_command()
        self.assertEqual(Cancha.objects.count(), 2)
        self.assertEqual(HorarioCancha.objects.count(), 14)
        self.assertEqual(TarifaCancha.objects.count(), 56)
        self.assertEqual(User.objects.count(), 4)
        self.assertEqual(Rol.objects.count(), 3)
        self.assert_no_operations()
        for hour in HorarioCancha.objects.all():
            self.assertEqual((hour.apertura, hour.cierre), (time(8), time(23)))
        cashier = User.objects.get(correo='cajero1@gmail.com')
        reception = User.objects.get(correo='recepcion@gmail.com')
        self.assertTrue(cashier.check_password('password'))
        self.assertTrue(reception.check_password('password'))
        self.assertIn('caja.abrir', effective_permissions(cashier))
        self.assertNotIn('caja.configurar', effective_permissions(cashier))
        self.assertNotIn('alcance.global', effective_permissions(reception))
        self.assertIn('reservas.crear', effective_permissions(reception))
        password_hash = cashier.password
        self.run_command()
        cashier.refresh_from_db()
        self.assertEqual(cashier.password, password_hash)
        self.assertEqual(Cancha.objects.count(), 2)
        self.assertEqual(TarifaCancha.objects.count(), 56)
        self.assert_no_operations()

    def test_existing_other_courts_require_explicit_reset(self):
        Cancha.objects.create(sucursal=self.branch, codigo='OLD', nombre='Anterior')
        with self.assertRaises(CommandError):
            self.run_command()
        self.assertEqual(Cancha.objects.count(), 1)

    def test_reset_access_updates_existing_accounts_without_duplicates(self):
        old = User.objects.create_user('recepcion@soccerrealmadrid.com', 'old-password', nombre='Recepcion', sucursal=self.branch)
        self.run_command(reset_access=True)
        old.refresh_from_db()
        self.actor.refresh_from_db()
        self.assertEqual(old.correo, 'recepcion@gmail.com')
        self.assertEqual(old.username, old.correo)
        self.assertTrue(old.check_password('password'))
        self.assertEqual(self.actor.correo, 'admin@gmail.com')
        self.assertTrue(self.actor.check_password('password'))
        self.assertEqual(User.objects.count(), 4)

    def test_reset_removes_old_data_and_preserves_admin_password(self):
        Cancha.objects.create(sucursal=self.branch, codigo='OLD', nombre='Anterior')
        Cliente.objects.create(sucursal=self.branch, nombre='Anterior', telefono='No registrado')
        Caja.objects.create(sucursal=self.branch, nombre='Anterior')
        User.objects.create_user('old@example.test', 'old-password', nombre='Anterior')
        password = self.actor.password
        with TemporaryDirectory() as directory:
            backup = Path(directory) / 'backup.json'
            backup.write_text('[]', encoding='utf-8')
            output = Path(directory) / 'credentials.json'
            self.run_command(reset=True, backup_file=str(backup), credentials_file=str(output))
            credentials = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(len(credentials), 3)
            for row in credentials:
                self.assertTrue(User.objects.get(correo=row['correo']).check_password(row['password']))
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.password, password)
        self.assertEqual(Cancha.objects.count(), 2)
        self.assert_no_operations()

    def test_reset_requires_backup_and_existing_credential_file_is_protected(self):
        with self.assertRaises(CommandError):
            self.run_command(reset=True)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'
            path.write_text('keep', encoding='utf-8')
            with self.assertRaises(CommandError):
                self.run_command(credentials_file=str(path))
            self.assertEqual(path.read_text(), 'keep')

    def test_dry_run_rolls_back(self):
        counts = [m.objects.count() for m in (Cancha, User, PoliticaReserva, Rol)]
        self.run_command(dry_run=True)
        self.assertEqual(counts, [m.objects.count() for m in (Cancha, User, PoliticaReserva, Rol)])

    def test_missing_retention_preserves_configured_cancellation(self):
        policy = PoliticaReserva.objects.create(sucursal=self.branch, version=50,
            minutos_retencion=None, horas_limite_cancelacion=8)
        self.run_command()
        policy.refresh_from_db()
        self.assertEqual(policy.minutos_retencion, 15)
        self.assertEqual(policy.horas_limite_cancelacion, 8)
