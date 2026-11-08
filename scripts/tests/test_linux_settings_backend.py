"""L7a A0 settings foundation, independent of the frozen v2 launch protocol.

C integration uses the actual dynamically loaded GIO module and GSettings. CI
must set EIDOLON_REQUIRE_GIO_TESTS=1; missing tools are an error in that job.
Only disposable synthetic profiles are written; Mousepad is not launched here.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
LOADER = importlib.machinery.SourceFileLoader('settings_builder', str(ROOT / 'linux_real_app_build.py'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
builder = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(builder)

SCHEMA = '''<schemalist>
  <enum id="org.xfce.mousepad.SessionRestore">
    <value nick="never" value="0"/><value nick="after-a-crash" value="1"/>
    <value nick="unsaved-documents" value="2"/><value nick="always" value="3"/>
  </enum>
  <enum id="org.xfce.mousepad.OpeningMode">
    <value nick="tab" value="0"/><value nick="window" value="1"/><value nick="mixed" value="2"/>
  </enum>
  <schema id="org.xfce.mousepad.preferences.file" path="/org/xfce/mousepad/preferences/file/">
    <key name="session-restore" enum="org.xfce.mousepad.SessionRestore"><default>'after-a-crash'</default></key>
    <key name="autosave-timer" type="u"><default>30</default></key>
  </schema>
  <schema id="org.xfce.mousepad.preferences.window" path="/org/xfce/mousepad/preferences/window/">
    <key name="opening-mode" enum="org.xfce.mousepad.OpeningMode"><default>'window'</default></key>
  </schema>
  <schema id="org.xfce.mousepad.preferences.view" path="/org/xfce/mousepad/preferences/view/">
    <key name="show-line-numbers" type="b"><default>false</default></key>
    <key name="font-name" type="s"><default>'Monospace 10'</default></key>
  </schema>
</schemalist>'''

CONFLICTING = b'''[org/xfce/mousepad/preferences/file]
session-restore='after-a-crash'
autosave-timer=30

[org/xfce/mousepad/preferences/window]
opening-mode='window'

[org/xfce/mousepad/preferences/view]
font-name='Monospace 9'
show-line-numbers=false
'''


class SettingsSourceBoundary(unittest.TestCase):
    """These always run; they do not substitute for the C integration job."""

    def test_only_public_backend_entry_points_are_used(self):
        source = (ROOT / 'linux/eidolon_settings_backend.c').read_text()
        self.assertNotRegex(source, r'\bg_settings_backend_(read|write|reset|subscribe|watch)\s*\(')
        self.assertNotIn('G_TYPE_INITABLE', source)
        self.assertIn('g_keyfile_settings_backend_new (filename, "/", NULL)', source)
        self.assertIn('g_settings_backend_changed_tree (backend, tree, origin_tag)', source)

    def test_builder_never_overwrites_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'existing'
            target.mkdir(mode=0o700)
            marker = target / 'keep'
            marker.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                builder.build_settings(target)
            self.assertEqual(marker.read_bytes(), b'preserve')
            self.assertEqual(sorted(p.name for p in target.iterdir()), ['keep'])

    def test_missing_build_tool_has_fixed_failure_and_no_compiled_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'output'
            with patch.object(builder, '_tool', side_effect=builder.BuildError('APP_BUILD_DEPENDENCY_UNAVAILABLE')):
                with self.assertRaisesRegex(builder.BuildError, '^APP_BUILD_DEPENDENCY_UNAVAILABLE$'):
                    builder.build_settings(target)
            self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob('*')), ['gio'])

    def test_wrapper_reports_fixed_error_for_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([str(ROOT / 'build-linux-settings-backend'),
                                        '--output-directory', directory],
                                       capture_output=True, timeout=5)
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stderr, b'')
            self.assertEqual(json.loads(completed.stdout),
                             {'schema': 1, 'status': 'failed', 'production_ready': False,
                              'reason': 'APP_SETTINGS_BUILD_UNAVAILABLE'})
            self.assertEqual(list(Path(directory).iterdir()), [])


class SettingsBackendIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = ('/usr/bin/cc', '/usr/bin/pkg-config', '/usr/bin/glib-compile-schemas',
                    '/usr/bin/gio-querymodules')
        missing = [name for name in required if not os.path.isfile(name)]
        if not missing:
            found = subprocess.run(['/usr/bin/pkg-config', '--atleast-version=2.80', 'gio-2.0'],
                                   capture_output=True, timeout=5)
            if found.returncode:
                missing = ['GLib/GIO >= 2.80 development package']
        if missing:
            reason = 'C integration unavailable: ' + ', '.join(missing)
            if os.environ.get('EIDOLON_REQUIRE_GIO_TESTS') == '1':
                raise AssertionError(reason)
            raise unittest.SkipTest(reason + '; CI requires EIDOLON_REQUIRE_GIO_TESTS=1')
        cls.temporary = tempfile.TemporaryDirectory(prefix='eidolon-settings-a0-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.artifacts = Path(cls.temporary.name)
        cls.bundle = cls.artifacts / 'bundle'
        completed = subprocess.run([str(ROOT / 'build-linux-settings-backend'),
                                    '--output-directory', str(cls.bundle)],
                                   capture_output=True, timeout=60)
        if completed.returncode:
            compiler_tail = b''
            log = cls.bundle / 'build.log'
            if log.is_file():
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 8192))
                    compiler_tail = stream.read(8192)
            raise AssertionError('settings build failed: ' + completed.stdout.decode('utf-8', 'replace') +
                                 '\nfixed-source compiler log (last 8192 bytes):\n' +
                                 compiler_tail.decode('utf-8', 'replace'))
        cls.build_receipt = json.loads(completed.stdout)
        cls.probe = cls.bundle / 'linux-settings-probe'
        cls.schemas = cls.artifacts / 'schemas'
        cls.schemas.mkdir(mode=0o700)
        (cls.schemas / 'mousepad.gschema.xml').write_text(SCHEMA)
        subprocess.run(['/usr/bin/glib-compile-schemas', '--strict', str(cls.schemas)],
                       check=True, capture_output=True, timeout=5)

    def setUp(self):
        self.profile = Path(tempfile.mkdtemp(dir=self.artifacts, prefix='profile-'))
        for name in ('home', 'config', 'data', 'state', 'cache', 'runtime'):
            (self.profile / name).mkdir(mode=0o700)
        marker = self.profile / 'settings-probe-v1'
        marker.write_bytes(b'eidolon-settings-probe-v1\n')
        marker.chmod(0o600)
        self.keyfile = self.profile / 'config/glib-2.0/settings/keyfile'
        self.environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'LANG': 'C',
                            'HOME': str(self.profile / 'home'),
                            'XDG_CONFIG_HOME': str(self.profile / 'config'),
                            'XDG_DATA_HOME': str(self.profile / 'data'),
                            'XDG_STATE_HOME': str(self.profile / 'state'),
                            'XDG_CACHE_HOME': str(self.profile / 'cache'),
                            'XDG_RUNTIME_DIR': str(self.profile / 'runtime'),
                            'GSETTINGS_BACKEND': 'eidolon-keyfile-v1',
                            'GIO_EXTRA_MODULES': str(self.bundle / 'gio'),
                            'GIO_USE_VFS': 'local', 'GSETTINGS_SCHEMA_DIR': str(self.schemas)}

    def seed(self, data=CONFLICTING):
        self.keyfile.parent.parent.mkdir(mode=0o700, exist_ok=True)
        self.keyfile.parent.mkdir(mode=0o700, exist_ok=True)
        self.keyfile.write_bytes(data)
        self.keyfile.chmod(0o600)

    def run_probe(self, mode='--check', expected=0):
        result = subprocess.run([str(self.probe), mode], env=self.environment,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected,
                         msg=f'stdout={result.stdout!r} stderr={result.stderr!r}')
        if expected != 78:
            value = json.loads(result.stdout)
            self.assertEqual(value['status'], 'passed' if expected == 0 else 'failed')
            return value
        self.assertEqual(result.stderr, b'APP_SETTINGS_UNVERIFIED\n')
        self.assertEqual(result.stdout, b'')
        return None

    def test_real_module_loading_pins_policy_without_writing_user_values(self):
        self.seed()
        before = self.keyfile.read_bytes()
        self.run_probe()
        self.assertEqual(self.keyfile.read_bytes(), before)
        self.assertFalse(self.build_receipt['production_ready'])
        manifest = json.loads((self.bundle / 'settings-backend.json').read_text())
        for name, expected in manifest['files'].items():
            self.assertEqual(hashlib.sha256((self.bundle / name).read_bytes()).hexdigest(), expected)

    def test_mutations_notifications_atomic_refusal_reset_and_restart_persistence(self):
        self.seed()
        self.run_probe('--exercise')
        self.run_probe('--verify-persistence')
        data = self.keyfile.read_text()
        # Policy overlays existing values. It does not rewrite them as a reset workaround.
        self.assertIn("session-restore='after-a-crash'", data)
        self.assertIn('autosave-timer=30', data)
        self.assertIn("opening-mode='window'", data)
        self.assertIn("font-name='Monospace 13'", data)
        self.assertIn('show-line-numbers=true', data)

    def test_synthetic_marker_required_for_exercise(self):
        self.seed()
        before = self.keyfile.read_bytes()
        (self.profile / 'settings-probe-v1').unlink()
        result = self.run_probe('--exercise', expected=1)
        self.assertEqual(result['reason'], 'SETTINGS_PROBE_SYNTHETIC_REQUIRED')
        self.assertEqual(self.keyfile.read_bytes(), before)

    def test_missing_module_rejects_glib_fallback(self):
        missing = self.artifacts / 'no-modules'
        self.environment['GIO_EXTRA_MODULES'] = str(missing)
        result = self.run_probe(expected=1)
        self.assertEqual(result['reason'], 'APP_SETTINGS_BACKEND_UNVERIFIED')
        self.assertFalse(self.keyfile.exists())

    def test_corrupt_module_rejects_glib_fallback(self):
        broken = self.profile / 'broken-modules'
        broken.mkdir(mode=0o700)
        (broken / 'libeidolon-settings.so').write_bytes(b'not-an-ELF-module')
        self.environment['GIO_EXTRA_MODULES'] = str(broken)
        result = self.run_probe(expected=1)
        self.assertEqual(result['reason'], 'APP_SETTINGS_BACKEND_UNVERIFIED')
        self.assertFalse(self.keyfile.exists())

    def test_explicit_other_backend_never_passes(self):
        self.environment['GSETTINGS_BACKEND'] = 'memory'
        result = self.run_probe(expected=1)
        self.assertEqual(result['reason'], 'APP_SETTINGS_BACKEND_UNVERIFIED')

    def test_corrupt_real_profile_terminates_instead_of_falling_back(self):
        self.seed(b'[malformed')
        self.run_probe(expected=78)
        self.assertEqual(self.keyfile.read_bytes(), b'[malformed')

    def test_wrong_home_contract_terminates(self):
        self.environment['HOME'] = str(self.profile / 'other-home')
        self.run_probe(expected=78)
        self.assertFalse(self.keyfile.exists())

    def test_unsafe_keyfile_variants_terminate_and_preserve_state(self):
        self.seed()
        self.keyfile.chmod(0o644)
        self.run_probe(expected=78)
        self.keyfile.chmod(0o4600)
        self.run_probe(expected=78)
        self.keyfile.chmod(0o600)
        outside = self.profile / 'outside'
        outside.write_bytes(b'preserve')
        outside.chmod(0o600)
        self.keyfile.unlink()
        self.keyfile.symlink_to(outside)
        self.run_probe(expected=78)
        self.keyfile.unlink()
        os.link(outside, self.keyfile)
        self.run_probe(expected=78)
        self.keyfile.unlink()
        os.mkfifo(self.keyfile, mode=0o600)
        self.run_probe(expected=78)
        self.keyfile.unlink()
        self.seed(b'x' * (1024 * 1024 + 1))
        self.run_probe(expected=78)
        self.assertEqual(outside.read_bytes(), b'preserve')

    def test_symlinked_parent_and_nonprivate_profile_terminate(self):
        self.profile.chmod(0o755)
        self.run_probe(expected=78)
        self.profile.chmod(0o1700)
        self.run_probe(expected=78)
        self.profile.chmod(0o700)
        external = self.profile / 'external'
        external.mkdir(mode=0o700)
        (self.profile / 'config/glib-2.0').symlink_to(external)
        self.run_probe(expected=78)
        self.assertEqual(list(external.iterdir()), [])

    def test_schema_type_drift_is_not_available(self):
        schemas = self.profile / 'wrong-schema'
        schemas.mkdir(mode=0o700)
        (schemas / 'mousepad.gschema.xml').write_text(
            SCHEMA.replace('name="autosave-timer" type="u"><default>30',
                           'name="autosave-timer" type="s"><default>\'30\''))
        subprocess.run(['/usr/bin/glib-compile-schemas', '--strict', str(schemas)],
                       check=True, capture_output=True, timeout=5)
        self.environment['GSETTINGS_SCHEMA_DIR'] = str(schemas)
        result = self.run_probe(expected=1)
        self.assertEqual(result['reason'], 'APP_SETTINGS_SCHEMA_UNVERIFIED')

    def test_keyfile_environment_cannot_redirect_delegate(self):
        alternate = self.profile / 'alternate'
        self.environment['GSETTINGS_KEYFILE'] = str(alternate)
        self.run_probe('--exercise')
        self.run_probe('--verify-persistence')
        self.assertTrue(self.keyfile.is_file())
        self.assertFalse(alternate.exists())


if __name__ == '__main__':
    unittest.main()
