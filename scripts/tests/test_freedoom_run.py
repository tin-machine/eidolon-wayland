import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import freedoom_install as assets
import freedoom_run as game
import freedoom_probe_host as host
from sdl_probe_install import InstallError
from sdl_lease_capture import CaptureError
loader=importlib.machinery.SourceFileLoader("freedoom_demo_cli",str(SCRIPTS/"quest-run-freedoom-probe"))
spec=importlib.util.spec_from_loader(loader.name,loader);cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
PROBE="a"*32;RUN="b"*32


def trace(expected=None):
    expected=game.app_id(PROBE) if expected is None else expected
    return ('xdg_wm_base@4.get_xdg_surface(new id xdg_surface@5, wl_surface@6)\n'
        'xdg_surface@5.get_toplevel(new id xdg_toplevel@7)\n'
        f'xdg_toplevel@7.set_app_id("{expected}")\n'
        'wl_shm_pool@8.create_buffer(new id wl_buffer@9, 0, 640, 400, 2560, 0)\n'
        'wl_surface@6.attach(wl_buffer@9, 0, 0)\n'+('wl_surface@6.commit()\n'*3))


def program(extra="",body=None):
    body=trace() if body is None else body
    return [sys.executable,"-c","import os,time,signal,sys\nos.write(2,"+repr(body.encode())+")\ntime.sleep(.08)\n"+extra]


class TraceTests(unittest.TestCase):
    def test_app_id_shm_and_raw_unknown_body_are_reduced_to_counts(self):
        row=game.GameTrace(game.app_id(PROBE));row.feed(trace().encode());value=row.summary()
        self.assertEqual(value["matching_app_id_shm_commits"],3);self.assertEqual(value["expected_app_id_events"],1)
        row.feed(b'xdg_toplevel@7.set_app_id("PRIVATE body /path")\n');value=row.summary()
        self.assertEqual(value["unknown_app_id_events"],1);self.assertNotIn("PRIVATE",json.dumps(value))
    def test_reused_object_cannot_reuse_a_prior_verified_app_id(self):
        row=game.GameTrace(game.app_id(PROBE));row.feed(trace().encode())
        row.feed(b'wl_surface@6.destroy()\nxdg_surface@5.get_toplevel(new id xdg_toplevel@7)\nwl_surface@6.attach(wl_buffer@9, 0, 0)\nwl_surface@6.commit()\n')
        self.assertEqual(row.summary()["matching_app_id_shm_commits"],3)


