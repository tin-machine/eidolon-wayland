"""Temp の非secret固定bundleだけを作る。既存のhost/private bundleや鍵を読まない。"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
loader=importlib.machinery.SourceFileLoader('desktop_builder_fixture',str(ROOT/'scripts/build-linux-desktop-bundle'))
spec=importlib.util.spec_from_loader(loader.name,loader);builder=importlib.util.module_from_spec(spec);loader.exec_module(builder)

class DesktopBuilderTest(unittest.TestCase):
    def test_fixed_new_assets_and_manifest_leave_base_unchanged_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as name:
            home=Path(name)
            directory=home/'.local/lib/eidolon-linux';directory.mkdir(parents=True,mode=0o700)
            (home/'.local').chmod(0o700);(home/'.local/lib').chmod(0o700)
            files={}
            for item in builder.helper.FILES:
                data=(ROOT/'scripts/linux/eidolon-linux-session.py').read_bytes() if item.endswith('.py') else b'fixed-existing-base-asset'
                path=directory/item;path.write_bytes(data);path.chmod(0o600 if item.endswith('.py') else 0o700)
                files[item]=hashlib.sha256(data).hexdigest()
            base=dict(schema=1,backend='linux_posix',revision=builder.helper.REVISION,files=files)
            manifest=directory/'bundle.json';manifest.write_text(json.dumps(base));manifest.chmod(0o600);before=manifest.read_bytes()
            result=builder.build(directory)
            self.assertFalse(result['executed']);self.assertTrue(result['compiler_reaped'])
            self.assertEqual(manifest.read_bytes(),before);self.assertEqual(builder.helper.bundle(home)[1],files)
            self.assertEqual(set(result['source_hashes']),builder.desktop.FILES)
            with self.assertRaisesRegex(ValueError,'BUILD_ALREADY_EXISTS'):builder.build(directory)
            self.assertEqual(manifest.read_bytes(),before)
    def test_nonstandard_directory_is_not_an_implicit_install_location(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(ValueError,'BUILD_LOCATION_INVALID'):builder.build(Path(name))

if __name__=='__main__':unittest.main()
