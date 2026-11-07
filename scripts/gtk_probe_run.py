"""固定gtk3-widget-factoryだけを現行leaseで短く観測し、実生成groupを停止する。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

from sdl_lease_capture import CaptureError, canonical, capture_remote, decode, hexadecimal, need
from sdl_probe_run import checked_file, group_live, live_binding

if "sdl_observation" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("sdl_observation", Path(__file__).parent / "termux/eidolon-sdl-probe.py")
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    sys.modules[_spec.name] = _module
from sdl_observation import TRACE_LIMIT, WaylandTrace

GTK_UID = 10158
GTK_VERSION = "3.24.52"
GTK_BYTES = 174336
GTK_SHA256 = "3438ab1bdd34eeb2132bef00ba28a075318750c6f2750d0f2f7a55213fff200b"
OBSERVATION_SECONDS = 5.0
MAX_STDOUT = 16384


def file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def validate_target(home):
    need(os.getuid() == GTK_UID, "GTK_UID_MISMATCH")
    prefix = home.parent / "usr"
    fd = os.open(prefix / "bin", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = os.stat("gtk3-widget-factory", dir_fd=fd, follow_symlinks=False)
        binary = checked_file(fd, "gtk3-widget-factory", GTK_UID, 0o700, GTK_BYTES)
        need(len(binary) == GTK_BYTES and hashlib.sha256(binary).hexdigest() == GTK_SHA256, "GTK_BINARY_CHANGED")
        after = os.stat("gtk3-widget-factory", dir_fd=fd, follow_symlinks=False)
        need(file_identity(before) == file_identity(after), "GTK_BINARY_CHANGED")
    finally:
        os.close(fd)
    # 固定package一つの版だけを読む。本文を保存しない。
    package = subprocess.run([str(prefix / "bin/dpkg-query"), "-W", "-f", "${db:Status-Status}:${Version}", "gtk3"],
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=3,
                             env=dict(PATH=str(prefix / "bin") + ":/system/bin", HOME=str(home), LANG="C.UTF-8"))
    need(package.returncode == 0 and package.stdout == ("installed:" + GTK_VERSION).encode() and not package.stderr,
         "GTK_PACKAGE_CHANGED")
    return prefix / "bin/gtk3-widget-factory", file_identity(after)


def private_run(home, probe_id):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(home, flags)
    try:
        for part in (".local", "state", "eidolon", "nx15"):
            info = os.fstat(fd)
            need(info.st_uid == os.getuid() and not info.st_mode & 0o022, "GTK_DIRECTORY_UNSAFE")
            try: os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError: pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd);fd = child
        info = os.fstat(fd)
        need(info.st_uid == os.getuid() and not info.st_mode & 0o022, "GTK_DIRECTORY_UNSAFE")
        os.mkdir(probe_id, mode=0o700, dir_fd=fd)  # 既存runは空でも再利用しない。
    finally:
        os.close(fd)
    directory = home / ".local/state/eidolon/nx15" / probe_id
    for name in ("home", "config", "cache"):
        (directory / name).mkdir(mode=0o700)
    return directory


def fixed_environment(home, directory, captured):
    prefix = home.parent / "usr"
    need(set(captured["environment"]) == {"WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"}, "GTK_ENV_INVALID")
    return dict(PATH=str(prefix / "bin") + ":/system/bin", PREFIX=str(prefix), TMPDIR=str(prefix / "tmp"),
                HOME=str(directory / "home"), XDG_CONFIG_HOME=str(directory / "config"),
                XDG_CACHE_HOME=str(directory / "cache"), XDG_DATA_DIRS=str(prefix / "share"), LANG="C.UTF-8",
                GDK_BACKEND="wayland", GDK_GL="disable", GDK_RENDERING="image", GSETTINGS_BACKEND="memory",
                GTK_USE_PORTAL="0", WAYLAND_DEBUG="client", **captured["environment"])


def observe_owned(command, environment, control_fd, checker, emit, *, executable_identity=None,
                  observation_seconds=OBSERVATION_SECONDS, grace_seconds=2.0, natural=False,
                  stdout_handler=None, trace=None, maximum_stdout=MAX_STDOUT, spawn_handler=None):
    """command/短時間の差替えはlocalhost fixture専用。CLIへ露出しない。"""
    blocking = os.get_blocking(control_fd)
    selector = selectors.DefaultSelector()
    try:
        selector.register(control_fd, selectors.EVENT_READ, "control")
        os.set_blocking(control_fd, False)
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=environment, start_new_session=True, close_fds=True)
    except Exception:
        selector.close();os.set_blocking(control_fd, blocking)
        raise
    trace, output = trace if trace is not None else WaylandTrace(), dict(stdout=0, stderr=0)
    started, last_check, stop_at = time.monotonic(), 0.0, None
    stop_reason, cancel_reason, failure = None, None, None
    term_sent = kill_sent = False
    control = bytearray()

    def send(number):
        nonlocal term_sent, kill_sent
        try:
            os.killpg(child.pid, number)  # WNOWAITでleaderを予約中。実生成groupだけ。
            if number == signal.SIGTERM: term_sent = True
            if number == signal.SIGKILL: kill_sent = True
        except ProcessLookupError:
            pass

    def stop(reason, cancelled=False):
        nonlocal stop_at, stop_reason, cancel_reason
        if cancelled and cancel_reason is None:
            cancel_reason = reason
        if stop_at is None:
            stop_at, stop_reason = time.monotonic(), reason
            if group_live(child.pid): send(signal.SIGTERM)

    def read_pipe(key):
        nonlocal failure, stdout_handler
        data = os.read(key.fd, 65536)
        if not data:
            selector.unregister(key.fd)
        else:
            output[key.data] += len(data)
            if key.data == "stderr": trace.feed(data)
            elif stdout_handler is not None and output["stdout"] <= maximum_stdout:
                try: stdout_handler(data)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    if failure is None: failure=error.reason if isinstance(error,CaptureError) else "GTK_CLIENT_OUTPUT_INVALID"
                    stdout_handler=None;stop("CLIENT_OUTPUT_INVALID",True)
            if output["stdout"] > maximum_stdout or output["stderr"] > TRACE_LIMIT:
                stop("OUTPUT_LIMIT", True)

    try:
        for name in ("stdout", "stderr"):
            pipe = getattr(child, name);os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        if spawn_handler is not None: spawn_handler(child.pid)
        if executable_identity is not None:
            info = os.stat(f"/proc/{child.pid}/exe")
            need(info.st_uid == os.getuid() and file_identity(info) == executable_identity, "GTK_EXEC_CHANGED")
        emit(dict(phase="started", child_pid=child.pid, child_group=child.pid))
        while True:
            now = time.monotonic()
            exited = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            if exited and stop_at is None: stop("NATURAL_EXIT" if natural else "EARLY_EXIT", not natural)
            if stop_at is None and now - started >= observation_seconds: stop("OBSERVATION_TIMEOUT" if natural else "OBSERVATION_COMPLETE", natural)
            if now - last_check >= .1 and cancel_reason is None:
                last_check = now
                try: checker()
                except (OSError, ValueError, TypeError, KeyError) as error:
                    stop(error.reason if isinstance(error, CaptureError) else "LEASE_CHANGED", True)
            if stop_at is not None and now - stop_at >= grace_seconds and group_live(child.pid): send(signal.SIGKILL)
            if stop_at is not None and now - stop_at >= grace_seconds + 2:
                failure = "GTK_STOP_TIMEOUT";break
            if (exited and not group_live(child.pid) and
                    not any(key.data in {"stdout", "stderr"} for key in selector.get_map().values())):
                break
            for key, _ in selector.select(.02):
                if key.data != "control":
                    read_pipe(key);continue
                data = os.read(key.fd, 1024)
                if not data:
                    selector.unregister(key.fd);stop("CONTROL_EOF", True);continue
                control.extend(data)
                if len(control) > 1024:
                    stop("CONTROL_INVALID", True)
                if b"\n" in control:
                    need(decode(bytes(control).split(b"\n", 1)[0], 1024) == {"type": "cancel"}, "CONTROL_INVALID")
                    stop("HOST_CANCEL", True)
    except Exception as error:
        failure = error.reason if isinstance(error, CaptureError) else "GTK_OPERATOR_IO_FAILED"
        stop("OPERATOR_ERROR", True)
    finally:
        # 例外時もTERM猶予→所有groupだけKILL。観測したfoot/root/Waypipeへsignalしない。
        if group_live(child.pid):
            if not term_sent: send(signal.SIGTERM)
            until = time.monotonic() + grace_seconds
            while group_live(child.pid) and time.monotonic() < until:
                for key, _ in selector.select(.02):
                    if key.data in {"stdout", "stderr"}: read_pipe(key)
                    else: selector.unregister(key.fd)
            if group_live(child.pid): send(signal.SIGKILL)
        # SIGKILL配送直後の短い生存も待つ。leaderをまだreapせずPGID再利用を防ぐ。
        until = time.monotonic() + 1
        while group_live(child.pid) and time.monotonic() < until:
            for key, _ in selector.select(.02):
                if key.data in {"stdout", "stderr"}: read_pipe(key)
                else: selector.unregister(key.fd)
        reclaimed = not group_live(child.pid)
        reaped = False
        try:
            child.wait(timeout=2);reaped = True
        except subprocess.TimeoutExpired:
            reclaimed = False
        until = time.monotonic() + 1
        while any(key.data in {"stdout", "stderr"} for key in selector.get_map().values()) and time.monotonic() < until:
            for key, _ in selector.select(.02):
                if key.data in {"stdout", "stderr"}: read_pipe(key)
                else: selector.unregister(key.fd)
        drained = not any(key.data in {"stdout", "stderr"} for key in selector.get_map().values())
        selector.close();child.stdout.close();child.stderr.close();os.set_blocking(control_fd, blocking)
    summary = trace.summary();summary["complete"] &= drained
    cleanup = reaped and reclaimed and drained
    shm = (cleanup and stop_reason == "OBSERVATION_COMPLETE" and cancel_reason is None and failure is None
           and summary["complete"] and summary["content_shm_commits"] >= 1 and summary["protocol_errors"] == 0
           and summary["content_non_shm_commits"] == 0 and summary["content_unknown_commits"] == 0
           and bool(summary["content_shm_formats"]) and set(summary["content_shm_formats"]) <= {0, 1})
    return dict(status="observed" if shm else "failed", stop_reason=stop_reason, cancel_reason=cancel_reason,
                observation_seconds=observation_seconds, elapsed_ms=int((time.monotonic() - started) * 1000),
                child_returncode=child.returncode,
                termination="unreaped" if child.returncode is None else "signal" if child.returncode < 0 else "natural",
                termination_signal=-child.returncode if child.returncode is not None and child.returncode < 0 else None,
                term_sent=term_sent, kill_sent=kill_sent, timed_out=failure == "GTK_STOP_TIMEOUT" or stop_reason=="OBSERVATION_TIMEOUT", reaped=reaped, group_reclaimed=reclaimed,
                pipes_drained=drained, cleanup_confirmed=cleanup, output_bytes=output, error=failure,
                trace=summary, shm_gate="pass" if shm else "fail")


def run_operator(request, home, control_fd, emit, *, signal_state=None):
    need(type(request) is dict and set(request) == {"binding", "probe_id"} and hexadecimal(request["probe_id"], 32), "GTK_REQUEST_INVALID")
    need(request["binding"]["uid"] == GTK_UID == os.getuid(), "GTK_UID_MISMATCH")
    captured = capture_remote(request["binding"], home)
    binary, expected_identity = validate_target(home)
    def check():
        need(not (signal_state and signal_state["signal"]), "WRAPPER_SIGNAL")
        live_binding(request["binding"], home, captured)
    check()
    directory = private_run(home, request["probe_id"])
    environment = fixed_environment(home, directory, captured)
    emit(dict(phase="validated", probe_id=request["probe_id"], session=request["binding"]["session"],
              gtk_version=GTK_VERSION, binary_sha256=GTK_SHA256))
    report = observe_owned([str(binary)], environment, control_fd, check, emit,
                           executable_identity=expected_identity)
    try: validate_target(home)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        report.update(status="failed", error="GTK_TARGET_CHANGED")
    report.update(schema=1, operator="eidolon-gtk-first-frame-v1", probe_id=request["probe_id"],
                  gtk_version=GTK_VERSION, binary_sha256=GTK_SHA256, uid=GTK_UID, capture=captured,
                  dialog="not_run", input="not_run", resize="not_run", hmd="not_run", physical_input="not_run")
    fd = os.open(directory / "report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(report) + b"\n");stream.flush();os.fsync(stream.fileno())
    emit(dict(phase="finished", result=report))
    return report


def remote_main():
    from sdl_probe_install import termux_home
    os.umask(0o077)
    previous, interrupted = {}, {"signal": False}
    try:
        line = bytearray()
        while len(line) <= 16384:
            byte = os.read(0, 1);need(byte, "STARTUP_EOF")
            if byte == b"\n": break
            line.extend(byte)
        need(len(line) <= 16384, "GTK_REQUEST_LIMIT")
        request = decode(bytes(line), 16384)
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[number] = signal.signal(number, lambda *_: interrupted.__setitem__("signal", True))
        report = run_operator(request, termux_home(sys.executable), 0, lambda value: print(json.dumps(value), flush=True),
                              signal_state=interrupted)
        return 0 if report["status"] == "observed" else 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(phase="blocked", reason=error.reason if isinstance(error, CaptureError) else "GTK_OPERATOR_UNAVAILABLE")), flush=True)
        return 2
    finally:
        for number, handler in previous.items(): signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(remote_main())
