"""固定Termux runtimeの公開EGL APIだけでsurfaceless CPU初期化を分離する。"""
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

from sdl_probe_install import InstallError, canonical, require, termux_home, unique_object
from egl_loaded_identity import capture as capture_loaded, NAMES as LOADED_NAMES, PROFILE as LOADED_PROFILE, REASONS as LOADED_REASONS

if "sdl_observation" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("sdl_observation", Path(__file__).parent / "termux/eidolon-sdl-probe.py")
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    sys.modules[_spec.name] = _module
from sdl_observation import EglLogSummary, TRACE_LIMIT

UID = 10158
OPERATOR = "eidolon-egl-surfaceless-v3-kopper-variant"
VARIANTS = ("baseline", "kopper-disabled")
LIBRARIES = {
    "libEGL.so.1.1.0": "88ccad71e437eadaead1729d43ff8bd0fed9a5a70956d0dcd98532f0558ee6e8",
    "libEGL_mesa.so": "4c07cd55d4d59c4db60035abb2cb517776f50829cac5486de2f5c18ccf72f228",
    "libgallium-26.2.6.so": "2684bf73cd4cec80f40c19ffd4e787af4680b9c03912650e1bdc7320670934f1",
    "libc++_shared.so": "05bef9d3901910c3ff8ab450b03bc8b28383d363a2290f2f7b9aed7331ea6ff7",
}
ALIASES = {"libEGL.so.1": "libEGL.so.1.1.0", "libEGL_mesa.so.0": "libEGL_mesa.so"}
VENDOR = "share/glvnd/egl_vendor.d/50_mesa.json"
MAX_LIBRARY = 32 * 1024 * 1024
MAX_STDOUT = 16384
TIMEOUT = 12.0
CHILD_SOURCE = None  # hostが同じrepositoryの固定sourceだけを埋め込む。requestでは受け取らない。
ERRORS = {0x3000: "success", 0x3001: "not_initialized", 0x3002: "bad_access", 0x3003: "bad_alloc",
          0x3004: "bad_attribute", 0x3005: "bad_config", 0x3006: "bad_context", 0x3007: "bad_current_surface",
          0x3008: "bad_display", 0x3009: "bad_match", 0x300A: "bad_native_pixmap", 0x300B: "bad_native_window",
          0x300C: "bad_parameter", 0x300D: "bad_surface", 0x300E: "context_lost"}
BLOCKED = {"UID_MISMATCH", "RUNTIME_DIRECTORY_UNSAFE", "RUNTIME_LIBRARY_UNSAFE", "RUNTIME_LIBRARY_CHANGED",
           "RUNTIME_ALIAS_CHANGED", "RUNTIME_VENDOR_CHANGED", "CTYPES_WIDTH_INVALID", "EGL_SYMBOL_MISSING", "EGL_DLOPEN_FAILED",
           "EGL_API_UNAVAILABLE", "RUNTIME_UNAVAILABLE", "REQUEST_INVALID", "CHILD_SOURCE_MISSING"}


