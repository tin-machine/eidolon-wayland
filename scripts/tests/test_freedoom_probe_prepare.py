import argparse
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import freedoom_probe_prepare as f
loader=importlib.machinery.SourceFileLoader('freedoom_cli',str(SCRIPTS/'prepare-freedoom-probe'));spec=importlib.util.spec_from_loader(loader.name,loader)
cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
RUN='a'*32


def demo():
    header=bytearray(13);header[0]=109;header[2:4]=bytes((1,6));header[9]=1
    return bytes(header)+bytes((1,2,3,4))*129+b'\x80'


def wad(data=None):
    body=data or demo()
    return b'IWAD'+struct.pack('<II',1,12+len(body))+body+struct.pack('<II8s',12,len(body),b'DEMO1\0\0\0')


def assets():return {'freedoom1.wad':wad(),'COPYING.txt':b'fixture license','CREDITS.txt':b'fixture credits','CREDITS-MUSIC.txt':b'fixture music'}


def pins(values):return {k:(len(v),hashlib.sha256(v).hexdigest()) for k,v in values.items()}


def archive(values,extra=()):
    output=io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name,data in values.items():z.writestr('freedoom-'+f.VERSION+'/'+name,data)
            for name,data in extra:z.writestr(name,data)
    return output.getvalue()


