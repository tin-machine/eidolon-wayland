"""固定Freedoom一項目だけのcatalog CAS登録。exec/任意path/envを受け取らない。"""
import hashlib
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import types
from sdl_probe_install import InstallError,require,termux_home,unique_object

UID=10158
FILES={'eidolon-session.py':131072,'eidolon-gui.py':65536,'eidolon_gui_owner.py':32768}
APP_ID='freedoom'
CATALOG='.config/eidolon/launcher.toml'
REASONS={'REQUEST_INVALID','UID_MISMATCH','HELPER_SOURCE_CHANGED','HELPER_SOURCE_UNSAFE','SOURCE_NAMES_INVALID','GUI_NOT_VERIFIED','CATALOG_CHANGED',
    'CATALOG_INVALID','CATALOG_SCHEMA','CATALOG_LIMIT','CATALOG_DUPLICATE_ID','CATALOG_FILE_UNSAFE','CATALOG_UNAVAILABLE','PATH_MISSING','PATH_UNAVAILABLE',
    'LAUNCHER_ID_CONFLICT','REGISTRATION_UNAVAILABLE','STAGING_UNSAFE','CATALOG_PRESERVATION_FAILED','PLATFORM_UNSUPPORTED'}


def hexstr(value,size):return type(value) is str and re.fullmatch('[0-9a-f]{'+str(size)+'}',value) is not None
def sha(data):return hashlib.sha256(data).hexdigest()
def identity(row):return row.st_dev,row.st_ino,row.st_size,row.st_mtime_ns,row.st_ctime_ns


def exclusive_link(parent,source,target):
    """固定basenameを同じowned dirfdでlink。Android Pythonのos.link欠落だけを公開libc ABIで補う。"""
    require(re.fullmatch(r'\.freedoom-[0-9a-f]{32}\.toml',source) is not None and target=='launcher.toml','STAGING_UNSAFE')
    row=os.fstat(parent);require(stat.S_ISDIR(row.st_mode) and row.st_uid==os.getuid() and not row.st_mode&0o022,'STAGING_UNSAFE')
    if hasattr(os,'link'):
        os.link(source,target,src_dir_fd=parent,dst_dir_fd=parent,follow_symlinks=False);return 'python_os_link'
    require(sys.platform=='android' and platform.machine()=='aarch64' and ctypes.sizeof(ctypes.c_void_p)==8 and ctypes.sizeof(ctypes.c_int)==4,'PLATFORM_UNSUPPORTED')
    library=ctypes.CDLL(None,use_errno=True);function=getattr(library,'linkat',None);require(function is not None,'PLATFORM_UNSUPPORTED')
    function.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_int];function.restype=ctypes.c_int
    ctypes.set_errno(0)
    if function(parent,source.encode('ascii'),parent,target.encode('ascii'),0)!=0:
        code=ctypes.get_errno();raise OSError(code,errno.errorcode.get(code,'LINKAT_FAILED'))
    return 'android_bionic_linkat'


def directories(home,parts,*,missing=False):
    root=home
    for part in ('',*parts):
        root=root/part if part else root
        try:row=root.lstat()
        except FileNotFoundError:
            require(missing,'HELPER_SOURCE_UNSAFE');return False
        require(stat.S_ISDIR(row.st_mode) and row.st_uid==os.getuid() and not row.st_mode&0o022,'HELPER_SOURCE_UNSAFE')
    return True


def definition(profile):
    require(type(profile) is dict and set(profile)=={'kind','assets_run_id','manifest_sha256'} and profile['kind']=='freedoom_phase1_software' and
        hexstr(profile['assets_run_id'],32) and hexstr(profile['manifest_sha256'],64),'REQUEST_INVALID')
    return dict(id=APP_ID,label='Freedoom',mode='gui',icon='builtin:game',executable='/data/data/com.termux/files/usr/bin/chocolate-doom',args=[],cwd='.',execution_profile=dict(profile))


def load_helper(home,pins):
    require(type(pins) is dict and set(pins)==set(FILES),'SOURCE_NAMES_INVALID');data={}
    directories(home,('.local','lib','eidolon'));root=home/'.local/lib/eidolon'
    for name,limit in FILES.items():
        info=pins[name];require(type(info) is dict and set(info)=={'bytes','sha256'} and type(info['bytes']) is int and 0<info['bytes']<=limit and hexstr(info['sha256'],64),'REQUEST_INVALID')
        with os.fdopen(os.open(root/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
            before=os.fstat(stream.fileno());require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and stat.S_IMODE(before.st_mode)==0o700 and before.st_nlink==1,'HELPER_SOURCE_UNSAFE')
            raw=stream.read(limit+1);require(len(raw)==info['bytes'] and sha(raw)==info['sha256'] and identity(before)==identity(os.fstat(stream.fileno())),'HELPER_SOURCE_CHANGED');data[name]=raw
    module=types.ModuleType('eidolon_registration_source');module.__file__=str(root/'eidolon-session.py');exec(compile(data['eidolon-session.py'],module.__file__,'exec'),module.__dict__)
    require(module.GUI_WORKER_SHA==pins['eidolon-gui.py']['sha256'],'HELPER_SOURCE_CHANGED');return module


def config_parent(home):
    root=home
    for name in ('.config','eidolon'):
        root=root/name
        try:root.mkdir(mode=0o700)
        except FileExistsError:pass
        info=root.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o022,'CATALOG_FILE_UNSAFE')
    return root


