import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import freedoom_probe_prepare as source
import freedoom_install as assets
spec=importlib.util.spec_from_file_location('gui_policy',SCRIPTS/'termux/eidolon-gui.py');gui=importlib.util.module_from_spec(spec);spec.loader.exec_module(gui)
NONCE='a'*16;RUN='b'*32


class GuiWorkerTests(unittest.TestCase):
    def setUp(self):
        self.old=os.umask(0o077);self.addCleanup(os.umask,self.old)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.temp=tempfile.TemporaryDirectory(prefix='eidolon-gui-worker-');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.home=self.root/'files/home';self.home.mkdir(parents=True,mode=0o700);prefix=self.home.parent/'usr'
        self.binary=prefix/'bin/chocolate-doom';self.binary.parent.mkdir(parents=True,mode=0o700)
        shutil.copyfile(Path(sys.executable).resolve(),self.binary);self.binary.chmod(0o700)
        self.engine_sha=assets.sha(self.binary.read_bytes());self.engine_bytes=self.binary.stat().st_size
        members={name:('fixture '+name).encode() for name in source.MEMBERS};demo=b'd'*526
        self.stack.enter_context(patch.object(source,'MEMBERS',{name:(len(body),assets.sha(body)) for name,body in members.items()}))
        self.stack.enter_context(patch.object(source,'DEMO_SHA',assets.sha(demo)))
        self.directory=self.home/'.local/share/eidolon/freedoom/0.13.0'/RUN;self.directory.parent.mkdir(parents=True,mode=0o700)
        source.create_bundle(self.directory,RUN,members,demo)
        manifest=json.loads((self.directory/'manifest.json').read_bytes());self.pin=assets.sha((self.directory/'manifest.json').read_bytes())
        self.profile=dict(kind='freedoom_phase1_software',assets_run_id=RUN,manifest_sha256=self.pin)
        self.static={name:(row['bytes'],row['sha256']) for name,row in manifest['files'].items() if name!='launch-plan.json'}
        self.stack.enter_context(patch.object(gui,'STATIC_FILES',self.static));self.stack.enter_context(patch.object(gui,'ENGINE_SHA',self.engine_sha))
        self.stack.enter_context(patch.object(gui,'ENGINE_BYTES',self.engine_bytes))
        payload=hashlib.sha256()
        for name in sorted(manifest['files']):payload.update((self.directory/name).read_bytes())
        engine=dict(package='chocolate-doom',version='3.1.1',sha256=self.engine_sha,bytes=self.engine_bytes,uid=os.getuid(),mode=0o700,nlink=1,elf_machine=183,identity=list(gui.ident(self.binary.stat())))
        receipt=dict(schema=1,kind='eidolon-freedoom-installed-v1',run_id=RUN,uid=os.getuid(),manifest_sha256=self.pin,payload_sha256=payload.hexdigest(),wire_sha256='c'*64,
            engine=engine,files={name:row['bytes'] for name,row in manifest['files'].items()},execution='not_run',catalog='not_modified')
        (self.directory/'install-receipt.json').write_bytes(gui.canonical(receipt));(self.directory/'install-receipt.json').chmod(0o600)
        library=prefix/'lib/libSDL2-2.0.so';library.parent.mkdir(mode=0o700);library.write_bytes(b'fixed fake SDL');library.chmod(0o700)
        self.runtime={library.name:assets.sha(library.read_bytes())};self.aliases={'libSDL2.so':library.name};(library.parent/'libSDL2.so').symlink_to(library.name)
        self.stack.enter_context(patch.object(gui,'RUNTIME',self.runtime));self.stack.enter_context(patch.object(gui,'ALIASES',self.aliases))
        vendor=prefix/'share/glvnd/egl_vendor.d/50_mesa.json';vendor.parent.mkdir(parents=True,mode=0o700)
        vendor.write_bytes(gui.canonical({'file_format_version':'1.0.0','ICD':{'library_path':'libEGL_mesa.so.0'}}));vendor.chmod(0o600)
        self.lib=self.home/'.local/lib/eidolon';self.lib.mkdir(parents=True,mode=0o700)
        for name in ('eidolon-gui.py','eidolon_gui_owner.py'):
            shutil.copyfile(SCRIPTS/'termux'/name,self.lib/name);(self.lib/name).chmod(0o700)
    def request(self,launch='1'):
        return dict(schema=1,nonce=NONCE,session='17',launch_id=launch,execution_profile=self.profile,parent=gui.process(os.getpid()))
    def setup_lease(self):
        lease=self.home/'.eidolon'/('s-'+NONCE);lease.mkdir(parents=True,mode=0o700,exist_ok=True)
        (lease/'owner.json').write_bytes(gui.canonical(dict(schema=1,nonce=NONCE,uid=os.getuid())));(lease/'owner.json').chmod(0o600)
        self.anchor=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],start_new_session=True)
        self.addCleanup(self.cleanup_anchor)
        (lease/'lease.json').write_bytes(gui.canonical(dict(schema=1,nonce=NONCE,uid=os.getuid(),owner=dict(self.request()['parent'],state='S'),child=dict(gui.process(self.anchor.pid),state='S'))))
        (lease/'lease.json').chmod(0o600)
        self.socket=socket.socket(socket.AF_UNIX);self.socket.bind(str(lease/'w'));self.addCleanup(self.socket.close)
        return lease
    def start(self,body,launch='1'):
        self.setup_lease()
        code=("import importlib.util,pathlib,json,sys,signal\n"+f"s=importlib.util.spec_from_file_location('gui_worker',{str(self.lib/'eidolon-gui.py')!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            +f"m.ENGINE_SHA={self.engine_sha!r};m.ENGINE_BYTES={self.engine_bytes!r};m.STATIC_FILES={self.static!r};m.RUNTIME={self.runtime!r};m.ALIASES={self.aliases!r}\n"
            +f"m.command=lambda home,state,checked:[{str(self.binary)!r},'-I','-S','-B','-c',{body!r}]\n"
            +f"value=m.worker({self.request(launch)!r},0,lambda row:print(json.dumps(row),flush=True),home=pathlib.Path({str(self.home)!r}))\n")
        return subprocess.Popen([sys.executable,'-I','-S','-B','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def cleanup_anchor(self):
        if self.anchor.poll() is None:self.anchor.terminate()
        self.anchor.wait(timeout=2)
    def collect(self,child,control=None):
        selector=selectors.DefaultSelector();selector.register(child.stdout,selectors.EVENT_READ);rows=[];pending=bytearray();deadline=time.monotonic()+8
        try:
            while time.monotonic()<deadline:
                if selector.select(.02):
                    block=os.read(child.stdout.fileno(),8192)
                    if not block:break
                    pending.extend(block)
                    while b'\n' in pending:
                        line,_,rest=pending.partition(b'\n');pending[:]=rest;row=json.loads(line);rows.append(row)
                        if row['phase']=='started' and control is not None:
                            if control=='eof':child.stdin.close()
                            else:child.stdin.write(control);child.stdin.flush()
                if child.poll() is not None:break
            child.wait(timeout=2)
            more=child.stdout.read();pending.extend(more)
            rows.extend(json.loads(line) for line in pending.splitlines())
            errors=child.stderr.read();self.assertEqual(child.returncode,0,errors.decode());self.assertEqual(errors,b'')
            self.assertIsNone(self.anchor.poll())
            return rows
        finally:
            selector.close()
            if not child.stdin.closed:child.stdin.close()
            child.stdout.close();child.stderr.close()
    def test_policy_matches_known_source_pins_and_rejects_free_profile_env_and_paths(self):
        self.assertEqual(gui.profile(self.profile),self.profile)
        for value in (dict(self.profile,env={'LD_PRELOAD':'body'}),dict(self.profile,kind='gtk'),dict(self.profile,assets_run_id='../elsewhere')):
            with self.assertRaises(gui.GuiError):gui.profile(value)
        checked=gui.inspect_assets(self.home,self.profile);state=gui.make_state(self.home,NONCE,'1',checked)
        command=gui.command(self.home,state,checked);self.assertEqual(command[0],str(self.binary));self.assertIn('-warp',command);self.assertNotIn('-playdemo',command)
        env=gui.environment(self.home,state,self.home/'.eidolon'/('s-'+NONCE),NONCE,'1')
        self.assertEqual(env['SDL_VIDEO_WAYLAND_WMCLASS'],gui.app_id(NONCE,'1'));self.assertEqual(env['LIBGL_KOPPER_DISABLE'],'1');self.assertNotIn('LD_PRELOAD',env)
    def test_missing_license_or_runtime_change_is_rejected_before_spawn(self):
        path=self.directory/'assets/COPYING.txt';path.write_bytes(b'changed')
        with self.assertRaises(gui.GuiError):gui.inspect_assets(self.home,self.profile)
    def test_natural_failure_preserves_root_rc_and_reaps_owned_process_only(self):
        child=self.start("import time;print('FIXED',flush=True);time.sleep(.08);raise SystemExit(7)")
        rows=self.collect(child);self.assertEqual([row['phase'] for row in rows],['started','finished'])
        last=rows[-1];self.assertEqual(last['reason'],'natural');self.assertEqual(last['child_returncode'],7)
        self.assertTrue(last['cleanup']['complete'] and last['pipes_closed'] and last['assets_runtime_unchanged']);self.assertEqual(last['cleanup']['open_pidfds'],0)
    def test_cancel_and_control_eof_reap_game_without_stopping_anchor(self):
        for index,control in enumerate((b'{"type":"cancel"}\n','eof'),1):
            # fresh fixture worker uses another launch-id; socket/anchor remain in same ownership scope.
            if index>1:
                self.socket.close();(self.home/'.eidolon'/('s-'+NONCE)/'w').unlink();self.cleanup_anchor()
            child=self.start('import time;time.sleep(4)',str(index));rows=self.collect(child,control)
            self.assertEqual(rows[-1]['reason'],'cancel' if control!='eof' else 'control_eof')
            self.assertEqual(rows[-1]['child_returncode'],-15);self.assertTrue(rows[-1]['cleanup']['complete'] and rows[-1]['pipes_closed'])
    def test_separate_session_grandchild_is_adopted_reaped_and_closed(self):
        code="import os,signal,time\nif os.fork()==0:\n os.setsid();signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(4);os._exit(0)\ntime.sleep(.1)\n"
        child=self.start(code);rows=self.collect(child);last=rows[-1]
        self.assertEqual(last['child_returncode'],0);self.assertTrue(last['cleanup']['complete']);self.assertGreaterEqual(last['cleanup']['reaped'],2)
        self.assertGreaterEqual(last['cleanup']['kill_signals'],1);self.assertTrue(last['pipes_closed'])
    def manager(self,body):
        lease=self.setup_lease();text=(self.lib/'eidolon-gui.py').read_text()
        text=text.replace("HOME=Path('/data/data/com.termux/files/home')",'HOME=Path('+repr(str(self.home))+')')
        overrides=(f'ENGINE_SHA={self.engine_sha!r};ENGINE_BYTES={self.engine_bytes!r};STATIC_FILES={self.static!r};RUNTIME={self.runtime!r};ALIASES={self.aliases!r}\n'
            +f'command=lambda home,state,checked:[{str(self.binary)!r},"-I","-S","-B","-c",{body!r}]\n')
        text=text.replace("if __name__=='__main__':",overrides+"if __name__=='__main__':")
        return gui.GuiChildren(self.home,lease,'17',text.encode())
    def test_parent_manager_accepts_gui_without_foot_window_and_keeps_anchor_after_last_exit(self):
        manager=self.manager('import time;time.sleep(.1)')
        manager.allow_initial=True
        definition=dict(id='freedoom',label='Freedoom',mode='gui',executable=gui.EXECUTABLE,args=[],execution_profile=self.profile)
        manager.begin(None,'1',definition);opened=[];closed=[];deadline=time.monotonic()+6
        while manager.rows and time.monotonic()<deadline:
            accepted,retired=manager.tick();opened+=accepted;closed+=retired;time.sleep(.02)
        self.assertEqual(len(opened),1);self.assertTrue(opened[0]['success']);self.assertEqual(opened[0]['mode'],'gui')
        self.assertIsNone(opened[0]['request_id'])
        self.assertEqual(len(closed),1);self.assertEqual(closed[0]['child_returncode'],0);self.assertTrue(closed[0]['cleanup_complete'])
        self.assertEqual(manager.summary(),dict(schema=1,active=0,started=1,retired=1,cleanup_pending=False));self.assertIsNone(self.anchor.poll())
        with self.assertRaises(gui.GuiError):manager.begin('11','1',definition)
        with self.assertRaises(gui.GuiError):manager.begin(None,'2',definition)
        manager.begin('11','2',definition);deadline=time.monotonic()+6;again=[]
        while manager.rows and time.monotonic()<deadline:
            _,retired=manager.tick();again+=retired;time.sleep(.02)
        self.assertEqual(len(again),1);self.assertEqual(again[0]['child_returncode'],0);self.assertEqual(manager.summary()['started'],2)
        self.assertEqual(manager.summary()['retired'],2);self.assertIsNone(self.anchor.poll())
    def test_parent_manager_cancel_uses_control_and_preserves_worker_original_root_status(self):
        manager=self.manager('import time;time.sleep(4)');manager.begin('10','1',dict(mode='gui',executable=gui.EXECUTABLE,execution_profile=self.profile))
        closed=[];deadline=time.monotonic()+6;accepted=False
        while manager.rows and time.monotonic()<deadline:
            opened,retired=manager.tick();closed+=retired
            if opened:accepted=True;manager.cancel_all()
            time.sleep(.02)
        self.assertTrue(accepted);self.assertEqual(len(closed),1);self.assertEqual(closed[0]['child_returncode'],-15)
        self.assertTrue(closed[0]['cleanup_complete']);self.assertIsNone(self.anchor.poll())
    def test_worker_wire_rejects_unknown_body_and_cookie_or_owner_mismatch(self):
        request=self.request();row=dict(schema=1,nonce=NONCE,session='17',launch_id='1',uid=os.getuid(),app_id=gui.app_id(NONCE,'1'),phase='started',child=dict(pid=123,start='456',group=123))
        self.assertEqual(gui.worker_event(row,request),row)
        for change in (dict(body='PRIVATE'),dict(app_id='unrelated'),dict(uid=os.getuid()+1),dict(child=dict(pid=123,start='456',group=1))):
            with self.assertRaises(gui.GuiError):gui.worker_event(dict(row,**change),request)


class PinContractTests(unittest.TestCase):
    def test_fixed_product_asset_and_owner_pins_match_existing_preparer(self):
        expected=assets.expected_files(RUN)
        self.assertTrue(all(expected[name]==dict(bytes=row[0],sha256=row[1]) for name,row in gui.STATIC_FILES.items()))
        self.assertEqual(gui.OWNER_SHA,assets.sha((SCRIPTS/'termux/eidolon_gui_owner.py').read_bytes()))
        self.assertEqual(gui.ENGINE_SHA,assets.ENGINE_SHA);self.assertEqual(gui.ENGINE_BYTES,assets.ENGINE_BYTES)


if __name__=='__main__':unittest.main()
