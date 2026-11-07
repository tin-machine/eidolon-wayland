#!/usr/bin/env python3
"""登録済み専用terminal root用の固定1MiB I/O。任意引数/path/PIDは受理しない。"""
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import tempfile
import time

FILE_BYTES = 1024 * 1024
PHASE_SECONDS = (6., 6., 4., 8.)  # baseline / live after write / zombie / after wait
DEADLINE_SECONDS = 40.


class FixtureFailure(Exception):
    pass


def interrupted(signum, frame):
    raise FixtureFailure('timeout' if signum == signal.SIGALRM else 'cancelled')


def receive(fd, expected, timeout=3.):
    if not select.select([fd], [], [], timeout)[0] or os.read(fd, 1) != expected:
        raise FixtureFailure('child_protocol')


def wait(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(.05, max(0., deadline - time.monotonic())))


def writer(control, reply, directory):
    # 専用leaderをwaitするまで保持し、PID再利用後のgroupへsignalしない。
    os.setsid()
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    path = directory / 'fixed.bin'
    code = 0
    try:
        os.write(reply, b'R')
        receive(control, b'W', DEADLINE_SECONDS)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            block = bytes(65536)
            for _ in range(FILE_BYTES // len(block)):
                pending = memoryview(block)
                while pending:
                    written = os.write(fd, pending)
                    if written <= 0: raise FixtureFailure('short_write')
                    pending = pending[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.write(reply, b'D')
        receive(control, b'X', DEADLINE_SECONDS)
    except (OSError, FixtureFailure):
        code = 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try: path.unlink(missing_ok=True)
        except OSError: code = 1
        # 親が強制終了しても固定data directoryを残さない。
        try: directory.rmdir()
        except OSError: code = 1
        os._exit(code)


def reap_owned(pid, group_ready):
    """未回収のdirect childだけ。既存foot/tmux/sessionのgroupには触らない。"""
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        return False
    try:
        if group_ready:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 2.
    while time.monotonic() < deadline:
        result, _ = os.waitpid(pid, os.WNOHANG)
        if result == pid: return True
        time.sleep(.01)
    return False


def run_fixture(parent, emit, *, phases=PHASE_SECONDS):
    """parent/phasesの差替えはhost fixtureだけ。CLIには公開しない。"""
    directory = Path(tempfile.mkdtemp(prefix='.io-data-', dir=parent))
    os.chmod(directory, 0o700)
    control_read, control_write = os.pipe()
    reply_read, reply_write = os.pipe()
    pid, group_ready, result, reason, cleanup = None, False, 'fail', None, True
    try:
        pid = os.fork()
        if pid == 0:
            # setsid/初期化失敗を親のfinallyへ落とさない（pid=0のsignal禁止）。
            try:
                os.close(control_write); os.close(reply_read)
                writer(control_read, reply_write, directory)
            finally:
                os._exit(1)
        cleanup = False
        os.close(control_read); control_read = None
        os.close(reply_write); reply_write = None
        receive(reply_read, b'R')
        if os.getpgid(pid) != pid: raise FixtureFailure('child_group')
        group_ready = True
        emit('baseline'); wait(phases[0])
        os.write(control_write, b'W'); receive(reply_read, b'D')
        emit('written_live'); wait(phases[1])
        os.write(control_write, b'X')
        deadline = time.monotonic() + 3.
        while True:
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if info is not None: break
            if time.monotonic() >= deadline: raise FixtureFailure('exit_timeout')
            wait(.02)
        if info.si_code != os.CLD_EXITED or info.si_status != 0: raise FixtureFailure('child_failed')
        emit('zombie'); wait(phases[2])
        # leaderを回収する前に自分のgroupを終了。以後このPID/groupへsignalしない。
        cleanup = reap_owned(pid, group_ready)
        if not cleanup: raise FixtureFailure('cleanup_pending')
        pid = None
        emit('waited_idle'); wait(phases[3])
        result = 'completed'
    except FixtureFailure as failure:
        reason = str(failure)
    except OSError:
        reason = 'io_unavailable'
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if pid is not None:
            cleanup = reap_owned(pid, group_ready)
        for fd in (control_read, control_write, reply_read, reply_write):
            if fd is not None:
                try: os.close(fd)
                except OSError: pass
        try:
            (directory / 'fixed.bin').unlink(missing_ok=True)
            if directory.exists(): directory.rmdir()
        except OSError:
            cleanup = False
        if not cleanup: result, reason = 'fail', 'cleanup_pending'
    return dict(schema=1, result=result, reason=reason, cleanup_completed=cleanup,
                file_bytes=FILE_BYTES, uid=os.getuid(), physical_disk_guaranteed=False)


def main():
    if (len(sys.argv) != 1 or not re.fullmatch(r'[0-9a-f]{16}', os.environ.get('EIDOLON_SESSION_NONCE', ''))
            or not re.fullmatch(r'[1-9][0-9]{0,18}', os.environ.get('EIDOLON_TERMINAL_ID', ''))):
        print(json.dumps(dict(result='rejected', reason='registered_terminal_required')))
        return 2
    parent = Path(__file__).absolute().parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        return 2
    for name in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
        signal.signal(name, interrupted)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    started = time.monotonic()
    def emit(phase):
        print(json.dumps(dict(phase=phase, elapsed_ms=int((time.monotonic()-started)*1000), uid=os.getuid())), flush=True)
    result = run_fixture(parent, emit)
    print(json.dumps(result), flush=True)
    return 0 if result['result'] == 'completed' else 1


if __name__ == '__main__': sys.exit(main())
