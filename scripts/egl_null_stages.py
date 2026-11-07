"""固定own-childのMesa3戻り値をhardware BPでboolへ縮約する。自由PID/offsetは公開しない。"""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
import zlib
from sdl_probe_install import InstallError,canonical,require,termux_home,unique_object
import egl_surfaceless_probe as egl
from ptrace_capability_probe import Ptrace,PtraceError,decode_regsets

UID=egl.UID
OPERATOR="eidolon-egl-null-stages-v1"
LOADING_PROFILE="existing-egl-query-display-no-preload"
NAME="libgallium-26.2.6.so"
POINTS=(("screen_allocation",0xaa91f0,0xb4000a80),("pipe_screen",0xaa92b8,0xaa0003e1),("dri_configs",0xaa92cc,0xf9000280))
MAX_MAPS=1024*1024
CHILD_SOURCE=None
REASONS={"PTRACE_UNAVAILABLE","PTRACE_DENIED","PTRACE_UNSUPPORTED","ARCH_UNSUPPORTED","REGSET_UNSUPPORTED","REGSET_SIZE_INVALID",
         "SLOT_LIMIT","DEBUG_STATE_BUSY","LIBRARY_UNVERIFIED","ELF_INVALID","INSTRUCTION_CHANGED","MAP_UNVERIFIED",
         "SETREGSET_UNSUPPORTED","BREAKPOINT_VERIFY_FAILED","DISARM_UNCONFIRMED","DRI_MODE_UNEXPECTED","STAGE_ORDER_INVALID",
         "UNEXPECTED_STOP","CHECKPOINT_MISSING","NO_HITS","PARTIAL_STAGES"}
BLOCKED=egl.BLOCKED|REASONS|{"CHILD_COMMAND_LIMIT"}


def child_bootstrap(source):
    payload=base64.b64encode(zlib.compress(source.encode(),9)).decode()
    code=f"import base64,zlib;exec(zlib.decompress(base64.b64decode({payload!r})))"
    require(len(code.encode())<=49*1024,"CHILD_COMMAND_LIMIT")
    return code


class Backend(Ptrace):
    def set_hardware(self,pid,data):
        class Iovec(ctypes.Structure):_fields_=[("base",ctypes.c_void_p),("length",ctypes.c_size_t)]
        require(len(data)==264,"REGSET_SIZE_INVALID")
        # kernelは渡した各slotにperf eventを作る。未使用13slotを初期化しない。
        prefix=data[:56];array=(ctypes.c_ubyte*56).from_buffer_copy(prefix);view=Iovec(ctypes.cast(array,ctypes.c_void_p),56)
        self.call(0x4205,pid,0x402,ctypes.byref(view)) # PTRACE_SETREGSET。code/data memoryは書き換えない。
        require(view.length==56,"REGSET_SIZE_INVALID")
    def instruction(self,pid,address):
        ctypes.set_errno(0)
        value=self.function(1,pid,ctypes.c_void_p(address),None) # PTRACE_PEEKTEXT、既知の3命令のみ。
        number=ctypes.get_errno()
        if value==-1 and number:raise PtraceError(number)
        return value&0xffffffff


def hardware_matches(actual,expected):
    require(len(actual)==len(expected)==264,"REGSET_SIZE_INVALID")
    if actual[:8]!=expected[:8] or actual[56:]!=expected[56:]:return False
    for i in range(3):
        address,ctrl,pad=struct.unpack_from("<QII",actual,8+i*16)
        wanted,control,reserved=struct.unpack_from("<QII",expected,8+i*16)
        # ptrace_hbp_set_ctrl(disabled)は長さ/EL0を維持するため0x1e4へ正規化し得る。
        if address!=wanted or pad!=reserved or (ctrl not in {0,0x1e4} if control==0 else ctrl!=control):return False
    return True


