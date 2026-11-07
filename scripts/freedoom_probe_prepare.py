"""固定Freedoom Phase1だけのhost準備。実行/転送/catalog変更は行わない。"""
import hashlib
import io
import json
import os
import re
import stat
import struct
import zipfile

VERSION="0.13.0"
ARCHIVE_URL="https://github.com/freedoom/freedoom/releases/download/v0.13.0/freedoom-0.13.0.zip"
ARCHIVE_BYTES=24143781
ARCHIVE_SHA="3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59"
MEMBERS={
    "freedoom1.wad":(28795076,"7323bcc168c5a45ff10749b339960e98314740a734c30d4b9f3337001f9e703d"),
    "COPYING.txt":(1644,"7c62f2c520769c798774f416637eec4921e5f0aafdac1245ae7c8a8cf65fe102"),
    "CREDITS.txt":(15872,"72364333d295c598d75ef9553d3b3661ad0b60e27271c87b7637c74b2f46918a"),
    "CREDITS-MUSIC.txt":(9388,"2bb77677bbc4e587bba44ccb6514ee0f5397c7bb8ae9580b9188d3d6a57c0a31"),
}
DEMO_SOURCE_BYTES=28482
DEMO_SOURCE_SHA="1b2be1878d9eabd866dc2766a63254dcb33020532a8d1dee82de7a01d777e27c"
DEMO_BYTES=526
DEMO_SHA="6cdd695fa613d6291c4e0fae9fef5237337cba806a4464beaf945934201487ce"
CASES=("demo128","interactive")
PROFILE="sdl-wayland-softpipe-kopper-disabled"
EXTRA_CONFIG=("fullscreen 0\nwindow_width 640\nwindow_height 400\nforce_software_renderer 1\n"
              "smooth_pixel_scaling 0\ngraphical_startup 0\nshow_endoom 0\n").encode()


class PrepareError(ValueError):pass


def need(value,code):
    if not value:raise PrepareError(code)


def canonical(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()


def digest(data):return hashlib.sha256(data).hexdigest()


def identity(info):return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns


def read_archive(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),"rb") as stream:
        before=os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and not before.st_mode&0o022 and
             before.st_size==ARCHIVE_BYTES,"ARCHIVE_UNSAFE")
        data=stream.read(ARCHIVE_BYTES+1)
        need(len(data)==ARCHIVE_BYTES and identity(before)==identity(os.fstat(stream.fileno())) and digest(data)==ARCHIVE_SHA,"ARCHIVE_PIN_MISMATCH")
        return data


def selected_members(data,*,expected=MEMBERS):
    """expected差替えは小さなhost fixtureのみ。CLIは固定pin以外を受けない。"""
    result={}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            rows=archive.infolist();need(0<len(rows)<=64,"ZIP_LIMIT");names=set();total=0
            for row in rows:
                name=row.filename;parts=name.rstrip("/").split("/");mode=row.external_attr>>16
                need(type(name) is str and 0<len(name)<=160 and name.isascii() and "\x00" not in name and "\\" not in name and
                     not name.startswith("/") and all(p not in {"",".",".."} for p in parts) and all(32<=ord(c)<127 for c in name),"ZIP_PATH_INVALID")
                need(name not in names,"ZIP_DUPLICATE");names.add(name)
                need(not row.flag_bits&1 and row.compress_type in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED} and
                     stat.S_IFMT(mode) in {0,stat.S_IFREG,stat.S_IFDIR} and not stat.S_ISLNK(mode),"ZIP_TYPE_INVALID")
                need(not stat.S_ISDIR(mode) or row.is_dir(),"ZIP_TYPE_INVALID")
                need(0<=row.file_size<=64*1024*1024 and 0<=row.compress_size<=32*1024*1024,"ZIP_LIMIT")
                total+=row.file_size;need(total<=128*1024*1024,"ZIP_LIMIT")
            for name,(size,sha) in expected.items():
                full="freedoom-"+VERSION+"/"+name
                need(full in names,"ASSET_MISSING");row=archive.getinfo(full)
                need(not row.is_dir() and row.file_size==size,"ASSET_PIN_MISMATCH")
                with archive.open(row) as stream:body=stream.read(size+1)
                need(len(body)==size and digest(body)==sha,"ASSET_PIN_MISMATCH");result[name]=body
    except (zipfile.BadZipFile,RuntimeError,EOFError,NotImplementedError):raise PrepareError("ZIP_INVALID") from None
    return result


