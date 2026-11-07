"""固定Freedoom demo128だけを現行leaseで起動。任意exec/PID/envと通常GUI解禁は行わない。"""
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from sdl_probe_install import InstallError,require,termux_home,unique_object
from sdl_lease_capture import CaptureError,capture_remote,proc_identity
from sdl_probe_run import live_binding,group_live
import freedoom_install as assets
import egl_surfaceless_probe as egl

if "sdl_observation" not in sys.modules:
    spec=importlib.util.spec_from_file_location("sdl_observation",Path(__file__).parent/"termux/eidolon-sdl-probe.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);sys.modules[spec.name]=module
from sdl_observation import WaylandTrace,TRACE_LIMIT

OPERATOR="eidolon-freedoom-demo128-v1"
CASE="demo128"
TIMEOUT=25.0
UID=assets.UID
SDL_SHA="5e70d30076c0259aac79d226465c1b0e265b5bbd0cf1665f82764c031b98cc0a"
SDL_BYTES=1522200
REASONS={"REQUEST_INVALID","UID_MISMATCH","ASSET_RECEIPT_INVALID","ASSET_CHANGED","STATE_UNSAFE","STATE_EXISTS","CHILD_IDENTITY_UNVERIFIED",
         "HOST_CANCEL","CONTROL_EOF","CONTROL_INVALID","TIMEOUT","OUTPUT_LIMIT","LEASE_CHANGED","RUNTIME_CHANGED","OPERATOR_ERROR",
         "CLEANUP_UNCONFIRMED","DESCENDANTS_AFTER_EXIT","CLIENT_OUTPUT_INVALID","CLIENT_EXIT_UNEXPECTED"}


def app_id(probe_id):
    require(type(probe_id) is str and re.fullmatch(r"[0-9a-f]{32}",probe_id),"REQUEST_INVALID")
    return "dev.eidolon.freedoom.probe.r"+probe_id


def validate_runtime(home):
    directory=home.parent/"usr/lib";target=directory/"libSDL2-2.0.so";alias=directory/"libSDL2.so"
    info=target.lstat();link=alias.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==UID and stat.S_IMODE(info.st_mode)==0o700 and info.st_size==SDL_BYTES and
        stat.S_ISLNK(link.st_mode) and link.st_uid==UID and alias.resolve(strict=True)==target,"RUNTIME_LIBRARY_CHANGED")
    result=egl.validate_runtime(home,pins=dict(egl.LIBRARIES,**{"libSDL2-2.0.so":SDL_SHA}))
    require(assets.identity(info)==assets.identity(target.lstat()) and assets.identity(link)==assets.identity(alias.lstat()),"RUNTIME_LIBRARY_CHANGED")
    return result


class GameTrace(WaylandTrace):
    def __init__(self,expected):
        super().__init__();self.expected=expected;self.roles={};self.classes={};self.expected_ids=0;self.other_ids=0;self.matching_commits=0
    def line(self,line):
        super().line(line);obj=r'[@#](\d+)'
        match=re.search(r'xdg_surface'+obj+r'\.get_toplevel\(new id xdg_toplevel'+obj,line)
        if match and int(match[1]) in self.xdg_surfaces and len(self.roles)<128:
            self.roles[int(match[2])]=self.xdg_surfaces[int(match[1])];self.classes.pop(int(match[2]),None)
        match=re.search(r'xdg_toplevel'+obj+r'\.set_app_id\("([^"\\]*)"\)',line)
        if match and len(self.classes)<128:
            correct=match[2]==self.expected;self.classes[int(match[1])]=correct
            if correct:self.expected_ids+=1
            else:self.other_ids+=1
        match=re.search(r'wl_surface'+obj+r'\.commit\(',line)
        if match and self.attached.get(int(match[1]))=='shm' and any(self.classes.get(t) is True and s==int(match[1]) for t,s in self.roles.items()):self.matching_commits+=1
        match=re.search(r'wl_surface'+obj+r'\.destroy\(',line)
        if match:
            for top in [t for t,s in self.roles.items() if s==int(match[1])]:self.roles.pop(top,None);self.classes.pop(top,None)
    def summary(self):
        result=super().summary();result.update(expected_app_id_events=self.expected_ids,unknown_app_id_events=self.other_ids,matching_app_id_shm_commits=self.matching_commits)
        return result