class InitLogSummary(EglLogSummary):
    PATTERNS = dict(EglLogSummary.PATTERNS,
                    dri_config_generation_failed=b"dri_fill_in_modes: driCreateConfigs failed",
                    llvmpipe_anonymous_file_failed=b"Failed to create anonymous file for memory allocations",
                    llvmpipe_jit_disallowed=b"llvmpipe: JIT is not permitted in this process",
                    surfaceless_software_fallback=b"Falling back to surfaceless swrast without DRM.")
    INFORMATION = EglLogSummary.INFORMATION | {"surfaceless_software_fallback"}

    def summary(self):
        result = super().summary()
        result["profile"] = "mesa-26.2.6-init-known-v1"
        return result


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def validate_runtime(home, *, expected_uid=UID, pins=LIBRARIES):
    """uid/pins差替えはlocalhost fixtureだけ。CLIへ公開しない。"""
    require(os.getuid() == expected_uid, "UID_MISMATCH")
    directory = home.parent / "usr/lib"
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    checked = {}
    try:
        info = os.fstat(fd)
        require(info.st_uid == expected_uid and not info.st_mode & 0o022, "RUNTIME_DIRECTORY_UNSAFE")
        for name, expected in pins.items():
            target = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(target, "rb") as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_uid == expected_uid and
                        not before.st_mode & 0o022 and 0 < before.st_size <= MAX_LIBRARY, "RUNTIME_LIBRARY_UNSAFE")
                data = stream.read(MAX_LIBRARY + 1)
                after = os.fstat(stream.fileno())
                require(identity(before) == identity(after) and len(data) == after.st_size and
                        hashlib.sha256(data).hexdigest() == expected, "RUNTIME_LIBRARY_CHANGED")
                checked[name] = dict(sha256=expected, bytes=len(data), identity=list(identity(after)))
        for name, target in ALIASES.items():
            require((directory / name).resolve(strict=True) == directory.resolve(strict=True) / target,
                    "RUNTIME_ALIAS_CHANGED")
    finally:
        os.close(fd)
    vendor = home.parent / "usr" / VENDOR
    with os.fdopen(os.open(vendor, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == expected_uid and not info.st_mode & 0o022 and
                0 < info.st_size <= 4096, "RUNTIME_VENDOR_CHANGED")
        data = stream.read(4097)
        require(identity(info) == identity(os.fstat(stream.fileno())) and len(data) == info.st_size and
                json.loads(data, object_pairs_hook=unique_object) == {"file_format_version": "1.0.0", "ICD": {"library_path": "libEGL_mesa.so.0"}},
                "RUNTIME_VENDOR_CHANGED")
        checked["vendor_json"] = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), identity=list(identity(info)))
    return checked


def private_run(home, probe_id):
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (".local", "state", "eidolon", "egl-init"):
            info = os.fstat(fd)
            require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "RUNTIME_DIRECTORY_UNSAFE")
            try: os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd);fd = child
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "RUNTIME_DIRECTORY_UNSAFE")
        os.mkdir(probe_id, mode=0o700, dir_fd=fd)  # 既存runを再利用しない。
    finally:
        os.close(fd)
    directory = home / ".local/state/eidolon/egl-init" / probe_id
    for name in ("home", "config", "cache", "tmp"):
        (directory / name).mkdir(mode=0o700)
    return directory


def fixed_environment(home, directory, variant="baseline"):
    require(variant in VARIANTS, "REQUEST_INVALID")
    prefix = home.parent / "usr"
    result = dict(PATH=str(prefix / "bin") + ":/system/bin", PREFIX=str(prefix), LANG="C.UTF-8",
                HOME=str(directory / "home"), TMPDIR=str(directory / "tmp"),
                XDG_CONFIG_HOME=str(directory / "config"), XDG_CACHE_HOME=str(directory / "cache"),
                LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="softpipe", EGL_LOG_LEVEL="debug",
                __EGL_VENDOR_LIBRARY_FILENAMES=str(prefix / VENDOR))
    if variant == "kopper-disabled": result["LIBGL_KOPPER_DISABLE"] = "1"
    return result


def egl_error(library):
    value = int(library.eglGetError())
    return dict(value=value, name=ERRORS.get(value, "unknown"))


