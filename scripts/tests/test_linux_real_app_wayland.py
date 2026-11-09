"""Controller invariants; live Mousepad feasibility is a separate explicit gate."""
import contextlib
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_wayland as owner

driver = owner.driver
APP_ID = owner.inspect_api.WINDOW_APP_ID


def window(identifier="1", parent=None, *, app_id=APP_ID, valid=True, frame="1", life="Mapped"):
    return {"window": identifier, "parent": parent, "app_id": app_id, "app_id_valid": valid,
            "life": life, "frame": frame, "logical_size": {"width": 800, "height": 600}, "layers": 1}


def snapshot(*windows):
    return {"windows": list(windows), "observed_mapped": [{"window": w["window"], "parent": w["parent"],
              "app_id": w["app_id"], "app_id_valid": w["app_id_valid"], "dispatch_batch": 1}
              for w in windows], "input": {"delivered": 0, "pressed": 0}, "counts": {"clients": 1}}


def controller(*snapshots):
    value = object.__new__(driver.Control)
    value.main = None
    value.expected_app_id = None
    value.pump = lambda: None
    values = iter(snapshots)
    last = snapshots[-1]
    value.snapshot = lambda: next(values, last)
    value.focus = lambda _: None
    value.settle = lambda: None
    return value


class WindowEvidenceTests(unittest.TestCase):
    def test_identity_failure_keeps_observed_id_without_admitting_window(self):
        observed = snapshot(window(app_id="org.xfce.mousepad"))
        value = controller(observed)
        value.last_snapshot = observed
        with self.assertRaisesRegex(driver.ProbeError, "INITIAL_APP_ID"):
            value.wait_main(APP_ID)
        self.assertIsNone(value.main)
        metadata = driver.diagnostic_metadata(value)
        self.assertEqual(metadata["phase"], "initial_main")
        self.assertEqual(metadata["windows"][0]["app_id"], "org.xfce.mousepad")
        self.assertEqual(metadata["windows"][0]["life"], "Mapped")

    def test_diagnostics_are_bounded_and_exclude_payloads_and_unknown_fields(self):
        value = controller(snapshot(window()))
        value.phase, value.last_operation, value.last_error = {"secret": "text"}, "private/path", "private error"
        windows = [dict(window(str(n)), title="private title", text="private payload", pixels=[1, 2, 3])
                   for n in range(1, 17)]
        windows[0]["app_id"] = "private /path"
        value.last_snapshot = snapshot(*windows)
        value.last_snapshot["input"].update(text="private text", pressed=True)
        metadata = driver.diagnostic_metadata(value)
        encoded = json.dumps(metadata)
        self.assertNotIn("private", encoded)
        self.assertNotIn("pixels", encoded)
        self.assertNotIn("title", encoded)
        self.assertEqual(metadata["phase"], "unknown")
        self.assertIsNone(metadata["operation"])
        self.assertEqual(metadata["native_error"], "REJECTED")
        self.assertEqual(metadata["window_count"], 16)
        self.assertEqual(len(metadata["windows"]), 8)
        self.assertTrue(metadata["windows_truncated"])
        self.assertIsNone(metadata["windows"][0]["app_id"])
        self.assertFalse(metadata["windows"][0]["app_id_valid"])
        self.assertIsNone(metadata["input"]["pressed"])
        self.assertLess(len(encoded), 4096)

    def test_main_requires_owned_frame_and_latches_identity(self):
        value = controller(snapshot(window(frame=None)), snapshot(window()))
        self.assertEqual(value.wait_main(APP_ID), "1")
        with self.assertRaisesRegex(driver.ProbeError, "MAIN_ALREADY_LATCHED"):
            value.wait_main(APP_ID)
        with self.assertRaisesRegex(driver.ProbeError, "MAIN_LIFETIME_LOST"):
            value.assert_topology(snapshot(window("2")))

    def test_main_first_rejects_prior_destroyed_or_initial_second_toplevel(self):
        prior = snapshot(window())
        prior["observed_mapped"].insert(0, {"window": "9"})
        for value in [prior, snapshot(window(), window("2", "1")), snapshot(window(parent="2"))]:
            with self.assertRaises(driver.ProbeError):
                controller(value).wait_main(APP_ID)

    def test_dialog_uses_actual_transient_chain_and_accepts_absent_appid(self):
        value = controller(snapshot(window()))
        value.wait_main(APP_ID)
        value.assert_topology(snapshot(window(), window("2", "1", app_id=None), window("3", "2", app_id="")))
        invalid = [window("2"), window("2", "99"), window("2", "2"),
                   window("2", "1", app_id="org.other.app"), window("2", "1", app_id=None, valid=False)]
        for bad in invalid:
            with self.assertRaises(driver.ProbeError):
                value.assert_topology(snapshot(window(), bad))

    def test_metadata_alone_does_not_prove_frame_and_ids_are_not_lossy_numbers(self):
        self.assertFalse(driver.has_frame(window(frame=None)))
        self.assertFalse(driver.has_frame(window(frame=1)))
        self.assertTrue(driver.has_frame(window(frame="9223372036854775807")))
        for identifier in ["0", "01", "-1", 1, "9223372036854775808", "١"]:
            with self.assertRaises(driver.ProbeError):
                driver.mapped(snapshot(window(identifier)))
        with self.assertRaisesRegex(driver.ProbeError, "DUPLICATE"):
            driver.mapped(snapshot(window(), window()))


