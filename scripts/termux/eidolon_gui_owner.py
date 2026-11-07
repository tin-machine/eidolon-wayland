"""固定GUI専用workerの子孫所有。C2aと同じpidfd境界をTermux procへ適用する。"""
import ctypes
import errno
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

MAX_PROCESSES = 1024
MAX_TASKS = 4096
MAX_PROC_ENTRIES = 4096
SCAN_SECONDS = .25
_active_owner = None


class OwnershipError(RuntimeError):
    """固定tokenのみ。argv/path/環境/terminal本文は例外へ含めない。"""


class _Pidfds:
    """Android CPythonのwrapper欠落だけを固定AArch64 UAPIで補う。所有fdはこのinstance内だけ。"""
    def __init__(self):
        self.owned=set();self.library=None
        if sys.platform=='linux' and hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'):
            self.kind='python';return
        require(sys.platform=='android' and os.name=='posix' and platform.machine()=='aarch64' and
                ctypes.sizeof(ctypes.c_void_p)==ctypes.sizeof(ctypes.c_long)==8,'PIDFD_UNSUPPORTED')
        self.library=ctypes.CDLL(None,use_errno=True)
        self.library.syscall.restype=ctypes.c_long
        self.library.syscall.argtypes=[ctypes.c_long]  # Bionic: long syscall(long, ...)
        self.kind='bionic_aarch64_syscall'

    def _call(self,number,*args):
        ctypes.set_errno(0)
        value=self.library.syscall(ctypes.c_long(number),*args)
        if value<0:raise OSError(ctypes.get_errno(),'PIDFD_SYSCALL_FAILED')
        return value

    def open(self,pid):
        require(type(pid) is int and 0<pid<=2147483647,'PROCESS_IDENTITY_INVALID')
        descriptor=os.pidfd_open(pid,0) if self.kind=='python' else self._call(434,ctypes.c_int(pid),ctypes.c_uint(0))
        require(type(descriptor) is int and descriptor>=0,'PIDFD_UNSUPPORTED')
        self.owned.add(descriptor);return descriptor

    def send(self,descriptor,number):
        require(type(descriptor) is int and descriptor in self.owned and number in (0,signal.SIGTERM,signal.SIGKILL),'PIDFD_NOT_OWNED')
        if self.kind=='python':signal.pidfd_send_signal(descriptor,number,None,0)
        else:self._call(424,ctypes.c_int(descriptor),ctypes.c_int(number),ctypes.c_void_p(),ctypes.c_uint(0))

    def close(self,descriptor):
        require(descriptor in self.owned,'PIDFD_NOT_OWNED');self.owned.remove(descriptor);os.close(descriptor)


def require(value, token):
    if not value:
        raise OwnershipError(token)


def bounded_read(path, limit):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'PROCESS_SCAN_LIMIT')
    return data


def identity(pid):
    directory = Path('/proc') / str(pid)
    raw = bounded_read(directory / 'stat', 8192)
    fields = raw.rsplit(b')', 1)[1].split()
    require(len(fields) >= 20, 'PROCESS_IDENTITY_INVALID')
    rows = bounded_read(directory / 'status', 32768).splitlines()
    uid = next(row.split()[1:] for row in rows if row.startswith(b'Uid:'))
    require(len(uid) == 4 and all(value.isdigit() for value in uid), 'PROCESS_IDENTITY_INVALID')
    require(fields[19].isdigit() and int(fields[19]) > 0, 'PROCESS_IDENTITY_INVALID')
    return dict(pid=pid, start=int(fields[19]), parent=int(fields[1]),
                group=int(fields[2]), session=int(fields[3]), state=fields[0].decode('ascii'), uid=tuple(map(int, uid)))


def same_process(first, second):
    return all(first[key] == second[key] for key in ('pid', 'start', 'uid'))


def kernel_children_absent():
    """専用subreaperのkernel child集合。None/live/zombieを空に変換しない。"""
    try:os.waitid(os.P_ALL,0,os.WEXITED|os.WNOHANG|os.WNOWAIT)
    except ChildProcessError as error:
        if error.errno==errno.ECHILD:return True
        raise
    return False


def require_empty_proof(rows):
    if not rows:require(kernel_children_absent(),'PROCESS_CHILDREN_UNVERIFIED')
    return rows


def children(row, budget, deadline):
    """threadがforkしたchildも列挙する。途中のexit/reparentは成功判定せずscanを再試行。"""
    directory = Path('/proc') / str(row['pid']) / 'task'
    found = set()
    with os.scandir(directory) as tasks:
        for task in tasks:
            budget[0] -= 1
            require(budget[0] >= 0 and time.monotonic() < deadline, 'PROCESS_SCAN_LIMIT')
            require(task.name.isdecimal(), 'PROCESS_IDENTITY_INVALID')
            raw = bounded_read(Path(task.path) / 'children', 65536)
            for token in raw.split():
                require(token.isdigit() and int(token) > 0, 'PROCESS_IDENTITY_INVALID')
                found.add(int(token))
                require(len(found) <= MAX_PROCESSES, 'PROCESS_SCAN_LIMIT')
    require(same_process(row, identity(row['pid'])), 'PROCESS_CHANGED')
    return found


