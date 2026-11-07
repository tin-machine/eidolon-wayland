import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
import argparse
import contextlib
import io
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
import gtk_interaction_run as gui
import gtk_interaction_evidence as proof
from sdl_lease_capture import CaptureError, canonical

loader=importlib.machinery.SourceFileLoader("gtk_live_cli",str(SCRIPTS/"quest-run-gtk-interaction"))
spec=importlib.util.spec_from_loader(loader.name,loader);cli=importlib.util.module_from_spec(spec);loader.exec_module(cli)
NONCE="a"*32


def client(case="input",phase="final",sequence=1,uid=10158,pid=71):
    row={name:0 for name in gui.COUNTERS};row.update({name:False for name in gui.BOOLS})
    row.update(schema=1,probe="eidolon-gtk3-interaction-v1",run_id=NONCE,case=case,app_id=gui.app_ids(NONCE)[0],dialog_app_id=gui.app_ids(NONCE)[1],
               pid=pid,uid=uid,sequence=sequence,phase=phase,reason="completed" if phase=="final" else "running",complete=phase=="final",
               logical_size=[640,360],entry_rect=[24,58,592,56],button_rect=[24,130,592,206],main_draws=1)
    if phase=="final":
        if case=="input":row.update(a_down=1,a_up=1,delete_down=1,delete_up=1,entry_changes=2,button_clicks=1,token_seen=True,delete_seen=True)
        if case=="dialog":row.update(dialog_opened=1,dialog_closed=1,dialog_responses=1,dialog_parent_set=True,dialog_draws=1)
        if case=="resize":row.update(resize_large_seen=True,resize_return_seen=True)
    return row


def trace(dialog=False,unknown=False):
    def top(base,app,parent=None):
        return (f'xdg_wm_base@4.get_xdg_surface(new id xdg_surface@{base+5}, wl_surface@{base+6})\n'
                f'xdg_surface@{base+5}.get_toplevel(new id xdg_toplevel@{base+7})\n'
                f'xdg_toplevel@{base+7}.set_app_id("{app}")\n'
                +(f'xdg_toplevel@{base+7}.set_parent(xdg_toplevel@{parent})\n' if parent else '')+
                f'wl_shm_pool@{base+8}.create_buffer(new id wl_buffer@{base+9}, 0, 640, 360, 2560, 0)\n'
                f'wl_surface@{base+6}.attach(wl_buffer@{base+9}, 0, 0)\nwl_surface@{base+6}.commit()\n')
    value=top(0,"PRIVATE" if unknown else gui.app_ids(NONCE)[0])
    if dialog:value+=top(20,gui.app_ids(NONCE)[1],7)
    return value


