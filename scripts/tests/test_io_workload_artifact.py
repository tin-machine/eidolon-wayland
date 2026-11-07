"""source pinとprivate導入・固定debug bootstrapが一致することを検査する。"""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('io_artifact',ROOT/'scripts/io_workload_artifact.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ArtifactTests(unittest.TestCase):
    def test_pin_executes_only_verified_private_bytes(self):
        source=b"print('fixed-ok')\n";pin=hashlib.sha256(source).hexdigest()
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);result=m.install(source,pin,home)
            target=home.joinpath(*m.PARTS,m.NAME)
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            self.assertEqual(target.parent.stat().st_mode&0o777,0o700)
            self.assertEqual(result['sha256'],pin)
            code=m.bootstrap(pin).replace(str(m.HOME),str(home))
            run=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True,timeout=3)
            self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(run.stdout,'fixed-ok\n')
            target.write_text("print('untrusted')\n")
            rejected=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,text=True,timeout=3)
            self.assertNotEqual(rejected.returncode,0);self.assertEqual(rejected.stdout,'')

    def test_symlink_hardlink_and_unsafe_directory_rejected(self):
        source=b'fixed';pin=hashlib.sha256(source).hexdigest()
        with tempfile.TemporaryDirectory() as name:
            home=Path(name);m.install(source,pin,home)
            target=home.joinpath(*m.PARTS,m.NAME);elsewhere=home/'unrelated';elsewhere.write_bytes(b'keep')
            target.unlink();target.symlink_to(elsewhere)
            with self.assertRaisesRegex(ValueError,'ARTIFACT_UNSAFE'):m.install(source,pin,home)
            target.unlink();os.link(elsewhere,target)
            with self.assertRaisesRegex(ValueError,'ARTIFACT_UNSAFE'):m.install(source,pin,home)
            target.unlink();target.parent.chmod(0o755)
            with self.assertRaisesRegex(ValueError,'DIRECTORY_UNSAFE'):m.install(source,pin,home)
            self.assertEqual(elsewhere.read_bytes(),b'keep')

    def test_android_fixed_bootstrap_matches_source_pin_and_has_no_adb_payload(self):
        source=(ROOT/'scripts/termux/eidolon-io-workload.py').read_bytes()
        pin=hashlib.sha256(source).hexdigest()
        kotlin=(ROOT/'android/app/src/debug/java/io/github/tinmachine/eidolon/IoWorkloadCase.kt').read_text()
        self.assertEqual(re.search(r'SOURCE_SHA256 = "([0-9a-f]+)"',kotlin)[1],pin)
        self.assertEqual(kotlin.split('private val BOOTSTRAP = """',1)[1].split('"""',1)[0],m.bootstrap(pin))
        with self.assertRaisesRegex(ValueError,'PIN_INVALID'):m.bootstrap('not-a-pin')
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(ValueError,'SOURCE_MISMATCH'):m.install(b'changed',pin,Path(name))
            self.assertEqual(list(Path(name).iterdir()),[])


if __name__=='__main__':unittest.main()
