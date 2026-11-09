"""Real process boundaries for the fixed A0 coordinator and its workers.

These tests use finite synthetic children, never Mousepad, a user profile, or
saved PIDs as signal targets. Actual GUI evidence belongs to the separate CI
feasibility run. Every ProcessOwner runs in a dedicated non-root subprocess.
"""
import errno
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_harness as harness
import linux_real_app_wayland as app

SELF = str(Path(__file__).resolve())
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
CASES = {"spawn_handoff", "target_scope", "pending_io"}


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def line(stream, timeout=8):
    if not select.select([stream], [], [], timeout)[0]:
        raise RuntimeError("HARNESS_TEST_TIMEOUT")
    value = stream.readline(32769)
    if not value or len(value) > 32768:
        raise RuntimeError("HARNESS_TEST_OUTPUT")
    return value


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError("HARNESS_TEST_TIMEOUT")
        time.sleep(.01)


def synthetic_child():
    # The natural lifetime is an independent safety net for a broken owner.
    # The tests require prompt proven cleanup, not eventual natural expiry.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    read_fd, write_fd = os.pipe()
    if os.fork() == 0:
        os.close(read_fd)
        os.setsid()
        if os.fork() != 0:
            os._exit(0)
        os.write(write_fd, b"R")
        os.close(write_fd)
        time.sleep(18)
        os._exit(0)
    os.close(write_fd)
    with os.fdopen(read_fd, "rb", buffering=0) as reader:
        if not select.select([reader], [], [], 2)[0] or reader.read(1) != b"R":
            os._exit(3)
    emit({"ready": True})
    time.sleep(18)
    os._exit(0)


def descendant_rows(run):
    deadline = time.monotonic() + 1
    while True:
        try:
            return app.owner_api.descendant_snapshot(run.owner.owner)
        except (FileNotFoundError, ProcessLookupError, app.owner_api.OwnershipError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.01)


