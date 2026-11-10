"""C2 normal-v3 remote ownership, private bus, cleanup and Recover evidence."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import pwd
import re
import selectors
import stat
import time
from xml.sax.saxutils import escape as xml_escape

import linux_desktop_v3_contract as C
import linux_desktop_v3_wire as W
from eidolon_linux_persistent_profile import JournalBarrier, ProfileIdentity, storage_owner

STATE_DIR = ".eidolon-desktop-v3"
LEASE_MS = W.LEASE_MS
LEASE_SECONDS = LEASE_MS / 1000.0
OUTPUT_RATE_BYTES_PER_SEC = 1024 * 1024
OUTPUT_BURST_SECONDS = 5.0
OUTPUT_BURST_BYTES = int(OUTPUT_RATE_BYTES_PER_SEC * OUTPUT_BURST_SECONDS)
MAX_STATE_ENTRIES = 128
BUS_TEMPLATE = "linux-desktop-v3-bus.conf.in"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
FIXED_APP = ("/usr/bin/mousepad", "--disable-server")
FIXED_DBUS = "/usr/bin/dbus-daemon"
FIXED_WAYPIPE_ARGS = ("--no-gpu", "--compress", "none", "--threads", "1")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SESSION = re.compile(r"s-[0-9a-f]{16}\Z")
_RECEIPT = re.compile(r"r-[0-9a-f]{16}\.json\Z")


class HelperError(RuntimeError):
    def __init__(self, public_reason="APP_STATE_INVALID", failure_reason="protocol"):
        public = {item.value for item in C.Reason if item is not C.Reason.NONE}
        failures = {item.value for item in C.FailureReason}
        if public_reason not in public:
            public_reason = "APP_STATE_INVALID"
        if failure_reason not in failures:
            failure_reason = "protocol"
        super().__init__(public_reason)
        self.public_reason = public_reason
        self.failure_reason = failure_reason


def require(value, public="APP_STATE_INVALID", failure="protocol"):
    if not value:
        raise HelperError(public, failure)


def canonical(value):
    return C.canonical_json(value)


def boot_id():
    try:
        value = BOOT_ID.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise HelperError("APP_STATE_RECOVERY_REQUIRED", "process_ownership") from None
    require(_UUID.fullmatch(value) is not None and value != "00000000-0000-0000-0000-000000000000",
            "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
    return value


def account_home(uid):
    require(type(uid) is int and uid > 0 and os.getresuid() == (uid,) * 3)
    row = pwd.getpwuid(uid)
    require(row.pw_uid == uid and row.pw_dir.startswith("/") and row.pw_dir != "/")
    return Path(row.pw_dir)


def _id(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_dir(path, uid, mode=None):
    path = Path(path)
    require(path.is_absolute() and all(part not in (".", "..") for part in path.parts))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022)
        if mode is not None:
            require(stat.S_IMODE(info.st_mode) == mode)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _ensure_dir(parent_fd, name, uid, mode=0o700):
    require("/" not in name and name not in ("", ".", ".."))
    try:
        os.mkdir(name, mode, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 dir_fd=parent_fd)
    info = os.fstat(fd)
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and
            stat.S_IMODE(info.st_mode) == mode and _id(info) == _id(named))
    return fd


def _read_at(parent_fd, name, uid, limit=65536):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == uid and before.st_nlink == 1 and
                stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= limit)
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(16384, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        require(len(data) == before.st_size and _id(before) == _id(after) == _id(named))
        return bytes(data)
    finally:
        os.close(fd)


def _write_new(parent_fd, name, raw, uid):
    require(type(raw) is bytes and 0 < len(raw) <= 65536 and "/" not in name)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent_fd)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            require(written > 0)
            view = view[written:]
        os.fsync(fd)
        info = os.fstat(fd)
        require(info.st_uid == uid and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
    finally:
        os.close(fd)
    os.fsync(parent_fd)


def _replace_at(parent_fd, name, raw, uid):
    temp = "." + name + ".tmp"
    try:
        os.unlink(temp, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    _write_new(parent_fd, temp, raw, uid)
    os.rename(temp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)
    require(_read_at(parent_fd, name, uid) == raw)


def _identity_record(identity):
    require(type(identity) is ProfileIdentity, "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
    def one(value):
        return {"device": str(value.device), "inode": str(value.inode)}
    return {"home": one(identity.home), "root": one(identity.root), "lock": one(identity.lock),
            "owner": one(identity.owner),
            "directories": [[name, one(value)] for name, value in identity.directories]}


@dataclass
class SessionPaths:
    home: Path
    state: Path
    session: Path
    runtime: Path
    cache: Path
    tmp: Path
    bus_socket: Path
    wayland_socket: Path
    bus_config: Path


class RemoteSessionBarrier(JournalBarrier):
    """Remote C1 barrier. B2 native journal remains the durable local authority."""
    def __init__(self, context, owner, paths, state_fd, session_fd, uid, origin_boot):
        super().__init__(context, owner)
        self.paths = paths
        self.state_fd = state_fd
        self.session_fd = session_fd
        self.uid = uid
        self.origin_boot = origin_boot
        self.phase = "reserved"
        self._record = None

    def _value(self, phase, identity=None, evidence=None):
        value = {"schema": 1, "protocol_revision": C.REVISION, "scope": self.context.scope(),
                 "nonce": self.context.record()["nonce"], "boot_id": self.origin_boot,
                 "owner_sha256": self.owner.sha256(), "phase": phase,
                 "profile_identity": None, "release": None}
        if identity is not None:
            value["profile_identity"] = _identity_record(identity)
        if evidence is not None:
            value["release"] = {"helper_pid": str(evidence.helper_pid),
                                "helper_start": str(evidence.helper_start),
                                "roots": evidence.roots, "pipes": evidence.pipes}
        return canonical(value) + b"\n"

    def before_acquire(self):
        require(self.phase == "reserved", "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
        raw = self._value("reserved")
        _write_new(self.session_fd, "barrier.json", raw, self.uid)
        self._record = raw

    def after_acquire(self, identity):
        require(self.phase == "reserved" and
                _read_at(self.session_fd, "barrier.json", self.uid) == self._record,
                "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
        raw = self._value("acquired", identity=identity)
        _replace_at(self.session_fd, "barrier.json", raw, self.uid)
        self._record = raw
        self.phase = "acquired"

    def before_release(self, evidence):
        require(self.phase == "acquired" and
                _read_at(self.session_fd, "barrier.json", self.uid) == self._record,
                "APP_STATE_RECOVERY_REQUIRED", "receipt_write")
        raw = self._value("release_authorized", identity=evidence.identity, evidence=evidence)
        _replace_at(self.session_fd, "barrier.json", raw, self.uid)
        self._record = raw
        self.phase = "release_authorized"


def prepare_session(context):
    value = context.record()
    uid = value["uid"]
    home = account_home(uid)
    home_fd = _open_dir(home, uid)
    state_fd = None
    session_fd = None
    try:
        state_fd = _ensure_dir(home_fd, STATE_DIR, uid)
        count = 0
        with os.scandir(state_fd) as entries:
            for entry in entries:
                count += 1
                require(count <= MAX_STATE_ENTRIES, "STATE_LIMIT", "directory_cleanup")
                info = entry.stat(follow_symlinks=False)
                require(info.st_uid == uid and not stat.S_ISLNK(info.st_mode),
                        "APP_STATE_INVALID", "directory_cleanup")
                if _SESSION.fullmatch(entry.name):
                    raise HelperError("APP_STATE_RECOVERY_REQUIRED", "process_ownership")
                require(_RECEIPT.fullmatch(entry.name) is not None and
                        stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and
                        info.st_nlink == 1, "APP_STATE_INVALID", "directory_cleanup")
        name = "s-" + value["nonce"]
        os.mkdir(name, 0o700, dir_fd=state_fd)
        os.fsync(state_fd)
        session_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=state_fd)
        for child_name in ("runtime", "cache", "tmp"):
            child = _ensure_dir(session_fd, child_name, uid)
            os.close(child)
        base = home / STATE_DIR / name
        paths = SessionPaths(home, home / STATE_DIR, base, base / "runtime", base / "cache",
                             base / "tmp", base / "b", base / "p", base / "bus.conf")
        require(W.valid_socket(str(paths.wayland_socket), value["nonce"]),
                "APP_STATE_INVALID", "protocol")
        barrier = RemoteSessionBarrier(context, storage_owner(context), paths, state_fd, session_fd,
                                       uid, boot_id())
        return paths, barrier, home_fd, state_fd, session_fd
    except BaseException:
        if session_fd is not None:
            os.close(session_fd)
        if state_fd is not None:
            os.close(state_fd)
        os.close(home_fd)
        raise


def _safe_template(bundle):
    raw = bundle.files[BUS_TEMPLATE]
    require(type(raw) is bytes and 1 <= len(raw) <= 16384 and
            raw.count(b"@EIDOLON_BUS_SOCKET@") == 1,
            "APP_DEPENDENCY_CHANGED", "tool_inspection")
    return raw


_DBUS_OPTIONAL = frozenset(b"-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_/.*")


def _dbus_escape_path(path):
    raw = os.fsencode(path)
    require(raw and b"\x00" not in raw, "APP_STATE_INVALID", "tool_inspection")
    return "".join(chr(byte) if byte in _DBUS_OPTIONAL else f"%{byte:02X}" for byte in raw)


def _validate_bus_address(address, socket_path):
    require(type(address) is str and "\n" not in address and "\r" not in address and ";" not in address,
            "APP_STATE_INVALID", "tool_inspection")
    expected = "unix:path=" + _dbus_escape_path(socket_path)
    if address == expected:
        return address
    prefix = expected + ",guid="
    require(address.startswith(prefix) and re.fullmatch(r"[0-9a-f]{32}", address[len(prefix):]) is not None,
            "APP_STATE_INVALID", "tool_inspection")
    return address


def render_bus_config(bundle, paths, session_fd, uid):
    socket_path = str(paths.bus_socket)
    require(len(os.fsencode(socket_path)) <= 100 and "\x00" not in socket_path,
            "APP_STATE_INVALID", "tool_inspection")
    escaped = _dbus_escape_path(socket_path)
    raw = _safe_template(bundle).replace(b"@EIDOLON_BUS_SOCKET@",
                                         xml_escape(escaped).encode("ascii"))
    _write_new(session_fd, "bus.conf", raw, uid)
    return paths.bus_config


def launch_environment(profile, paths, bundle, bus_address=None):
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           **profile.environment(), "XDG_CACHE_HOME": str(paths.cache), "TMPDIR": str(paths.tmp),
           "XDG_RUNTIME_DIR": str(paths.runtime), "GDK_BACKEND": "wayland",
           "GDK_GL": "disable", "GDK_RENDERING": "image", "GIO_USE_VFS": "local",
           "GTK_USE_PORTAL": "0", "NO_AT_BRIDGE": "1", "GTK_IM_MODULE": "gtk-im-context-simple",
           "GSETTINGS_BACKEND": "eidolon-keyfile-v1",
           "GIO_EXTRA_MODULES": str(bundle.directory / "gio")}
    if bus_address is not None:
        env["DBUS_SESSION_BUS_ADDRESS"] = bus_address
    return env


class Drain:
    """Continuously drain output with a per-stream 1 MiB/s token bucket.

    Each stream may burst up to five seconds of allowance, but total bytes are
    intentionally unbounded so a long-lived GUI cannot fail merely because its
    lifetime output eventually exceeds a cumulative threshold.
    """
    def __init__(self, clock=None):
        self.selector = selectors.DefaultSelector()
        self.clock = time.monotonic if clock is None else clock
        self.streams = set()
        self.eof = set()
        self.rate = {}

    def add(self, child):
        now = self.clock()
        for stream in (child.stdout, child.stderr):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ)
            self.streams.add(stream)
            self.rate[stream] = (float(OUTPUT_BURST_BYTES), now)

    def _charge(self, stream, size):
        require(stream in self.rate and type(size) is int and size >= 0,
                "APP_STATE_INVALID", "pipe_eof")
        now = self.clock()
        tokens, last = self.rate[stream]
        require(now >= last, "APP_STATE_INVALID", "pipe_eof")
        tokens = min(float(OUTPUT_BURST_BYTES),
                     tokens + (now - last) * OUTPUT_RATE_BYTES_PER_SEC)
        require(size <= tokens, "APP_OUTPUT_RATE_LIMIT", "pipe_eof")
        self.rate[stream] = (tokens - size, now)

    def pump(self, timeout=0):
        for key, _ in self.selector.select(timeout):
            try:
                data = os.read(key.fd, 16384)
            except BlockingIOError:
                continue
            if data:
                self._charge(key.fileobj, len(data))
            else:
                try:
                    self.selector.unregister(key.fileobj)
                except KeyError:
                    pass
                self.eof.add(key.fileobj)
        return len(self.eof) == len(self.streams)

    def close(self):
        self.selector.close()


def spawn_tracked(owner, profile, drain, command, env, cwd=None):
    child = owner.spawn(command, env=env, cwd=cwd)
    profile.track_process(child)
    drain.add(child)
    return child


def _wait_line(child, drain, deadline):
    data = bytearray()
    os.set_blocking(child.stdout.fileno(), False)
    while time.monotonic() < deadline:
        try:
            chunk = os.read(child.stdout.fileno(), 4096)
        except BlockingIOError:
            chunk = b""
        if chunk:
            data.extend(chunk)
            require(len(data) <= 4096, "APP_STATE_INVALID", "tool_inspection")
            if b"\n" in data:
                line, rest = data.split(b"\n", 1)
                require(not rest, "APP_STATE_INVALID", "tool_inspection")
                return line.decode("ascii")
        elif child.poll() is not None:
            break
        drain.pump(.01)
    raise HelperError("APP_STATE_INVALID", "tool_inspection")


def start_private_bus(owner, profile, drain, env, config, socket_path):
    child = owner.spawn([FIXED_DBUS, "--nofork", "--nopidfile", "--print-address=1",
                         "--config-file=" + str(config)], env=env)
    profile.track_process(child)
    address = _wait_line(child, drain, time.monotonic() + 3)
    drain.add(child)
    require(child.poll() is None, "APP_STATE_INVALID", "tool_inspection")
    return child, _validate_bus_address(address, socket_path)


def run_settings_probe(owner, profile, drain, env, bundle, synthetic=True):
    probe_env = dict(env)
    if synthetic:
        root = Path(env["XDG_RUNTIME_DIR"]) / "probe"
        root.mkdir(mode=0o700)
        (root / "config").mkdir(mode=0o700)
        marker = root / "settings-probe-v1"
        marker.write_bytes(b"eidolon-settings-probe-v1\n")
        marker.chmod(0o600)
        probe_env["XDG_CONFIG_HOME"] = str(root / "config")
    child = spawn_tracked(owner, profile, drain,
                          [str(bundle.directory / "linux-settings-probe"), "--check"], probe_env)
    deadline = time.monotonic() + 5
    while child.poll() is None and time.monotonic() < deadline:
        drain.pump(.02)
    require(child.poll() == 0, "APP_SETTINGS_UNVERIFIED", "tool_inspection")
    while not drain.pump(.01) and time.monotonic() < deadline:
        pass
    require(child.poll() == 0, "APP_SETTINGS_UNVERIFIED", "tool_inspection")


def launch_mousepad(owner, profile, drain, env, paths):
    try:
        forwarded = paths.wayland_socket.lstat()
    except FileNotFoundError:
        raise HelperError("APP_STATE_INVALID", "start") from None
    require(stat.S_ISSOCK(forwarded.st_mode) and forwarded.st_uid == os.getuid(),
            "APP_STATE_INVALID", "start")
    waypipe = paths.home / ".local/lib/eidolon-linux/waypipe"
    command = [str(waypipe), *FIXED_WAYPIPE_ARGS, "--socket", str(paths.wayland_socket),
               "--display", str(paths.runtime / "w"), "server", "--", *FIXED_APP]
    return spawn_tracked(owner, profile, drain, command, env, cwd=str(profile.paths.documents))


def cleanup_owner(owner, drain, profile, deadline_seconds=5.0):
    deadline = time.monotonic() + deadline_seconds
    report = None
    while time.monotonic() < deadline:
        drain.pump(.01)
        report = owner.finish(timeout=.12, grace=.03)
        drain.pump(.01)
        if report["complete"]:
            while time.monotonic() < deadline and not drain.pump(.01):
                pass
            break
    require(report is not None and report["complete"] is True,
            "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
    require(drain.pump(0), "APP_STATE_RECOVERY_REQUIRED", "pipe_eof")
    evidence = profile.release()
    return report, evidence


def _remove_tree_at(parent_fd, name, uid, budget, depth=0):
    require(depth <= 8 and budget[0] > 0, "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
    budget[0] -= 1
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    require(info.st_uid == uid and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode),
            "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
    if stat.S_ISDIR(info.st_mode):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            require(_id(before) == _id(info), "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
            with os.scandir(fd) as entries:
                names = []
                for entry in entries:
                    require(len(names) < 512, "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
                    names.append(entry.name)
            for child in names:
                _remove_tree_at(fd, child, uid, budget, depth + 1)
            after = os.fstat(fd)
            require((before.st_dev, before.st_ino, before.st_mode, before.st_uid) ==
                    (after.st_dev, after.st_ino, after.st_mode, after.st_uid),
                    "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent_fd)
        return
    require((stat.S_ISREG(info.st_mode) and info.st_nlink == 1) or
            stat.S_ISSOCK(info.st_mode) or stat.S_ISFIFO(info.st_mode),
            "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
    require(info.st_size <= budget[1], "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
    budget[1] -= info.st_size
    os.unlink(name, dir_fd=parent_fd)


def _remove_session(paths, fds):
    home_fd, state_fd, session_fd = fds
    uid = os.getuid()
    allowed = {"runtime", "cache", "tmp", "bus.conf", "barrier.json", "b", "p"}
    with os.scandir(session_fd) as entries:
        names = {entry.name for entry in entries}
    require(names <= allowed, "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
    budget = [1024, 32 * 1024 * 1024]
    for name in sorted(names):
        _remove_tree_at(session_fd, name, uid, budget)
    os.fsync(session_fd)
    os.close(session_fd)
    os.rmdir(paths.session.name, dir_fd=state_fd)
    os.fsync(state_fd)
    return home_fd, state_fd


def receipt(binding, source_hashes, expected_boot, origin_boot, current_boot, proof, status,
            process_complete, pipes_eof, remote_cleaned, receipt_written):
    return {"schema": 1, "protocol_revision": C.REVISION, "context": binding.context.record(),
            "pins": binding.pins.record(), "build_id": binding.pins.build_id(),
            "source_hashes": source_hashes, "expected_boot_id": expected_boot,
            "origin_boot_id": origin_boot, "boot_id": current_boot, "status": status,
            "process_evidence": proof, "process_complete": process_complete,
            "pipes_eof": pipes_eof, "remote_cleaned": remote_cleaned,
            "receipt_written": receipt_written}


def write_final_receipt(state_fd, nonce, value, binding, source_hashes, expected_boot="-"):
    raw = canonical(value) + b"\n"
    W.parse_receipt(raw, binding, source_hashes, expected_boot,
                    0 if value["status"] == "complete" else 1)
    _write_new(state_fd, "r-" + nonce + ".json", raw, os.getuid())
    return raw


def _remote_barrier_origin(session_fd, binding, uid):
    raw = _read_at(session_fd, "barrier.json", uid)
    require(raw.endswith(b"\n") and b"\n" not in raw[:-1],
            "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
    value = C.parse_json(raw[:-1])
    fields = {"schema", "protocol_revision", "scope", "nonce", "boot_id", "owner_sha256",
              "phase", "profile_identity", "release"}
    require(type(value) is dict and set(value) == fields and type(value["schema"]) is int and
            value["schema"] == 1 and value["protocol_revision"] == C.REVISION and
            value["scope"] == binding.context.scope() and
            value["nonce"] == binding.context.record()["nonce"] and
            value["owner_sha256"] == storage_owner(binding.context).sha256() and
            _UUID.fullmatch(value["boot_id"]) is not None and
            value["phase"] in {"reserved", "acquired", "release_authorized"},
            "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
    return value["boot_id"]


def _recover_remove_session(state_fd, session_name, uid):
    sfd = os.open(session_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                  dir_fd=state_fd)
    try:
        info = os.fstat(sfd)
        named = os.stat(session_name, dir_fd=state_fd, follow_symlinks=False)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and
                stat.S_IMODE(info.st_mode) == 0o700 and _id(info) == _id(named),
                "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
        allowed = {"runtime", "cache", "tmp", "bus.conf", "barrier.json", "b", "p"}
        with os.scandir(sfd) as entries:
            names = {entry.name for entry in entries}
        require(names <= allowed, "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
        budget = [1024, 32 * 1024 * 1024]
        for name in sorted(names):
            _remove_tree_at(sfd, name, uid, budget)
        os.fsync(sfd)
    finally:
        os.close(sfd)
    os.rmdir(session_name, dir_fd=state_fd)
    os.fsync(state_fd)


def recovery_receipt(binding, source_hashes, expected_boot):
    uid = binding.context.record()["uid"]
    home = account_home(uid)
    current = boot_id()
    home_fd = _open_dir(home, uid)
    state_fd = None
    try:
        try:
            state_fd = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                               dir_fd=home_fd)
        except FileNotFoundError:
            value = receipt(binding, source_hashes, expected_boot, None, current, "unknown", "pending",
                            False, False, False, False)
            raw = canonical(value) + b"\n"
            W.parse_receipt(raw, binding, source_hashes, expected_boot, 1)
            return raw, 1
        info = os.fstat(state_fd)
        named = os.stat(STATE_DIR, dir_fd=home_fd, follow_symlinks=False)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and
                stat.S_IMODE(info.st_mode) == 0o700 and _id(info) == _id(named),
                "APP_STATE_RECOVERY_REQUIRED", "directory_cleanup")
        nonce = binding.context.record()["nonce"]
        try:
            saved = W.parse_receipt(_read_at(state_fd, "r-" + nonce + ".json", uid),
                                    binding, source_hashes, "-", 0)
            origin = saved["origin_boot_id"]
            require(origin is not None and (expected_boot == "-" or origin == expected_boot),
                    "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
            value = receipt(binding, source_hashes, expected_boot, origin, current, "receipt",
                            "complete", True, True, True, True)
            raw = canonical(value) + b"\n"
            W.parse_receipt(raw, binding, source_hashes, expected_boot, 0)
            return raw, 0
        except FileNotFoundError:
            pass
        session_name = "s-" + nonce
        origin = None
        try:
            sfd = os.open(session_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=state_fd)
        except FileNotFoundError:
            sfd = None
        if sfd is not None:
            try:
                origin = _remote_barrier_origin(sfd, binding, uid)
            finally:
                os.close(sfd)
        require(expected_boot == "-" or origin is None or origin == expected_boot,
                "APP_STATE_RECOVERY_REQUIRED", "process_ownership")
        if origin is not None and origin != current:
            _recover_remove_session(state_fd, session_name, uid)
            value = receipt(binding, source_hashes, expected_boot, origin, current, "boot_changed",
                            "complete", True, False, True, True)
            raw = canonical(value) + b"\n"
            W.parse_receipt(raw, binding, source_hashes, expected_boot, 0)
            _write_new(state_fd, "r-" + nonce + ".json", raw, uid)
            return raw, 0
        value = receipt(binding, source_hashes, expected_boot, origin, current, "unknown",
                        "pending", False, False, False, False)
        raw = canonical(value) + b"\n"
        W.parse_receipt(raw, binding, source_hashes, expected_boot, 1)
        return raw, 1
    except (C.ContractError, OSError, HelperError):
        value = receipt(binding, source_hashes, expected_boot, None, current, "unknown",
                        "pending", False, False, False, False)
        raw = canonical(value) + b"\n"
        W.parse_receipt(raw, binding, source_hashes, expected_boot, 1)
        return raw, 1
    finally:
        if state_fd is not None:
            os.close(state_fd)
        os.close(home_fd)
