"""型付き固定FreedoomだけのGUI policy/専用worker。任意exec/env/PIDを受け取らない。"""
import hashlib
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

HOME=Path('/data/data/com.termux/files/home')
EXECUTABLE='/data/data/com.termux/files/usr/bin/chocolate-doom'
ENGINE_SHA='abcb1d304f6c9f859d46c156990c9db214b0a3e67707170a7655844dc0c5c253'
ENGINE_BYTES=801632
OWNER_SHA='66e7ca785253c625d37647ec587c026086b8c2622160f68155766cbbea853bd1'
EXTRA_CONFIG=b'fullscreen 0\nwindow_width 640\nwindow_height 400\nforce_software_renderer 1\nsmooth_pixel_scaling 0\ngraphical_startup 0\nshow_endoom 0\n'
STATIC_FILES={
    'assets/freedoom1.wad':(28795076,'7323bcc168c5a45ff10749b339960e98314740a734c30d4b9f3337001f9e703d'),
    'assets/COPYING.txt':(1644,'7c62f2c520769c798774f416637eec4921e5f0aafdac1245ae7c8a8cf65fe102'),
    'assets/CREDITS.txt':(15872,'72364333d295c598d75ef9553d3b3661ad0b60e27271c87b7637c74b2f46918a'),
    'assets/CREDITS-MUSIC.txt':(9388,'2bb77677bbc4e587bba44ccb6514ee0f5397c7bb8ae9580b9188d3d6a57c0a31'),
    'assets/eidlsmok.lmp':(526,'6cdd695fa613d6291c4e0fae9fef5237337cba806a4464beaf945934201487ce'),
    'config/default.cfg':(1,hashlib.sha256(b'\n').hexdigest()),
    'config/chocolate-doom.cfg':(len(EXTRA_CONFIG),hashlib.sha256(EXTRA_CONFIG).hexdigest()),
}
RUNTIME={
    'libEGL.so.1.1.0':'88ccad71e437eadaead1729d43ff8bd0fed9a5a70956d0dcd98532f0558ee6e8',
    'libEGL_mesa.so':'4c07cd55d4d59c4db60035abb2cb517776f50829cac5486de2f5c18ccf72f228',
    'libgallium-26.2.6.so':'2684bf73cd4cec80f40c19ffd4e787af4680b9c03912650e1bdc7320670934f1',
    'libc++_shared.so':'05bef9d3901910c3ff8ab450b03bc8b28383d363a2290f2f7b9aed7331ea6ff7',
    'libSDL2-2.0.so':'5e70d30076c0259aac79d226465c1b0e265b5bbd0cf1665f82764c031b98cc0a',
}
ALIASES={'libEGL.so.1':'libEGL.so.1.1.0','libEGL_mesa.so.0':'libEGL_mesa.so','libSDL2.so':'libSDL2-2.0.so'}
GRAPHICS=dict(SDL_VIDEODRIVER='wayland',LIBGL_ALWAYS_SOFTWARE='1',EGL_PLATFORM='wayland',GALLIUM_DRIVER='softpipe',LIBGL_KOPPER_DISABLE='1')


class GuiError(ValueError):pass
def require(value,code='GUI_NOT_VERIFIED'):
    if not value:raise GuiError(code)
def ident(row):return row.st_dev,row.st_ino,row.st_size,row.st_mtime_ns,row.st_ctime_ns
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def unique(pairs):
    value={}
    for key,item in pairs:require(key not in value);value[key]=item
    return value
def decode(raw):return json.loads(raw,object_pairs_hook=unique)
def hexstr(value,size):return type(value) is str and re.fullmatch('[0-9a-f]{'+str(size)+'}',value) is not None
def decimal(value):return type(value) is str and re.fullmatch(r'[1-9][0-9]{0,18}',value) is not None and int(value)<=9223372036854775807


def profile(value):
    require(type(value) is dict and set(value)=={'kind','assets_run_id','manifest_sha256'} and value['kind']=='freedoom_phase1_software'
        and hexstr(value['assets_run_id'],32) and hexstr(value['manifest_sha256'],64))
    return dict(value)