class ParserTests(unittest.TestCase):
    def parser(self,case="input"):
        result=gui.FixtureReports(NONCE,case,lambda _:None);result.pid=71;return result
    def test_fixed_cases_require_final_counters_and_reject_body_or_wrong_owner(self):
        for case in gui.CASES:
            parser=self.parser(case);row=client(case);parser.feed(canonical(row)+b"\n")
            self.assertTrue(parser.summary()["complete"])
        for change in (lambda x:x.update(text="PRIVATE"),lambda x:x.update(uid=10159),lambda x:x.update(pid=72),
                       lambda x:x.update(host_selftest=True),lambda x:x.update(app_id="PRIVATE")):
            row=client();change(row)
            with self.assertRaises(CaptureError):self.parser().feed(canonical(row)+b"\n")
    def test_counter_regression_truncation_and_bad_exit_are_not_complete(self):
        parser=self.parser();row=client(phase="state");row["a_down"]=1;parser.feed(canonical(row)+b"\n")
        row["a_down"]=0;row["sequence"]=2
        with self.assertRaises(CaptureError):parser.feed(canonical(row)+b"\n")
        parser=self.parser();parser.feed(canonical(client())[:-1]);self.assertFalse(parser.summary()["complete"])
        row=client();row["button_clicks"]=0;parser=self.parser();parser.feed(canonical(row)+b"\n");self.assertFalse(parser.summary()["complete"])
    def test_main_dialog_parent_and_shm_are_distinguished_from_popup(self):
        parser=gui.InteractionTrace(NONCE);parser.feed(trace(True).encode());summary=parser.summary()
        self.assertEqual(summary["fixture_shm_commits"],dict(main=1,dialog=1));self.assertTrue(summary["dialog_parent_observed"])
        self.assertEqual(summary["popup_count"],0)
        parser.feed(b'xdg_surface@45.get_popup(new id xdg_popup@46, xdg_surface@5, xdg_positioner@47)\n')
        self.assertEqual(parser.summary()["popup_count"],1)
        parser=gui.InteractionTrace(NONCE);parser.feed(trace(unknown=True).encode())
        self.assertEqual(parser.summary()["unknown_app_ids"],1);self.assertNotIn("PRIVATE",json.dumps(parser.summary()))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.old=os.umask(0o077);self.addCleanup(os.umask,self.old)
        self.temp=tempfile.TemporaryDirectory(prefix="gtk-live-artifact-");self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/"home";self.home.mkdir(mode=0o700)
        self.directory=self.home/".local/state/eidolon/nx15-fixture"/("b"*32);self.directory.mkdir(mode=0o700,parents=True)
        self.pins={}
        for name in ("gtk3-interaction-fixture.c","build-gtk3-fixture.py"):
            data=(name+" fixed source").encode();(self.directory/name).write_bytes(data);(self.directory/name).chmod(0o600)
            self.pins[name]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        manifest=dict(schema=1,kind="eidolon-gtk3-fixture-source-v1",run_id="b"*32,files=self.pins)
        data=canonical(manifest)+b"\n";(self.directory/"manifest.json").write_bytes(data)
        build=self.directory/"build";build.mkdir(mode=0o700)
        elf=bytearray(64);elf[:6]=b"\x7fELF\x02\x01";elf[18:20]=(183).to_bytes(2,"little")
        (build/"gtk3-fixture").write_bytes(elf);(build/"gtk3-fixture").chmod(0o700)
        self.request=dict(run_id="b"*32,manifest_sha256=hashlib.sha256(data).hexdigest(),binary_sha256=hashlib.sha256(elf).hexdigest())
        receipt=dict(status="built",executed=False,gtk_version="3.24.52",manifest_sha256=self.request["manifest_sha256"],
                     binary_sha256=self.request["binary_sha256"],binary_bytes=64,elf_machine=183)
        (build/"build.json").write_bytes(canonical(receipt)+b"\n")
    def validate(self):
        with patch.object(gui,"UID",os.getuid()),patch.object(gui,"SOURCE_PINS",self.pins),patch.object(gui,"validate_gtk_runtime"):
            return gui.validate_artifacts(self.request,self.home)
    def test_known_hash_source_and_receipt_validate_then_tamper_is_rejected(self):
        binary,_=self.validate();self.assertEqual(binary,self.directory/"build/gtk3-fixture")
        binary.write_bytes(b"CHANGED");binary.chmod(0o700)
        with self.assertRaisesRegex(CaptureError,"GTK_BINARY_CHANGED"):self.validate()
    def test_changed_manifest_mode_and_symlink_cannot_be_used(self):
        (self.directory/"gtk3-interaction-fixture.c").write_bytes(b"CHANGED")
        with self.assertRaisesRegex(CaptureError,"GTK_SOURCE_CHANGED"):self.validate()


