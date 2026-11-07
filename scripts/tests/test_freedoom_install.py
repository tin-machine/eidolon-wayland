import base64
import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import freedoom_install as f
import freedoom_host_stream as host
from sdl_probe_install import InstallError
loader=importlib.machinery.SourceFileLoader('freedoom_install_cli',str(SCRIPTS/'quest-install-freedoom-probe'));spec=importlib.util.spec_from_loader(loader.name,loader)
cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
RUN='c'*32


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='freedoom-stream-');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        assets={n:('fixture '+n).encode() for n in f.source.MEMBERS};demo=b'fixture demo'
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(f.source,'MEMBERS',{n:(len(v),f.sha(v)) for n,v in assets.items()}))
        self.stack.enter_context(patch.object(f.source,'DEMO_BYTES',len(demo)));self.stack.enter_context(patch.object(f.source,'DEMO_SHA',f.sha(demo)))
        self.prepared=self.root/'prepared';f.source.create_bundle(self.prepared,RUN,assets,demo)
        self.pin=f.sha((self.prepared/'manifest.json').read_bytes());self.raw,self.manifest,self.identities,self.payload=f.validate_prepared(self.prepared,self.pin)
        self.home=self.root/'device/files/home';self.home.mkdir(mode=0o700,parents=True);prefix=self.home.parent/'usr'
        self.binary=prefix/'bin/chocolate-doom';self.binary.parent.mkdir(mode=0o700,parents=True)
        data=bytearray(64);data[:7]=b'\x7fELF\x02\x01\x01';struct.pack_into('<H',data,18,183);self.binary.write_bytes(data);self.binary.chmod(0o700)
        self.engine_pin=f.sha(data);self.stack.enter_context(patch.object(f,'ENGINE_SHA',self.engine_pin));self.stack.enter_context(patch.object(f,'ENGINE_BYTES',64))
        self.status=prefix/'var/lib/dpkg/status';self.status.parent.mkdir(mode=0o700,parents=True)
        self.status.write_bytes(b'Package: chocolate-doom\nStatus: install ok installed\nVersion: 3.1.1\n\n');self.status.chmod(0o600)
    def wire(self):return b''.join(f.stream_chunks(self.prepared,self.raw,self.pin,self.engine_pin,self.identities,self.payload))
    def install(self,data=None,**kwargs):
        data=self.wire() if data is None else data;r,w=os.pipe()
        try:
            self.assertLess(len(data),8192);os.write(w,data);os.close(w);w=None;reader=f.Reader(r,timeout=2)
            try:return f.install(reader,self.home,uid=kwargs.pop('uid',os.getuid()),**kwargs)
            finally:reader.close()
        finally:
            os.close(r)
            if w is not None:os.close(w)
    def target(self):return self.home/'.local/share/eidolon/freedoom/0.13.0'/RUN
    def header(self,wire,**changes):
        line,body=wire.split(b'\n',1);value=json.loads(line);value.update(changes);return f.canonical(value)+b'\n'+body
    def test_fixed_stream_has_wire_payload_file_pins_license_and_private_owned_new_run(self):
        wire=self.wire();value=self.install(wire);self.assertEqual(value['status'],'installed');self.assertEqual(value['uid'],os.getuid())
        self.assertEqual(value['wire_sha256'],f.sha(wire));self.assertEqual(value['payload_sha256'],self.payload);self.assertEqual(value['manifest_sha256'],self.pin)
        self.assertEqual(value['execution'],'not_run');self.assertEqual(value['catalog'],'not_modified');self.assertEqual(value['engine']['sha256'],self.engine_pin)
        for name,expected in self.manifest['files'].items():
            target=self.target()/name;self.assertEqual(f.sha(target.read_bytes()),expected['sha256']);self.assertEqual(target.stat().st_mode&0o777,0o600)
        self.assertEqual(self.target().stat().st_mode&0o777,0o700);self.assertTrue((self.target()/'assets/COPYING.txt').exists())
        saved=(self.target()/'manifest.json').read_bytes()
        with self.assertRaisesRegex(InstallError,'RUN_EXISTS'):self.install()
        self.assertEqual((self.target()/'manifest.json').read_bytes(),saved)
    def test_corruption_truncation_extra_payload_and_payload_sha_never_create_ready_manifest(self):
        wire=self.wire()
        for variant in (wire[:-1],wire+b'X',wire[:-1]+bytes([wire[-1]^1]),self.header(wire,payload_sha256='0'*64)):
            with tempfile.TemporaryDirectory(prefix='freedoom-other-') as td:
                original=self.home;self.home=Path(td)/'files/home';self.home.mkdir(mode=0o700,parents=True)
                # engineは同じfixture prefixへcopyし、他runへ触れない。
                new_binary=self.home.parent/'usr/bin/chocolate-doom';new_binary.parent.mkdir(parents=True);new_binary.write_bytes(self.binary.read_bytes());new_binary.chmod(0o700)
                status=self.home.parent/'usr/var/lib/dpkg/status';status.parent.mkdir(parents=True);status.write_bytes(self.status.read_bytes());status.chmod(0o600)
                try:
                    with self.assertRaises(InstallError):self.install(variant)
                    self.assertFalse((self.target()/'manifest.json').exists())
                    with self.assertRaisesRegex(InstallError,'RUN_EXISTS'):self.install(wire)
                finally:self.home=original
    def test_missing_license_changed_plan_boolean_size_and_manifest_extra_fields_reject_before_mkdir(self):
        for mutate in (lambda v:v['files'].pop('assets/COPYING.txt'),lambda v:v['files']['config/default.cfg'].update(bytes=True),
                       lambda v:v['files']['launch-plan.json'].update(sha256='f'*64),lambda v:v.update(environment={'PRIVATE':'body'})):
            value=copy.deepcopy(self.manifest);mutate(value);raw=f.canonical(value)+b'\n'
            with self.assertRaisesRegex(InstallError,'MANIFEST_INVALID'):f.validate_manifest(raw,f.sha(raw))
        self.assertFalse(self.target().exists())
    def test_host_changed_file_and_symlink_are_rejected_before_or_during_stream(self):
        path=self.prepared/'assets/COPYING.txt';original=path.read_bytes();path.write_bytes(b'X'*len(original))
        with self.assertRaisesRegex(InstallError,'FILE_PIN_MISMATCH'):f.validate_prepared(self.prepared,self.pin)
        with self.assertRaisesRegex(InstallError,'FILE_CHANGED'):list(f.stream_chunks(self.prepared,self.raw,self.pin,self.engine_pin,self.identities,self.payload))
        path.unlink();path.symlink_to(self.prepared/'assets/CREDITS.txt')
        with self.assertRaises(OSError):f.validate_prepared(self.prepared,self.pin)
    def test_uid_engine_pin_mode_link_and_package_version_are_strict(self):
        with self.assertRaisesRegex(InstallError,'UID_MISMATCH'):self.install(uid=os.getuid()+1)
        self.binary.chmod(0o755)
        with self.assertRaisesRegex(InstallError,'ENGINE_UNSAFE'):self.install()
        self.binary.chmod(0o700);self.status.write_bytes(b'Package: chocolate-doom\nStatus: install ok installed\nVersion: 3.1.0\n\n')
        with self.assertRaisesRegex(InstallError,'ENGINE_VERSION_MISMATCH'):self.install()
        self.status.write_bytes(b'Package: chocolate-doom\nStatus: install ok installed\nVersion: 3.1.1\n\n')
        os.link(self.binary,self.binary.parent/'engine-link')
        with self.assertRaisesRegex(InstallError,'ENGINE_UNSAFE'):self.install()
        self.assertFalse(self.target().exists())
    def test_existing_symlink_parent_is_never_followed_and_early_request_has_no_writes(self):
        elsewhere=self.root/'elsewhere';elsewhere.mkdir();(self.home/'.local').symlink_to(elsewhere)
        with self.assertRaises(OSError):self.install()
        self.assertFalse(list(elsewhere.iterdir()))
        (self.home/'.local').unlink()
        with self.assertRaisesRegex(InstallError,'REQUEST_INVALID'):self.install(self.header(self.wire(),pid=77))
        self.assertFalse(self.target().exists())
    def test_reader_eof_timeout_and_cancel_are_finite(self):
        r,w=os.pipe()
        try:
            reader=f.Reader(r,timeout=.03)
            try:
                with self.assertRaisesRegex(InstallError,'TRANSFER_TIMEOUT'):reader.header()
            finally:reader.close()
            os.close(w);w=None;reader=f.Reader(r,timeout=.1)
            try:
                with self.assertRaisesRegex(InstallError,'TRANSFER_EOF'):reader.header()
            finally:reader.close()
            reader=f.Reader(r,interrupted=lambda:True)
            try:
                with self.assertRaisesRegex(InstallError,'TRANSFER_CANCELLED'):reader.read(1)
            finally:reader.close()
        finally:
            os.close(r)
            if w is not None:os.close(w)
    def test_fixed_receipt_rejects_arbitrary_body_uid_engine_metadata_and_boolean_file_size(self):
        wire=self.wire();value=self.install(wire)
        with patch.object(cli,'UID',os.getuid()),patch.object(cli,'ENGINE_BYTES',64):
            self.assertEqual(cli.validated_result(value,self.manifest,self.pin,self.engine_pin,self.payload,f.sha(wire)),value)
            for mutate in (lambda v:v.update(body='PRIVATE'),lambda v:v.update(uid=os.getuid()+1),lambda v:v['engine'].update(path='PRIVATE'),
                           lambda v:v['engine'].update(mode=0o755),lambda v:v['files'].update({'config/default.cfg':True})):
                changed=copy.deepcopy(value);mutate(changed)
                with self.assertRaises(InstallError):cli.validated_result(changed,self.manifest,self.pin,self.engine_pin,self.payload,f.sha(wire))