def directory(root,parts=(),*,create=False,exclusive=False):
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd);require(info.st_uid==os.getuid() and not info.st_mode&0o022)
        for index,name in enumerate(parts):
            if create:
                try:os.mkdir(name,0o700,dir_fd=fd)
                except FileExistsError:require(not exclusive or index!=len(parts)-1)
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(child)
            if info.st_uid!=os.getuid() or info.st_mode&0o022:os.close(child);raise GuiError('GUI_NOT_VERIFIED')
            os.close(fd);fd=child
        return os.dup(fd)
    finally:os.close(fd)


def read_file(root,name,*,size=None,pin=None,mode=0o600,limit=32768,retain=True,payload=None):
    parts=name.split('/');fd=directory(root,parts[:-1]);target=None
    try:
        target=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd);before=os.fstat(target)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and stat.S_IMODE(before.st_mode)==mode and before.st_nlink==1
            and 0<before.st_size<=limit and (size is None or before.st_size==size))
        digest=hashlib.sha256();body=bytearray();count=0
        while True:
            block=os.read(target,65536)
            if not block:break
            count+=len(block);require(count<=limit and (size is None or count<=size));digest.update(block)
            if payload is not None:payload.update(block)
            if retain:body.extend(block)
        require(count==before.st_size and ident(before)==ident(os.fstat(target)) and (pin is None or digest.hexdigest()==pin))
        return dict(identity=ident(before),bytes=count,sha256=digest.hexdigest()),bytes(body)
    finally:
        if target is not None:os.close(target)
        os.close(fd)


def inspect_assets(home,value):
    value=profile(value);run=value['assets_run_id'];root=home/'.local/share/eidolon/freedoom/0.13.0'/run
    parent=directory(home,('.local','share','eidolon','freedoom','0.13.0',run));os.close(parent)
    _,raw=read_file(root,'manifest.json',pin=value['manifest_sha256'],limit=16384);manifest=decode(raw)
    require(type(manifest) is dict and set(manifest)=={'schema','kind','run_id','version','archive','demo','files','signature_verification','catalog','execution','termux_install'} and
        type(manifest['schema']) is int and manifest['schema']==1 and manifest['kind']=='eidolon-freedoom-source-v1' and manifest['run_id']==run and manifest['version']=='0.13.0' and
        manifest['catalog']=='not_modified' and manifest['execution']=='not_run' and manifest['termux_install']=='not_run' and manifest['signature_verification']=='not_performed' and
        type(manifest['files']) is dict and set(manifest['files'])==set(STATIC_FILES)|{'launch-plan.json'})
    require(manifest['archive']==dict(url='https://github.com/freedoom/freedoom/releases/download/v0.13.0/freedoom-0.13.0.zip',bytes=24143781,
        sha256='3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59') and
        manifest['demo']==dict(source='freedoom1.wad/DEMO1',source_sha256='1b2be1878d9eabd866dc2766a63254dcb33020532a8d1dee82de7a01d777e27c',tics=128,
        bytes=STATIC_FILES['assets/eidlsmok.lmp'][0],sha256=STATIC_FILES['assets/eidlsmok.lmp'][1]))
    files={};payload=hashlib.sha256()
    for name,expected in sorted(manifest['files'].items()):
        require(type(expected) is dict and set(expected)=={'bytes','sha256'} and type(expected['bytes']) is int and hexstr(expected['sha256'],64))
        if name in STATIC_FILES:require((expected['bytes'],expected['sha256'])==STATIC_FILES[name])
        else:require(0<expected['bytes']<=8192)
        files[name],_=read_file(root,name,size=expected['bytes'],pin=expected['sha256'],limit=32*1024*1024,retain=False,payload=payload)
    _,raw=read_file(root,'install-receipt.json',limit=16384);receipt=decode(raw)
    require(type(receipt) is dict and set(receipt)=={'schema','kind','run_id','uid','manifest_sha256','payload_sha256','wire_sha256','engine','files','execution','catalog'} and
        type(receipt['schema']) is int and receipt['schema']==1 and receipt['kind']=='eidolon-freedoom-installed-v1' and receipt['run_id']==run and receipt['uid']==os.getuid() and
        receipt['manifest_sha256']==value['manifest_sha256'] and receipt['payload_sha256']==payload.hexdigest() and hexstr(receipt['wire_sha256'],64) and
        type(receipt['files']) is dict and all(type(n) is int for n in receipt['files'].values()) and receipt['files']=={n:m['bytes'] for n,m in manifest['files'].items()} and
        receipt['execution']=='not_run' and receipt['catalog']=='not_modified')
    prefix=home.parent/'usr';engine,_=read_file(prefix/'bin','chocolate-doom',size=ENGINE_BYTES,pin=ENGINE_SHA,mode=0o700,limit=16*1024*1024,retain=False)
    claimed=receipt['engine'];require(type(claimed) is dict and set(claimed)=={'package','version','sha256','bytes','uid','mode','nlink','elf_machine','identity'} and
        claimed==dict(package='chocolate-doom',version='3.1.1',sha256=ENGINE_SHA,bytes=ENGINE_BYTES,uid=os.getuid(),mode=0o700,nlink=1,elf_machine=183,identity=list(engine['identity'])))
    runtime={}
    for name,pin in RUNTIME.items():runtime[name],_=read_file(prefix/'lib',name,pin=pin,mode=0o700,limit=32*1024*1024,retain=False)
    for name,target in ALIASES.items():
        link=prefix/'lib'/name;require(link.lstat().st_uid==os.getuid() and link.is_symlink() and link.resolve(strict=True)==prefix/'lib'/target)
    _,raw=read_file(prefix,'share/glvnd/egl_vendor.d/50_mesa.json',limit=4096)
    require(decode(raw)=={'file_format_version':'1.0.0','ICD':{'library_path':'libEGL_mesa.so.0'}})
    return dict(root=root,files=files,engine=engine,runtime=runtime,profile=value)


