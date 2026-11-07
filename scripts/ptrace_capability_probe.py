"""固定own-childの公開ptrace/AArch64 regset能力だけ。EGL/他process attachは使わない。"""
import ctypes
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
from sdl_probe_install import InstallError, canonical, require, termux_home, unique_object

UID=10158
OPERATOR="eidolon-ptrace-capability-v1"
EXPECTED_EXIT=23
DENIED_EXIT=77
TIMEOUT=6.0
CHILD_SOURCE=None
MAX_OUTPUT=8192
REASONS={"UID_MISMATCH","REQUEST_INVALID","CHILD_SOURCE_MISSING","DIRECTORY_UNSAFE","PTRACE_UNAVAILABLE","PTRACE_DENIED",
         "PTRACE_UNSUPPORTED","ARCH_UNSUPPORTED","REGSET_UNSUPPORTED","REGSET_SIZE_INVALID","SLOT_LIMIT","DEBUG_STATE_BUSY",
         "UNEXPECTED_STOP","CHILD_OUTPUT_INVALID","CHILD_EXIT_UNEXPECTED","HOST_CANCEL","CONTROL_EOF","CONTROL_INVALID","TIMEOUT",
         "OPERATOR_UNAVAILABLE","CLEANUP_UNCONFIRMED"}


class PtraceError(ValueError):
    def __init__(self,number):self.number=number;super().__init__("PTRACE_DENIED" if number in (1,13) else "PTRACE_UNSUPPORTED")


class Ptrace:
    def __init__(self):
        try:
            self.function=ctypes.CDLL(None,use_errno=True).ptrace
            self.function.argtypes=[ctypes.c_uint,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p]
            self.function.restype=ctypes.c_long
        except (OSError,AttributeError):raise InstallError("PTRACE_UNAVAILABLE") from None
    def call(self,request,pid=0,address=0,data=0):
        ctypes.set_errno(0)
        result=self.function(request,pid,ctypes.c_void_p(address),ctypes.c_void_p(data) if isinstance(data,int) else data)
        if result==-1:raise PtraceError(ctypes.get_errno())
        return result
    def regset(self,pid,note,size):
        class Iovec(ctypes.Structure):_fields_=[("base",ctypes.c_void_p),("length",ctypes.c_size_t)]
        data=(ctypes.c_ubyte*size)();view=Iovec(ctypes.cast(data,ctypes.c_void_p),size)
        self.call(0x4204,pid,note,ctypes.byref(view)) # PTRACE_GETREGSET
        require(view.length==size,"REGSET_SIZE_INVALID")
        return bytes(data)


def decode_regsets(gpr,hardware):
    require(len(gpr)==272 and len(hardware)==264,"REGSET_SIZE_INVALID")
    # registers/addressは返さない。UAPI prefixとBP利用状態だけに縮約する。
    info=int.from_bytes(hardware[:4],"little");slots=info&255
    require(slots<=16,"REGSET_SIZE_INVALID")
    require(hardware[4:8]==b"\0"*4,"REGSET_SIZE_INVALID")
    clean=all(hardware[8+i*16:24+i*16]==b"\0"*16 for i in range(16))
    return dict(gpr_bytes=272,hardware_bytes=264,hardware_slots=slots,debug_arch=(info>>8)&255,state_clean=clean)


def assess(backend,pid):
    return decode_regsets(backend.regset(pid,1,272),backend.regset(pid,0x402,264))


def child_main():
    emit=lambda v:print(json.dumps(v,separators=(",",":")),flush=True)
    try:
        raw=sys.stdin.buffer.readline(129)
        require(raw.endswith(b"\n") and len(raw)<=128,"REQUEST_INVALID")
        value=json.loads(raw,object_pairs_hook=unique_object)
        require(type(value) is dict and set(value)=={"probe_id"} and isinstance(value["probe_id"],str) and re.fullmatch(r"[0-9a-f]{32}",value["probe_id"]),"REQUEST_INVALID")
        require(os.getuid()==UID,"UID_MISMATCH")
        binding=dict(probe_id=value["probe_id"],pid=os.getpid(),uid=os.getuid())
        try:Ptrace().call(0) # PTRACE_TRACEME。対象PIDやaddressは取らない。
        except PtraceError as error:
            emit(dict(phase="traceme",success=False,errno=error.number,**binding));return DENIED_EXIT
        except (OSError,ValueError,AttributeError):
            emit(dict(phase="traceme",success=False,errno=None,**binding));return DENIED_EXIT
        emit(dict(phase="traceme",success=True,errno=0,**binding))
        os.kill(os.getpid(),signal.SIGSTOP)
        emit(dict(phase="continued",**binding))
        return EXPECTED_EXIT
    except (OSError,ValueError,TypeError):return DENIED_EXIT