def installed(home,run_id,pin,engine_pin):
    require(type(run_id) is str and re.fullmatch(r"[0-9a-f]{32}",run_id),"REQUEST_INVALID")
    fd=assets.open_root(home,UID)
    try:
        for part in (".local","share","eidolon","freedoom",assets.source.VERSION,run_id):
            child=assets.open_child(fd,part,UID,mode=0o700 if part==run_id else None);os.close(fd);fd=child
        with os.fdopen(assets.fixed_file(fd,"install-receipt.json",UID),"rb") as stream:
            before=os.fstat(stream.fileno());raw=stream.read(16385)
            require(len(raw)<=16384 and assets.identity(before)==assets.identity(os.fstat(stream.fileno())),"ASSET_RECEIPT_INVALID")
            receipt=json.loads(raw,object_pairs_hook=unique_object)
    finally:os.close(fd)
    directory=home/".local/share/eidolon/freedoom"/assets.source.VERSION/run_id
    raw,manifest,identities,payload=assets.validate_prepared(directory,pin);engine=assets.engine(home,engine_pin)
    require(type(receipt) is dict and set(receipt)=={"schema","kind","run_id","uid","manifest_sha256","payload_sha256","wire_sha256","engine","files","execution","catalog"} and
        type(receipt["schema"]) is int and receipt["schema"]==1 and receipt["kind"]=="eidolon-freedoom-installed-v1" and receipt["run_id"]==run_id and
        receipt["uid"]==UID and receipt["manifest_sha256"]==pin and receipt["payload_sha256"]==payload and assets.hex64(receipt["wire_sha256"]) and
        receipt["engine"]==engine and type(receipt["files"]) is dict and all(type(v) is int for v in receipt["files"].values()) and receipt["files"]=={n:v["bytes"] for n,v in manifest["files"].items()} and
        receipt["execution"]=="not_run" and receipt["catalog"]=="not_modified","ASSET_RECEIPT_INVALID")
    return dict(directory=directory,manifest=manifest,identities=identities,payload_sha256=payload,engine=engine)


def check_assets(home,checked,engine_pin):
    require(assets.engine(home,engine_pin)==checked["engine"],"RUNTIME_CHANGED")
    root=assets.open_root(checked["directory"],UID)
    try:
        for name,expected in checked["identities"].items():
            fd=assets.fixed_file(root,name,UID)
            try:require(assets.identity(os.fstat(fd))==expected,"ASSET_CHANGED")
            finally:os.close(fd)
    finally:os.close(root)


def new_state(home,run_id,probe_id,checked):
    app_id(probe_id);fd=assets.open_root(home,UID)
    try:
        for part in (".local","state","eidolon","freedoom",run_id):
            child=assets.open_child(fd,part,UID,mode=None,create=True);os.close(fd);fd=child
        state=assets.open_child(fd,probe_id,UID,create=True,exclusive=True)
        try:
            for name in ("home","config","cache","tmp","save"):
                child=assets.open_child(state,name,UID,create=True,exclusive=True);os.close(child)
            root=assets.open_root(checked["directory"],UID)
            try:
                for name in ("default.cfg","chocolate-doom.cfg"):
                    with os.fdopen(assets.fixed_file(root,"config/"+name,UID),"rb") as stream:data=stream.read(4096)
                    expected=checked["manifest"]["files"]["config/"+name]
                    require(len(data)==expected["bytes"] and assets.sha(data)==expected["sha256"],"ASSET_CHANGED")
                    target=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=state)
                    with os.fdopen(target,"wb") as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            finally:os.close(root)
            os.fsync(state)
        finally:os.close(state)
    finally:os.close(fd)
    return home/".local/state/eidolon/freedoom"/run_id/probe_id


