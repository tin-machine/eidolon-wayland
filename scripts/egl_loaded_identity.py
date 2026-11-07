"""既知Mesa依存3個だけのlinker列挙/実mapping/file identity。任意maps/アドレスは返さない。"""
import ctypes
import hashlib
import os
from pathlib import Path
import re
import stat
from sdl_probe_install import InstallError, require

NAMES = ("libEGL_mesa.so", "libgallium-26.2.6.so", "libc++_shared.so")
ALIASES = {"libEGL_mesa.so.0": "libEGL_mesa.so"}
PROFILE = "termux-egl-loaded-identity-v1"
MAX_MAPS = 1024 * 1024
MAX_LIBRARY = 32 * 1024 * 1024
REASONS = {"LOADED_ENUM_UNAVAILABLE", "LOADED_ENUM_SCHEMA", "LOADED_ENUM_LIMIT", "LOADED_LIBRARY_MISSING",
           "LOADED_LIBRARY_DUPLICATE", "LOADED_LIBRARY_PATH_CHANGED", "LOADED_LIBRARY_DELETED", "LOADED_MAPS_UNAVAILABLE",
           "LOADED_MAPS_SCHEMA", "LOADED_MAPS_LIMIT", "LOADED_MAPS_MISMATCH", "LOADED_FILE_UNSAFE", "LOADED_FILE_CHANGED"}


def selected_name(path, names=NAMES):
    basename=path.rsplit(b"/",1)[-1]
    deleted=basename.endswith(b" (deleted)")
    if deleted:basename=basename[:-10]
    try:basename=basename.decode("ascii")
    except UnicodeError:return None
    name=ALIASES.get(basename,basename)
    if name not in names:return None
    require(not deleted,"LOADED_LIBRARY_DELETED")
    return name


def enumerate_loaded(names=NAMES):
    """AOSP link.hのLP64共通prefixのみ。API30追加fieldやaddressを採取しない。"""
    class PhdrInfo(ctypes.Structure):
        _fields_=[("address",ctypes.c_void_p),("name",ctypes.c_void_p),("headers",ctypes.c_void_p),("count",ctypes.c_ushort)]
    require(ctypes.sizeof(ctypes.c_void_p)==ctypes.sizeof(ctypes.c_size_t)==8,"LOADED_ENUM_SCHEMA")
    try:
        libc=ctypes.CDLL(None)
        strnlen=libc.strnlen;strnlen.argtypes=[ctypes.c_void_p,ctypes.c_size_t];strnlen.restype=ctypes.c_size_t
        callback_type=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.POINTER(PhdrInfo),ctypes.c_size_t,ctypes.c_void_p)
        iterate=libc.dl_iterate_phdr;iterate.argtypes=[callback_type,ctypes.c_void_p];iterate.restype=ctypes.c_int
    except (AttributeError,OSError):raise InstallError("LOADED_ENUM_UNAVAILABLE") from None
    found={name:[] for name in names};state=dict(calls=0,error=None)
    def visit(info,size,_):
        try:
            state["calls"]+=1
            require(state["calls"]<=1024,"LOADED_ENUM_LIMIT")
            require(info and size>=ctypes.sizeof(PhdrInfo),"LOADED_ENUM_SCHEMA")
            pointer=info.contents.name
            if not pointer:return 0
            length=strnlen(pointer,4097)
            require(length<=4096,"LOADED_ENUM_LIMIT")
            path=ctypes.string_at(pointer,length)
            name=selected_name(path,names)
            if name is not None:found[name].append(path)
            return 0
        except (OSError,ValueError,TypeError) as error:
            state["error"]=str(error) if isinstance(error,InstallError) and str(error) in REASONS else "LOADED_ENUM_SCHEMA";return 1
    callback=callback_type(visit)
    result=iterate(callback,None)
    require(result==0 and state["error"] is None,state["error"] or "LOADED_ENUM_SCHEMA")
    return found


def read_self_maps():
    try:
        with open("/proc/self/maps","rb") as stream:data=stream.read(MAX_MAPS+1)
    except OSError:raise InstallError("LOADED_MAPS_UNAVAILABLE") from None
    require(len(data)<=MAX_MAPS,"LOADED_MAPS_LIMIT")
    require(not data or data.endswith(b"\n"),"LOADED_MAPS_SCHEMA")
    return data