def current(home,helper):
    catalog=helper.LauncherCatalog(home);path=home/CATALOG
    if not directories(home,('.config','eidolon'),missing=True):return catalog,None,False
    try:raw=helper.catalog_read(path)
    except FileNotFoundError:return catalog,None,False
    catalog.reload(CATALOG);require(catalog.revision==sha(raw),'CATALOG_CHANGED');return catalog,catalog.revision,True


def create_with_builtins(home,helper,app,run_id):
    """未作成時だけ標準二項目を保持。stagingで構築後、exclusive linkで一括採用する。"""
    parent=config_parent(home);stage=parent/('.freedoom-'+run_id+'.toml')
    builder=helper.LauncherCatalog(home);defaults=[dict(row) for row in builder.apps];created=None
    # 標準項目とGUI定義を完全に検査してから一つの新規fileを作る。登録前の途中catalogは公開しない。
    data=builder.encode([*defaults,app])
    fd=os.open(stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        created=os.fstat(fd)
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        require(sha(helper.catalog_read(stage))==sha(data),'CATALOG_CHANGED')
        fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            try:exclusive_link(fd,stage.name,'launcher.toml')
            except FileExistsError:raise InstallError('CATALOG_CHANGED') from None
            os.fsync(fd)
        finally:os.close(fd)
    finally:
        if stage.exists() or stage.is_symlink():
            info=stage.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o600 and created is not None and (created.st_dev,created.st_ino)==(info.st_dev,info.st_ino),'STAGING_UNSAFE')
            stage.unlink()


def register(request,home,helper,*,expected_uid=UID):
    require(os.getuid()==expected_uid,'UID_MISMATCH')
    require(type(request) is dict and set(request)=={'schema','action','run_id','profile','expected_revision','sources'} and type(request['schema']) is int and request['schema']==1 and
        request['action'] in {'inspect','register'} and hexstr(request['run_id'],32) and (request['expected_revision'] is None or hexstr(request['expected_revision'],64)),'REQUEST_INVALID')
    app=definition(request['profile']);app=helper.catalog_definition(app)
    helper.gui_policy(home).inspect_assets(home,request['profile'])
    catalog,revision,configured=current(home,helper);before=[dict(row) for row in catalog.apps];existing=next((row for row in before if row['id']==APP_ID),None)
    matches=existing==app;changed=False
    if request['action']=='register':
        require(revision==request['expected_revision'],'CATALOG_CHANGED');require(existing is None or matches,'LAUNCHER_ID_CONFLICT')
        if not matches:
            if configured:catalog.save(CATALOG,revision,app)
            else:create_with_builtins(home,helper,app,request['run_id'])
            changed=True
        catalog,revision,configured=current(home,helper)
        retained={row['id']:row for row in catalog.apps if row['id']!=APP_ID}
        require(retained=={row['id']:row for row in before if row['id']!=APP_ID},'CATALOG_PRESERVATION_FAILED')
        require(next((row for row in catalog.apps if row['id']==APP_ID),None)==app,'CATALOG_PRESERVATION_FAILED')
    helper.gui_policy(home).inspect_assets(home,request['profile'])
    return dict(schema=1,status='registered' if request['action']=='register' else 'inspected',run_id=request['run_id'],uid=os.getuid(),action=request['action'],
        configured=configured,revision=revision,total=len(catalog.apps),target_present=any(row['id']==APP_ID for row in catalog.apps),target_matches=matches or request['action']=='register',
        changed=changed,other_entries_preserved=True,asset_runtime_validated=True,execution='not_run',reload_requested=False)


def remote_main():
    os.umask(0o077)
    try:
        raw=sys.stdin.buffer.read(4097);require(len(raw)<=4096,'REQUEST_INVALID');request=json.loads(raw,object_pairs_hook=unique_object)
        require(type(request) is dict and type(request.get('sources')) is dict,'REQUEST_INVALID');require(os.getuid()==UID,'UID_MISMATCH')
        home=termux_home(sys.executable);helper=load_helper(home,request['sources'])
        result=register(request,home,helper);load_helper(home,request['sources']);print(json.dumps(result));return 0
    except Exception as error:
        text=str(error);reason=text if text in REASONS else 'REGISTRATION_UNAVAILABLE'
        print(json.dumps(dict(schema=1,status='blocked',reason=reason,execution='not_run')));return 2
