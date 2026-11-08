import copy
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import launcher_bundle as bundle
import freedoom_launcher_registration as registration
from sdl_probe_install import InstallError
loader=importlib.machinery.SourceFileLoader('register_freedoom_cli',str(SCRIPTS/'quest-register-freedoom-launcher'))
spec=importlib.util.spec_from_loader(loader.name,loader);cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        old=os.umask(0o077);self.addCleanup(os.umask,old)
        temp=tempfile.TemporaryDirectory(prefix='eidolon-freedoom-register-');self.addCleanup(temp.cleanup)
        self.home=Path(temp.name)/'home';self.home.mkdir(mode=0o700)
        wire,self.manifest=bundle.prepare(SCRIPTS.parent);bundle.install(wire,bundle.sha(wire),self.home)
        self.helper=registration.load_helper(self.home,self.manifest['files']);self.policy=Mock()
        self.helper.gui_policy=lambda home:self.policy
        self.request=dict(schema=1,action='inspect',run_id='1'*32,profile=dict(kind='freedoom_phase1_software',assets_run_id='2'*32,manifest_sha256='3'*64),expected_revision=None,sources=self.manifest['files'])
    def invoke(self,**changes):
        request=copy.deepcopy(self.request);request.update(changes)
        return registration.register(request,self.home,self.helper,expected_uid=os.getuid())
    def test_inspect_does_not_create_config_or_execute_and_register_preserves_default_entries(self):
        result=self.invoke();self.assertEqual(result['status'],'inspected');self.assertFalse((self.home/'.config').exists())
        self.assertFalse(result['configured']);self.assertIsNone(result['revision']);self.assertEqual(result['total'],2)
        saved=self.invoke(action='register');self.assertTrue(saved['changed']);self.assertEqual(saved['total'],3)
        self.assertEqual(saved['execution'],'not_run');self.assertFalse(saved['reload_requested'])
        catalog=self.helper.LauncherCatalog(self.home);catalog.reload(registration.CATALOG)
        self.assertEqual([row['id'] for row in catalog.apps],['terminal','editor','freedoom'])
        self.assertEqual(catalog.apps[-1],registration.definition(self.request['profile']))
        self.assertEqual((self.home/registration.CATALOG).stat().st_mode&0o777,0o600)
        self.assertFalse(list((self.home/'.config/eidolon').glob('.freedoom-*')))
        self.assertEqual(self.policy.inspect_assets.call_count,4)
    def test_existing_custom_definition_preserved_and_repeated_registration_idempotent(self):
        catalog=self.helper.LauncherCatalog(self.home)
        custom=dict(id='custom',label='private custom label',mode='terminal',executable='/fixed/test',args=['private body'])
        catalog.save(registration.CATALOG,None,custom);before=list(catalog.apps);revision=catalog.revision
        result=self.invoke(action='register',expected_revision=revision);self.assertTrue(result['other_entries_preserved'])
        catalog.reload(registration.CATALOG);self.assertEqual(catalog.apps[:-1],before)
        again=self.invoke(action='register',expected_revision=catalog.revision);self.assertFalse(again['changed'])
        self.assertNotIn('private',str(result));self.assertNotIn('private',str(again))
    def test_stale_cas_and_conflicting_fixed_id_do_not_overwrite(self):
        result=self.invoke(action='register');path=self.home/registration.CATALOG;original=path.read_bytes()
        with self.assertRaisesRegex(InstallError,'CATALOG_CHANGED'):self.invoke(action='register',expected_revision=None)
        self.assertEqual(path.read_bytes(),original)
        catalog=self.helper.LauncherCatalog(self.home);catalog.reload(registration.CATALOG)
        wrong=registration.definition(self.request['profile']);wrong['label']='different'
        catalog.save(registration.CATALOG,result['revision'],wrong);original=path.read_bytes()
        with self.assertRaisesRegex(InstallError,'LAUNCHER_ID_CONFLICT'):self.invoke(action='register',expected_revision=catalog.revision)
        self.assertEqual(path.read_bytes(),original)
    def test_first_creation_race_preserves_foreign_file_and_cleans_only_own_staging(self):
        real_link=os.link
        def raced(source,target,**kwargs):
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=kwargs['dst_dir_fd'])
            with os.fdopen(fd,'wb') as out:out.write(b'concurrent editor data')
            return real_link(source,target,**kwargs)
        with patch.object(registration.os,'link',side_effect=raced),self.assertRaisesRegex(InstallError,'CATALOG_CHANGED'):
            self.invoke(action='register')
        self.assertEqual((self.home/registration.CATALOG).read_bytes(),b'concurrent editor data')
        self.assertFalse(list((self.home/'.config/eidolon').glob('.freedoom-*')))
    def test_wrong_runtime_is_rejected_before_writes_and_uid_rejected_before_policy(self):
        self.policy.inspect_assets.side_effect=ValueError('ASSET_CHANGED')
        with self.assertRaisesRegex(ValueError,'ASSET_CHANGED'):self.invoke(action='register')
        self.assertFalse((self.home/'.config').exists());self.policy.reset_mock()
        with self.assertRaisesRegex(InstallError,'UID_MISMATCH'):
            registration.register(self.request,self.home,self.helper,expected_uid=os.getuid()+1)
        self.policy.inspect_assets.assert_not_called()
    def test_symlink_parent_source_mode_hash_and_unknown_request_rejected(self):
        outside=self.home.parent/'outside';outside.mkdir();(self.home/'.config').symlink_to(outside)
        with self.assertRaisesRegex(InstallError,'HELPER_SOURCE_UNSAFE'):self.invoke(action='register')
        self.assertFalse(list(outside.iterdir()))
        source=self.home/'.local/lib/eidolon/eidolon-gui.py';source.chmod(0o777)
        with self.assertRaisesRegex(InstallError,'HELPER_SOURCE_UNSAFE'):registration.load_helper(self.home,self.manifest['files'])
        source.chmod(0o700);source.write_bytes(source.read_bytes()+b'\n')
        with self.assertRaisesRegex(InstallError,'HELPER_SOURCE_CHANGED'):registration.load_helper(self.home,self.manifest['files'])
        with self.assertRaisesRegex(InstallError,'REQUEST_INVALID'):self.invoke(executable='/unwanted')
    def test_fixed_profile_rejects_free_env_argv_and_mutable_stage_without_overwrite(self):
        profile=dict(self.request['profile'],environment={'FREE':'VALUE'})
        with self.assertRaisesRegex(InstallError,'REQUEST_INVALID'):self.invoke(profile=profile)
        parent=registration.config_parent(self.home);stage=parent/('.freedoom-'+self.request['run_id']+'.toml');stage.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):self.invoke(action='register')
        self.assertEqual(stage.read_bytes(),b'keep');self.assertFalse((self.home/registration.CATALOG).exists())
    def test_apk_guard_and_compressed_remote_import_are_fixed_and_bounded(self):
        value=dict(process_id=1,build_info=dict(schema=1,source_digest='a'*64,profile='debug',target='aarch64-linux-android',apk_version_code=175))
        self.assertEqual(cli.apk_binding(value,'a'*64)['process_id'],1)
        with self.assertRaisesRegex(InstallError,'SOURCE_BUILD_MISMATCH'):cli.apk_binding(value,'b'*64)
        command=shlex.join(['run-as','com.termux','/data/data/com.termux/files/usr/bin/python3','-c',cli.remote_source()])
        self.assertLessEqual(len(command.encode()),49*1024)
        # 固定remote codeをimportし、HOME/UID/assetsへ到達する前の不正schema拒否を確認。error本文は固定分類だけ。
        process=subprocess.run([sys.executable,'-I','-S','-B','-c',cli.remote_source()],input=b'{}',capture_output=True,timeout=5)
        self.assertEqual(process.returncode,2);self.assertEqual(process.stderr,b'')
        self.assertIn(b'REQUEST_INVALID',process.stdout)
    def test_android_no_python_link_uses_owned_dirfd_libc_noreplace_without_overwrite(self):
        parent=registration.config_parent(self.home);stage=parent/('.freedoom-'+self.request['run_id']+'.toml');stage.write_bytes(b'own fixed stage')
        fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);self.addCleanup(os.close,fd)
        # 同じ公開libc ABIをlocalhost owned directoryだけで実callする。既存fileへreplaceしない。
        real_hasattr=hasattr
        def without_link(obj,name):return False if obj is os and name=='link' else real_hasattr(obj,name)
        with patch('builtins.hasattr',side_effect=without_link),patch.object(registration.sys,'platform','android'),patch.object(registration.platform,'machine',return_value='aarch64'):
            self.assertEqual(registration.exclusive_publish(fd,stage.name,'launcher.toml'),'android_bionic_renameat2_noreplace')
            self.assertEqual((parent/'launcher.toml').read_bytes(),b'own fixed stage')
            self.assertFalse(stage.exists());before=(parent/'launcher.toml').stat();stage.write_bytes(b'replacement refused')
            with self.assertRaises(FileExistsError) as failure:registration.exclusive_publish(fd,stage.name,'launcher.toml')
            self.assertEqual(failure.exception.errno,17)
            self.assertEqual((parent/'launcher.toml').read_bytes(),b'own fixed stage')
            self.assertEqual((parent/'launcher.toml').stat().st_ino,before.st_ino);self.assertEqual(stage.read_bytes(),b'replacement refused')
            with self.assertRaisesRegex(InstallError,'STAGING_UNSAFE'):registration.exclusive_publish(fd,'../outside','launcher.toml')
            with patch.object(registration.platform,'machine',return_value='x86_64'),self.assertRaisesRegex(InstallError,'PLATFORM_UNSUPPORTED'):
                registration.exclusive_publish(fd,stage.name,'launcher.toml')
    def test_android_noreplace_first_creation_moves_staging_and_keeps_nlink_one(self):
        real_hasattr=hasattr
        def without_link(obj,name):return False if obj is os and name=='link' else real_hasattr(obj,name)
        with patch('builtins.hasattr',side_effect=without_link),patch.object(registration.sys,'platform','android'),patch.object(registration.platform,'machine',return_value='aarch64'):
            result=self.invoke(action='register');self.assertEqual(result['status'],'registered')
            self.assertFalse(list((self.home/'.config/eidolon').glob('.freedoom-*')))
            self.assertEqual((self.home/registration.CATALOG).stat().st_nlink,1)


if __name__=='__main__':unittest.main()