def perform_api(library, emit, after_api=None, before_initialize=None):
    """公開EGLだけ。API結果をflushしてからterminateし、初回errorを保持する。"""
    require(ctypes.sizeof(ctypes.c_int) == ctypes.sizeof(ctypes.c_uint) == 4 and
            ctypes.sizeof(ctypes.c_ssize_t) == ctypes.sizeof(ctypes.c_void_p), "CTYPES_WIDTH_INVALID")
    library.eglGetError.argtypes, library.eglGetError.restype = [], ctypes.c_int
    library.eglQueryString.argtypes, library.eglQueryString.restype = [ctypes.c_void_p, ctypes.c_int], ctypes.c_char_p
    library.eglInitialize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    library.eglInitialize.restype = ctypes.c_uint
    library.eglTerminate.argtypes, library.eglTerminate.restype = [ctypes.c_void_p], ctypes.c_uint
    extensions = library.eglQueryString(None, 0x3055)  # EGL_EXTENSIONS。全文は保存しない。
    query_error = egl_error(library)
    supported = isinstance(extensions, bytes) and len(extensions) <= 65536 and b"EGL_MESA_platform_surfaceless" in extensions.split()
    api = dict(schema=1, platform="surfaceless_mesa", driver="softpipe", supported=supported,
               query_error=query_error, entrypoint="not_run", display=None, initialize=None)
    display = None
    try:
        if supported:
            try:
                get_display = library.eglGetPlatformDisplay
                get_display.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
                get_display.restype, api["entrypoint"] = ctypes.c_void_p, "core"
            except AttributeError:
                library.eglGetProcAddress.argtypes, library.eglGetProcAddress.restype = [ctypes.c_char_p], ctypes.c_void_p
                address = library.eglGetProcAddress(b"eglGetPlatformDisplayEXT")
                require(bool(address), "EGL_SYMBOL_MISSING")
                # EXTのattrib_listはEGLint*。coreのEGLAttrib*とは幅が異なる。
                get_display = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_int))(address)
                api["entrypoint"] = "ext"
            display = get_display(0x31DD, None, None)
            api["display"] = dict(returned=bool(display), error=egl_error(library))
            if display:
                major, minor = ctypes.c_int(), ctypes.c_int()
                if before_initialize is not None:before_initialize()
                initialized = bool(library.eglInitialize(display, ctypes.byref(major), ctypes.byref(minor)))
                error = egl_error(library)  # terminate/他のEGL呼出しより必ず先。
                api["initialize"] = dict(returned=initialized, error=error,
                                         major=major.value if initialized else None,
                                         minor=minor.value if initialized else None)
        emit(dict(phase="api", result=api))
        if after_api is not None:after_api()
    finally:
        if display:
            terminated = bool(library.eglTerminate(display))
            cleanup = dict(called=True, returned=terminated, error=egl_error(library))
        else:
            cleanup = dict(called=False, returned=None, error=None)
        emit(dict(phase="egl_cleanup", result=cleanup))
    return 0 if api["initialize"] and api["initialize"]["returned"] else 1 if supported else 2


def child_main(before_initialize=None):
    emit = lambda value: print(json.dumps(value, separators=(",", ":")), flush=True)
    try:
        home = termux_home(sys.executable)
        before = validate_runtime(home)
        try:
            library = ctypes.CDLL(str(home.parent / "usr/lib/libEGL.so.1.1.0"), mode=os.RTLD_NOW | os.RTLD_LOCAL)
        except OSError:
            raise InstallError("EGL_DLOPEN_FAILED") from None
        status = perform_api(library, emit, lambda:emit(dict(phase="loaded_libraries",result=capture_loaded(home.parent/"usr/lib",before,LIBRARIES))),before_initialize)
        emit(dict(phase="runtime", unchanged=validate_runtime(home) == before))
        return status
    except (OSError, ValueError, AttributeError, TypeError) as error:
        reason = str(error) if isinstance(error, InstallError) and str(error) in BLOCKED else "EGL_API_UNAVAILABLE"
        emit(dict(phase="blocked", reason=reason))
        return 3