def synthetic_worker(role, directory, bundle, case):
    """A fixed test actor with the production coordinator protocol/ownership."""
    directory = Path(directory)
    release = directory / "release"
    previous = {number: signal.getsignal(number) for number in SIGNALS}
    guard = app._TerminationGuard()
    run = app._Run(directory, guard)
    lock_path = directory / "test-profile.lock"
    command_fd = None
    completed = False
    try:
        run.profile_lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC, 0o600)
        fcntl.flock(run.profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        child = run.spawn("synthetic", [sys.executable, "-I", "-S", "-B", SELF, "--child"],
                          ENV, control=True)
        if json.loads(line(child.stdout, 3)) != {"ready": True}:
            raise RuntimeError("HARNESS_TEST_CHILD_READY")
        rows = descendant_rows(run)
        observed = len(rows)
        escaped = any(row["session"] != child.pid for row in rows.values())
        data = {"lock": str(lock_path), "observed": observed, "escaped": escaped}
        command_fd = os.open(directory / "commands", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        emit({"schema": 1, "event": "ready", "id": 0, "data": data})
        (directory / "test-ready").touch(exist_ok=False)
        command_id = 0
        buffer = b""
        while guard.requested is None:
            if not select.select([command_fd], [], [], .02)[0]:
                continue
            raw = os.read(command_fd, 4096)
            if not raw:
                raise RuntimeError("HARNESS_TEST_COMMAND_EOF")
            buffer += raw
            if len(buffer) > 8192:
                raise RuntimeError("HARNESS_TEST_COMMAND_LIMIT")
            if b"\n" not in buffer:
                continue
            raw, buffer = buffer.split(b"\n", 1)
            command = json.loads(raw)
            command_id = command["id"]
            if command["op"] == "observe":
                emit({"schema": 1, "event": "observation", "id": command_id,
                      "data": {"responsive": child.poll() is None,
                               "lock_held": run.profile_lock is not None}})
            elif command["op"] in {"stop", "close"}:
                break
            else:
                raise RuntimeError("HARNESS_TEST_COMMAND")

        pending_identity = False
        failed_receipt_preserved = False
        if case == "pending_io" and role == "precursor":
            actual_finish = run.owner.finish
            actual_scan = app.owner_api.descendant_snapshot
            injection_deadline = time.monotonic() + 12

            def incomplete_finish(**_kwargs):
                if release.exists() or time.monotonic() >= injection_deadline:
                    return actual_finish(timeout=5, grace=.05)
                scans = 0

                def incomplete_scan(*args, **kwargs):
                    nonlocal scans
                    scans += 1
                    if scans == 1:
                        return actual_scan(*args, **kwargs)
                    raise app.owner_api.OwnershipError("PROCESS_SCAN_LIMIT")

                with mock.patch.object(app.owner_api, "descendant_snapshot", side_effect=incomplete_scan):
                    return actual_finish(timeout=.04, grace=.03)

            with mock.patch.object(run.owner, "finish", side_effect=incomplete_finish):
                try:
                    run.finish()
                except app.CleanupPending as caught:
                    pending = caught
                else:
                    raise RuntimeError("HARNESS_TEST_PENDING_MISSING")
                pending.receipt = {"state": "fail", "status": "failed",
                                   "error": "APP_WAYLAND_CLEANUP_PENDING"}
                pending_identity = pending.run is run
                (directory / "test-pending").touch(exist_ok=False)

                def no_space(*_args, **_kwargs):
                    (directory / "test-receipt-failed").touch(exist_ok=True)
                    raise OSError(errno.ENOSPC, "synthetic")

                with mock.patch.object(harness, "_write", side_effect=no_space):
                    harness._retain_run_cleanup(pending, directory)
                failed_receipt_preserved = pending.receipt["error"] == "APP_WAYLAND_CLEANUP_PENDING"
            cleanup = run.cleanup
        else:
            cleanup = run.finish()
        completed = True
        data = {"observed": observed, "escaped": escaped, "cleanup": cleanup,
                "descendants_empty": not descendant_rows(run),
                "root_reaped": child.returncode is not None, "root_pidfds_remaining": len(run.pidfds),
                "same_pending_owner": pending_identity, "failure_preserved": failed_receipt_preserved,
                "guard_restored": {number: signal.getsignal(number) for number in SIGNALS} == previous}
        (directory / "test-complete.json").write_text(json.dumps(data), encoding="utf-8")
        emit({"schema": 1, "event": "done", "id": command_id, "data": data})
    finally:
        if command_fd is not None:
            os.close(command_fd)
        if not completed:
            # Injections have unwound; only the live worker completes cleanup.
            while True:
                try:
                    run.finish()
                    break
                except app.CleanupPending:
                    time.sleep(.05)


def coordinator_worker(case):
    previous = {number: signal.getsignal(number) for number in SIGNALS}
    coordinator = None
    cleanup_finished = False
    with tempfile.TemporaryDirectory(prefix="eidolon-l7a-coordinator-test-") as temporary:
        base = Path(temporary)
        artifact, bundle = base / "artifact", base / "bundle"
        artifact.mkdir(mode=0o700)
        bundle.mkdir(mode=0o700)

        def fixed_command(role, directory, settings_bundle):
            return [sys.executable, "-I", "-S", "-B", SELF, "--synthetic-worker", role,
                    str(directory), str(settings_bundle), case]

        try:
            with mock.patch.object(harness, "_worker_command", side_effect=fixed_command):
                coordinator = harness._Coordinator(artifact, bundle)
                if case == "spawn_handoff":
                    actual_popen = app.owner_api.subprocess.Popen

                    def signalled_popen(*args, **kwargs):
                        child = actual_popen(*args, **kwargs)
                        command = args[0]
                        directory = Path(command[command.index("--synthetic-worker") + 2])
                        until(lambda: (directory / "test-ready").exists(), 4)
                        os.kill(os.getpid(), signal.SIGTERM)
                        return child

                    with mock.patch.object(app.owner_api.subprocess, "Popen", side_effect=signalled_popen):
                        try:
                            coordinator.spawn("precursor")
                        except app.WaylandError as error:
                            if "INTERRUPTED" not in str(error):
                                raise
                        else:
                            raise RuntimeError("HARNESS_TEST_INTERRUPT_MISSING")
                    first = coordinator.workers[0]
                    observations = {"registered_workers": len(coordinator.workers),
                                    "registered_roots": len(coordinator.owner.roots),
                                    "live_pidfd": isinstance(first.pidfd, int)}
                    emit({"event": "owned", "locks": [str(first.directory / "test-profile.lock")]})
                    if line(sys.stdin.buffer) != b"finish\n":
                        raise RuntimeError("HARNESS_TEST_FINISH_COMMAND")
                else:
                    first = coordinator.spawn("precursor")
                    coordinator.receive(first, "ready", seconds=6)
                    target = coordinator.spawn("target")
                    coordinator.receive(target, "ready", seconds=6)
                    observations = {}
                    emit({"event": "owned",
                          "locks": [str(worker.directory / "test-profile.lock") for worker in (first, target)],
                          "release": str(first.directory / "release"),
                          "pending": str(first.directory / "test-pending"),
                          "worker_receipt_failed": str(first.directory / "test-receipt-failed"),
                          "coordinator_receipt_failed": str(artifact / "test-receipt-failed")})
                    if case == "target_scope":
                        if line(sys.stdin.buffer) != b"act\n":
                            raise RuntimeError("HARNESS_TEST_ACTION_COMMAND")
                        coordinator.send(target, "stop")
                        coordinator.receive(target, "done", seconds=6)
                        coordinator.send(first, "observe")
                        reply = coordinator.receive(first, "observation", seconds=3)
                        observations["precursor_responsive"] = reply["responsive"]
                        emit({"event": "target_stopped", "precursor_responsive": observations["precursor_responsive"]})
                        if line(sys.stdin.buffer) != b"finish\n":
                            raise RuntimeError("HARNESS_TEST_FINISH_COMMAND")
                    else:
                        try:
                            while True:
                                coordinator.termination.check()
                                time.sleep(.01)
                        except app.WaylandError:
                            pass

                result = {"state": "fail", "status": "failed", "error": "TEST_ORIGINAL_FAILURE"}
                if case == "pending_io":
                    def io_failure(*_args, **_kwargs):
                        (artifact / "test-receipt-failed").touch(exist_ok=True)
                        raise OSError(errno.EIO, "synthetic")
                    with mock.patch.object(harness, "_write", side_effect=io_failure):
                        harness._finish_coordinator(coordinator, result, artifact)
                else:
                    harness._finish_coordinator(coordinator, result, artifact)
                cleanup_finished = True
                completed_workers = [json.loads((worker.directory / "test-complete.json").read_bytes())
                                     for worker in coordinator.workers]
                observations.update(event="complete", result=result, workers=completed_workers,
                                    descendants_empty=not app.owner_api.descendant_snapshot(coordinator.owner.owner),
                                    all_workers_reaped=all(worker.process.returncode == 0 for worker in coordinator.workers),
                                    guard_restored={number: signal.getsignal(number) for number in SIGNALS} == previous,
                                    requested=coordinator.termination.requested)
                emit(observations)
                if line(sys.stdin.buffer) != b"exit\n":
                    raise RuntimeError("HARNESS_TEST_EXIT_COMMAND")
        finally:
            if coordinator is not None and not cleanup_finished:
                for worker in coordinator.workers:
                    (worker.directory / "release").touch(exist_ok=True)
                harness._finish_coordinator(coordinator, {"state": "fail", "error": "TEST_FAILED"}, artifact)


class RealCoordinatorLifecycleTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != "linux" or os.getuid() == 0:
            reason = "real coordinator lifecycle requires a non-root Linux helper"
            if os.environ.get("EIDOLON_REQUIRE_LIFECYCLE_TESTS") == "1":
                self.fail(reason)
            self.skipTest(reason)

    def lock_probe(self, path, *, held):
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if held:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def exercise(self, case):
        helper = subprocess.Popen([sys.executable, "-I", "-S", "-B", SELF, "--coordinator", case],
                                  env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        release = None
        try:
            initial = json.loads(line(helper.stdout, 10))
            self.assertEqual(initial["event"], "owned")
            for lock in initial["locks"]:
                self.lock_probe(lock, held=True)
            if case == "spawn_handoff":
                helper.stdin.write(b"finish\n")
                helper.stdin.flush()
            elif case == "target_scope":
                helper.stdin.write(b"act\n")
                helper.stdin.flush()
                stopped = json.loads(line(helper.stdout, 8))
                self.assertEqual(stopped, {"event": "target_stopped", "precursor_responsive": True})
                self.lock_probe(initial["locks"][0], held=True)
                self.lock_probe(initial["locks"][1], held=False)
                helper.stdin.write(b"finish\n")
                helper.stdin.flush()
            else:
                release = Path(initial["release"])
                helper.send_signal(signal.SIGTERM)
                until(lambda: Path(initial["pending"]).exists(), 5)
                until(lambda: Path(initial["worker_receipt_failed"]).exists(), 3)
                # Force a coordinator CleanupPending retry and its journal
                # error while the real worker still retains its profile lock.
                until(lambda: Path(initial["coordinator_receipt_failed"]).exists(), 8)
                for number in (signal.SIGINT, signal.SIGHUP):
                    helper.send_signal(number)
                    time.sleep(.05)
                    self.assertIsNone(helper.poll())
                    self.lock_probe(initial["locks"][0], held=True)
                release.touch(exist_ok=False)
            value = json.loads(line(helper.stdout, 10))
            self.assertEqual(value["event"], "complete")
            for lock in initial["locks"]:
                self.lock_probe(lock, held=False)
            helper.stdin.write(b"exit\n")
            helper.stdin.flush()
            output, diagnostics = helper.communicate(timeout=4)
            self.assertEqual(helper.returncode, 0, diagnostics.decode(errors="replace"))
            self.assertEqual(output, b"")
            self.assertTrue(value["descendants_empty"])
            self.assertTrue(value["all_workers_reaped"])
            self.assertTrue(value["guard_restored"])
            self.assertEqual(value["result"]["error"], "TEST_ORIGINAL_FAILURE")
            self.assertTrue(value["result"]["cleanup"]["complete"])
            self.assertFalse(value["result"]["cleanup"]["pending"])
            for worker in value["workers"]:
                self.assertGreaterEqual(worker["observed"], 2)
                self.assertTrue(worker["escaped"])
                self.assertTrue(worker["cleanup"]["complete"])
                self.assertTrue(worker["descendants_empty"])
                self.assertTrue(worker["root_reaped"])
                self.assertEqual(worker["root_pidfds_remaining"], 0)
                self.assertTrue(worker["guard_restored"])
            return value
        except Exception as error:
            if helper.poll() is not None:
                _, diagnostics = helper.communicate(timeout=2)
                raise AssertionError(str(error) + "; helper: " + diagnostics[:8192].decode(errors="replace")) from error
            raise
        finally:
            if release is not None and helper.poll() is None:
                try:
                    release.touch(exist_ok=True)
                except FileNotFoundError:
                    pass
            if helper.poll() is None:
                try:
                    helper.communicate(b"act\nfinish\nexit\n", timeout=28)
                except (BrokenPipeError, ValueError):
                    helper.wait(timeout=28)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.communicate(timeout=2)
            for stream in (helper.stdin, helper.stdout, helper.stderr):
                stream.close()

    def test_signal_during_worker_popen_handoff_retains_registered_worker(self):
        value = self.exercise("spawn_handoff")
        self.assertEqual(value["registered_workers"], 1)
        self.assertEqual(value["registered_roots"], 1)
        self.assertTrue(value["live_pidfd"])
        self.assertEqual(value["requested"], signal.SIGTERM)

    def test_target_stop_preserves_precursor_worker_and_ownership_scope(self):
        value = self.exercise("target_scope")
        self.assertTrue(value["precursor_responsive"])
        self.assertEqual(len(value["workers"]), 2)

    def test_coordinator_retains_pending_worker_through_signals_and_two_receipt_failures(self):
        value = self.exercise("pending_io")
        self.assertEqual(value["requested"], signal.SIGTERM)
        self.assertTrue(value["result"]["receipt_write_failed"])
        self.assertTrue(value["workers"][0]["same_pending_owner"])
        self.assertTrue(value["workers"][0]["failure_preserved"])


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--child":
        synthetic_child()
    elif len(sys.argv) == 3 and sys.argv[1] == "--coordinator" and sys.argv[2] in CASES:
        coordinator_worker(sys.argv[2])
    elif len(sys.argv) == 6 and sys.argv[1] == "--synthetic-worker" and sys.argv[5] in CASES:
        synthetic_worker(*sys.argv[2:])
    else:
        unittest.main()
