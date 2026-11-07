"""固定SDL自然終了1回のoperator。製品helperやGUI catalogを変更しない。"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time

from sdl_lease_capture import (CaptureError, canonical, capture_remote, decode, hexadecimal,
                               identity, need, proc_identity, read_at)

MAX_FILE = 8 * 1024 * 1024
MAX_OUTPUT = 65536


def checked_file(directory_fd, name, uid, mode, maximum):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == uid and
             stat.S_IMODE(before.st_mode) == mode and 0 < before.st_size <= maximum, "ARTIFACT_UNSAFE")
        data = bytearray()
        while len(data) <= maximum:
            block = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        need(len(data) <= maximum and len(data) == before.st_size and
             (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "ARTIFACT_CHANGED")
        return bytes(data)
    finally:
        os.close(fd)


def validate_artifacts(request, home):
    run_id = request.get("run_id")
    need(hexadecimal(run_id, 32) and hexadecimal(request.get("binary_sha256"), 64) and
         hexadecimal(request.get("manifest_sha256"), 64), "PIN_INVALID")
    uid = request["binding"]["uid"]
    need(uid == os.getuid(), "UID_MISMATCH")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(home, flags)
    try:
        for part in (".local", "state", "eidolon", "nx11", run_id):
            info = os.fstat(descriptor)
            need(info.st_uid == uid and not info.st_mode & 0o022, "ARTIFACT_DIRECTORY_UNSAFE")
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        need(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700, "ARTIFACT_DIRECTORY_UNSAFE")
        manifest_raw = checked_file(descriptor, "manifest.json", uid, 0o600, 16384)
        need(hashlib.sha256(manifest_raw).hexdigest() == request["manifest_sha256"], "MANIFEST_CHANGED")
        manifest = decode(manifest_raw, 16384)
        need(type(manifest) is dict and manifest.get("schema") == 1 and manifest.get("kind") == "eidolon-sdl-source-v1" and
             manifest.get("run_id") == run_id and manifest.get("files") == request.get("sources") and
             set(request.get("sources", {})) == {"sdl-wayland-probe.c", "eidolon-sdl-probe.py"}, "MANIFEST_MISMATCH")
        for name, expected in request["sources"].items():
            data = checked_file(descriptor, name, uid, 0o600, 65536)
            need(len(data) == expected.get("bytes") and hashlib.sha256(data).hexdigest() == expected.get("sha256"), "SOURCE_CHANGED")
        binary = checked_file(descriptor, "eidolon-sdl-probe", uid, 0o700, MAX_FILE)
        need(hashlib.sha256(binary).hexdigest() == request["binary_sha256"], "BINARY_CHANGED")
        need(len(binary) >= 64 and binary[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", binary, 18)[0] == 183,
             "BINARY_ELF_INVALID")
    finally:
        os.close(descriptor)
    return home / ".local/state/eidolon/nx11" / run_id


def live_binding(binding, home, captured):
    """ageを捏造せず、開始時に固定したlease/実processの生存と2envの行先を確認する。"""
    directory = home / ".eidolon" / ("s-" + binding["nonce"])
    uid = binding["uid"]
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        need(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700, "LEASE_CHANGED")
        record = decode(read_at(descriptor, "lease.json", 16384, uid, True), 16384)
        need(record.get("nonce") == binding["nonce"] and record.get("uid") == uid and
             identity(record.get("child"), True) == captured["identities"]["child"] and
             identity(record.get("owner")) == captured["identities"]["owner"], "LEASE_CHANGED")
        sock = os.stat("w", dir_fd=descriptor, follow_symlinks=False)
        need(stat.S_ISSOCK(sock.st_mode) and sock.st_uid == uid, "LEASE_CHANGED")
    finally:
        os.close(descriptor)
    for expected in captured["identities"].values():
        fd = os.open(f"/proc/{expected['pid']}", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            need(os.fstat(fd).st_uid == uid, "LEASE_CHANGED")
            current = proc_identity(fd, expected["pid"])
            need(all(current[key] == value for key, value in expected.items()), "LEASE_CHANGED")
        finally:
            os.close(fd)


def group_live(group):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rpartition(") ")[2].split()
            if len(fields) >= 3 and int(fields[2]) == group and fields[0] != "Z":
                return True
        except (OSError, ValueError):
            pass
    return False


def run_operator(request, home, control_fd, emit, *, capture=capture_remote, checker=live_binding, timeout=20.0):
    """capture/checker/短いtimeoutの差替えはlocalhost fixtureだけ。CLIへ露出しない。"""
    captured = capture(request["binding"], home)
    directory = validate_artifacts(request, home)
    checker(request["binding"], home, captured)
    need(hexadecimal(request.get("probe_id"), 32), "PROBE_ID_INVALID")
    result_name = "operator-" + request["probe_id"] + ".json"
    result_path = directory / result_name
    report_name = "operator-" + request["probe_id"] + "-report.json"
    need(not result_path.exists() and not result_path.is_symlink() and
         not (directory / report_name).exists() and not (directory / report_name).is_symlink(), "RESULT_EXISTS")
    prefix = home.parent / "usr"
    environment = dict(PATH=str(prefix / "bin") + ":/system/bin", PREFIX=str(prefix), HOME=str(home),
                       TMPDIR=str(prefix / "tmp"), LANG="C.UTF-8", LIBGL_ALWAYS_SOFTWARE="1", **captured["environment"])
    command = [sys.executable, str(directory / "eidolon-sdl-probe.py"), "--binary", str(directory / "eidolon-sdl-probe"),
               "--result", str(result_path), "--driver", "wayland", "--frames", "120", "--timeout", "15"]
    emit(dict(phase="validated", run_id=request["run_id"], session=request["binding"]["session"]))
    blocking = os.get_blocking(control_fd)
    selector = selectors.DefaultSelector()
    try:
        selector.register(control_fd, selectors.EVENT_READ, "control")
        os.set_blocking(control_fd, False)
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=environment, start_new_session=True, close_fds=True)
    except Exception:
        selector.close()
        os.set_blocking(control_fd, blocking)
        raise
    started, last_check = time.monotonic(), 0.0
    cancelled, cancel_at, forced, timed_out = None, None, False, False
    output = dict(stdout=0, stderr=0)
    pipes_drained = False
    control = bytearray()
    failure = None

    def cancel(reason):
        nonlocal cancelled, cancel_at
        if cancelled is None:
            cancelled, cancel_at = reason, time.monotonic()
            child.terminate()  # 実際にspawnしたrunnerだけ。runnerが自分のSDL groupを協調回収する。

    try:
        for name in ("stdout", "stderr"):
            pipe = getattr(child, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        emit(dict(phase="started", run_id=request["run_id"], runner_pid=child.pid, runner_group=child.pid))
        while True:
            now = time.monotonic()
            exited = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            if exited:
                break
            if now - started >= timeout:
                timed_out = True;cancel("OPERATOR_TIMEOUT")
            if now - last_check >= .1 and cancelled is None:
                last_check = now
                try:
                    checker(request["binding"], home, captured)
                except (OSError, ValueError, TypeError, KeyError):
                    cancel("LEASE_CHANGED")
            if cancel_at is not None and now - cancel_at >= 3:
                os.killpg(child.pid, signal.SIGKILL)
                forced = True
            if cancel_at is not None and now - cancel_at >= 5:
                raise TimeoutError("RUNNER_STOP_TIMEOUT")
            for key, _ in selector.select(.05):
                data = os.read(key.fd, 4096)
                if not data:
                    selector.unregister(key.fd)
                    if key.data == "control":
                        cancel("CONTROL_EOF")
                    continue
                if key.data == "control":
                    control.extend(data)
                    if len(control) > 1024:
                        cancel("CONTROL_INVALID")
                    if b"\n" in control:
                        need(decode(bytes(control).split(b"\n", 1)[0], 1024) == dict(type="cancel", run_id=request["run_id"]),
                             "CONTROL_INVALID")
                        cancel("HOST_CANCEL")
                else:
                    output[key.data] += len(data)
                    if output[key.data] > MAX_OUTPUT:
                        cancel("OUTPUT_LIMIT")
    except Exception as error:
        failure = error.reason if isinstance(error, CaptureError) else type(error).__name__
        cancel("OPERATOR_ERROR")
    finally:
        # WNOWAITでrunner leaderのPIDを予約したまま、所有groupだけを回収する。
        try:
            if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                child.terminate()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                    time.sleep(.02)
                if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                    forced = True
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
        finally:
            # 終了後に残ったpipeも上限付きでdrainし、本文は保持しない。
            drain_deadline = time.monotonic() + 1
            while any(key.data in {"stdout", "stderr"} for key in selector.get_map().values()) and time.monotonic() < drain_deadline:
                for key, _ in selector.select(.02):
                    if key.data == "control":
                        selector.unregister(key.fd)
                        continue
                    data = os.read(key.fd, 4096)
                    if not data:
                        selector.unregister(key.fd)
                    else:
                        output[key.data] += len(data)
            pipes_drained = not any(key.data in {"stdout", "stderr"} for key in selector.get_map().values())
            selector.close()
            child.stdout.close();child.stderr.close()
            os.set_blocking(control_fd, blocking)
    if cancelled is None:
        try:
            checker(request["binding"], home, captured)
        except (OSError, ValueError, TypeError, KeyError):
            cancelled = "LEASE_CHANGED"
    if any(size > MAX_OUTPUT for size in output.values()):
        failure = "OUTPUT_LIMIT"
    # 元のrenderer失敗/exit codeを上書きせず、固定runnerの結果をそのまま入れる。
    probe = None
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            probe = decode(checked_file(fd, result_name, os.getuid(), 0o600, MAX_OUTPUT), MAX_OUTPUT)
        finally:
            os.close(fd)
        need(type(probe) is dict and probe.get("schema") == 1 and probe.get("probe") == "eidolon-sdl-observation-v1" and
             probe.get("binary_sha256") == request["binary_sha256"], "PROBE_RESULT_INVALID")
        validate_artifacts(request, home)
    except (OSError, ValueError, TypeError, KeyError) as error:
        failure = error.reason if isinstance(error, CaptureError) else "PROBE_RESULT_UNAVAILABLE"
    reclaimed = not group_live(child.pid)
    cleanup = (not forced and reclaimed and pipes_drained and isinstance(probe, dict) and
               probe.get("group_reclaimed") is True and probe.get("reaped") is True)
    success = (cleanup and cancelled is None and failure is None and child.returncode == 0 and
               probe.get("graphics_client") == "completed" and probe.get("shm_gate") == "pass")
    result = dict(schema=1, operator="eidolon-sdl-live-v1", status="completed" if success else "failed",
                  run_id=request["run_id"], session=request["binding"]["session"], nonce=request["binding"]["nonce"],
                  binary_sha256=request["binary_sha256"], manifest_sha256=request["manifest_sha256"],
                  runner_returncode=child.returncode, runner_reaped=True, runner_group_reclaimed=reclaimed,
                  cleanup_confirmed=cleanup, pipes_drained=pipes_drained, forced_runner_kill=forced, timed_out=timed_out, cancel_reason=cancelled,
                  error=failure, output_bytes=output, result_file=result_name, probe_result=probe,
                  capture=captured, physical_input="not_run", hmd="not_run")
    fd = os.open(directory / report_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(result) + b"\n");stream.flush();os.fsync(stream.fileno())
    emit(dict(phase="finished", result=result))
    return result


def remote_main():
    from sdl_probe_install import termux_home
    previous = {}
    try:
        line = bytearray()
        while len(line) <= 16384:
            byte = os.read(0, 1)
            need(byte, "STARTUP_EOF")
            if byte == b"\n":
                break
            line.extend(byte)
        request = decode(bytes(line), 16384)
        def emit(value):
            print(json.dumps(value), flush=True)
        # signalでも協調取消を通す。観測PIDをkillせずrunnerが自分のSDLを回収する。
        interrupted = {"signal": False}
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[number] = signal.signal(number, lambda *_: interrupted.__setitem__("signal", True))
        def check(binding, home, captured):
            need(not interrupted["signal"], "WRAPPER_SIGNAL")
            live_binding(binding, home, captured)
        report = run_operator(request, termux_home(sys.executable), 0, emit, checker=check)
        return 0 if report["status"] == "completed" else 1
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps(dict(phase="blocked", reason=error.reason if isinstance(error, CaptureError) else "OPERATOR_UNAVAILABLE")), flush=True)
        return 2
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(remote_main())
