"""Fixed A0 restart/independence harness with process-local worker ownership.

The coordinator never finishes its descendant-wide owner while a worker is
alive: each worker first completes its own live pidfd/lock cleanup. IPC reports
observations, not ownership. Only inspector-created synthetic profiles enter
this harness; no command or existing-profile option is exposed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import selectors
import signal
import stat
import sys
import time
import uuid

import linux_real_app_wayland as app

ROOT = app.ROOT
MAX_MESSAGE = 128 * 1024
MAX_QUEUE = 8
CASES = ("close_restart", "stop_restart", "crash_restart")
RECOVERY_CASES = ("session", "autosave", "ambiguous")
EVENTS = frozenset({"ready", "observation", "case", "done"})
OPERATIONS = frozenset({"run", "observe", "close", "stop"})
COMPACT_FIELDS = (
    "state", "status", "error", "run_id", "owner_scope_id", "profile_id", "runtime_id",
    "termination_kind", "termination", "initial_main", "profile_before", "profile_after", "documents_before_termination",
    "app_spawned", "cleanup", "recovery_guard", "mousepad_sha256", "waypipe_sha256", "waypipe_version", "final_graph",
)
_write = app._write


class CoordinatorPending(RuntimeError):
    """The caller retains this object and retries the same live coordinator."""
    def __init__(self, coordinator):
        super().__init__("APP_COORDINATOR_CLEANUP_PENDING")
        self.coordinator = coordinator


def _compact(receipt):
    return {key: receipt.get(key) for key in COMPACT_FIELDS}


def _worker_command(role, directory, settings_bundle):
    app.require(role in ("precursor", "target"), "APP_WORKER_ROLE")
    return [app._tool(Path("/usr/bin/python3")), str(Path(__file__).resolve()),
            "--worker", role, str(directory), str(settings_bundle)]


def _decode(line):
    app.require(0 < len(line) <= MAX_MESSAGE, "APP_WORKER_MESSAGE_BOUND")
    try:
        return json.loads(line, object_pairs_hook=app.driver._object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, app.driver.ProbeError) as exc:
        raise app.WaylandError("APP_WORKER_MESSAGE_JSON") from exc


def _event(value):
    app.require(isinstance(value, dict) and set(value) == {"schema", "event", "id", "data"}
                and type(value["schema"]) is int and value["schema"] == 1
                and type(value["event"]) is str and value["event"] in EVENTS and type(value["id"]) is int
                and 0 <= value["id"] <= 64 and isinstance(value["data"], dict),
                "APP_WORKER_EVENT")
    return value


class _Worker:
    def __init__(self, role, directory, command_fd):
        self.role, self.directory, self.command_fd = role, directory, command_fd
        self.process = self.pidfd = self.stdout = self.stderr = None
        self.buffer = bytearray()
        self.messages = []
        self.next_id = 0
        self.stop_requested = False
        self.output_bytes = 0


class _Coordinator:
    """Own all workers while preserving their narrower ownership scopes."""
    def __init__(self, artifact, settings_bundle):
        self.artifact, self.settings_bundle = Path(artifact), Path(settings_bundle)
        app._private(self.artifact)
        self.workers = []
        self.selector = selectors.DefaultSelector()
        self.cleanup = None
        self.closed = False
        self.termination = app._TerminationGuard()
        try:
            self.owner = app.owner_api.ProcessOwner()
        except BaseException:
            self.selector.close()
            self.termination.restore()
            raise

    def spawn(self, role):
        self.termination.check()
        app.require(role in ("precursor", "target") and not any(w.role == role for w in self.workers),
                    "APP_WORKER_ROLE")
        directory = self.artifact / role
        directory.mkdir(mode=0o700)
        fifo = directory / "commands"
        os.mkfifo(fifo, 0o600)
        fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        worker = _Worker(role, directory, fd)
        self.workers.append(worker)
        # No handler throws between Popen and these registrations. Even a later
        # pidfd/pipe registration failure leaves the child in owner.roots.
        worker.process = self.owner.spawn(_worker_command(role, directory, self.settings_bundle),
                                         env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
        worker.stdout, worker.stderr = worker.process.stdout, worker.process.stderr
        worker.pidfd = os.pidfd_open(worker.process.pid, 0)
        self._register_pipes(worker)
        self.termination.check()
        return worker

    def _register_pipes(self, worker):
        # A failed pidfd/selector registration cannot strand a worker blocked
        # on its first bounded stdout message. finish retries this exact set.
        for stream, name in ((worker.stdout, "stdout"), (worker.stderr, "stderr")):
            if stream is None or stream.closed:
                continue
            try:
                self.selector.get_key(stream)
                continue
            except KeyError:
                pass
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ, (worker, name))

    def send(self, worker, op, *, enforce=True, **fields):
        if enforce:
            self.termination.check()
        app.require(worker in self.workers and type(op) is str and op in OPERATIONS, "APP_WORKER_COMMAND")
        app.require((set(fields) == {"case"} and type(fields["case"]) is str
                     and fields["case"] in (*CASES, *RECOVERY_CASES)) if op == "run" else not fields,
                    "APP_WORKER_COMMAND")
        worker.next_id += 1
        app.require(worker.next_id <= 64, "APP_WORKER_COMMAND_SEQUENCE")
        value = {"schema": 1, "id": worker.next_id, "op": op, **fields}
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        app.require(len(encoded) <= 1024, "APP_WORKER_COMMAND_BOUND")
        # These fixed commands fit PIPE_BUF and are sent atomically. A full or
        # broken channel is failure; cleanup can still request SIGTERM through
        # the live root pidfd without killing a worker retaining its lock.
        app.require(os.write(worker.command_fd, encoded) == len(encoded), "APP_WORKER_COMMAND_WRITE")
        return worker.next_id

    def pump(self, *, enforce=True):
        if enforce:
            self.termination.check()
        for key, _ in self.selector.select(0):
            worker, name = key.data
            try:
                data = os.read(key.fd, 16384)
            except BlockingIOError:
                continue
            if not data:
                self.selector.unregister(key.fd)
                if enforce and name == "stdout":
                    app.require(not worker.buffer, "APP_WORKER_TRUNCATED_MESSAGE")
                continue
            worker.output_bytes += len(data)
            if not enforce or name == "stderr":
                continue
            app.require(worker.output_bytes <= 4 * 1024 * 1024, "APP_WORKER_OUTPUT_LIMIT")
            worker.buffer.extend(data)
            while b"\n" in worker.buffer:
                line, _, remainder = worker.buffer.partition(b"\n")
                worker.buffer[:] = remainder
                worker.messages.append(_event(_decode(line)))
                app.require(len(worker.messages) <= MAX_QUEUE, "APP_WORKER_QUEUE_LIMIT")
            app.require(len(worker.buffer) <= MAX_MESSAGE, "APP_WORKER_MESSAGE_BOUND")

    def receive(self, worker, event, *, seconds=120, request_id=None):
        app.require(event in EVENTS and 0 < seconds <= 240, "APP_WORKER_WAIT")
        deadline = time.monotonic() + seconds
        while True:
            self.pump()
            if worker.messages:
                value = worker.messages.pop(0)
                app.require(value["event"] == event
                            and (request_id is None or value["id"] == request_id), "APP_WORKER_EVENT_ORDER")
                return value["data"]
            app.require(worker.process.poll() is None, "APP_WORKER_EARLY_EXIT")
            app.require(time.monotonic() < deadline, "APP_WORKER_TIMEOUT")
            time.sleep(.01)

    def finish(self):
        if self.closed:
            return dict(self.cleanup)
        try:
            for worker in self.workers:
                if worker.process is not None and worker.stdout is None:
                    worker.stdout, worker.stderr = worker.process.stdout, worker.process.stderr
                self._register_pipes(worker)
                if worker.process is None or worker.process.poll() is not None or worker.stop_requested:
                    continue
                try:
                    self.send(worker, "stop", enforce=False)
                    if self.termination.requested is not None and worker.pidfd is not None:
                        # An active target pair is not reading the command
                        # FIFO between every Wayland operation. Its own
                        # nonthrowing guard latches this request promptly.
                        try:
                            signal.pidfd_send_signal(worker.pidfd, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                except (OSError, ValueError):
                    if worker.pidfd is not None:
                        try:
                            signal.pidfd_send_signal(worker.pidfd, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    else:
                        # Pidfd registration itself may have failed after
                        # Popen. Retry opening this still-live owned child.
                        worker.pidfd = os.pidfd_open(worker.process.pid, 0)
                        signal.pidfd_send_signal(worker.pidfd, signal.SIGTERM)
                worker.stop_requested = True
            deadline = time.monotonic() + 5
            while any(w.process is not None and w.process.poll() is None for w in self.workers):
                self.pump(enforce=False)
                if time.monotonic() >= deadline:
                    raise CoordinatorPending(self)
                time.sleep(.01)
            # Every worker has released its own live owner and profile lock,
            # or exited unexpectedly. Only now may the coordinator finish its
            # full closure, including unexpected adopted descendants.
            self.cleanup = self.owner.finish(timeout=5.0, grace=1.5)
            if not self.cleanup["complete"]:
                raise CoordinatorPending(self)
            deadline = time.monotonic() + .5
            while self.selector.get_map() and time.monotonic() < deadline:
                self.pump(enforce=False)
                time.sleep(.01)
            if self.selector.get_map():
                raise CoordinatorPending(self)
        except (OSError, ValueError, app.owner_api.OwnershipError) as exc:
            raise CoordinatorPending(self) from exc
        try:
            for worker in self.workers:
                if worker.pidfd is not None:
                    os.close(worker.pidfd)
                    worker.pidfd = None
                if worker.command_fd is not None:
                    os.close(worker.command_fd)
                    worker.command_fd = None
                for stream in (worker.stdout, worker.stderr):
                    if stream is not None:
                        stream.close()
            self.selector.close()
            self.cleanup.update(root_pidfds_closed=True, output_pipes_eof=True,
                                workers_complete=True, worker_count=len(self.workers),
                                worker_returncodes={worker.role: worker.process.returncode
                                                    for worker in self.workers if worker.process is not None})
            self.closed = True
        finally:
            # This boundary is reached only after workers and every adopted
            # descendant are proven gone. A later descriptor-close failure
            # cannot leave process-global handlers installed indefinitely.
            self.termination.restore()
        return dict(self.cleanup)


def retry_cleanup(pending):
    app.require(isinstance(pending, CoordinatorPending), "APP_COORDINATOR_PENDING_HANDLE")
    return pending.coordinator.finish()


def _finish_coordinator(coordinator, result, artifact):
    """Keep the original failure and live handle even if recording fails."""
    journal_attempted = False
    while True:
        try:
            result["cleanup"] = coordinator.finish()
            break
        except CoordinatorPending as pending:
            app.require(pending.coordinator is coordinator, "APP_COORDINATOR_PENDING_HANDLE")
            result.update(state="fail", status="failed")
            result["error"] = result.get("error") or "APP_COORDINATOR_CLEANUP_PENDING"
            result["cleanup"] = coordinator.cleanup
            if not journal_attempted:
                journal_attempted = True
                try:
                    _write(Path(artifact) / "cleanup-pending.json", {"state": "pending", "error": result["error"]})
                except (OSError, ValueError):
                    result["receipt_write_failed"] = True
            time.sleep(.02)
    if coordinator.termination.requested is not None:
        result.update(state="fail", status="failed", termination_signal=coordinator.termination.requested)
        result["error"] = result.get("error") or "APP_WAYLAND_INTERRUPTED"
    if any(worker.process is not None and worker.process.returncode != 0 for worker in coordinator.workers):
        result.update(state="fail", status="failed")
        result["error"] = result.get("error") or "APP_WORKER_EXIT"


def _retain_run_cleanup(pending, artifact):
    """A worker keeps its exact process-local owner/guard/lock until reaped."""
    app.require(isinstance(pending, app.CleanupPending), "APP_WAYLAND_PENDING_HANDLE")
    receipt = getattr(pending, "receipt", {"state": "fail", "status": "failed",
                                         "error": "APP_WAYLAND_CLEANUP_PENDING"})
    try:
        _write(Path(artifact) / "cleanup-pending.json", {"state": "pending", "error": receipt["error"]})
    except (OSError, ValueError):
        receipt["receipt_write_failed"] = True
    while True:
        try:
            receipt["cleanup"] = app.retry_cleanup(pending)
            break
        except app.CleanupPending as retry:
            app.require(retry.run is pending.run, "APP_WAYLAND_PENDING_HANDLE")
            time.sleep(.02)
        except (OSError, ValueError):
            # Retry still owns the same process-local closure. A scan, drain,
            # or receipt I/O exception cannot terminate this worker instead.
            receipt["cleanup_io_failed"] = True
            time.sleep(.02)
    receipt["cleanup_after_pending"] = True
    return receipt


def _call_run(directory, bundle, profile, name, *, ending="close", exercise=False, on_ready=None):
    artifact = directory / "runs" / name
    artifact.mkdir(mode=0o700)
    try:
        receipt = app.run_case(ROOT, artifact, bundle, profile=profile,
                               ending=ending, exercise=exercise, on_ready=on_ready)
    except app.CleanupPending as pending:
        receipt = _retain_run_cleanup(pending, artifact)
    return receipt


def _clean(receipt):
    cleanup = receipt.get("cleanup") or {}
    return (cleanup.get("complete") is True and cleanup.get("pending") is False
            and cleanup.get("open_pidfds") == 0 and cleanup.get("root_pidfds_closed") is True
            and cleanup.get("output_pipes_eof") is True)


def _pair(directory, bundle, profile, case):
    app.require(case in CASES, "APP_WORKER_CASE")
    ending = case.removesuffix("_restart")
    first = _call_run(directory, bundle, profile, case + "-first", ending=ending, exercise=True)
    app.require(first.get("state") == "partial" and first.get("error") is None and _clean(first),
                "APP_RESTART_FIRST_FAILED")
    restarted = _call_run(directory, bundle, profile, case + "-restart")
    outcome = "restarted"
    if restarted.get("error") == "APP_STATE_RECOVERY_REQUIRED" and restarted.get("app_spawned") is False:
        outcome = "recovery_required"
    else:
        app.require(restarted.get("state") == "partial" and restarted.get("error") is None,
                    "APP_RESTART_FAILED")
    app.require(_clean(restarted), "APP_RESTART_CLEANUP")
    before, after = first["profile_after"], restarted["profile_after"]
    documents_preserved = (first["documents_before_termination"] == before["documents_sha256"]
                           == restarted["profile_before"]["documents_sha256"]
                           == after["documents_sha256"])
    settings_preserved = None
    if ending == "close":
        settings_preserved = (before["preferences_count"] > 0
            and before["preferences_sha256"] == restarted["profile_before"]["preferences_sha256"]
            == after["preferences_sha256"])
        app.require(outcome == "restarted" and settings_preserved, "APP_RESTART_SETTINGS")
    if outcome == "recovery_required":
        app.require(restarted["profile_before"]["tree_sha256"] == after["tree_sha256"],
                    "APP_RESTART_RECOVERY_CHANGED")
    app.require(documents_preserved, "APP_RESTART_DOCUMENTS")
    return {"first": _compact(first), "restart": _compact(restarted), "outcome": outcome,
            "documents_preserved": documents_preserved, "settings_preserved": settings_preserved}, first


def _seed(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _recovery_case(directory, bundle, name):
    app.require(name in RECOVERY_CASES, "APP_WORKER_RECOVERY_CASE")
    profile = app.SyntheticProfile.create(directory / ("recovery-" + name))
    settings = profile.root / "config/glib-2.0/settings"
    settings.parent.mkdir(mode=0o700)
    settings.mkdir(mode=0o700)
    value = b"['file:///eidolon-synthetic-recovery']" if name == "session" else b"[]"
    if name == "ambiguous":
        value = b"@unknown []"
    _seed(settings / "keyfile", b"[org/xfce/mousepad/state/application]\nsession=" + value
          + b"\n[org/xfce/mousepad/state/window]\nwidth=800\n")
    if name == "autosave":
        recovery = profile.root / "data/Mousepad"
        recovery.mkdir(mode=0o700)
        _seed(recovery / "synthetic-autosave", b"eidolon-preserved-recovery\n")
    _seed(profile.documents / "recovery-document.txt", b"eidolon-preserved-document\n")
    receipt = _call_run(directory, bundle, profile, "recovery-" + name)
    app.require(receipt.get("error") == "APP_STATE_RECOVERY_REQUIRED"
                and receipt.get("app_spawned") is False and _clean(receipt), "APP_RECOVERY_NOT_REFUSED")
    preserved = receipt["profile_before"]["tree_sha256"] == receipt["profile_after"]["tree_sha256"]
    app.require(preserved, "APP_RECOVERY_CHANGED")
    return {"run": _compact(receipt), "profile_preserved": preserved}


class _WorkerChannel:
    def __init__(self, directory):
        fifo = directory / "commands"
        info = fifo.lstat()
        app.require(stat.S_ISFIFO(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600, "APP_WORKER_FIFO")
        self.fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.buffer = bytearray()
        self.last_id = 0

    def read(self, pump):
        while True:
            pump()
            if b"\n" in self.buffer:
                line, _, remainder = self.buffer.partition(b"\n")
                self.buffer[:] = remainder
                app.require(len(line) <= 1024, "APP_WORKER_COMMAND_BOUND")
                value = _decode(line)
                app.require(isinstance(value, dict) and set(value) in
                            ({"schema", "id", "op"}, {"schema", "id", "op", "case"})
                            and type(value["schema"]) is int and value["schema"] == 1
                            and type(value["id"]) is int and value["id"] == self.last_id + 1
                            and value["id"] <= 64 and type(value["op"]) is str
                            and value["op"] in OPERATIONS
                            and (set(value) == {"schema", "id", "op", "case"}
                                 and type(value["case"]) is str and value["case"] in (*CASES, *RECOVERY_CASES)
                                 if value["op"] == "run" else set(value) == {"schema", "id", "op"}),
                            "APP_WORKER_COMMAND")
                self.last_id = value["id"]
                return value
            app.require(len(self.buffer) <= 1024, "APP_WORKER_COMMAND_BOUND")
            if not select.select([self.fd], [], [], .02)[0]:
                continue
            try:
                data = os.read(self.fd, 1024)
            except BlockingIOError:
                continue
            app.require(bool(data), "APP_WORKER_PARENT_EOF")
            self.buffer.extend(data)

    def emit(self, event, request_id, data):
        value = _event({"schema": 1, "event": event, "id": request_id, "data": data})
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        app.require(len(encoded) <= MAX_MESSAGE, "APP_WORKER_MESSAGE_BOUND")
        # Pipe failures are caught by run_case's finally, never a reason to
        # release a live run. Output is bounded fixed metadata only.
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()

    def close(self):
        os.close(self.fd)


def _observation(run, control, result, profile):
    response = app.driver.exercise_response(control)
    settled_profile = _stable_profile(run, control, profile)
    snapshot = control.snapshot()
    windows = app.driver.mapped(snapshot)
    app.require(len(windows) == 1 and windows[0]["window"] == control.main,
                "APP_PRECURSOR_TOPOLOGY")
    topology = [{key: row.get(key) for key in ("window", "parent", "app_id")} for row in windows]
    return {**{key: result[key] for key in ("run_id", "owner_scope_id", "profile_id", "runtime_id")},
            "main": control.main, "app_id": windows[0]["app_id"],
            "client_count": snapshot["counts"]["clients"],
            "topology_sha256": hashlib.sha256(json.dumps(topology, sort_keys=True).encode()).hexdigest(),
            "profile": settled_profile, "response": response}


def _stable_profile(run, control, profile):
    """Observe ordinary settings materialization before defining a baseline.

    Mousepad 0.6.3 schedules geometry persistence one second after configure:
    https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c#L1416-L1458
    Its fresh-profile defaults save width/height/maximized/fullscreen. A first
    mapped frame or responsive dialog is earlier than that asynchronous write.
    Require the observed four entries and stable bytes, rather than certifying
    an elapsed sleep. All later comparisons still require identical raw
    settings, ordinary settings and documents across the target's lifetime.
    """
    deadline = min(run.deadline, time.monotonic() + 8)
    candidate, stable_since = None, None
    while True:
        run.pump()
        try:
            current = profile.snapshot(run.profile_lock)
        except FileNotFoundError:
            # Optional files may be created/atomically replaced by this live
            # app during its first flush. A failed sample is never stability.
            current = None
        except app.WaylandError as exc:
            if str(exc) != "APP_WAYLAND_PROFILE_SNAPSHOT_CHANGED":
                raise
            current = None
        now = time.monotonic()
        ready = (current is not None and current["settings_present"] is True
                 and current["preferences_count"] >= 4 and current["preferences_sha256"] is not None)
        if not ready or current != candidate:
            candidate, stable_since = (current, now) if ready else (None, None)
        elif now - stable_since >= 1.1:
            return current
        app.require(now < deadline, "APP_PRECURSOR_SETTINGS_NOT_STABLE")
        # Control.settle dispatches Wayland and validates the latched main
        # topology. The run's guard, output limits and deadline remain active.
        control.settle(min(.05, deadline - now))


def _worker(role, directory, bundle):
    app.require(role in ("precursor", "target"), "APP_WORKER_ROLE")
    directory, bundle = Path(directory), Path(bundle)
    app._private(directory)
    app._private(bundle)
    os.umask(0o077)
    guard = app._TerminationGuard()
    channel = None
    try:
        channel = _WorkerChannel(directory)
        (directory / "runs").mkdir(mode=0o700)
        worker_id = uuid.uuid4().hex
        if role == "precursor":
            profile = app.SyntheticProfile.create(directory / "profile")
            def hold(run, control, result):
                # This one live precursor spans all target cases. Its budget
                # remains finite, while every target run keeps its own budget.
                run.deadline = time.monotonic() + 360
                initial = _observation(run, control, result, profile)
                channel.emit("ready", 0, {"worker_id": worker_id, "observation": initial})
                while True:
                    command = channel.read(run.pump)
                    if command["op"] == "observe" and "case" not in command:
                        channel.emit("observation", command["id"], _observation(run, control, result, profile))
                    elif command["op"] == "close" and "case" not in command:
                        return
                    elif command["op"] == "stop" and "case" not in command:
                        raise app.WaylandError("APP_WORKER_STOP_REQUESTED")
                    else:
                        raise app.WaylandError("APP_WORKER_COMMAND")
            receipt = _call_run(directory, bundle, profile, "precursor", on_ready=hold)
            channel.emit("done", channel.last_id, {"worker_id": worker_id, "run": _compact(receipt)})
            return 0 if receipt.get("state") == "partial" else 1
        channel.emit("ready", 0, {"worker_id": worker_id})
        expected = list(CASES) + list(RECOVERY_CASES)
        while True:
            command = channel.read(guard.check)
            if command["op"] in ("close", "stop") and "case" not in command:
                if command["op"] == "close":
                    app.require(not expected, "APP_WORKER_CASES_INCOMPLETE")
                channel.emit("done", command["id"], {"worker_id": worker_id, "state": "complete"})
                return 0
            app.require(command["op"] == "run" and expected and command.get("case") == expected[0],
                        "APP_WORKER_CASE_ORDER")
            case = expected.pop(0)
            if case in CASES:
                # Endings are independent experiments. Reuse one profile
                # within each pair, while preserving a protected post-Stop
                # profile without blocking the separate crash experiment.
                profile = app.SyntheticProfile.create(directory / ("profile-" + case))
                result, first = _pair(directory, bundle, profile, case)
                data = {"name": case, "case": result}
                if case == "close_restart":
                    keys = ("evidence", "recovery_guard", "mousepad_sha256", "waypipe_sha256", "waypipe_version",
                            "waypipe_ends", "private_bus", "profile_kind", "source_argv")
                    data["foundation"] = {key: first[key] for key in keys}
            else:
                data = {"name": case, "case": _recovery_case(directory, bundle, case)}
            channel.emit("case", command["id"], data)
    finally:
        if channel is not None:
            channel.close()
        guard.restore()


def run_suite(repo, artifact, settings_bundle):
    repo, artifact, settings_bundle = map(Path, (repo, artifact, settings_bundle))
    app.require(repo.resolve() == ROOT and artifact.is_absolute(), "APP_WAYLAND_ARGUMENT")
    app._private(artifact)
    app.require(not any(artifact.iterdir()), "APP_WAYLAND_ARTIFACT_NOT_EMPTY")
    result = {"schema": 1, "stage": "wayland", "state": "fail", "status": "failed", "error": None,
              "feasibility_verified": False, "physical_input_verified": False,
              "pending": list(app.REQUIRED_CHECKS), "checks": {key: False for key in app.REQUIRED_CHECKS},
              "payload_logged": False, "titles_logged": False, "pixels_logged": False, "production_ready": False,
              "lifecycle": {"schema": 1, "cases": {}, "recovery": {}, "independence": {}}}
    coordinator = None
    try:
        coordinator = _Coordinator(artifact, settings_bundle)
        precursor = coordinator.spawn("precursor")
        ready = coordinator.receive(precursor, "ready", request_id=0)
        independent = result["lifecycle"]["independence"]
        independent.update(precursor_worker_id=ready["worker_id"], precursor_initial=ready["observation"])
        target = coordinator.spawn("target")
        ready = coordinator.receive(target, "ready", request_id=0)
        independent["target_worker_id"] = ready["worker_id"]
        app.require(independent["precursor_worker_id"] != independent["target_worker_id"], "APP_WORKER_IDENTITY")
        for case in (*CASES, *RECOVERY_CASES):
            request_id = coordinator.send(target, "run", case=case)
            event = coordinator.receive(target, "case", request_id=request_id, seconds=240)
            app.require(event.get("name") == case, "APP_WORKER_CASE_ORDER")
            group = "cases" if case in CASES else "recovery"
            result["lifecycle"][group][case] = event["case"]
            if case == "close_restart":
                result.update(event["foundation"])
            if case in ("close_restart", "stop_restart"):
                request_id = coordinator.send(precursor, "observe")
                observation = coordinator.receive(precursor, "observation", request_id=request_id)
                independent["after_close" if case == "close_restart" else "after_stop"] = observation
                initial = independent["precursor_initial"]
                app.require(all(initial[key] == observation[key] for key in
                                ("run_id", "owner_scope_id", "profile_id", "runtime_id", "main",
                                 "app_id", "client_count", "topology_sha256")), "APP_PRECURSOR_CHANGED")
                app.require(all(initial["profile"][key] == observation["profile"][key] for key in
                                ("documents_sha256", "settings_sha256", "preferences_sha256")),
                            "APP_PRECURSOR_SETTINGS_CHANGED")
        # This receipt is written while the precursor still owns live GUI
        # processes. Any open/write/fsync failure takes the same finally path.
        _write(artifact / "independence.json", independent)
        request_id = coordinator.send(target, "close")
        coordinator.receive(target, "done", request_id=request_id)
        request_id = coordinator.send(precursor, "close")
        final = coordinator.receive(precursor, "done", request_id=request_id)
        independent["precursor_final"] = final["run"]
        app.require(final["run"]["state"] == "partial" and final["run"]["error"] is None
                    and _clean(final["run"]), "APP_PRECURSOR_CLEANUP")
        result["checks"].update({key: True for key in app.REQUIRED_CHECKS
                                  if key not in ("gui_setting_persistence", "protected_settings_gui_readonly")})
        result.update(state="partial", status="incomplete")
    except (app.WaylandError, app.driver.ProbeError, app.owner_api.OwnershipError) as exc:
        token = str(exc)
        result["error"] = token if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", token) else "APP_HARNESS_FAILED"
    except (OSError, ValueError, KeyError, TypeError):
        result["error"] = "APP_HARNESS_DEPENDENCY_OR_IO"
    finally:
        if coordinator is not None:
            _finish_coordinator(coordinator, result, artifact)
            result["output_bytes"] = {worker.role: worker.output_bytes for worker in coordinator.workers}
        result["pending"] = [key for key in app.REQUIRED_CHECKS if not result["checks"][key]]
        _write(artifact / "wayland.json", result)
    return result


def run_feasibility(repo, artifact, settings_bundle):
    return run_suite(repo, artifact, settings_bundle)


if __name__ == "__main__":
    try:
        app.require(len(sys.argv) == 5 and sys.argv[1] == "--worker", "APP_WORKER_INTERNAL_ONLY")
        raise SystemExit(_worker(sys.argv[2], sys.argv[3], sys.argv[4]))
    except (OSError, ValueError, app.WaylandError, app.driver.ProbeError, app.owner_api.OwnershipError):
        print("APP_WORKER_FAILED", file=sys.stderr)
        raise SystemExit(1)
