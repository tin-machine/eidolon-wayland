"""Explicit A0 Waypipe/Mousepad run with a dedicated live pidfd owner.

run_case is for a new, single-threaded non-root check process, after fixed builds
have finished. It never launches in the caller's desktop/profile or invokes a
shell. Output is counted/drained, never retained. No result is a full A0 pass.
"""
from __future__ import annotations

import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
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


def require(value, reason):
    if not value:
        raise WaylandError(reason)


def _private(path):
    info = path.lstat()
    require(path.is_absolute() and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0, "APP_WAYLAND_PRIVATE_DIRECTORY")


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


class _Run:
    def __init__(self, artifact):
        self.artifact = artifact
        self.owner = owner_api.ProcessOwner()
        self.selector = selectors.DefaultSelector()
        self.children = []
        self.pidfds = []
        self.pipes = []
        self.output_bytes = {}
        self.runtime = None
        self.command_pipe = None
        self.control_stdout = None
        self.profile_lock = None
        self.deadline = time.monotonic() + 110
        self.cleanup = None

    def spawn(self, label, command, env, *, control=False):
        child = self.owner.spawn(command, env=env)
        self.children.append((label, child))
        # Keep a live descriptor immediately, before waiting or inspecting
        # other processes. Numeric IDs are never recovered from artifacts.
        self.pidfds.append((label, os.pidfd_open(child.pid, 0)))
        for name in ("stdout", "stderr"):
            stream = getattr(child, name)
            self.pipes.append(stream)
            if control and name == "stdout":
                self.control_stdout = stream
                continue
            os.set_blocking(stream.fileno(), False)
            key = label + "_" + name
            self.output_bytes[key] = 0
            self.selector.register(stream, selectors.EVENT_READ, key)
        return child

    def pump(self, *, enforce=True):
        if enforce:
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

    def signal_root(self, label):
        fd = next(fd for name, fd in self.pidfds if name == label)
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def finish(self):
        # The existing owner captures/reaps the full descendant closure,
        # including descendants which changed session/process group.
        self.cleanup = self.owner.finish(timeout=5.0, grace=1.5)
        if self.control_stdout is not None:
            self.output_bytes["control_stdout_remaining"] = 0
            self.selector.register(self.control_stdout, selectors.EVENT_READ, "control_stdout_remaining")
            self.control_stdout = None
        deadline = time.monotonic() + .5
        while self.selector.get_map() and time.monotonic() < deadline:
            self.pump(enforce=False)
            time.sleep(.01)
        if not self.cleanup["complete"]:
            raise CleanupPending(self)
        pipes_eof = not self.selector.get_map()
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


def retry_cleanup(pending):
    require(isinstance(pending, CleanupPending), "APP_WAYLAND_PENDING_HANDLE")
    return pending.run.finish()


def run_case(repo_root, artifact, settings_bundle, *, expected_app_id="org.xfce.mousepad"):
    """Run one fresh synthetic profile. Return fail/partial, never full pass.

    repo_root must be this module's checkout, artifact an empty private owner
    directory, and settings_bundle the validated build directory containing
    settings-backend.json and gio/libeidolon-settings.so. Native fixture must
    already exist in target/debug; builds are a separate fixed command stage.
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
              "production_ready": False}
    run = None
    try:
        require(sys.platform == "linux" and os.getuid() == os.geteuid() != 0,
                "APP_WAYLAND_NONROOT_REQUIRED")
        require(expected_app_id == "org.xfce.mousepad", "APP_WAYLAND_APP_ID")
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
        for subdir in ("profile", "profile/home", "profile/config", "profile/data", "profile/state", "files", "compositor"):
            (artifact / subdir).mkdir(mode=0o700)
        run = _Run(artifact)
        run.profile_lock = os.open(artifact / "profile/profile.lock",
                                   os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        fcntl.flock(run.profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run.runtime = Path(tempfile.mkdtemp(prefix="eidolon-l7a-", dir="/tmp"))
        for name in ("cache", "tmp"):
            (run.runtime / name).mkdir(mode=0o700)
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "HOME": str(artifact / "profile/home"), "XDG_CONFIG_HOME": str(artifact / "profile/config"),
               "XDG_DATA_HOME": str(artifact / "profile/data"), "XDG_STATE_HOME": str(artifact / "profile/state"),
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
        recovery = inspect_api.guard_recovery_state(artifact / "profile")
        require(recovery.get("clear") is True, "APP_STATE_RECOVERY_REQUIRED")
        result["recovery_guard"] = {**recovery, "profile_lock_held": True}
        server = run.spawn("waypipe_server", [*common, "--display", "wayland-r", "server", "--", dbus_run,
                           "--dbus-daemon=" + dbus_daemon, "--config-file=" + str(bus_config),
                           "--", mousepad, "--disable-server"], env)
        main = control.wait_main(expected_app_id)
        initial = b"eidolon-a0-opened\n"
        source, destination = artifact / "files/input.txt", artifact / "files/saved.txt"
        with source.open("xb") as stream:
            os.chmod(source, 0o600)
            stream.write(initial)
        evidence = driver.exercise_open_edit_save(control, source, destination, initial)
        control.close(main)
        control.wait(lambda snapshot: not driver.mapped(snapshot), 5, "APP_WAYLAND_MAIN_CLOSE")
        require(run.wait_child(server, 5) == 0, "APP_WAYLAND_SERVER_EXIT")
        run.signal_root("waypipe_client")
        run.wait_child(client, 5)
        control.wait(lambda snapshot: all(v == 0 for v in snapshot["counts"].values()), 5,
                     "APP_WAYLAND_CLIENT_CLEANUP")
        control.shutdown()
        require(run.wait_child(compositor, 5) == 0, "APP_WAYLAND_COMPOSITOR_EXIT")
        receipt = _json(artifact / "compositor/result.json")
        require(receipt.get("state") == "complete" and receipt.get("client_cleanup_verified") is True
                and receipt.get("forced_disconnect") is False, "APP_WAYLAND_COMPOSITOR_RECEIPT")
        result["evidence"] = evidence
        result["checks"].update({
            "private_bus": True, "first_main": True,
            "expected_app_id": True, "transient_dialogs": True, "open_edit_save_as": True,
        })
        result.update({"state": "partial", "status": "incomplete", "stage": "wayland",
                       "mousepad_sha256": executable_hash,
                       "waypipe_sha256": metadata["sha256"], "waypipe_version": "0.11.3",
                       "waypipe_ends": 2, "private_bus": True, "profile_kind": "synthetic",
                       "source_argv": "mousepad --disable-server"})
    except (WaylandError, driver.ProbeError, owner_api.OwnershipError) as exc:
        result["error"] = str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", str(exc)) else "APP_WAYLAND_FAILED"
        result["state"] = "fail"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["error"] = "APP_WAYLAND_DEPENDENCY_OR_IO"
        result["state"] = "fail"
    finally:
        if run is not None:
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
                pending.receipt = result
                _write(artifact / "wayland.json", result)
                raise
            result["output_bytes"] = run.output_bytes
            if not result["cleanup"]["output_pipes_eof"]:
                result["state"] = "fail"
                result["error"] = "APP_WAYLAND_OUTPUT_EOF"
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
    return run_case(repo, artifact, settings_bundle)


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