def select_maps(data,names=NAMES):
    require(type(data) is bytes and len(data)<=MAX_MAPS,"LOADED_MAPS_LIMIT")
    require(not data or data.endswith(b"\n"),"LOADED_MAPS_SCHEMA")
    found={name:[] for name in names}
    for line in data.splitlines():
        fields=line.split(None,5)
        if len(fields)!=6:continue
        name=selected_name(fields[5],names)
        if name is None:continue  # 未知path/addressは選択も出力もしない。
        require(len(line)<=8192 and re.fullmatch(rb"[0-9a-f]+-[0-9a-f]+",fields[0]) is not None and
                re.fullmatch(rb"[r-][w-][x-][ps]",fields[1]) is not None and
                re.fullmatch(rb"[0-9a-f]+",fields[2]) is not None and re.fullmatch(rb"[0-9a-f]+:[0-9a-f]+",fields[3]) is not None and
                fields[4].isdigit(),"LOADED_MAPS_SCHEMA")
        major,minor=(int(v,16) for v in fields[3].split(b":"))
        found[name].append(dict(path=fields[5],device=(major,minor),inode=int(fields[4]),executable=b"x" in fields[1]))
        require(len(found[name])<=64,"LOADED_MAPS_LIMIT")
    return found


def file_identity(info):return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns


def capture(directory,before,pins,*,expected_uid=10158,names=NAMES,enumerator=enumerate_loaded,maps_reader=read_self_maps):
    """fixture差替えはhost test専用。CLIにはpath/pid/names/pins/readerを公開しない。"""
    rows=[]
    try:
        directory=directory.resolve(strict=True)
        loaded=enumerator(names);maps=select_maps(maps_reader(),names)
        for name in names:
            require(len(loaded[name])>0 and maps[name],"LOADED_LIBRARY_MISSING")
            require(len(loaded[name])==1,"LOADED_LIBRARY_DUPLICATE")
            expected=directory/name
            def canonical_path(raw):
                path=Path(os.fsdecode(raw))
                require(path.is_absolute() and path.resolve(strict=True)==expected,"LOADED_LIBRARY_PATH_CHANGED")
                return str(expected)
            canonical_path(loaded[name][0])
            for mapping in maps[name]:canonical_path(mapping["path"])
            fd=os.open(expected,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,"rb") as stream:
                info=os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_uid==expected_uid and not info.st_mode&0o022 and
                        0<info.st_size<=MAX_LIBRARY,"LOADED_FILE_UNSAFE")
                require(list(file_identity(info))==before[name]["identity"],"LOADED_FILE_CHANGED")
                require(all(m["device"]==(os.major(info.st_dev),os.minor(info.st_dev)) and m["inode"]==info.st_ino for m in maps[name]) and
                        any(m["executable"] for m in maps[name]),"LOADED_MAPS_MISMATCH")
                data=stream.read(MAX_LIBRARY+1)
                require(len(data)==info.st_size and file_identity(info)==file_identity(os.fstat(stream.fileno())) and
                        hashlib.sha256(data).hexdigest()==pins[name],"LOADED_FILE_CHANGED")
                rows.append(dict(name=name,canonical_path=str(expected),device_major=os.major(info.st_dev),device_minor=os.minor(info.st_dev),
                    inode=info.st_ino,bytes=info.st_size,sha256=pins[name],uid=info.st_uid,mode=stat.S_IMODE(info.st_mode)))
        # hash読取り中に別imageへ入れ替わった場合も成功にしない。
        require(enumerator(names)==loaded and select_maps(maps_reader(),names)==maps,"LOADED_MAPS_MISMATCH")
        return dict(profile=PROFILE,status="verified",reason=None,libraries=rows)
    except (OSError,ValueError,TypeError,KeyError) as error:
        reason=str(error) if isinstance(error,InstallError) and str(error) in REASONS else "LOADED_FILE_UNSAFE"
        return dict(profile=PROFILE,status="not_verified",reason=reason,libraries=rows)
