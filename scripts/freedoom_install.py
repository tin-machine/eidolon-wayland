"""固定Freedoom8fileのpin/stream/owned配備。実engineの起動やcatalog変更はしない。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import sys
import time
from sdl_probe_install import InstallError,require,termux_home,unique_object
import freedoom_probe_prepare as source

UID=10158
HEADER_LIMIT=16384
TRANSFER_TIMEOUT=180.0
MAX_ENGINE=16*1024*1024
ENGINE_SHA="abcb1d304f6c9f859d46c156990c9db214b0a3e67707170a7655844dc0c5c253"
ENGINE_BYTES=801632
REASONS={"UID_MISMATCH","REQUEST_INVALID","MANIFEST_INVALID","MANIFEST_PIN_MISMATCH","FILE_PIN_MISMATCH","FILE_UNSAFE","FILE_CHANGED",
         "DIRECTORY_UNSAFE","RUN_EXISTS","TRANSFER_EOF","TRANSFER_LIMIT","TRANSFER_TIMEOUT","TRANSFER_CANCELLED","PAYLOAD_PIN_MISMATCH",
         "ENGINE_MISSING","ENGINE_UNSAFE","ENGINE_PIN_MISMATCH","ENGINE_ELF_INVALID","ENGINE_VERSION_MISMATCH","INSTALL_IO_FAILED"}


def canonical(value):return source.canonical(value)
def sha(data):return hashlib.sha256(data).hexdigest()
def identity(info):return source.identity(info)
def hex64(value):return type(value) is str and re.fullmatch(r"[0-9a-f]{64}",value) is not None


def expected_files(run_id):
    values={"assets/"+n:dict(bytes=v[0],sha256=v[1]) for n,v in source.MEMBERS.items()}
    for name,data in (("assets/eidlsmok.lmp",None),("config/default.cfg",b"\n"),("config/chocolate-doom.cfg",source.EXTRA_CONFIG),
                      ("launch-plan.json",canonical(source.launch_plan(run_id))+b"\n")):
        values[name]=dict(bytes=source.DEMO_BYTES,sha256=source.DEMO_SHA) if data is None else dict(bytes=len(data),sha256=sha(data))
    return values


def validate_manifest(raw,pin):
    require(hex64(pin) and type(raw) is bytes and 0<len(raw)<=HEADER_LIMIT and sha(raw)==pin,"MANIFEST_PIN_MISMATCH")
    try:value=json.loads(raw,object_pairs_hook=unique_object)
    except (ValueError,UnicodeError):raise InstallError("MANIFEST_INVALID") from None
    require(type(value) is dict and type(value.get("run_id")) is str and re.fullmatch(r"[0-9a-f]{32}",value["run_id"]),"MANIFEST_INVALID")
    require(type(value.get("files")) is dict and set(value["files"])==set(expected_files(value["run_id"])) and
            all(type(v) is dict and set(v)=={"bytes","sha256"} and type(v["bytes"]) is int and hex64(v["sha256"]) for v in value["files"].values()),"MANIFEST_INVALID")
    expected=dict(schema=1,kind="eidolon-freedoom-source-v1",run_id=value["run_id"],version=source.VERSION,
        archive=dict(url=source.ARCHIVE_URL,bytes=source.ARCHIVE_BYTES,sha256=source.ARCHIVE_SHA),
        demo=dict(source="freedoom1.wad/DEMO1",source_sha256=source.DEMO_SOURCE_SHA,tics=128,bytes=source.DEMO_BYTES,sha256=source.DEMO_SHA),
        files=expected_files(value["run_id"]),signature_verification="not_performed",catalog="not_modified",execution="not_run",termux_install="not_run")
    require(value==expected and type(value["schema"]) is int,"MANIFEST_INVALID")
    return value


def open_child(directory,name,uid,*,mode=0o700,create=False,exclusive=False):
    if create:
        try:os.mkdir(name,0o700,dir_fd=directory)
        except FileExistsError:require(not exclusive,"RUN_EXISTS")
    fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
    info=os.fstat(fd)
    if info.st_uid!=uid or info.st_mode&0o022 or mode is not None and stat.S_IMODE(info.st_mode)!=mode:
        os.close(fd);raise InstallError("DIRECTORY_UNSAFE")
    return fd


def open_root(path,uid):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);info=os.fstat(fd)
    if info.st_uid!=uid or info.st_mode&0o022:os.close(fd);raise InstallError("DIRECTORY_UNSAFE")
    return fd


def fixed_file(root,name,uid):
    parts=name.split("/");fd=os.dup(root)
    try:
        for part in parts[:-1]:
            child=open_child(fd,part,uid);os.close(fd);fd=child
        file_fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        info=os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o600:
            os.close(file_fd);raise InstallError("FILE_UNSAFE")
        return file_fd
    finally:os.close(fd)


def validate_prepared(directory,pin):
    uid=os.getuid();root=open_root(directory,uid);identities={};payload=hashlib.sha256()
    try:
        with os.fdopen(fixed_file(root,"manifest.json",uid),"rb") as f:
            before=os.fstat(f.fileno());raw=f.read(HEADER_LIMIT+1)
            require(identity(before)==identity(os.fstat(f.fileno())),"FILE_CHANGED")
        manifest=validate_manifest(raw,pin)
        for name,expected in sorted(manifest["files"].items()):
            with os.fdopen(fixed_file(root,name,uid),"rb") as stream:
                before=os.fstat(stream.fileno());digest=hashlib.sha256();count=0
                require(before.st_size==expected["bytes"],"FILE_PIN_MISMATCH")
                while True:
                    block=stream.read(65536)
                    if not block:break
                    count+=len(block);require(count<=expected["bytes"],"FILE_PIN_MISMATCH");digest.update(block);payload.update(block)
                require(count==expected["bytes"] and digest.hexdigest()==expected["sha256"] and identity(before)==identity(os.fstat(stream.fileno())),"FILE_PIN_MISMATCH")
                identities[name]=identity(before)
        return raw,manifest,identities,payload.hexdigest()
    finally:os.close(root)


def stream_chunks(directory,raw,pin,engine_pin,identities,payload_pin):
    manifest=validate_manifest(raw,pin);require(hex64(engine_pin) and hex64(payload_pin),"REQUEST_INVALID")
    header=canonical(dict(schema=1,run_id=manifest["run_id"],manifest_base64=base64.b64encode(raw).decode(),manifest_sha256=pin,
                          engine_sha256=engine_pin,payload_sha256=payload_pin))+b"\n"
    require(len(header)<=HEADER_LIMIT,"TRANSFER_LIMIT");yield header
    uid=os.getuid();root=open_root(directory,uid)
    try:
        for name,expected in sorted(manifest["files"].items()):
            with os.fdopen(fixed_file(root,name,uid),"rb") as stream:
                before=os.fstat(stream.fileno());require(identity(before)==identities[name],"FILE_CHANGED");digest=hashlib.sha256();count=0
                while True:
                    block=stream.read(65536)
                    if not block:break
                    count+=len(block);require(count<=expected["bytes"],"FILE_CHANGED");digest.update(block);yield block
                require(count==expected["bytes"] and digest.hexdigest()==expected["sha256"] and identity(before)==identity(os.fstat(stream.fileno())),"FILE_CHANGED")
    finally:os.close(root)


def engine(home,pin,*,uid=UID):
    require(hex64(pin) and pin==ENGINE_SHA,"ENGINE_PIN_MISMATCH");prefix=home.parent/"usr";path=prefix/"bin/chocolate-doom"
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:raise InstallError("ENGINE_MISSING") from None
    with os.fdopen(fd,"rb") as stream:
        before=os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid==uid and stat.S_IMODE(before.st_mode)==0o700 and before.st_nlink==1 and before.st_size==ENGINE_BYTES,"ENGINE_UNSAFE")
        data=stream.read(MAX_ENGINE+1);after=os.fstat(stream.fileno())
        require(len(data)==before.st_size and identity(before)==identity(after) and sha(data)==pin,"ENGINE_PIN_MISMATCH")
        require(data[:7]==b"\x7fELF\x02\x01\x01" and struct.unpack_from("<H",data,18)[0]==183,"ENGINE_ELF_INVALID")
    status=prefix/"var/lib/dpkg/status"
    try:
        with os.fdopen(os.open(status,os.O_RDONLY|os.O_NOFOLLOW),"rb") as stream:
            info=os.fstat(stream.fileno());require(stat.S_ISREG(info.st_mode) and info.st_uid==uid and not info.st_mode&0o022 and info.st_size<=8*1024*1024,"ENGINE_VERSION_MISMATCH")
            text=stream.read(8*1024*1024+1);require(len(text)==info.st_size and identity(info)==identity(os.fstat(stream.fileno())),"ENGINE_VERSION_MISMATCH")
    except FileNotFoundError:raise InstallError("ENGINE_VERSION_MISMATCH") from None
    matches=[]
    for paragraph in text.split(b"\n\n"):
        fields={}
        for line in paragraph.splitlines():
            if line.startswith(b" ") or b": " not in line:continue
            key,value=line.split(b": ",1)
            if key in {b"Package",b"Version",b"Status"}:require(key not in fields,"ENGINE_VERSION_MISMATCH");fields[key]=value
        if fields.get(b"Package")==b"chocolate-doom":matches.append(fields)
    require(len(matches)==1 and matches[0].get(b"Version")==b"3.1.1" and matches[0].get(b"Status")==b"install ok installed","ENGINE_VERSION_MISMATCH")
    return dict(package="chocolate-doom",version="3.1.1",sha256=pin,bytes=before.st_size,uid=before.st_uid,mode=stat.S_IMODE(before.st_mode),nlink=before.st_nlink,elf_machine=183,identity=list(identity(before)))


class Reader:
    def __init__(self,fd,timeout=TRANSFER_TIMEOUT,interrupted=lambda:False):
        self.fd=fd;self.deadline=time.monotonic()+timeout;self.interrupted=interrupted;self.wire=hashlib.sha256()
        self.selector=selectors.DefaultSelector();self.blocking=os.get_blocking(fd);os.set_blocking(fd,False);self.selector.register(fd,selectors.EVENT_READ)
    def read(self,size):
        while True:
            require(not self.interrupted(),"TRANSFER_CANCELLED");require(time.monotonic()<self.deadline,"TRANSFER_TIMEOUT")
            if self.selector.select(min(.1,max(0,self.deadline-time.monotonic()))):
                try:data=os.read(self.fd,size)
                except BlockingIOError:continue
                self.wire.update(data);return data
    def header(self):
        data=bytearray()
        while len(data)<HEADER_LIMIT:
            byte=self.read(1);require(byte,"TRANSFER_EOF");data.extend(byte)
            if byte==b"\n":return bytes(data)
        raise InstallError("TRANSFER_LIMIT")
    def close(self):self.selector.close();os.set_blocking(self.fd,self.blocking)


def install(reader,home,*,uid=UID):
    require(os.getuid()==uid,"UID_MISMATCH")
    try:header=json.loads(reader.header(),object_pairs_hook=unique_object)
    except (ValueError,UnicodeError):raise InstallError("REQUEST_INVALID") from None
    require(type(header) is dict and set(header)=={"schema","run_id","manifest_base64","manifest_sha256","engine_sha256","payload_sha256"} and
            type(header["schema"]) is int and header["schema"]==1 and hex64(header["payload_sha256"]),"REQUEST_INVALID")
    try:raw=base64.b64decode(header["manifest_base64"],validate=True)
    except (ValueError,TypeError):raise InstallError("REQUEST_INVALID") from None
    manifest=validate_manifest(raw,header["manifest_sha256"]);require(header["run_id"]==manifest["run_id"],"REQUEST_INVALID")
    checked_engine=engine(home,header["engine_sha256"],uid=uid)
    root=open_root(home,uid);run_fd=None;payload=hashlib.sha256();counts={}
    try:
        for part in (".local","share","eidolon","freedoom",source.VERSION):
            child=open_child(root,part,uid,mode=None,create=True);os.close(root);root=child
        run_fd=open_child(root,manifest["run_id"],uid,create=True,exclusive=True)
        for part in ("assets","config"):
            fd=open_child(run_fd,part,uid,create=True,exclusive=True);os.close(fd)
        for name,expected in sorted(manifest["files"].items()):
            parts=name.split("/");parent=os.dup(run_fd)
            try:
                for part in parts[:-1]:child=open_child(parent,part,uid);os.close(parent);parent=child
                fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
                with os.fdopen(fd,"wb") as stream:
                    count=0;digest=hashlib.sha256()
                    while count<expected["bytes"]:
                        data=reader.read(min(65536,expected["bytes"]-count));require(data,"TRANSFER_EOF");stream.write(data);digest.update(data);payload.update(data);count+=len(data)
                    require(digest.hexdigest()==expected["sha256"],"FILE_PIN_MISMATCH");stream.flush();os.fsync(stream.fileno());counts[name]=count
            finally:os.close(parent)
        require(reader.read(1)==b"","TRANSFER_LIMIT")
        require(payload.hexdigest()==header["payload_sha256"],"PAYLOAD_PIN_MISMATCH")
        require(engine(home,header["engine_sha256"],uid=uid)==checked_engine,"ENGINE_PIN_MISMATCH")
        for part in ("assets","config"):
            child=open_child(run_fd,part,uid)
            try:os.fsync(child)
            finally:os.close(child)
        for name,data in (("manifest.json",raw),("install-receipt.json",canonical(dict(schema=1,kind="eidolon-freedoom-installed-v1",run_id=manifest["run_id"],
                     uid=uid,manifest_sha256=header["manifest_sha256"],payload_sha256=payload.hexdigest(),wire_sha256=reader.wire.hexdigest(),engine=checked_engine,
                     files=counts,execution="not_run",catalog="not_modified"))+b"\n")):
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=run_fd)
            with os.fdopen(fd,"wb") as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.fsync(run_fd)
        return dict(schema=1,status="installed",run_id=manifest["run_id"],uid=uid,manifest_sha256=header["manifest_sha256"],payload_sha256=payload.hexdigest(),
                    wire_sha256=reader.wire.hexdigest(),files=counts,engine=checked_engine,execution="not_run",catalog="not_modified")
    finally:
        if run_fd is not None:os.close(run_fd)
        os.close(root)


def remote_main():
    os.umask(0o077);reader=None;previous={};interrupted={"value":False}
    try:
        require(os.getuid()==UID,"UID_MISMATCH")
        for number in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):previous[number]=signal.signal(number,lambda *_:interrupted.__setitem__("value",True))
        reader=Reader(0,interrupted=lambda:interrupted["value"]);result=install(reader,termux_home(sys.executable))
        print(json.dumps(result),flush=True);return 0
    except (OSError,ValueError,TypeError,KeyError) as error:
        reason=str(error) if isinstance(error,InstallError) and str(error) in REASONS else "INSTALL_IO_FAILED"
        print(json.dumps(dict(schema=1,status="failed",reason=reason,execution="not_run",catalog="not_modified")),flush=True);return 1
    finally:
        if reader is not None:reader.close()
        for number,handler in previous.items():signal.signal(number,handler)