class OwnedChildTests(unittest.TestCase):
    def run_owned(self,command=None,*,control=None,checker=lambda:None,**kwargs):
        r,w=os.pipe();events=[]
        worker=None
        try:
            if control:
                def write():
                    time.sleep(.05)
                    if control=="eof":os.close(w)
                    else:os.write(w,control)
                worker=threading.Thread(target=write);worker.start()
            with patch.object(game,"UID",os.getuid()):
                result=game.observe(command or program(),dict(os.environ),Path.cwd(),r,checker,PROBE,timeout=kwargs.pop("timeout",2),grace=.08,emit=events.append,**kwargs)
            return result,events
        finally:
            if worker:worker.join(timeout=1)
            os.close(r)
            if control!="eof":os.close(w)
    def test_natural_exit_zero_keeps_owned_identity_and_shm_not_physical_input(self):
        value,events=self.run_owned()
        self.assertEqual(value["status"],"completed");self.assertEqual(value["child_returncode"],0);self.assertEqual(value["termination"],"natural")
        self.assertTrue(value["cleanup_confirmed"] and value["reaped"] and value["group_reclaimed"] and value["pipes_drained"])
        self.assertFalse(value["term_sent"] or value["kill_sent"]);self.assertEqual(events[0]["child"],value["child"])
        self.assertEqual(value["physical_input"],"not_run")
    def test_failure_code_signal_and_bad_app_id_preserve_the_original_outcome(self):
        for command,rc in ((program("raise SystemExit(7)"),7),(program("os.kill(os.getpid(),signal.SIGUSR1)"),-signal.SIGUSR1),(program(body=trace("PRIVATE")),0)):
            value,_=self.run_owned(command);self.assertEqual(value["status"],"failed");self.assertEqual(value["child_returncode"],rc)
            self.assertTrue(value["cleanup_confirmed"]);self.assertNotIn("PRIVATE",json.dumps(value))
    def test_cancel_eof_timeout_and_lease_failure_only_stop_owned_group(self):
        unrelated=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"],start_new_session=True)
        try:
            for control,expected in ((b'{"type":"cancel"}\n',"HOST_CANCEL"),("eof","CONTROL_EOF"),(b'{"type":"arbitrary","pid":1}\n',"CONTROL_INVALID"),(None,"TIMEOUT")):
                value,_=self.run_owned(program("time.sleep(20)"),control=control,timeout=.14)
                self.assertEqual(value["cancel_reason"],expected);self.assertEqual(value["child_returncode"],-signal.SIGTERM)
                self.assertTrue(value["cleanup_confirmed"]);self.assertIsNone(unrelated.poll())
            def gone():raise CaptureError("PRIVATE")
            value,_=self.run_owned(program("time.sleep(20)"),checker=gone)
            self.assertEqual(value["cancel_reason"],"LEASE_CHANGED");self.assertIsNone(unrelated.poll())
        finally:unrelated.terminate();unrelated.wait(timeout=2)
    def test_term_ignored_is_killed_without_changing_original_signal(self):
        value,_=self.run_owned(program("signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)"),timeout=.2)
        self.assertEqual(value["child_returncode"],-signal.SIGKILL);self.assertTrue(value["term_sent"] and value["kill_sent"] and value["cleanup_confirmed"])


class InstalledAssetsTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.temporary=tempfile.TemporaryDirectory(prefix="freedoom-run-assets-");self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.home=self.root/"files/home";self.home.mkdir(parents=True,mode=0o700)
        members={name:("fixture "+name).encode() for name in assets.source.MEMBERS};demo=b"demo"
        self.stack.enter_context(patch.object(assets.source,"MEMBERS",{name:(len(data),assets.sha(data)) for name,data in members.items()}))
        self.stack.enter_context(patch.object(assets.source,"DEMO_BYTES",len(demo)));self.stack.enter_context(patch.object(assets.source,"DEMO_SHA",assets.sha(demo)))
        prepared=self.root/"prepared";assets.source.create_bundle(prepared,RUN,members,demo)
        self.pin=assets.sha((prepared/"manifest.json").read_bytes());raw,manifest,identities,payload=assets.validate_prepared(prepared,self.pin)
        self.binary=self.home.parent/"usr/bin/chocolate-doom";self.binary.parent.mkdir(parents=True,mode=0o700)
        data=bytearray(64);data[:7]=b"\x7fELF\x02\x01\x01";struct.pack_into("<H",data,18,183);self.binary.write_bytes(data);self.binary.chmod(0o700)
        self.engine_pin=assets.sha(data)
        self.stack.enter_context(patch.object(assets,"ENGINE_SHA",self.engine_pin));self.stack.enter_context(patch.object(assets,"ENGINE_BYTES",64))
        self.stack.enter_context(patch.object(game,"UID",os.getuid()))
        status=self.home.parent/"usr/var/lib/dpkg/status";status.parent.mkdir(parents=True)
        status.write_bytes(b"Package: chocolate-doom\nStatus: install ok installed\nVersion: 3.1.1\n\n");status.chmod(0o600)
        original=assets.engine
        self.stack.enter_context(patch.object(assets,"engine",lambda home,pin,**_:original(home,pin,uid=os.getuid())))
        wire=b"".join(assets.stream_chunks(prepared,raw,self.pin,self.engine_pin,identities,payload));r,w=os.pipe()
        try:
            os.write(w,wire);os.close(w);reader=assets.Reader(r,timeout=2)
            try:assets.install(reader,self.home,uid=os.getuid())
            finally:reader.close()
        finally:os.close(r)
        self.directory=self.home/".local/share/eidolon/freedoom/0.13.0"/RUN
    def checked(self):return game.installed(self.home,RUN,self.pin,self.engine_pin)
    def test_all_pins_receipt_new_state_and_fixed_invocation(self):
        checked=self.checked();state=game.new_state(self.home,RUN,PROBE,checked)
        args=game.invocation(self.home,self.directory,state)
        self.assertEqual(args[0],str(self.binary));self.assertIn("-playdemo",args);self.assertNotIn("-timedemo",args)
        self.assertEqual(args[-1],str(self.directory/"assets/eidlsmok.lmp"));self.assertEqual((state/"chocolate-doom.cfg").read_bytes(),assets.source.EXTRA_CONFIG)
        self.assertEqual(state.stat().st_mode&0o777,0o700);self.assertEqual((state/"default.cfg").stat().st_mode&0o777,0o600)
        (state/"chocolate-doom.cfg").write_bytes(b"game may save this config")
        game.check_assets(self.home,checked,self.engine_pin)
        with self.assertRaisesRegex(InstallError,"RUN_EXISTS"):game.new_state(self.home,RUN,PROBE,checked)
        env=game.environment(self.home,state,dict(WAYLAND_DISPLAY="verified-socket",XDG_RUNTIME_DIR="verified-runtime"),PROBE)
        self.assertEqual(env["LIBGL_KOPPER_DISABLE"],"1");self.assertEqual(env["SDL_VIDEO_WAYLAND_WMCLASS"],game.app_id(PROBE))
        self.assertNotIn("LD_PRELOAD",env);self.assertEqual(env["SDL_VIDEODRIVER"],"wayland");self.assertEqual(env["HOME"],str(state/"home"))
        with self.assertRaisesRegex(InstallError,"REQUEST_INVALID"):game.environment(self.home,state,dict(WAYLAND_DISPLAY="x",XDG_RUNTIME_DIR="y",LD_PRELOAD="arbitrary"),PROBE)
    def test_receipt_corruption_boolean_size_license_and_symlink_are_rejected(self):
        path=self.directory/"install-receipt.json";raw=path.read_bytes();value=json.loads(raw);value["files"]["config/default.cfg"]=True;path.write_bytes(assets.canonical(value))
        with self.assertRaisesRegex(InstallError,"ASSET_RECEIPT_INVALID"):self.checked()
        path.write_bytes(raw+b" "*16384)
        with self.assertRaisesRegex(InstallError,"ASSET_RECEIPT_INVALID"):self.checked()
        path.write_bytes(raw);license=self.directory/"assets/COPYING.txt";license.unlink();license.symlink_to("CREDITS.txt")
        with self.assertRaises(OSError):self.checked()
    def test_changed_engine_asset_and_uid_reject_before_execution(self):
        checked=self.checked();path=self.directory/"assets/COPYING.txt";original=path.read_bytes();path.write_bytes(b"changed")
        with self.assertRaisesRegex(InstallError,"ASSET_CHANGED"):game.check_assets(self.home,checked,self.engine_pin)
        path.write_bytes(original)
        self.binary.write_bytes(b"changed");self.binary.chmod(0o700)
        with self.assertRaisesRegex(InstallError,"ENGINE_UNSAFE"):self.checked()
        with patch.object(game,"UID",os.getuid()+1),self.assertRaisesRegex(InstallError,"DIRECTORY_UNSAFE"):self.checked()
    def test_request_runs_real_owned_fixture_and_saves_strict_report_without_rewriting_assets(self):
        request=dict(schema=1,probe_id=PROBE,asset_run_id=RUN,manifest_sha256=self.pin,engine_sha256=self.engine_pin,binding=dict(uid=os.getuid(),session="17"))
        original={name:path.read_bytes() for name,path in ((str(p.relative_to(self.directory)),p) for p in self.directory.rglob("*") if p.is_file())}
        r,w=os.pipe();events=[];lease_calls=[]
        try:
            with patch.object(game,"validate_runtime",return_value={"fixed_runtime":"known"}),patch.object(game,"invocation",return_value=program()), \
                 patch.object(game,"game_identity",side_effect=lambda pid,*_:game.child_identity(pid)):
                value=game.run_request(request,self.home,r,events.append,capture=lambda *_:dict(environment=dict(WAYLAND_DISPLAY="verified",XDG_RUNTIME_DIR="verified")),
                    lease_checker=lambda *args:lease_calls.append(args))
        finally:os.close(r);os.close(w)
        self.assertEqual(value["status"],"completed");self.assertTrue(value["assets_unchanged"] and value["runtime_unchanged"] and value["lease_current"])
        self.assertGreaterEqual(len(lease_calls),2);self.assertEqual([v["phase"] for v in events],["validated","started"])
        self.assertEqual(host.validate_result(value,request),value)
        saved=self.home/".local/state/eidolon/freedoom"/RUN/PROBE/"report.json"
        self.assertEqual(json.loads(saved.read_text()),value);self.assertEqual(saved.stat().st_mode&0o777,0o600)
        self.assertEqual({str(p.relative_to(self.directory)):p.read_bytes() for p in self.directory.rglob("*") if p.is_file()},original)
        changed=copy.deepcopy(value);changed["cleanup_confirmed"]=False
        with self.assertRaises(CaptureError):host.validate_result(changed,request)
        with self.assertRaisesRegex(InstallError,"REQUEST_INVALID"):
            game.run_request(dict(request,environment={"LD_PRELOAD":"arbitrary"}),self.home,-1,events.append)
    def test_actual_proc_executable_must_match_the_fixed_engine_identity(self):
        child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"],start_new_session=True)
        try:
            with self.assertRaisesRegex(InstallError,"CHILD_IDENTITY_UNVERIFIED"):game.game_identity(child.pid,self.home,self.checked())
        finally:child.terminate();child.wait(timeout=2)
    def test_sdl_canonical_file_and_alias_are_checked_with_fixed_runtime_pins(self):
        directory=self.home.parent/"usr/lib";directory.mkdir(mode=0o700);target=directory/"libSDL2-2.0.so";target.write_bytes(b"known SDL");target.chmod(0o700)
        alias=directory/"libSDL2.so";alias.symlink_to(target.name)
        with patch.object(game,"SDL_BYTES",9),patch.object(game.egl,"validate_runtime",return_value={"known":True}) as validate:
            self.assertEqual(game.validate_runtime(self.home),{"known":True});self.assertEqual(validate.call_args.kwargs["pins"][target.name],game.SDL_SHA)
            target.chmod(0o755)
            with self.assertRaisesRegex(InstallError,"RUNTIME_LIBRARY_CHANGED"):game.validate_runtime(self.home)
            target.chmod(0o700);alias.unlink();alias.symlink_to("arbitrary.so")
            with self.assertRaises(OSError):game.validate_runtime(self.home)