def elf_points(data):
    require(len(data)>=64 and data[:7]==b"\x7fELF\x02\x01\x01","ELF_INVALID")
    h=struct.unpack_from("<HHIQQQIHHHHHH",data,16)
    require(h[0]==3 and h[1]==183 and h[2]==1 and h[7]==64 and h[8]==56 and 0<h[9]<=128 and
            64<=h[4]<=len(data)-h[9]*56,"ELF_INVALID")
    loads=[]
    for i in range(h[9]):
        kind,flags,offset,address,_,filesize,memsize,align=struct.unpack_from("<IIQQQQQQ",data,h[4]+i*56)
        if kind!=1:continue
        require(filesize<=memsize and offset<=len(data) and filesize<=len(data)-offset and
                address+memsize<2**64 and align>0 and align&(align-1)==0,"ELF_INVALID")
        loads.append(dict(flags=flags,offset=offset,address=address,filesize=filesize))
    selected=[]
    for _,address,word in POINTS:
        segments=[s for s in loads if s["flags"]&1 and s["address"]<=address and address+4<=s["address"]+s["filesize"]]
        require(len(segments)==1,"ELF_INVALID");s=segments[0];offset=s["offset"]+address-s["address"]
        require(struct.unpack_from("<I",data,offset)[0]==word,"INSTRUCTION_CHANGED")
        selected.append((s,offset))
    return selected


def selected_mappings(raw,expected,info):
    require(type(raw) is bytes and len(raw)<=MAX_MAPS and raw.endswith(b"\n"),"MAP_UNVERIFIED")
    rows=[];allowed={os.fsencode(expected)}
    aliases={b"/data/data/com.termux/files/usr/lib/"+NAME.encode(),b"/data/user/0/com.termux/files/usr/lib/"+NAME.encode()}
    if os.fsencode(expected) in aliases:allowed|=aliases
    for line in raw.splitlines():
        fields=line.split(None,5)
        if len(fields)!=6:continue
        path=fields[5]
        if path.rsplit(b"/",1)[-1] not in {NAME.encode(),NAME.encode()+b" (deleted)"}:continue
        require(len(line)<=8192 and path in allowed and re.fullmatch(rb"[0-9a-f]+-[0-9a-f]+",fields[0]) and
                re.fullmatch(rb"[r-][w-][x-][ps]",fields[1]) and re.fullmatch(rb"[0-9a-f]+",fields[2]) and
                re.fullmatch(rb"[0-9a-f]+:[0-9a-f]+",fields[3]) and fields[4].isdigit(),"MAP_UNVERIFIED")
        start,end=(int(v,16) for v in fields[0].split(b"-"));major,minor=(int(v,16) for v in fields[3].split(b":"))
        require(0<start<end<2**64 and (major,minor)==(os.major(info.st_dev),os.minor(info.st_dev)) and
                int(fields[4])==info.st_ino,"MAP_UNVERIFIED")
        rows.append(dict(start=start,end=end,offset=int(fields[2],16),executable=b"x" in fields[1]))
        require(len(rows)<=64,"MAP_UNVERIFIED")
    require(rows,"MAP_UNVERIFIED")
    return rows


def mapped_addresses(rows,points,page):
    require(page in {4096,16384,65536},"MAP_UNVERIFIED")
    candidates=set()
    for segment,_ in points:
        for m in rows:
            if m["executable"] and segment["offset"]//page*page<=m["offset"]<(segment["offset"]+segment["filesize"]+page-1)//page*page:
                bias=m["start"]-segment["address"]+segment["offset"]-m["offset"]
                if bias>=0 and bias%page==0:candidates.add(bias)
    valid=[]
    for bias in candidates:
        addresses=[bias+v for _,v,_ in POINTS]
        if all(0<a<2**64 and any(m["executable"] and m["start"]<=a<a+4<=m["end"] and
                    m["offset"]+a-m["start"]==offset for m in rows) for a,(_,offset) in zip(addresses,points)):
            valid.append(addresses)
    require(len(valid)==1,"MAP_UNVERIFIED")
    return valid[0]