def group_live(group):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():continue
        try:
            fields=(entry/"stat").read_text().rpartition(") ")[2].split()
            if len(fields)>=20 and int(fields[2])==group and fields[0]!="Z":return True
        except (OSError,ValueError):pass
    return False


def receipt(line,pid,probe_id,uid):
    try:value=json.loads(line,object_pairs_hook=unique_object)
    except (ValueError,UnicodeError):raise InstallError("CHILD_OUTPUT_INVALID") from None
    require(type(value) is dict and value.get("phase") in {"traceme","continued"} and
            value.get("pid")==pid and type(value.get("pid")) is int and value.get("probe_id")==probe_id and
            type(value.get("uid")) is int and value["uid"]==uid,"CHILD_OUTPUT_INVALID")
    if value["phase"]=="traceme":
        require(set(value)=={"phase","probe_id","pid","uid","success","errno"} and type(value["success"]) is bool and
                (value["errno"]==0 and type(value["errno"]) is int if value["success"] else value["errno"] is None or type(value["errno"]) is int and 0<value["errno"]<=4095),"CHILD_OUTPUT_INVALID")
    else:require(set(value)=={"phase","probe_id","pid","uid"},"CHILD_OUTPUT_INVALID")
    return value


def observe(command,environment,control_fd,probe_id,*,expected_uid=UID,architecture=None,assessor=assess,timeout=TIMEOUT,grace=1.0,interrupted=lambda:False):
    """command/uid/arch/assessor差替えはlocalhost fixtureのみ。CLIは任意exec/PIDを受けない。"""
    architecture=architecture or os.uname().machine
    backend=Ptrace();selector=selectors.DefaultSelector();blocking=os.get_blocking(control_fd)
    try:
        os.set_blocking(control_fd,False)
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,start_new_session=True,close_fds=True)
    except Exception:
        selector.close();os.set_blocking(control_fd,blocking);raise
    try:child.stdin.write(canonical(dict(probe_id=probe_id))+b"\n");child.stdin.flush()
    except (OSError,ValueError):pass
    try:child.stdin.close()
    except (OSError,ValueError):pass
    events={};pending=bytearray();control=bytearray();sizes=dict(stdout=0,stderr=0)
    started=time.monotonic();stop_at=None;reason=None;error_number=None;result=None
    stop_seen=continued=options_set=term_sent=term_forwarded=kill_sent=timed_out=False
    def fail(value,number=None):
        nonlocal reason,error_number
        if reason is None:reason=value;error_number=number
    def send(number):
        nonlocal term_sent,kill_sent
        try:
            os.killpg(child.pid,number)
            if number==signal.SIGTERM:term_sent=True
            if number==signal.SIGKILL:kill_sent=True
        except ProcessLookupError:pass
    def cancel(value):
        nonlocal stop_at
        fail(value)
        if stop_at is None:stop_at=time.monotonic();send(signal.SIGTERM)
    def continue_child(number=0):
        nonlocal continued,term_forwarded
        backend.call(7,child.pid,data=number) # PTRACE_CONT
        if number==signal.SIGTERM:term_forwarded=True
        elif number==0:continued=True
    def stops():
        nonlocal stop_seen,options_set,result
        try:stopped=os.waitid(os.P_PID,child.pid,os.WSTOPPED|os.WNOHANG)
        except ChildProcessError:
            # Linuxは終了済みの子にWSTOPPEDだけを指定するとECHILDを返す。
            # 終了statusはconsumeせず、Popen.waitで元のcodeを回収する。
            if os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None:return
            raise
        if stopped is None:return
        number=stopped.si_status
        if number==signal.SIGSTOP and not stop_seen and stop_at is None:
            stop_seen=True
            try:backend.call(0x4200,child.pid,data=1<<20);options_set=True # PTRACE_O_EXITKILL
            except PtraceError as error:fail(str(error),error.number)
            if options_set:
                if architecture!="aarch64":fail("ARCH_UNSUPPORTED")
                else:
                    try:
                        result=assessor(backend,child.pid)
                        if result["hardware_slots"]<3:fail("SLOT_LIMIT")
                        elif not result["state_clean"]:fail("DEBUG_STATE_BUSY")
                    except PtraceError as error:fail("REGSET_UNSUPPORTED",error.number)
                    except (OSError,ValueError,TypeError) as error:fail(str(error) if isinstance(error,InstallError) and str(error) in REASONS else "REGSET_UNSUPPORTED")
            try:continue_child()
            except PtraceError as error:fail(str(error),error.number);cancel(reason)
        else:
            if stop_at is None:fail("UNEXPECTED_STOP")
            try:continue_child(signal.SIGTERM if stop_at is not None else number)
            except PtraceError as error:fail(str(error),error.number);cancel(reason)
    def drain(key):
        nonlocal pending
        data=os.read(key.fd,4096)
        if not data:selector.unregister(key.fd);return
        sizes[key.data]+=len(data)
        if key.data=="stderr":
            if sizes["stderr"]>MAX_OUTPUT:cancel("CHILD_OUTPUT_INVALID")
            return # raw本文は保存しない。
        if sizes["stdout"]>MAX_OUTPUT:cancel("CHILD_OUTPUT_INVALID");pending.clear();return
        pending.extend(data)
        while b"\n" in pending:
            line,_,rest=pending.partition(b"\n");pending=bytearray(rest)
            try:
                event=receipt(line,child.pid,probe_id,expected_uid)
                require(event["phase"] not in events,"CHILD_OUTPUT_INVALID");events[event["phase"]]=event
            except (OSError,ValueError,TypeError):cancel("CHILD_OUTPUT_INVALID")
    try:
        for name in ("stdout","stderr"):
            pipe=getattr(child,name);os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,name)
        selector.register(control_fd,selectors.EVENT_READ,"control")
        while True:
            if interrupted():cancel("HOST_CANCEL")
            stops() # cancel後のsignal-delivery-stopでもTERMをforwardする。
            terminal=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if terminal is not None and not group_live(child.pid):break
            now=time.monotonic()
            if now-started>=timeout and not timed_out:timed_out=True;cancel("TIMEOUT")
            if stop_at is not None and now-stop_at>=grace and group_live(child.pid):send(signal.SIGKILL)
            if stop_at is not None and now-stop_at>=grace+2:break
            for key,_ in selector.select(.02):
                if key.data!="control":drain(key);continue
                data=os.read(key.fd,1024)
                if not data:selector.unregister(key.fd);cancel("CONTROL_EOF");continue
                control.extend(data)
                if len(control)>1024 or b"\n" in control and control!=b'{"type":"cancel"}\n':cancel("CONTROL_INVALID")
                elif control==b'{"type":"cancel"}\n':cancel("HOST_CANCEL")
    except (OSError,ValueError,TypeError):cancel("OPERATOR_UNAVAILABLE")
    finally:
        if group_live(child.pid):
            cancel(reason or "CLEANUP_UNCONFIRMED")
            until=time.monotonic()+grace
            while group_live(child.pid) and time.monotonic()<until:
                try:stops()
                except (OSError,ValueError):pass
                for key,_ in selector.select(.02):
                    if key.data!="control":drain(key)
                    else:selector.unregister(key.fd)
            if group_live(child.pid):send(signal.SIGKILL)
        until=time.monotonic()+2
        while group_live(child.pid) and time.monotonic()<until:time.sleep(.02)
        reclaimed=not group_live(child.pid);reaped=False
        try:child.wait(timeout=2);reaped=True
        except subprocess.TimeoutExpired:fail("CLEANUP_UNCONFIRMED")
        until=time.monotonic()+1
        while any(k.data!="control" for k in selector.get_map().values()) and time.monotonic()<until:
            for key,_ in selector.select(.02):
                if key.data!="control":drain(key)
                else:selector.unregister(key.fd)
        drained=not any(k.data!="control" for k in selector.get_map().values())
        selector.close();child.stdout.close();child.stderr.close();os.set_blocking(control_fd,blocking)
    trace=events.get("traceme");cleanup=reaped and reclaimed and drained
    if pending:fail("CHILD_OUTPUT_INVALID")
    if trace and not trace["success"]:fail("PTRACE_DENIED" if trace["errno"] in (1,13) else "PTRACE_UNAVAILABLE",trace["errno"])
    if not cleanup:fail("CLEANUP_UNCONFIRMED")
    if trace and trace["success"] and (child.returncode!=EXPECTED_EXIT or set(events)!={"traceme","continued"}):fail("CHILD_EXIT_UNEXPECTED")
    if not trace:fail("CHILD_OUTPUT_INVALID")
    supported=reason is None and stop_seen and options_set and continued and result is not None and cleanup and child.returncode==EXPECTED_EXIT
    if not supported and reason is None:reason="PTRACE_UNSUPPORTED"
    return dict(status="supported" if supported else "unsupported",reason=reason,errno=error_number,architecture="aarch64" if architecture=="aarch64" else "unsupported",
        child_pid=child.pid,child_group=child.pid,child_returncode=child.returncode,expected_exit=EXPECTED_EXIT,
        termination="unreaped" if child.returncode is None else "signal" if child.returncode<0 else "natural",
        stop_observed=stop_seen,exitkill_set=options_set,continue_sent=continued,regset=result,
        term_sent=term_sent,term_forwarded=term_forwarded,kill_sent=kill_sent,timed_out=timed_out,
        reaped=reaped,group_reclaimed=reclaimed,pipes_drained=drained,cleanup_confirmed=cleanup,output_bytes=sizes,
        traceme=trace,continued_receipt=events.get("continued"),egl="not_run",drawing="not_run")