def invocation(home,directory,state):
    args=[]
    for item in assets.source.fixed_case(CASE)["arguments"]:
        if type(item) is str:args.append(item)
        elif set(item)=={"asset"}:args.append(str(directory/"assets"/item["asset"]))
        elif set(item)=={"state"}:args.append(str(state/item["state"]))
        elif set(item)=={"state_directory"}:args.append(str(state/item["state_directory"]))
        else:raise InstallError("REQUEST_INVALID")
    return [str(home.parent/"usr/bin/chocolate-doom"),*args]


def environment(home,state,wayland,probe_id):
    require(set(wayland)=={"WAYLAND_DISPLAY","XDG_RUNTIME_DIR"},"REQUEST_INVALID")
    prefix=home.parent/"usr"
    return dict(PATH=str(prefix/"bin")+":/system/bin",PREFIX=str(prefix),HOME=str(state/"home"),LANG="C.UTF-8",TMPDIR=str(state/"tmp"),
        XDG_CONFIG_HOME=str(state/"config"),XDG_CACHE_HOME=str(state/"cache"),**wayland,**assets.source.launch_plan("0"*32)["graphics_environment"],
        SDL_VIDEO_WAYLAND_WMCLASS=app_id(probe_id),WAYLAND_DEBUG="client")


def child_identity(pid):
    fd=os.open(f"/proc/{pid}",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        require(os.fstat(fd).st_uid==UID,"CHILD_IDENTITY_UNVERIFIED");row=proc_identity(fd,pid)
        require(row["group"]==pid,"CHILD_IDENTITY_UNVERIFIED");return row
    finally:os.close(fd)


def game_identity(pid,home,checked):
    row=child_identity(pid);directory=os.open(f"/proc/{pid}",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        path=os.readlink("exe",dir_fd=directory);expected=home.parent/"usr/bin/chocolate-doom"
        require(not path.endswith(" (deleted)") and Path(path).resolve(strict=True)==expected,"CHILD_IDENTITY_UNVERIFIED")
        info=os.stat("exe",dir_fd=directory,follow_symlinks=True)
        require(info.st_uid==UID and assets.identity(info)==tuple(checked["engine"]["identity"]),"CHILD_IDENTITY_UNVERIFIED")
    finally:os.close(directory)
    return row


def observe(command,env,cwd,control_fd,checker,probe_id,*,timeout=TIMEOUT,grace=2.0,identity_reader=child_identity,emit=lambda *_:None):
    """command/env差替えはlocalhost fixtureのみ。CLIは固定demo以外を受けない。"""
    selector=selectors.DefaultSelector();blocking=os.get_blocking(control_fd)
    try:
        os.set_blocking(control_fd,False)
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=cwd,env=env,start_new_session=True,close_fds=True)
    except Exception:selector.close();os.set_blocking(control_fd,blocking);raise
    trace=GameTrace(app_id(probe_id));counts=dict(stdout=0,stderr=0);control=bytearray();started=time.monotonic();last_check=0;stop_at=None
    reason=error=None;term_sent=kill_sent=timed_out=False;identity=None
    def send(number):
        nonlocal term_sent,kill_sent
        try:
            os.killpg(child.pid,number)
            if number==signal.SIGTERM:term_sent=True
            if number==signal.SIGKILL:kill_sent=True
        except ProcessLookupError:pass
    def stop(value):
        nonlocal reason,stop_at
        if reason is None:reason=value
        if stop_at is None:stop_at=time.monotonic();send(signal.SIGTERM)
    def ready(wait):
        for key,_ in selector.select(wait):
            try:data=os.read(key.fd,65536 if key.data!="control" else 1024)
            except BlockingIOError:continue
            if not data:
                selector.unregister(key.fd)
                if key.data=="control":stop("CONTROL_EOF")
                continue
            if key.data=="control":
                control.extend(data)
                if len(control)>1024 or b"\n" in control and control!=b'{"type":"cancel"}\n':stop("CONTROL_INVALID")
                elif control==b'{"type":"cancel"}\n':stop("HOST_CANCEL")
            else:
                counts[key.data]+=len(data)
                if key.data=="stderr":trace.feed(data)
                if counts[key.data]>(TRACE_LIMIT if key.data=="stderr" else 65536):stop("OUTPUT_LIMIT")
    try:
        for name in ("stdout","stderr"):
            pipe=getattr(child,name);os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,name)
        selector.register(control_fd,selectors.EVENT_READ,"control")
        try:
            identity=identity_reader(child.pid)
            emit(dict(phase="started",child=identity,uid=UID,app_id=app_id(probe_id)))
        except (OSError,ValueError):error="CHILD_IDENTITY_UNVERIFIED";stop(error)
        while True:
            exited=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None
            if exited:
                if not group_live(child.pid):break
                stop("DESCENDANTS_AFTER_EXIT")
            now=time.monotonic()
            if now-started>=timeout and not timed_out:timed_out=True;stop("TIMEOUT")
            if now-last_check>=.2 and stop_at is None:
                last_check=now
                try:checker()
                except (OSError,ValueError) as failure:stop(str(failure) if isinstance(failure,InstallError) and str(failure) in REASONS else "LEASE_CHANGED")
                if stop_at is None:
                    try:require(identity_reader(child.pid)==identity,"CHILD_IDENTITY_UNVERIFIED")
                    except (OSError,ValueError):
                        # 終了直後の/proc読取りは失敗し得る。元terminal statusを保持して回収する。
                        if os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None:stop("CHILD_IDENTITY_UNVERIFIED")
            if stop_at is not None and now-stop_at>=grace and group_live(child.pid):send(signal.SIGKILL)
            if stop_at is not None and now-stop_at>=grace+2:error="CLEANUP_UNCONFIRMED";break
            ready(.02)
    except (OSError,ValueError,TypeError,KeyError):error="OPERATOR_ERROR";stop(error)
    finally:
        if group_live(child.pid):
            stop(reason or "CLEANUP_UNCONFIRMED");until=time.monotonic()+grace
            while group_live(child.pid) and time.monotonic()<until:
                try:ready(.02)
                except (OSError,ValueError):pass
            if group_live(child.pid):send(signal.SIGKILL)
        until=time.monotonic()+2
        while group_live(child.pid) and time.monotonic()<until:
            try:ready(.02)
            except (OSError,ValueError):pass
        reclaimed=not group_live(child.pid);reaped=False
        try:child.wait(timeout=2);reaped=True
        except subprocess.TimeoutExpired:error="CLEANUP_UNCONFIRMED"
        until=time.monotonic()+1
        while any(k.data!="control" for k in selector.get_map().values()) and time.monotonic()<until:
            try:ready(.02)
            except (OSError,ValueError):break
        drained=not any(k.data!="control" for k in selector.get_map().values())
        selector.close();child.stdout.close();child.stderr.close();os.set_blocking(control_fd,blocking)
    summary=trace.summary();summary["complete"]&=drained;cleanup=reaped and reclaimed and drained
    shm=(summary["complete"] and summary["content_shm_commits"]>=3 and summary["matching_app_id_shm_commits"]>=3 and summary["expected_app_id_events"]>=1 and
         not any(summary[k] for k in ("content_non_shm_commits","content_unknown_commits","protocol_errors","unknown_app_id_events")))
    success=cleanup and reason is None and error is None and child.returncode==0 and identity is not None and shm
    return dict(status="completed" if success else "failed",case=CASE,profile=assets.source.PROFILE,app_id=app_id(probe_id),
        child=identity,child_pid=child.pid,child_group=child.pid,child_returncode=child.returncode,
        termination="unreaped" if child.returncode is None else "signal" if child.returncode<0 else "natural",term_sent=term_sent,kill_sent=kill_sent,timed_out=timed_out,
        cancel_reason=reason,error=error,reaped=reaped,group_reclaimed=reclaimed,pipes_drained=drained,cleanup_confirmed=cleanup,
        shm_gate="pass" if shm else "fail",wayland=summary,output_bytes=counts,physical_input="not_run",hmd="not_run",renderer_policy="software_pinned_config")