def process(pid):
    root=Path('/proc')/str(pid);require(root.stat().st_uid==os.getuid(),'GUI_IDENTITY_INVALID')
    raw=(root/'stat').read_bytes();require(len(raw)<=8192,'GUI_IDENTITY_INVALID');fields=raw.rsplit(b')',1)[1].split()
    require(len(fields)>=20 and fields[19].isdigit() and fields[0] not in {b'Z',b'X',b'x'},'GUI_IDENTITY_INVALID')
    return dict(pid=pid,start=fields[19].decode(),group=int(fields[2]))


def lease(home,nonce,parent):
    root=home/'.eidolon'/('s-'+nonce);fd=directory(home,('.eidolon','s-'+nonce));os.close(fd)
    _,raw=read_file(root,'owner.json',limit=1024);require(decode(raw)==dict(schema=1,nonce=nonce,uid=os.getuid()),'GUI_LEASE_CHANGED')
    _,raw=read_file(root,'lease.json',limit=4096);value=decode(raw)
    require(type(value) is dict and set(value)=={'schema','nonce','uid','owner','child'} and value['schema']==1 and value['nonce']==nonce and value['uid']==os.getuid(),'GUI_LEASE_CHANGED')
    def normalized(row):return {key:row[key] for key in ('pid','start','group')}
    require(normalized(value['owner'])==parent and process(parent['pid'])==parent and parent['pid']==os.getppid(),'GUI_LEASE_CHANGED')
    child=normalized(value['child']);require(process(child['pid'])==child,'GUI_LEASE_CHANGED')
    info=(root/'w').lstat();require(stat.S_ISSOCK(info.st_mode) and info.st_uid==os.getuid(),'GUI_LEASE_CHANGED')
    return root


