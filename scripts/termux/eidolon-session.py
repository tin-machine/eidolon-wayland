#!/data/data/com.termux/files/usr/bin/python3
"""固定mode/nonceだけを扱い、prepare作成者がleaseと子processを所有する。"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import sys
import threading
import time
import subprocess
import tempfile
import math
import struct
import secrets

SIZES = (1, 65536, *(8 * 1024 * 1024 for _ in range(4)))
BLOCK = bytes(range(256)) * 256
TRAILER = b"AFTER_EOF"
RING_BYTES = 65536
TERMUX_PREFIX = Path('/data/data/com.termux/files/usr')
TERMUX_HOME = Path('/data/data/com.termux/files/home')
_direct_exec_supported = None

# 観測専用。ここで得たPIDをsignal_owned/cleanupへ渡してはならない。
WORKLOAD_PROCESSES = 512
WORKLOAD_ENTRIES = 4096
WORKLOAD_SECONDS = 0.050
WORKLOAD_TERMINALS = 16
WORKLOAD_BYTES = 14000


def proc_stat(text, pid):
    """commを保存せず、括弧/空白入りcommとTermuxのlinker64を同じ規約で扱う。"""
    if len(text) > 8192 or not text.startswith(str(pid) + ' ('):
        raise ValueError('STAT_INVALID')
    fields = text[text.rindex(')') + 2:].split()
    if len(fields) < 22 or len(fields[0]) != 1:
        raise ValueError('STAT_INVALID')
    ppid, group, session, tty = map(int, fields[1:5])
    user, system = int(fields[11]), int(fields[12])
    ticks = user + system  # cutime/cstimeを再加算しない。
    start, rss = int(fields[19]), int(fields[21])
    if min(pid, start) <= 0 or min(ppid, group, session, user, system, rss) < 0:
        raise ValueError('STAT_INVALID')
    return dict(pid=pid, start=str(start), ppid=ppid, group=group, session=session,
                tty=tty, ticks=ticks, rss_pages=rss, state=fields[0])


def observe_process(pid, proc=Path('/proc')):
    """同UIDのproc directoryを開いてからstatを読む。cmdline/environは読まない。"""
    directory = os.open(proc / str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(directory).st_uid != os.getuid():
            raise PermissionError('OTHER_UID')
        fd = os.open('stat', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            raw = os.read(fd, 8193)
        finally:
            os.close(fd)
        return proc_stat(raw.decode(), pid)
    finally:
        os.close(directory)


def observation_boot(proc=Path('/proc')):
    with (proc / 'sys/kernel/random/boot_id').open() as stream:
        boot = stream.read(65).strip()
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot):
        raise ValueError('BOOT_INVALID')
    return boot


def scan_processes(proc=Path('/proc'), *, clock=time.monotonic, stopped=lambda: False):
    """1回50ms/512同UID process/4096entry。Androidの他UID PermissionErrorは通常動作。"""
    started = clock()
    rows, partial, entries = {}, False, 0
    with os.scandir(proc) as iterator:
        for entry in iterator:
            if not entry.name.isdecimal():
                continue
            entries += 1
            if stopped() or entries > WORKLOAD_ENTRIES or len(rows) >= WORKLOAD_PROCESSES or clock() - started >= WORKLOAD_SECONDS:
                partial = True
                break
            try:
                # 他UIDのstatの内容は開かない。vanish/reuseはread側でも照合する。
                if entry.stat(follow_symlinks=False).st_uid != os.getuid():
                    continue
            except PermissionError:
                # /proc/<other uid>自体が不可視なAndroidを想定。登録rootの欠測は別途判定。
                continue
            except (FileNotFoundError, ProcessLookupError):
                partial = True
                continue
            try:
                row = observe_process(int(entry.name), proc)
                rows[row['pid']] = row
            except (FileNotFoundError, ProcessLookupError):
                partial = True
            except (OSError, ValueError, UnicodeError):
                partial = True
    return rows, partial, max(0.0, clock() - started)


def process_tree(rows, roots):
    """登録identityと一致するrootからの観測集合。PID再利用で別rootへ乗り換えない。"""
    children = {}
    for row in rows.values():
        children.setdefault(row['ppid'], []).append(row)
    found, missing, queue = {}, False, []
    for identity in roots:
        row = rows.get(identity['pid'])
        if row is None or row['start'] != identity['start'] or row['state'] in {'Z', 'X', 'x'}:
            missing = True
        else:
            queue.append(row)
    while queue:
        row = queue.pop()
        if row['pid'] in found:
            continue
        found[row['pid']] = row
        queue.extend(child for child in children.get(row['pid'], [])
                     if int(child['start']) >= int(row['start']) and child['state'] not in {'Z', 'X', 'x'})
    return found, missing


class WorkloadDelta:
    """collector内の単調時計だけでdeltaを作る。欠測/新PID/逆行を0で補完しない。"""
    def __init__(self, ticks=None, pages=None):
        self.ticks = ticks if ticks is not None else os.sysconf('SC_CLK_TCK')
        self.pages = pages if pages is not None else os.sysconf('SC_PAGE_SIZE')
        if min(self.ticks, self.pages) <= 0:
            raise ValueError('CLOCK_INVALID')
        self.previous = {}
        self.identity = None
        self.at = None

    def update(self, boot, epoch, at, rows):
        if not math.isfinite(at):
            raise ValueError('CLOCK_INVALID')
        interval = at - self.at if self.at is not None else 0
        valid = self.identity == (boot, epoch) and 0 < interval <= 3
        values, current = {}, {}
        for row in rows.values():
            key = row['pid'], row['start']
            before = self.previous.get(key) if valid else None
            current[key] = row['ticks']
            values[key] = ((row['ticks'] - before) / (self.ticks * interval)
                           if before is not None and row['ticks'] >= before else None)
        self.previous, self.identity, self.at = current, (boot, epoch), at
        return values, interval if valid else None

    def total(self, rows, values, *, partial=False, missing=False):
        selected = [values.get((row['pid'], row['start'])) for row in rows.values()]
        complete = bool(selected) and not missing and not partial and all(value is not None for value in selected)
        return dict(cpu_cores=sum(selected) if complete else None,
                    rss_bytes=sum(row['rss_pages'] * self.pages for row in rows.values()) if rows else None,
                    processes=len(rows), coverage='complete' if complete else ('partial' if rows else 'unknown'),
                    reason=None if complete else ('root_missing' if missing else 'scan_partial' if partial else 'baseline'))


def parse_tmux_snapshot(clients_text, panes_text, rows):
    """登録socketに対する固定formatだけを解析。title、command、session名は受けない。"""
    if len(clients_text.encode()) + len(panes_text.encode()) > 32768:
        raise ValueError('TMUX_LIMIT')
    clients, panes, pane_ttys, server = {}, {}, {}, None
    for text, kind in ((clients_text, 'clients'), (panes_text, 'panes')):
        lines = text.splitlines()
        if len(lines) > 128:
            raise ValueError('TMUX_LIMIT')
        for line in lines:
            fields = line.split('\t')
            if len(fields) != (4 if kind == 'clients' else 6) or not fields[0].isdecimal():
                raise ValueError('TMUX_INVALID')
            candidate = rows.get(int(fields[0]))
            if candidate is None or candidate['state'] in {'Z', 'X', 'x'}:
                raise ValueError('TMUX_SERVER_MISSING')
            identity = candidate['pid'], candidate['start']
            if server is not None and server != identity:
                raise ValueError('TMUX_SERVER_CHANGED')
            server = identity
            if kind == 'clients':
                _, client, tty, session = fields
                if not client.isdecimal() or not re.fullmatch(r'/dev/pts/[0-9]+', tty) or not re.fullmatch(r'\$[0-9]+', session):
                    raise ValueError('TMUX_INVALID')
                process = rows.get(int(client))
                if process is None or process['state'] in {'Z', 'X', 'x'}:
                    raise ValueError('TMUX_CLIENT_MISSING')
                if tty in clients:
                    raise ValueError('TMUX_AMBIGUOUS')
                clients[tty] = dict(session=session, process=process)
            else:
                _, session, window, pane, pid, tty = fields
                if (not re.fullmatch(r'\$[0-9]+', session) or not re.fullmatch(r'@[0-9]+', window)
                        or not re.fullmatch(r'%[0-9]+', pane) or not pid.isdecimal() or not re.fullmatch(r'/dev/pts/[0-9]+', tty)):
                    raise ValueError('TMUX_INVALID')
                process = rows.get(int(pid))
                if process is None or process['state'] in {'Z', 'X', 'x'}:
                    raise ValueError('TMUX_PANE_MISSING')
                key = session, pane
                if key in panes and panes[key] != process:
                    raise ValueError('TMUX_AMBIGUOUS')
                panes[key] = process
                pane_ttys[key] = tty
    if server is None and (clients or panes):
        raise ValueError('TMUX_SERVER_MISSING')
    return dict(server=server, clients=clients, panes=panes, pane_ttys=pane_ttys)


def tmux_scope(snapshot, tty, shell_tree):
    """clientが登録shell系統に属する時だけsessionを対応付け、共有paneを重複排除。"""
    client = snapshot['clients'].get(tty)
    if client is None:
        return None
    process = client['process']
    if shell_tree.get(process['pid']) != process:
        raise ValueError('TMUX_CLIENT_UNRELATED')
    roots = {row['pid']: row for (session, _), row in snapshot['panes'].items() if session == client['session']}
    if not roots:
        raise ValueError('TMUX_PANE_MISSING')
    if any(pane_tty in snapshot['clients'] for key, pane_tty in snapshot['pane_ttys'].items() if key[0] == client['session']):
        raise ValueError('TMUX_NESTED')
    return dict(server=snapshot['server'], session=client['session'], roots=list(roots.values()))


def bounded_observation_command(command, *, stopped=lambda: False, seconds=.15):
    """固定tmux照会を短時間で打ち切る。回収対象はこのPopenだけ。"""
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    data, eof, deadline = bytearray(), False, time.monotonic() + seconds
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(child.stdout.fileno(), False)
        selector.register(child.stdout, selectors.EVENT_READ)
        while not stopped() and time.monotonic() < deadline:
            if eof and waitable(child):
                status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
                if status.si_code != os.CLD_EXITED or status.si_status != 0:
                    raise ValueError('TMUX_QUERY_FAILED')
                return data.decode()
            for key, _ in selector.select(.01):
                try: block = os.read(key.fd, 4096)
                except BlockingIOError: continue
                if not block:
                    eof = True
                    selector.unregister(key.fd)
                data.extend(block)
                if len(data) > 32768:
                    raise ValueError('TMUX_LIMIT')
        raise ValueError('TMUX_TIMEOUT')
    finally:
        # WNOWAIT中のleaderを保持するので、再利用PIDや観測tmux serverへsignalしない。
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(timeout=2)
        selector.close()
        child.stdout.close()


def checked_tmux_socket(path):
    if not path.is_absolute() or len(os.fsencode(path)) > 103 or '..' in path.parts:
        raise ValueError('TMUX_SOCKET_INVALID')
    parent = path.parent.lstat()
    info = path.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077
            or not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o007):
        raise ValueError('TMUX_SOCKET_INVALID')
    return info.st_dev, info.st_ino


def query_tmux(path, rows, stopped=lambda: False):
    before = checked_tmux_socket(path)
    command = [str(TERMUX_PREFIX / 'bin/tmux'), '-S', str(path)]
    clients = bounded_observation_command(command + ['list-clients', '-F', '#{pid}\t#{client_pid}\t#{client_tty}\t#{session_id}'], stopped=stopped)
    panes = bounded_observation_command(command + ['list-panes', '-a', '-F', '#{pid}\t#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pane_tty}'], stopped=stopped)
    # attach/detach/session切替の途中の組を採用しない。別sampleで取り直す。
    again = bounded_observation_command(command + ['list-clients', '-F', '#{pid}\t#{client_pid}\t#{client_tty}\t#{session_id}'], stopped=stopped)
    if clients != again or before != checked_tmux_socket(path):
        raise ValueError('TMUX_CHANGED')
    snapshot = parse_tmux_snapshot(clients, panes, rows)
    identities = [item['process'] for item in snapshot['clients'].values()] + list(snapshot['panes'].values())
    if snapshot['server'] is not None:
        identities.append(rows[snapshot['server'][0]])
    for identity in identities:
        if stopped():
            raise ValueError('TMUX_TIMEOUT')
        now = observe_process(identity['pid'])
        if now['start'] != identity['start'] or now['tty'] != identity['tty']:
            raise ValueError('TMUX_CHANGED')
    return snapshot


def valid_terminal_id(value):
    return isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,18}', value) is not None and int(value) <= 9223372036854775807


def terminal_registration(home, nonce, terminal):
    directory = owned(home, nonce)
    if not valid_terminal_id(terminal) or not os.isatty(0):
        raise ValueError('TERMINAL_INVALID')
    current = observe_process(os.getpid())
    tty = os.ttyname(0)
    if not re.fullmatch(r'/dev/pts/[0-9]+', tty) or current['tty'] == 0:
        raise ValueError('TERMINAL_INVALID')
    return directory, dict(schema=1, kind='terminal', nonce=nonce, terminal_id=terminal,
                           boot=observation_boot(), pid=current['pid'], start=current['start'], tty=tty,
                           tty_nr=current['tty'])


def send_registration(directory, value):
    encoded = json.dumps(value, separators=(',', ':')).encode()
    if len(encoded) > 2048:
        raise ValueError('REGISTRATION_LIMIT')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as peer:
        peer.settimeout(.1)
        peer.sendto(encoded, str(directory / 'm'))


def terminal_shell(home, nonce, terminal, mode='foot'):
    # 計測不可でもterminal本来の機能は続ける。PTY内でwrapperをshellへexecする。
    if not valid_terminal_id(terminal):
        raise ValueError('TERMINAL_INVALID')
    directory = owned(home, nonce)
    try:
        _, value = terminal_registration(home, nonce, terminal)
        send_registration(directory, value)
    except (OSError, ValueError):
        pass
    env = tool_environment(directory)
    env.update(EIDOLON_SESSION_NONCE=nonce, EIDOLON_TERMINAL_ID=terminal)
    command = shell_command(mode)
    os.execve(command[0], command, env)


class WorkloadObserver:
    """1 worker、最新sample1個、登録16件。lease/control threadで/procを走査しない。"""
    def __init__(self, directory, nonce, session):
        self.directory, self.nonce, self.session = directory, nonce, session
        self.epoch = secrets.token_hex(16)
        self.lock, self.stopped = threading.Lock(), threading.Event()
        self.allowed, self.registered, self.latest = set(), {}, None
        self.pending_deadlines = {}
        self.last_terminal = 0
        self.sockets = {TERMUX_PREFIX / 'tmp' / f'tmux-{os.getuid()}' / 'default'}
        self.sequence = 0
        self.peer = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.peer.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.peer.bind(str(directory / 'm'))
            self.peer.setblocking(False)
        except BaseException:
            self.peer.close()
            raise
        self.thread = threading.Thread(target=self.run, name='eidolon-observer')
        self.thread.start()

    def allow(self, terminal):
        if not valid_terminal_id(terminal):
            raise ValueError('TERMINAL_INVALID')
        with self.lock:
            if int(terminal) <= self.last_terminal:
                raise ValueError('TERMINAL_REUSED')
            self.last_terminal = int(terminal)
            if len(self.allowed) < WORKLOAD_TERMINALS:
                self.allowed.add(terminal)
                self.pending_deadlines[terminal] = time.monotonic() + 6

    def take(self):
        with self.lock:
            value, self.latest = self.latest, None
            return value

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise ValueError('OBSERVER_STOP_PENDING')
        self.peer.close()
        # directory/socketのunlinkはowner cleanupだけが行う。

    def receive(self, boot):
        for _ in range(32):
            try:
                data, ancillary, flags, _ = self.peer.recvmsg(2049, socket.CMSG_SPACE(12))
            except BlockingIOError:
                break
            try:
                credentials = [struct.unpack('3i', value) for level, kind, value in ancillary
                               if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(value) == 12]
                if flags or len(data) > 2048 or len(credentials) != 1:
                    continue
                pid, uid, _ = credentials[0]
                value = json.loads(data)
                if not isinstance(value, dict) or uid != os.getuid() or value.get('schema') != 1 or value.get('nonce') != self.nonce:
                    continue
                if value.get('kind') == 'tmux_socket':
                    if set(value) != {'schema', 'nonce', 'kind', 'path'} or len(self.sockets) >= 4:
                        continue
                    path = Path(value['path'])
                    checked_tmux_socket(path)
                    self.sockets.add(path)
                    continue
                if set(value) != {'schema', 'nonce', 'kind', 'terminal_id', 'boot', 'pid', 'start', 'tty', 'tty_nr'} or value['kind'] != 'terminal':
                    continue
                terminal = value['terminal_id']
                with self.lock:
                    allowed = terminal in self.allowed
                if not allowed or terminal in self.registered or value['boot'] != boot or value['pid'] != pid:
                    continue
                current = observe_process(pid)
                tty = value['tty']
                if (current['start'] != value['start'] or current['tty'] == 0 or current['tty'] != value['tty_nr']
                        or not isinstance(tty, str) or not re.fullmatch(r'/dev/pts/[0-9]+', tty)):
                    continue
                info = os.stat(tty)
                if not stat.S_ISCHR(info.st_mode) or info.st_uid != uid or (info.st_rdev & 0xffffffff) != (current['tty'] & 0xffffffff):
                    continue
                self.registered[terminal] = dict(pid=pid, start=current['start'], tty=tty)
                with self.lock: self.pending_deadlines.pop(terminal, None)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                continue

    def sample(self, boot, counter):
        self.receive(boot)
        with self.lock:
            expired = [terminal for terminal, deadline in self.pending_deadlines.items() if time.monotonic() > deadline]
            for terminal in expired:
                self.pending_deadlines.pop(terminal)
                self.allowed.discard(terminal)
        rows, partial, scan_seconds = scan_processes(stopped=self.stopped.is_set)
        at = time.monotonic()
        values, interval = counter.update(boot, self.epoch, at, rows)
        adapters, adapter_partial = [], False
        for path in self.sockets:
            if self.stopped.is_set():
                break
            try:
                if not path.exists():
                    continue
                adapters.append(query_tmux(path, rows, self.stopped.is_set))
            except (OSError, ValueError, UnicodeError):
                adapter_partial = True
        result = []
        for terminal, identity in list(self.registered.items()):
            shell, missing = process_tree(rows, [identity])
            key, scope, selected = f'shell:{terminal}:{identity["pid"]}:{identity["start"]}', 'shell_tree', shell
            failed = adapter_partial
            try:
                matches = [value for adapter in adapters if (value := tmux_scope(adapter, identity['tty'], shell)) is not None]
                if len(matches) > 1:
                    raise ValueError('TMUX_AMBIGUOUS')
                if matches:
                    tmux = matches[0]
                    selected, lost = process_tree(rows, tmux['roots'])
                    missing |= lost
                    pid, start = tmux['server']
                    key, scope = f'tmux:{pid}:{start}:{tmux["session"]}', 'tmux_session'
            except ValueError:
                failed = True
            total = counter.total(selected, values, partial=partial or failed, missing=missing)
            if failed:
                total['reason'] = 'tmux_uncertain'
            result.append(dict(terminal_id=terminal, workload_id=key, scope=scope, **total))
            # 確実なroot終了時のみ枠を開放。欠測だけでは他のprocessへ乗り換えない。
            gone = identity['pid'] in rows and (rows[identity['pid']]['start'] != identity['start'] or rows[identity['pid']]['state'] in {'Z', 'X', 'x'})
            if identity['pid'] not in rows:
                try: observe_process(identity['pid'])
                except (FileNotFoundError, ProcessLookupError): gone = True
                except (OSError, ValueError): pass
            if gone:
                del self.registered[terminal]
                with self.lock: self.allowed.discard(terminal)
        return dict(boot=boot, capability='available', interval_seconds=interval, scan_seconds=scan_seconds,
                    registrations=[dict(terminal_id=terminal, **identity) for terminal, identity in self.registered.items()], workloads=result)

    def run(self):
        try:
            boot, counter = observation_boot(), WorkloadDelta()
            observe_process(os.getpid())
        except (OSError, ValueError):
            boot, counter = None, None
        while not self.stopped.is_set():
            started = time.monotonic()
            try:
                body = self.sample(boot, counter) if counter is not None else dict(boot=None, capability='unavailable', interval_seconds=None, scan_seconds=0, registrations=[], workloads=[])
            except (OSError, ValueError, UnicodeError):
                body = dict(boot=boot, capability='unavailable', interval_seconds=None, scan_seconds=0, registrations=[], workloads=[])
            self.sequence += 1
            value = dict(**marker(self.nonce), type='workload_v1', session=self.session, epoch=self.epoch,
                         sequence=str(self.sequence), sampled_monotonic_ms=int(time.monotonic() * 1000), **body)
            if len(json.dumps(value, separators=(',', ':')).encode()) > WORKLOAD_BYTES:
                value.update(capability='unavailable', interval_seconds=None, registrations=[], workloads=[])
            with self.lock:
                self.latest = value
            self.stopped.wait(max(0, 1 - (time.monotonic() - started)))


class JsonOutput:
    """stdout backpressureでleaseを止めない。control優先、metricsは最新1件。"""
    def __init__(self, fd):
        self.fd, self.blocking = fd, os.get_blocking(fd)
        self.control, self.metric, self.current = [], None, b''
        self.offset = 0
        os.set_blocking(fd, False)

    def __call__(self, value):
        encoded = (json.dumps(value, separators=(',', ':')) + '\n').encode()
        if len(encoded) > 16384:
            raise OSError('OUTPUT_LIMIT')
        if value.get('type') == 'workload_v1':
            self.metric = encoded
        else:
            if sum(map(len, self.control)) + len(encoded) > 65536:
                raise OSError('OUTPUT_LIMIT')
            self.control.append(encoded)
        self.flush()

    def flush(self):
        # 1loopの仕事量も固定する。開始済みJSON lineは途中で捨てない。
        for _ in range(8):
            if not self.current:
                if self.control:
                    self.current = self.control.pop(0)
                elif self.metric is not None:
                    self.current, self.metric = self.metric, None
                else:
                    return
            try:
                count = os.write(self.fd, self.current[self.offset:self.offset + 4096])
            except BlockingIOError:
                return
            if count <= 0:
                raise OSError('OUTPUT_CLOSED')
            self.offset += count
            if self.offset == len(self.current):
                self.current, self.offset = b'', 0

    def close(self):
        self.metric = None
        deadline = time.monotonic() + 2
        try:
            while self.current or self.control:
                self.flush()
                if time.monotonic() >= deadline:
                    raise OSError('OUTPUT_TIMEOUT')
                time.sleep(.01)
        finally:
            os.set_blocking(self.fd, self.blocking)


def checked(directory):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("DIRECTORY_INVALID")


def paths(home, nonce):
    if not re.fullmatch(r"[0-9a-f]{16}", nonce):
        raise ValueError("NONCE_INVALID")
    root = home / ".eidolon"
    directory = root / ("s-" + nonce)
    if len(os.fsencode(directory / "p")) > 103:
        raise ValueError("SOCKET_PATH_LONG")
    return root, directory


def marker(nonce):
    return {"schema": 1, "nonce": nonce, "uid": os.getuid()}


def prepare(home, nonce):
    root, directory = paths(home, nonce)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    checked(root)
    receipt = root / ("r-" + nonce + ".json")
    if receipt.exists() or receipt.is_symlink():
        raise FileExistsError("NONCE_REUSED")
    directory.mkdir(mode=0o700)  # 再利用・既存socketの上書きをしない。
    checked(directory)
    with (directory / "owner.json").open("x") as out:
        json.dump(marker(nonce), out)
    (directory / "owner.json").chmod(0o600)
    return {"schema": 1, "socket": str(directory / "p"), "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def owned(home, nonce):
    root, directory = paths(home, nonce)
    checked(root)
    checked(directory)
    owner = directory / "owner.json"
    info = owner.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024:
        raise ValueError("OWNER_INVALID")
    if json.loads(owner.read_text()) != marker(nonce):
        raise ValueError("OWNER_MISMATCH")
    return directory


def cleanup(home, nonce, *, creator=False):
    root, directory = paths(home, nonce)
    checked(root)
    if not directory.exists() and not directory.is_symlink():
        result = {"schema": 1, "cleaned": True, "already_absent": True}
        receipt = root / ("r-" + nonce + ".json")
        if receipt.exists() or receipt.is_symlink():
            result["receipt"] = read_record(receipt, nonce)
            result["child_alive"] = same_process(result["receipt"].get("child"))
        return result
    directory = owned(home, nonce)
    lease = directory / "lease.json"
    if lease.exists() or lease.is_symlink():
        record = read_record(lease, nonce)
        now = process_identity(os.getpid())
        owner_is_self = isinstance(record.get('owner'), dict) and all(
            record['owner'].get(key) == now[key] for key in ('pid', 'start', 'group'))
        if same_process(record.get("child")) or (same_process(record.get("owner")) and not (creator and owner_is_self)):
            return {"schema": 1, "cleaned": False, "active": same_process(record.get("owner")), "orphaned": same_process(record.get("child"))}
        if not creator:
            return {"schema": 1, "cleaned": False, "pending": True, "orphaned": True}
    if not creator:
        # 後日の照合では既存directoryの所有権を新たに取得しない。
        return {"schema": 1, "cleaned": False, "pending": True}
    # child/groupの終了後、作成者だけが転送pとWayland wを回収する。
    # unexpected fileやsymlinkならlease/markerも残し、完了を偽らない。
    if {entry.name for entry in directory.iterdir()} - {"owner.json", "lease.json", "p", "w", "f", "m"}:
        raise ValueError("DIRECTORY_NOT_EMPTY")
    sockets = []
    for name in ('p', 'w', 'f', 'm'):
        sock = directory / name
        try:
            info = sock.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("SOCKET_INVALID")
        sockets.append(sock)
    for sock in sockets:
        sock.unlink()
    if lease.exists():
        lease.unlink()
    if {entry.name for entry in directory.iterdir()} != {"owner.json"}:
        raise ValueError("DIRECTORY_NOT_EMPTY")
    (directory / "owner.json").unlink()
    directory.rmdir()
    return {"schema": 1, "cleaned": True, "already_absent": False}


def transfer(path, size, barrier=None):
    if barrier:
        barrier.wait(timeout=10)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        # 制御ownerの60秒leaseより長くし、失効が子のsocket timeoutへ依存しないようにする。
        peer.settimeout(90)
        peer.connect(str(path))
        expected = hashlib.sha256()
        failure = []

        def send():
            try:
                left = size
                while left:
                    block = BLOCK[:min(left, len(BLOCK))]
                    expected.update(block)
                    peer.sendall(block)
                    left -= len(block)
                peer.shutdown(socket.SHUT_WR)
            except Exception as error:
                failure.append(error)
                peer.close()

        writer = threading.Thread(target=send)
        writer.start()
        received, digest, tail = 0, hashlib.sha256(), b""
        try:
            while True:
                block = peer.recv(65536)
                if not block:
                    break
                received += len(block)
                pending = tail + block
                digest.update(pending[:-len(TRAILER)])
                tail = pending[-len(TRAILER):]
        finally:
            writer.join(timeout=35)
        if writer.is_alive() or failure or received != size + len(TRAILER) or tail != TRAILER or digest.digest() != expected.digest():
            raise ValueError("ECHO_MISMATCH")
        return {"size": size, "bytes": received - len(TRAILER), "sha256": digest.hexdigest(), "tail_matched": True}


def echo(home, nonce):
    directory = owned(home, nonce)
    path = directory / "p"
    reports = [transfer(path, size) for size in SIZES[:2]]
    barrier = threading.Barrier(4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(transfer, path, size, barrier) for size in SIZES[2:]]
        reports.extend(future.result(timeout=45) for future in futures)
    return {"schema": 1, "result": "pass", "uid": os.getuid(), "channels": len(reports), "bytes": sum(SIZES), "copies": reports}


def process_identity(pid):
    """Linuxのstarttimeを含め、再利用されたPIDを同じprocessと扱わない。"""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rindex(")") + 2:].split()
        return {"pid": pid, "start": fields[19], "group": int(fields[2]), "state": fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def same_process(identity):
    if not isinstance(identity, dict) or not isinstance(identity.get("pid"), int) or identity["pid"] <= 0:
        return False
    now = process_identity(identity["pid"])
    return now is not None and now["start"] == identity.get("start") and now["state"] != "Z"


def write_record(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_record(path, nonce):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384:
        raise ValueError("RECORD_INVALID")
    value = json.loads(path.read_text())
    if any(value.get(key) != expected for key, expected in marker(nonce).items()):
        raise ValueError("RECORD_MISMATCH")
    return value


class Tail:
    def __init__(self):
        self.data, self.total = bytearray(), 0

    def push(self, block):
        self.total += len(block)
        self.data.extend(block)
        del self.data[:-RING_BYTES]

    def metrics(self):
        return {"bytes": self.total, "truncated": self.total > RING_BYTES, "retained": len(self.data)}


def child_command(mode, nonce):
    if mode == "echo":
        return [sys.executable, str(Path(__file__).resolve()), "echo", nonce]
    if mode not in {"pattern", "foot", "foot_fixture"}:
        raise ValueError("DEPENDENCY_NOT_READY")
    _, directory = paths(TERMUX_HOME, nonce)
    checked(directory)
    command = [str(TERMUX_PREFIX / 'bin/waypipe'), '--no-gpu', '--compress', 'none', '--threads', '1',
               '--socket', str(directory / 'p'), '--display', str(directory / 'w'), 'server', '--']
    if mode == 'pattern':
        command += [str(TERMUX_HOME / '.local/lib/eidolon/eidolon-shm-fixture'), '--pattern']
    else:
        command += [str(TERMUX_PREFIX / 'bin/foot'), '--app-id=io.github.tinmachine.eidolon.foot',
                    '--title=Eidolon', '--font=DejaVu Sans Mono:size=14',
                    '--override=resize-by-cells=no',
                    '--override=tweak.max-shm-pool-size-mb=0']
        command += ['--server=' + str(directory / 'f')]
    return command


def shell_command(mode):
    if mode == 'foot_fixture':
        source = ("import time\n"
                  "print('Eidolon foot fixture: ABCD 0123456789', flush=True)\n"
                  "for row in range(1200):\n"
                  " print('row %04d | ABCDEFGH 0123456789 | scroll fixture' % row, flush=True)\n"
                  " time.sleep(0.05)\n"
                  "time.sleep(600)\n")
        command = [sys.executable, '-u', '-c', source]
    elif mode == 'foot':
        command = [str(TERMUX_PREFIX / 'bin/bash'), '--noprofile', '--norc', '-i']
    else:
        raise ValueError('DEPENDENCY_NOT_READY')
    return command


def terminal_command(directory, mode, terminal=None):
    command = [str(TERMUX_PREFIX / 'bin/footclient'), '--server-socket=' + str(directory / 'f'), '--no-wait']
    if terminal is None:
        return command + shell_command(mode)
    nonce = directory.name.removeprefix('s-')
    if not valid_terminal_id(terminal) or not re.fullmatch(r'[0-9a-f]{16}', nonce) or mode not in {'foot', 'foot_fixture'}:
        raise ValueError('TERMINAL_INVALID')
    command += ['--app-id=io.github.tinmachine.eidolon.foot.t-' + nonce + '-' + terminal,
                sys.executable, str(Path(__file__).resolve()),
                'terminal-shell' if mode == 'foot' else 'terminal-fixture', nonce, terminal]
    return command


class TerminalStarter:
    """短命footclientを所有し、lease/Stopを待たせずにserverへ固定起動を依頼する。"""
    def __init__(self, directory, mode):
        self.directory, self.mode = directory, mode
        self.child = None
        self.deadline = None
        self.request = None
        self.count = 0
        self.terminal = None

    def begin(self, request, terminal=None):
        if self.deadline is not None:
            raise ValueError('CONTROL_INVALID')
        self.request = request
        self.terminal = terminal
        self.deadline = time.monotonic() + 5

    def close(self):
        if self.child is not None:
            # waitable/WNOWAITでleaderのPIDを予約している間だけ自分のgroupをsignalする。
            try: os.killpg(self.child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            self.child.wait(timeout=2)
            self.child = None

    def tick(self):
        if self.deadline is None:
            return None
        success = False
        if self.child is None:
            path = self.directory / 'f'
            try: info = path.lstat()
            except FileNotFoundError: info = None
            if info is not None:
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise ValueError('DEPENDENCY_NOT_READY')
                self.child = subprocess.Popen(terminal_command(self.directory, self.mode, self.terminal),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True, env=tool_environment(self.directory))
        if self.child is not None and waitable(self.child):
            # poll()/wait()より先に、同じgroupを回収してPID再利用を防ぐ。
            status = os.waitid(os.P_PID,self.child.pid,os.WEXITED | os.WNOWAIT)
            success = status.si_code == os.CLD_EXITED and status.si_status == 0
        elif time.monotonic() < self.deadline:
            return None
        self.close()
        self.deadline = None
        if success: self.count += 1
        return dict(request_id=self.request, success=success, ordinal=self.count)


def termux_direct_exec_available(env):
    """実contextで直接execできる場合だけlinker迂回を外す。結果はlauncher内で一度だけ確認。"""
    global _direct_exec_supported
    if _direct_exec_supported is not None:
        return _direct_exec_supported
    python = TERMUX_PREFIX / 'bin/python3'
    if not python.is_file():
        _direct_exec_supported = False
        return False
    probe_env = env.copy()
    probe_env['TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE'] = 'disable'
    probe_env.pop('TERMUX_EXEC__PROC_SELF_EXE', None)
    # 最初のexecは親の既存interceptorが迂回し得る。新設定を読んだ子からもう一度execする。
    source = f'import os; os.execv({str(python)!r}, [{str(python)!r}, "-c", "pass"])'
    try:
        result = subprocess.run([str(python), '-c', source], env=probe_env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=1, check=False)
        _direct_exec_supported = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _direct_exec_supported = False
    return _direct_exec_supported


def tool_environment(directory):
    env = os.environ.copy()
    env.update(PATH=str(TERMUX_PREFIX / 'bin') + ':/system/bin', PREFIX=str(TERMUX_PREFIX),
               HOME=str(TERMUX_HOME), TMPDIR=str(TERMUX_PREFIX / 'tmp'),
               XDG_RUNTIME_DIR=str(directory), SHELL=str(TERMUX_PREFIX / 'bin/bash'))
    # ADB由来のSSH環境では不要なlinker経由execが有効になり、Neovimの自己再実行を壊す。
    # force等の明示設定と、直接execできないAndroid contextは維持する。LD_PRELOADは外さない。
    if env.get('TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE', 'enable') == 'enable' and termux_direct_exec_available(env):
        env['TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE'] = 'disable'
        env.pop('TERMUX_EXEC__PROC_SELF_EXE', None)
    return env


def tool_version(path):
    child = subprocess.Popen([str(path), '--version'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
    selector = selectors.DefaultSelector()
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        for name in output:
            pipe = getattr(child, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        deadline = time.monotonic() + 3
        while selector.get_map() or not waitable(child):
            if time.monotonic() >= deadline:
                raise ValueError('DEPENDENCY_NOT_READY')
            for key, _ in selector.select(timeout=0.02):
                block = os.read(key.fd, 4096)
                if not block:
                    selector.unregister(key.fd)
                elif len(output[key.data]) + len(block) > 4096:
                    raise ValueError('DEPENDENCY_NOT_READY')
                else:
                    output[key.data].extend(block)
    finally:
        # leaderをWNOWAITで保持する間だけ、自分がspawnしたgroupを終了する。
        try:os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:pass
        status = child.wait(timeout=2)
        selector.close()
        child.stdout.close()
        child.stderr.close()
    if status != 0:
        raise ValueError('DEPENDENCY_NOT_READY')
    return output['stdout'].decode('utf-8')


def inspect_tools(mode):
    if mode not in {'pattern', 'foot', 'foot_fixture'}:
        raise ValueError('DEPENDENCY_NOT_READY')
    try:
        waypipe = tool_version(TERMUX_PREFIX / 'bin/waypipe').splitlines()
        if waypipe != ['waypipe 0.11.3', 'features: lz4 zstd', 'unavailable: dmabuf video vaapi']:
            raise ValueError('DEPENDENCY_NOT_READY')
        result = {'mode': mode, 'waypipe': {'version': '0.11.3', 'features': ['lz4', 'zstd'],
                                          'unavailable': ['dmabuf', 'video', 'vaapi']}}
        if mode == 'pattern':
            path = TERMUX_HOME / '.local/lib/eidolon/eidolon-shm-fixture'
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 8388608:
                raise ValueError('DEPENDENCY_NOT_READY')
            fixture = json.loads(tool_version(path))
            if fixture != {'schema': 1, 'fixture_revision': 'eidolon-shm-client-v1', 'frames': 100}:
                raise ValueError('DEPENDENCY_NOT_READY')
            result['fixture'] = {**fixture, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            if not tool_version(TERMUX_PREFIX / 'bin/foot').startswith('foot version: 1.28.0 '):
                raise ValueError('DEPENDENCY_NOT_READY')
            if not tool_version(TERMUX_PREFIX / 'bin/footclient').startswith('footclient version: 1.28.0 '):
                raise ValueError('DEPENDENCY_NOT_READY')
            result['foot'] = {'version': '1.28.0'}
        return result
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        raise ValueError('DEPENDENCY_NOT_READY') from None


def pattern_summary(data):
    value = json.loads(data)
    if (set(value) != {'schema', 'result', 'fixture_revision', 'uid', 'protocol'} or value['schema'] != 1
            or value['result'] != 'pass' or value['fixture_revision'] != 'eidolon-shm-client-v1' or value['uid'] != os.getuid()
            or not isinstance(value['protocol'], dict) or value['protocol'].get('protocol_error') is not False):
        raise ValueError('PATTERN_SUMMARY_INVALID')
    for key in ('frames', 'callbacks', 'releases'):
        if type(value['protocol'].get(key)) is not int or value['protocol'][key] != 100:
            raise ValueError('PATTERN_SUMMARY_INVALID')
    for key in ('configures', 'acks'):
        if type(value['protocol'].get(key)) is not int or value['protocol'][key] < 1:
            raise ValueError('PATTERN_SUMMARY_INVALID')
    return value


def group_live(pid):
    # leaderをWNOWAITで保持している期間だけ調べる。後日のrecordからgroupへsignalしない。
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            identity = process_identity(int(entry.name))
        except PermissionError:
            continue
        if identity is not None and identity["group"] == pid and identity["state"] != "Z":
            return True
    return False


def waitable(child):
    # Popen.pollはreapするため使わない。group終了までPIDを予約して再利用を防ぐ。
    return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def echo_summary(data):
    value = json.loads(data)
    if (set(value) != {"schema", "result", "uid", "channels", "bytes", "copies"}
            or value["schema"] != 1 or value["result"] != "pass" or value["uid"] != os.getuid()
            or value["channels"] != 6 or value["bytes"] != sum(SIZES) or len(value["copies"]) != 6):
        raise ValueError("ECHO_SUMMARY_INVALID")
    for copy, size in zip(value["copies"], SIZES):
        if (set(copy) != {"size", "bytes", "sha256", "tail_matched"} or copy["size"] != size or copy["bytes"] != size
                or copy["tail_matched"] is not True or not isinstance(copy["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", copy["sha256"])):
            raise ValueError("ECHO_SUMMARY_INVALID")
    return value


def supervise(home, nonce, control_fd, emit, *, lease_seconds=60.0, grace_seconds=3.0, factory=child_command):
    """prepareの作成者がlease/子/FD/directoryを所有する。factoryの差替えはhost内部testのみ。"""
    prepared = prepare(home, nonce)
    directory = Path(prepared["socket"]).parent
    owner = process_identity(os.getpid())
    record = {**marker(nonce), "owner": owner, "child": None}
    write_record(directory / "lease.json", record)
    report = {**marker(nonce), "type": "finished", "reason": None, "child": None,
              "exit_status": None, "exit_signal": None, "term_sent": False, "kill_sent": False,
              "cleaned": False, "group_alive": False, "pipes_closed": False}
    child, child_reaped = None, False
    started_mode = None
    checked_tools = None
    terminals = None
    observer, observer_pending = None, False
    terminal_session, last_terminal = None, 0
    output = {"stdout": Tail(), "stderr": Tail()}
    opened = set()
    pending = bytearray()
    last_heartbeat = time.monotonic()
    stopping_at = None
    interrupted = [False]
    saved_signals = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            saved_signals[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda number, frame: interrupted.__setitem__(0, True))
    selector = selectors.DefaultSelector()
    original_blocking = os.get_blocking(control_fd)
    os.set_blocking(control_fd, False)

    def signal_owned(sig):
        if child is not None and not child_reaped:
            try:
                os.killpg(child.pid, sig)
                report["term_sent" if sig == signal.SIGTERM else "kill_sent"] = True
            except ProcessLookupError:
                pass

    def stop(reason):
        nonlocal stopping_at
        if stopping_at is None:
            report["reason"] = reason
            stopping_at = time.monotonic()
            if child is not None and group_live(child.pid):
                signal_owned(signal.SIGTERM)

    def publish(value):
        try:
            emit(value)
        except OSError:
            # SSH stdoutが先に閉じても所有者は停止・回収を最後まで進める。
            stop("IOFailed")

    try:
        selector.register(control_fd, selectors.EVENT_READ, "control")
        publish({**marker(nonce), **prepared, "type": "prepared"})
        while True:
            now = time.monotonic()
            if hasattr(emit, 'flush'):
                try: emit.flush()
                except OSError: stop('IOFailed')
            if interrupted[0]:
                stop("Signal")
            if stopping_at is None and now - last_heartbeat >= lease_seconds:
                stop("LeaseExpired")
            if child is not None and not child_reaped and waitable(child):
                stop("Completed")
                if not group_live(child.pid):
                    status = child.wait()
                    child_reaped = True
                    report["exit_status" if status >= 0 else "exit_signal"] = status if status >= 0 else -status
            if terminals is not None and stopping_at is None:
                opened_terminal = terminals.tick()
                if opened_terminal is not None:
                    publish({**marker(nonce), 'type': 'terminal_opened', **opened_terminal})
                    if not opened_terminal['success'] and opened_terminal['request_id'] is None:
                        report['error'] = 'DEPENDENCY_NOT_READY'
                        stop('ControlInvalid')
            if observer is not None and stopping_at is None:
                sample = observer.take()
                if sample is not None:
                    publish(sample)
            if stopping_at is not None:
                if child is None or child_reaped:
                    if not opened:
                        break
                elif now - stopping_at >= grace_seconds and not report["kill_sent"]:
                    signal_owned(signal.SIGKILL)
                if now - stopping_at > grace_seconds + 3:
                    report["reason"] = "CleanupTimeout"
                    break
            for key, _ in selector.select(timeout=0.05):
                try:
                    block = os.read(key.fd, 4096 if key.data == "control" else 65536)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fd)
                    if key.data == "control":
                        stop("EOF")
                    else:
                        opened.discard(key.data)
                    continue
                if key.data != "control":
                    output[key.data].push(block)
                    continue
                if stopping_at is not None:
                    continue
                pending.extend(block)
                if len(pending) > 4096:
                    stop("ControlInvalid")
                    continue
                while b"\n" in pending and stopping_at is None:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    try:
                        value = json.loads(line)
                        kind = value["type"]
                        shapes = {'start': {'type', 'nonce', 'mode'}, 'inspect_tools': {'type', 'nonce', 'mode'},
                                  'open_terminal': {'type', 'nonce', 'request_id'},
                                  'start_terminal_v1': {'type', 'nonce', 'mode', 'schema', 'session', 'terminal_id'},
                                  'open_terminal_v1': {'type', 'nonce', 'request_id', 'schema', 'session', 'terminal_id'}}
                        expected = shapes.get(kind, {'type', 'nonce'})
                        if len(line) > 1024 or set(value) != expected or value["nonce"] != nonce:
                            raise ValueError("CONTROL_INVALID")
                        if kind == "heartbeat":
                            last_heartbeat = time.monotonic()
                        elif kind == "stop":
                            stop("Stop")
                        elif kind in {'open_terminal', 'open_terminal_v1'} and terminals is not None and terminals.count > 0:
                            request = value['request_id']
                            if not isinstance(request,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',request) or int(request)>9223372036854775807:
                                raise ValueError('CONTROL_INVALID')
                            terminal = None
                            if kind == 'open_terminal_v1':
                                terminal = value['terminal_id']
                                if (type(value['schema']) is not int or value['schema'] != 1 or terminal_session is None
                                        or value['session'] != terminal_session or not valid_terminal_id(terminal) or int(terminal) <= last_terminal):
                                    raise ValueError('CONTROL_INVALID')
                                last_terminal = int(terminal)
                                if observer is not None: observer.allow(terminal)
                            elif terminal_session is not None:
                                raise ValueError('CONTROL_INVALID')
                            terminals.begin(request, terminal)
                        elif kind == 'inspect_tools' and child is None:
                            checked_tools = inspect_tools(value['mode'])
                            publish({**marker(nonce), 'type': 'tools', **checked_tools})
                        elif kind in {'start', 'start_terminal_v1'} and child is None and value["mode"] in {"echo", "pattern", "foot", "foot_fixture"}:
                            started_mode = value["mode"]
                            if started_mode != 'echo' and (checked_tools is None or checked_tools['mode'] != started_mode):
                                raise ValueError('DEPENDENCY_NOT_READY')
                            terminal = None
                            if kind == 'start_terminal_v1':
                                terminal = value['terminal_id']
                                terminal_session = value['session']
                                if (type(value['schema']) is not int or value['schema'] != 1 or started_mode not in {'foot', 'foot_fixture'}
                                        or not valid_terminal_id(terminal) or not valid_terminal_id(terminal_session)):
                                    raise ValueError('CONTROL_INVALID')
                                last_terminal = int(terminal)
                                try:
                                    observer = WorkloadObserver(directory, nonce, terminal_session)
                                    observer.allow(terminal)
                                except (OSError, ValueError):
                                    publish(dict(**marker(nonce), type='workload_v1', session=terminal_session,
                                                 epoch=secrets.token_hex(16), sequence='1', sampled_monotonic_ms=int(time.monotonic() * 1000),
                                                 boot=None, capability='unavailable', interval_seconds=None, scan_seconds=0, registrations=[], workloads=[]))
                            command = factory(value["mode"], nonce)
                            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                     start_new_session=True, close_fds=True,
                                                     env=tool_environment(directory) if started_mode != 'echo' else None)
                            identity = process_identity(child.pid)
                            if identity is None or identity["group"] != child.pid:
                                raise ValueError("CHILD_IDENTITY_INVALID")
                            report["child"] = record["child"] = identity
                            write_record(directory / "lease.json", record)
                            for name in ("stdout", "stderr"):
                                pipe = getattr(child, name)
                                os.set_blocking(pipe.fileno(), False)
                                selector.register(pipe, selectors.EVENT_READ, name)
                                opened.add(name)
                            publish({**marker(nonce), "type": "child_started", "child": identity})
                            if started_mode in {'foot','foot_fixture'}:
                                terminals = TerminalStarter(directory, started_mode)
                                terminals.begin(None, terminal)
                        else:
                            raise ValueError("CONTROL_INVALID")
                    except (KeyError, TypeError, ValueError, OSError) as error:
                        report["error"] = "DEPENDENCY_NOT_READY" if error.args == ("DEPENDENCY_NOT_READY",) else "CONTROL_INVALID"
                        stop("ControlInvalid")
        report["group_alive"] = child is not None and not child_reaped and group_live(child.pid)
    finally:
        if observer is not None:
            try: observer.close()
            except (OSError, ValueError):
                observer_pending = True
                report['error'] = 'CLEANUP_FAILED'
        if terminals is not None: terminals.close()
        if child is not None and not child_reaped:
            signal_owned(signal.SIGKILL)
            try:
                child.wait(timeout=2)
                child_reaped = True
            except subprocess.TimeoutExpired:
                report["group_alive"] = True
        selector.close()
        if child is not None:
            child.stdout.close()
            child.stderr.close()
        report["pipes_closed"] = not opened
        os.set_blocking(control_fd, original_blocking)
        for name, ring in output.items():
            report[name] = ring.metrics()
        # echoの固定JSON以外のstdout/stderr本文は診断/receiptへ公開しない。
        try:
            if started_mode == "echo":
                report["echo"] = echo_summary(output["stdout"].data)
            elif started_mode == 'pattern':
                report['pattern'] = pattern_summary(output['stdout'].data)
        except (ValueError, TypeError, KeyError):
            pass
        if (child is None or child_reaped) and not report["group_alive"] and not opened and not observer_pending:
            try:
                report["cleaned"] = cleanup(home, nonce, creator=True)["cleaned"]
            except (OSError, ValueError):
                report['error'] = 'CLEANUP_FAILED'
        if report["cleaned"]:
            write_record(directory.parent / ("r-" + nonce + ".json"), report)
            receipts = []
            for path in directory.parent.glob("r-*.json"):
                token = path.name[2:-5]
                if not re.fullmatch(r"[0-9a-f]{16}", token):
                    continue
                try:
                    saved = read_record(path, token)
                    if saved.get("type") == "finished" and saved.get("cleaned") is True:
                        receipts.append(path)
                except (ValueError, OSError):
                    continue
            for path in sorted(receipts, key=lambda p: p.stat().st_mtime_ns)[:-64]:
                path.unlink()
        publish(report)
        for sig, previous in saved_signals.items():
            signal.signal(sig, previous)
    return report


def main(argv):
    os.umask(0o077)
    if len(argv) == 3 and argv[0] in {'terminal-shell', 'terminal-fixture'}:
        terminal_shell(Path.home(), argv[1], argv[2], 'foot' if argv[0] == 'terminal-shell' else 'foot_fixture')
        return
    if len(argv) == 3 and argv[0] == 'register-tmux':
        directory = owned(Path.home(), argv[1])
        path = Path(argv[2])
        checked_tmux_socket(path)
        send_registration(directory, dict(schema=1, nonce=argv[1], kind='tmux_socket', path=str(path)))
        return
    if len(argv) != 2 or argv[0] not in {"prepare", "echo", "pattern", "foot", "foot_fixture", "cleanup"}:
        raise ValueError("MODE_INVALID")
    mode, nonce = argv
    if mode == "prepare":
        output = JsonOutput(sys.stdout.fileno())
        try:
            supervise(Path.home(), nonce, sys.stdin.fileno(), output)
        finally:
            output.close()
        return
    if mode in {"pattern", "foot", "foot_fixture"}:
        raise ValueError("DEPENDENCY_NOT_READY")
    operation = {"prepare": prepare, "echo": echo, "cleanup": cleanup}[mode]
    print(json.dumps(operation(Path.home(), nonce), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        print('{"schema":1,"result":"fail","error":"LAUNCHER_FAILED"}', flush=True)
        sys.exit(1)