def verified_image(home,before,pid,backend):
    """pidは実Popen handleのみ。固定file/mapping/命令を照合し、addressは返り値の内部だけ。"""
    directory=home.parent/"usr/lib";path=directory/NAME
    require(directory.resolve(strict=True)==directory,"LIBRARY_UNVERIFIED")
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),"rb") as stream:
        info=os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid==UID and not info.st_mode&0o022 and
                list(egl.identity(info))==before[NAME]["identity"] and 0<info.st_size<=egl.MAX_LIBRARY,"LIBRARY_UNVERIFIED")
        data=stream.read(egl.MAX_LIBRARY+1)
        require(egl.identity(info)==egl.identity(os.fstat(stream.fileno())) and len(data)==info.st_size and
                hashlib.sha256(data).hexdigest()==egl.LIBRARIES[NAME],"LIBRARY_UNVERIFIED")
        points=elf_points(data)
        def read_maps():
            try:
                with open(f"/proc/{pid}/maps","rb") as maps:raw=maps.read(MAX_MAPS+1)
            except OSError:raise InstallError("MAP_UNVERIFIED") from None
            return selected_mappings(raw,path,info)
        rows=read_maps();addresses=mapped_addresses(rows,points,os.sysconf("SC_PAGE_SIZE"))
        require(all(backend.instruction(pid,a)==word for a,(_,_,word) in zip(addresses,POINTS)),"INSTRUCTION_CHANGED")
        require(read_maps()==rows and egl.identity(info)==egl.identity(os.fstat(stream.fileno())),"MAP_UNVERIFIED")
        return addresses


