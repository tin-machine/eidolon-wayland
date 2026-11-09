"""Explicit A0 Waypipe/Mousepad run with a dedicated live pidfd owner.

run_case is for a new, single-threaded non-root check process, after fixed builds
have finished. It never launches in the caller's desktop/profile or invokes a
shell. Output is counted/drained, never retained. No result is a full A0 pass.
"""
from __future__ import annotations

import hashlib
import configparser
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import select
import selectors
import signal
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]

# Stable contract consumed by the A0 gate. Values describe observed behavior;
# no backend API assertion or launch argument can stand in for GUI evidence.
REQUIRED_CHECKS = (
    "private_bus", "independent_instance", "first_main", "expected_app_id",
    "transient_dialogs", "open_edit_save_as", "clean_close_restart",
    "gui_setting_persistence", "protected_settings_gui_readonly",
)
PENDING_CHECKS = (
    "independent_instance", "clean_close_restart", "gui_setting_persistence", "protected_settings_gui_readonly",
)


def _module(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


driver = _module("l7a_wayland_driver", ROOT / "scripts/fixtures/linux-real-app-wayland/driver.py")
owner_api = _module("l7a_process_owner", ROOT / "scripts/linux/eidolon_linux_process_owner.py")
inspect_api = _module("l7a_recovery_inspect", ROOT / "scripts/linux_real_app_inspect.py")


class WaylandError(ValueError):
    pass


class CleanupPending(RuntimeError):
    """Caller must retain this live run and invoke retry_cleanup; never pass."""
    def __init__(self, run):
        super().__init__("APP_WAYLAND_CLEANUP_PENDING")
        self.run = run


class _TerminationGuard:
    """Latch requests without interrupting child adoption or cleanup.

    This dedicated process installs the guard before creating its owner. The
    live run retains it across CleanupPending; only completed cleanup restores
    the previous handlers. A Python signal-handler exception could otherwise
    interrupt Popen before ProcessOwner has registered the child.
    """
    def __init__(self):
        self.requested = None
        self.previous = {}
        try:
            for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                self.previous[number] = signal.signal(number, self._request)
                parent = getattr(self.previous[number], "__self__", None)
                if isinstance(parent, _TerminationGuard) and parent.requested is not None:
                    # A worker-level request latched between sequential runs
                    # cannot be hidden by installing a fresh run-level guard.
                    self._request(parent.requested, None)
        except BaseException:
            self.restore()
            raise

    def _request(self, number, _frame):
        if self.requested is None:
            self.requested = number

    def check(self):
        require(self.requested is None, "APP_WAYLAND_INTERRUPTED")

    def restore(self):
        for number, handler in self.previous.items():
            parent = getattr(handler, "__self__", None)
            if isinstance(parent, _TerminationGuard) and self.requested is not None:
                parent._request(self.requested, None)
            signal.signal(number, handler)
        self.previous.clear()


def require(value, reason):
    if not value:
        raise WaylandError(reason)


def _private(path):
    require(path.is_absolute(), "APP_WAYLAND_PRIVATE_DIRECTORY")
    fd = inspect_api._open_absolute_directory(path)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700, "APP_WAYLAND_PRIVATE_DIRECTORY")
    finally:
        os.close(fd)


def _json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536,
            "APP_WAYLAND_METADATA")
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=driver._object)
    except (ValueError, UnicodeError, driver.ProbeError) as exc:
        raise WaylandError("APP_WAYLAND_METADATA") from exc


def _sha(path, maximum=64 * 1024 * 1024):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum,
            "APP_WAYLAND_FILE")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tool(path):
    # System commands are fixed root-owned executables; symlinks may resolve
    # inside the system installation (as /bin and /usr/bin normally do).
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    require(info.st_uid == 0 and info.st_mode & 0o022 == 0
            and stat.S_ISREG(info.st_mode) and os.access(resolved, os.X_OK),
            "APP_WAYLAND_SYSTEM_TOOL")
    return str(path)


def _write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _inode(info):
    return info.st_dev, info.st_ino