class InputAndOracleTests(unittest.TestCase):
    def test_chooser_accept_waits_for_location_debounce_and_preserves_file_oracle(self):
        # Model GTK's 150 ms selection debounce: Enter while insensitive is
        # lost, not queued. A compositor delivery acknowledgement is not an
        # application acknowledgement. This is a sequencing regression only;
        # the actual GTK/Waypipe run remains the feasibility evidence.
        for dispatches, writes in [(False, True), (True, False), (True, True)]:
            with self.subTest(dispatches=dispatches, writes=writes), tempfile.TemporaryDirectory() as temporary:
                source, destination = (Path(temporary) / name for name in ("input.txt", "output.txt"))
                initial, appended = b"opened\n", "saved\n"
                expected = initial + appended.encode("ascii")
                source.write_bytes(initial)
                clock, changed, closed = [0.0], {}, set()
                control = mock.Mock(spec=driver.Control)
                control.main, control.phase = "1", "main_ready"
                control.pump = lambda: None
                control.snapshot.side_effect = lambda: dict(snapshot(window()), input={
                    "delivered": 0 if control.phase == "main_ready" else 100, "pressed": 0})
                control.wait_dialog.side_effect = ["2", "3"]
                control.settle.side_effect = lambda seconds=.15: clock.__setitem__(
                    0, clock[0] + (seconds if dispatches else 0))
                def entered(_, window_id, text):
                    if control.phase in ("open_file", "save_as_file"):
                        changed[window_id] = clock[0]
                def keys(_, window_id, *codes):
                    if codes == (driver.ENTER,) and clock[0] - changed[window_id] >= .150:
                        closed.add(window_id)
                        if writes and control.phase == "save_as_file":
                            destination.write_bytes(expected)
                    if writes and control.phase == "save_file" and codes == (driver.CTRL, 31):
                        source.write_bytes(expected)
                def wait_closed(window_id):
                    if window_id not in closed:
                        raise driver.ProbeError("DIALOG_CLOSE_TIMEOUT")
                control.wait_closed.side_effect = wait_closed
                original_oracle = driver.wait_file_bytes
                def fast_oracle(path, expected_bytes, timeout=5.0, pump=None):
                    return original_oracle(path, expected_bytes, .025, pump)
                with mock.patch.object(driver, "type_ascii", side_effect=entered), \
                     mock.patch.object(driver, "chord", side_effect=keys), \
                     mock.patch.object(driver, "wait_file_bytes", side_effect=fast_oracle):
                    if not dispatches or not writes:
                        code = "DIALOG_CLOSE_TIMEOUT" if not dispatches else "FILE_ORACLE_TIMEOUT"
                        with self.assertRaisesRegex(driver.ProbeError, code):
                            driver.exercise_open_edit_save(control, source, destination, initial, appended)
                    else:
                        result = driver.exercise_open_edit_save(control, source, destination, initial, appended)
                        self.assertEqual(result["state"], "partial")
                        self.assertFalse(result["feasibility_verified"])
                        self.assertEqual(source.read_bytes(), expected)
                        self.assertEqual(destination.read_bytes(), expected)

    def test_failed_chord_releases_already_pressed_modifiers(self):
        events = []
        class Fault:
            def key(self, window, code, pressed):
                events.append((code, pressed))
                if code == 31 and pressed:
                    raise driver.ProbeError("TEST_KEY_FAILED")
        with self.assertRaisesRegex(driver.ProbeError, "TEST_KEY_FAILED"):
            driver.chord(Fault(), "1", driver.CTRL, driver.SHIFT, 31)
        self.assertEqual(events[-2:], [(driver.SHIFT, False), (driver.CTRL, False)])

    def test_ascii_is_real_bounded_evdev_sequence(self):
        events = []
        class Keys:
            def key(self, window, code, pressed):
                events.append((window, code, pressed))
        driver.type_ascii(Keys(), "1", "a_A/\n")
        self.assertIn(("1", 30, True), events)
        self.assertIn(("1", driver.SHIFT, True), events)
        self.assertEqual(events[-2:], [("1", driver.ENTER, True), ("1", driver.ENTER, False)])
        for text in ["", "日本語", "a" * 1025, "\\"]:
            with self.assertRaisesRegex(driver.ProbeError, "TEXT_UNSUPPORTED"):
                driver.type_ascii(Keys(), "1", text)

    def test_file_oracle_rejects_unchanged_data_extra_bytes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "output.txt"
            path.write_bytes(b"before\n")
            with self.assertRaisesRegex(driver.ProbeError, "FILE_ORACLE_TIMEOUT"):
                driver.wait_file_bytes(path, b"after\n", .025)
            path.write_bytes(b"after\nextra")
            with self.assertRaisesRegex(driver.ProbeError, "FILE_ORACLE_TIMEOUT"):
                driver.wait_file_bytes(path, b"after\n", .025)
            path.write_bytes(b"after\n")
            driver.wait_file_bytes(path, b"after\n", .025)
            link = path.with_name("link.txt")
            link.symlink_to(path)
            with self.assertRaisesRegex(driver.ProbeError, "FILE_ORACLE_SYMLINK"):
                driver.wait_file_bytes(link, b"after\n", .025)

    def test_no_api_probe_can_make_gui_checks_pass(self):
        self.assertIn("preferences_widgets_read_only", driver.PENDING_CHECKS)
        self.assertIn("settings_persist_after_restart", driver.PENDING_CHECKS)
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary)
            # No runtime dependency is invoked, and unsupported prerequisites
            # must be a failure receipt rather than a skipped successful gate.
            # Keep the actual temp directory owner/UID relationship intact on
            # both root local environments and non-root GitHub runners.
            with mock.patch.object(owner.sys, "platform", "unsupported"):
                result = owner.run_case(ROOT, artifact, artifact / "missing-settings")
            self.assertEqual(result["state"], "fail")
            self.assertFalse(result["feasibility_verified"])
            self.assertEqual(result["error"], "APP_WAYLAND_NONROOT_REQUIRED")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["pending"], list(owner.REQUIRED_CHECKS))
            self.assertEqual(set(result["checks"]), set(owner.REQUIRED_CHECKS))
            self.assertFalse(result["checks"]["protected_settings_gui_readonly"])
            self.assertFalse(result["checks"]["gui_setting_persistence"])
            self.assertFalse(result["checks"]["clean_close_restart"])
            self.assertTrue((artifact / "wayland.json").is_file())