class HostTests(unittest.TestCase):
    def request(self):return dict(schema=1,probe_id=PROBE,asset_run_id=RUN,manifest_sha256="c"*64,engine_sha256=assets.ENGINE_SHA,binding=dict(session="17",uid=10158))
    def test_bundle_fixed_command_budget_import_and_source_guards(self):
        command=host.remote_command();self.assertLessEqual(len(shlex.join(command).encode()),49*1024)
        self.assertEqual(command[:4],["run-as","com.termux","/data/data/com.termux/files/usr/bin/python3","-c"])
        program=host.remote_program().rsplit("raise SystemExit",1)[0]+"print(callable(sys.modules['freedoom_run'].run_request))\n"
        value=subprocess.run([sys.executable,"-"],input=program.encode(),capture_output=True,timeout=5)
        self.assertEqual(value.returncode,0,value.stderr.decode());self.assertEqual(value.stdout,b"True\n")
        self.assertIn("scripts/quest-run-sdl-probe",host.source_hashes())
    def test_strict_wire_never_accepts_body_or_forged_completed_state(self):
        request=self.request();events=host.Events(request)
        validated=dict(phase="validated",case=game.CASE,profile=assets.source.PROFILE,app_id=game.app_id(PROBE),session="17")
        for value in (dict(validated,body="PRIVATE"),dict(phase="blocked",reason="PRIVATE /path"),dict(phase="finished",result={})):
            with self.assertRaises(CaptureError):host.Events(request).receive(value)
        events.receive(validated);events.receive(dict(phase="started",uid=game.UID,app_id=game.app_id(PROBE),child=dict(pid=71,start="123",group=71)))
        with self.assertRaises(CaptureError):events.receive(dict(phase="finished",result=dict(status="completed",body="PRIVATE")))
    def test_early_remote_refusal_keeps_original_rc_and_closed_stdin_cancel_is_safe(self):
        program="import json;print(json.dumps(dict(phase='blocked',reason='ENGINE_MISSING')),flush=True);raise SystemExit(2)"
        value=cli.transport([sys.executable,"-c",program],self.request(),lambda:None,lambda:False,timeout=2,tail=.01)
        self.assertEqual(value["remote_returncode"],2);self.assertEqual(value["blocked_reason"],"ENGINE_MISSING")
        self.assertTrue(value["host_reaped"] and value["host_pipes_drained"]);self.assertIsNone(value["transport_failure"])
    def test_transport_cancel_waits_for_owned_remote_original_failure_and_discards_stderr(self):
        program=("import os,sys,json,time\nos.write(2,b'PRIVATE /path')\nx=sys.stdin.buffer.readline()\n"
                 "sys.stdin.buffer.readline()\nprint(json.dumps(dict(phase='blocked',reason='HOST_CANCEL')),flush=True)\nraise SystemExit(2)")
        def changed():raise CaptureError("APK_BINDING_CHANGED")
        value=cli.transport([sys.executable,"-c",program],self.request(),changed,lambda:False,timeout=2,tail=.01)
        self.assertEqual(value["remote_returncode"],2);self.assertTrue(value["host_reaped"] and value["host_pipes_drained"])
        self.assertEqual(value["monitor_failure"],"APK_BINDING_CHANGED");self.assertNotIn("PRIVATE",json.dumps(value))
    def test_sdk_gate_rejects_wrong_appid_transient_foot_loss_and_resource_leftovers(self):
        baseline=dict(mapped=["1"])
        samples=[dict(mapped=["1","2"],expected_windows=["2"],other_windows=[]),dict(mapped=["1"],expected_windows=[],other_windows=[])]
        summary=dict(status="observed",additional_windows=["2"],all_retired=True)
        with patch.object(host.sdk,"summarize_sdk",side_effect=lambda *_:dict(summary)),patch.object(host.sdk,"sdk_resources_retired",return_value=True):
            self.assertEqual(host.summarize_sdk(baseline,samples)["status"],"observed")
            other=copy.deepcopy(samples);other[0].update(expected_windows=[],other_windows=["2"])
            self.assertEqual(host.summarize_sdk(baseline,other)["status"],"not_verified")
            lost=copy.deepcopy(samples);lost[0]["mapped"]=["2"]
            self.assertEqual(host.summarize_sdk(baseline,lost)["status"],"not_verified")
        with patch.object(host.sdk,"summarize_sdk",side_effect=lambda *_:dict(summary)),patch.object(host.sdk,"sdk_resources_retired",return_value=False):
            self.assertEqual(host.summarize_sdk(baseline,samples)["status"],"not_verified")


if __name__=="__main__":unittest.main()