def descendant_snapshot(owner, deadline=None):
    deadline = min(deadline if deadline is not None else float('inf'), time.monotonic() + SCAN_SECONDS)
    current = identity(owner['pid'])
    require(same_process(owner, current), 'OWNER_CHANGED')
    require(os.getpid() == owner['pid'] and current['uid'] == (os.getuid(),) * 4, 'OWNER_CHANGED')
    # Questの/proc/task/*/childrenは未提供。既存terminal observerと同じPPID関係を
    # 専用subreaperの系統だけに限定し、捕捉したlive pidfd以外はsignalしない。
    if not Path('/proc/self/task').joinpath(str(os.getpid()), 'children').exists():
        return proc_descendants(owner,deadline)
    budget = [MAX_TASKS]
    pending = [(current, None)]
    rows = {}
    while pending:
        row, parent = pending.pop()
        require(time.monotonic() < deadline and len(rows) <= MAX_PROCESSES, 'PROCESS_SCAN_LIMIT')
        require(row['uid'] == owner['uid'], 'DESCENDANT_UID_CHANGED')
        if parent is not None:
            require(row['parent'] == parent and row['pid'] != owner['pid'], 'PROCESS_CHANGED')
            require(row['pid'] not in rows, 'PROCESS_CHANGED')
            rows[row['pid']] = row
        for pid in children(row, budget, deadline):
            require(len(rows) + len(pending) < MAX_PROCESSES, 'PROCESS_SCAN_LIMIT')
            pending.append((identity(pid), row['pid']))
    require(len(rows) <= MAX_PROCESSES, 'PROCESS_SCAN_LIMIT')
    return require_empty_proof(rows)


def proc_descendants(owner,deadline):
    rows={};count=0
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdecimal():continue
            count+=1;require(count<=MAX_PROC_ENTRIES and time.monotonic()<deadline,'PROCESS_SCAN_LIMIT')
            try:
                info=entry.stat(follow_symlinks=False)
            except (FileNotFoundError,ProcessLookupError,PermissionError):continue
            if info.st_uid!=os.getuid():continue
            try:
                row=identity(int(entry.name));require(row['uid']==owner['uid'],'DESCENDANT_UID_CHANGED');rows[row['pid']]=row
            except (FileNotFoundError,ProcessLookupError):continue
            require(len(rows)<=MAX_PROCESSES,'PROCESS_SCAN_LIMIT')
    current=rows.get(owner['pid']);require(current is not None and same_process(owner,current),'OWNER_CHANGED')
    found={};pending=[owner['pid']]
    while pending:
        parent=pending.pop()
        for row in rows.values():
            if row['parent']!=parent:continue
            require(row['pid']!=owner['pid'] and row['pid'] not in found and row['start']>=rows[parent]['start'],'PROCESS_CHANGED')
            found[row['pid']]=row;pending.append(row['pid'])
    # sample後の同一性を確認し、途中exit/reparentを完全snapshotへ数えない。
    for pid,row in found.items():
        require(time.monotonic()<deadline,'PROCESS_SCAN_LIMIT')
        now=identity(pid);require(same_process(row,now) and row['parent']==now['parent'],'PROCESS_CHANGED')
    require(same_process(owner,identity(owner['pid'])),'OWNER_CHANGED')
    return require_empty_proof(found)