def private_run(home,probe_id):
    fd=os.open(home,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in (".local","state","eidolon","ptrace-capability",probe_id):
            info=os.fstat(fd);require(info.st_uid==UID and not info.st_mode&0o022,"DIRECTORY_UNSAFE")
            try:os.mkdir(part,0o700,dir_fd=fd)
            except FileExistsError:
                require(part!=probe_id,"DIRECTORY_UNSAFE")
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        info=os.fstat(fd);require(info.st_uid==UID and stat.S_IMODE(info.st_mode)==0o700,"DIRECTORY_UNSAFE")
    finally:os.close(fd)
    return home/".local/state/eidolon/ptrace-capability"/probe_id


def remote_main():
    os.umask(0o077)
    previous={};interrupted={"value":False}
    try:
        require(os.getuid()==UID,"UID_MISMATCH")
        # 後続cancelをBufferedReaderへ先読みしない。
        raw=bytearray()
        while len(raw)<=128:
            byte=os.read(0,1)
            if not byte:break
            raw.extend(byte)
            if byte==b"\n":break
        require(raw.endswith(b"\n") and len(raw)<=128,"REQUEST_INVALID")
        request=json.loads(raw,object_pairs_hook=unique_object)
        require(type(request) is dict and set(request)=={"probe_id"} and isinstance(request["probe_id"],str) and re.fullmatch(r"[0-9a-f]{32}",request["probe_id"]),"REQUEST_INVALID")
        require(isinstance(CHILD_SOURCE,str) and CHILD_SOURCE,"CHILD_SOURCE_MISSING")
        home=termux_home(sys.executable);directory=private_run(home,request["probe_id"])
        prefix=home.parent/"usr"
        environment=dict(PATH=str(prefix/"bin")+":/system/bin",HOME=str(home),PREFIX=str(prefix),LANG="C.UTF-8")
        for number in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
            previous[number]=signal.signal(number,lambda *_:interrupted.__setitem__("value",True))
        report=observe([sys.executable,"-c",CHILD_SOURCE],environment,0,request["probe_id"],interrupted=lambda:interrupted["value"])
        report.update(schema=1,operator=OPERATOR,probe_id=request["probe_id"],uid=UID)
        fd=os.open(directory/"report.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as stream:stream.write(canonical(report)+b"\n");stream.flush();os.fsync(stream.fileno())
        print(json.dumps(dict(phase="finished",result=report)),flush=True)
        return 0 if report["status"]=="supported" else 2
    except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as error:
        reason=str(error) if isinstance(error,InstallError) and str(error) in REASONS else "OPERATOR_UNAVAILABLE"
        print(json.dumps(dict(phase="blocked",status="unsupported",reason=reason)),flush=True);return 2

    finally:
        for number,handler in previous.items():signal.signal(number,handler)
