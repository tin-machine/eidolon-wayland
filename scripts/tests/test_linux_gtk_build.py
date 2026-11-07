import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_linux_helper as base

loader = importlib.machinery.SourceFileLoader('linux_gtk_builder', str(Path(__file__).resolve().parents[1] / 'build-linux-gtk-bundle'))
spec = importlib.util.spec_from_loader(loader.name, loader)
builder = importlib.util.module_from_spec(spec); loader.exec_module(builder)


class LinuxGtkBuildTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='elgb-'); self.home = Path(self.temporary.name)
        self.directory = base.LinuxHelperTest.bundle(self)
    def tearDown(self): self.temporary.cleanup()
    def test_existing_extension_or_link_is_not_replaced(self):
        source = self.directory / builder.helper.GTK_FILES[0]; source.symlink_to('/does/not/exist')
        with self.assertRaisesRegex(ValueError, 'BUILD_ALREADY_EXISTS'): builder.build(self.directory)
        self.assertTrue(source.is_symlink()); self.assertFalse((self.directory / 'gtk-build').exists())
    def test_base_pin_mismatch_stops_before_compile(self):
        (self.directory / 'waypipe').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'BUNDLE_HASH_MISMATCH'): builder.build(self.directory)
        self.assertFalse((self.directory / 'gtk-build').exists())
    def test_build_timeout_reaps_only_owned_group(self):
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home)}
        with self.assertRaisesRegex(ValueError, 'BUILD_TIMEOUT'):
            builder.command(['/usr/bin/python3', '-c', 'import time;time.sleep(10)'], environment, seconds=.05)
    def test_fixed_real_c_build_pins_source_without_execution_and_preserves_base(self):
        if not Path('/usr/bin/cc').exists() or not Path('/usr/bin/pkg-config').exists():
            self.skipTest('system compiler/pkg-config unavailable')
        check = subprocess.run(['/usr/bin/pkg-config', '--exists', 'gtk+-3.0'], capture_output=True)
        if check.returncode != 0: self.skipTest('GTK3 development headers unavailable')
        before = (self.directory / 'bundle.json').read_bytes()
        report = builder.build(self.directory)
        self.assertEqual(report['status'], 'built'); self.assertFalse(report['executed']); self.assertTrue(report['compiler_reaped'])
        self.assertEqual(before, (self.directory / 'bundle.json').read_bytes())
        manifest = json.loads((self.directory / 'gtk-bundle.json').read_text())
        self.assertEqual(set(manifest['files']), set(builder.helper.GTK_FILES))
        self.assertEqual(builder.helper.gtk_bundle(self.home)[4], report['gtk_bundle_sha256'])
        self.assertEqual((self.directory / builder.helper.GTK_FILES[0]).read_bytes(), builder.SOURCE.read_bytes())
        with self.assertRaisesRegex(ValueError, 'BUILD_ALREADY_EXISTS'): builder.build(self.directory)


if __name__ == '__main__': unittest.main()
