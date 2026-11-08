"""Temp の非secret固定bundleだけを作る。既存のhost/private bundleや鍵を読まない。"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
            self.assertEqual(result['pins'],builder.print_pins(directory))
            with self.assertRaisesRegex(ValueError,'BUILD_ALREADY_EXISTS'):builder.build(directory)
            self.assertEqual(manifest.read_bytes(),before)
    def test_nonstandard_directory_is_not_an_implicit_install_location(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(ValueError,'BUILD_LOCATION_INVALID'):builder.build(Path(name))


class DesktopPinsTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.home=Path(self.temporary.name)
        self.directory=self.home/'.local/lib/eidolon-linux';self.directory.mkdir(parents=True,mode=0o700)
        (self.home/'.local').chmod(0o700);(self.home/'.local/lib').chmod(0o700)
        self.base={}
        for name in builder.helper.FILES:
            data=(ROOT/'scripts/linux/eidolon-linux-session.py').read_bytes() if name.endswith('.py') else b'never-executed-base-fixture'
            self.write(name,data);self.base[name]=hashlib.sha256(data).hexdigest()
        self.write('bundle.json',json.dumps(dict(schema=1,backend='linux_posix',revision=builder.helper.REVISION,files=self.base)).encode())
        self.hashes={}
        for name in sorted(builder.desktop.FILES):
            data=builder.SOURCES[name].read_bytes() if name in builder.SOURCES else b'never-executed-desktop-fixture'
            self.write(name,data);self.hashes[name]=hashlib.sha256(data).hexdigest()
        self.manifest=dict(schema=1,backend='linux_posix',revision=builder.desktop.REVISION,
            base_bundle_sha256=hashlib.sha256((self.directory/'bundle.json').read_bytes()).hexdigest(),gtk_version='3.24.52',files=self.hashes)
        self.publish()

    def tearDown(self):self.temporary.cleanup()

    def write(self,name,data):
        path=self.directory/name;path.write_bytes(data)
        path.chmod(0o700 if name in {'waypipe','eidolon-shm-fixture','eidolon-gtk3-desktop'} else 0o600)

    def publish(self):self.write('desktop-bundle.json',json.dumps(self.manifest).encode())

    def test_export_is_exact_pins_without_execution_or_mutation(self):
        before={p.name:(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_mode) for p in self.directory.iterdir()}
        with patch.object(builder.tools,'command',side_effect=AssertionError('must not execute')),patch.object(builder,'build',side_effect=AssertionError('must not build')):
            exported=builder.print_pins(self.directory)
        self.assertEqual(exported,dict(schema=1,backend='linux_desktop',base_bundle_sha256=self.manifest['base_bundle_sha256'],
            desktop_bundle_sha256=hashlib.sha256(before['desktop-bundle.json'][0]).hexdigest(),
            waypipe_sha256=self.base['waypipe'],shm_fixture_sha256=self.base['eidolon-shm-fixture'],desktop_binary_sha256=self.hashes['eidolon-gtk3-desktop']))
        self.assertLess(len(json.dumps(exported)),4096)
        self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_mode) for p in self.directory.iterdir()})

    def test_export_rejects_stale_source_even_if_manifest_hashes_match(self):
        source='eidolon_linux_desktop_session.py';self.write(source,b'previous-checkout-wire')
        self.manifest['files'][source]=hashlib.sha256(b'previous-checkout-wire').hexdigest();self.publish()
        with self.assertRaisesRegex(ValueError,'DESKTOP_SOURCE_CHANGED'):builder.print_pins(self.directory)

    def test_export_rejects_binary_tampering_and_linked_manifest(self):
        original=(self.directory/'eidolon-gtk3-desktop').read_bytes();self.write('eidolon-gtk3-desktop',b'tampered')
        with self.assertRaisesRegex(ValueError,'DESKTOP_FILE_CHANGED'):builder.print_pins(self.directory)
        self.write('eidolon-gtk3-desktop',original)
        path=self.directory/'desktop-bundle.json';saved=self.directory/'saved.json';path.rename(saved);path.symlink_to(saved.name)
        with self.assertRaises((ValueError,OSError)):builder.print_pins(self.directory)

    def test_export_rejects_unknown_manifest_fields_and_changed_base(self):
        self.manifest['unexpected']=True;self.publish()
        with self.assertRaisesRegex(ValueError,'DESKTOP_BUNDLE_INVALID'):builder.print_pins(self.directory)
        self.manifest.pop('unexpected');self.publish();self.write('waypipe',b'changed-base')
        with self.assertRaisesRegex(ValueError,'BUNDLE_HASH_MISMATCH'):builder.print_pins(self.directory)

if __name__=='__main__':unittest.main()
