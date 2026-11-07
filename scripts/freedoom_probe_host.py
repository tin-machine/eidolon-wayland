"""固定demo operatorの圧縮source、本文なしwire検査、同job SDK照合。"""
import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import re
import shlex
import zlib
from native_build import ROOT
from sdl_lease_capture import need, decimal
import freedoom_run as game

loader=importlib.machinery.SourceFileLoader("freedoom_sdl_sdk",str(ROOT/"scripts/quest-run-sdl-probe"))
spec=importlib.util.spec_from_loader(loader.name,loader);sdk=importlib.util.module_from_spec(spec);loader.exec_module(sdk)

MODULES=("sdl_probe_install","freedoom_probe_prepare","freedoom_install","sdl_lease_capture","sdl_probe_run","sdl_observation","egl_loaded_identity","egl_surfaceless_probe","freedoom_run")
MODULE_PATHS={name:"scripts/"+name+".py" for name in MODULES}
MODULE_PATHS["sdl_observation"]="scripts/termux/eidolon-sdl-probe.py"
OPERATOR_SOURCES=tuple(MODULE_PATHS.values())+("scripts/quest-run-freedoom-probe","scripts/freedoom_probe_host.py","scripts/gui_probe_sdk.py",
    "scripts/quest-run-sdl-probe","scripts/gtk_interaction_evidence.py","scripts/gtk_interaction_run.py","scripts/gtk_probe_run.py",
    "scripts/quest_device.py","scripts/native_build.py")


def source_hashes():
    return {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in OPERATOR_SOURCES}


def remote_program():
    value="import sys,types\n"
    for name in MODULES:
        data=(ROOT/MODULE_PATHS[name]).read_text()
        marker={"sdl_probe_run":"\ndef run_operator(","sdl_observation":"\ndef probe_report(","egl_surfaceless_probe":"\ndef observe_owned("}.get(name)
        if marker:
            need(marker in data,"REMOTE_SOURCE_SCHEMA");data=data.split(marker,1)[0]
        value+=f"m=types.ModuleType({name!r});sys.modules[m.__name__]=m;exec({data!r},m.__dict__)\n"
    return value+"raise SystemExit(sys.modules['freedoom_run'].remote_main())\n"


def remote_command():
    encoded=base64.b64encode(zlib.compress(remote_program().encode(),9)).decode("ascii")
    bootstrap=f"import base64,zlib;exec(zlib.decompress(base64.b64decode({encoded!r})))"
    command=["run-as","com.termux","/data/data/com.termux/files/usr/bin/python3","-c",bootstrap]
    need(len(shlex.join(command).encode())<=49*1024,"REMOTE_COMMAND_LIMIT")
    return command


def integer(value,low=0,high=2**63-1):
    return type(value) is int and low<=value<=high


def child_identity(value):
    return type(value) is dict and set(value)=={"pid","start","group"} and integer(value["pid"],1) and value["group"]==value["pid"] and decimal(value["start"])


def engine(value):
    fields={"package","version","sha256","bytes","uid","mode","nlink","elf_machine","identity"}
    return (type(value) is dict and set(value)==fields and value["package"]=="chocolate-doom" and value["version"]=="3.1.1" and
            value["sha256"]==game.assets.ENGINE_SHA and type(value["bytes"]) is int and value["bytes"]==game.assets.ENGINE_BYTES and
            value["uid"]==game.UID and value["mode"]==0o700 and type(value["nlink"]) is int and value["nlink"]==1 and value["elf_machine"]==183 and
            type(value["identity"]) is list and len(value["identity"])==5 and all(integer(v) for v in value["identity"]))


def wayland(value):
    flags={"complete"};numbers={"bytes","content_shm_commits","content_non_shm_commits","content_unknown_commits","content_buffer_releases",
        "content_frame_callbacks","protocol_errors","expected_app_id_events","unknown_app_id_events","matching_app_id_shm_commits"}
    return (type(value) is dict and set(value)==flags|numbers|{"content_shm_formats","bound_globals"} and
            all(type(value[k]) is bool for k in flags) and all(integer(value[k]) for k in numbers) and
            type(value["content_shm_formats"]) is list and len(value["content_shm_formats"])<=32 and all(integer(v,0,2**32-1) for v in value["content_shm_formats"]) and
            type(value["bound_globals"]) is dict and len(value["bound_globals"])<=128 and
            all(type(k) is str and re.fullmatch(r"[a-z0-9_]{1,100}",k) and integer(v,1,1000) for k,v in value["bound_globals"].items()))


RESULT_FIELDS={"status","case","profile","app_id","child","child_pid","child_group","child_returncode","termination","term_sent","kill_sent","timed_out",
    "cancel_reason","error","reaped","group_reclaimed","pipes_drained","cleanup_confirmed","shm_gate","wayland","output_bytes","physical_input","hmd","renderer_policy",
    "schema","operator","probe_id","asset_run_id","uid","manifest_sha256","engine","assets_unchanged","runtime_unchanged","lease_current","sdk","catalog","execution"}


