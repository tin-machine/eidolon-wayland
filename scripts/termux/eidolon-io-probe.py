#!/usr/bin/env python3
"""NX13専用の固定self/子process probe。任意PID/path/commandは受け付けない。"""
import errno
import json
import os
import select
import signal
import stat
import sys
import tempfile
import time

FIELDS = ('rchar', 'wchar', 'syscr', 'syscw', 'read_bytes', 'write_bytes', 'cancelled_write_bytes')
FILE_BYTES = 1024 * 1024
LIMIT = 16384


class ProbeError(Exception):
    def __init__(self, reason, target="fixture"):
        self.reason, self.target = reason, target


def parse_io(text):
    result = {}
    for line in text.splitlines():
        parts = line.split(':')
        if len(parts) != 2 or parts[0] not in FIELDS or parts[0] in result:
            raise ProbeError('IO_FORMAT')
        value = parts[1].strip()
        if not value.isascii() or not value.isdecimal() or len(value) > 20:
            raise ProbeError('IO_FORMAT')
        result[parts[0]] = int(value)
        if result[parts[0]] > 2**64 - 1:
            raise ProbeError('IO_RANGE')
    if set(result) != set(FIELDS):
        raise ProbeError('IO_FIELDS_MISSING')
    return result


def parse_identity(stat_text, status_text, pid):
    head, separator, tail = stat_text.rpartition(') ')
    fields = tail.split()
    if not separator or not head.startswith(str(pid) + ' (') or len(fields) < 20:
        raise ProbeError('STAT_FORMAT')
    try:
        parent, group, session, start = (int(fields[i]) for i in (1, 2, 3, 19))
        rows = [line.split()[1:] for line in status_text.splitlines() if line.startswith('Uid:')]
        if len(rows) != 1 or len(rows[0]) != 4:
            raise ValueError()
        uids = tuple(map(int, rows[0]))
        if min(parent, group, session, start, *uids) < 0 or group == 0 or session == 0:
            raise ValueError()
    except ValueError:
        raise ProbeError('IDENTITY_FORMAT') from None
    return (pid, start, uids, parent, group, session)