class SyntheticProfile:
    """An in-memory capability for a profile created by this fixed inspector.

    There is deliberately no reopen(path) or CLI profile argument. A dedicated
    worker keeps this object across sequential runs. Directory and lock inode
    identities survive those runs; a replacement inode is never a new lock on
    the same profile. Runtime, bus, artifacts and owners belong to each run.
    """
    DIRECTORIES = ("", "home", "config", "data", "state", "home/Documents")

    @classmethod
    def create(cls, root):
        root = Path(root)
        require(root.is_absolute(), "APP_WAYLAND_PROFILE_ARGUMENT")
        parent_fd = inspect_api._open_absolute_directory(root.parent)
        try:
            _private(root.parent)
            os.mkdir(root.name, mode=0o700, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        for name in cls.DIRECTORIES[1:]:
            (root / name).mkdir(mode=0o700)
        lock_fd = os.open(root / "profile.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL
                          | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            lock_identity = _inode(os.fstat(lock_fd))
        finally:
            os.close(lock_fd)
        value = object.__new__(cls)
        value.root = root
        value.documents = root / "home/Documents"
        value.profile_id = secrets.token_hex(16)
        value.creator = os.getpid()
        value.identities = {name: _inode((root / name).lstat()) for name in cls.DIRECTORIES}
        value.lock_identity = lock_identity
        value.validate()
        return value

    def validate(self, lock_fd=None):
        require(self.creator == os.getpid(), "APP_WAYLAND_PROFILE_OWNER")
        for name, identity in self.identities.items():
            fd = inspect_api._open_absolute_directory(self.root / name)
            try:
                info = os.fstat(fd)
                require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                        and stat.S_IMODE(info.st_mode) == 0o700 and _inode(info) == identity,
                        "APP_WAYLAND_PROFILE_CHANGED")
            finally:
                os.close(fd)
        root_fd = inspect_api._open_absolute_directory(self.root)
        fd = None
        try:
            fd = os.open("profile.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
                         | os.O_NONBLOCK, dir_fd=root_fd)
            info = os.fstat(fd)
            current = os.stat("profile.lock", dir_fd=root_fd, follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                    and info.st_size == 0 and _inode(info) == self.lock_identity == _inode(current),
                    "APP_WAYLAND_PROFILE_LOCK_CHANGED")
            if lock_fd is not None:
                require(_inode(os.fstat(lock_fd)) == self.lock_identity,
                        "APP_WAYLAND_PROFILE_LOCK_CHANGED")
        finally:
            if fd is not None:
                os.close(fd)
            os.close(root_fd)

    def acquire(self):
        self.validate()
        root_fd = inspect_api._open_absolute_directory(self.root)
        fd = None
        try:
            fd = os.open("profile.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
                         | os.O_NONBLOCK, dir_fd=root_fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.validate(fd)
            return fd
        except BaseException:
            if fd is not None:
                os.close(fd)
            raise
        finally:
            os.close(root_fd)

    def snapshot(self, lock_fd=None):
        """Bounded private byte oracle; publish only digests and counts.

        A settings snapshot includes a preferences-only digest. Session/history
        updates during a valid close must not masquerade as preference loss.
        Malformed settings still have a raw digest, so recovery rejection can
        prove that ambiguous bytes were retained without parsing them as safe.
        """
        own_lock = lock_fd is None
        if own_lock:
            lock_fd = self.acquire()
        try:
            self.validate(lock_fd)
            records, settings = [], None
            entries_left, bytes_left = [256], [4 * 1024 * 1024]

            def visit(fd, prefix, depth):
                nonlocal settings
                require(depth <= 8, "APP_WAYLAND_PROFILE_SNAPSHOT_LIMIT")
                before = os.fstat(fd)
                require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid()
                        and stat.S_IMODE(before.st_mode) == 0o700,
                        "APP_WAYLAND_PROFILE_SNAPSHOT_TYPE")
                with os.scandir(fd) as items:
                    names = []
                    for item in items:
                        entries_left[0] -= 1
                        require(entries_left[0] >= 0, "APP_WAYLAND_PROFILE_SNAPSHOT_LIMIT")
                        names.append(item.name)
                for name in sorted(names):
                    if not prefix and name == "profile.lock":
                        continue
                    relative = prefix + name
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                           | os.O_CLOEXEC, dir_fd=fd)
                        try:
                            require(_inode(info) == _inode(os.fstat(child_fd)),
                                    "APP_WAYLAND_PROFILE_SNAPSHOT_CHANGED")
                            records.append((relative, "directory", None))
                            visit(child_fd, relative + "/", depth + 1)
                            require(_inode(info) == _inode(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                                    "APP_WAYLAND_PROFILE_SNAPSHOT_CHANGED")
                        finally:
                            os.close(child_fd)
                    else:
                        # NONBLOCK and no-follow avoid blocking on an injected
                        # FIFO/device. Hardlinks and non-private files fail.
                        data = inspect_api._read_file_at(fd, name, uid=os.getuid(),
                                limit=min(1024 * 1024, bytes_left[0]), private=True,
                                allow_empty=True, reason="APP_WAYLAND_PROFILE_SNAPSHOT_TYPE")
                        bytes_left[0] -= len(data)
                        require(bytes_left[0] >= 0, "APP_WAYLAND_PROFILE_SNAPSHOT_LIMIT")
                        records.append((relative, "file", hashlib.sha256(data).hexdigest()))
                        if relative == "config/glib-2.0/settings/keyfile":
                            settings = data
                after = os.fstat(fd)
                require(inspect_api._identity(before) == inspect_api._identity(after),
                        "APP_WAYLAND_PROFILE_SNAPSHOT_CHANGED")

            root_fd = inspect_api._open_absolute_directory(self.root)
            try:
                visit(root_fd, "", 0)
            finally:
                os.close(root_fd)
            self.validate(lock_fd)
            preferences = []
            if settings is not None:
                parser = configparser.ConfigParser(interpolation=None, strict=True)
                parser.optionxform = str
                try:
                    parser.read_string(settings.decode("utf-8"))
                    for section in sorted(parser.sections()):
                        # Mousepad 0.6.3 flushes remembered geometry under
                        # state/window (mousepad-window.c:1356-1408). It is
                        # ordinary persistent configuration, unlike the
                        # state/application session/history recovery data.
                        if (section.startswith("org/xfce/mousepad/preferences/")
                                or section == "org/xfce/mousepad/state/window"):
                            preferences.extend((section, key, value) for key, value in sorted(parser.items(section)))
                except (UnicodeError, configparser.Error):
                    preferences = None
            digest = lambda value: hashlib.sha256(json.dumps(value, separators=(",", ":"),
                                                           sort_keys=True).encode()).hexdigest()
            return {"tree_sha256": digest(records),
                    "documents_sha256": digest([row for row in records if row[0].startswith("home/Documents/")]),
                    "settings_present": settings is not None,
                    "settings_sha256": None if settings is None else hashlib.sha256(settings).hexdigest(),
                    "preferences_sha256": digest(preferences) if preferences else None,
                    "preferences_count": 0 if preferences is None else len(preferences)}
        finally:
            if own_lock:
                os.close(lock_fd)


class _Run:
    def __init__(self, artifact, termination):
        self.artifact = artifact
        self.termination = termination
        self.owner = owner_api.ProcessOwner()
        self.selector = selectors.DefaultSelector()
        self.children = []
        self.pidfds = []
        self.pipes = []
        self._pipe_keys = {}
        self._pipe_eof = set()
        self.output_bytes = {}
        self.runtime = None
        self.command_pipe = None
        self.control_stdout = None
        self.profile_lock = None
        self.profile = None
        self.profile_after = None
        self.profile_snapshot_error = False
        self.owner_scope_id = secrets.token_hex(16)
        self.runtime_id = secrets.token_hex(16)
        self.deadline = time.monotonic() + 110
        self.cleanup = None

    def spawn(self, label, command, env, *, control=False):
        self.termination.check()
        child = self.owner.spawn(command, env=env)
        self.children.append((label, child))
        # Remember both Popen pipes before any descriptor/selector operation
        # can fail. The owner already holds this child; finish must drain and
        # close its pipes even if pidfd_open or a later registration fails.
        for name in ("stdout", "stderr"):
            stream = getattr(child, name)
            self.pipes.append(stream)
            if control and name == "stdout":
                self.control_stdout = stream
                key = "control_stdout_remaining"
            else:
                key = label + "_" + name
            self._pipe_keys[stream] = key
            self.output_bytes[key] = 0
        # Keep a live descriptor before waiting or inspecting other processes.
        # Numeric IDs are never recovered from artifacts.
        self.pidfds.append((label, os.pidfd_open(child.pid, 0)))
        self._register_pipes()
        # An interrupt received during Popen must not break the handoff from
        # the OS child to its live owner, pidfd and pipe registrations.
        self.termination.check()
        return child

    def _register_pipes(self, *, cleanup=False):
        """Reconcile partial registrations without reopening observed EOF."""
        for stream, key in self._pipe_keys.items():
            if stream in self._pipe_eof or (not cleanup and stream is self.control_stdout):
                continue
            try:
                self.selector.get_key(stream)
                continue
            except KeyError:
                pass
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ, key)

    def pump(self, *, enforce=True):
        if enforce:
            self.termination.check()
            require(time.monotonic() < self.deadline, "APP_WAYLAND_RUN_TIMEOUT")
        # Fair and bounded reads; application stderr/stdout contents are never
        # decoded, persisted, or included in exceptions.
        for key, _ in self.selector.select(0):
            try:
                data = os.read(key.fd, 16384)
            except BlockingIOError:
                continue
            if not data:
                self.selector.unregister(key.fd)
                self._pipe_eof.add(key.fileobj)
            else:
                self.output_bytes[key.data] += len(data)
                if enforce:
                    require(sum(self.output_bytes.values()) <= 4 * 1024 * 1024,
                            "APP_WAYLAND_OUTPUT_LIMIT")

    def wait_until(self, predicate, seconds, reason):
        deadline = min(self.deadline, time.monotonic() + seconds)
        while not predicate():
            self.pump()
            require(time.monotonic() < deadline, reason)
            time.sleep(.01)

    def wait_child(self, child, seconds):
        self.wait_until(lambda: child.poll() is not None, seconds, "APP_WAYLAND_CHILD_TIMEOUT")
        return child.wait(timeout=0)

    def signal_root(self, label, number=signal.SIGTERM):
        fd = next(fd for name, fd in self.pidfds if name == label)
        try:
            signal.pidfd_send_signal(fd, number)
        except ProcessLookupError:
            pass

    def crash_mousepad(self, executable):
        """Signal only a presently owned Mousepad process through a live fd.

        This fixed abnormal-exit case finds a descendant using the kernel's
        live executable inode, captures its pidfd, then revalidates process
        identity and descendant membership before SIGKILL. No PID is read
        from disk and no inherited sibling/precursor process is eligible.
        """
        self.termination.check()
        expected = _inode(Path(executable).resolve(strict=True).stat())
        deadline = time.monotonic() + 2
        while True:
            try:
                rows = owner_api.descendant_snapshot(self.owner.owner)
                found = [row for row in rows.values() if row["state"] != "Z"
                         and _inode((Path("/proc") / str(row["pid"]) / "exe").stat()) == expected]
                require(len(found) == 1, "APP_WAYLAND_CRASH_TARGET")
                row = found[0]
                descriptor = os.pidfd_open(row["pid"], 0)
                try:
                    require(owner_api.same_process(row, owner_api.identity(row["pid"]))
                            and row["pid"] in owner_api.descendant_snapshot(self.owner.owner)
                            and _inode((Path("/proc") / str(row["pid"]) / "exe").stat()) == expected,
                            "APP_WAYLAND_CRASH_IDENTITY")
                    self.pidfds.append(("mousepad_crash", descriptor))
                except BaseException:
                    os.close(descriptor)
                    raise
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                self.wait_until(lambda: bool(select.select([descriptor], [], [], 0)[0]), 5,
                                "APP_WAYLAND_CRASH_EXIT")
                return True
            except (FileNotFoundError, ProcessLookupError, owner_api.OwnershipError) as exc:
                if isinstance(exc, owner_api.OwnershipError) and str(exc) not in (
                        "PROCESS_CHANGED", "PROCESS_SCAN_LIMIT"):
                    raise
                self.pump()
                require(time.monotonic() < deadline, "APP_WAYLAND_CRASH_TARGET")
                time.sleep(.01)

    def finish(self):
        # The existing owner captures/reaps the full descendant closure,
        # including descendants which changed session/process group.
        try:
            self.cleanup = self.owner.finish(timeout=5.0, grace=1.5)
            # A spawn may have stopped between Popen and pidfd/pipe adoption.
            # Register every remembered, not-yet-EOF stream before claiming
            # output completion. The Control stream also joins this drain.
            self._register_pipes(cleanup=True)
            self.control_stdout = None
            deadline = time.monotonic() + .5
            while self.selector.get_map() and time.monotonic() < deadline:
                self.pump(enforce=False)
                time.sleep(.01)
        except (OSError, ValueError, owner_api.OwnershipError) as exc:
            # A failed scan/drain is not evidence that ownership can end.
            raise CleanupPending(self) from exc
        if not self.cleanup["complete"]:
            raise CleanupPending(self)
        if self.selector.get_map():
            # Child exit alone does not prove that all inherited output ends
            # have reached EOF. Keep the live guard, pidfds and exact profile
            # lock while a later bounded attempt drains the remaining pipes.
            self.cleanup = {**self.cleanup, "complete": False, "pending": True,
                            "reason": "OUTPUT_PIPES_PENDING"}
            raise CleanupPending(self)
        try:
            pipes_eof = not self.selector.get_map()
            if self.profile is not None:
                try:
                    # All app descendants have exited; settings have finished
                    # flushing. Keep the exact profile flock during the read.
                    self.profile_after = self.profile.snapshot(self.profile_lock)
                except (OSError, ValueError, inspect_api.InspectionError):
                    self.profile_snapshot_error = True
            for _, fd in self.pidfds:
                os.close(fd)
            self.pidfds.clear()
            if self.command_pipe is not None:
                self.command_pipe.close()
            for pipe in self.pipes:
                pipe.close()
            self.selector.close()
            if self.profile_lock is not None:
                os.close(self.profile_lock)
                self.profile_lock = None
            if self.runtime is not None:
                # Only this live run's mkdtemp subtree, never a user profile.
                import shutil
                shutil.rmtree(self.runtime)
            return {**self.cleanup, "root_pidfds_closed": True,
                    "output_pipes_eof": pipes_eof}
        finally:
            # All descendants are proven gone. Even a later artifact-removal
            # error must not leave our process-global handlers installed.
            self.termination.restore()