class ZipTests(unittest.TestCase):
    def test_selected_wad_and_all_license_credits_only_are_returned(self):
        values=assets();data=archive(values,[('freedoom-0.13.0/README.txt',b'not copied')])
        self.assertEqual(f.selected_members(data,expected=pins(values)),values)
    def test_traversal_absolute_backslash_dot_duplicate_and_symlink_are_rejected(self):
        values=assets()
        for name in ('../escape','/absolute','x/../escape','x//escape','x/./escape','x\\escape','x\nbody'):
            with self.subTest(name=name),self.assertRaises(f.PrepareError):f.selected_members(archive(values,[(name,b'x')]),expected=pins(values))
        duplicate='freedoom-0.13.0/COPYING.txt'
        with self.assertRaisesRegex(f.PrepareError,'ZIP_DUPLICATE'):f.selected_members(archive(values,[(duplicate,b'x')]),expected=pins(values))
        info=zipfile.ZipInfo('freedoom-0.13.0/link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
        with self.assertRaisesRegex(f.PrepareError,'ZIP_TYPE_INVALID'):f.selected_members(archive(values,[(info,b'/private')]),expected=pins(values))
    def test_missing_changed_and_wrong_size_assets_and_bad_zip_fail(self):
        values=assets();expected=pins(values)
        for mutation in (dict(values,**{'COPYING.txt':b'changed'}),{k:v for k,v in values.items() if k!='CREDITS.txt'}):
            with self.assertRaises(f.PrepareError):f.selected_members(archive(mutation),expected=expected)
        expected['COPYING.txt']=(len(values['COPYING.txt']),'f'*64)
        with self.assertRaisesRegex(f.PrepareError,'ASSET_PIN_MISMATCH'):f.selected_members(archive(values),expected=expected)
        with self.assertRaisesRegex(f.PrepareError,'ZIP_INVALID'):f.selected_members(b'PRIVATE /not-a-zip',expected={})
    def test_member_count_and_size_bounds_are_checked_before_reading_content(self):
        extra=[(f'extra/{i}',b'x') for i in range(65)]
        with self.assertRaisesRegex(f.PrepareError,'ZIP_LIMIT'):f.selected_members(archive(assets(),extra),expected=pins(assets()))
    def test_archive_owner_mode_symlink_and_fixed_pin_are_checked(self):
        data=archive(assets())
        with tempfile.TemporaryDirectory(prefix='freedoom-zip-') as td:
            path=Path(td)/'archive.zip';path.write_bytes(data);path.chmod(0o600)
            with patch.object(f,'ARCHIVE_BYTES',len(data)),patch.object(f,'ARCHIVE_SHA',f.digest(data)):
                self.assertEqual(f.read_archive(path),data)
                with patch.object(f.os,'getuid',return_value=os.getuid()+1),self.assertRaisesRegex(f.PrepareError,'ARCHIVE_UNSAFE'):f.read_archive(path)
                path.chmod(0o666)
                with self.assertRaisesRegex(f.PrepareError,'ARCHIVE_UNSAFE'):f.read_archive(path)
                path.chmod(0o600);path.write_bytes(bytes([data[0]^1])+data[1:])
                with self.assertRaisesRegex(f.PrepareError,'ARCHIVE_PIN_MISMATCH'):f.read_archive(path)
                link=Path(td)/'link';link.symlink_to(path)
                with self.assertRaises(OSError):f.read_archive(link)


class DemoAndPlanTests(unittest.TestCase):
    def short(self):return demo()[:13+128*4]+b'\x80'
    def test_short_demo_keeps_header_and_128_tics_then_single_end_marker(self):
        data=demo();short=self.short()
        with patch.object(f,'DEMO_SOURCE_BYTES',len(data)),patch.object(f,'DEMO_SOURCE_SHA',f.digest(data)),patch.object(f,'DEMO_SHA',f.digest(short)):
            self.assertEqual(f.wad_demo(wad()),short)
        self.assertEqual(len(short),526);self.assertEqual(short[:13],data[:13]);self.assertEqual(short[-1],128)
    def test_wad_bounds_magic_duplicate_demo_and_header_are_rejected(self):
        bad=[b'PWAD'+wad()[4:],wad()[:10],b'IWAD'+struct.pack('<II',99999,12)+b'fixed',b'IWAD'+struct.pack('<II',1,999999)+b'fixed']
        changed=bytearray(wad());struct.pack_into('<I',changed,len(changed)-16,2**32-1);bad.append(bytes(changed))
        for data in bad:
            with self.assertRaises(f.PrepareError):f.wad_demo(data)
        wrong=bytearray(demo());wrong[0]=110
        with patch.object(f,'DEMO_SOURCE_BYTES',len(wrong)),patch.object(f,'DEMO_SOURCE_SHA',f.digest(wrong)),self.assertRaisesRegex(f.PrepareError,'DEMO_INVALID'):f.wad_demo(wad(wrong))
    def test_fixed_cases_do_not_allow_free_args_or_terminal_mode_bypass(self):
        plan=f.launch_plan(RUN);self.assertEqual(plan['mode'],'gui');self.assertEqual(plan['catalog'],dict(automatically_registered=False,available=False,error='GUI_NOT_VERIFIED'))
        self.assertEqual(plan['profile'],f.PROFILE);self.assertEqual(plan['graphics_environment']['SDL_VIDEODRIVER'],'wayland')
        self.assertIn('LIBGL_KOPPER_DISABLE',plan['graphics_environment']);self.assertEqual(plan['execution'],'not_run')
        self.assertEqual([v['case'] for v in plan['cases']],list(f.CASES));self.assertIn('-playdemo',plan['cases'][0]['arguments']);self.assertNotIn('-timedemo',json.dumps(plan))
        self.assertNotIn('-playdemo',plan['cases'][1]['arguments']);self.assertIn('-warp',plan['cases'][1]['arguments'])
        for value in ('demo128 --free',{},None):
            with self.assertRaises(f.PrepareError):f.fixed_case(value)
        for value in ('../bad','b'*31,None):
            with self.assertRaises(f.PrepareError):f.launch_plan(value)


class BundleTests(unittest.TestCase):
    def test_new_owned_bundle_has_pins_licenses_private_modes_and_never_overwrites(self):
        values=assets();short=demo()[:13+128*4]+b'\x80'
        with tempfile.TemporaryDirectory(prefix='freedoom-bundle-') as td,patch.object(f,'MEMBERS',pins(values)),patch.object(f,'DEMO_SHA',f.digest(short)):
            directory=Path(td)/'run';manifest=f.create_bundle(directory,RUN,values,short)
            self.assertEqual(manifest['catalog'],'not_modified');self.assertEqual(manifest['execution'],'not_run');self.assertEqual(manifest['signature_verification'],'not_performed')
            for name,metadata in manifest['files'].items():
                data=(directory/name).read_bytes();self.assertEqual(metadata,dict(bytes=len(data),sha256=f.digest(data)));self.assertEqual((directory/name).stat().st_mode&0o777,0o600)
            for path in (directory,directory/'assets',directory/'config'):self.assertEqual(path.stat().st_mode&0o777,0o700)
            self.assertTrue((directory/'assets/COPYING.txt').exists());self.assertTrue((directory/'assets/CREDITS-MUSIC.txt').exists())
            saved=(directory/'manifest.json').read_bytes()
            with self.assertRaises(FileExistsError):f.create_bundle(directory,RUN,values,short)
            self.assertEqual((directory/'manifest.json').read_bytes(),saved)
    def test_changed_or_missing_license_and_demo_are_refused_before_creating_run(self):
        values=assets();short=demo()[:13+128*4]+b'\x80'
        with tempfile.TemporaryDirectory(prefix='freedoom-reject-') as td,patch.object(f,'MEMBERS',pins(values)),patch.object(f,'DEMO_SHA',f.digest(short)):
            directory=Path(td)/'run'
            with self.assertRaises(f.PrepareError):f.create_bundle(directory,RUN,{k:v for k,v in values.items() if k!='COPYING.txt'},short)
            with self.assertRaises(f.PrepareError):f.create_bundle(directory,RUN,values,short+b'x')
            self.assertFalse(directory.exists())
    def test_output_symlink_never_replaces_existing_target(self):
        with tempfile.TemporaryDirectory(prefix='freedoom-link-') as td:
            directory=Path(td);victim=directory/'victim';victim.write_bytes(b'PRIVATE');(directory/'link').symlink_to(victim)
            with self.assertRaises(FileExistsError):f.write_new(directory,'link',b'overwrite')
            self.assertEqual(victim.read_bytes(),b'PRIVATE')
    def test_cli_rejects_wrong_archive_without_execution_or_copying_raw_body(self):
        with tempfile.TemporaryDirectory(prefix='freedoom-cli-') as td:
            root=Path(td);archive_path=root/'bad.zip';archive_path.write_bytes(b'PRIVATE');archive_path.chmod(0o600);output=io.StringIO()
            with patch.object(cli.argparse.ArgumentParser,'parse_args',return_value=argparse.Namespace(archive=archive_path,artifact_root=root/'out')),contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(),1)
            value=json.loads(output.getvalue());self.assertEqual(value['reason'],'ARCHIVE_UNSAFE');self.assertEqual(value['execution'],'not_run');self.assertNotIn('PRIVATE',output.getvalue())
            self.assertFalse(list((root/'out').iterdir()))


if __name__=='__main__':unittest.main()