class OwnedNaturalTests(unittest.TestCase):
    def test_fixed_exec_natural_final_keeps_foot_lease_and_maps_only_owned_group(self):
        with tempfile.TemporaryDirectory(prefix="gtk-live-exec-") as temporary:
            home=Path(temporary)/"home";home.mkdir(mode=0o700)
            binary=Path(temporary)/"fixed-fixture"
            row=client(uid=os.getuid());source=(f'#!{sys.executable}\nimport json,os\nx={row!r}\nx["pid"]=os.getpid()\n'
                  +f'os.write(2,{trace().encode()!r})\nprint(json.dumps(x),flush=True)\n')
            binary.write_text(source);binary.chmod(0o700)
            request=dict(binding=dict(uid=os.getuid()),run_id="b"*32,binary_sha256="c"*64,manifest_sha256="d"*64,case="input",nonce=NONCE)
            captured=dict(environment=dict(WAYLAND_DISPLAY="unused",XDG_RUNTIME_DIR=temporary))
            read_fd,write_fd=os.pipe();events=[]
            try:
                with patch.object(gui,"UID",os.getuid()),patch.object(gui,"capture_remote",return_value=captured), \
                     patch.object(gui,"validate_artifacts",return_value=(binary,None)),patch.object(gui,"live_binding") as guard:
                    result=gui.run_operator(request,home,read_fd,events.append)
                    self.assertGreaterEqual(guard.call_count,2)
                self.assertEqual(result["status"],"observed");self.assertEqual(result["child_returncode"],0)
                self.assertEqual(result["termination"],"natural");self.assertFalse(result["term_sent"] or result["kill_sent"])
                self.assertTrue(result["cleanup_confirmed"]);self.assertEqual(result["trace"]["fixture_shm_commits"],dict(main=1,dialog=0))
            finally:os.close(read_fd);os.close(write_fd)

    def test_natural_failure_and_malformed_body_keep_original_failure_and_owned_cleanup(self):
        from gtk_probe_run import observe_owned
        for raw,code in ((b"PRIVATE /body\n",7),(b"",7)):
            parser=gui.FixtureReports(NONCE,"input",lambda _:None,uid=os.getuid())
            read_fd,write_fd=os.pipe()
            try:
                def event(value):parser.pid=value["child_pid"]
                command=[sys.executable,"-c",f"import os;os.write(1,{raw!r});raise SystemExit({code})"]
                result=observe_owned(command,dict(PATH=os.environ["PATH"]),read_fd,lambda:None,event,
                                     natural=True,stdout_handler=parser.feed,observation_seconds=2,grace_seconds=.1)
                self.assertEqual(result["status"],"failed");self.assertTrue(result["cleanup_confirmed"])
                self.assertIn(result["child_returncode"],{7,-signal.SIGTERM})
                self.assertNotIn("PRIVATE",json.dumps(result));self.assertFalse(parser.summary()["complete"])
            finally:os.close(read_fd);os.close(write_fd)

    def test_natural_timeout_is_not_observation_complete(self):
        from gtk_probe_run import observe_owned
        read_fd,write_fd=os.pipe()
        try:
            result=observe_owned([sys.executable,"-c","import time;time.sleep(30)"],dict(PATH=os.environ["PATH"]),read_fd,lambda:None,lambda _:None,
                                 natural=True,observation_seconds=.1,grace_seconds=.1)
            self.assertEqual(result["stop_reason"],"OBSERVATION_TIMEOUT");self.assertTrue(result["timed_out"])
            self.assertEqual(result["child_returncode"],-signal.SIGTERM);self.assertTrue(result["cleanup_confirmed"])
        finally:os.close(read_fd);os.close(write_fd)

    def test_malformed_output_during_stop_is_discarded_and_cannot_interrupt_reaping(self):
        from gtk_probe_run import observe_owned
        read_fd,write_fd=os.pipe();calls=[]
        def broken(data):calls.append(len(data));raise CaptureError("GTK_REPORT_IDENTITY")
        try:
            source="import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);os.write(1,b'PRIVATE /body\\n');time.sleep(.05)\nwhile True:os.write(1,b'PRIVATE /body\\n'*128)\n"
            result=observe_owned([sys.executable,"-c",source],dict(PATH=os.environ["PATH"]),read_fd,lambda:None,lambda _:None,
                                 natural=True,stdout_handler=broken,observation_seconds=2,grace_seconds=.1)
            self.assertEqual(len(calls),1);self.assertTrue(result["cleanup_confirmed"] and result["kill_sent"])
            self.assertEqual(result["error"],"GTK_REPORT_IDENTITY");self.assertNotIn("PRIVATE",json.dumps(result))
        finally:os.close(read_fd);os.close(write_fd)