class Trace:
    """hardware state/actual addressesは保持するが、reportへは固定stage/boolのみを出す。"""
    def __init__(self,backend,pid,image,architecture=None):
        self.backend,self.pid,self.image=backend,pid,image
        self.architecture=architecture or os.uname().machine;self.original=None;self.current=None;self.addresses=None
        self.initial=False;self.checkpoint=False;self.options=False;self.armed=False;self.cleared=True
        self.library_verified=self.instructions_verified=self.mapping_verified=False
        self.reason=self.errno=None;self.capability=None;self.mode=None;self.hits=0
        self.last_trap_owned=False
        self.values={name:None for name,_,_ in POINTS}
    def fail(self,reason,number=None):
        if self.reason is None:self.reason=reason;self.errno=number
    def read_hardware(self):return self.backend.regset(self.pid,0x402,264)
    def disarm(self):
        if self.original is None:return True
        try:
            if not hardware_matches(self.read_hardware(),self.original):
                try:self.backend.set_hardware(self.pid,self.original)
                except (OSError,ValueError,TypeError):pass # 部分拒否でも全owned slotがdisabledならGETで確認できる。
            require(hardware_matches(self.read_hardware(),self.original),"DISARM_UNCONFIRMED")
            self.current=self.original;self.cleared=True;return True
        except (OSError,ValueError,TypeError):
            self.fail("DISARM_UNCONFIRMED");self.cleared=False;return False
    def start(self):
        self.initial=True
        try:self.backend.call(0x4200,self.pid,data=1<<20);self.options=True
        except PtraceError as error:self.fail(str(error),error.number)
        if self.architecture!="aarch64":self.fail("ARCH_UNSUPPORTED")
    def prepare(self):
        self.checkpoint=True
        if self.reason is not None:return True
        try:
            self.original=self.read_hardware();gpr=self.backend.regset(self.pid,1,272)
            self.capability=decode_regsets(gpr,self.original)
            require(self.capability["hardware_slots"]>=3,"SLOT_LIMIT")
            require(self.capability["state_clean"],"DEBUG_STATE_BUSY")
            self.addresses=self.image(self.backend,self.pid)
            self.library_verified=self.instructions_verified=self.mapping_verified=True
            state=bytearray(self.original)
            for i,address in enumerate(self.addresses):struct.pack_into("<QII",state,8+i*16,address,0x1e5,0)
            self.current=bytes(state);self.cleared=False
            try:self.backend.set_hardware(self.pid,self.current)
            except PtraceError as error:self.fail("SETREGSET_UNSUPPORTED",error.number);return self.disarm()
            require(hardware_matches(self.read_hardware(),self.current),"BREAKPOINT_VERIFY_FAILED")
            self.armed=True;return True
        except PtraceError as error:self.fail("REGSET_UNSUPPORTED",error.number)
        except (OSError,ValueError,TypeError,KeyError) as error:
            self.fail(str(error) if isinstance(error,InstallError) and str(error) in REASONS else "LIBRARY_UNVERIFIED")
        return self.disarm()
    def trap(self):
        self.last_trap_owned=False
        if not self.armed or self.reason is not None:self.fail("UNEXPECTED_STOP");return False
        try:
            data=self.backend.regset(self.pid,1,272);require(len(data)==272,"REGSET_SIZE_INVALID")
            gpr=struct.unpack("<34Q",data);pc=gpr[32]
            require(pc in self.addresses,"UNEXPECTED_STOP")
            self.last_trap_owned=True
            index=self.addresses.index(pc);self.hits+=1
            expected=sum(v is not None for v in self.values.values())
            require(index==expected and not any(v is False for v in self.values.values()),"STAGE_ORDER_INVALID")
            if index==0:
                self.mode=(gpr[23]&0xffffffff)==2 and (gpr[26]&0xffffffff)==0xffffffff
                require(self.mode,"DRI_MODE_UNEXPECTED")
            name=POINTS[index][0];self.values[name]=gpr[0]!=0
            if not self.values[name] or index==2:return self.disarm()
            updated=bytearray(self.current);struct.pack_into("<QII",updated,8+index*16,0,0,0)
            try:self.backend.set_hardware(self.pid,bytes(updated))
            except PtraceError as error:self.fail("SETREGSET_UNSUPPORTED",error.number);return self.disarm()
            self.current=bytes(updated)
            require(hardware_matches(self.read_hardware(),self.current),"BREAKPOINT_VERIFY_FAILED")
            return True
        except PtraceError as error:self.fail("REGSET_UNSUPPORTED",error.number)
        except (OSError,ValueError,TypeError) as error:
            self.fail(str(error) if isinstance(error,InstallError) and str(error) in REASONS else "REGSET_UNSUPPORTED")
        return self.disarm()
    def report(self):
        complete=self.mode is True and (any(v is False for v in self.values.values()) or all(v is True for v in self.values.values()))
        reason=self.reason or (None if complete else "CHECKPOINT_MISSING" if not self.checkpoint else "NO_HITS" if not self.hits else "PARTIAL_STAGES")
        return dict(profile="mesa-26.2.6-first-swrast-three-null-v1",status="observed" if complete and reason is None and self.cleared else "unsupported",
            reason=reason,errno=self.errno,initial_stop=self.initial,initialize_stop=self.checkpoint,exitkill_set=self.options,
            capability=self.capability,library_verified=self.library_verified,instructions_verified=self.instructions_verified,mapping_verified=self.mapping_verified,
            breakpoints_armed=self.armed,breakpoints_cleared=self.cleared,mode_expected=self.mode,hits=self.hits,stages=dict(self.values))


def child_main():
    emit=lambda value:print(json.dumps(value,separators=(",",":")),flush=True)
    raw=sys.stdin.buffer.readline(129)
    require(raw.endswith(b"\n") and len(raw)<=128,"REQUEST_INVALID")
    value=json.loads(raw,object_pairs_hook=unique_object)
    require(type(value) is dict and set(value)=={"probe_id"} and type(value["probe_id"]) is str and re.fullmatch(r"[0-9a-f]{32}",value["probe_id"]),"REQUEST_INVALID")
    require(os.getuid()==UID,"UID_MISMATCH")
    binding=dict(probe_id=value["probe_id"],pid=os.getpid(),uid=UID)
    traced=False;number=None
    try:Backend().call(0);traced=True;number=0
    except PtraceError as error:number=error.number
    except (OSError,ValueError,AttributeError):pass
    emit(dict(phase="traceme",success=traced,errno=number,**binding))
    if traced:os.kill(os.getpid(),signal.SIGSTOP)
    def checkpoint():
        emit(dict(phase="initialize_checkpoint",**binding));os.kill(os.getpid(),signal.SIGSTOP)
    return egl.child_main(before_initialize=checkpoint if traced else None)