def make_state(home,nonce,launch,checked):
    parts=('.local','state','eidolon','freedoom-gui',nonce,launch);fd=directory(home,parts,create=True,exclusive=True)
    try:
        for name in ('home','config','cache','tmp','save'):os.mkdir(name,0o700,dir_fd=fd)
        for name in ('default.cfg','chocolate-doom.cfg'):
            _,data=read_file(checked['root'],'config/'+name,pin=checked['files']['config/'+name]['sha256'],limit=4096)
            target=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(target,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.fsync(fd)
    finally:os.close(fd)
    return home.joinpath(*parts)


def app_id(nonce,launch):
    require(hexstr(nonce,16) and decimal(launch),'GUI_IDENTITY_INVALID')
    return 'io.github.tinmachine.eidolon.gui.g-'+nonce+'-'+launch


def command(home,state,checked):
    return [str(home.parent/'usr/bin/chocolate-doom'),'-iwad',str(checked['root']/'assets/freedoom1.wad'),'-config',str(state/'default.cfg'),
        '-extraconfig',str(state/'chocolate-doom.cfg'),'-savedir',str(state/'save'),'-window','-width','640','-height','400','-nosound','-nomouse','-nograbmouse','-warp','1','1','-skill','1']


def environment(home,state,root,nonce,launch):
    prefix=home.parent/'usr'
    return dict(PATH=str(prefix/'bin')+':/system/bin',PREFIX=str(prefix),HOME=str(state/'home'),LANG='C.UTF-8',TMPDIR=str(state/'tmp'),
        XDG_CONFIG_HOME=str(state/'config'),XDG_CACHE_HOME=str(state/'cache'),XDG_RUNTIME_DIR=str(root),WAYLAND_DISPLAY='w',SDL_VIDEO_WAYLAND_WMCLASS=app_id(nonce,launch),**GRAPHICS)


def load_owner():
    root=Path(__file__).resolve().parent;_,data=read_file(root,'eidolon_gui_owner.py',pin=OWNER_SHA,mode=0o700,limit=32768)
    module=type(sys)('eidolon_gui_owner');exec(compile(data,str(root/'eidolon_gui_owner.py'),'exec'),module.__dict__);return module


def worker(request,control_fd,emit,*,home=HOME,owner_module=None,interrupted=lambda:False):
    require(type(request) is dict and set(request)=={'schema','nonce','session','launch_id','execution_profile','parent'} and type(request['schema']) is int and request['schema']==1 and
        hexstr(request['nonce'],16) and decimal(request['session']) and decimal(request['launch_id']),'GUI_REQUEST_INVALID')
    profile(request['execution_profile']);nonce=request['nonce'];launch=request['launch_id'];parent=request['parent']
    require(type(parent) is dict and set(parent)=={'pid','start','group'} and type(parent['pid']) is int and parent['pid']>0 and decimal(parent['start']) and
        type(parent['group']) is int and parent['group']>0,'GUI_REQUEST_INVALID')
    base=dict(schema=1,nonce=nonce,session=request['session'],launch_id=launch,uid=os.getuid(),app_id=app_id(nonce,launch))
    try:owner_module=owner_module or load_owner();owner=owner_module.ProcessOwner()
    except (OSError,ValueError,RuntimeError):
        value=dict(base,phase='finished',reason='unsupported',child=None,child_returncode=None,pipes_closed=True,output_bytes=dict(stdout=0,stderr=0),assets_runtime_unchanged=False,
            cleanup=dict(schema=1,complete=True,pending=False,reason='NOT_STARTED',term_signals=0,kill_signals=0,reaped=0,open_pidfds=0))
        emit(value);return value
    child=None;selector=selectors.DefaultSelector();original=os.get_blocking(control_fd)
    counts=dict(stdout=0,stderr=0);pending=bytearray();reason='natural';cleanup=None;checked=None;current=None;identity=None
    try:
        root=lease(home,nonce,parent);checked=inspect_assets(home,request['execution_profile']);state=make_state(home,nonce,launch,checked)
        child=owner.spawn(command(home,state,checked),env=environment(home,state,root,nonce,launch),cwd=str(checked['root']))
        identity=process(child.pid);require(identity['group']==child.pid and ident((Path('/proc')/str(child.pid)/'exe').stat())==checked['engine']['identity'],'GUI_IDENTITY_INVALID')
        emit(dict(base,phase='started',child=identity))
        os.set_blocking(control_fd,False);selector.register(control_fd,selectors.EVENT_READ,'control')
        for name in counts:
            stream=getattr(child,name);os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,name)
        last_check=0
        while True:
            if os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None:break
            if interrupted():reason='signal';break
            if time.monotonic()-last_check>.25:
                last_check=time.monotonic();lease(home,nonce,parent)
                for name,row in checked['files'].items():require(ident((checked['root']/name).lstat())==row['identity'],'GUI_ASSET_CHANGED')
            for key,_ in selector.select(.02):
                try:data=os.read(key.fd,4096)
                except BlockingIOError:continue
                if not data:
                    selector.unregister(key.fd)
                    if key.data=='control':reason='control_eof'
                    continue
                if key.data=='control':
                    pending.extend(data);require(len(pending)<=1024 and (b'\n' not in pending or pending==b'{"type":"cancel"}\n'),'GUI_REQUEST_INVALID')
                    if pending==b'{"type":"cancel"}\n':reason='cancel'
                else:
                    counts[key.data]+=len(data)
                    if counts[key.data]>65536:reason='output_limit'
            if reason!='natural':break
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):reason='failed'
    finally:
        # pendingのlive owner/実handleを捨てない。新たなgameをspawnする経路は無い。
        while True:
            cleanup=owner.finish(timeout=3,grace=.25)
            if cleanup['complete']:break
            if current!=cleanup['reason']:emit(dict(base,phase='pending',reason='GUI_CLEANUP_PENDING'));current=cleanup['reason']
            time.sleep(.05)
        drained=child is None
        if child is not None:
            until=time.monotonic()+1
            while any(k.data!='control' for k in selector.get_map().values()) and time.monotonic()<until:
                for key,_ in selector.select(.02):
                    if key.data=='control':continue
                    data=os.read(key.fd,4096)
                    if data:counts[key.data]+=len(data)
                    else:selector.unregister(key.fd)
            drained=not any(k.data!='control' for k in selector.get_map().values());child.stdout.close();child.stderr.close()
        selector.close();os.set_blocking(control_fd,original)
    unchanged=False
    if checked is not None:
        try:unchanged=checked==inspect_assets(home,request['execution_profile'])
        except (OSError,ValueError):pass
    value=dict(base,phase='finished',reason=reason,child=identity,child_returncode=None if child is None else child.returncode,
        cleanup=cleanup,pipes_closed=drained,output_bytes=counts,assets_runtime_unchanged=unchanged)
    emit(value);return value


