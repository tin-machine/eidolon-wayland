"""NX13固定sourceのsame UID/private/hash pin。任意remote pathを公開しない。"""
import hashlib
import os
from pathlib import Path
import stat

HOME = Path('/data/data/com.termux/files/home')
PARTS = ('.local', 'state', 'eidolon', 'nx13-workload')
NAME = 'fixture.py'
MAX_BYTES = 65536


def install(data, expected, home=HOME):
    if not 0 < len(data) <= MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('SOURCE_MISMATCH')
    import secrets
    fd=os.open(home,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    temporary=None
    try:
        for part in PARTS:
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or info.st_mode&0o022:raise ValueError('DIRECTORY_UNSAFE')
            try:os.mkdir(part,0o700,dir_fd=fd)
            except FileExistsError:pass
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child
        info=os.fstat(fd)
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('DIRECTORY_UNSAFE')
        try:info=os.stat(NAME,dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:info=None
        if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600):
            raise ValueError('ARTIFACT_UNSAFE')
        temporary='.source-'+secrets.token_hex(16)
        source=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        with os.fdopen(source,'wb') as out:
            out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(temporary,NAME,src_dir_fd=fd,dst_dir_fd=fd);temporary=None
        source=os.open(NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
        with os.fdopen(source,'rb') as inp:observed=inp.read(MAX_BYTES+1)
        if hashlib.sha256(observed).hexdigest()!=expected:raise ValueError('SOURCE_CHANGED')
    finally:
        if temporary is not None:
            try:os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError:pass
        os.close(fd)
    return dict(status='installed',sha256=expected,bytes=len(data),uid=os.getuid())


def bootstrap(expected):
    if len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):raise ValueError('PIN_INVALID')
    return '''import hashlib,os,stat,sys
fd=os.open('/data/data/com.termux/files/home',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for part in ('.local','state','eidolon','nx13-workload'):
  child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
  info=os.fstat(fd)
  assert info.st_uid==os.getuid() and not info.st_mode&0o022
 assert stat.S_IMODE(os.fstat(fd).st_mode)==0o700
 source=os.open('fixture.py',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
 try:
  info=os.fstat(source)
  assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1
  data=bytearray()
  while len(data)<=65536:
   block=os.read(source,min(4096,65537-len(data)))
   if not block:break
   data.extend(block)
  assert 0<len(data)<=65536 and hashlib.sha256(data).hexdigest()==''' + repr(expected) + '''
 finally:os.close(source)
finally:os.close(fd)
path='/data/data/com.termux/files/home/.local/state/eidolon/nx13-workload/fixture.py'
sys.argv=[path]
exec(compile(bytes(data),path,'exec'),{'__name__':'__main__','__file__':path})
'''
