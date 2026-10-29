import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent


class InstallationScriptsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='srm_install_test_')
        self.base = Path(self.temp.name).resolve()
        self.assertEqual(self.base.parent, Path(tempfile.gettempdir()).resolve())
        self.root = self.base / 'backend'
        self.root.mkdir()
        (self.root / 'scripts').mkdir()
        for name in ('init_env.py', 'check_install.py'):
            shutil.copy2(SCRIPTS / name, self.root / 'scripts' / name)
        (self.root / '.env.example').write_text('SECRET_KEY=example\nDB_NAME=sample\n', encoding='utf-8')
        (self.root / 'requirements.txt').write_text('', encoding='utf-8')
        (self.root / 'compatibilidad.json').write_text('{"entrega":"prueba","migraciones_requeridas":{}}', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, *args):
        return subprocess.run([sys.executable, str(self.root / 'scripts' / name), *args],
            capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})

    def test_init_generates_secret_and_never_overwrites_existing_configuration(self):
        first = self.run_script('init_env.py')
        self.assertEqual(first.returncode, 0, first.stderr)
        path = self.root / '.env'
        original = path.read_text(encoding='utf-8')
        self.assertIn('DB_NAME=sample', original)
        self.assertNotIn('SECRET_KEY=example', original)
        self.assertGreater(len(original.splitlines()[0]), 50)
        second = self.run_script('init_env.py')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_checker_reports_missing_environment_and_dependencies(self):
        (self.root / 'requirements.txt').write_text('srm-impossible-install-test-package==1.0\n', encoding='utf-8')
        result = self.run_script('check_install.py', '--skip-db')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Falta backend/.env', result.stderr)
        self.assertIn('srm-impossible-install-test-package', result.stderr)

    def test_checker_reports_incompatible_pinned_version(self):
        (self.root / '.env').write_text('SECRET_KEY=private\n', encoding='utf-8')
        (self.root / 'requirements.txt').write_text('pip==0.0.0\n', encoding='utf-8')
        result = self.run_script('check_install.py', '--skip-db')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Versiones distintas', result.stderr)
        self.assertNotIn('private', result.stderr + result.stdout)

    def test_checker_reports_missing_migration_source(self):
        (self.root / '.env').write_text('SECRET_KEY=private\n', encoding='utf-8')
        (self.root / 'compatibilidad.json').write_text('{"entrega":"prueba","migraciones_requeridas":{"caja":"0004"}}', encoding='utf-8')
        result = self.run_script('check_install.py', '--skip-db')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Falta la migracion caja.0004', result.stderr)

    def test_checker_rejects_mismatched_frontend_release(self):
        (self.root / '.env').write_text('SECRET_KEY=private\n', encoding='utf-8')
        frontend = self.base / 'frontend'
        frontend.mkdir()
        (frontend / 'compatibilidad.json').write_text('{"entrega":"otra"}', encoding='utf-8')
        result = self.run_script('check_install.py', '--skip-db')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identificadores de entrega distintos', result.stderr)

    def test_checker_reports_missing_frontend_manifest(self):
        (self.root / '.env').write_text('SECRET_KEY=private\n', encoding='utf-8')
        (self.base / 'frontend').mkdir()
        result = self.run_script('check_install.py', '--skip-db')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('frontend vecino no incluye compatibilidad.json', result.stderr)


if __name__ == '__main__':
    unittest.main()