def decode_event(line,pid,probe_id):
    try:value=json.loads(line,object_pairs_hook=unique_object)
    except (ValueError,UnicodeError):raise InstallError("CHILD_OUTPUT_INVALID") from None
    if type(value) is dict and value.get("phase") in {"traceme","initialize_checkpoint"}:
        require(value.get("probe_id")==probe_id and type(value.get("pid")) is int and value["pid"]==pid and value.get("uid")==UID,"CHILD_OUTPUT_INVALID")
        if value["phase"]=="traceme":
            require(set(value)=={"phase","probe_id","pid","uid","success","errno"} and type(value["success"]) is bool and
                    (type(value["errno"]) is int and value["errno"]==0 if value["success"] else value["errno"] is None or type(value["errno"]) is int and 0<value["errno"]<4096),"CHILD_OUTPUT_INVALID")
        else:require(set(value)=={"phase","probe_id","pid","uid"},"CHILD_OUTPUT_INVALID")
        return value
    return egl.decode_event(line)


def prior_failure_matches(api,code):
    if api is None or api["initialize"] is None:return None
    return (api["supported"] and api["entrypoint"]=="core" and api["query_error"]["value"]==0x3000 and
            api["display"]["returned"] and api["display"]["error"]["value"]==0x3000 and
            not api["initialize"]["returned"] and api["initialize"]["error"]["value"]==0x3001 and code==1)