def decode_event(line):
    """library stdoutを信頼しない。固定API schema以外は捨て、本文を保存しない。"""
    def error_valid(value):
        return (type(value) is dict and set(value) == {"value", "name"} and type(value["value"]) is int and
                0 <= value["value"] <= 0xFFFFFFFF and value["name"] == ERRORS.get(value["value"], "unknown"))
    def call_valid(value, keys):
        return type(value) is dict and set(value) == keys and type(value["returned"]) is bool and error_valid(value["error"])
    try: value = json.loads(line, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError): raise InstallError("CHILD_OUTPUT_INVALID") from None
    require(type(value) is dict and type(value.get("phase")) is str, "CHILD_OUTPUT_INVALID")
    phase = value["phase"]
    if phase == "api":
        require(set(value) == {"phase", "result"}, "CHILD_OUTPUT_INVALID")
        api = value["result"]
        require(type(api) is dict and set(api) == {"schema", "platform", "driver", "supported", "query_error",
                                                  "entrypoint", "display", "initialize"}, "CHILD_OUTPUT_INVALID")
        require(api["schema"] == 1 and api["platform"] == "surfaceless_mesa" and api["driver"] == "softpipe" and
                type(api["supported"]) is bool and error_valid(api["query_error"]) and
                api["entrypoint"] in {"not_run", "core", "ext"}, "CHILD_OUTPUT_INVALID")
        if api["display"] is not None:
            require(call_valid(api["display"], {"returned", "error"}), "CHILD_OUTPUT_INVALID")
        if api["initialize"] is not None:
            init = api["initialize"]
            require(call_valid(init, {"returned", "error", "major", "minor"}), "CHILD_OUTPUT_INVALID")
            require(all(type(init[k]) is int and 0 <= init[k] < 100 if init["returned"] else init[k] is None
                        for k in ("major", "minor")), "CHILD_OUTPUT_INVALID")
        require((not api["supported"] and api["entrypoint"] == "not_run" and api["display"] is None and api["initialize"] is None)
                or (api["supported"] and api["entrypoint"] in {"core", "ext"} and api["display"] is not None and
                    ((api["display"]["returned"] and api["initialize"] is not None) or
                     (not api["display"]["returned"] and api["initialize"] is None))), "CHILD_OUTPUT_INVALID")
    elif phase == "egl_cleanup":
        cleanup = value.get("result")
        require(set(value) == {"phase", "result"} and type(cleanup) is dict and
                set(cleanup) == {"called", "returned", "error"} and type(cleanup["called"]) is bool and
                ((cleanup["called"] and type(cleanup["returned"]) is bool and error_valid(cleanup["error"])) or
                 (not cleanup["called"] and cleanup["returned"] is None and cleanup["error"] is None)), "CHILD_OUTPUT_INVALID")
    elif phase == "loaded_libraries":
        loaded=value.get("result")
        require(set(value)=={"phase","result"} and type(loaded) is dict and set(loaded)=={"profile","status","reason","libraries"} and
                loaded["profile"]==LOADED_PROFILE and loaded["status"] in {"verified","not_verified"} and
                (loaded["reason"] is None if loaded["status"]=="verified" else loaded["reason"] in LOADED_REASONS) and
                type(loaded["libraries"]) is list and len(loaded["libraries"])<=len(LOADED_NAMES),"CHILD_OUTPUT_INVALID")
        names=[]
        for row in loaded["libraries"]:
            require(type(row) is dict and set(row)=={"name","canonical_path","device_major","device_minor","inode","bytes","sha256","uid","mode"} and
                    row["name"] in LOADED_NAMES and row["name"] not in names and row["canonical_path"] in
                    {"/data/data/com.termux/files/usr/lib/"+row["name"],"/data/user/0/com.termux/files/usr/lib/"+row["name"]} and
                    row["sha256"]==LIBRARIES[row["name"]] and row["uid"]==UID and
                    all(type(row[k]) is int and 0<=row[k]<=2**64-1 for k in ("device_major","device_minor","inode","bytes","mode")) and
                    row["inode"]>0 and 0<row["bytes"]<=MAX_LIBRARY and row["mode"]<=0o7777 and not row["mode"]&0o022,"CHILD_OUTPUT_INVALID")
            names.append(row["name"])
        require(loaded["status"]!="verified" or names==list(LOADED_NAMES),"CHILD_OUTPUT_INVALID")
    elif phase == "runtime":
        require(set(value) == {"phase", "unchanged"} and type(value["unchanged"]) is bool, "CHILD_OUTPUT_INVALID")
    elif phase == "blocked":
        require(set(value) == {"phase", "reason"} and value["reason"] in BLOCKED, "CHILD_OUTPUT_INVALID")
    else:
        raise InstallError("CHILD_OUTPUT_INVALID")
    return value


def group_live(group):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal(): continue
        try:
            fields = (entry / "stat").read_text().rpartition(") ")[2].split()
            if len(fields) >= 3 and int(fields[2]) == group and fields[0] != "Z": return True
        except (OSError, ValueError): pass
    return False