def run_request(request,home,control_fd,emit,*,capture=capture_remote,lease_checker=live_binding,interrupted=lambda:False):
    require(type(request) is dict and set(request)=={"schema","probe_id","asset_run_id","manifest_sha256","engine_sha256","binding"} and
            type(request["schema"]) is int and request["schema"]==1 and request["engine_sha256"]==assets.ENGINE_SHA,"REQUEST_INVALID")
    app_id(request["probe_id"]);require(os.getuid()==UID and request["binding"]["uid"]==UID,"UID_MISMATCH")
    captured=capture(request["binding"],home);checked=installed(home,request["asset_run_id"],request["manifest_sha256"],request["engine_sha256"])
    runtime=validate_runtime(home);state=new_state(home,request["asset_run_id"],request["probe_id"],checked)
    def checker():
        require(not interrupted(),"HOST_CANCEL")
        lease_checker(request["binding"],home,captured);check_assets(home,checked,request["engine_sha256"])
    checker();emit(dict(phase="validated",case=CASE,profile=assets.source.PROFILE,app_id=app_id(request["probe_id"]),session=request["binding"]["session"]))
    result=observe(invocation(home,checked["directory"],state),environment(home,state,captured["environment"],request["probe_id"]),checked["directory"],control_fd,checker,request["probe_id"],
                   identity_reader=lambda pid:game_identity(pid,home,checked),emit=emit)
    post={}
    for name,check in (("assets",lambda:installed(home,request["asset_run_id"],request["manifest_sha256"],request["engine_sha256"])==checked),
                       ("runtime",lambda:validate_runtime(home)==runtime),
                       ("lease",lambda:lease_checker(request["binding"],home,captured) is None)):
        try:post[name]=bool(check())
        except (OSError,ValueError):post[name]=False
    if not all(post.values()):result["status"]="failed"
    result.update(schema=1,operator=OPERATOR,probe_id=request["probe_id"],asset_run_id=request["asset_run_id"],uid=UID,
        manifest_sha256=request["manifest_sha256"],engine=checked["engine"],assets_unchanged=post["assets"],runtime_unchanged=post["runtime"],lease_current=post["lease"],
        sdk="host_observation_required",catalog="not_modified",execution="demo128")
    fd=os.open(state/"report.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:stream.write(assets.canonical(result)+b"\n");stream.flush();os.fsync(stream.fileno())
    return result


def remote_main():
    os.umask(0o077);previous={};interrupted={"value":False}
    try:
        require(os.getuid()==UID,"UID_MISMATCH");raw=bytearray()
        while len(raw)<16384:
            byte=os.read(0,1);require(byte,"REQUEST_INVALID")
            if byte==b"\n":break
            raw.extend(byte)
        require(len(raw)<16384,"REQUEST_INVALID");request=json.loads(raw,object_pairs_hook=unique_object)
        for number in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):previous[number]=signal.signal(number,lambda *_:interrupted.__setitem__("value",True))
        emit=lambda value:print(json.dumps(value),flush=True)
        result=run_request(request,termux_home(sys.executable),0,emit,interrupted=lambda:interrupted["value"]);emit(dict(phase="finished",result=result))
        return 0 if result["status"]=="completed" else 1
    except (OSError,ValueError,TypeError,KeyError) as error:
        reason=error.reason if isinstance(error,CaptureError) else str(error) if isinstance(error,InstallError) and str(error) in REASONS|assets.REASONS|egl.BLOCKED else "FREEDOOM_OPERATOR_UNAVAILABLE"
        if type(reason) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}",reason) is None:reason="FREEDOOM_OPERATOR_UNAVAILABLE"
        print(json.dumps(dict(phase="blocked",reason=reason)),flush=True);return 2
    finally:
        for number,handler in previous.items():signal.signal(number,handler)