def wad_demo(wad):
    need(len(wad)>=12 and wad[:4]==b"IWAD","WAD_INVALID")
    count,offset=struct.unpack_from("<II",wad,4)
    need(0<count<=10000 and 12<=offset<=len(wad) and count*16<=len(wad)-offset,"WAD_INVALID")
    found=[]
    for i in range(count):
        start,size,name=struct.unpack_from("<II8s",wad,offset+i*16)
        need(start<=len(wad) and size<=len(wad)-start,"WAD_INVALID")
        if name.rstrip(b"\0")==b"DEMO1":found.append((start,size))
    need(len(found)==1,"DEMO_INVALID");start,size=found[0];data=wad[start:start+size]
    need(size==DEMO_SOURCE_BYTES and digest(data)==DEMO_SOURCE_SHA and len(data)>=13+128*4 and
         data[0]==109 and data[2:4]==bytes((1,6)) and data[9:13]==bytes((1,0,0,0)),"DEMO_INVALID")
    short=data[:13+128*4]+b"\x80"
    need(len(short)==DEMO_BYTES and digest(short)==DEMO_SHA,"DEMO_PIN_MISMATCH")
    return short


def fixed_case(case):
    need(type(case) is str and case in CASES,"CASE_INVALID")
    # 参照は専用installerがTermux HOME由来のowned pathへ解決する。shell展開ではない。
    args=["-iwad",{"asset":"freedoom1.wad"},"-config",{"state":"default.cfg"},"-extraconfig",{"state":"chocolate-doom.cfg"},
          "-savedir",{"state_directory":"save"},"-window","-width","640","-height","400","-nosound","-nomouse","-nograbmouse"]
    args+=( ["-playdemo",{"asset":"eidlsmok.lmp"}] if case=="demo128" else ["-warp","1","1","-skill","1"] )
    return dict(case=case,arguments=args,exit_contract="natural_demo_end" if case=="demo128" else "owned_quit_or_stop",
                physical_input="not_verified",hmd="not_verified")


def launch_plan(run_id):
    need(type(run_id) is str and re.fullmatch(r"[0-9a-f]{32}",run_id),"RUN_ID_INVALID")
    return dict(schema=1,kind="eidolon-freedoom-prepared-plan-v1",run_id=run_id,freedoom_version=VERSION,
        engine=dict(package="chocolate-doom",version="3.1.1",binary="chocolate-doom",binary_sha256="pending_installed_pin"),
        profile=PROFILE,mode="gui",icon="builtin:game",cases=[fixed_case(c) for c in CASES],
        graphics_environment=dict(SDL_VIDEODRIVER="wayland",LIBGL_ALWAYS_SOFTWARE="1",EGL_PLATFORM="wayland",GALLIUM_DRIVER="softpipe",LIBGL_KOPPER_DISABLE="1"),
        wayland_environment="authenticated_current_lease_two_values_only",
        templates={"default.cfg":"config/default.cfg","chocolate-doom.cfg":"config/chocolate-doom.cfg"},
        state_contract="new_owned_state_copy_templates_before_exec",app_id_binding="pending_source_and_runtime_verification",
        catalog=dict(automatically_registered=False,available=False,error="GUI_NOT_VERIFIED"),
        gates=["engine_pin","owned_asset_install","sdl_shm_sdk_full_retirement","fixed_demo_natural_exit","owned_quit_stop_repeat","gui_profile_optin"],
        execution="not_run",termux_install="not_run")


def write_new(directory,name,data):
    fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())


def create_bundle(directory,run_id,assets,demo):
    """選択・pin検査済みの固定ファイルだけ。任意filenameやcatalog書込みを受けない。"""
    need(set(assets)==set(MEMBERS) and all(len(assets[n])==MEMBERS[n][0] and digest(assets[n])==MEMBERS[n][1] for n in MEMBERS),"ASSET_PIN_MISMATCH")
    need(len(demo)==DEMO_BYTES and digest(demo)==DEMO_SHA,"DEMO_PIN_MISMATCH")
    plan=launch_plan(run_id)
    directory.mkdir(mode=0o700) # 既存runは途中失敗も含めて上書きしない。
    (directory/"assets").mkdir(mode=0o700);(directory/"config").mkdir(mode=0o700)
    files={"assets/"+name:body for name,body in assets.items()}
    files.update({"assets/eidlsmok.lmp":demo,"config/default.cfg":b"\n","config/chocolate-doom.cfg":EXTRA_CONFIG,
                  "launch-plan.json":canonical(plan)+b"\n"})
    for name,data in files.items():write_new(directory,name,data)
    manifest=dict(schema=1,kind="eidolon-freedoom-source-v1",run_id=run_id,version=VERSION,
        archive=dict(url=ARCHIVE_URL,bytes=ARCHIVE_BYTES,sha256=ARCHIVE_SHA),
        demo=dict(source="freedoom1.wad/DEMO1",source_sha256=DEMO_SOURCE_SHA,tics=128,bytes=DEMO_BYTES,sha256=DEMO_SHA),
        files={name:dict(bytes=len(data),sha256=digest(data)) for name,data in files.items()},
        signature_verification="not_performed",catalog="not_modified",execution="not_run",termux_install="not_run")
    write_new(directory,"manifest.json",canonical(manifest)+b"\n")
    return manifest


def prepare(archive,directory,run_id):
    data=read_archive(archive);assets=selected_members(data);demo=wad_demo(assets["freedoom1.wad"])
    return create_bundle(directory,run_id,assets,demo)