def raw_resources(runtime, retired=False):
    runtime.setdefault("scene","3")
    keys=[dict(t["key"]) for t in runtime["render"]["targets"]]
    n=len(keys)
    adapter=dict(scene="3",created=n,destroyed=0,bindings=n,listeners=n,decoration_entities=n,
        window_controls=dict(entity_owners=n*6,listener_owners=n*2),
        window_mode_badges={name:n for name in ("entity_owners","object_owners","mesh_owners","material_owners","texture_owners","pixel_slots_in_use")},targets=[],retired=[])
    adapter["window_mode_badges"]["cleanup_pending_owners"]=0
    for key in keys:
        number=int(key["window"])*100
        adapter["targets"].append(dict(target=key,entity=str(number),visual_entity=str(number+1),listener_owned=True,input_owned=True,
            retiring=False,sdk_disposed=False,disposal_failed=False,move={},
            controls=dict(failures=0,buttons=[dict(entity=str(number+2)),dict(entity=str(number+3))]),
            mode_badge=dict(entity=str(number+4),object_ready=True,failures=0)))
    if retired:
        for window in ("5","6"):
            adapter["retired"].append(dict(runtime="9",target=dict(session="2",window=window,scene="3",generation="4"),entity=str(int(window)*100),
                sdk_disposed=True,sdk_binding_released=True,disposal_failed=False))
    runtime.update(panels=[dict(target=key,action="create",applied=True,error=None) for key in keys],shutdown=dict(sdk_bindings=n),
        window_slots=dict(occupied=n,cleanup_required=False,unassigned=[],entries=[dict(lease=dict(runtime="9",session="2",token=k["window"],position="center"),state=dict(phase="bound",window=k["window"])) for k in keys]))
    return adapter


def sdk(main=True,dialog=False,retired=False,size=None,phase="idle"):
    rows=[dict(window="1",role="baseline",logical_size=dict(width=1024,height=768),resize_phase="idle")]
    targets=[dict(key=dict(session="2",window="1",scene="3",generation="4"),frames=4,content_frame="1")]
    if main and not retired:
        rows.append(dict(window="5",role="main",logical_size=size or dict(width=640,height=360),resize_phase=phase))
        targets.append(dict(key=dict(session="2",window="5",scene="3",generation="4"),frames=2,content_frame="2"))
    if dialog:
        rows.append(dict(window="6",role="dialog",logical_size=dict(width=320,height=180),resize_phase="idle"))
        targets.append(dict(key=dict(session="2",window="6",scene="3",generation="4"),frames=1,content_frame="1"))
    runtime=dict(render=dict(targets=targets));adapter=raw_resources(runtime,retired)
    resource=proof.resources(dict(runtime=dict(handle="9",runtime=runtime),adapter=adapter))
    return dict(sequence="1",windows=rows,targets=targets,resources=resource,epoch=1,focused_window="5",pressed=0,pointer_pressed=0,delivered=4,pointer_buttons=4)


