"""専用Linux helper processの子孫所有。CLI/保存PIDからのsignal APIは提供しない。"""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

MAX_PROCESSES = 1024
MAX_TASKS = 4096
SCAN_SECONDS = .25


class OwnershipError(RuntimeError):
    """固定tokenのみ。argv/path/環境/terminal本文は例外へ含めない。"""


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
    return rows


class ProcessOwner:
    """新規single-thread helperだけでenable。pending時はこのlive ownerを保持する。"""
    def __init__(self):
        require(sys.platform == 'linux' and os.getuid() == os.geteuid() != 0, 'PLATFORM_UNSUPPORTED')
        require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'PIDFD_UNSUPPORTED')
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
        descriptor = os.pidfd_open(os.getpid(), 0)
        try: signal.pidfd_send_signal(descriptor, 0, None, 0)
        finally: os.close(descriptor)
        self.handles = {}  # (pid,start) -> live fd; journalから復元しない。
        self.roots = {}
        self.closing = False
        self.result = None
        self.term_signals = self.kill_signals = self.reaped = 0

    def _current(self):
        require(os.getpid() == self.owner['pid'] and same_process(self.owner, identity(os.getpid())), 'OWNER_CHANGED')
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
        descriptor = os.pidfd_open(row['pid'], 0)
        try:
            require(same_process(row, identity(row['pid'])), 'PROCESS_CHANGED')
            value = dict(fd=descriptor, term=False, kill=False)
            self.handles[key] = value
            return value
        except BaseException:
            os.close(descriptor)
            raise

    def _signal(self, row, number):
        if row['state'] == 'Z': return
        value = self._capture(row)
        field = 'kill' if number == signal.SIGKILL else 'term'
        if value[field]: return
        try: signal.pidfd_send_signal(value['fd'], number, None, 0)
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
                        for value in self.handles.values(): os.close(value['fd'])
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
