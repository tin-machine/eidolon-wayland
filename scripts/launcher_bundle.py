"""固定Termux helper/GUI worker/ownerのhash鎖と更新bundle。任意配備先/execは受けない。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from sdl_probe_install import InstallError,canonical,require,termux_home,unique_object

FILES=('eidolon-session.py','eidolon-gui.py','eidolon_gui_owner.py')
LIMITS={'eidolon-session.py':131072,'eidolon-gui.py':65536,'eidolon_gui_owner.py':32768}
MAX_TOTAL=196608
MAX_WIRE=262144


def sha(data):return hashlib.sha256(data).hexdigest()
def identity(row):return row.st_dev,row.st_ino,row.st_size,row.st_mtime_ns,row.st_ctime_ns


def dependency_pins(files):
    require(type(files) is dict and set(files)==set(FILES),'SOURCE_NAMES_INVALID')
    require(all(type(data) is bytes and 0<len(data)<=LIMITS[name] for name,data in files.items()) and sum(map(len,files.values()))<=MAX_TOTAL,'SOURCE_SIZE_INVALID')
    worker=re.search(rb"^GUI_WORKER_SHA = '([0-9a-f]{64})'$",files['eidolon-session.py'],re.M)
    owner=re.search(rb"^OWNER_SHA='([0-9a-f]{64})'$",files['eidolon-gui.py'],re.M)
    require(worker is not None and worker[1].decode()==sha(files['eidolon-gui.py']) and owner is not None and owner[1].decode()==sha(files['eidolon_gui_owner.py']),'SOURCE_DEPENDENCY_MISMATCH')


def prepare(repository):
    files={}
    for name in FILES:
        path=repository/'scripts/termux'/name
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
            before=os.fstat(stream.fileno());require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and not before.st_mode&0o002,'SOURCE_UNSAFE')
            data=stream.read(LIMITS[name]+1);require(identity(before)==identity(os.fstat(stream.fileno())) and len(data)==before.st_size,'SOURCE_CHANGED');files[name]=data
    dependency_pins(files)
    manifest=dict(schema=2,kind='eidolon-termux-helper-bundle-v2',files={name:dict(bytes=len(data),sha256=sha(data)) for name,data in files.items()})
    wire=canonical(dict(manifest=manifest,files={name:base64.b64encode(data).decode() for name,data in files.items()}))
    require(len(wire)<=MAX_WIRE,'BUNDLE_LIMIT');return wire,manifest


def decode(wire,pin):
    require(type(wire) is bytes and 0<len(wire)<=MAX_WIRE and type(pin) is str and re.fullmatch(r'[0-9a-f]{64}',pin) and sha(wire)==pin,'BUNDLE_PIN_MISMATCH')
    try:value=json.loads(wire,object_pairs_hook=unique_object)
    except (ValueError,UnicodeError):raise InstallError('BUNDLE_INVALID') from None
    require(type(value) is dict and set(value)=={'manifest','files'} and type(value['manifest']) is dict and set(value['manifest'])=={'schema','kind','files'} and
        type(value['manifest']['schema']) is int and value['manifest']['schema']==2 and value['manifest']['kind']=='eidolon-termux-helper-bundle-v2' and
        type(value['manifest']['files']) is dict and set(value['manifest']['files'])==set(FILES) and type(value['files']) is dict and set(value['files'])==set(FILES),'BUNDLE_INVALID')
    files={}
    for name,encoded in value['files'].items():
        info=value['manifest']['files'][name]
        require(type(info) is dict and set(info)=={'bytes','sha256'} and type(info['bytes']) is int and 0<info['bytes']<=LIMITS[name] and type(encoded) is str and len(encoded)<=(LIMITS[name]+2)//3*4,'SOURCE_METADATA_INVALID')
        try:data=base64.b64decode(encoded,validate=True)
        except ValueError:raise InstallError('SOURCE_ENCODING_INVALID') from None
        require(len(data)==info['bytes'] and sha(data)==info['sha256'],'SOURCE_PIN_MISMATCH');files[name]=data
    dependency_pins(files);return files,value['manifest']


def install(wire,pin,home):
    files,manifest=decode(wire,pin);directory=home
    info=directory.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o022,'DIRECTORY_UNSAFE')
    for part in ('.local','lib','eidolon'):
        directory=directory/part
        try:directory.mkdir(mode=0o700)
        except FileExistsError:pass
        info=directory.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o022,'DIRECTORY_UNSAFE')
    root=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);staged={}
    try:
        for name,data in files.items():
            try:
                info=os.stat(name,dir_fd=root,follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and not info.st_mode&0o022,'TARGET_UNSAFE')
            except FileNotFoundError:pass
            descriptor,temporary=tempfile.mkstemp(prefix='.bundle-',dir=directory);staged[name]=Path(temporary).name
            with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.chmod(temporary,0o700)
        # 依存先を先に交換し、hash鎖を持つ主helperを最後に交換する。途中はfail closed。
        for name in ('eidolon_gui_owner.py','eidolon-gui.py','eidolon-session.py'):
            os.replace(staged[name],name,src_dir_fd=root,dst_dir_fd=root);del staged[name];os.fsync(root)
        for name,data in files.items():
            descriptor=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=root)
            with os.fdopen(descriptor,'rb') as stream:
                before=os.fstat(stream.fileno());actual=stream.read(LIMITS[name]+1)
                require(before.st_uid==os.getuid() and stat.S_IMODE(before.st_mode)==0o700 and before.st_nlink==1 and actual==data and identity(before)==identity(os.fstat(stream.fileno())),'INSTALLED_SOURCE_CHANGED')
    finally:
        for temporary in staged.values():
            try:os.unlink(temporary,dir_fd=root)
            except FileNotFoundError:pass
        os.close(root)
    return dict(status='pass',sha256=manifest['files']['eidolon-session.py']['sha256'],files=manifest['files'],bundle_sha256=pin,python=sys.version.split()[0])


def remote_main():
    os.umask(0o077)
    try:
        require(len(sys.argv)==2,'REQUEST_INVALID');wire=sys.stdin.buffer.read(MAX_WIRE+1)
        report=install(wire,sys.argv[1],termux_home(sys.executable));print(json.dumps(report));return 0
    except (OSError,ValueError,TypeError,KeyError):
        print(json.dumps(dict(status='fail',error='HELPER_BUNDLE_INSTALL_FAILED')));return 1