def validate_result(value,request):
    need(type(value) is dict and set(value)==RESULT_FIELDS,"REMOTE_RESULT_INVALID")
    need(type(value["schema"]) is int and value["schema"]==1 and value["operator"]==game.OPERATOR and value["status"] in {"completed","failed"} and
        value["case"]==game.CASE and value["execution"]==game.CASE and value["profile"]==game.assets.source.PROFILE and value["app_id"]==game.app_id(request["probe_id"]) and
        value["probe_id"]==request["probe_id"] and value["asset_run_id"]==request["asset_run_id"] and value["manifest_sha256"]==request["manifest_sha256"] and value["uid"]==game.UID and
        value["sdk"]=="host_observation_required" and value["catalog"]=="not_modified" and value["physical_input"]==value["hmd"]=="not_run" and value["renderer_policy"]=="software_pinned_config","REMOTE_RESULT_INVALID")
    need(all(type(value[k]) is bool for k in ("term_sent","kill_sent","timed_out","reaped","group_reclaimed","pipes_drained","cleanup_confirmed","assets_unchanged","runtime_unchanged","lease_current")) and
        (value["child"] is None or child_identity(value["child"])) and integer(value["child_pid"],1) and value["child_group"]==value["child_pid"] and
        (value["child"] is None or value["child"]["pid"]==value["child_pid"]) and (value["child_returncode"] is None or integer(value["child_returncode"],-255,255)) and
        value["termination"] in {"unreaped","signal","natural"} and value["shm_gate"] in {"pass","fail"} and
        all(value[k] is None or value[k] in game.REASONS for k in ("error","cancel_reason")) and engine(value["engine"]) and wayland(value["wayland"]) and
        type(value["output_bytes"]) is dict and set(value["output_bytes"])=={"stdout","stderr"} and all(integer(v) for v in value["output_bytes"].values()),"REMOTE_RESULT_INVALID")
    if value["status"]=="completed":
        need(value["cleanup_confirmed"] and all(value[k] for k in ("reaped","group_reclaimed","pipes_drained","assets_unchanged","runtime_unchanged","lease_current")) and
            value["child_returncode"]==0 and value["termination"]=="natural" and value["shm_gate"]=="pass" and value["error"] is None and value["cancel_reason"] is None and
            value["child"] is not None and not any(value[k] for k in ("term_sent","kill_sent","timed_out")),"REMOTE_RESULT_INVALID")
    return value


class Events:
    def __init__(self,request):self.request=request;self.events=[];self.finished=None;self.blocked=None
    def receive(self,value):
        need(type(value) is dict and value.get("phase") in {"validated","started","finished","blocked"} and self.finished is None and self.blocked is None,"REMOTE_EVENT_INVALID")
        phase=value["phase"];expected=game.app_id(self.request["probe_id"])
        if phase=="validated":
            need(not self.events and set(value)=={"phase","case","profile","app_id","session"} and value["case"]==game.CASE and value["profile"]==game.assets.source.PROFILE and
                value["app_id"]==expected and value["session"]==self.request["binding"]["session"],"REMOTE_EVENT_INVALID")
        elif phase=="started":
            need(len(self.events)==1 and set(value)=={"phase","child","uid","app_id"} and value["uid"]==game.UID and value["app_id"]==expected and child_identity(value["child"]),"REMOTE_EVENT_INVALID")
        elif phase=="finished":
            need(set(value)=={"phase","result"} and self.events,"REMOTE_EVENT_INVALID");self.finished=validate_result(value["result"],self.request)
            if self.finished["status"]=="completed":need(len(self.events)==2,"REMOTE_EVENT_INVALID")
            if len(self.events)==2:need(self.finished["child"]==self.events[-1]["child"],"REMOTE_EVENT_INVALID")
            return
        else:
            need(set(value)=={"phase","reason"} and type(value["reason"]) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}",value["reason"]),"REMOTE_EVENT_INVALID")
            self.blocked=value["reason"];return
        need(len(self.events)<2,"REMOTE_EVENT_INVALID");self.events.append(value)


def sdk_sample(value,probe_id,baseline):
    row=sdk.sdk_sample(value);expected=game.app_id(probe_id)
    row["expected_windows"]=sorted(w["window"] for w in value["runtime"]["runtime"]["app"]["windows"] if w.get("life")=="Mapped" and w["window"] not in baseline and w.get("app_id")==expected)
    row["other_windows"]=sorted(set(row["mapped"])-set(baseline)-set(row["expected_windows"]))
    return row


def summarize_sdk(baseline,samples):
    result=sdk.summarize_sdk(baseline,samples);expected=set();preserved=True;other=False
    for row in samples:
        expected.update(row["expected_windows"]);preserved &= set(baseline["mapped"])<=set(row["mapped"]);other |= bool(row["other_windows"])
    result.update(app_id_matched=len(expected)==1 and expected==set(result["additional_windows"]),unexpected_windows=other,
        baseline_windows_preserved=preserved,resources_retired=sdk.sdk_resources_retired(baseline,samples))
    if not (result["app_id_matched"] and preserved and not other and result["resources_retired"]):result["status"]="not_verified"
    return result
