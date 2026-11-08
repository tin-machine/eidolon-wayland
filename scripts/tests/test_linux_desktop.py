"""固定通常Linux capability/child ownerのみ。SSH/Quest/任意path・argvの入力は受けない。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
DESKTOP = load('desktop_fixture', ROOT / 'scripts/linux/eidolon_linux_desktop.py')
BASE = load('base_fixture', ROOT / 'scripts/linux/eidolon-linux-session.py')
OWNER = load('owner_fixture', ROOT / 'scripts/linux/eidolon_linux_process_owner.py')
CONTEXT = ('1', '2', 'a' * 64, 'b' * 16)


class BundleFixture:
    def __init__(self, root):
        self.home = root;self.directory = root / 'bundle';self.directory.mkdir(mode=0o700)
        self.hashes = {}
        for name in sorted(DESKTOP.FILES):
            data = ('fixed-' + name).encode();file = self.directory / name;file.write_bytes(data)
            file.chmod(0o700 if name == 'eidolon-gtk3-desktop' else 0o600)
            self.hashes[name] = hashlib.sha256(data).hexdigest()
        self.manifest = dict(schema=1, backend='linux_posix', revision=DESKTOP.REVISION,
                             base_bundle_sha256='c' * 64, gtk_version='3.24.52', files=self.hashes)
        self.publish()
        self.base = types.SimpleNamespace(bundle=lambda home:(self.directory, {}, 'c' * 64), private_read=BASE.private_read, decode=BASE.decode)
    def publish(self, raw=None):
        raw = raw if raw is not None else json.dumps(self.manifest).encode()
        file = self.directory / 'desktop-bundle.json';file.write_bytes(raw);file.chmod(0o600)
        self.pin = hashlib.sha256(raw).hexdigest()
    def bundle(self):
        return DESKTOP.Bundle(self.base,self.home,self.pin,self.hashes['eidolon_linux_desktop.py'],
                              self.hashes['eidolon_linux_process_owner.py'],self.hashes['gtk3-desktop-demo.c'])


class DesktopContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name);self.fixture = BundleFixture(self.root)
    def tearDown(self): self.temp.cleanup()
    def test_context_is_bound_and_not_printed(self):
        context = DESKTOP.Context(*CONTEXT)
        for index in range(4):
            values = list(CONTEXT);values[index] = ('3','4','d'*64,'e'*16)[index]
            self.assertNotEqual(context.scope, DESKTOP.Context(*values).scope)
        self.assertEqual(repr(context),'LinuxDesktopContext(<private>)')
        with self.assertRaisesRegex(AttributeError,'CONTEXT_IMMUTABLE'):context.nonce='c'*16
        for invalid in [(True,*CONTEXT[1:]),('0',*CONTEXT[1:]),('01',*CONTEXT[1:]),('1','2','z'*64,'b'*16)]:
            with self.assertRaisesRegex(ValueError,'CONTEXT_INVALID'):DESKTOP.Context(*invalid)
    def test_fixed_catalog_never_grants_shell_terminal_or_arbitrary_exec(self):
        catalog = self.fixture.bundle()._catalog(DESKTOP.Context(*CONTEXT))
        self.assertEqual([entry['id'] for entry in catalog['entries']],['gtk3-desktop'])
        self.assertFalse(catalog['terminal']);self.assertFalse(catalog['arbitrary_exec']);self.assertEqual(catalog['instance_limit'],1)
        self.assertEqual(catalog['entries'][0]['mode'],'gui')
    def test_manifest_unknown_duplicate_or_unpinned_source_is_rejected(self):
        self.fixture.manifest['unexpected'] = True;self.fixture.publish()
        with self.assertRaisesRegex(ValueError,'DESKTOP_BUNDLE_INVALID'):self.fixture.bundle()
        self.fixture.publish(b'{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError,'JSON_INVALID'):self.fixture.bundle()
        self.fixture.manifest.pop('unexpected');self.fixture.publish()
        bundle = self.fixture.bundle();(self.fixture.directory/'gtk3-desktop-demo.c').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'DESKTOP_FILE_CHANGED'):bundle.recheck()
    def test_selection_requires_same_profile_scope_and_bundle_revision(self):
        bundle = self.fixture.bundle();context = DESKTOP.Context(*CONTEXT);catalog = bundle._catalog(context)
        directory = self.root/'session';directory.mkdir(mode=0o700)
        with socket.socket(socket.AF_UNIX) as channel:
            channel.bind(str(directory/'p'))
            good = dict(scope=context.scope,revision=catalog['revision'],app_id='gtk3-desktop')
            command = bundle.command(directory,context,good)
            self.assertEqual(command[-2:],['--run-id',context.nonce*2]);self.assertIn('--no-gpu',command)
            for wrong in [dict(good,app_id='foot'),dict(good,revision='f'*64),dict(good,scope='f'*64),dict(good,argv=['/bin/sh'])]:
                with self.assertRaisesRegex(ValueError,'CAPABILITY_STALE'):bundle.command(directory,context,wrong)
            (directory/'w').touch()
            with self.assertRaisesRegex(ValueError,'STREAMLOCAL_INVALID'):bundle.command(directory,context,good)
    def test_same_path_bundle_change_is_not_a_second_capability(self):
        bundle = self.fixture.bundle();self.fixture.manifest['gtk_version']='3.24.53';self.fixture.publish()
        with self.assertRaisesRegex(ValueError,'DESKTOP_BUNDLE_CHANGED'):bundle.recheck()


ENV = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
def fixed_child(mode):
    if mode=='oversize':
        os.write(1,b'x'*70000);time.sleep(3);return
    if os.fork()==0:
        os.setsid();print('{"child":true}',flush=True);time.sleep(3);os._exit(0)
    time.sleep(.1)

def worker(mode):
    with tempfile.TemporaryDirectory() as temporary:
        directory=Path(temporary);tool=directory/'waypipe'
        if mode in {'inspect_oversize','inspect_timeout'}:
            script="#!/usr/bin/python3\nimport os,time\nif os.fork()==0:\n os.setsid();time.sleep(4);os._exit(0)\n"
            script += "os.write(1,b'x'*70000)\ntime.sleep(4)\n" if mode=='inspect_oversize' else "time.sleep(4)\n"
        else:script='#!/usr/bin/python3\nimport sys\nsys.stdout.buffer.write('+repr(BASE.VERSION)+')\n'
        tool.write_text(script);tool.chmod(0o700)
        if mode=='inspect_spawn':tool.unlink()
        class FixedBundle:
            home=directory
            base=types.SimpleNamespace(VERSION=BASE.VERSION,environment=lambda *_:ENV,gtk_environment=lambda *_:ENV)
            def recheck(self):pass
            def _catalog(self,context):return dict(revision='fixture')
            def command(self,*_):return [sys.executable,'-I','-S','-B',str(Path(__file__).resolve()),'--child',mode]
        bundle=FixedBundle();bundle.directory=directory
        value=DESKTOP.Instance(bundle,DESKTOP.Context(*CONTEXT),OWNER,directory)
        try:
            try:value.start({})
            except ValueError as error:
                if str(error)!='INSTANCE_CLOSED':raise
            else:raise RuntimeError('START_BEFORE_CAPABILITY')
            if mode.startswith('inspect_'):
                try:value.inspect()
                except (ValueError,FileNotFoundError):pass
                else:raise RuntimeError('INSPECTION_FAILURE_ACCEPTED')
                expected={'inspect_oversize':'TOOL_OUTPUT_LIMIT','inspect_timeout':'TOOL_TIMEOUT','inspect_spawn':'TOOL_INSPECTION_FAILED'}[mode]
                assert value.inspection_error==expected and not value.inspected
                if mode!='inspect_spawn':assert value.pipes
                try:value.inspect()
                except ValueError as error:assert str(error)=='INSTANCE_CLOSED'
                else:raise RuntimeError('INSPECTION_RETRIED')
                report=value.close();assert report['complete'] and report['pipes_eof'] and report['inspection_error']==expected
                assert value.close()==report
                print(json.dumps(dict(complete=True,pipes_eof=True,open_pidfds=0)),flush=True);return
            value.inspect();value.start({});deadline=time.monotonic()+2
            output_rejected=False
            while time.monotonic()<deadline:
                try:value.drain()
                except ValueError as error:
                    if mode!='oversize' or str(error)!='OUTPUT_LIMIT':raise
                    output_rejected=True;break
                if mode!='oversize' and value.stdout>0:break
                time.sleep(.01)
            if value.stdout==0 or mode=='oversize' and not output_rejected:raise RuntimeError('CHILD_NOT_READY')
            try:value.start({})
            except ValueError:pass
            else:raise RuntimeError('SECOND_INSTANCE_ACCEPTED')
            if mode=='pending':
                original=OWNER.descendant_snapshot
                def unavailable(*_args):raise OWNER.OwnershipError('PROCESS_SCAN_LIMIT')
                OWNER.descendant_snapshot=unavailable
                try:pending=value.close(timeout=.03)
                finally:OWNER.descendant_snapshot=original
                assert pending['pending'] and not pending['complete'] and not value.closed
                try:value.start({})
                except ValueError:pass
                else:raise RuntimeError('PENDING_RELAUNCHED')
            report=value.close();assert report['complete'] and report['pipes_eof'] and report['open_pidfds']==0
            assert value.close()==report
            print(json.dumps(dict(complete=True,pipes_eof=True,open_pidfds=0)),flush=True)
        finally:
            if not value.closed:value.close()


class DesktopProcessTest(unittest.TestCase):
    def run_worker(self,mode):
        child=subprocess.run([sys.executable,'-I','-S','-B',str(Path(__file__).resolve()),'--worker',mode],env=ENV,capture_output=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr.decode())
        self.assertEqual(json.loads(child.stdout),dict(complete=True,pipes_eof=True,open_pidfds=0))
    def test_dedicated_instance_reaps_setsid_descendant_and_closes_pipes(self):self.run_worker('plain')
    def test_pending_retains_live_owner_and_rejects_new_launch(self):self.run_worker('pending')
    def test_oversize_output_does_not_prevent_owned_cleanup(self):self.run_worker('oversize')
    def test_inspect_oversize_retains_original_error_and_drains_owned_pipes(self):self.run_worker('inspect_oversize')
    def test_inspect_timeout_retains_original_error_and_drains_owned_pipes(self):self.run_worker('inspect_timeout')
    def test_inspect_spawn_failure_is_terminal_without_retry(self):self.run_worker('inspect_spawn')


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--child' and sys.argv[2] in {'plain','pending','oversize'}:fixed_child(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--worker' and sys.argv[2] in {'plain','pending','oversize','inspect_oversize','inspect_timeout','inspect_spawn'}:worker(sys.argv[2])
    else:unittest.main()