def retry_cleanup(pending):
    require(isinstance(pending, CleanupPending), "APP_WAYLAND_PENDING_HANDLE")
    return pending.run.finish()


def run_case(repo_root, artifact, settings_bundle, *, expected_app_id=inspect_api.WINDOW_APP_ID,
             profile=None, ending="close", on_ready=None, exercise=True):
    """Run a fresh owner/Control, optionally reusing our synthetic profile.

    repo_root must be this module's checkout, artifact an empty private owner
    directory, and settings_bundle the validated build directory containing
    settings-backend.json and gio/libeidolon-settings.so. Native fixture must
    already exist in target/debug; builds are a separate fixed command stage.
    profile is an in-memory SyntheticProfile created by this dedicated worker,
    never an arbitrary caller-supplied path. The internal callback coordinates
    a fixed predecessor response test; it does not transfer live ownership.
    """
    repo_root, artifact, settings_bundle = map(Path, (repo_root, artifact, settings_bundle))
    require(repo_root.resolve() == ROOT and artifact.is_absolute(), "APP_WAYLAND_ARGUMENT")
    _private(artifact)
    require(not any(artifact.iterdir()), "APP_WAYLAND_ARTIFACT_NOT_EMPTY")
    result = {"schema": 1, "stage": "wayland", "state": "fail", "status": "failed", "error": None,
              "feasibility_verified": False, "physical_input_verified": False,
              "pending": list(REQUIRED_CHECKS),
              "checks": {item: False for item in REQUIRED_CHECKS},
              "payload_logged": False, "titles_logged": False, "pixels_logged": False,
              "production_ready": False, "run_id": secrets.token_hex(16),
              "app_spawned": False, "initial_main": None, "termination_kind": ending,
              "documents_before_termination": None,
              "termination": {"kind": ending, "observed": False, "app_pidfd_exit": None,
                              "server_returncode": None, "forced_scope_cleanup": False}}
    run = None
    control = None
    termination = None
    try:
        require(sys.platform == "linux" and os.getuid() == os.geteuid() != 0,
                "APP_WAYLAND_NONROOT_REQUIRED")
        termination = _TerminationGuard()
        require(ending in ("close", "stop", "crash") and type(exercise) is bool,
                "APP_WAYLAND_CASE_ARGUMENT")
        require(expected_app_id == inspect_api.WINDOW_APP_ID, "APP_WAYLAND_APP_ID")
        _private(settings_bundle)
        manifest = _json(settings_bundle / "settings-backend.json")
        module = settings_bundle / "gio/libeidolon-settings.so"
        require(manifest.get("kind") == "linux-real-app-settings-a0"
                and manifest.get("settings_kind") == "eidolon-keyfile-v1"
                and manifest.get("settings_policy") == "mousepad-single-main-no-restore-v1"
                and manifest.get("files", {}).get("gio/libeidolon-settings.so") == _sha(module),
                "APP_WAYLAND_SETTINGS_BUNDLE")
        metadata = _json(repo_root / "native/generated/waypipe-host.json")
        waypipe = Path(metadata.get("executable", ""))
        require(waypipe.is_absolute() and metadata.get("target") == "host"
                and metadata.get("version") == "0.11.3"
                and metadata.get("sha256") == _sha(waypipe) and os.access(waypipe, os.X_OK),
                "APP_WAYLAND_WAYPIPE_UNAVAILABLE")
        binary = repo_root / "target/debug/linux-real-app-wayland-probe"
        require(binary.is_file() and not binary.is_symlink() and os.access(binary, os.X_OK),
                "APP_WAYLAND_FIXTURE_UNBUILT")
        mousepad = _tool(Path("/usr/bin/mousepad"))
        dbus_run = _tool(Path("/usr/bin/dbus-run-session"))
        dbus_daemon = _tool(Path("/usr/bin/dbus-daemon"))
        python = _tool(Path("/usr/bin/python3"))
        executable_hash = _sha(Path(mousepad).resolve())
        if profile is None:
            profile = SyntheticProfile.create(artifact / "profile")
        require(isinstance(profile, SyntheticProfile), "APP_WAYLAND_PROFILE_ARGUMENT")
        profile.validate()
        (artifact / "compositor").mkdir(mode=0o700)
        termination.check()
        run = _Run(artifact, termination)
        run.profile_lock = profile.acquire()
        run.profile = profile
        result.update(profile_id=profile.profile_id, owner_scope_id=run.owner_scope_id,
                      runtime_id=run.runtime_id, mousepad_sha256=executable_hash,
                      waypipe_sha256=metadata["sha256"], waypipe_version="0.11.3",
                      waypipe_ends=2, private_bus=True, profile_kind="synthetic",
                      source_argv="mousepad --disable-server")
        recovery = inspect_api.guard_recovery_state(profile.root)
        result["recovery_guard"] = {**recovery, "profile_lock_held": True}
        # Regular synthetic recovery fixtures can be hashed even when their
        # session text is ambiguous. A linked/unsafe fixture is still refused
        # by the recovery reason, without following it merely for evidence.
        try:
            result["profile_before"] = profile.snapshot(run.profile_lock)
        except (OSError, ValueError, inspect_api.InspectionError):
            require(recovery.get("clear") is True, "APP_STATE_RECOVERY_REQUIRED")
            raise
        require(recovery.get("clear") is True, "APP_STATE_RECOVERY_REQUIRED")
        run.runtime = Path(tempfile.mkdtemp(prefix="eidolon-l7a-", dir="/tmp"))
        for name in ("cache", "tmp"):
            (run.runtime / name).mkdir(mode=0o700)
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "HOME": str(profile.root / "home"), "XDG_CONFIG_HOME": str(profile.root / "config"),
               "XDG_DATA_HOME": str(profile.root / "data"), "XDG_STATE_HOME": str(profile.root / "state"),
               "XDG_CACHE_HOME": str(run.runtime / "cache"), "TMPDIR": str(run.runtime / "tmp"),
               "XDG_RUNTIME_DIR": str(run.runtime), "WAYLAND_DISPLAY": "wayland-l7a",
               "GDK_BACKEND": "wayland", "GDK_GL": "disable", "GDK_RENDERING": "image",
               "GIO_USE_VFS": "local", "GTK_USE_PORTAL": "0", "NO_AT_BRIDGE": "1",
               "GTK_IM_MODULE": "gtk-im-context-simple", "GSETTINGS_BACKEND": "eidolon-keyfile-v1",
               "GIO_EXTRA_MODULES": str(settings_bundle / "gio")}
        fifo = run.runtime / "control"
        os.mkfifo(fifo, 0o600)
        run.command_pipe = os.fdopen(os.open(fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC), "wb", buffering=0)
        compositor = run.spawn("compositor", [python, str(Path(__file__).resolve()), "--exec-compositor",
                         str(binary), str(run.runtime), str(artifact / "compositor"), str(fifo)], env, control=True)
        control = driver.Control(SimpleNamespace(stdin=run.command_pipe, stdout=compositor.stdout), pump=run.pump)
        common = [str(waypipe), "--no-gpu", "--compress", "zstd", "--threads", "2", "--socket", str(run.runtime / "channel")]
        client = run.spawn("waypipe_client", [*common, "client"], env)
        run.wait_until(lambda: (run.runtime / "channel").exists() or client.poll() is not None,
                       5, "APP_WAYLAND_CLIENT_START")
        require(client.poll() is None and stat.S_ISSOCK((run.runtime / "channel").stat().st_mode),
                "APP_WAYLAND_CLIENT_START")
        bus_config = run.runtime / "bus.conf"
        with bus_config.open("x", encoding="utf-8") as stream:
            os.chmod(bus_config, 0o600)
            stream.write('<busconfig><type>session</type><keep_umask/><listen>unix:path=' + escape(str(run.runtime / "bus"))
                         + '</listen><auth>EXTERNAL</auth><policy context="default"><allow own="*"/>'
                         + '<allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>')
        # Recheck immediately before the app-bearing spawn while retaining
        # this exact profile lock until the complete descendant closure exits.
        profile.validate(run.profile_lock)
        recovery = inspect_api.guard_recovery_state(profile.root)
        require(recovery.get("clear") is True, "APP_STATE_RECOVERY_REQUIRED")
        result["recovery_guard"] = {**recovery, "profile_lock_held": True}
        before_app = control.snapshot()
        require(before_app.get("counts", {}).get("clients") == 0 and not driver.mapped(before_app)
                and before_app.get("observed_mapped") == [], "APP_WAYLAND_CLIENT_NOT_FRESH")
        server = run.spawn("waypipe_server", [*common, "--display", "wayland-r", "server", "--", dbus_run,
                           "--dbus-daemon=" + dbus_daemon, "--config-file=" + str(bus_config),
                           "--", mousepad, "--disable-server"], env)
        result["app_spawned"] = True
        main = control.wait_main(expected_app_id)
        result["initial_main"] = {**driver.observe_ready(control), "prelaunch_client_count": 0}
        require(result["initial_main"]["observed_mapped_count"] == 1, "APP_WAYLAND_MAIN_NOT_FIRST")
        result["checks"].update({
            "private_bus": True, "first_main": True,
            "expected_app_id": True,
        })
        if exercise:
            initial = b"eidolon-a0-opened\n"
            source, destination = profile.documents / "input.txt", profile.documents / "saved.txt"
            with source.open("xb") as stream:
                os.chmod(source, 0o600)
                stream.write(initial)
            result["evidence"] = driver.exercise_open_edit_save(control, source, destination, initial)
            result["checks"].update(transient_dialogs=True, open_edit_save_as=True)
        if on_ready is not None:
            on_ready(run, control, result)
        termination.check()
        # Save's independent byte oracle precedes termination. Correlate its
        # resulting Documents tree with the post-cleanup snapshot: comparing
        # only post-exit/restart snapshots could accept consistent data loss.
        result["documents_before_termination"] = profile.snapshot(run.profile_lock)["documents_sha256"]
        termination.check()
        if ending == "stop":
            # The finally block forcibly collects this worker's full scope.
            # It must not fabricate normal-Close or compositor-shutdown proof.
            control.phase = "forced_stop"
            result["termination"]["forced_scope_cleanup"] = True
        else:
            if ending == "close":
                control.phase = "normal_close"
                control.close(main)
            else:
                control.phase = "app_crash"
                result["termination"]["app_pidfd_exit"] = run.crash_mousepad(mousepad)
            control.wait(lambda snapshot: not driver.mapped(snapshot), 5, "APP_WAYLAND_MAIN_CLOSE")
            control.phase = "waypipe_cleanup"
            server_returncode = run.wait_child(server, 5)
            result["termination"]["server_returncode"] = server_returncode
            require(server_returncode == 0 if ending == "close" else server_returncode != 0,
                    "APP_WAYLAND_SERVER_EXIT")
            run.signal_root("waypipe_client")
            run.wait_child(client, 5)
            final_snapshot = control.wait(lambda snapshot: snapshot if all(v == 0 for v in snapshot["counts"].values())
                                          else None, 5, "APP_WAYLAND_CLIENT_CLEANUP")
            result["final_graph"] = {"window_count": len(driver.mapped(final_snapshot)),
                                     "client_count": final_snapshot["counts"]["clients"],
                                     "pressed": final_snapshot["input"]["pressed"],
                                     "focus_window": final_snapshot["input"].get("focus_window")}
            control.phase = "compositor_shutdown"
            control.shutdown()
            require(run.wait_child(compositor, 5) == 0, "APP_WAYLAND_COMPOSITOR_EXIT")
            receipt = _json(artifact / "compositor/result.json")
            require(receipt.get("state") == "complete" and receipt.get("client_cleanup_verified") is True
                    and receipt.get("forced_disconnect") is False, "APP_WAYLAND_COMPOSITOR_RECEIPT")
            result["termination"]["observed"] = True
        result.update(state="partial", status="incomplete", stage="wayland")
    except (WaylandError, driver.ProbeError, owner_api.OwnershipError, inspect_api.InspectionError) as exc:
        result["error"] = str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", str(exc)) else "APP_WAYLAND_FAILED"
        result["state"] = "fail"
        if control is not None and result["error"] == "INITIAL_APP_ID":
            result["diagnostics"] = driver.diagnostic_metadata(control)
            # Observe whether GTK changes metadata after the first mapped
            # frame, without admitting it or sending any application input.
            # The initial identity failure remains the result in either case.
            try:
                control.settle(.25)
                result["identity_after_settle"] = driver.diagnostic_metadata(control)
            except (WaylandError, driver.ProbeError, OSError, ValueError):
                pass
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["error"] = "APP_WAYLAND_DEPENDENCY_OR_IO"
        result["state"] = "fail"
    finally:
        if control is not None:
            result.setdefault("diagnostics", driver.diagnostic_metadata(control))
        if run is not None:
            result["app_spawned"] = result["app_spawned"] or any(
                label == "waypipe_server" for label, _ in run.children)
            try:
                try:
                    result["cleanup"] = run.finish()
                except CleanupPending:
                    # A second bounded attempt retains this live owner and
                    # all pidfds; no recovery from numeric artifact PIDs.
                    result["cleanup_retry"] = True
                    result["cleanup"] = run.finish()
            except CleanupPending as pending:
                result["state"] = "fail"
                result["status"] = "failed"
                result["pending"] = [name for name in REQUIRED_CHECKS if not result["checks"][name]]
                result["error"] = "APP_WAYLAND_CLEANUP_PENDING"
                result["cleanup"] = run.cleanup
                if termination.requested is not None:
                    result["termination_signal"] = termination.requested
                pending.receipt = result
                try:
                    _write(artifact / "wayland.json", result)
                except (OSError, ValueError):
                    # ENOSPC, a partial file or failed fsync cannot replace
                    # the live handle needed by the caller's retry loop.
                    result["receipt_write_failed"] = True
                raise
            result["output_bytes"] = run.output_bytes
            if run.profile_after is not None:
                result["profile_after"] = run.profile_after
                if (result["documents_before_termination"] is not None
                        and result["documents_before_termination"] != run.profile_after["documents_sha256"]):
                    result["state"] = "fail"
                    if result["error"] is None:
                        result["error"] = "APP_WAYLAND_DOCUMENTS_CHANGED"
            if run.profile_snapshot_error:
                result["state"] = "fail"
                if result["error"] is None:
                    result["error"] = "APP_WAYLAND_PROFILE_SNAPSHOT_FAILED"
            if ending == "stop" and result["termination"]["forced_scope_cleanup"] and result["state"] == "partial":
                result["termination"]["observed"] = True
            if not result["cleanup"]["output_pipes_eof"]:
                result["state"] = "fail"
                result["error"] = "APP_WAYLAND_OUTPUT_EOF"
        elif termination is not None:
            # A prerequisite/constructor failure created no owned children.
            termination.restore()
        if termination is not None and termination.requested is not None:
            result["termination_signal"] = termination.requested
            result["state"] = "fail"
            if result["error"] is None:
                result["error"] = "APP_WAYLAND_INTERRUPTED"
        if result["state"] == "fail":
            result["status"] = "failed"
        result["pending"] = [name for name in REQUIRED_CHECKS if not result["checks"][name]]
        _write(artifact / "wayland.json", result)
    return result


