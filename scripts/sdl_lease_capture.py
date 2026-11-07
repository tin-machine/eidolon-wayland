"""現行APK bindingとTermux leaseの読取り専用照合。環境は2値だけ保持する。"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

MAX_SNAPSHOT = 1048576
MAX_ENV = 65536


class CaptureError(ValueError):
    def __init__(self, reason, status="blocked", details=None):
        super().__init__(reason)
        self.reason, self.status, self.details = reason, status, details or {}


def need(value, reason):
    if not value:
        raise CaptureError(reason)


def integer(value, positive=False):
    return type(value) is int and (0 < value <= 2147483647 if positive else value >= 0)


def decimal(value):
    return type(value) is str and re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None and int(value) <= 9223372036854775807


def hexadecimal(value, length):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def identity(value, leader=False):
    need(type(value) is dict and integer(value.get("pid"), True) and decimal(value.get("start")) and
         integer(value.get("group"), True), "PROCESS_IDENTITY_INVALID")
    if leader:
        need(value["pid"] == value["group"], "PROCESS_IDENTITY_INVALID")
    return {key: value[key] for key in ("pid", "start", "group")}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "DUPLICATE_FIELD")
        result[key] = value
    return result


def decode(raw, limit=MAX_SNAPSHOT):
    need(0 < len(raw) <= limit, "JSON_SIZE_INVALID")
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as failure:
        raise CaptureError("JSON_INVALID") from failure


def select_snapshot(value, source_digest, uid):
    """DiagnosticsStore→JNI envelope→Runtimeの固定schemaからidentityだけを選ぶ。"""
    need(type(value) is dict and type(value.get("schema")) is int and value["schema"] == 1 and
         decimal(value.get("sequence")) and integer(value.get("process_id"), True), "DIAGNOSTICS_SCHEMA_INVALID")
    build = value.get("build_info", {})
    need(type(build) is dict and type(build.get("schema")) is int and build["schema"] == 1 and
         build.get("source_digest") == source_digest and hexadecimal(source_digest, 64) and
         hexadecimal(build.get("source_commit"), 40) and type(build.get("source_dirty")) is bool and
         build.get("profile") == "debug" and build.get("target") == "aarch64-linux-android" and
         integer(build.get("apk_version_code"), True), "SOURCE_BUILD_MISMATCH")
    envelope = value.get("runtime", {})
    need(type(envelope) is dict and type(envelope.get("schema")) is int and envelope["schema"] == 1 and
         type(envelope.get("code")) is int and envelope["code"] == 0 and decimal(envelope.get("handle")), "NATIVE_SCHEMA_INVALID")
    runtime = envelope.get("runtime", {})
    need(type(runtime) is dict and type(runtime.get("schema")) is int and runtime["schema"] == 1 and
         runtime.get("state") == "Ready", "RUNTIME_NOT_READY")
    app, transport = runtime.get("app"), runtime.get("transport")
    need(type(app) is dict and type(transport) is dict and app.get("state") == "running" and
         app.get("error") is None and app.get("mode") in {"foot", "foot_fixture"} and
         decimal(app.get("session")) and hexadecimal(app.get("nonce"), 16) and
         transport.get("state") == "Ready" and transport.get("session") == app["session"], "APP_BINDING_INVALID")
    forward = transport.get("app_forward", {})
    remote = forward.get("remote", {}) if type(forward) is dict else {}
    need(type(forward) is dict and forward.get("state") == "running" and forward.get("nonce") == app["nonce"] and
         type(remote) is dict and remote.get("phase") == "running" and remote.get("error") is None and
         type(remote.get("tools")) is dict and type(remote["tools"].get("uid")) is int and
         remote["tools"]["uid"] == uid, "REMOTE_OWNER_INVALID")
    child = identity(remote.get("child"), leader=True)
    journal = transport.get("pending_cleanup")
    need(type(journal) is list and len(journal) <= 8, "JOURNAL_INVALID")
    active, unrelated = 0, 0
    for entry in journal:
        need(type(entry) is dict and hexadecimal(entry.get("nonce"), 16) and decimal(entry.get("session")) and
             integer(entry.get("uid")), "JOURNAL_INVALID")
        matches = (entry["nonce"], entry["session"], entry["uid"]) == (app["nonce"], app["session"], uid)
        if matches:
            need(identity(entry.get("child"), leader=True) == child, "ACTIVE_JOURNAL_CHILD_MISMATCH")
            active += 1
        else:
            unrelated += 1
    summary = dict(active=active, unrelated=unrelated)
    need(active == 1, "ACTIVE_JOURNAL_MISSING_OR_AMBIGUOUS")
    if unrelated:
        raise CaptureError("UNRELATED_CLEANUP_RECORDS", details=dict(journal=summary))
    workload = remote.get("workload")
    if type(workload) is not dict or workload.get("capability") != "available" or not workload.get("registrations"):
        raise CaptureError("REGISTRATION_MISSING_OR_DISABLED", "missing", dict(journal=summary))
    need(type(workload.get("schema")) is int and workload["schema"] == 1 and workload.get("uid") == uid and
         workload.get("session") == app["session"] and workload.get("nonce") == app["nonce"] and
         hexadecimal(workload.get("epoch"), 32) and decimal(workload.get("sequence")) and
         integer(workload.get("sampled_monotonic_ms")) and type(workload.get("boot")) is str and
         re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", workload["boot"]) is not None,
         "WORKLOAD_BINDING_INVALID")
    registrations = workload["registrations"]
    need(type(registrations) is list and len(registrations) == 1, "REGISTRATION_AMBIGUOUS")
    registration = registrations[0]
    need(type(registration) is dict and decimal(registration.get("terminal_id")) and
         integer(registration.get("pid"), True) and decimal(registration.get("start")) and
         type(registration.get("tty")) is str and re.fullmatch(r"/dev/pts/[0-9]+", registration["tty"]) is not None,
         "REGISTRATION_INVALID")
    return dict(schema=1, process_id=value["process_id"], diagnostics_sequence=value["sequence"], handle=envelope["handle"],
                source_digest=source_digest, apk_version=build["apk_version_code"], session=app["session"], nonce=app["nonce"], uid=uid,
                child=child, registration={key: registration[key] for key in ("terminal_id", "pid", "start", "tty")},
                epoch=workload["epoch"], sample_sequence=workload["sequence"], sampled_monotonic_ms=workload["sampled_monotonic_ms"],
                boot=workload["boot"], journal=summary)


def binding_key(value):
    return {key: item for key, item in value.items() if key not in {"diagnostics_sequence", "sample_sequence", "sampled_monotonic_ms"}}


def verify_following(before, after):
    need(binding_key(before) == binding_key(after) and int(after["diagnostics_sequence"]) > int(before["diagnostics_sequence"]) and
         int(after["sample_sequence"]) >= int(before["sample_sequence"]), "APK_BINDING_CHANGED_OR_STALE")


def read_all(fd, limit, reader=os.read):
    result = bytearray()
    while len(result) <= limit:
        data = reader(fd, min(4096, limit + 1 - len(result)))
        if not data:
            break
        result.extend(data)
    need(len(result) <= limit, "READ_LIMIT")
    return bytes(result)


def read_at(directory_fd, name, limit, uid=None, private=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and (uid is None or info.st_uid == uid) and
             (not private or stat.S_IMODE(info.st_mode) == 0o600), "READ_OWNER_OR_MODE_INVALID")
        return read_all(fd, limit)
    finally:
        os.close(fd)


def proc_identity(directory_fd, pid):
    raw = read_at(directory_fd, "stat", 8192)
    try:
        text = raw.decode("utf-8")
        fields = text[text.rindex(")") + 2:].split()
        need(int(text.split(" ", 1)[0]) == pid and len(fields) >= 22 and fields[0] not in {"Z", "X", "x"}, "PROCESS_CHANGED")
        return dict(pid=pid, start=fields[19], group=int(fields[2]))
    except (UnicodeError, ValueError, IndexError) as failure:
        if isinstance(failure, CaptureError):
            raise
        raise CaptureError("PROC_STAT_INVALID") from failure


def check_private_directory(fd, uid):
    info = os.fstat(fd)
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700, "LEASE_DIRECTORY_UNSAFE")


def selected_environment(raw):
    result = {}
    for field in raw.split(b"\0"):
        key, separator, value = field.partition(b"=")
        if separator and key in {b"WAYLAND_DISPLAY", b"XDG_RUNTIME_DIR"}:
            name = key.decode("ascii")
            need(name not in result and value, "ENVIRONMENT_INVALID")
            try:
                result[name] = value.decode("utf-8", "strict")
            except UnicodeError as failure:
                raise CaptureError("ENVIRONMENT_INVALID") from failure
    need(set(result) == {"WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"}, "ENVIRONMENT_MISSING")
    return result


def capture_remote(request, home, proc=Path("/proc"), clock=time.monotonic):
    uid = request["uid"]
    need(type(uid) is int and uid == os.getuid(), "UID_MISMATCH")
    need(hexadecimal(request["nonce"], 16) and decimal(request["session"]), "REQUEST_INVALID")
    age = int(clock() * 1000) - request["sampled_monotonic_ms"]
    need(0 <= age <= 5000, "LIVE_SAMPLE_EXPIRED")
    with (proc / "sys/kernel/random/boot_id").open("rb") as stream:
        need(stream.read(66).decode("ascii").strip() == request["boot"], "BOOT_CHANGED")
    directory = home / ".eidolon" / ("s-" + request["nonce"])
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        home_fd = os.open(home, flags); descriptors.append(home_fd); check_private_directory(home_fd, uid)
        root_fd = os.open(".eidolon", flags, dir_fd=home_fd); descriptors.append(root_fd); check_private_directory(root_fd, uid)
        lease_fd = os.open("s-" + request["nonce"], flags, dir_fd=root_fd); descriptors.append(lease_fd); check_private_directory(lease_fd, uid)
        owner = decode(read_at(lease_fd, "owner.json", 1024, uid, True), 1024)
        marker = dict(schema=1, nonce=request["nonce"], uid=uid)
        need(owner == marker and type(owner["schema"]) is int, "OWNER_MARKER_MISMATCH")
        lease_raw = read_at(lease_fd, "lease.json", 16384, uid, True)
        lease = decode(lease_raw, 16384)
        need(type(lease) is dict and all(lease.get(key) == value for key, value in marker.items()) and type(lease.get("schema")) is int and
             identity(lease.get("child"), True) == request["child"], "LEASE_CHILD_MISMATCH")
        identities = dict(owner=identity(lease.get("owner")), child=request["child"],
                          root={key: request["registration"][key] for key in ("pid", "start")})
        opened = {}
        for kind, expected in identities.items():
            fd = os.open(proc / str(expected["pid"]), flags); descriptors.append(fd)
            need(os.fstat(fd).st_uid == uid, "PROC_UID_MISMATCH")
            actual = proc_identity(fd, expected["pid"])
            need(all(actual[key] == value for key, value in expected.items()), "PROCESS_CHANGED")
            opened[kind] = fd
        environment = selected_environment(read_at(opened["root"], "environ", MAX_ENV, uid))
        need(Path(environment["XDG_RUNTIME_DIR"]).is_absolute() and
             Path(environment["XDG_RUNTIME_DIR"]).resolve() == directory.resolve(), "ENVIRONMENT_LEASE_MISMATCH")
        display = Path(environment["WAYLAND_DISPLAY"])
        display = display if display.is_absolute() else Path(environment["XDG_RUNTIME_DIR"]) / display
        need(display.resolve() == (directory / "w").resolve(), "ENVIRONMENT_LEASE_MISMATCH")
        socket_before = os.stat("w", dir_fd=lease_fd, follow_symlinks=False)
        need(stat.S_ISSOCK(socket_before.st_mode) and socket_before.st_uid == uid, "WAYLAND_SOCKET_INVALID")
        for kind, expected in identities.items():
            actual = proc_identity(opened[kind], expected["pid"])
            need(all(actual[key] == value for key, value in expected.items()), "PROCESS_CHANGED")
        need(read_at(lease_fd, "lease.json", 16384, uid, True) == lease_raw, "LEASE_CHANGED")
        socket_after = os.stat("w", dir_fd=lease_fd, follow_symlinks=False)
        need((socket_before.st_dev, socket_before.st_ino, socket_before.st_uid) ==
             (socket_after.st_dev, socket_after.st_ino, socket_after.st_uid), "WAYLAND_SOCKET_CHANGED")
        need(int(clock() * 1000) - request["sampled_monotonic_ms"] <= 5000, "LIVE_SAMPLE_EXPIRED")
        return dict(status="captured", uid=uid, nonce=request["nonce"], session=request["session"], identities=identities,
                    environment=environment, binding_sha256=hashlib.sha256(canonical(request)).hexdigest())
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def remote_main():
    from sdl_probe_install import termux_home
    try:
        request = decode(sys.stdin.buffer.read(16385), 16384)
        print(json.dumps(capture_remote(request, termux_home(sys.executable))))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as failure:
        report = dict(status="blocked", reason=failure.reason if isinstance(failure, CaptureError) else "REMOTE_READ_UNAVAILABLE")
        print(json.dumps(report))
        return 2


if __name__ == "__main__":
    raise SystemExit(remote_main())
