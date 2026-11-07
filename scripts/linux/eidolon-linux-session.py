#!/usr/bin/python3
"""Linux専用固定SHM helper。CLIはprepare/cleanup+nonceのみ、任意argv/pathなし。"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

REVISION = 'eidolon-linux-shm-v1'
MAX_LINE = 16384
FILES = ('eidolon-linux-session.py', 'waypipe', 'eidolon-shm-fixture')
VERSION = b'waypipe 0.11.3\nfeatures: lz4 zstd\nunavailable: dmabuf video vaapi\n'


def require(value, token):
    if not value:
        raise ValueError(token)


def no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'JSON_INVALID')
        value[key] = item
    return value


def decode(data, limit=MAX_LINE):
    require(0 < len(data) <= limit, 'JSON_INVALID')
    return json.loads(data.decode('utf-8'), object_pairs_hook=no_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('JSON_INVALID')))


def checked_directory(path, private=True):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            (stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022), 'DIRECTORY_INVALID')


def private_read(path, limit=MAX_LINE, executable=False):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1 and
                not before.st_mode & 0o022 and (before.st_mode & 0o100 if executable else stat.S_IMODE(before.st_mode) == 0o600) and
                0 < before.st_size <= limit, 'FILE_INVALID')
        data = source.read(limit + 1)
        after = os.fstat(source.fileno())
        require(len(data) == before.st_size and (before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'FILE_CHANGED')
        return data


def account_home():
    require(sys.platform == 'linux' and os.getuid() == os.geteuid() != 0, 'PLATFORM_UNSUPPORTED')
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    require(home.is_absolute() and home.resolve() == home, 'HOME_INVALID')
    checked_directory(home, private=False)
    return home


def system_python():
    path = Path('/usr/bin/python3')
    # root所有のsystem interpreterはユーザー所有bundleと区別。環境PATHから選ばない。
    actual = path.resolve(strict=True)
    for item in (path, actual, *actual.parents):
        info = item.lstat()
        require(info.st_uid == 0 and (stat.S_ISLNK(info.st_mode) or not info.st_mode & 0o022), 'PYTHON_UNTRUSTED')
    require(stat.S_ISREG(actual.stat().st_mode) and os.access(actual, os.X_OK) and
            Path(sys.executable).resolve() == actual, 'PYTHON_UNTRUSTED')
    return path


def bundle(home):
    directory = home / '.local/lib/eidolon-linux'
    for parent in (home / '.local', home / '.local/lib'):
        checked_directory(parent, private=False)
    checked_directory(directory)
    manifest_bytes = private_read(directory / 'bundle.json')
    manifest = decode(manifest_bytes)
    require(set(manifest) == {'schema', 'backend', 'revision', 'files'} and type(manifest['schema']) is int and
            manifest['schema'] == 1 and manifest['backend'] == 'linux_posix' and manifest['revision'] == REVISION and
            type(manifest['files']) is dict and set(manifest['files']) == set(FILES), 'BUNDLE_INVALID')
    hashes = {}
    for name in FILES:
        expected = manifest['files'][name]
        require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'BUNDLE_INVALID')
        data = private_read(directory / name, 64 * 1024 * 1024, executable=name != FILES[0])
        hashes[name] = hashlib.sha256(data).hexdigest()
        require(hashes[name] == expected, 'BUNDLE_HASH_MISMATCH')
    require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == hashes[FILES[0]], 'HELPER_HASH_MISMATCH')
    return directory, hashes, hashlib.sha256(manifest_bytes).hexdigest()


def environment(home, directory):
    # 既存desktop/SSH-agent/Termux/Python/linker環境は子へ渡さない。
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'XDG_RUNTIME_DIR': str(directory), 'TMPDIR': str(directory), 'SHELL': '/bin/sh'}


def paths(home, nonce):
    require(isinstance(nonce, str) and re.fullmatch('[0-9a-f]{16}', nonce), 'NONCE_INVALID')
    root = home / '.eidolon-linux'
    directory = root / ('s-' + nonce)
    require(len(os.fsencode(directory / 'p')) <= 103, 'SOCKET_PATH_LONG')
    return root, directory


def marker(nonce):
    return {'schema': 1, 'backend': 'linux_posix', 'revision': REVISION, 'nonce': nonce, 'uid': os.getuid()}


def write_record(path, value, replace=True):
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode()
    require(len(raw) <= MAX_LINE, 'RECORD_LIMIT')
    descriptor, temporary = tempfile.mkstemp(prefix='.record-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        if not replace:
            os.link(temporary, path, follow_symlinks=False)  # exclusive publish
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def record(path, nonce):
    value = decode(private_read(path))
    require(type(value) is dict and all(value.get(key) == item and type(value.get(key)) is type(item)
            for key, item in marker(nonce).items()), 'OWNER_MISMATCH')
    return value


def identity(pid):
    try:
        directory = Path('/proc') / str(pid)
        fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        uids = next(line for line in (directory / 'status').read_text().splitlines() if line.startswith('Uid:')).split()[1:]
        require(len(uids) == 4 and all(int(uid) == os.getuid() for uid in uids), 'PROCESS_UID_MISMATCH')
        return {'pid': pid, 'start': fields[19], 'group': int(fields[2]), 'uid': os.getuid(), 'state': fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def same_process(saved):
    if type(saved) is not dict or type(saved.get('pid')) is not int or saved['pid'] <= 0:
        return False
    current = identity(saved['pid'])
    return current is not None and current['state'] != 'Z' and all(current[key] == saved.get(key) for key in ('pid', 'start', 'group', 'uid'))


def prepare(home, nonce):
    root, directory = paths(home, nonce)
    try: root.mkdir(mode=0o700)
    except FileExistsError: pass
    checked_directory(root)
    entries = []
    with os.scandir(root) as iterator:
        for item in iterator:
            require(len(entries) < 80, 'STATE_LIMIT'); entries.append(item.name)
    require(sum(name.startswith('s-') for name in entries) < 8, 'STATE_LIMIT')
    require(not os.path.lexists(root / ('r-' + nonce + '.json')), 'NONCE_REUSED')
    directory.mkdir(mode=0o700)  # nonce再利用/symlink/既存socketは拒否
    checked_directory(directory)
    write_record(directory / 'owner.json', marker(nonce), replace=False)
    return directory


def owned(home, nonce):
    root, directory = paths(home, nonce)
    checked_directory(root); checked_directory(directory)
    require(record(directory / 'owner.json', nonce) == marker(nonce), 'OWNER_MISMATCH')
    return directory


def cleanup(home, nonce, creator=None):
    root, directory = paths(home, nonce)
    checked_directory(root)
    if not os.path.lexists(directory):
        receipt = root / ('r-' + nonce + '.json')
        return {**marker(nonce), 'cleaned': True, 'already_absent': True,
                'receipt': record(receipt, nonce) if os.path.lexists(receipt) else None}
    directory = owned(home, nonce)
    lease = record(directory / 'lease.json', nonce)
    # 後日のrecordからPID/groupへsignalしない。creatorは現在の実processだけ。
    current = identity(os.getpid())
    if (creator is None or current is None or any(creator.get(key) != current[key] for key in ('pid', 'start', 'group', 'uid'))
            or lease['owner'] != creator or same_process(lease.get('child'))):
        return {**marker(nonce), 'cleaned': False, 'pending': True}
    require({item.name for item in directory.iterdir()} <= {'owner.json', 'lease.json', 'p', 'w'}, 'DIRECTORY_NOT_EMPTY')
    sockets = []
    for name in ('p', 'w'):
        path = directory / name
        if os.path.lexists(path):
            info = path.lstat()
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(), 'SOCKET_INVALID')
            sockets.append(path)
    for path in sockets: path.unlink()
    (directory / 'lease.json').unlink(); (directory / 'owner.json').unlink(); directory.rmdir()
    return {**marker(nonce), 'cleaned': True, 'already_absent': False}


class Group:
    def __init__(self, command, env):
        self.child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        self.reaped = False
        try:
            self.identity = identity(self.child.pid)
            require(self.identity is not None and self.identity['group'] == self.child.pid, 'PROCESS_IDENTITY_INVALID')
        except (OSError, ValueError):
            self.signal(signal.SIGKILL); self.child.wait(timeout=2); self.reaped = True
            self.child.stdout.close(); self.child.stderr.close(); raise

    def exited(self):
        return os.waitid(os.P_PID, self.child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None

    def signal(self, number):
        if not self.reaped:
            try: os.killpg(self.child.pid, number)
            except ProcessLookupError: pass

    def live(self):
        # leaderをWNOWAITで保持している間だけgroupを検査。曖昧なら回収完了にしない。
        deadline = time.monotonic() + .05
        with os.scandir('/proc') as entries:
            for count, entry in enumerate(entries):
                require(count < 16384 and time.monotonic() < deadline, 'GROUP_SCAN_LIMIT')
                if not entry.name.isdecimal(): continue
                try:
                    fields = Path(entry.path, 'stat').read_text().rsplit(')', 1)[1].split()
                except (FileNotFoundError, ProcessLookupError, PermissionError): continue
                if int(fields[2]) == self.child.pid and fields[0] != 'Z': return True
        return False

    def finish(self):
        if self.reaped: return True
        self.signal(signal.SIGKILL)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                if self.exited() and not self.live():
                    self.child.wait(); self.reaped = True; return True
            except (OSError, ValueError): pass
            time.sleep(.01)
        return False


class Ring:
    def __init__(self): self.data, self.total = bytearray(), 0
    def push(self, raw): self.total += len(raw); self.data.extend(raw); del self.data[:-65536]
    def metadata(self): return {'bytes': self.total, 'retained': len(self.data), 'truncated': self.total > 65536}


def tool_output(path, env):
    group = Group([str(path), '--version'], env)
    selector = selectors.DefaultSelector(); data = bytearray(); error_bytes = 0
    try:
        for name in ('stdout', 'stderr'):
            pipe = getattr(group.child, name); os.set_blocking(pipe.fileno(), False); selector.register(pipe, selectors.EVENT_READ, name)
        deadline = time.monotonic() + 3
        while selector.get_map() or not group.exited():
            require(time.monotonic() < deadline, 'TOOL_TIMEOUT')
            for key, _ in selector.select(.01):
                raw = os.read(key.fd, 4096)
                if not raw: selector.unregister(key.fd)
                elif key.data == 'stdout': data.extend(raw)
                else: error_bytes += len(raw)
                require(len(data) <= 4096 and error_bytes <= 4096, 'TOOL_OUTPUT_LIMIT')
        require(os.waitid(os.P_PID, group.child.pid, os.WEXITED | os.WNOWAIT).si_status == 0, 'TOOL_FAILED')
        return bytes(data)
    finally:
        finished = group.finish(); selector.close(); group.child.stdout.close(); group.child.stderr.close()
        require(finished, 'TOOL_CLEANUP_PENDING')


def inspect_tools(home, directory):
    tools, hashes, bundle_hash = bundle(home)
    env = environment(home, directory)
    require(tool_output(tools / 'waypipe', env) == VERSION, 'WAYPIPE_VERSION_MISMATCH')
    fixture = decode(tool_output(tools / 'eidolon-shm-fixture', env))
    require(type(fixture) is dict and set(fixture) == {'schema', 'fixture_revision', 'frames'} and
            type(fixture['schema']) is int and fixture['schema'] == 1 and type(fixture['frames']) is int and
            fixture['frames'] == 100 and fixture['fixture_revision'] == 'eidolon-shm-client-v1', 'FIXTURE_VERSION_MISMATCH')
    return {'waypipe_version': '0.11.3', 'fixture_revision': 'eidolon-shm-client-v1', 'frames': 100,
            'bundle_sha256': bundle_hash, 'files': hashes}


def child_command(home, directory):
    tools, _, _ = bundle(home)  # inspect後の差替えもhashを再検査
    forwarded = (directory / 'p').lstat()
    require(stat.S_ISSOCK(forwarded.st_mode) and forwarded.st_uid == os.getuid(), 'STREAMLOCAL_MISSING')
    require(not os.path.lexists(directory / 'w'), 'DISPLAY_REUSED')
    return [str(tools / 'waypipe'), '--no-gpu', '--compress', 'none', '--threads', '1', '--socket',
            str(directory / 'p'), '--display', str(directory / 'w'), 'server', '--', str(tools / 'eidolon-shm-fixture'), '--pattern']


class Output:
    def __init__(self, descriptor):
        self.descriptor, self.pending = descriptor, bytearray()
        self.blocking = os.get_blocking(descriptor); os.set_blocking(descriptor, False)
    def __call__(self, value):
        raw = json.dumps(value, separators=(',', ':')).encode() + b'\n'
        require(len(raw) <= MAX_LINE and len(self.pending) + len(raw) <= 32768, 'OUTPUT_LIMIT')
        self.pending.extend(raw); self.flush()
    def flush(self):
        if self.pending:
            try: count = os.write(self.descriptor, self.pending)
            except BlockingIOError: return
            del self.pending[:count]
    def close(self): os.set_blocking(self.descriptor, self.blocking)


def serve(home, nonce, control_fd, emit, *, lease_seconds=60.0, maximum_seconds=120.0,
          inspect=inspect_tools, factory=child_command):
    """inspect/factory/homeの注入はhost test内部のみ。CLIはaccount_home/bundleに固定。"""
    directory = prepare(home, nonce); owner = identity(os.getpid())
    lease = {**marker(nonce), 'owner': owner, 'child': None}
    write_record(directory / 'lease.json', lease)
    result = {**marker(nonce), 'type': 'finished', 'reason': None, 'cleaned': False, 'group_alive': False,
              'child_reaped': False, 'pipes_closed': False, 'pattern_verified': False}
    child = None; inspected = False; pending = bytearray(); stopping = None; interrupted = [False]
    started = last_heartbeat = time.monotonic(); rings = {'stdout': Ring(), 'stderr': Ring()}
    selector = selectors.DefaultSelector(); blocking = os.get_blocking(control_fd); opened = set(); signals = {}
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            signals[number] = signal.getsignal(number); signal.signal(number, lambda *_: interrupted.__setitem__(0, True))
    def stop(reason):
        nonlocal stopping
        if stopping is None:
            stopping = time.monotonic(); result['reason'] = reason
            if child is not None: child.signal(signal.SIGTERM)
    def publish(value):
        try: emit(value)
        except (OSError, ValueError): stop('OutputFailed')
    try:
        os.set_blocking(control_fd, False); selector.register(control_fd, selectors.EVENT_READ, 'control')
        publish({**marker(nonce), 'type': 'prepared', 'socket': str(directory / 'p'),
                 'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        while True:
            now = time.monotonic()
            if hasattr(emit, 'flush'):
                try: emit.flush()
                except OSError: stop('OutputFailed')
            if interrupted[0]: stop('Signal')
            if now - last_heartbeat >= lease_seconds: stop('LeaseExpired')
            if now - started >= maximum_seconds: stop('MaximumDuration')
            if child is not None and child.exited(): stop('Completed')
            if stopping is not None:
                if child is not None and now - stopping >= 1: child.signal(signal.SIGKILL)
                if child is None or (child.exited() and not child.live() and not opened): break
                if now - stopping > 4: break
            for key, _ in selector.select(.02):
                try: raw = os.read(key.fd, 4096)
                except BlockingIOError: continue
                if not raw:
                    selector.unregister(key.fd)
                    if key.data == 'control': stop('EOF')
                    else: opened.discard(key.data)
                    continue
                if key.data != 'control': rings[key.data].push(raw); continue
                if stopping is not None: continue
                pending.extend(raw)
                while b'\n' in pending and stopping is None:
                    line, _, rest = pending.partition(b'\n'); pending = bytearray(rest)
                    try:
                        value = decode(line)
                        require(type(value) is dict and value.get('schema') == 1 and type(value.get('schema')) is int and
                                value.get('nonce') == nonce, 'CONTROL_INVALID')
                        kind = value.get('type'); fields = {'schema', 'nonce', 'type'} | ({'mode'} if kind == 'start' else set())
                        require(set(value) == fields, 'CONTROL_INVALID')
                        if kind == 'heartbeat': last_heartbeat = time.monotonic()
                        elif kind == 'stop': stop('Stop')
                        elif kind == 'inspect_tools' and child is None and not inspected:
                            tools = inspect(home, directory); inspected = True
                            publish({**marker(nonce), 'type': 'tools', **tools})
                        elif kind == 'start' and value['mode'] == 'pattern' and child is None and inspected:
                            child = Group(factory(home, directory), environment(home, directory))
                            lease['child'] = child.identity; write_record(directory / 'lease.json', lease)
                            for name in rings:
                                pipe = getattr(child.child, name); os.set_blocking(pipe.fileno(), False)
                                selector.register(pipe, selectors.EVENT_READ, name); opened.add(name)
                            publish({**marker(nonce), 'type': 'child_started', 'child': child.identity})
                        else: raise ValueError('CONTROL_INVALID')
                    except (OSError, ValueError, TypeError, KeyError, RecursionError): stop('ControlInvalid')
                if len(pending) > MAX_LINE: stop('ControlInvalid')
    except (OSError, ValueError, TypeError, KeyError):
        stop('InternalFailure')
    finally:
        group_finished = child is None or child.finish()
        result['group_alive'] = not group_finished
        if child is not None:
            result['child_reaped'] = child.reaped
            result['exit_status'] = child.child.returncode
            child.child.stdout.close(); child.child.stderr.close()
        result['pipes_closed'] = not opened
        selector.close(); os.set_blocking(control_fd, blocking)
        result.update({name: ring.metadata() for name, ring in rings.items()})
        if child is not None and child.reaped and child.child.returncode == 0:
            try:
                report = decode(rings['stdout'].data, 65536)
                protocol = report['protocol']
                require(type(report['schema']) is int and report['schema'] == 1 and report['result'] == 'pass' and
                        type(report['uid']) is int and report['uid'] == os.getuid() and report['fixture_revision'] == 'eidolon-shm-client-v1' and
                        all(type(protocol[key]) is int and protocol[key] >= 1 for key in ('frames', 'configures', 'acks', 'releases', 'callbacks')) and
                        protocol['frames'] == 100 and protocol['callbacks'] == 100 and protocol['releases'] == 100 and
                        protocol['protocol_error'] is False, 'PATTERN_INVALID')
                result['pattern_verified'] = True
                result['pattern'] = {key: protocol[key] for key in ('frames', 'configures', 'acks', 'releases', 'callbacks')}
            except (ValueError, TypeError, KeyError): pass
        if group_finished and not opened:
            try: result['cleaned'] = cleanup(home, nonce, owner)['cleaned']
            except (OSError, ValueError): result['reason'] = 'CleanupPending'
        if result['cleaned']:
            write_record(directory.parent / ('r-' + nonce + '.json'), result, replace=False)
            receipts = []
            for path in directory.parent.iterdir():
                if re.fullmatch(r'r-[0-9a-f]{16}\.json', path.name):
                    try:
                        saved = record(path, path.name[2:-5])
                        if saved.get('type') == 'finished' and saved.get('cleaned') is True: receipts.append(path)
                    except (OSError, ValueError): pass
            for path in sorted(receipts, key=lambda item: item.stat().st_mtime_ns)[:-64]: path.unlink()
        publish(result)
        for number, previous in signals.items(): signal.signal(number, previous)
    return result


def main(argv):
    os.umask(0o077); system_python(); home = account_home()
    require(sys.flags.isolated == 1 and sys.flags.no_site == 1, 'PYTHON_FLAGS_UNSAFE')
    require(len(argv) == 2 and argv[0] in {'prepare', 'cleanup'}, 'MODE_INVALID')
    mode, nonce = argv; paths(home, nonce)
    if mode == 'cleanup': print(json.dumps(cleanup(home, nonce), separators=(',', ':'))); return
    bundle(home)
    output = Output(sys.stdout.fileno())
    try:
        serve(home, nonce, sys.stdin.fileno(), output)
        deadline = time.monotonic() + 1
        while output.pending and time.monotonic() < deadline: output.flush(); time.sleep(.01)
    finally: output.close()


if __name__ == '__main__':
    try: main(sys.argv[1:])
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        try: print('{"schema":1,"error":"LINUX_HELPER_FAILED"}', flush=True)
        except OSError: pass
        sys.exit(1)
