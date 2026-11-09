"""Refusal and ownership-boundary tests for the fixed A0 harness protocol."""
import errno
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_harness as harness
import linux_real_app_wayland as app

CLEAN = {"schema": 1, "complete": True, "pending": False, "reason": "COMPLETED",
         "open_pidfds": 0, "root_pidfds_closed": True, "output_pipes_eof": True,
         "workers_complete": True, "worker_count": 2}


def event(**changes):
    return {"schema": 1, "event": "ready", "id": 0, "data": {}, **changes}


class PrecursorReadinessTests(unittest.TestCase):
    def state(self, token, count=4):
        return {"settings_present": True, "preferences_count": count,
                "preferences_sha256": token if count else None,
                "settings_sha256": token, "documents_sha256": "unchanged"}

    def exercise(self, snapshot):
        clock = [0.0]
        run = mock.Mock(deadline=20, profile_lock=123)
        control = mock.Mock()
        control.settle.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        profile = mock.Mock()
        profile.snapshot.side_effect = lambda lock: snapshot(clock[0])
        return clock, run, control, profile

    def test_baseline_waits_for_observed_flush_and_resets_quiet_interval_on_change(self):
        def snapshot(now):
            if now < 1.5:
                return self.state("session-only", 0)
            return self.state("first-flush" if now < 2 else "final-flush")
        clock, run, control, profile = self.exercise(snapshot)
        with mock.patch.object(harness.time, "monotonic", side_effect=lambda: clock[0]):
            value = harness._stable_profile(run, control, profile)
        self.assertEqual(value, self.state("final-flush"))
        self.assertGreaterEqual(clock[0], 3.1)
        self.assertLess(clock[0], 4)
        self.assertGreater(run.pump.call_count, 1)
        profile.snapshot.assert_called_with(run.profile_lock)

    def test_absent_ordinary_settings_and_continuous_writes_both_time_out(self):
        for snapshot in (lambda now: self.state("unchanged-empty", 0),
                         lambda now: self.state(str(int(now * 4)))):
            with self.subTest(snapshot=snapshot):
                clock, run, control, profile = self.exercise(snapshot)
                with mock.patch.object(harness.time, "monotonic", side_effect=lambda: clock[0]):
                    with self.assertRaisesRegex(app.WaylandError, "APP_PRECURSOR_SETTINGS_NOT_STABLE"):
                        harness._stable_profile(run, control, profile)
                self.assertGreaterEqual(clock[0], 8)

    def test_signal_and_unsafe_profile_are_not_swallowed_as_transient_writes(self):
        for reason in ("APP_WAYLAND_INTERRUPTED", "APP_WAYLAND_PROFILE_CHANGED"):
            clock, run, control, profile = self.exercise(lambda now: self.state("stable"))
            if reason.endswith("INTERRUPTED"):
                run.pump.side_effect = app.WaylandError(reason)
            else:
                profile.snapshot.side_effect = app.WaylandError(reason)
            with mock.patch.object(harness.time, "monotonic", side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(app.WaylandError, reason):
                    harness._stable_profile(run, control, profile)
            control.settle.assert_not_called()


class WorkerProtocolTests(unittest.TestCase):
    def test_json_rejects_duplicates_nonfinite_and_oversize(self):
        for data in (b'{"schema":1,"schema":1}', b'{"x":NaN}', b'{"x":Infinity}',
                     b'{"x":-Infinity}', b'\xff', b'', b'x' * (harness.MAX_MESSAGE + 1)):
            with self.subTest(data=data[:40]), self.assertRaises(app.WaylandError):
                harness._decode(data)

    def test_event_requires_exact_keys_types_and_bounded_sequence(self):
        self.assertEqual(harness._event(event()), event())
        invalid = [event(schema=True), event(schema="1"), event(schema=2), event(id=True),
                   event(id=-1), event(id=65), event(id="0"), event(event="pending"),
                   event(event=[]), event(data=[]), event(payload="not allowed"), []]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(app.WaylandError):
                harness._event(value)

    def channel(self, commands):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        fifo = directory / "commands"
        os.mkfifo(fifo, 0o600)
        writer = os.open(fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.addCleanup(os.close, writer)
        channel = harness._WorkerChannel(directory)
        self.addCleanup(channel.close)
        encoded = b"".join(json.dumps(command).encode() + b"\n" for command in commands)
        self.assertLess(len(encoded), 4096)
        os.write(writer, encoded)
        return channel

    def test_command_replay_unknown_fields_and_types_are_refused(self):
        valid = {"schema": 1, "id": 1, "op": "observe"}
        for invalid in (dict(valid, schema=True), dict(valid, id=True), dict(valid, id=2),
                        dict(valid, op=[]), dict(valid, op="shell"), dict(valid, argv=["/bin/sh"]),
                        dict(valid, id="1")):
            with self.subTest(value=invalid):
                channel = self.channel([invalid])
                with self.assertRaises(app.WaylandError):
                    channel.read(lambda: None)
        channel = self.channel([valid, valid])
        self.assertEqual(channel.read(lambda: None), valid)
        with self.assertRaisesRegex(app.WaylandError, "APP_WORKER_COMMAND"):
            channel.read(lambda: None)

    def test_only_run_accepts_a_known_fixed_case(self):
        for invalid in ({"schema": 1, "id": 1, "op": "run"},
                        {"schema": 1, "id": 1, "op": "run", "case": "arbitrary"},
                        {"schema": 1, "id": 1, "op": "run", "case": []},
                        {"schema": 1, "id": 1, "op": "stop", "case": "close_restart"}):
            with self.subTest(value=invalid), self.assertRaises(app.WaylandError):
                self.channel([invalid]).read(lambda: None)
        valid = {"schema": 1, "id": 1, "op": "run", "case": "crash_restart"}
        self.assertEqual(self.channel([valid]).read(lambda: None), valid)

    def test_command_channel_rejects_regular_file_symlink_and_loose_mode(self):
        for kind in ("file", "link", "mode"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                path = directory / "commands"
                if kind == "file":
                    path.touch(mode=0o600)
                elif kind == "link":
                    target = directory / "target"
                    os.mkfifo(target, 0o600)
                    path.symlink_to(target)
                else:
                    os.mkfifo(path, 0o600)
                    path.chmod(0o666)
                with self.assertRaisesRegex(app.WaylandError, "APP_WORKER_FIFO"):
                    harness._WorkerChannel(directory)

    def test_response_cannot_satisfy_wrong_case_request(self):
        coordinator = object.__new__(harness._Coordinator)
        coordinator.pump = lambda: None
        worker = harness._Worker("target", Path("/not-used"), -1)
        worker.process = mock.Mock()
        worker.process.poll.return_value = None
        for incoming in (event(event="case", id=1), event(event="observation", id=2)):
            worker.messages[:] = [incoming]
            with self.assertRaisesRegex(app.WaylandError, "APP_WORKER_EVENT_ORDER"):
                coordinator.receive(worker, "case", seconds=.1, request_id=2)

    def test_fixed_worker_builder_has_no_free_argv_or_user_profile_mode(self):
        with mock.patch.object(app, "_tool", return_value="/usr/bin/python3"):
            for role in ("precursor", "target"):
                value = harness._worker_command(role, Path("/synthetic/worker"), Path("/synthetic/bundle"))
                self.assertEqual(value[2:], ["--worker", role, "/synthetic/worker", "/synthetic/bundle"])
            with self.assertRaisesRegex(app.WaylandError, "APP_WORKER_ROLE"):
                harness._worker_command("shell", Path("/synthetic/worker"), Path("/synthetic/bundle"))


class OwnershipBoundaryTests(unittest.TestCase):
    def test_constructor_failure_restores_handlers_before_any_worker_exists(self):
        numbers = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        previous = {number: signal.getsignal(number) for number in numbers}
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(app.owner_api, "ProcessOwner", side_effect=app.owner_api.OwnershipError("TEST_OWNER")):
            with self.assertRaisesRegex(app.owner_api.OwnershipError, "TEST_OWNER"):
                harness._Coordinator(Path(temporary), Path(temporary) / "bundle")
        self.assertEqual({number: signal.getsignal(number) for number in numbers}, previous)

    def test_failed_pidfd_registration_still_drains_registered_child_pipes(self):
        numbers = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        previous = {number: signal.getsignal(number) for number in numbers}
        streams = []
        for _ in range(2):
            reader, writer = os.pipe()
            os.write(writer, b"bounded test output\n")
            os.close(writer)
            streams.append(os.fdopen(reader, "rb"))
        child = mock.Mock(pid=1234, stdout=streams[0], stderr=streams[1], returncode=0)
        child.poll.return_value = 0
        owner = mock.Mock()
        owner.spawn.return_value = child
        owner.finish.return_value = dict(CLEAN)
        coordinator = None
        try:
            with tempfile.TemporaryDirectory() as temporary, \
                 mock.patch.object(app.owner_api, "ProcessOwner", return_value=owner), \
                 mock.patch.object(harness, "_worker_command", return_value=["fixed-test-worker"]):
                coordinator = harness._Coordinator(Path(temporary), Path(temporary) / "bundle")
                with mock.patch.object(os, "pidfd_open", side_effect=OSError(errno.EMFILE, "synthetic")):
                    with self.assertRaises(OSError):
                        coordinator.spawn("precursor")
                self.assertIs(coordinator.workers[0].process, child)
                result = coordinator.finish()
            self.assertTrue(result["complete"])
            self.assertTrue(result["output_pipes_eof"])
            self.assertTrue(all(stream.closed for stream in streams))
            self.assertTrue(coordinator.closed)
            self.assertEqual({number: signal.getsignal(number) for number in numbers}, previous)
        finally:
            if coordinator is not None and not coordinator.closed:
                coordinator.finish()
            for stream in streams:
                stream.close()

    def test_coordinator_never_finishes_all_descendants_while_worker_is_alive(self):
        coordinator = object.__new__(harness._Coordinator)
        coordinator.closed = False
        coordinator.cleanup = None
        coordinator.owner = mock.Mock()
        coordinator.termination = mock.Mock()
        coordinator.send = mock.Mock()
        coordinator.pump = mock.Mock()
        worker = harness._Worker("precursor", Path("/not-used"), -1)
        worker.process = mock.Mock()
        worker.process.poll.return_value = None
        coordinator.workers = [worker]
        with mock.patch.object(harness.time, "monotonic", side_effect=[0, 6]):
            with self.assertRaises(harness.CoordinatorPending) as caught:
                coordinator.finish()
        self.assertIs(caught.exception.coordinator, coordinator)
        coordinator.owner.finish.assert_not_called()
        coordinator.termination.restore.assert_not_called()
        coordinator.send.assert_called_once_with(worker, "stop", enforce=False)

    def test_coordinator_pending_journal_failure_preserves_original_failure(self):
        coordinator = mock.Mock()
        coordinator.termination.requested = signal.SIGTERM
        coordinator.cleanup = {"complete": False, "pending": True}
        coordinator.workers = []
        pending = harness.CoordinatorPending(coordinator)
        coordinator.finish.side_effect = [pending, CLEAN]
        result = {"state": "fail", "status": "failed", "error": "APP_ORIGINAL_FAILURE"}
        with mock.patch.object(harness, "_write", side_effect=OSError(errno.EIO, "synthetic")) as writer, \
             mock.patch.object(harness.time, "sleep"):
            harness._finish_coordinator(coordinator, result, Path("/not-used"))
        self.assertEqual(coordinator.finish.call_count, 2)
        self.assertEqual(writer.call_count, 1)
        self.assertTrue(result["receipt_write_failed"])
        self.assertEqual(result["error"], "APP_ORIGINAL_FAILURE")
        self.assertEqual(result["termination_signal"], signal.SIGTERM)
        self.assertEqual(result["state"], "fail")
        self.assertEqual(result["cleanup"], CLEAN)

    def test_worker_nonzero_exit_cannot_be_promoted_by_complete_cleanup(self):
        coordinator = mock.Mock()
        coordinator.termination.requested = None
        coordinator.finish.return_value = dict(CLEAN)
        worker = harness._Worker("target", Path("/not-used"), -1)
        worker.process = mock.Mock(returncode=1)
        coordinator.workers = [worker]
        result = {"state": "partial", "status": "incomplete", "error": None}
        harness._finish_coordinator(coordinator, result, Path("/not-used"))
        self.assertEqual(result["state"], "fail")
        self.assertEqual(result["error"], "APP_WORKER_EXIT")

    def test_worker_pending_journal_and_retry_io_failure_keep_same_live_run(self):
        run = mock.Mock()
        pending = app.CleanupPending(run)
        pending.receipt = {"state": "fail", "status": "failed", "error": "APP_ORIGINAL_FAILURE"}
        again = app.CleanupPending(run)
        with mock.patch.object(harness, "_write", side_effect=OSError(errno.ENOSPC, "synthetic")), \
             mock.patch.object(app, "retry_cleanup", side_effect=[OSError(errno.EIO, "synthetic"), again, CLEAN]) as retry, \
             mock.patch.object(harness.time, "sleep"):
            value = harness._retain_run_cleanup(pending, Path("/not-used"))
        self.assertIs(value, pending.receipt)
        self.assertIs(pending.run, run)
        self.assertEqual(retry.call_count, 3)
        self.assertTrue(value["receipt_write_failed"])
        self.assertEqual(value["error"], "APP_ORIGINAL_FAILURE")
        self.assertEqual(value["state"], "fail")
        self.assertEqual(value["cleanup"], CLEAN)

    def test_receipt_failure_while_both_workers_live_always_finalizes_suite(self):
        # Exercise the real run_suite finally path, with fake observation data
        # only. This is not counted as real app/restart evidence.
        observation = {key: "same" for key in ("run_id", "owner_scope_id", "profile_id", "runtime_id",
                                               "main", "app_id", "client_count", "topology_sha256")}
        observation["profile"] = {key: "same" for key in
                                  ("documents_sha256", "settings_sha256", "preferences_sha256")}
        responses = [{"worker_id": "precursor", "observation": observation}, {"worker_id": "target"}]
        for case in (*harness.CASES, *harness.RECOVERY_CASES):
            responses.append({"name": case, "case": {}, "foundation": {}})
            if case in ("close_restart", "stop_restart"):
                responses.append(observation)
        coordinator = mock.Mock()
        coordinator.receive.side_effect = responses
        coordinator.finish.return_value = dict(CLEAN)
        coordinator.termination.requested = None
        coordinator.workers = []
        writes = []

        def write(path, value):
            writes.append(path.name)
            if path.name == "independence.json":
                raise OSError(errno.ENOSPC, "synthetic")

        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(harness, "_Coordinator", return_value=coordinator), \
             mock.patch.object(harness, "_write", side_effect=write):
            value = harness.run_suite(ROOT, Path(temporary), Path(temporary) / "bundle")
        coordinator.finish.assert_called_once()
        self.assertEqual(writes, ["independence.json", "wayland.json"])
        self.assertEqual(value["state"], "fail")
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "APP_HARNESS_DEPENDENCY_OR_IO")
        self.assertEqual(value["cleanup"], CLEAN)
        self.assertTrue(all(flag is False for flag in value["checks"].values()))
        self.assertEqual(value["pending"], list(app.REQUIRED_CHECKS))


if __name__ == "__main__":
    unittest.main()