def worker_event(value,request):
    base={'schema','nonce','session','launch_id','uid','app_id','phase'}
    require(type(value) is dict and value.get('phase') in {'started','pending','finished'},'GUI_WORKER_INVALID')
    require(type(value.get('schema')) is int and value['schema']==1 and value.get('nonce')==request['nonce'] and value.get('session')==request['session'] and
        value.get('launch_id')==request['launch_id'] and value.get('uid')==os.getuid() and value.get('app_id')==app_id(request['nonce'],request['launch_id']),'GUI_WORKER_INVALID')
    def child(row):
        return type(row) is dict and set(row)=={'pid','start','group'} and type(row['pid']) is int and row['pid']>0 and row['group']==row['pid'] and decimal(row['start'])
    if value['phase']=='started':require(set(value)==base|{'child'} and child(value['child']),'GUI_WORKER_INVALID')
    elif value['phase']=='pending':require(set(value)==base|{'reason'} and value['reason']=='GUI_CLEANUP_PENDING','GUI_WORKER_INVALID')
    else:
        require(set(value)==base|{'reason','child','child_returncode','cleanup','pipes_closed','output_bytes','assets_runtime_unchanged'} and
            value['reason'] in {'natural','signal','control_eof','cancel','output_limit','failed','unsupported'} and (value['child'] is None or child(value['child'])) and
            (value['child_returncode'] is None or type(value['child_returncode']) is int and -255<=value['child_returncode']<=255) and
            type(value['pipes_closed']) is bool and type(value['assets_runtime_unchanged']) is bool and type(value['output_bytes']) is dict and set(value['output_bytes'])=={'stdout','stderr'} and
            all(type(n) is int and 0<=n<=2**63-1 for n in value['output_bytes'].values()),'GUI_WORKER_INVALID')
        cleanup=value['cleanup'];require(type(cleanup) is dict and set(cleanup)=={'schema','complete','pending','reason','term_signals','kill_signals','reaped','open_pidfds'} and
            type(cleanup['schema']) is int and cleanup['schema']==1 and type(cleanup['complete']) is bool and type(cleanup['pending']) is bool and cleanup['complete']!=cleanup['pending'] and
            type(cleanup['reason']) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',cleanup['reason']) and
            all(type(cleanup[n]) is int and 0<=cleanup[n]<=65536 for n in ('term_signals','kill_signals','reaped','open_pidfds')),'GUI_WORKER_INVALID')
        require(not cleanup['complete'] or cleanup['open_pidfds']==0,'GUI_WORKER_INVALID')
    return value