class ProcessOwner:
    """新規single-thread helperだけでenable。pending時はこのlive ownerを保持する。"""
    def __init__(self):
        global _active_owner
        require(sys.platform in {'linux','android'} and os.name=='posix' and os.getuid() == os.geteuid() != 0, 'PLATFORM_UNSUPPORTED')
        require(_active_owner is None or _active_owner.owner['pid'] != os.getpid() or
                _active_owner.result is not None, 'OWNER_ALREADY_ACTIVE')
        self.pidfds=_Pidfds()
        require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'SIGCHLD_UNSUPPORTED')
        self.owner = identity(os.getpid())
        require(len(list(Path('/proc/self/task').iterdir())) == 1, 'HELPER_NOT_DEDICATED')
        require(not descendant_snapshot(self.owner), 'HELPER_NOT_DEDICATED')
        library = ctypes.CDLL(None, use_errno=True)
        library.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        library.prctl.restype = ctypes.c_int
        require(library.prctl(36, 1, 0, 0, 0) == 0, 'SUBREAPER_UNSUPPORTED')
        status = ctypes.c_int()
        require(library.prctl(37, ctypes.addressof(status), 0, 0, 0) == 0 and status.value == 1, 'SUBREAPER_UNSUPPORTED')
        descriptor = self.pidfds.open(os.getpid())
        try: self.pidfds.send(descriptor, 0)
        finally: self.pidfds.close(descriptor)
        self.handles = {}  # (pid,start) -> live fd; journalから復元しない。
        self.roots = {}
        self.closing = False
        self.result = None
        self.term_signals = self.kill_signals = self.reaped = 0
        _active_owner = self

    def _current(self):
        require(_active_owner is self and os.getpid() == self.owner['pid'] and
                same_process(self.owner, identity(os.getpid())), 'OWNER_CHANGED')
        require(len(list(Path('/proc/self/task').iterdir())) == 1, 'HELPER_NOT_DEDICATED')

    def spawn(self, command, *, env, cwd=None):
        """引数を選ぶのは固定helper adapter。SSH/ADB経由の任意argvを直接渡さない。"""
        self._current()
        require(not self.closing and self.result is None and len(self.roots) < 16, 'OWNER_CLOSED')
        child = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 close_fds=True, start_new_session=True)
        self.roots[child.pid] = child
        return child

    def _capture(self, row):
        key = row['pid'], row['start']
        if key in self.handles:
            return self.handles[key]
        require(len(self.handles) < MAX_PROCESSES, 'PROCESS_HANDLE_LIMIT')
        require(row['uid'] == self.owner['uid'], 'DESCENDANT_UID_CHANGED')
        descriptor = self.pidfds.open(row['pid'])
        try:
            require(same_process(row, identity(row['pid'])), 'PROCESS_CHANGED')
            value = dict(fd=descriptor, term=False, kill=False)
            self.handles[key] = value
            return value
        except BaseException:
            self.pidfds.close(descriptor)
            raise

    def _signal(self, row, number):
        if row['state'] == 'Z': return
        value = self._capture(row)
        field = 'kill' if number == signal.SIGKILL else 'term'
        if value[field]: return
        try: self.pidfds.send(value['fd'], number)
        except ProcessLookupError: return
        value[field] = True
        if number == signal.SIGKILL: self.kill_signals += 1
        else: self.term_signals += 1

    def _reap(self, rows):
        for pid, row in rows.items():
            if row['parent'] != self.owner['pid'] or row['state'] != 'Z': continue
            if pid in self.roots:
                self.roots[pid].wait(timeout=0)
            else:
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG)
            self.reaped += 1

    def finish(self, *, timeout=3.0, grace=.25):
        self._current()
        require(0 < timeout <= 5 and 0 <= grace < timeout, 'DEADLINE_INVALID')
        if self.result is not None: return dict(self.result)
        self.closing = True
        started = time.monotonic(); deadline = started + timeout
        empty_scans = 0; reason = 'CLEANUP_TIMEOUT'
        while time.monotonic() < deadline:
            try:
                rows = descendant_snapshot(self.owner, deadline)
                require(time.monotonic() < deadline, 'CLEANUP_TIMEOUT')
                if not rows:
                    empty_scans += 1
                    if empty_scans >= 2:
                        # Popenのroot statusも確定させる。既に呼出元がwaitした場合はno-op。
                        for child in self.roots.values(): child.wait(timeout=0)
                        for value in self.handles.values(): self.pidfds.close(value['fd'])
                        self.handles.clear()
                        self.result = self._report(True, 'COMPLETED')
                        return dict(self.result)
                else:
                    empty_scans = 0
                    number = signal.SIGKILL if time.monotonic() - started >= grace else signal.SIGTERM
                    for row in rows.values():
                        require(time.monotonic() < deadline, 'CLEANUP_TIMEOUT')
                        self._signal(row, number)
                    self._reap(rows)
            except (FileNotFoundError, ProcessLookupError, ChildProcessError, subprocess.TimeoutExpired):
                empty_scans = 0  # 同group消失や親exitだけではcompleteにしない。
            except OwnershipError as error:
                empty_scans = 0; reason = str(error)
            except (OSError, ValueError, IndexError, StopIteration, UnicodeError):
                empty_scans = 0; reason = 'OWNERSHIP_UNVERIFIED'
            time.sleep(.01)
        # fdとlive ownerを保持する。保存PIDへsignalする別processのrecoverに付け替えない。
        return self._report(False, reason)

    def _report(self, complete, reason):
        return dict(schema=1, complete=complete, pending=not complete, reason=reason,
                    term_signals=self.term_signals, kill_signals=self.kill_signals,
                    reaped=self.reaped, open_pidfds=len(self.handles))
