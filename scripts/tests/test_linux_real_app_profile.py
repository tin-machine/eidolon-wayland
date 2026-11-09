"""Synthetic profile reuse and real ownership/GUI observation boundaries."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import signal
import selectors
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_wayland as wayland
import test_linux_real_app_wayland as controller_tests


def write_private(path, data):
    missing, cursor = [], path.parent
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


class ProfileIdentityTests(unittest.TestCase):
    def test_reuse_preserves_exact_lock_inode_and_bytes_across_independent_acquisition(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
            write_private(profile.documents / "saved.txt", b"synthetic saved bytes\n")
            write_private(profile.root / "config/glib-2.0/settings/keyfile",
                          b"[org/xfce/mousepad/state/window]\nwidth=800\nheight=600\n")
            before = profile.snapshot()
            identity, token = profile.lock_identity, profile.profile_id
            for _ in range(2):
                fd = profile.acquire()
                try:
                    # Independent OS process, not a reentrant in-process flock.
                    probe = subprocess.run([sys.executable, "-I", "-c",
                        "import fcntl,os,sys\nfd=os.open(sys.argv[1],os.O_RDWR)\n"
                        "try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                        "except BlockingIOError: sys.exit(0)\n"
                        "sys.exit(9)\n", str(profile.root / "profile.lock")],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                    self.assertEqual(probe.returncode, 0, probe.stderr)
                    self.assertEqual(wayland._inode(os.fstat(fd)), identity)
                    self.assertEqual(profile.snapshot(fd), before)
                finally:
                    os.close(fd)
            self.assertEqual(profile.profile_id, token)
            self.assertEqual(profile.snapshot(), before)
            self.assertGreater(before["preferences_count"], 0)

    def test_existing_directory_is_not_adopted_as_a_synthetic_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "profile"
            root.mkdir(mode=0o700)
            with self.assertRaises(FileExistsError):
                wayland.SyntheticProfile.create(root)
            self.assertFalse((root / "profile.lock").exists())

    def test_directory_or_lock_replacement_is_not_a_reusable_identity(self):
        for name in ("home", "config", "data", "state", "home/Documents", "profile.lock"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
                original = profile.root / name
                original.rename(original.with_name(original.name + "-old"))
                if name == "profile.lock":
                    write_private(original, b"")
                else:
                    original.mkdir(mode=0o700)
                with self.assertRaises(wayland.WaylandError):
                    profile.acquire()

    def test_root_and_ancestor_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "parent"
            parent.mkdir(mode=0o700)
            profile = wayland.SyntheticProfile.create(parent / "profile")
            parent.rename(parent.with_name("moved"))
            parent.symlink_to(parent.with_name("moved"), target_is_directory=True)
            with self.assertRaises(OSError):
                profile.acquire()

    def test_lock_replacement_while_old_inode_is_locked_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
            fd = profile.acquire()
            try:
                lock = profile.root / "profile.lock"
                lock.rename(profile.root / "detached.lock")
                write_private(lock, b"")
                with self.assertRaisesRegex(wayland.WaylandError, "PROFILE_LOCK_CHANGED"):
                    profile.validate(fd)
                with self.assertRaisesRegex(wayland.WaylandError, "PROFILE_LOCK_CHANGED"):
                    profile.acquire()
            finally:
                os.close(fd)

    def test_hardlinked_writable_and_nonempty_lock_are_refused(self):
        for fault in ("hardlink", "permissions", "contents"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
                lock = profile.root / "profile.lock"
                if fault == "hardlink":
                    os.link(lock, profile.root / "lock-alias")
                elif fault == "permissions":
                    lock.chmod(0o644)
                else:
                    lock.write_bytes(b"unowned identity\n")
                with self.assertRaisesRegex(wayland.WaylandError, "PROFILE_LOCK_CHANGED"):
                    profile.acquire()

    def test_directory_permissions_are_revalidated_on_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
            (profile.root / "config").chmod(0o755)
            with self.assertRaisesRegex(wayland.WaylandError, "PROFILE_CHANGED"):
                profile.acquire()

    def test_snapshot_retains_malformed_settings_bytes_without_certifying_preferences(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
            keyfile = profile.root / "config/glib-2.0/settings/keyfile"
            write_private(keyfile, b"[org/xfce/mousepad/state/application]\nsession=@unknown []\n")
            before = profile.snapshot()
            self.assertTrue(before["settings_present"])
            self.assertIsNone(before["preferences_sha256"])
            self.assertFalse(wayland.inspect_api.guard_recovery_state(profile.root)["clear"])
            self.assertEqual(profile.snapshot(), before)
            self.assertEqual(keyfile.read_bytes(), b"[org/xfce/mousepad/state/application]\nsession=@unknown []\n")

    def test_settings_oracle_excludes_history_but_detects_geometry_and_document_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
            keyfile = profile.root / "config/glib-2.0/settings/keyfile"
            base = b"[org/xfce/mousepad/state/window]\nwidth=800\nheight=600\n"
            write_private(keyfile, base + b"[org/xfce/mousepad/state/application]\nsession=[]\n")
            before = profile.snapshot()
            keyfile.write_bytes(base + b"[org/xfce/mousepad/state/application]\nsession=['synthetic']\n")
            history = profile.snapshot()
            self.assertNotEqual(before["settings_sha256"], history["settings_sha256"])
            self.assertEqual(before["preferences_sha256"], history["preferences_sha256"])
            keyfile.write_bytes(base.replace(b"800", b"900"))
            self.assertNotEqual(before["preferences_sha256"], profile.snapshot()["preferences_sha256"])
            write_private(profile.documents / "document.txt", b"preserved\n")
            self.assertNotEqual(before["documents_sha256"], profile.snapshot()["documents_sha256"])
            self.assertNotIn("preserved", json.dumps(profile.snapshot()))

    def test_snapshot_refuses_linked_files_without_reading_target_bytes(self):
        for link in ("symbolic", "hard"):
            with self.subTest(link=link), tempfile.TemporaryDirectory() as temporary:
                profile = wayland.SyntheticProfile.create(Path(temporary) / "profile")
                target = Path(temporary) / "outside"
                write_private(target, b"private outside bytes\n")
                if link == "symbolic":
                    (profile.documents / "linked").symlink_to(target)
                else:
                    os.link(target, profile.documents / "linked")
                with self.assertRaises((OSError, wayland.inspect_api.InspectionError)):
                    profile.snapshot()
                self.assertEqual(target.read_bytes(), b"private outside bytes\n")


class ProfileLaunchTests(unittest.TestCase):
    def test_nested_run_guard_retains_worker_request_between_and_during_runs(self):
        for boundary in ("between_runs", "inside_run"):
            with self.subTest(boundary=boundary):
                prior = {number: signal.getsignal(number) for number in
                         (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
                outer = wayland._TerminationGuard()
                inner = None
                try:
                    if boundary == "between_runs":
                        signal.raise_signal(signal.SIGTERM)
                    inner = wayland._TerminationGuard()
                    if boundary == "inside_run":
                        signal.raise_signal(signal.SIGTERM)
                    with self.assertRaisesRegex(wayland.WaylandError, "APP_WAYLAND_INTERRUPTED"):
                        inner.check()
                    inner.restore()
                    self.assertIs(signal.getsignal(signal.SIGTERM).__self__, outer)
                    with self.assertRaisesRegex(wayland.WaylandError, "APP_WAYLAND_INTERRUPTED"):
                        outer.check()
                finally:
                    if inner is not None:
                        inner.restore()
                    outer.restore()
                self.assertEqual({number: signal.getsignal(number) for number in prior}, prior)

    def test_owner_complete_without_pipe_eof_retains_profile_lock_and_signal_guard(self):
        with controller_tests.RunCaseCleanupTests().prepared() as prepared:
            repo, artifact, settings, process_owner, holder = prepared
            profile = wayland.SyntheticProfile.create(artifact.parent / "reused-profile")
            process_owner.finish.return_value = controller_tests.RunCaseCleanupTests.report(True)
            guard = wayland._TerminationGuard()
            run = wayland._Run(artifact, guard)
            run.profile_lock = profile.acquire()
            run.profile = profile
            read_fd, write_fd = os.pipe()
            reader = os.fdopen(read_fd, "rb", buffering=0)
            os.set_blocking(read_fd, False)
            run.pipes.append(reader)
            run.output_bytes["synthetic"] = 0
            run.selector.register(reader, selectors.EVENT_READ, "synthetic")
            try:
                with self.assertRaises(wayland.CleanupPending) as raised:
                    run.finish()
                self.assertIs(raised.exception.run, run)
                self.assertEqual(run.cleanup["reason"], "OUTPUT_PIPES_PENDING")
                self.assertFalse(run.cleanup["complete"])
                self.assertIs(signal.getsignal(signal.SIGTERM).__self__, guard)
                with self.assertRaises(BlockingIOError):
                    profile.acquire()
                os.close(write_fd)
                write_fd = None
                completed = wayland.retry_cleanup(raised.exception)
                self.assertTrue(completed["complete"])
                self.assertTrue(completed["output_pipes_eof"])
                self.assertIsNone(run.profile_lock)
                self.assertFalse(guard.previous)
                self.assertEqual(run.profile_after, profile.snapshot())
            finally:
                if write_fd is not None:
                    os.close(write_fd)
                if guard.previous:
                    run.finish()

    def test_seeded_recovery_is_rejected_before_any_process_spawn_and_retains_all_bytes(self):
        for seed in ("session", "autosave", "ambiguous"):
            with self.subTest(seed=seed), controller_tests.RunCaseCleanupTests().prepared() as prepared:
                repo, artifact, settings, process_owner, holder = prepared
                profile = wayland.SyntheticProfile.create(artifact.parent / "reused-profile")
                write_private(profile.documents / "sentinel.txt", b"synthetic saved document\n")
                keyfile = b"[org/xfce/mousepad/state/window]\nwidth=800\n[org/xfce/mousepad/state/application]\nsession="
                keyfile += {"session": b"['file:///synthetic-recovery']\n", "autosave": b"[]\n",
                            "ambiguous": b"@unknown []\n"}[seed]
                write_private(profile.root / "config/glib-2.0/settings/keyfile", keyfile)
                if seed == "autosave":
                    write_private(profile.root / "data/Mousepad/recovery", b"synthetic unsaved bytes\n")
                before = profile.snapshot()
                process_owner.finish.return_value = controller_tests.RunCaseCleanupTests.report(True)
                result = wayland.run_case(repo, artifact, settings, profile=profile, exercise=False)
                self.assertEqual(result["error"], "APP_STATE_RECOVERY_REQUIRED")
                self.assertEqual(result["state"], "fail")
                self.assertFalse(result["app_spawned"])
                self.assertIsNone(result["initial_main"])
                self.assertIsNone(result["documents_before_termination"])
                self.assertEqual(holder, [])
                process_owner.spawn.assert_not_called()
                self.assertEqual(result["profile_before"], before)
                self.assertEqual(result["profile_after"], before)
                self.assertEqual(profile.snapshot(), before)
                self.assertTrue(result["cleanup"]["complete"])

    def test_each_run_reacquires_same_profile_and_constructs_new_owner(self):
        with controller_tests.RunCaseCleanupTests().prepared() as prepared:
            repo, artifact, settings, process_owner, holder = prepared
            profile = wayland.SyntheticProfile.create(artifact.parent / "reused-profile")
            process_owner.finish.return_value = controller_tests.RunCaseCleanupTests.report(True)
            first = wayland.run_case(repo, artifact, settings, profile=profile, exercise=False)
            second_artifact = artifact.with_name("second-run")
            second_artifact.mkdir(mode=0o700)
            second = wayland.run_case(repo, second_artifact, settings, profile=profile, exercise=False)
            self.assertEqual(first["profile_id"], second["profile_id"])
            self.assertEqual(first["profile_after"], second["profile_before"])
            for key in ("run_id", "owner_scope_id", "runtime_id"):
                self.assertNotEqual(first[key], second[key])
            self.assertEqual(len(holder), 2)
            self.assertIsNot(holder[0], holder[1])
            self.assertIsNone(holder[0].profile_lock)
            self.assertIsNone(holder[1].profile_lock)

    def test_replaced_profile_lock_refuses_all_spawns(self):
        with controller_tests.RunCaseCleanupTests().prepared() as prepared:
            repo, artifact, settings, process_owner, holder = prepared
            profile = wayland.SyntheticProfile.create(artifact.parent / "reused-profile")
            lock = profile.root / "profile.lock"
            lock.rename(profile.root / "old.lock")
            write_private(lock, b"")
            result = wayland.run_case(repo, artifact, settings, profile=profile, exercise=False)
            self.assertEqual(result["error"], "APP_WAYLAND_PROFILE_LOCK_CHANGED")
            self.assertFalse(result["app_spawned"])
            self.assertEqual(holder, [])
            process_owner.spawn.assert_not_called()

    def test_saved_documents_are_compared_across_each_termination_not_only_restart(self):
        # Fixed runtime doubles isolate this byte oracle; genuine app endings
        # are exercised by the mandatory Mousepad job. Files here are real:
        # Save writes them, and owner cleanup fault injection removes/changes
        # them after the pre-termination snapshot but before the final one.
        for ending in ("close", "stop", "crash"):
            for mutation in (None, "delete", "change"):
                with self.subTest(ending=ending, mutation=mutation), \
                     controller_tests.RunCaseCleanupTests().prepared() as prepared:
                    repo, artifact, settings, process_owner, holder = prepared
                    profile = wayland.SyntheticProfile.create(artifact.parent / "reused-profile")
                    control = mock.Mock()
                    control.wait_main.return_value = "1"
                    control.snapshot.return_value = {"counts": {"clients": 0}, "windows": [], "observed_mapped": []}
                    control.wait.return_value = {"counts": {"clients": 0}, "windows": [],
                                                 "input": {"pressed": 0, "focus_window": None}}

                    def spawn(run, label, *_args, **_kwargs):
                        if label == "compositor":
                            holder.append(run)
                            (artifact / "compositor/result.json").write_text(json.dumps({
                                "state": "complete", "client_cleanup_verified": True, "forced_disconnect": False}))
                        elif label == "waypipe_client":
                            (run.runtime / "channel").touch()
                        child = mock.Mock()
                        child.test_label = label
                        child.poll.return_value = None
                        return child

                    def save(_control, source, destination, initial):
                        expected = initial + b"synthetic saved data\n"
                        source.write_bytes(expected)
                        write_private(destination, expected)
                        return {"synthetic_unit_oracle": True}

                    def cleanup(**_kwargs):
                        destination = profile.documents / "saved.txt"
                        if mutation == "delete":
                            destination.unlink()
                        elif mutation == "change":
                            destination.write_bytes(b"unexpected changed bytes\n")
                        return controller_tests.RunCaseCleanupTests.report(True)

                    process_owner.finish.side_effect = cleanup
                    with mock.patch.object(wayland._Run, "spawn", spawn), \
                         mock.patch.object(wayland._Run, "wait_child", side_effect=lambda child, _seconds:
                                           137 if ending == "crash" and child.test_label == "waypipe_server" else 0), \
                         mock.patch.object(wayland._Run, "signal_root"), \
                         mock.patch.object(wayland._Run, "crash_mousepad", return_value=True), \
                         mock.patch.object(wayland.stat, "S_ISSOCK", return_value=True), \
                         mock.patch.object(wayland.driver, "Control", return_value=control), \
                         mock.patch.object(wayland.driver, "observe_ready", return_value={"observed_mapped_count": 1}), \
                         mock.patch.object(wayland.driver, "exercise_open_edit_save", side_effect=save):
                        result = wayland.run_case(repo, artifact, settings, profile=profile, ending=ending)
                    self.assertTrue(result["cleanup"]["complete"])
                    self.assertEqual(len(result["documents_before_termination"]), 64)
                    if mutation is None:
                        self.assertEqual(result["state"], "partial")
                        self.assertIsNone(result["error"])
                        self.assertEqual(result["documents_before_termination"],
                                         result["profile_after"]["documents_sha256"])
                    else:
                        self.assertEqual(result["state"], "fail")
                        self.assertEqual(result["error"], "APP_WAYLAND_DOCUMENTS_CHANGED")
                        self.assertNotEqual(result["documents_before_termination"],
                                            result["profile_after"]["documents_sha256"])

    def test_document_loss_does_not_overwrite_original_termination_error(self):
        with controller_tests.RunCaseCleanupTests().prepared() as prepared:
            repo, artifact, settings, process_owner, holder = prepared
            profile = wayland.SyntheticProfile.create(artifact.parent / "reused-profile")
            document = profile.documents / "saved.txt"
            write_private(document, b"previously saved document\n")
            control = mock.Mock()
            control.wait_main.return_value = "1"
            control.snapshot.return_value = {"counts": {"clients": 0}, "windows": [], "observed_mapped": []}
            control.close.side_effect = wayland.driver.ProbeError("SYNTHETIC_CLOSE_ERROR")

            def spawn(run, label, *_args, **_kwargs):
                if label == "compositor":
                    holder.append(run)
                elif label == "waypipe_client":
                    (run.runtime / "channel").touch()
                child = mock.Mock()
                child.poll.return_value = None
                return child

            def cleanup(**_kwargs):
                document.unlink()
                return controller_tests.RunCaseCleanupTests.report(True)

            process_owner.finish.side_effect = cleanup
            with mock.patch.object(wayland._Run, "spawn", spawn), \
                 mock.patch.object(wayland.stat, "S_ISSOCK", return_value=True), \
                 mock.patch.object(wayland.driver, "Control", return_value=control), \
                 mock.patch.object(wayland.driver, "observe_ready", return_value={"observed_mapped_count": 1}):
                result = wayland.run_case(repo, artifact, settings, profile=profile, exercise=False)
            self.assertEqual(result["state"], "fail")
            self.assertEqual(result["error"], "SYNTHETIC_CLOSE_ERROR")
            self.assertTrue(result["cleanup"]["complete"])
            self.assertNotEqual(result["documents_before_termination"],
                                result["profile_after"]["documents_sha256"])


class ResponseOracleTests(unittest.TestCase):
    def test_main_ids_may_repeat_in_fresh_controls_but_old_latch_cannot_be_reused(self):
        first = controller_tests.controller(controller_tests.snapshot(controller_tests.window()))
        second = controller_tests.controller(controller_tests.snapshot(controller_tests.window()))
        self.assertEqual(first.wait_main(controller_tests.APP_ID), "1")
        self.assertEqual(second.wait_main(controller_tests.APP_ID), "1")
        with self.assertRaisesRegex(wayland.driver.ProbeError, "MAIN_ALREADY_LATCHED"):
            first.wait_main(controller_tests.APP_ID)

    def test_ready_observation_requires_single_actual_client_and_frame(self):
        good = controller_tests.snapshot(controller_tests.window())
        control = controller_tests.controller(good)
        control.wait_main(controller_tests.APP_ID)
        observed = wayland.driver.observe_ready(control)
        self.assertEqual(observed["client_count"], 1)
        self.assertEqual(observed["observed_mapped_count"], 1)
        for count in (0, 2, True):
            bad = dict(good, counts={"clients": count})
            control.snapshot = lambda: bad
            with self.assertRaisesRegex(wayland.driver.ProbeError, "RESPONSE_CLIENT_INPUT"):
                wayland.driver.observe_ready(control)

    def test_response_requires_new_dialog_acknowledgement_and_dismissal(self):
        # A delivered key counter is not an app response: absent dialog or an
        # undismissed dialog must fail even if the compositor accepted keys.
        for fault in ("dialog_absent", "dialog_retained", "no_input", None):
            with self.subTest(fault=fault):
                control = mock.Mock(spec=wayland.driver.Control)
                control.main = "1"
                if fault == "dialog_absent":
                    control.wait_dialog.side_effect = wayland.driver.ProbeError("DIALOG_TIMEOUT")
                else:
                    control.wait_dialog.return_value = "2"
                if fault == "dialog_retained":
                    control.wait_closed.side_effect = wayland.driver.ProbeError("DIALOG_CLOSE_TIMEOUT")
                before = {"window": "1", "frame": "3", "input_delivered": 10,
                          "client_count": 1, "pressed": 0}
                after = dict(before, frame="4", input_delivered=10 if fault == "no_input" else 16)
                with mock.patch.object(wayland.driver, "observe_ready", side_effect=[before, after]):
                    if fault:
                        with self.assertRaises(wayland.driver.ProbeError):
                            wayland.driver.exercise_response(control)
                    else:
                        result = wayland.driver.exercise_response(control)
                        self.assertTrue(result["responsive"])
                        self.assertTrue(result["topology_preserved"])
                        self.assertEqual(result["dialog_window"], "2")


if __name__ == "__main__":
    unittest.main()