def read_at(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
    try:
        value = os.read(fd, LIMIT + 1)
        if len(value) > LIMIT:
            raise ProbeError('PROC_LIMIT')
        try:
            return value.decode('ascii')
        except UnicodeError:
            raise ProbeError('PROC_FORMAT') from None
    finally:
        os.close(fd)


def identity_at(fd, pid):
    return parse_identity(read_at(fd, 'stat'), read_at(fd, 'status'), pid)


def checked_sample(pid, expected=None, *, self_sample=False):
    """proc directory FDを固定し、io読取りの前後でPID/starttime/全UID等を照合。"""
    fd = os.open('/proc/self' if self_sample else '/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        before = identity_at(fd, pid)
        if before[2] != (os.getuid(),) * 4:
            raise ProbeError('UID_CHANGED')
        if expected is not None and before != expected:
            raise ProbeError('IDENTITY_CHANGED')
        io = parse_io(read_at(fd, 'io'))
        if identity_at(fd, pid) != before:
            raise ProbeError('IDENTITY_CHANGED')
        return before, io
    finally:
        os.close(fd)


def error_token(error):
    if isinstance(error, ProbeError):
        return error.reason
    if isinstance(error, OSError):
        return {errno.EACCES: 'ACCESS_DENIED', errno.EPERM: 'ACCESS_DENIED', errno.ENOENT: 'PROCESS_GONE',
                errno.ESRCH: 'PROCESS_GONE'}.get(error.errno, 'OS_ERROR')
    return 'INTERNAL_ERROR'


def sample(pid, expected=None, *, self_sample=False):
    try:
        if expected is None:
            directory = os.open("/proc/self" if self_sample else "/proc/" + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                expected = identity_at(directory, pid)
            finally:
                os.close(directory)
        identity, io = checked_sample(pid, expected, self_sample=self_sample)
        return identity, dict(status='available', uid=os.getuid(), identity='pid_starttime_all_uids_stable', counters=io)
    except (OSError, ProbeError) as error:
        return expected, dict(status='unavailable', uid=os.getuid(), reason=error_token(error))


def delta(before, after):
    if before.get('status') != 'available' or after.get('status') != 'available':
        return dict(status='unavailable', reason='COUNTER_UNAVAILABLE')
    values = {key: after['counters'][key] - before['counters'][key] for key in FIELDS}
    if any(value < 0 for value in values.values()):
        return dict(status='unavailable', reason='COUNTER_REGRESSED')
    return dict(status='available', counters=values)


def send(fd, value):
    data = json.dumps(value, separators=(',', ':'), sort_keys=True).encode('ascii') + b'\n'
    if len(data) > LIMIT:
        raise ProbeError('OUTPUT_LIMIT')
    while data:
        data = data[os.write(fd, data):]


def receive(fd, timeout=3):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while b'\n' not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ProbeError('FIXTURE_TIMEOUT')
        chunk = os.read(fd, 4096)
        if not chunk:
            raise ProbeError('FIXTURE_EOF')
        data.extend(chunk)
        if len(data) > LIMIT:
            raise ProbeError('OUTPUT_LIMIT')
    try:
        return json.loads(data)
    except (UnicodeError, ValueError):
        raise ProbeError('FIXTURE_FORMAT') from None


def file_operation(directory, writer):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL if writer else os.O_RDONLY
    fd = os.open('fixed.bin', flags | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        if writer:
            os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ProbeError('FILE_IDENTITY')
        transferred = 0
        if writer:
            block = b'E' * 65536
            for _ in range(FILE_BYTES // len(block)):
                pending = block
                while pending:
                    amount = os.write(fd, pending)
                    transferred += amount
                    pending = pending[amount:]
            os.fsync(fd)
        else:
            while transferred < FILE_BYTES:
                data = os.read(fd, min(65536, FILE_BYTES - transferred))
                if not data or data != b'E' * len(data):
                    raise ProbeError('FILE_CONTENT')
                transferred += len(data)
            if os.read(fd, 1):
                raise ProbeError('FILE_SIZE')
        return dict(status='completed', bytes=transferred, fsync=writer)
    finally:
        os.close(fd)


def spawn_fixture(directory, writer):
    incoming, outgoing = os.pipe()
    commands, control = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(incoming); os.close(control)
        try:
            identity, before = sample(os.getpid(), self_sample=True)
            send(outgoing, before)
            if os.read(commands, 1) != b'G':
                os._exit(2)
            operation = file_operation(directory, writer)
            _, after = sample(os.getpid(), identity, self_sample=True)
            send(outgoing, dict(operation=operation, self_after=after))
            if os.read(commands, 1) != b'X':
                os._exit(2)
            os._exit(0)
        except BaseException as error:
            try:
                send(outgoing, dict(status='failed', reason=error_token(error)))
            finally:
                os._exit(2)
    os.close(outgoing); os.close(commands)
    return pid, incoming, control


def scenario(directory):
    """親と専用children以外を走査しない。wait前後を別sampleにする。"""
    observations = {}
    me, observations['parent_before'] = sample(os.getpid(), self_sample=True)
    children = []
    for role in ('writer', 'reader'):
        pid, incoming, control = spawn_fixture(directory, role == 'writer')
        children.append((pid, incoming, control, None))
        observations[role + '_self_before'] = receive(incoming)
        pin, observations[role + '_parent_before'] = sample(pid)
        children[-1] = (pid, incoming, control, pin)
        # PID/UIDを読めない場合もfixtureは自己所有子だけ。io利用可能とは扱わない。
        os.write(control, b'G')
        done = receive(incoming)
        if 'operation' not in done:
            raise ProbeError(done.get('reason', 'FIXTURE_FAILED'), role)
        observations[role + '_operation'] = done['operation']
        observations[role + '_self_after'] = done['self_after']
        _, observations[role + '_parent_live'] = sample(pid, pin)
    _, observations['parent_children_live'] = sample(os.getpid(), me, self_sample=True)
    pid, incoming, control, writer_pin = children[0]
    os.write(control, b'X')
    # WNOWAITでexitを観測し、親へのwait集計が起きる前のsampleを残す。
    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
    _, observations['parent_before_wait'] = sample(os.getpid(), me, self_sample=True)
    _, observations['writer_parent_zombie'] = sample(pid, writer_pin)
    waited, status = os.waitpid(pid, 0)
    if waited != pid or status != 0:
        raise ProbeError('CHILD_EXIT')
    _, observations['parent_after_writer_wait'] = sample(os.getpid(), me, self_sample=True)
    pid, _, control, _ = children[1]
    os.write(control, b'X')
    waited, status = os.waitpid(pid, 0)
    if waited != pid or status != 0:
        raise ProbeError('CHILD_EXIT')
    _, observations['parent_after_all_wait'] = sample(os.getpid(), me, self_sample=True)
    for _, incoming, control, _ in children:
        os.close(incoming); os.close(control)
    child_delta = delta(observations['writer_parent_before'], observations['writer_parent_live'])
    wait_delta = delta(observations['parent_before_wait'], observations['parent_after_writer_wait'])
    risk = dict(character_io='unknown', storage_io='unknown')
    if child_delta['status'] == wait_delta['status'] == 'available':
        for field, category in (('wchar', 'character_io'), ('write_bytes', 'storage_io')):
            written = child_delta['counters'][field]
            if written > 0 and wait_delta['counters'][field] >= written:
                risk[category] = 'observed'
    available = all(value.get('status') == 'available' for key, value in observations.items() if not key.endswith('_operation'))
    return dict(schema=1, probe='eidolon-io-v1', status='observed' if available else 'partial', uid=os.getuid(),
                reason='OBSERVATIONS_COMPLETE' if available else 'OBSERVATION_UNAVAILABLE',
                unavailable_targets=[key for key, value in observations.items() if value.get('status') == 'unavailable'],
                observations=observations, writer_delta=child_delta, parent_wait_delta=wait_delta,
                naive_sum_double_count_risk=risk, physical_disk_guaranteed=False, nx13_complete=False)


def run_probe():
    """専用session/group leaderをwaitする前に回収。既存shell/Termuxをkillしない。"""
    if not all(hasattr(os, name) for name in ('fork', 'setsid', 'waitid', 'WNOWAIT')):
        raise ProbeError('PROCESS_API_UNAVAILABLE')
    directory = tempfile.mkdtemp(prefix='eidolon-io-probe-')
    os.chmod(directory, 0o700)
    folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    info = os.fstat(folder)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(folder)
        raise ProbeError('DIRECTORY_IDENTITY')
    incoming, outgoing = os.pipe()
    child = None
    result = None
    cleaned = False
    group_gone = child is None
    handlers = {}
    def interrupted(_signal, _frame):
        raise ProbeError('INTERRUPTED')
    for number in (signal.SIGINT, signal.SIGTERM):
        handlers[number] = signal.signal(number, interrupted)
    try:
        child = os.fork()
        if child == 0:
            os.close(incoming)
            try:
                os.setsid()
                result = scenario(folder)
            except BaseException as error:
                result = dict(schema=1, probe='eidolon-io-v1', status='failed', uid=os.getuid(),
                              reason=error_token(error), target=getattr(error, 'target', 'fixture'), physical_disk_guaranteed=False, nx13_complete=False)
            try:
                send(outgoing, result)
            finally:
                os._exit(0)
        os.close(outgoing); outgoing = None
        result = receive(incoming, timeout=12)
    except (OSError, ProbeError) as error:
        result = dict(schema=1, probe='eidolon-io-v1', status='failed', uid=os.getuid(), reason=error_token(error), target=getattr(error, 'target', 'fixture'),
                      physical_disk_guaranteed=False, nx13_complete=False)
    finally:
        # A second ordinary signal must not interrupt recovery of this owned group.
        for number in handlers:
            signal.signal(number, signal.SIG_IGN)
        if child is not None:
            # Unreaped direct fork child pins PID; only this fixed code's dedicated group is signalled.
            try:
                if os.getpgid(child) == child and os.getsid(child) == child:
                    os.killpg(child, signal.SIGKILL)
                else:
                    os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(child, 0)
            deadline = time.monotonic() + .5
            group_gone = False
            while time.monotonic() < deadline:
                try:
                    os.killpg(child, 0)  # Read-only existence check; never signal after leader reaping.
                except ProcessLookupError:
                    group_gone = True
                    break
                time.sleep(.01)
        if outgoing is not None:
            os.close(outgoing)
        os.close(incoming)
        try:
            os.unlink('fixed.bin', dir_fd=folder)
        except FileNotFoundError:
            pass
        os.close(folder)
        try:
            os.rmdir(directory)
            cleaned = group_gone
        except OSError:
            pass
        for number, handler in handlers.items():
            signal.signal(number, handler)
    result['cleanup'] = 'completed' if cleaned else 'unconfirmed'
    if not cleaned:
        result['status'] = 'failed'
        result['reason'] = 'CLEANUP_UNCONFIRMED'
    return result


def main():
    if len(sys.argv) != 1:
        print(json.dumps(dict(schema=1, probe='eidolon-io-v1', status='rejected', reason='NO_ARGUMENTS_ALLOWED', uid=os.getuid(), physical_disk_guaranteed=False, nx13_complete=False)))
        return 2
    try:
        result = run_probe()
    except BaseException as error:
        result = dict(schema=1, probe='eidolon-io-v1', status='failed', uid=os.getuid(), reason=error_token(error), target=getattr(error, 'target', 'fixture'), cleanup='unconfirmed', physical_disk_guaranteed=False, nx13_complete=False)
    print(json.dumps(result, separators=(',', ':'), sort_keys=True))
    return 0 if result['status'] == 'observed' else 1


if __name__ == '__main__':
    sys.exit(main())