class EvidenceTests(unittest.TestCase):
    def test_input_requires_client_counter_android_completion_retirement_and_foot(self):
        samples=[sdk(main=False),sdk(),sdk(retired=True)];android=dict(result="completed")
        self.assertEqual(proof.summarize(samples,{"1"},"input",[client()],android)["status"],"observed")
        self.assertEqual(proof.summarize(samples[:-1],{"1"},"input",[client()],android)["status"],"not_verified")
        bad=copy.deepcopy(samples);bad[-1]["windows"]=[]
        self.assertFalse(proof.summarize(bad,{"1"},"input",[client()],android)["baseline_preserved"])
        self.assertEqual(proof.summarize(samples,{"1"},"input",[client()],None)["status"],"not_verified")
        bad=copy.deepcopy(samples);bad[1]["targets"]=[t for t in bad[1]["targets"] if t["key"]["window"]!="1"]
        self.assertFalse(proof.summarize(bad,{"1"},"input",[client()],android)["baseline_preserved"])
    def test_missing_third_target_or_wrong_resize_cannot_pass(self):
        samples=[sdk(main=False),sdk(),sdk(retired=True)]
        self.assertEqual(proof.summarize(samples,{"1"},"dialog",[client("dialog")],dict(result="completed"))["status"],"not_verified")
        self.assertEqual(proof.summarize([sdk(main=False),sdk(dialog=True),*samples[1:]],{"1"},"dialog",[client("dialog")],dict(result="completed"))["status"],"observed")
        self.assertFalse(proof.summarize(samples,{"1"},"resize",[client("resize")],dict(result="completed"))["sdk_resize_gate"])
        sizes=[sdk(main=False),sdk(size=dict(width=800,height=500),phase="applied"),sdk(phase="applied"),sdk(retired=True)]
        self.assertEqual(proof.summarize(sizes,{"1"},"resize",[client("resize")],dict(result="completed"))["status"],"observed")
    def test_missing_mapped_and_render_targets_cannot_hide_sdk_board_or_mode_leaks(self):
        samples=[sdk(main=False),sdk(),sdk(retired=True)]
        self.assertTrue(proof.summarize(samples,{"1"},"input",[client()],dict(result="completed"))["all_retired"])
        mutations=[lambda r:r["retired"].clear(),lambda r:r.update(sdk_bindings=2),
            lambda r:r["owners"].update(mode_texture_owners=2),lambda r:r["owners"].update(control_listeners=3),
            lambda r:r["board"].update(cleanup_required=True),lambda r:r["board"]["entries"].append(dict(runtime="9",session="2",window="5",phase="retiring")),
            lambda r:r["targets"][0].update(entity="101"),lambda r:r["targets"][0].update(mode_entity="999"),
            lambda r:r["retired"][0].update(disposal_failed=True),lambda r:r["retired"][0]["key"].update(generation="8"),
            lambda r:r["retired"][0].update(runtime="10"),lambda r:r["targets"][0].update(listener_owned=False)]
        for mutation in mutations:
            bad=copy.deepcopy(samples);mutation(bad[-1]["resources"])
            self.assertFalse(proof.summarize(bad,{"1"},"input",[client()],dict(result="completed"))["all_retired"])
    def test_preexisting_leaks_cannot_be_subtracted_as_baseline_resources(self):
        samples=[sdk(main=False),sdk(),sdk(retired=True)]
        for row in (samples[0],samples[-1]):row["resources"]["owners"]["mode_texture_owners"]+=1
        self.assertFalse(proof.summarize(samples,{"1"},"input",[client()],dict(result="completed"))["all_retired"])
        value=sdk(main=False);value["resources"]["board"]["entries"][0]["runtime"]="10"
        self.assertFalse(proof.baseline_resources(value["resources"],{"1"},[t["key"] for t in value["targets"]]))
    def test_resource_capture_selects_only_ownership_metadata_not_trace_or_input_body(self):
        runtime=dict(render=dict(targets=sdk()["targets"]));adapter=raw_resources(runtime)
        adapter["trace"]=[dict(body="PRIVATE")];adapter["targets"][0]["decoration"]="PRIVATE"
        captured=proof.resources(dict(runtime=dict(handle="9",runtime=runtime),adapter=adapter))
        self.assertNotIn("PRIVATE",json.dumps(captured))
        del adapter["targets"][0]["listener_owned"]
        with self.assertRaises(KeyError):proof.resources(dict(runtime=dict(handle="9",runtime=runtime),adapter=adapter))
    def test_click_geometry_is_fixed_and_never_guessed_when_outside_entry(self):
        row=client(phase="ready");self.assertTrue(proof.ready(sdk(),row,"input"))
        row["entry_rect"]=[24,100,592,56]
        with self.assertRaisesRegex(CaptureError,"GTK_GEOMETRY_UNSUPPORTED"):proof.ready(sdk(),row,"input")
    def test_late_android_receipt_cannot_claim_another_runtime_scene_or_target(self):
        row=dict(runtime="9",session="2",scene="3",foot_windows=["1"],main_window="5",target=dict(session="2",window="5",scene="3",generation="4"),steps=[])
        binding=dict(handle="9",session="2")
        proof.receipt_binding(row,binding,"3",{"1"},{"5"},set())
        for key,value in (("runtime","10"),("scene","4"),("main_window","7")):
            changed=copy.deepcopy(row);changed[key]=value
            with self.assertRaises(CaptureError):proof.receipt_binding(changed,binding,"3",{"1"},{"5"},set())
        changed=copy.deepcopy(row);changed["target"]["window"]="1"
        with self.assertRaisesRegex(CaptureError,"GTK_ANDROID_TARGET_CHANGED"):proof.receipt_binding(changed,binding,"3",{"1"},{"5"},set())
    def test_close_waits_for_current_content_and_android_sequence(self):
        value=sdk();value["windows"][1]["frame"]="3"
        self.assertIsNone(proof.current_frame(value,"main"))
        value["targets"][1]["content_frame"]="3"
        self.assertEqual(proof.current_frame(value,"main"),3)
        row=dict(case="gtk-input",nonce=NONCE,result="completed",phase="COMPLETE",reason=None,scope="target_bound_synthetic_native",
                 physical_input_verified=False,ime_verified=False,host_physical_filter_verified=False,runtime="9",session="2",scene="3",
                 main_window="5",target=dict(session="2",window="5",scene="3",generation="4"),input_epoch=1,foot_windows=["1"],
                 steps=[dict(stage=stage,target=None,input_epoch=1,delivered=4) for stage in proof.STAGES["input"]])
        self.assertEqual(proof.android_receipt(dict(check=row),NONCE,"input"),row)
        row["steps"]=[]
        with self.assertRaisesRegex(CaptureError,"GTK_ANDROID_SEQUENCE"):proof.android_receipt(dict(check=row),NONCE,"input")
    def test_compressed_remote_command_and_fixed_receiver_only(self):
        source=cli.remote_source();compile(source,"gtk-interaction-remote","exec")
        command=__import__("shlex").join(["run-as","com.termux","/data/data/com.termux/files/usr/bin/python3","-c",source])
        self.assertLessEqual(len(command.encode()),49*1024)
        import ast,base64,zlib
        payload=next(node.value for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Constant) and isinstance(node.value,str))
        program=zlib.decompress(base64.b64decode(payload)).decode().rsplit("raise SystemExit(",1)[0]
        program+="print(all(callable(getattr(sys.modules['sdl_probe_run'],n,None)) for n in ('checked_file','group_live','live_binding')))\n"
        imported=subprocess.run([sys.executable,"-"],input=program.encode(),capture_output=True,timeout=5)
        self.assertEqual(imported.returncode,0);self.assertEqual(imported.stdout.strip(),b"True")


