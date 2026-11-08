import base64
import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import launcher_bundle as bundle
from sdl_probe_install import InstallError,canonical
loader=importlib.machinery.SourceFileLoader('install_launcher_cli',str(SCRIPTS/'quest-install-launcher'))
spec=importlib.util.spec_from_loader(loader.name,loader);cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)


class BundleTests(unittest.TestCase):
    def setUp(self):
        old=os.umask(0o077);self.addCleanup(os.umask,old)
        self.temp=tempfile.TemporaryDirectory(prefix='eidolon-helper-bundle-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'home';self.home.mkdir(mode=0o700)
        owner=b'# fixed fixture owner\n';worker=("OWNER_SHA='"+bundle.sha(owner)+"'\n").encode()
        main=("GUI_WORKER_SHA = '"+bundle.sha(worker)+"'\n").encode();self.files=dict(zip(bundle.FILES,(main,worker,owner)))
        self.manifest=dict(schema=2,kind='eidolon-termux-helper-bundle-v2',files={n:dict(bytes=len(v),sha256=bundle.sha(v)) for n,v in self.files.items()})
        self.value=dict(manifest=self.manifest,files={n:base64.b64encode(v).decode() for n,v in self.files.items()})
        self.wire=canonical(self.value);self.pin=bundle.sha(self.wire);self.target=self.home/'.local/lib/eidolon'
    def test_fixed_bundle_installs_owned_three_files_primary_last_and_preserves_other_data(self):
        self.target.mkdir(parents=True,mode=0o700);other=self.target/'user-keep';other.write_bytes(b'keep')
        order=[];replace=os.replace
        def replaced(source,target,**kwargs):order.append(target);return replace(source,target,**kwargs)
        with patch.object(bundle.os,'replace',side_effect=replaced):result=bundle.install(self.wire,self.pin,self.home)
        self.assertEqual(order,['eidolon_gui_owner.py','eidolon-gui.py','eidolon-session.py']);self.assertEqual(result['status'],'pass')
        self.assertEqual(result['sha256'],bundle.sha(self.files['eidolon-session.py']));self.assertEqual(other.read_bytes(),b'keep')
        for name,data in self.files.items():self.assertEqual((self.target/name).read_bytes(),data);self.assertEqual((self.target/name).stat().st_mode&0o777,0o700)
        self.assertFalse(list(self.target.glob('.bundle-*')))
    def test_unknown_files_bad_hash_boolean_size_and_dependency_change_reject_before_writes(self):
        changes=[lambda v:v['files'].update({'other.py':'YQ=='}),lambda v:v['manifest']['files']['eidolon-gui.py'].update(bytes=True),
            lambda v:v['manifest']['files']['eidolon-gui.py'].update(sha256='0'*64),lambda v:v['manifest'].update(command='body')]
        for change in changes:
            value=copy.deepcopy(self.value);change(value);wire=canonical(value)
            with self.assertRaises(InstallError):bundle.install(wire,bundle.sha(wire),self.home)
        files=dict(self.files);files['eidolon_gui_owner.py']=b'# different owner\n'
        with self.assertRaisesRegex(InstallError,'SOURCE_DEPENDENCY_MISMATCH'):bundle.dependency_pins(files)
        with self.assertRaisesRegex(InstallError,'BUNDLE_PIN_MISMATCH'):bundle.install(self.wire,'0'*64,self.home)
        self.assertFalse(self.target.exists())
    def test_symlink_target_and_parent_are_never_followed(self):
        outside=self.root/'outside';outside.mkdir();(self.home/'.local').symlink_to(outside)
        with self.assertRaisesRegex(InstallError,'DIRECTORY_UNSAFE'):bundle.install(self.wire,self.pin,self.home)
        self.assertFalse(list(outside.iterdir()));(self.home/'.local').unlink();self.target.mkdir(parents=True,mode=0o700)
        keep=outside/'keep';keep.write_bytes(b'unchanged');(self.target/'eidolon-session.py').symlink_to(keep)
        with self.assertRaisesRegex(InstallError,'TARGET_UNSAFE'):bundle.install(self.wire,self.pin,self.home)
        self.assertEqual(keep.read_bytes(),b'unchanged');self.assertFalse(list(self.target.glob('.bundle-*')))
    def test_interrupted_replacement_keeps_old_primary_and_removes_all_temporary_files(self):
        self.target.mkdir(parents=True,mode=0o700);main=self.target/'eidolon-session.py';main.write_bytes(b'# old primary\n');main.chmod(0o700)
        replace=os.replace
        def interrupted(source,target,**kwargs):
            if target=='eidolon-gui.py':raise OSError('fixture interrupted')
            return replace(source,target,**kwargs)
        with patch.object(bundle.os,'replace',side_effect=interrupted),self.assertRaises(OSError):bundle.install(self.wire,self.pin,self.home)
        self.assertEqual(main.read_bytes(),b'# old primary\n');self.assertFalse(list(self.target.glob('.bundle-*')))
        result=bundle.install(self.wire,self.pin,self.home);self.assertEqual(result['status'],'pass')
    def test_real_product_source_pin_chain_and_bounded_bootstrap_import(self):
        wire,manifest=bundle.prepare(SCRIPTS.parent);self.assertLessEqual(len(wire),bundle.MAX_WIRE)
        data,decoded=bundle.decode(wire,bundle.sha(wire));self.assertEqual(decoded,manifest);self.assertEqual(set(data),set(bundle.FILES))
        self.assertLessEqual(len(shlex.join(['run-as','com.termux','python3','-c',cli.remote_source(),'a'*64]).encode()),49*1024)
        # UID/home/配備を実行せず固定stdlib bootstrapのimportだけを確認する。
        import zlib
        program="import sys,types\n"
        for name in ('sdl_probe_install','launcher_bundle'):
            source=(SCRIPTS/(name+'.py')).read_text();program+=f"m=types.ModuleType({name!r});sys.modules[m.__name__]=m;exec({source!r},m.__dict__)\n"
        program+="print(callable(sys.modules['launcher_bundle'].install))\n"
        run=subprocess.run([sys.executable,'-I','-S','-B','-'],input=program.encode(),capture_output=True,timeout=5)
        self.assertEqual(run.returncode,0,run.stderr.decode());self.assertEqual(run.stdout,b'True\n')


if __name__=='__main__':unittest.main()
