from decimal import Decimal
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RetireAdvanceMigrationTests(TransactionTestCase):
    old = [('catalogo', '0006_remove_cancha_anticipacion_minutos')]
    latest = [('catalogo', '0009_cancha_dimensiones')]

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_archive_removal_and_reverse_preserve_policies(self):
        apps = self.migrate(self.old)
        business, _ = apps.get_model('catalogo', 'Negocio').objects.get_or_create(
            singleton=True, defaults={'nombre_comercial': 'Migracion'})
        branch = apps.get_model('users', 'Sucursal').objects.create(nombre='Migracion', negocio=business)
        court = apps.get_model('catalogo', 'Cancha').objects.create(
            sucursal=branch, codigo='M', nombre='Migracion', duracion_minima_minutos=90)
        Policy = apps.get_model('catalogo', 'PoliticaReserva')
        snapshots = []
        for version, amount in enumerate([None, Decimal('25.50'), Decimal('100.00')], 1):
            p = Policy.objects.create(sucursal=branch, version=version, porcentaje_anticipo=amount,
                                      horas_limite_cancelacion=12, minutos_retencion=17,
                                      condiciones='Conservar nota', activa=version == 3)
            snapshots.append((p.pk, amount))
        apps = self.migrate(self.latest)
        Policy = apps.get_model('catalogo', 'PoliticaReserva')
        self.assertNotIn('porcentaje_anticipo', [f.name for f in Policy._meta.fields])
        Backup = apps.get_model('catalogo', 'RespaldoAnticipo')
        for pk, amount in snapshots:
            self.assertEqual(Backup.objects.get(politica_id=pk).porcentaje_anticipo, amount)
            p = Policy.objects.get(pk=pk)
            self.assertEqual((p.sucursal_id, p.horas_limite_cancelacion, p.minutos_retencion,
                              p.condiciones), (branch.pk, 12, 17, 'Conservar nota'))
        self.assertEqual(apps.get_model('catalogo', 'Cancha').objects.get(pk=court.pk).duracion_minima_minutos, 90)
        migrated_court = apps.get_model('catalogo', 'Cancha').objects.get(pk=court.pk)
        self.assertEqual(migrated_court.superficie, '')
        self.assertIsNone(migrated_court.ancho_metros)
        self.assertIsNone(migrated_court.largo_metros)
        apps = self.migrate(self.old)
        for pk, amount in snapshots:
            self.assertEqual(apps.get_model('catalogo', 'PoliticaReserva').objects.get(pk=pk).porcentaje_anticipo, amount)