class HostJobTests(unittest.TestCase):
    def test_fixed_broadcast_counter_sdk_close_retirement_are_one_job(self):
        with tempfile.TemporaryDirectory(prefix="gtk-host-job-") as directory:
            root=Path(directory);marker=root/"step";state=dict(spawned=False,phase="before")
            def snapshot():
                phase=state["phase"];retired=phase=="retired";main=state["spawned"] and not retired
                content="3" if phase in {"operations","retired"} else "2"
                windows=[dict(window="1",app_id="baseline",life="Mapped",frame="1",logical_size=dict(width=1024,height=768),resize=dict(phase="idle"))]
                targets=[dict(key=dict(session="2",window="1",scene="3",generation="4"),frames=4,content_frame="1")]
                if main:
                    windows.append(dict(window="5",app_id=gui.app_ids(NONCE)[0],life="Mapped",frame=content,logical_size=dict(width=640,height=360),resize=dict(phase="idle")))
                    targets.append(dict(key=dict(session="2",window="5",scene="3",generation="4"),frames=4,content_frame=content))
                runtime=dict(scene="3",app=dict(session="2",windows=windows,input=dict(focus_window="5" if main else None,pressed=0,
                             delivered=4 if phase!="before" else 0,pointer=dict(pressed=0,buttons=4 if phase!="before" else 0))),
                             input_resets=1,render=dict(targets=targets))
                adapter=raw_resources(runtime,retired)
                result=dict(sequence="1" if phase=="before" else "2" if phase=="operations" else "3",runtime=dict(handle="9",runtime=runtime),adapter=adapter)
                if phase!="before":
                    stages=proof.STAGES["input"] if retired else proof.STAGES["input"][:-1]
                    result["check"]=dict(case="gtk-input",nonce=NONCE,result="completed" if retired else "running",phase="COMPLETE" if retired else "OPERATIONS_COMPLETE",reason=None,
                        scope="target_bound_synthetic_native",physical_input_verified=False,ime_verified=False,host_physical_filter_verified=False,runtime="9",session="2",scene="3",main_window="5",
                        target=dict(session="2",window="5",scene="3",generation="4"),input_epoch=1,foot_windows=["1"],
                        steps=[dict(stage=s,target=None,input_epoch=1,delivered=4) for s in stages])
                return result
            class Device:
                def __init__(self,*_):pass
                def validate(self):return dict(model="Quest 3",api=34)
                def adb(self,*args,**kwargs):
                    if args[:2]==("shell","pidof"):return "12345"
                    if "pm" in args:return "package:com.termux uid:10158\n"
                    if "broadcast" in args:
                        action=args[args.index("case")+1]
                        if action=="gtk-input":state["phase"]="operations";marker.write_text("operations")
                        if action=="gtk-close":state["phase"]="retired";marker.write_text("retired")
                    return ""
            original_popen=subprocess.Popen
            def spawn(*args,**kwargs):
                state["spawned"]=True
                ready=client(phase="ready");complete=client();complete["phase"]="state";complete["reason"]="running";complete["sequence"]=2
                final=client(sequence=3)
                remote=dict(schema=1,operator=gui.OPERATOR,status="observed",nonce=NONCE,binary_sha256="c"*64,manifest_sha256="d"*64,
                            cleanup_confirmed=True,reaped=True,group_reclaimed=True,pipes_drained=True)
                source=("import sys,json,os,time\nfrom pathlib import Path\nr=json.loads(sys.stdin.readline())\n"
                    f"marker=Path({str(marker)!r})\n"
                    "def emit(v):print(json.dumps(v),flush=True)\n"
                    f"emit(dict(phase='validated',nonce={NONCE!r}));emit(dict(phase='started',child_pid=os.getpid(),child_group=os.getpid()))\n"
                    f"emit(dict(phase='fixture',report={ready!r}))\n"
                    "while not marker.exists():time.sleep(.01)\n"
                    f"emit(dict(phase='fixture',report={complete!r}))\n"
                    "while marker.read_text()!='retired':time.sleep(.01)\n"
                    f"emit(dict(phase='fixture',report={final!r}));emit(dict(phase='finished',result={remote!r}))\n")
                return original_popen([sys.executable,"-c",source],**kwargs)
            args=argparse.Namespace(serial="fixture",case="input",run_id="b"*32,binary_sha256="c"*64,manifest_sha256="d"*64,artifact_root=root/"artifacts")
            binding=dict(handle="9",process_id=12345,session="2")
            with patch.object(cli.argparse.ArgumentParser,"parse_args",return_value=args),patch.object(cli,"QuestDevice",Device), \
                 patch.object(cli.secrets,"token_hex",return_value=NONCE),patch.object(cli,"digest_sources",return_value="e"*64), \
                 patch.object(cli,"select_snapshot",return_value=binding),patch.object(cli.subprocess,"Popen",side_effect=spawn), \
                 patch.object(cli.subprocess,"run",side_effect=lambda *a,**k:SimpleNamespace(returncode=0,stdout=canonical(snapshot()))),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(),0)
            report=json.loads(next((root/"artifacts").glob("*/result.json")).read_text())
            self.assertEqual(report["status"],"observed");self.assertTrue(report["evidence"]["all_retired"])
            self.assertEqual(report["physical_input"],"not_run")