class ControlTransportTests(unittest.TestCase):
    @staticmethod
    def ready(lifetime="run"):
        return {"schema": 1, "event": "ready", "socket_name": "wayland-l7a",
                "protocol": "eidolon-a0-control-v1", "server": "Eidolon ProtocolState",
                "lifetime": lifetime, "run_limit_seconds": owner._lifetime_limits(lifetime)[1]}

    def child(self, behavior, *, ready=None):
        ready = self.ready() if ready is None else ready
        script = "import json,sys,time\nprint(" + repr(json.dumps(ready)) + ",flush=True)\n" + behavior
        child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(self.cleanup, child)
        return child

    @staticmethod
    def cleanup(child):
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()

    def test_transport_requires_matching_reply_and_drains_auxiliary_pump(self):
        child = self.child("for line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({'id':r['id'],'ok':True,'snapshot':{'windows':[]}}),flush=True)\n")
        pumped = []
        control = driver.Control(child, pump=lambda: pumped.append(True),
                                 lifetime="run", run_limit_seconds=owner._lifetime_limits("run")[1])
        self.assertEqual(control.snapshot(), {"windows": []})
        self.assertTrue(pumped)

    def test_sequence_mismatch_rejected(self):
        child = self.child("sys.stdin.readline()\nprint('{\"id\":99,\"ok\":true,\"snapshot\":{\"windows\":[]}}',flush=True)\n")
        with self.assertRaisesRegex(driver.ProbeError, "REPLY_SEQUENCE"):
            driver.Control(child, lifetime="run", run_limit_seconds=owner._lifetime_limits("run")[1]).snapshot()

    def test_stalled_reply_has_deadline(self):
        child = self.child("sys.stdin.readline()\ntime.sleep(2)\n")
        control = driver.Control(child, timeout=.2,
                                 lifetime="run", run_limit_seconds=owner._lifetime_limits("run")[1])
        with self.assertRaisesRegex(driver.ProbeError, "CONTROL_TIMEOUT"):
            control.snapshot()

    def test_duplicate_json_fields_rejected(self):
        child = self.child("sys.stdin.readline()\nprint('{\"id\":1,\"id\":1,\"ok\":true,\"snapshot\":{\"windows\":[]}}',flush=True)\n")
        with self.assertRaisesRegex(driver.ProbeError, "DUPLICATE_FIELD"):
            driver.Control(child, lifetime="run", run_limit_seconds=owner._lifetime_limits("run")[1]).snapshot()

    def test_ready_requires_the_selected_role_and_exact_watchdog_limit(self):
        for lifetime in ("run", "precursor"):
            ready = self.ready(lifetime)
            child = self.child("time.sleep(1)\n", ready=ready)
            driver.Control(child, lifetime=lifetime, run_limit_seconds=ready["run_limit_seconds"])
            invalid = [dict(ready, lifetime="precursor" if lifetime == "run" else "run"),
                       dict(ready, run_limit_seconds=ready["run_limit_seconds"] - 10),
                       dict(ready, run_limit_seconds=ready["run_limit_seconds"] + 1),
                       dict(ready, run_limit_seconds=float(ready["run_limit_seconds"])),
                       dict(ready, run_limit_seconds=True), dict(ready, schema=True),
                       {k: v for k, v in ready.items() if k not in ("lifetime", "run_limit_seconds")}]
            for value in invalid:
                with self.subTest(lifetime=lifetime, ready=value):
                    child = self.child("time.sleep(1)\n", ready=value)
                    with self.assertRaisesRegex(driver.ProbeError, "CONTROL_READY"):
                        driver.Control(child, lifetime=lifetime, run_limit_seconds=ready["run_limit_seconds"])

    def test_ready_expectations_cannot_be_unknown_or_boolean(self):
        for lifetime, seconds in (("unknown", 370), (True, 370), ("run", True), ("run", 0), ("run", 120.0)):
            with self.subTest(lifetime=lifetime, seconds=seconds), \
                 self.assertRaisesRegex(driver.ProbeError, "CONTROL_EXPECTATION"):
                driver.Control(mock.Mock(), lifetime=lifetime, run_limit_seconds=seconds)