def observe(command,environment,control_fd,checker,probe_id,image,*,timeout=egl.TIMEOUT,grace=2.0,backend=None,architecture=None):
    """差替えはlocalhost fixtureだけ。terminal statusはWNOWAITで保持する。"""
    backend=backend or Backend();selector=selectors.DefaultSelector();blocking=os.get_blocking(control_fd)
    try:
        os.set_blocking(control_fd,False)
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,start_new_session=True,close_fds=True)
    except Exception:selector.close();os.set_blocking(control_fd,blocking);raise
    trace=Trace(backend,child.pid,image,architecture);events={};pending=bytearray();control=bytearray();output=dict(stdout=0,stderr=0)
    logs=egl.InitLogSummary(True);started=time.monotonic();last_check=0;stop_at=None;failure=cancel_reason=None
    term_sent=kill_sent=term_forwarded=timed_out=False
    try:child.stdin.write(canonical(dict(probe_id=probe_id))+b"\n");child.stdin.flush()
    except (OSError,ValueError):pass
    try:child.stdin.close()
    except (OSError,ValueError):pass
    def send(number):
        nonlocal term_sent,kill_sent
        try:
            os.killpg(child.pid,number)
            if number==signal.SIGTERM:term_sent=True
            if number==signal.SIGKILL:kill_sent=True
        except ProcessLookupError:pass
    def stop(reason):
        nonlocal stop_at,cancel_reason
        if cancel_reason is None:cancel_reason=reason
        if stop_at is None:stop_at=time.monotonic();send(signal.SIGTERM)
    def read_pipe(key):
        nonlocal pending,failure
        try:data=os.read(key.fd,65536)
        except BlockingIOError:return
        if not data:selector.unregister(key.fd);return
        output[key.data]+=len(data)
        if key.data=="stderr":logs.feed(data)
        elif output["stdout"]<=egl.MAX_STDOUT:
            pending.extend(data)
            while b"\n" in pending:
                line,_,rest=pending.partition(b"\n");pending=bytearray(rest)
                try:
                    event=decode_event(line,child.pid,probe_id)
                    require(event["phase"] not in events,"CHILD_OUTPUT_INVALID");events[event["phase"]]=event
                    if event["phase"]=="traceme" and not event["success"]:
                        trace.fail("PTRACE_DENIED" if event["errno"] in (1,13) else "PTRACE_UNAVAILABLE",event["errno"])
                except (OSError,ValueError,TypeError,KeyError):failure="CHILD_OUTPUT_INVALID";stop(failure)
        if output["stdout"]>egl.MAX_STDOUT or output["stderr"]>egl.TRACE_LIMIT:stop("OUTPUT_LIMIT")
    def ready(wait):
        for key,_ in selector.select(wait):
            if key.data!="control":read_pipe(key);continue
            data=os.read(key.fd,1024)
            if not data:selector.unregister(key.fd);stop("CONTROL_EOF");continue
            control.extend(data)
            if len(control)>1024 or b"\n" in control and control!=b'{"type":"cancel"}\n':stop("CONTROL_INVALID")
            elif control==b'{"type":"cancel"}\n':stop("HOST_CANCEL")
    def stopped():
        nonlocal term_forwarded
        try:event=os.waitid(os.P_PID,child.pid,os.WSTOPPED|os.WNOHANG)
        except ChildProcessError:
            if os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None:return
            raise
        if event is None:return
        ready(0) # stopより前にflushした固定receiptを読む。
        number=event.si_status;forward=0
        if stop_at is not None:
            trace.disarm();forward=signal.SIGTERM;term_forwarded=True
        elif number==signal.SIGSTOP and not trace.initial:
            if not events.get("traceme",{}).get("success"):trace.fail("CHECKPOINT_MISSING")
            trace.start()
        elif number==signal.SIGSTOP and not trace.checkpoint and "initialize_checkpoint" in events:
            if not trace.prepare():stop("DISARM_UNCONFIRMED");forward=signal.SIGTERM;term_forwarded=True
        elif number==signal.SIGTRAP and trace.armed and not trace.cleared:
            if not trace.trap():stop("DISARM_UNCONFIRMED");forward=signal.SIGTERM;term_forwarded=True
            elif not trace.last_trap_owned:forward=signal.SIGTRAP
        else:
            trace.fail("UNEXPECTED_STOP")
            trace.disarm();forward=number # SIGSEGV等の元signalも保持。
        try:backend.call(7,child.pid,data=forward)
        except PtraceError as error:trace.fail(str(error),error.number);stop("PTRACE_UNSUPPORTED")
    try:
        for name in ("stdout","stderr"):
            pipe=getattr(child,name);os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,name)
        selector.register(control_fd,selectors.EVENT_READ,"control")
        while True:
            ready(0);stopped()
            exited=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None
            if exited and not egl.group_live(child.pid):break
            now=time.monotonic()
            if now-started>=timeout and not timed_out:timed_out=True;stop("TIMEOUT")
            if now-last_check>=.25 and stop_at is None:
                last_check=now
                try:checker()
                except (OSError,ValueError):stop("RUNTIME_CHANGED")
            if stop_at is not None and now-stop_at>=grace and egl.group_live(child.pid):send(signal.SIGKILL)
            if stop_at is not None and now-stop_at>=grace+2:failure="STOP_TIMEOUT";break
            ready(.02)
    except (OSError,ValueError,TypeError,KeyError):failure="OPERATOR_IO_FAILED";stop("OPERATOR_ERROR")
    finally:
        if egl.group_live(child.pid):
            stop(cancel_reason or "CLEANUP_UNCONFIRMED");until=time.monotonic()+grace
            while egl.group_live(child.pid) and time.monotonic()<until:
                try:stopped();ready(.02)
                except (OSError,ValueError):pass
            if egl.group_live(child.pid):send(signal.SIGKILL)
        until=time.monotonic()+2
        while egl.group_live(child.pid) and time.monotonic()<until:
            try:ready(.02)
            except (OSError,ValueError):pass
        reclaimed=not egl.group_live(child.pid);reaped=False
        try:child.wait(timeout=2);reaped=True
        except subprocess.TimeoutExpired:failure="CLEANUP_UNCONFIRMED"
        until=time.monotonic()+1
        while any(k.data!="control" for k in selector.get_map().values()) and time.monotonic()<until:
            try:ready(.02)
            except (OSError,ValueError):break
        drained=not any(k.data!="control" for k in selector.get_map().values())
        selector.close();child.stdout.close();child.stderr.close();os.set_blocking(control_fd,blocking)
    if pending:failure="CHILD_OUTPUT_INVALID"
    summary=logs.summary();summary["complete"]&=drained
    api=events.get("api",{}).get("result");cleanup=reaped and reclaimed and drained
    expected=0 if api and api["initialize"] and api["initialize"]["returned"] else 1 if api and api["supported"] else 2
    required={"api","loaded_libraries","egl_cleanup","runtime"}
    api_ok=cleanup and cancel_reason is None and failure is None and child.returncode==expected and required<=set(events) and \
        events["loaded_libraries"]["result"]["status"]=="verified" and events["runtime"]["unchanged"] and summary["complete"]
    internal=trace.report()
    return dict(status="diagnosed" if api_ok and internal["status"]=="observed" else "unsupported" if api_ok else "failed",
        loading_profile=LOADING_PROFILE,prior_initialize_failure_matches=prior_failure_matches(api,child.returncode),
        api_observation="diagnosed" if api_ok else "failed",api=api,loaded_libraries=events.get("loaded_libraries",{}).get("result"),
        egl_cleanup=events.get("egl_cleanup",{}).get("result"),runtime_unchanged=events.get("runtime",{}).get("unchanged"),
        child_blocked=events.get("blocked",{}).get("reason"),internal=internal,child_pid=child.pid,child_group=child.pid,
        child_returncode=child.returncode,termination="unreaped" if child.returncode is None else "signal" if child.returncode<0 else "natural",
        term_sent=term_sent,term_forwarded=term_forwarded,kill_sent=kill_sent,timed_out=timed_out,cancel_reason=cancel_reason,error=failure,
        reaped=reaped,group_reclaimed=reclaimed,pipes_drained=drained,cleanup_confirmed=cleanup,output_bytes=output,egl_log=summary)