class CancelDrainTests(unittest.TestCase):
    def drain(self, source):
        import selectors
        child=subprocess.Popen([sys.executable,"-c",source],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        selector=selectors.DefaultSelector();pending=bytearray();sizes=dict(stdout=0,stderr=0);events=[]
        for name in ("stdout","stderr"):
            pipe=getattr(child,name);os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,name)
        child.stdin.close()
        try:
            cli.drain_remote(selector,pending,sizes,events.append,timeout=2)
            child.wait(timeout=2)
            return events,sizes,child.returncode
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=2)
            selector.close();child.stdout.close();child.stderr.close()

    def test_late_cleanup_footer_after_cancel_is_drained_without_rewriting_failed_exit(self):
        events,sizes,code=self.drain("import sys,time,json;sys.stdin.read();time.sleep(.02);print(json.dumps(dict(phase='finished',cleanup_confirmed=True)),flush=True);sys.exit(23)")
        self.assertEqual(events,[dict(phase="finished",cleanup_confirmed=True)])
        self.assertGreater(sizes["stdout"],0);self.assertEqual(code,23)

    def test_partial_footer_and_oversize_error_stream_are_not_cleanup_proof(self):
        for source in ("import sys;sys.stdout.write('{\"phase\":\"finished\"}')",
                       "import sys;sys.stderr.write('x'*70000)"):
            with self.assertRaises(CaptureError):self.drain(source)


