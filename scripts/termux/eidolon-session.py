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

SIZES = (1, 65536, *(8 * 1024 * 1024 for _ in range(4)))
BLOCK = bytes(range(256)) * 256
TRAILER = b"AFTER_EOF"
RING_BYTES = 65536
TERMUX_PREFIX = Path('/data/data/com.termux/files/usr')
TERMUX_HOME = Path('/data/data/com.termux/files/home')
_direct_exec_supported = None


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
    if {entry.name for entry in directory.iterdir()} - {"owner.json", "lease.json", "p", "w", "f"}:
        raise ValueError("DIRECTORY_NOT_EMPTY")
    sockets = []
    for name in ('p', 'w', 'f'):
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


def terminal_command(directory, mode):
    command = [str(TERMUX_PREFIX / 'bin/footclient'), '--server-socket=' + str(directory / 'f'), '--no-wait']
    if mode == 'foot_fixture':
        source = ("import time\n"
                  "print('Eidolon foot fixture: ABCD 0123456789', flush=True)\n"
                  "for row in range(1200):\n"
                  " print('row %04d | ABCDEFGH 0123456789 | scroll fixture' % row, flush=True)\n"
                  " time.sleep(0.05)\n"
                  "time.sleep(600)\n")
        command += [sys.executable, '-u', '-c', source]
    elif mode == 'foot':
        command += [str(TERMUX_PREFIX / 'bin/bash'), '--noprofile', '--norc', '-i']
    else:
        raise ValueError('DEPENDENCY_NOT_READY')
    return command


class TerminalStarter:
    """短命footclientを所有し、lease/Stopを待たせずにserverへ固定起動を依頼する。"""
    def __init__(self, directory, mode):
        self.directory, self.mode = directory, mode
        self.child = None
        self.deadline = None
        self.request = None
        self.count = 0

    def begin(self, request):
        if self.deadline is not None:
            raise ValueError('CONTROL_INVALID')
        self.request = request
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
                self.child = subprocess.Popen(terminal_command(self.directory, self.mode),
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
                        expected = {"type", "nonce", "mode"} if kind in {"start", "inspect_tools"} else ({"type", "nonce", "request_id"} if kind == "open_terminal" else {"type", "nonce"})
                        if len(line) > 1024 or set(value) != expected or value["nonce"] != nonce:
                            raise ValueError("CONTROL_INVALID")
                        if kind == "heartbeat":
                            last_heartbeat = time.monotonic()
                        elif kind == "stop":
                            stop("Stop")
                        elif kind == 'open_terminal' and terminals is not None and terminals.count > 0:
                            request = value['request_id']
                            if not isinstance(request,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',request) or int(request)>9223372036854775807:
                                raise ValueError('CONTROL_INVALID')
                            terminals.begin(request)
                        elif kind == 'inspect_tools' and child is None:
                            checked_tools = inspect_tools(value['mode'])
                            publish({**marker(nonce), 'type': 'tools', **checked_tools})
                        elif kind == "start" and child is None and value["mode"] in {"echo", "pattern", "foot", "foot_fixture"}:
                            started_mode = value["mode"]
                            if started_mode != 'echo' and (checked_tools is None or checked_tools['mode'] != started_mode):
                                raise ValueError('DEPENDENCY_NOT_READY')
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
                                terminals.begin(None)
                        else:
                            raise ValueError("CONTROL_INVALID")
                    except (KeyError, TypeError, ValueError, OSError) as error:
                        report["error"] = "DEPENDENCY_NOT_READY" if error.args == ("DEPENDENCY_NOT_READY",) else "CONTROL_INVALID"
                        stop("ControlInvalid")
        report["group_alive"] = child is not None and not child_reaped and group_live(child.pid)
    finally:
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
        if (child is None or child_reaped) and not report["group_alive"] and not opened:
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
    if len(argv) != 2 or argv[0] not in {"prepare", "echo", "pattern", "foot", "foot_fixture", "cleanup"}:
        raise ValueError("MODE_INVALID")
    mode, nonce = argv
    if mode == "prepare":
        supervise(Path.home(), nonce, sys.stdin.fileno(), lambda value: print(json.dumps(value, separators=(",", ":")), flush=True))
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