class HostTransferTests(unittest.TestCase):
    def test_bounded_stream_and_raw_stderr_discard_with_real_owned_child(self):
        source="import sys,os,json;x=sys.stdin.buffer.read();os.write(2,b'PRIVATE body /path');print(json.dumps(dict(bytes=len(x))),flush=True)"
        value=host.transfer([sys.executable,'-c',source],[b'a'*65536,b'b'*100],timeout=3)
        self.assertEqual(json.loads(value.pop('stdout')),dict(bytes=65636));self.assertNotIn('PRIVATE',json.dumps(value));self.assertEqual(value['returncode'],0)
        self.assertTrue(value['reaped'] and value['pipes_drained']);self.assertEqual(value['output_bytes']['stdin'],65636)
    def test_early_remote_refusal_preserves_result_and_original_code_despite_broken_stdin(self):
        source="import json;print(json.dumps(dict(schema=1,status='failed',reason='ENGINE_MISSING',execution='not_run',catalog='not_modified')),flush=True);raise SystemExit(1)"
        value=host.transfer([sys.executable,'-c',source],[b'x'*65536]*8,timeout=3)
        self.assertEqual(value['returncode'],1);self.assertEqual(json.loads(value['stdout'])['reason'],'ENGINE_MISSING');self.assertTrue(value['reaped'] and value['pipes_drained'])
    def test_timeout_only_reclaims_own_child_and_source_failure_body_is_not_recorded(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        try:
            value=host.transfer([sys.executable,'-c','import time;time.sleep(30)'],[b'x'*65536]*8,timeout=.05)
            self.assertTrue(value['timed_out'] and value['reaped']);self.assertEqual(value['returncode'],-signal.SIGTERM);self.assertIsNone(unrelated.poll())
            def bad():yield b'fixed';raise ValueError('PRIVATE path')
            value=host.transfer([sys.executable,'-c','import sys;sys.stdin.buffer.read();print("fixed")'],bad(),timeout=3)
            self.assertEqual(value['local_error'],'SOURCE_STREAM_FAILED');self.assertNotIn('PRIVATE',repr(value))
        finally:unrelated.terminate();unrelated.wait(timeout=2)
    def test_fixed_bootstrap_is_bounded_and_importable_without_executing_installer(self):
        source=cli.remote_source();self.assertLessEqual(len(shlex.join(['run-as','com.termux','/data/data/com.termux/files/usr/bin/python3','-c',source]).encode()),49*1024)
        program=cli.remote_program().rsplit("raise SystemExit(sys.modules['freedoom_install'].remote_main())",1)[0]+"print(callable(sys.modules['freedoom_install'].install))\n"
        p=subprocess.run([sys.executable,'-'],input=program.encode(),capture_output=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr.decode());self.assertEqual(p.stdout.strip(),b'True')
        compile(source,'remote','exec');self.assertIn('scripts/quest-install-freedoom-probe',cli.source_hashes())


if __name__=='__main__':unittest.main()
