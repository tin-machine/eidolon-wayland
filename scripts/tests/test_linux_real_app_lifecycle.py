"""Real termination/ownership regressions; no installed GUI app is invoked.

Each owner lives in a dedicated non-root helper. Fixed synthetic children have
an eight-second natural lifetime, including descendants which escape their
root's session. The parent independently probes the profile flock.
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
import linux_real_app_check as check
import linux_real_app_wayland as wayland

SELF = str(Path(__file__).resolve())
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
CASES = {"spawn_handoff", "running", "pending_journal"}


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def line(stream, timeout=4):
    if not select.select([stream], [], [], timeout)[0]:
        raise RuntimeError("LIFECYCLE_FIXTURE_TIMEOUT")
    value = stream.readline(8193)
    if not value or len(value) > 8192:
        raise RuntimeError("LIFECYCLE_FIXTURE_OUTPUT")
    return value


def synthetic_child(tree):
    # Safety net independent of the process under test: descendants naturally
    # exit even if a broken owner is terminated by the test's outer timeout.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    if tree:
        read_fd, write_fd = os.pipe()
        if os.fork() == 0:
            os.close(read_fd)
            os.setsid()
            if os.fork() != 0:
                os._exit(0)
            os.write(write_fd, b"R")
            os.close(write_fd)
            time.sleep(8)
            os._exit(0)
        os.close(write_fd)
        with os.fdopen(read_fd, "rb", buffering=0) as reader:
            if not select.select([reader], [], [], 2)[0] or reader.read(1) != b"R":
                os._exit(3)
    emit({"ready": True})
    time.sleep(8)
    os._exit(0)


def fixed_child(tree=False):
    return [sys.executable, "-I", "-S", "-B", SELF, "--child", "tree" if tree else "plain"]


def child_ready(child):
    if json.loads(line(child.stdout)) != {"ready": True}:
        raise RuntimeError("LIFECYCLE_CHILD_NOT_READY")


def descendants(run):
    deadline = time.monotonic() + 1
    while True:
        try:
            return wayland.owner_api.descendant_snapshot(run.owner.owner)
        except (FileNotFoundError, ProcessLookupError, wayland.owner_api.OwnershipError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.01)


def worker(case):
    previous = {number: signal.getsignal(number) for number in SIGNALS}
    run = None
    guard = None
    with tempfile.TemporaryDirectory(prefix="eidolon-l7a-lifecycle-") as temporary:
        artifact = Path(temporary)
        release = artifact / "release"
        lock_path = artifact / "profile.lock"
        try:
            guard = wayland._TerminationGuard()
            run = wayland._Run(artifact, guard)
            run.profile_lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC, 0o600)
            fcntl.flock(run.profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observations = {}
            if case == "spawn_handoff":
                actual_popen = wayland.owner_api.subprocess.Popen

                def signalled_popen(*args, **kwargs):
                    child = actual_popen(*args, **kwargs)
                    child_ready(child)
                    # A real signal precisely between Popen completion and
                    # ProcessOwner's roots registration. Raising in the signal
                    # handler would abandon this ownership handoff.
                    os.kill(os.getpid(), signal.SIGTERM)
                    return child

                with mock.patch.object(wayland.owner_api.subprocess, "Popen", side_effect=signalled_popen):
                    try:
                        run.spawn("synthetic", fixed_child(), ENV)
                    except wayland.WaylandError as error:
                        if str(error) != "APP_WAYLAND_INTERRUPTED":
                            raise
                    else:
                        raise RuntimeError("LIFECYCLE_INTERRUPTION_MISSING")
                observations.update(registered_roots=len(run.owner.roots),
                                    registered_pidfds=len(run.pidfds), registered_pipes=len(run.pipes))
                emit({"event": "owned", "lock": str(lock_path)})
                if line(sys.stdin.buffer) != b"finish\n":
                    raise RuntimeError("LIFECYCLE_HANDOFF_COMMAND")
                cleanup = run.finish()
            else:
                child = run.spawn("synthetic", fixed_child(tree=True), ENV, control=True)
                child_ready(child)
                rows = descendants(run)
                observations.update(observed=len(rows),
                                    escaped_session=any(row["session"] != child.pid for row in rows.values()),
                                    escaped_group=any(row["group"] != child.pid for row in rows.values()))
                if case == "running":
                    emit({"event": "owned", "lock": str(lock_path)})
                    try:
                        while True:
                            run.pump()
                            time.sleep(.01)
                    except wayland.WaylandError as error:
                        if str(error) != "APP_WAYLAND_INTERRUPTED":
                            raise
                    cleanup = run.finish()
                else:
                    actual_finish = run.owner.finish
                    actual_scan = wayland.owner_api.descendant_snapshot
                    # Bound failure injection independently of parent progress.
                    # This deadline and child expiry keep failing tests finite.
                    injection_deadline = time.monotonic() + 5
                    termination_acknowledged = False

                    def incomplete_finish(**_kwargs):
                        nonlocal termination_acknowledged
                        if guard.requested is not None and not termination_acknowledged:
                            termination_acknowledged = True
                            emit({"event": "termination_latched", "signal": guard.requested})
                        if release.exists() or time.monotonic() >= injection_deadline:
                            return actual_finish(timeout=5, grace=.05)
                        scans = 0

                        def missing_proof(*args, **kwargs):
                            nonlocal scans
                            scans += 1
                            if scans == 1:
                                return actual_scan(*args, **kwargs)
                            raise wayland.owner_api.OwnershipError("PROCESS_SCAN_LIMIT")

                        with mock.patch.object(wayland.owner_api, "descendant_snapshot", side_effect=missing_proof):
                            return actual_finish(timeout=.04, grace=.03)

                    with mock.patch.object(run.owner, "finish", side_effect=incomplete_finish):
                        try:
                            run.finish()
                        except wayland.CleanupPending as caught:
                            pending = caught
                        else:
                            raise RuntimeError("LIFECYCLE_PENDING_MISSING")
                        pending.receipt = {"status": "failed", "error": "APP_WAYLAND_CLEANUP_PENDING"}
                        observations["same_live_owner"] = pending.run is run
                        observations["pending_pidfds"] = run.cleanup["open_pidfds"]
                        emit({"event": "pending", "lock": str(lock_path), "release": str(release)})
                        with mock.patch.object(check, "write_json", side_effect=OSError(errno.ENOSPC, "synthetic")) as writer:
                            cleanup = check.retain_cleanup_owner(pending, artifact)
                        observations["journal_attempted"] = writer.call_count == 1
                        observations["failure_preserved"] = pending.receipt == {
                            "status": "failed", "error": "APP_WAYLAND_CLEANUP_PENDING"}
            observations.update(event="complete", cleanup=cleanup, requested=guard.requested,
                                descendants_empty=not descendants(run),
                                root_reaped=all(child.returncode is not None for child in run.owner.roots.values()),
                                root_pidfds_remaining=len(run.pidfds),
                                restored={number: signal.getsignal(number) for number in SIGNALS} == previous)
            emit(observations)
            # Keep the lock pathname alive while the independent parent proves
            # acquisition is possible after cleanup and before artifact removal.
            if line(sys.stdin.buffer) != b"exit\n":
                raise RuntimeError("LIFECYCLE_EXIT_COMMAND")
        finally:
            # Patches have unwound, so failure recovery uses the actual owner.
            # Signals remain latched until that owner's real cleanup completes.
            if run is not None and not (run.cleanup and run.cleanup.get("complete")):
                for _ in range(3):
                    try:
                        run.finish()
                        break
                    except wayland.CleanupPending:
                        continue
            elif run is None and guard is not None:
                guard.restore()


class RealLifecycleTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != "linux" or os.getuid() == 0:
            reason = "real ProcessOwner lifecycle requires a non-root Linux helper"
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
        helper = subprocess.Popen([sys.executable, "-I", "-S", "-B", SELF, "--worker", case],
                                  env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        release = None
        try:
            first = json.loads(line(helper.stdout, 6))
            self.assertEqual(first["event"], "pending" if case == "pending_journal" else "owned")
            self.lock_probe(first["lock"], held=True)
            if case == "spawn_handoff":
                helper.stdin.write(b"finish\n")
                helper.stdin.flush()
            elif case == "running":
                helper.send_signal(signal.SIGTERM)
            else:
                release = Path(first["release"])
                helper.send_signal(signal.SIGTERM)
                acknowledged = json.loads(line(helper.stdout))
                self.assertEqual(acknowledged, {"event": "termination_latched", "signal": signal.SIGTERM})
                for number in (signal.SIGINT, signal.SIGHUP):
                    helper.send_signal(number)
                    time.sleep(.05)
                    self.assertIsNone(helper.poll())
                    self.lock_probe(first["lock"], held=True)
                release.touch(exist_ok=False)
            value = json.loads(line(helper.stdout, 8))
            self.assertEqual(value["event"], "complete")
            self.lock_probe(first["lock"], held=False)
            helper.stdin.write(b"exit\n")
            helper.stdin.flush()
            output, error = helper.communicate(timeout=4)
            self.assertEqual(helper.returncode, 0, error.decode(errors="replace"))
            self.assertEqual(output, b"")
            self.assertTrue(value["cleanup"]["complete"])
            self.assertFalse(value["cleanup"]["pending"])
            self.assertEqual(value["cleanup"]["open_pidfds"], 0)
            self.assertTrue(value["cleanup"]["root_pidfds_closed"])
            self.assertTrue(value["cleanup"]["output_pipes_eof"])
            self.assertTrue(value["descendants_empty"])
            self.assertTrue(value["root_reaped"])
            self.assertEqual(value["root_pidfds_remaining"], 0)
            self.assertTrue(value["restored"])
            self.assertEqual(value["requested"], signal.SIGTERM)
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
                # Allow finite injection/child deadlines and the helper's own
                # finally cleanup to finish before a last-resort owner kill.
                try:
                    helper.communicate(b"exit\n", timeout=18)
                except (BrokenPipeError, ValueError):
                    helper.wait(timeout=18)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.communicate(timeout=2)
            for stream in (helper.stdin, helper.stdout, helper.stderr):
                stream.close()

    def test_signal_during_popen_handoff_finishes_root_registration_before_cleanup(self):
        value = self.exercise("spawn_handoff")
        self.assertEqual(value["registered_roots"], 1)
        self.assertEqual(value["registered_pidfds"], 1)
        self.assertEqual(value["registered_pipes"], 2)

    def test_sigterm_reaps_double_fork_and_setsid_descendants_before_unlock(self):
        value = self.exercise("running")
        self.assertGreaterEqual(value["observed"], 2)
        self.assertTrue(value["escaped_session"])
        self.assertTrue(value["escaped_group"])
        self.assertGreaterEqual(value["cleanup"]["reaped"], 2)

    def test_pending_owner_keeps_lock_through_repeated_signals_and_failed_journal(self):
        value = self.exercise("pending_journal")
        self.assertTrue(value["same_live_owner"])
        self.assertGreater(value["pending_pidfds"], 0)
        self.assertTrue(value["journal_attempted"])
        self.assertTrue(value["failure_preserved"])
        self.assertTrue(value["escaped_session"])
        self.assertGreaterEqual(value["cleanup"]["reaped"], 2)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child" and sys.argv[2] in {"plain", "tree"}:
        synthetic_child(sys.argv[2] == "tree")
    elif len(sys.argv) == 3 and sys.argv[1] == "--worker" and sys.argv[2] in CASES:
        worker(sys.argv[2])
    else:
        unittest.main()