class GuiChildren:
    """既存helper内の親側。専用workerのcontrol/実handleを保持し、観測PIDをsignalしない。"""
    def __init__(self,home,root,session,source_bytes,*,allow_initial=False):
        self.home,self.root,self.session=home,root,session;self.nonce=root.name.removeprefix('s-');self.source_bytes=source_bytes
        require(hexstr(self.nonce,16) and decimal(session));self.rows={};self.last=0;self.count=self.retired=0;self.receipts=[];self.allow_initial=allow_initial
    def begin(self,request,launch,definition):
        require((decimal(request) or request is None and self.allow_initial and self.last==0) and decimal(launch) and int(launch)>self.last and len(self.rows)<9,'GUI_NOT_VERIFIED')
        require(definition.get('mode')=='gui' and definition.get('executable')==EXECUTABLE and not definition.get('args',[]) and definition.get('cwd','.')=='.' and
            'interpreter' not in definition and 'execution_profile' in definition,'GUI_NOT_VERIFIED')
        selected=profile(definition['execution_profile']);self.last=int(launch)
        body=dict(schema=1,nonce=self.nonce,session=self.session,launch_id=launch,execution_profile=selected,parent=process(os.getpid()))
        prefix=self.home.parent/'usr';env=dict(PATH=str(prefix/'bin')+':/system/bin',PREFIX=str(prefix),HOME=str(self.home),LANG='C.UTF-8',TMPDIR=str(prefix/'tmp'))
        # 検査済みbytesを実行し、path交換後の別workerを開かない。自由sourceのrequestは無い。
        source='__file__='+repr(str(self.home/'.local/lib/eidolon/eidolon-gui.py'))+'\n'+self.source_bytes.decode()
        require(len(source.encode())<=49*1024,'GUI_NOT_VERIFIED')
        row=dict(child=None,command=[sys.executable,'-I','-S','-B','-c',source],environment=env,worker_identity=None,request=body,request_id=request,streams={'stdout','stderr'},pending=bytearray(),bytes=dict(stdout=0,stderr=0),started=None,finished=None,
            accepted=False,receipt=False,cancel=False,unknown=False,deadline=time.monotonic()+5)
        self.rows[launch]=row
    def _spawn(self,row):
        child=subprocess.Popen(row['command'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=row['environment'],start_new_session=True,close_fds=True)
        row['child']=child
        for name in ('stdout','stderr'):os.set_blocking(getattr(child,name).fileno(),False)
        try:child.stdin.write(canonical(row['request'])+b'\n');child.stdin.flush()
        except (OSError,ValueError):self._cancel(row)
        try:
            row['worker_identity']=process(child.pid);require(row['worker_identity']['group']==child.pid,'GUI_IDENTITY_INVALID')
        except (OSError,ValueError):row['unknown']=True;self._cancel(row)
    @staticmethod
    def _cancel(row):
        if row['cancel']:return
        child=row['child']
        if child is None:row['cancel']=True;return
        if not child.stdin.closed:
            try:child.stdin.write(b'{"type":"cancel"}\n');child.stdin.flush();row['cancel']=True
            except (OSError,ValueError):pass
    def cancel_all(self):
        for row in self.rows.values():self._cancel(row)
    def tick(self):
        opened=[];closed=[]
        for launch,row in list(self.rows.items()):
            child=row['child']
            if child is None:
                if row['cancel'] or time.monotonic()>row['deadline']:
                    opened.append(dict(request_id=row['request_id'],success=False,error='GUI_NOT_VERIFIED',mode='gui',ordinal=self.count));self.retired+=1;del self.rows[launch];continue
                try:
                    info=(self.root/'w').lstat()
                    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():raise GuiError('GUI_NOT_VERIFIED')
                    self._spawn(row);child=row['child']
                except FileNotFoundError:continue
                except (OSError,ValueError):
                    if row['child'] is None:
                        opened.append(dict(request_id=row['request_id'],success=False,error='GUI_NOT_VERIFIED',mode='gui',ordinal=self.count));self.retired+=1;del self.rows[launch];continue
                    row['unknown']=True;self._cancel(row);child=row['child']
            if not row['receipt'] and time.monotonic()>row['deadline']:self._cancel(row)
            try:
                for name in list(row['streams']):
                    for _ in range(8):
                        try:data=os.read(getattr(child,name).fileno(),4096)
                        except BlockingIOError:break
                        if not data:row['streams'].remove(name);break
                        row['bytes'][name]+=len(data);require(row['bytes'][name]<=(32768 if name=='stdout' else 4096),'GUI_WORKER_INVALID')
                        if name=='stderr':continue
                        row['pending'].extend(data);require(len(row['pending'])<=8192,'GUI_WORKER_INVALID')
                        while b'\n' in row['pending']:
                            line,_,rest=row['pending'].partition(b'\n');row['pending'][:]=rest;event=worker_event(decode(line),row['request'])
                            require(row['finished'] is None,'GUI_WORKER_INVALID')
                            if event['phase']=='started':
                                require(row['started'] is None,'GUI_WORKER_INVALID');row['started']=event['child']
                                if not row['cancel']:
                                    row['accepted']=row['receipt']=True;self.count+=1
                                    opened.append(dict(request_id=row['request_id'],success=True,error=None,mode='gui',ordinal=self.count))
                            elif event['phase']=='finished':
                                require(event['child']==row['started'] or row['started'] is None and event['child'] is None,'GUI_WORKER_INVALID');row['finished']=event
                exited=os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None
                if not exited or row['streams']:continue
                child.wait(timeout=0)
                final=row['finished'];proof=final is not None and final['cleanup']['complete'] and final['cleanup']['open_pidfds']==0 and final['pipes_closed'] and not row['pending'] and child.returncode==0
                if not proof:
                    row['unknown']=True;self._cancel(row);continue  # 所有者を失った孫を保存PIDで回収しない。
                if not row['receipt']:
                    row['receipt']=True;opened.append(dict(request_id=row['request_id'],success=False,error='GUI_NOT_VERIFIED',mode='gui',ordinal=self.count))
                receipt=dict(session=self.session,request_id=row['request_id'],terminal_id=launch,app_id=final['app_id'],worker=row['worker_identity'],child=final['child'],child_returncode=final['child_returncode'],reason=final['reason'],
                    cleanup_complete=True,pipes_closed=True,assets_runtime_unchanged=final['assets_runtime_unchanged'],worker_returncode=child.returncode,**{n:final['cleanup'][n] for n in ('term_signals','kill_signals','reaped','open_pidfds')})
                self.retired+=1;self.receipts.append(receipt);self.receipts=self.receipts[-16:];closed.append(receipt)
                for name in ('stdin','stdout','stderr'):
                    try:getattr(child,name).close()
                    except (OSError,ValueError):pass
                del self.rows[launch]
            except (OSError,ValueError,RuntimeError,TypeError,KeyError):row['unknown']=True;self._cancel(row)
        return opened,closed
    def summary(self):
        return dict(schema=1,active=len(self.rows),started=self.count,retired=self.retired,cleanup_pending=any(row['unknown'] for row in self.rows.values()))


def main():
    os.umask(0o077);stopped=[False]
    for number in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(number,lambda *_:stopped.__setitem__(0,True))
    try:
        raw=bytearray()
        while len(raw)<4096:
            byte=os.read(0,1);require(byte,'GUI_REQUEST_INVALID')
            if byte==b'\n':break
            raw.extend(byte)
        require(len(raw)<4096,'GUI_REQUEST_INVALID')
        value=worker(decode(raw),0,lambda value:print(json.dumps(value),flush=True),interrupted=lambda:stopped[0])
        return 0 if value['cleanup']['complete'] and value['pipes_closed'] else 2
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        print(json.dumps(dict(phase='blocked',reason='GUI_NOT_VERIFIED')),flush=True);return 2


if __name__=='__main__':raise SystemExit(main())