class ReceiptLedgerTests(unittest.TestCase):
    def row(self):
        return dict(runtime="9",session="2",scene="3",foot_windows=["1"],main_window="5",
                    target=dict(session="2",window="6",scene="3",generation="4"),steps=[],result="running")

    def ledger(self):
        return proof.ReceiptLedger(dict(handle="9",session="2"),"3",{"1"})

    def test_dialog_receipt_waits_for_same_full_target_in_later_persisted_snapshot(self):
        ledger=self.ledger();row=self.row()
        self.assertEqual(ledger.observe(sdk(),row,1.0),[])
        self.assertEqual(ledger.observe(sdk(),row,1.1),[])
        self.assertEqual(len(ledger.pending),1)
        self.assertEqual(ledger.observe(sdk(dialog=True),row,1.2),[row])
        self.assertEqual(ledger.pending,[])

    def test_unseen_or_wrong_generation_never_becomes_owned_and_expires(self):
        for generation in ("4","7"):
            ledger=self.ledger();row=self.row();row["target"]["generation"]=generation
            sample=sdk(dialog=generation=="7")
            self.assertEqual(ledger.observe(sample,row,1.0),[])
            with self.assertRaisesRegex(CaptureError,"GTK_ANDROID_TARGET_UNOBSERVED"):
                ledger.observe(sample,row,3.0)
        ledger=self.ledger();row=self.row();ledger.observe(sdk(),row,1.0)
        with self.assertRaisesRegex(CaptureError,"GTK_ANDROID_TARGET_UNOBSERVED"):
            ledger.observe(sdk(dialog=True),row,3.0)

    def test_other_runtime_scene_or_baseline_is_rejected_before_quarantine(self):
        for mutate in (lambda r:r.update(runtime="10"),lambda r:r["target"].update(scene="8"),
                       lambda r:r["target"].update(window="1")):
            row=self.row();mutate(row)
            with self.assertRaises(CaptureError):self.ledger().observe(sdk(),row,1.0)


if __name__=="__main__":unittest.main()