def observe_owned(command, environment, control_fd, checker, emit, *, timeout=TIMEOUT, grace=2.0):
    """command/timeout差替えはlocalhost fixtureのみ。生成したhandle/groupだけを所有する。"""
    selector, blocking = selectors.DefaultSelector(), os.get_blocking(control_fd)
    try:
        selector.register(control_fd, selectors.EVENT_READ, "control");os.set_blocking(control_fd, False)
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=environment, start_new_session=True, close_fds=True)
    except Exception:
        selector.close();os.set_blocking(control_fd, blocking);raise
    logs, output, pending, control = InitLogSummary(True), dict(stdout=0, stderr=0), bytearray(), bytearray()
    events, started, last_check, stop_at = {}, time.monotonic(), 0.0, None
    failure = cancel_reason = None
    term_sent = kill_sent = timed_out = False

    def send(number):
        nonlocal term_sent, kill_sent
        try:
            os.killpg(child.pid, number)
            if number == signal.SIGTERM: term_sent = True
            if number == signal.SIGKILL: kill_sent = True
        except ProcessLookupError: pass

    def stop(reason):
        nonlocal stop_at, cancel_reason
        if cancel_reason is None: cancel_reason = reason
        if stop_at is None:
            stop_at = time.monotonic()
            if group_live(child.pid): send(signal.SIGTERM)

    def read_pipe(key):
        nonlocal failure, pending
        data = os.read(key.fd, 65536)
        if not data: selector.unregister(key.fd);return
        output[key.data] += len(data)
        if key.data == "stderr": logs.feed(data)
        elif output["stdout"] <= MAX_STDOUT:
            pending.extend(data)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n");pending = bytearray(rest)
                try:
                    value = decode_event(line)
                    require(value["phase"] not in events, "CHILD_OUTPUT_INVALID")
                    events[value["phase"]] = value
                except (ValueError, TypeError, KeyError):
                    failure = "CHILD_OUTPUT_INVALID";stop(failure)
        if output["stdout"] > MAX_STDOUT or output["stderr"] > TRACE_LIMIT: stop("OUTPUT_LIMIT")

    try:
        for name in ("stdout", "stderr"):
            pipe = getattr(child, name);os.set_blocking(pipe.fileno(), False);selector.register(pipe, selectors.EVENT_READ, name)
        emit(dict(phase="started", child_pid=child.pid, child_group=child.pid))
        while True:
            now = time.monotonic()
            exited = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
            if exited and not group_live(child.pid): break
            if now - started >= timeout and not timed_out: timed_out = True;stop("TIMEOUT")
            if now - last_check >= .2 and cancel_reason is None:
                last_check = now
                try: checker()
                except (OSError, ValueError): stop("RUNTIME_CHANGED")
            if stop_at is not None and now - stop_at >= grace and group_live(child.pid): send(signal.SIGKILL)
            if stop_at is not None and now - stop_at >= grace + 2: failure = "STOP_TIMEOUT";break
            for key, _ in selector.select(.02):
                if key.data != "control": read_pipe(key);continue
                data = os.read(key.fd, 1024)
                if not data: selector.unregister(key.fd);stop("CONTROL_EOF");continue
                control.extend(data)
                if len(control) > 1024: stop("CONTROL_INVALID")
                elif b"\n" in control:
                    if bytes(control) == b'{"type":"cancel"}\n': stop("HOST_CANCEL")
                    else: stop("CONTROL_INVALID")
    except Exception:
        failure = "OPERATOR_IO_FAILED";stop("OPERATOR_ERROR")
    finally:
        if group_live(child.pid):
            if not term_sent: send(signal.SIGTERM)
            until = time.monotonic() + grace
            while group_live(child.pid) and time.monotonic() < until:
                for key, _ in selector.select(.02):
                    if key.data != "control": read_pipe(key)
                    else: selector.unregister(key.fd)
            if group_live(child.pid): send(signal.SIGKILL)
        until = time.monotonic() + 1
        while group_live(child.pid) and time.monotonic() < until:
            for key, _ in selector.select(.02):
                if key.data != "control": read_pipe(key)
                else: selector.unregister(key.fd)
        reclaimed, reaped = not group_live(child.pid), False
        try: child.wait(timeout=2);reaped = True
        except subprocess.TimeoutExpired: reclaimed = False
        until = time.monotonic() + 1
        while any(k.data != "control" for k in selector.get_map().values()) and time.monotonic() < until:
            for key, _ in selector.select(.02):
                if key.data != "control": read_pipe(key)
                else: selector.unregister(key.fd)
        drained = not any(k.data != "control" for k in selector.get_map().values())
        selector.close();child.stdout.close();child.stderr.close();os.set_blocking(control_fd, blocking)
    if pending: failure = "CHILD_OUTPUT_INVALID"
    summary = logs.summary();summary["complete"] &= drained
    api = events.get("api", {}).get("result")
    expected_rc = (0 if api and api["initialize"] and api["initialize"]["returned"] else
                   1 if api and api["supported"] else 2)
    cleanup = reaped and reclaimed and drained
    diagnosed = (cleanup and cancel_reason is None and failure is None and child.returncode == expected_rc and
                 set(events) == {"api", "loaded_libraries", "egl_cleanup", "runtime"} and events["loaded_libraries"]["result"]["status"]=="verified" and events["runtime"]["unchanged"] and summary["complete"])
    return dict(status="diagnosed" if diagnosed else "failed", api=api, loaded_libraries=events.get("loaded_libraries",{}).get("result"), egl_cleanup=events.get("egl_cleanup", {}).get("result"),
                child_blocked=events.get("blocked", {}).get("reason"), runtime_unchanged=events.get("runtime", {}).get("unchanged"),
                child_returncode=child.returncode, termination="unreaped" if child.returncode is None else
                "signal" if child.returncode < 0 else "natural", term_sent=term_sent, kill_sent=kill_sent,
                reaped=reaped, group_reclaimed=reclaimed, pipes_drained=drained, cleanup_confirmed=cleanup,
                timed_out=timed_out, cancel_reason=cancel_reason, error=failure, output_bytes=output, egl_log=summary)