def remote_main():
    previous={};interrupted={"value":False};os.umask(0o077)
    try:
        raw=bytearray()
        while len(raw)<=128:
            byte=os.read(0,1)
            if not byte:break
            raw.extend(byte)
            if byte==b"\n":break
        require(raw.endswith(b"\n") and len(raw)<=128,"REQUEST_INVALID")
        value=json.loads(raw,object_pairs_hook=unique_object)
        require(type(value) is dict and set(value)=={"probe_id"} and type(value["probe_id"]) is str and re.fullmatch(r"[0-9a-f]{32}",value["probe_id"]),"REQUEST_INVALID")
        require(type(CHILD_SOURCE) is str and CHILD_SOURCE,"CHILD_SOURCE_MISSING")
        home=termux_home(sys.executable);before=egl.validate_runtime(home);directory=egl.private_run(home,value["probe_id"])
        for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):previous[number]=signal.signal(number,lambda *_:interrupted.__setitem__("value",True))
        def checker():require(not interrupted["value"] and egl.validate_runtime(home)==before,"RUNTIME_LIBRARY_CHANGED")
        result=observe([sys.executable,"-c",child_bootstrap(CHILD_SOURCE)],egl.fixed_environment(home,directory),0,checker,value["probe_id"],
                       lambda backend,pid:verified_image(home,before,pid,backend))
        try:checker()
        except (OSError,ValueError):result.update(status="failed",runtime_unchanged=False,error="RUNTIME_LIBRARY_CHANGED")
        result.update(schema=1,operator=OPERATOR,probe_id=value["probe_id"],uid=UID,libraries=dict(egl.LIBRARIES),
                      wayland="not_run",sdk="not_run",drawing="not_run",input="not_run",hmd="not_run")
        fd=os.open(directory/"report.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as stream:stream.write(canonical(result)+b"\n");stream.flush();os.fsync(stream.fileno())
        print(json.dumps(dict(phase="finished",result=result)),flush=True)
        return 0 if result["status"]=="diagnosed" else 2 if result["status"]=="unsupported" else 1
    except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as error:
        reason=str(error) if isinstance(error,InstallError) and str(error) in BLOCKED else "RUNTIME_UNAVAILABLE"
        print(json.dumps(dict(phase="blocked",reason=reason)),flush=True);return 2
    finally:
        for number,handler in previous.items():signal.signal(number,handler)