class LifetimePolicyTests(unittest.TestCase):
    def test_fixed_roles_keep_owner_deadline_from_construction(self):
        for lifetime, budget, compositor in (("run", 110, 120), ("precursor", 360, 370)):
            with self.subTest(lifetime=lifetime):
                clock = [40.0]
                process_owner = mock.Mock()
                process_owner.finish.return_value = RunCaseCleanupTests.report(True)
                guard = mock.Mock()
                def new_owner():
                    clock[0] += 4  # Later setup must not restart the owner clock.
                    return process_owner
                with mock.patch.object(owner.owner_api, "ProcessOwner", side_effect=new_owner), \
                     mock.patch.object(owner.time, "monotonic", side_effect=lambda: clock[0]):
                    run = owner._Run(Path("/tmp"), guard, lifetime=lifetime)
                    try:
                        self.assertEqual(run.started, 40)
                        self.assertEqual(run.deadline, 40 + budget)
                        self.assertEqual(run.owner_limit_seconds, budget)
                        self.assertEqual(run.compositor_limit_seconds, compositor)
                        clock[0] = 161  # More than the original compositor's 120 seconds.
                        if lifetime == "run":
                            with self.assertRaisesRegex(owner.WaylandError, "APP_WAYLAND_RUN_TIMEOUT"):
                                run.pump()
                        else:
                            run.pump()
                        self.assertEqual(run.deadline, 40 + budget)
                        clock[0] = 40 + budget
                        with self.assertRaisesRegex(owner.WaylandError, "APP_WAYLAND_RUN_TIMEOUT"):
                            run.pump()
                    finally:
                        run.finish()
                guard.restore.assert_called_once()

    def test_unknown_or_numeric_lifetime_never_creates_an_owner(self):
        for lifetime in ("unknown", "360", 360, True, None, ["precursor"]):
            with self.subTest(lifetime=lifetime), \
                 mock.patch.object(owner.owner_api, "ProcessOwner") as factory:
                with self.assertRaisesRegex(owner.WaylandError, "APP_WAYLAND_LIFETIME$"):
                    owner._Run(Path("/tmp"), mock.Mock(), lifetime=lifetime)
                factory.assert_not_called()

    def test_shared_policy_rejects_unbounded_or_ambiguous_values(self):
        valid = {"run_seconds": 110, "precursor_seconds": 360, "compositor_grace_seconds": 10}
        invalid = [dict(valid, run_seconds=0), dict(valid, run_seconds=361),
                   dict(valid, precursor_seconds=601), dict(valid, precursor_seconds=True),
                   dict(valid, precursor_seconds=360.0), dict(valid, precursor_seconds="360"),
                   dict(valid, compositor_grace_seconds=0), dict(valid, compositor_grace_seconds=31),
                   dict(valid, unexpected=10), {"run_seconds": 110}, [], None]
        encoded = [json.dumps(value) for value in invalid]
        encoded.extend(('{"run_seconds":110,"precursor_seconds":360,"precursor_seconds":600,"compositor_grace_seconds":10}',
                        '{"run_seconds":110,"precursor_seconds":NaN,"compositor_grace_seconds":10}'))
        with tempfile.TemporaryDirectory() as temporary:
            policy = Path(temporary) / "lifetime.json"
            with mock.patch.object(owner, "LIFETIME_SPEC", policy):
                for value in encoded:
                    with self.subTest(value=value):
                        policy.write_text(value)
                        with self.assertRaisesRegex(owner.WaylandError, "APP_WAYLAND_LIFETIME_POLICY"):
                            owner._lifetime_limits("precursor")

    def test_run_case_passes_named_lifetime_to_its_internal_compositor(self):
        for lifetime, budget, watchdog in (("run", 110, 120), ("precursor", 360, 370)):
            with self.subTest(lifetime=lifetime), RunCaseCleanupTests().prepared() as prepared:
                repo, artifact, settings, process_owner, holder = prepared
                commands = []
                def capture(run, label, command, *_args, **_kwargs):
                    holder.append(run)
                    commands.append((label, command))
                    raise owner.WaylandError("APP_WAYLAND_TEST_ABORT")
                process_owner.finish.return_value = RunCaseCleanupTests.report(True)
                with mock.patch.object(owner._Run, "spawn", capture):
                    result = owner.run_case(repo, artifact, settings, lifetime=lifetime)
                self.assertEqual(result["error"], "APP_WAYLAND_TEST_ABORT")
                self.assertEqual(commands[0][0], "compositor")
                self.assertEqual(commands[0][1][2], "--exec-compositor")
                self.assertEqual(commands[0][1][-1], lifetime)
                self.assertEqual(len(commands[0][1][3:]), 5)
                self.assertEqual(result["lifetime"], lifetime)
                self.assertEqual(result["owner_limit_seconds"], budget)
                self.assertEqual(result["compositor_limit_seconds"], watchdog)
                self.assertTrue(result["cleanup"]["complete"])

    def test_internal_adapter_only_forwards_the_fixed_lifetime_role(self):
        binary = owner.ROOT / "target/debug/linux-real-app-wayland-probe"
        with tempfile.TemporaryDirectory(prefix="eidolon-l7a-", dir="/tmp") as temporary:
            runtime = Path(temporary)
            fifo = runtime / "control"
            os.mkfifo(fifo, 0o600)
            writer = os.open(fifo, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
            self.addCleanup(os.close, writer)
            argv = [str(binary), str(runtime), str(runtime / "artifact"), str(fifo)]
            for lifetime in ("run", "precursor"):
                with self.subTest(lifetime=lifetime), \
                     mock.patch.object(owner.os, "dup2") as duplicate, \
                     mock.patch.object(owner.os, "execve") as execute:
                    owner._exec_compositor([*argv, lifetime])
                    duplicate.assert_called_once()
                    self.assertEqual(execute.call_args.args[0], binary)
                    self.assertEqual(execute.call_args.args[1], [str(binary), "--runtime-dir", str(runtime),
                        "--artifact-dir", str(runtime / "artifact"), "--lifetime", lifetime])
            for invalid in (argv, [*argv, "360"], [*argv, "unknown"], [*argv, 360],
                            [*argv, "precursor", "--timeout", "9999"]):
                with self.subTest(invalid=invalid), mock.patch.object(owner.os, "execve") as execute:
                    with self.assertRaisesRegex(owner.WaylandError, "APP_WAYLAND_INTERNAL_ARGUMENT"):
                        owner._exec_compositor(invalid)
                    execute.assert_not_called()

    def test_feasibility_wrapper_preserves_the_explicit_hold_check(self):
        import linux_real_app_harness as harness
        repo, artifact, bundle = owner.ROOT, Path("/tmp/fixed-artifact"), Path("/tmp/fixed-bundle")
        waypipe = Path("/tmp/fixed-waypipe")
        compositor = repo / "target/debug/linux-real-app-wayland-probe"
        result = {"synthetic_unit_receipt": True}
        with mock.patch.object(owner, "_json", return_value={"executable": str(waypipe)}), \
             mock.patch.object(harness, "run_feasibility", return_value=result) as run:
            self.assertIs(owner.run_feasibility(repo, artifact, bundle, waypipe=waypipe,
                compositor=compositor, precursor_hold_check=True), result)
            run.assert_called_once_with(repo, artifact, bundle, precursor_hold_check=True)
            run.reset_mock()
            with self.assertRaisesRegex(owner.WaylandError, "APP_WAYLAND_ARGUMENT"):
                owner.run_feasibility(repo, artifact, bundle, waypipe=waypipe,
                                      compositor=compositor, precursor_hold_check=1)
            run.assert_not_called()


class PartialPipeRegistrationTests(unittest.TestCase):
    """Real pipes/subprocesses around injected registration faults.

    Only the owner factory is replaced, so these descriptor-boundary tests can
    run under root too. The mandatory non-root lifecycle suite separately
    verifies the actual frozen ProcessOwner and escaped descendants.
    """
    @contextlib.contextmanager
    def prepared_child(self):
        class FixedChildOwner:
            def __init__(self):
                self.child = None

            def spawn(self, command, *, env):
                self.child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              close_fds=True, start_new_session=True)
                return self.child

            def finish(self, **_kwargs):
                self.child.wait(timeout=3)
                return RunCaseCleanupTests.report(True)

        process_owner = FixedChildOwner()
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(owner.owner_api, "ProcessOwner", return_value=process_owner):
            guard = owner._TerminationGuard()
            run = owner._Run(Path(temporary), guard)
            try:
                yield run, process_owner
            finally:
                if process_owner.child is not None:
                    process_owner.child.wait(timeout=3)
                if guard.previous:
                    run.finish()

    @staticmethod
    def child_command():
        return [sys.executable, "-I", "-S", "-B", "-c",
                'import sys; print("synthetic-out", flush=True); print("synthetic-err", file=sys.stderr, flush=True)']

    def assert_drained(self, run, process_owner, receipt, control):
        self.assertEqual(process_owner.child.returncode, 0)
        self.assertTrue(receipt["complete"])
        self.assertTrue(receipt["output_pipes_eof"])
        self.assertTrue(receipt["root_pidfds_closed"])
        self.assertEqual(len(run.pipes), 2)
        self.assertTrue(all(stream.closed for stream in run.pipes))
        self.assertEqual(len(run.pidfds), 0)
        self.assertEqual(run.output_bytes["control_stdout_remaining" if control else "synthetic_stdout"],
                         len(b"synthetic-out\n"))
        self.assertEqual(run.output_bytes["synthetic_stderr"], len(b"synthetic-err\n"))
        self.assertFalse(run.termination.previous)

    def test_pidfd_failure_remembers_and_drains_both_real_child_pipes(self):
        for control in (False, True):
            with self.subTest(control=control), self.prepared_child() as (run, process_owner):
                with mock.patch.object(owner.os, "pidfd_open", side_effect=OSError(errno.EMFILE, "synthetic")):
                    with self.assertRaises(OSError):
                        run.spawn("synthetic", self.child_command(), {"PATH": "/usr/bin:/bin"}, control=control)
                self.assertEqual(len(run.pipes), 2)
                self.assertEqual(len(run.selector.get_map()), 0)
                self.assert_drained(run, process_owner, run.finish(), control)

    def test_partial_nonblocking_or_selector_registration_is_reconciled(self):
        for boundary in ("set_blocking", "register"):
            for control in (False, True):
                with self.subTest(boundary=boundary, control=control), self.prepared_child() as (run, process_owner):
                    original_blocking = os.set_blocking
                    original_register = run.selector.register

                    def failed_blocking(fd, value):
                        if fd == process_owner.child.stderr.fileno():
                            raise OSError(errno.EIO, "synthetic")
                        return original_blocking(fd, value)

                    def failed_register(stream, events, key):
                        if stream is process_owner.child.stderr:
                            raise OSError(errno.EIO, "synthetic")
                        return original_register(stream, events, key)

                    patch = (mock.patch.object(os, "set_blocking", side_effect=failed_blocking)
                             if boundary == "set_blocking" else
                             mock.patch.object(run.selector, "register", side_effect=failed_register))
                    with patch, self.assertRaises(OSError):
                        run.spawn("synthetic", self.child_command(), {"PATH": "/usr/bin:/bin"}, control=control)
                    self.assertEqual(len(run.pipes), 2)
                    with mock.patch.object(run.selector, "register", wraps=original_register) as register:
                        receipt = run.finish()
                    keys = [call.args[2] for call in register.call_args_list]
                    self.assertEqual(keys, ["control_stdout_remaining", "synthetic_stderr"]
                                     if control else ["synthetic_stderr"])
                    self.assert_drained(run, process_owner, receipt, control)

    def test_pending_eof_keeps_lock_and_does_not_reregister_drained_pipe(self):
        readers, writers = [], []
        for _ in range(2):
            read_fd, write_fd = os.pipe()
            readers.append(os.fdopen(read_fd, "rb"))
            writers.append(write_fd)
        process_owner = mock.Mock()
        process_owner.spawn.return_value = mock.Mock(pid=1234, stdout=readers[0], stderr=readers[1])
        process_owner.finish.return_value = RunCaseCleanupTests.report(True)
        run = None
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(owner.owner_api, "ProcessOwner", return_value=process_owner):
            lock = Path(temporary) / "profile.lock"
            guard = owner._TerminationGuard()
            try:
                run = owner._Run(Path(temporary), guard)
                run.profile_lock = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC, 0o600)
                fcntl.flock(run.profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                descriptor = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
                with mock.patch.object(os, "pidfd_open", return_value=descriptor):
                    run.spawn("synthetic", ["fixed-test-only"], {})
                os.write(writers[0], b"already-drained")
                os.close(writers[0])
                writers[0] = None
                run.pump()
                run.pump()
                self.assertIn(readers[0], run._pipe_eof)
                with mock.patch.object(run.selector, "register", wraps=run.selector.register) as register:
                    with self.assertRaises(owner.CleanupPending) as caught:
                        run.finish()
                    register.assert_not_called()
                self.assertIs(caught.exception.run, run)
                self.assertFalse(run.cleanup["complete"])
                self.assertTrue(run.cleanup["pending"])
                self.assertIs(signal.getsignal(signal.SIGTERM).__self__, guard)
                probe = os.open(lock, os.O_RDWR | os.O_CLOEXEC)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(probe)
                os.close(writers[1])
                writers[1] = None
                with mock.patch.object(run.selector, "register", wraps=run.selector.register) as register:
                    receipt = owner.retry_cleanup(caught.exception)
                    register.assert_not_called()
                self.assertTrue(receipt["output_pipes_eof"])
                self.assertTrue(all(stream.closed for stream in readers))
                self.assertIsNone(run.profile_lock)
                self.assertFalse(guard.previous)
            finally:
                for fd in writers:
                    if fd is not None:
                        os.close(fd)
                if run is not None and guard.previous:
                    run.finish()
                for stream in readers:
                    stream.close()
                guard.restore()


class RunCaseCleanupTests(unittest.TestCase):
    """Real run_case/lock/guard flow, with no application or native dependency.

    The dedicated live-process suite independently covers actual descendants;
    these faults isolate receipt open, partial write and fsync failures.
    """
    @contextlib.contextmanager
    def prepared(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            base = Path(temporary)
            repo, artifact, settings = (base / name for name in ("repo", "artifact", "settings"))
            artifact.mkdir(mode=0o700)
            (settings / "gio").mkdir(mode=0o700, parents=True)
            settings.chmod(0o700)
            module = settings / "gio/libeidolon-settings.so"
            module.write_bytes(b"fixed settings test bytes")
            (settings / "settings-backend.json").write_text(json.dumps({
                "kind": "linux-real-app-settings-a0", "settings_kind": "eidolon-keyfile-v1",
                "settings_policy": "mousepad-single-main-no-restore-v1",
                "files": {"gio/libeidolon-settings.so": owner._sha(module)},
            }))
            (repo / "target/debug").mkdir(parents=True)
            binary = repo / "target/debug/linux-real-app-wayland-probe"
            binary.write_bytes(b"fixed executable test bytes")
            binary.chmod(0o500)
            (repo / "native/generated").mkdir(parents=True)
            (repo / "native/generated/waypipe-host.json").write_text(json.dumps({
                "executable": str(binary), "target": "host", "version": "0.11.3",
                "sha256": owner._sha(binary),
            }))
            actual_require = owner.require
            def permit_test_uid(value, reason):
                if reason != "APP_WAYLAND_NONROOT_REQUIRED":
                    actual_require(value, reason)
            # Permit this dependency-free unit fixture under root as well;
            # real ProcessOwner is replaced, not its production constraints.
            stack.enter_context(mock.patch.object(owner, "ROOT", repo))
            stack.enter_context(mock.patch.object(owner, "require", side_effect=permit_test_uid))
            stack.enter_context(mock.patch.object(owner, "_tool", return_value=str(binary)))
            process_owner = stack.enter_context(mock.patch.object(owner.owner_api, "ProcessOwner")).return_value
            run_holder = []
            def abort_before_child(run, *_args, **_kwargs):
                run_holder.append(run)
                raise owner.WaylandError("APP_WAYLAND_TEST_ABORT")
            stack.enter_context(mock.patch.object(owner._Run, "spawn", abort_before_child))
            try:
                yield repo, artifact, settings, process_owner, run_holder
            finally:
                if run_holder and run_holder[0].termination.previous:
                    process_owner.finish.side_effect = None
                    process_owner.finish.return_value = self.report(True)
                    run_holder[0].finish()

    @staticmethod
    def report(complete):
        return {"schema": 1, "complete": complete, "pending": not complete,
                "reason": "COMPLETED" if complete else "CLEANUP_TIMEOUT",
                "open_pidfds": 0, "term_signals": 0, "kill_signals": 0, "reaped": 0}

    def assert_locked(self, run):
        fd = os.open(run.artifact / "profile/profile.lock", os.O_RDWR | os.O_CLOEXEC)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_pending_receipt_io_failure_preserves_exact_pending_and_lock(self):
        signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        before = {number: signal.getsignal(number) for number in signals}
        for number in (errno.ENOSPC, errno.EDQUOT, errno.EIO):
            for boundary in ("open", "partial_write", "fsync"):
                with self.subTest(errno=number, boundary=boundary), self.prepared() as prepared:
                    repo, artifact, settings, process_owner, holder = prepared
                    attempts, caught = [], []
                    def finish(**_kwargs):
                        attempts.append(True)
                        return self.report(len(attempts) > 2)
                    process_owner.finish.side_effect = finish
                    original_finish, original_write = owner._Run.finish, owner._write
                    def record_pending(run):
                        try:
                            return original_finish(run)
                        except owner.CleanupPending as pending:
                            caught.append(pending)
                            raise
                    def failed_receipt(path, value):
                        self.assert_locked(holder[0])
                        self.assertIs(signal.getsignal(signal.SIGTERM).__self__, holder[0].termination)
                        failure = OSError(number, "private disk diagnostic must not escape")
                        if boundary == "open":
                            with mock.patch.object(owner.os, "open", side_effect=failure):
                                original_write(path, value)
                        elif boundary == "partial_write":
                            path.write_bytes(b'{"schema":')
                            raise failure
                        else:
                            with mock.patch.object(owner.os, "fsync", side_effect=failure):
                                original_write(path, value)
                    with mock.patch.object(owner._Run, "finish", record_pending), \
                         mock.patch.object(owner, "_write", side_effect=failed_receipt):
                        with self.assertRaises(owner.CleanupPending) as raised:
                            owner.run_case(repo, artifact, settings)
                    pending = raised.exception
                    self.assertEqual(len(caught), 2)
                    self.assertIs(pending, caught[1])
                    self.assertIs(pending.run, holder[0])
                    self.assertEqual(pending.receipt["error"], "APP_WAYLAND_CLEANUP_PENDING")
                    self.assertEqual(pending.receipt["status"], "failed")
                    self.assertIs(pending.receipt["receipt_write_failed"], True)
                    self.assertNotIn("private disk diagnostic", json.dumps(pending.receipt))
                    self.assert_locked(pending.run)
                    if boundary == "open":
                        self.assertFalse((artifact / "wayland.json").exists())
                    else:
                        self.assertTrue((artifact / "wayland.json").exists())
                    result = owner.retry_cleanup(pending)
                    self.assertTrue(result["complete"])
                    self.assertIsNone(pending.run.profile_lock)
                    self.assertEqual({item: signal.getsignal(item) for item in signals}, before)

    def test_cleanup_io_error_retains_guard_and_lock_until_retry(self):
        with self.prepared() as prepared:
            repo, artifact, settings, process_owner, holder = prepared
            process_owner.finish.side_effect = [OSError(errno.EIO, "private scan text"),
                                                 OSError(errno.EIO, "private scan text"), self.report(True)]
            with self.assertRaises(owner.CleanupPending) as raised:
                owner.run_case(repo, artifact, settings)
            self.assertIs(raised.exception.run, holder[0])
            self.assert_locked(holder[0])
            self.assertTrue(holder[0].termination.previous)
            self.assertTrue(owner.retry_cleanup(raised.exception)["complete"])
            self.assertFalse(holder[0].termination.previous)

    def test_guard_installed_before_owner_and_restored_after_constructor_refusal(self):
        before = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        with self.prepared() as prepared:
            repo, artifact, settings, _process_owner, _holder = prepared
            def refuse():
                for number in before:
                    self.assertIsInstance(signal.getsignal(number).__self__, owner._TerminationGuard)
                raise owner.owner_api.OwnershipError("HELPER_NOT_DEDICATED")
            with mock.patch.object(owner.owner_api, "ProcessOwner", side_effect=refuse):
                result = owner.run_case(repo, artifact, settings)
            self.assertEqual(result["state"], "fail")
            self.assertEqual(result["error"], "HELPER_NOT_DEDICATED")
        self.assertEqual({number: signal.getsignal(number) for number in before}, before)

    def test_ready_expectations_and_final_receipt_use_the_selected_lifetime(self):
        for lifetime in ("run", "precursor"):
            limit = owner._lifetime_limits(lifetime)[1]
            valid = {"state": "complete", "client_cleanup_verified": True, "forced_disconnect": False,
                     "lifetime": lifetime, "run_limit_seconds": limit}
            mismatches = [dict(valid, lifetime="run" if lifetime == "precursor" else "precursor"),
                          dict(valid, run_limit_seconds=limit - 10), dict(valid, run_limit_seconds=limit + 1),
                          dict(valid, run_limit_seconds=float(limit)), dict(valid, run_limit_seconds=True),
                          {k: v for k, v in valid.items() if k not in ("lifetime", "run_limit_seconds")}]
            for proof in (valid, *mismatches):
                with self.subTest(lifetime=lifetime, proof=proof), self.prepared() as prepared:
                    repo, artifact, settings, process_owner, holder = prepared
                    def spawn(run, label, *_args, **_kwargs):
                        if label == "compositor":
                            holder.append(run)
                            (artifact / "compositor/result.json").write_text(json.dumps(proof))
                        elif label == "waypipe_client":
                            (run.runtime / "channel").touch()
                        child = mock.Mock()
                        child.poll.return_value = None
                        return child
                    process_owner.finish.return_value = self.report(True)
                    control = mock.Mock()
                    control.wait_main.return_value = "1"
                    control.snapshot.return_value = {"counts": {"clients": 0}, "windows": [], "observed_mapped": []}
                    control.wait.return_value = {"counts": {"clients": 0}, "windows": [],
                                                 "input": {"pressed": 0, "focus_window": None}}
                    with mock.patch.object(owner._Run, "spawn", spawn), \
                         mock.patch.object(owner._Run, "wait_child", return_value=0), \
                         mock.patch.object(owner._Run, "signal_root"), \
                         mock.patch.object(owner.stat, "S_ISSOCK", return_value=True), \
                         mock.patch.object(driver, "Control", return_value=control) as constructor, \
                         mock.patch.object(driver, "observe_ready", return_value={"observed_mapped_count": 1}):
                        result = owner.run_case(repo, artifact, settings, lifetime=lifetime, exercise=False)
                    self.assertEqual(constructor.call_args.kwargs["lifetime"], lifetime)
                    self.assertEqual(constructor.call_args.kwargs["run_limit_seconds"], limit)
                    self.assertTrue(result["cleanup"]["complete"])
                    if proof is valid:
                        self.assertEqual(result["state"], "partial")
                        self.assertIsNone(result["error"])
                    else:
                        self.assertEqual(result["state"], "fail")
                        self.assertEqual(result["error"], "APP_WAYLAND_COMPOSITOR_RECEIPT")

    def test_termination_during_final_cleanup_cannot_leave_a_partial_success(self):
        with self.prepared() as prepared:
            repo, artifact, settings, process_owner, holder = prepared
            def spawn_without_process(run, label, *_args, **_kwargs):
                if label == "compositor":
                    holder.append(run)
                    (artifact / "compositor/result.json").write_text(json.dumps({
                        "state": "complete", "client_cleanup_verified": True, "forced_disconnect": False,
                        "lifetime": run.lifetime, "run_limit_seconds": run.compositor_limit_seconds,
                    }))
                elif label == "waypipe_client":
                    (run.runtime / "channel").touch()
                child = mock.Mock()
                child.poll.return_value = None
                return child
            def complete_after_signal(**_kwargs):
                signal.raise_signal(signal.SIGTERM)
                return self.report(True)
            process_owner.finish.side_effect = complete_after_signal
            control = mock.Mock()
            control.wait_main.return_value = "1"
            control.snapshot.return_value = {"counts": {"clients": 0}, "windows": [], "observed_mapped": []}
            control.wait.return_value = {"counts": {"clients": 0}, "windows": [],
                                         "input": {"pressed": 0, "focus_window": None}}
            with mock.patch.object(owner._Run, "spawn", spawn_without_process), \
                 mock.patch.object(owner._Run, "wait_child", return_value=0), \
                 mock.patch.object(owner._Run, "signal_root"), \
                 mock.patch.object(owner.stat, "S_ISSOCK", return_value=True), \
                 mock.patch.object(driver, "Control", return_value=control), \
                 mock.patch.object(driver, "observe_ready", return_value={"observed_mapped_count": 1}), \
                 mock.patch.object(driver, "exercise_open_edit_save", return_value={"synthetic_unit_oracle": True}):
                result = owner.run_case(repo, artifact, settings)
            self.assertEqual(result["state"], "fail")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"], "APP_WAYLAND_INTERRUPTED")
            self.assertEqual(result["termination_signal"], signal.SIGTERM)
            self.assertTrue(result["cleanup"]["complete"])
            self.assertFalse(holder[0].termination.previous)


if __name__ == "__main__":
    unittest.main()