def remote_main():
    previous, interrupted = {}, {"signal": False}
    os.umask(0o077)
    try:
        request = bytearray()
        while len(request) < 128:
            byte = os.read(0, 1);require(byte, "REQUEST_INVALID")
            if byte == b"\n": break
            request.extend(byte)
        value = json.loads(request)
        require(type(value) is dict and set(value) == {"probe_id", "variant"} and type(value["probe_id"]) is str and
                re.fullmatch(r"[0-9a-f]{32}", value["probe_id"]) is not None and value["variant"] in VARIANTS, "REQUEST_INVALID")
        require(type(CHILD_SOURCE) is str and CHILD_SOURCE, "CHILD_SOURCE_MISSING")
        home = termux_home(sys.executable)
        before = validate_runtime(home)
        directory = private_run(home, value["probe_id"])
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[number] = signal.signal(number, lambda *_: interrupted.__setitem__("signal", True))
        def checker():
            require(not interrupted["signal"] and validate_runtime(home) == before, "RUNTIME_LIBRARY_CHANGED")
        emit = lambda event: print(json.dumps(event), flush=True)
        emit(dict(phase="validated", probe_id=value["probe_id"], uid=UID, libraries={k: v["sha256"] for k, v in before.items()}))
        report = observe_owned([sys.executable, "-c", CHILD_SOURCE], fixed_environment(home, directory, value["variant"]), 0, checker, emit)
        try: checker()
        except (OSError, ValueError): report.update(status="failed", runtime_unchanged=False, error="RUNTIME_LIBRARY_CHANGED")
        report.update(schema=1, operator=OPERATOR, probe_id=value["probe_id"], uid=UID, variant=value["variant"],
                      libraries={k: v["sha256"] for k, v in before.items()}, runtime_origin="libc++30-pinned",
                      wayland="not_run", sdk="not_run", drawing="not_run", input="not_run", hmd="not_run")
        fd = os.open(directory / "report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(report) + b"\n");stream.flush();os.fsync(stream.fileno())
        emit(dict(phase="finished", result=report))
        return 0 if report["status"] == "diagnosed" else 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, InstallError) and str(error) in BLOCKED else "RUNTIME_UNAVAILABLE"
        print(json.dumps(dict(phase="blocked", reason=reason)), flush=True)
        return 2
    finally:
        for number, handler in previous.items(): signal.signal(number, handler)
