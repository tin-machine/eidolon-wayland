import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from launcher_bundle import dependency_pins,FILES


class ReleaseToolsTests(unittest.TestCase):
    def test_actual_release_zip_block_includes_runtime_dependencies_and_offline_usage_inputs(self):
        script=(ROOT/'scripts/build-release').read_text();marker='python3 - "$repo_root" "$run_dir" <<\'PY\'\n'
        body=script.split(marker,1)[1].split('\nPY\n',1)[0]
        with tempfile.TemporaryDirectory(prefix='eidolon-release-tools-') as temporary:
            out=Path(temporary)
            run=subprocess.run([sys.executable,'-B','-c',body,str(ROOT),str(out)],capture_output=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr.decode())
            target=out/'eidolon-termux-tools.zip'
            with zipfile.ZipFile(target) as archive:
                files={name:archive.read('scripts/termux/'+name) for name in FILES};dependency_pins(files)
                for name in ('scripts/termux/eidolon-pair','scripts/pairing.py','scripts/termux/eidolon-start','scripts/termux-bootstrap.sh','docs/termux-wayland-tools.md','THIRD_PARTY_NOTICES.md'):
                    self.assertIn(name,archive.namelist())
                usage=archive.read('docs/usage.md').decode()
                positions=[usage.index('install -m 700 scripts/termux/'+name) for name in ('eidolon_gui_owner.py','eidolon-gui.py','eidolon-session.py')]
                self.assertEqual(positions,sorted(positions))
                self.assertNotIn('scripts/tests/', '\n'.join(archive.namelist()))
            self.assertEqual((out/'termux-tools.sha256').read_text(),hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')


if __name__=='__main__':unittest.main()