def run_feasibility(repo, artifact, settings_bundle, *, waypipe, compositor):
    """Root orchestrator API. Dependencies are fixed build outputs, no CLI argv.

    Supplied paths must be exactly the checkout's verified Waypipe metadata and
    target/debug fixture path. A successful real Open/Save sequence is partial
    until all GUI/read-only/restart pending checks have independent evidence.
    """
    repo, waypipe, compositor = map(Path, (repo, waypipe, compositor))
    require(repo.resolve() == ROOT and waypipe.is_absolute() and compositor.is_absolute(),
            "APP_WAYLAND_ARGUMENT")
    require(compositor == ROOT / "target/debug/linux-real-app-wayland-probe",
            "APP_WAYLAND_COMPOSITOR_PATH")
    metadata = _json(ROOT / "native/generated/waypipe-host.json")
    require(str(waypipe) == metadata.get("executable"), "APP_WAYLAND_WAYPIPE_PATH")
    from linux_real_app_harness import run_feasibility as run_harness
    return run_harness(repo, artifact, settings_bundle)


def _exec_compositor(args):
    # Internal fixed adapter permits ProcessOwner.spawn's DEVNULL stdin without
    # changing the existing shared owner API or spawning an unowned sibling.
    require(len(args) == 4, "APP_WAYLAND_INTERNAL_ARGUMENT")
    binary, runtime, artifact, fifo = map(Path, args)
    require(binary == ROOT / "target/debug/linux-real-app-wayland-probe"
            and runtime.parent == Path("/tmp") and runtime.name.startswith("eidolon-l7a-")
            and fifo == runtime / "control", "APP_WAYLAND_INTERNAL_ARGUMENT")
    _private(runtime)
    require(stat.S_ISFIFO(fifo.lstat().st_mode), "APP_WAYLAND_INTERNAL_FIFO")
    fd = os.open(fifo, os.O_RDONLY | os.O_NOFOLLOW)
    os.dup2(fd, 0)
    os.close(fd)
    os.execve(binary, [str(binary), "--runtime-dir", str(runtime), "--artifact-dir", str(artifact)], dict(os.environ))


if __name__ == "__main__":
    try:
        require(len(sys.argv) >= 2 and sys.argv[1] == "--exec-compositor", "APP_WAYLAND_INTERNAL_ONLY")
        _exec_compositor(sys.argv[2:])
    except (OSError, WaylandError):
        print("APP_WAYLAND_INTERNAL_FAILED", file=sys.stderr)
        raise SystemExit(1)
