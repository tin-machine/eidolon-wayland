"""Run C2 kernel ownership tests in a dedicated process, never the test runner."""
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/tests"))
from test_linux_desktop_v3_c2 import (
    C, W, core, profile_api, ProcessOwner, binding, ORIGIN, CURRENT, SOURCES,
)


@unittest.skipUnless(
    sys.platform == "linux" and os.getuid() != 0 and hasattr(os, "pidfd_open"),
    "C2 real ownership tests require a non-root Linux account with pidfd",
)
class RealC2Cases(unittest.TestCase):
    def setUp(self):
        self.uid = os.getuid()
        self.temp = tempfile.TemporaryDirectory(prefix=".eidolon-c2-test-", dir=Path.home())
        self.home = Path(self.temp.name)
        self.home.chmod(0o700)
        self.account = SimpleNamespace(pw_uid=self.uid, pw_dir=str(self.home))
        self.patches = [
            mock.patch.object(core.pwd, "getpwuid", return_value=self.account),
            mock.patch.object(profile_api.pwd, "getpwuid", return_value=self.account),
        ]
        for patch in self.patches:
            patch.start()
        self.binding = binding(self.uid)

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        for path in sorted(self.home.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            try:
                mode = path.lstat().st_mode
                if path.is_symlink() or stat.S_ISREG(mode) or stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode):
                    path.unlink()
                else:
                    path.rmdir()
            except FileNotFoundError:
                pass
        self.temp.cleanup()

    def prepare(self, origin=ORIGIN):
        with mock.patch.object(core, "boot_id", return_value=origin):
            return core.prepare_session(self.binding.context)

    def abandoned_session(self):
        paths, barrier, home_fd, state_fd, session_fd = self.prepare()
        barrier.before_acquire()
        for fd in (session_fd, state_fd, home_fd):
            os.close(fd)
        return paths

    def recover(self, expected="-", current=CURRENT, binding=None, sources=None):
        item = self.binding if binding is None else binding
        hashes = SOURCES if sources is None else sources
        with mock.patch.object(core, "boot_id", return_value=current):
            raw, status = core.recovery_receipt(item, hashes, expected)
        return status, W.parse_receipt(raw, item, hashes, expected, status)

    def assert_boot_recovered(self, expected):
        paths = self.abandoned_session()
        for requested in (expected, expected, "-", ORIGIN):
            status, value = self.recover(requested)
            self.assertEqual(status, 0)
            self.assertEqual(value["process_evidence"], "boot_changed")
            self.assertFalse(value["pipes_eof"])
            self.assertEqual(value["origin_boot_id"], ORIGIN)
        self.assertFalse(paths.session.exists())

    def test_recover_repeated_known_boot_preserves_evidence(self):
        self.assert_boot_recovered(ORIGIN)

    def test_recover_repeated_unknown_boot_preserves_evidence(self):
        self.assert_boot_recovered("-")

    def test_existing_receipt_with_explicit_expected_boot_can_be_retried(self):
        paths, _, hfd, sfd, fd = self.prepare()
        hfd, sfd = core._remove_session(paths, (hfd, sfd, fd))
        saved = core.receipt(self.binding, SOURCES, ORIGIN, ORIGIN, CURRENT,
                             "boot_changed", "complete", True, False, True, True)
        core.write_final_receipt(sfd, self.binding.context.record()["nonce"], saved,
                                 self.binding, SOURCES, ORIGIN)
        os.close(sfd)
        os.close(hfd)
        for expected in (ORIGIN, "-"):
            status, value = self.recover(expected)
            self.assertEqual(status, 0)
            self.assertEqual(value["process_evidence"], "boot_changed")
            self.assertFalse(value["pipes_eof"])

    def test_same_boot_unknown_owner_is_never_cleaned(self):
        paths = self.abandoned_session()
        status, value = self.recover(ORIGIN, current=ORIGIN)
        self.assertEqual(status, 1)
        self.assertEqual(value["process_evidence"], "unknown")
        self.assertTrue((paths.session / "barrier.json").exists())

    def test_partial_proof_write_keeps_origin_and_is_retryable(self):
        paths = self.abandoned_session()
        original = core._write_new

        def partial(fd, name, raw, uid):
            if name.startswith(".c-"):
                original(fd, name, b"{", uid)
                raise OSError("injected proof write failure")
            return original(fd, name, raw, uid)

        with mock.patch.object(core, "_write_new", side_effect=partial):
            self.assertEqual(self.recover(ORIGIN)[0], 1)
        self.assertTrue((paths.session / "barrier.json").exists())
        self.assertEqual(self.recover(ORIGIN)[0], 0)
        self.assertFalse(paths.session.exists())
        self.assertEqual(len(list(paths.state.iterdir())), 1)

    def test_partial_final_write_retries_after_another_boot(self):
        paths = self.abandoned_session()
        original = core._write_new

        def partial(fd, name, raw, uid):
            if name.startswith(".r-"):
                original(fd, name, b"{", uid)
                raise OSError("injected final write failure")
            return original(fd, name, raw, uid)

        with mock.patch.object(core, "_write_new", side_effect=partial):
            self.assertEqual(self.recover(ORIGIN)[0], 1)
        self.assertFalse(paths.session.exists())
        self.assertEqual(len(list(paths.state.glob("c-*.json"))), 1)
        self.assertEqual(len(list(paths.state.glob("r-*.json"))), 0)
        # A cleanup proof must block a different Serve even after session deletion.
        value = self.binding.context.record()
        value["nonce"] = "f" * 16
        context = C.Context.parse(C.canonical_json(value))
        with self.assertRaisesRegex(core.HelperError, "APP_STATE_RECOVERY_REQUIRED"):
            core.prepare_session(context)
        status, value = self.recover(ORIGIN, current="33333333-3333-4333-8333-333333333333")
        self.assertEqual(status, 0)
        self.assertFalse(value["pipes_eof"])
        self.assertEqual(value["process_evidence"], "boot_changed")
        self.assertEqual(len(list(paths.state.iterdir())), 1)

    def test_cleanup_failure_after_barrier_removal_resumes_from_proof(self):
        paths = self.abandoned_session()
        original = core._remove_tree_at

        def fail_cache(fd, name, *args):
            if name == "cache":
                raise OSError("injected cleanup failure")
            return original(fd, name, *args)

        with mock.patch.object(core, "_remove_tree_at", side_effect=fail_cache):
            self.assertEqual(self.recover()[0], 1)
        self.assertFalse((paths.session / "barrier.json").exists())
        self.assertTrue(paths.session.exists())
        self.assertEqual(self.recover()[0], 0)
        self.assertFalse(paths.session.exists())

    def test_final_rename_fsync_failure_is_retryable(self):
        paths = self.abandoned_session()
        rename, fsync = core.os.rename, core.os.fsync
        fail = [False]

        def renamed(source, target, **kwargs):
            rename(source, target, **kwargs)
            if target.startswith("r-"):
                fail[0] = True

        def synced(fd):
            if fail[0]:
                fail[0] = False
                raise OSError("injected directory fsync failure")
            fsync(fd)

        with mock.patch.object(core.os, "rename", side_effect=renamed), \
                mock.patch.object(core.os, "fsync", side_effect=synced):
            self.assertEqual(self.recover()[0], 1)
        self.assertEqual(len(list(paths.state.glob("r-*.json"))), 1)
        self.assertEqual(len(list(paths.state.glob("c-*.json"))), 1)
        self.assertEqual(self.recover()[0], 0)
        self.assertEqual(len(list(paths.state.iterdir())), 1)

    def test_cleanup_proof_rejects_wrong_sources_and_origin_before_deletion(self):
        paths = self.abandoned_session()
        with mock.patch.object(core, "_recover_remove_session", side_effect=OSError("injected")):
            self.assertEqual(self.recover()[0], 1)
        before = (paths.session / "barrier.json").read_bytes()
        wrong_sources = {"linux_desktop_v3_contract.py": "2" * 64}
        self.assertEqual(self.recover(sources=wrong_sources)[0], 1)
        self.assertEqual(self.recover(expected=CURRENT)[0], 1)
        self.assertEqual((paths.session / "barrier.json").read_bytes(), before)
        self.assertEqual(self.recover()[0], 0)

    def test_state_lock_prevents_recover_of_live_serve(self):
        paths, barrier, hfd, sfd, fd = self.prepare()
        barrier.before_acquire()
        try:
            self.assertEqual(self.recover()[0], 1)
            self.assertTrue(paths.session.exists())
        finally:
            for item in (fd, sfd, hfd):
                os.close(item)
        self.assertEqual(self.recover()[0], 0)

    def test_final_receipt_cannot_hide_an_existing_session_or_reuse_nonce(self):
        paths = self.abandoned_session()
        self.assertEqual(self.recover()[0], 0)
        with self.assertRaisesRegex(core.HelperError, "CAPABILITY_STALE"):
            core.prepare_session(self.binding.context)
        paths.session.mkdir(mode=0o700)
        self.assertEqual(self.recover()[0], 1)
        self.assertTrue(paths.session.exists())

    def test_live_cleanup_receipt_failure_recovers_same_boot_and_preserves_documents(self):
        owner = ProcessOwner()
        paths, barrier, hfd, sfd, fd = self.prepare()
        handle = profile_api.PersistentProfileRepository().acquire(
            self.binding.context, process_owner=owner, barrier=barrier)
        document = handle.paths.documents / "preserved.txt"
        document.write_text("saved document")
        drain = core.Drain()
        core.cleanup_owner(owner, drain, handle)
        self.assertTrue(handle.closed)
        with mock.patch.object(core, "boot_id", return_value=ORIGIN), \
                mock.patch.object(core, "write_final_receipt", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                core.finish_session_cleanup(paths, (hfd, sfd, fd), self.binding, SOURCES, ORIGIN)
        os.close(sfd)
        os.close(hfd)
        drain.close()
        self.assertFalse(paths.session.exists())
        status, value = self.recover(ORIGIN, current=ORIGIN)
        self.assertEqual(status, 0)
        self.assertEqual(value["process_evidence"], "receipt")
        self.assertTrue(value["pipes_eof"])
        self.assertEqual(document.read_text(), "saved document")

    def test_output_limit_failure_still_reaps_child_and_releases_profile(self):
        import eidolon_linux_desktop_session_v3 as session
        owner = ProcessOwner()
        paths, barrier, hfd, sfd, fd = self.prepare()
        handle = profile_api.PersistentProfileRepository().acquire(
            self.binding.context, process_owner=owner, barrier=barrier)
        # Freeze replenishment to make the burst boundary independent of CI speed.
        drain = core.Drain(clock=lambda: 0.0)
        child = core.spawn_tracked(owner, handle, drain,
            [sys.executable, "-c", "import os,time; data=b'x'*16384\n"
             "for i in range(512): os.write(1,data)\ntime.sleep(10)"],
            {"PATH": "/usr/bin:/bin"})
        events = []
        emitter = SimpleNamespace(send=lambda sequence, kind, payload: events.append((kind, payload)))
        try:
            deadline = time.monotonic() + 5
            with self.assertRaisesRegex(core.HelperError, "APP_OUTPUT_RATE_LIMIT") as caught:
                while time.monotonic() < deadline:
                    drain.pump(.01)
            with mock.patch.object(core, "boot_id", return_value=ORIGIN):
                result = session._cleanup(self.binding, SOURCES, ORIGIN, 1, emitter,
                    owner, handle, drain, paths, (hfd, sfd, fd), "stop", caught.exception)
            self.assertEqual(result, 1)
            self.assertEqual(events, [("pending", {
                "reason": "pipe_eof", "public_reason": "APP_OUTPUT_RATE_LIMIT",
                "process_complete": True, "pipes_eof": True,
            })])
            report = owner.result
            self.assertTrue(report["complete"])
            self.assertGreater(report["term_signals"] + report["kill_signals"], 0)
            self.assertIsNotNone(child.poll())
            self.assertTrue(handle.closed)
            self.assertFalse(paths.session.exists())
            self.assertEqual(self.recover(ORIGIN, current=ORIGIN)[0], 0)
        finally:
            if owner.result is None:
                owner.finish(timeout=3)
            drain.close()

    def test_real_ephemeral_tree_cleanup_is_bounded_and_profile_separate(self):
        paths, barrier, home_fd, state_fd, session_fd = self.prepare()
        (paths.cache / "nested").mkdir(mode=0o700)
        data = paths.cache / "nested/data"
        data.write_bytes(b"cache")
        data.chmod(0o600)
        os.mkfifo(paths.runtime / "fifo", 0o600)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(paths.runtime / "socket"))
        sock.close()

        returned_home, returned_state = core._remove_session(
            paths, (home_fd, state_fd, session_fd))
        self.assertFalse(paths.session.exists())
        os.close(returned_state)
        os.close(returned_home)

    def test_second_os_process_refuses_stale_session(self):
        paths, barrier, home_fd, state_fd, session_fd = self.prepare()
        worker = ROOT / "scripts/tests/fixtures/linux_desktop_v3_c2_worker.py"
        raw = C.canonical_json(self.binding.context.record()).decode()
        result = subprocess.run(
            [sys.executable, str(worker), str(self.home), raw],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout.strip(), "APP_STATE_RECOVERY_REQUIRED")
        os.close(session_fd)
        os.close(state_fd)
        os.close(home_fd)

    def test_boot_change_recover_uses_barrier_without_saved_pid(self):
        paths, barrier, home_fd, state_fd, session_fd = self.prepare(ORIGIN)
        barrier.before_acquire()
        os.close(session_fd)
        os.close(state_fd)
        os.close(home_fd)

        with mock.patch.object(core, "boot_id", return_value=CURRENT):
            raw, status = core.recovery_receipt(self.binding, SOURCES, "-")
        self.assertEqual(status, 0)
        value = W.parse_receipt(raw, self.binding, SOURCES, "-", 0)
        self.assertEqual(value["process_evidence"], "boot_changed")
        self.assertEqual(value["origin_boot_id"], ORIGIN)
        self.assertFalse(value["pipes_eof"])
        self.assertFalse(paths.session.exists())

    @unittest.skipUnless(Path("/usr/bin/dbus-daemon").is_file(), "dbus-daemon is required")
    def test_real_private_bus_prints_canonical_address_and_is_reclaimed(self):
        owner = ProcessOwner()
        paths, barrier, home_fd, state_fd, session_fd = self.prepare()
        handle = profile_api.PersistentProfileRepository().acquire(
            self.binding.context, process_owner=owner, barrier=barrier)
        drain = core.Drain()
        template = (ROOT / "scripts/linux/linux-desktop-v3-bus.conf.in").read_bytes()
        fake_bundle = SimpleNamespace(files={core.BUS_TEMPLATE: template}, directory=ROOT)
        core.render_bus_config(fake_bundle, paths, session_fd, self.uid)
        env = core.launch_environment(handle, paths, fake_bundle)
        bus, address = core.start_private_bus(
            owner, handle, drain, env, paths.bus_config, paths.bus_socket)
        self.assertIsNone(bus.poll())
        self.assertTrue(address.startswith("unix:path="))
        self.assertIn(",guid=", address)
        self.assertTrue(paths.bus_socket.exists())
        report, evidence = core.cleanup_owner(owner, drain, handle)
        self.assertTrue(report["complete"])
        self.assertTrue(handle.closed)
        self.assertGreaterEqual(evidence.roots, 1)
        returned_home, returned_state = core._remove_session(
            paths, (home_fd, state_fd, session_fd))
        os.close(returned_state)
        os.close(returned_home)
        drain.close()

    def test_real_child_pipe_eof_precedes_profile_release(self):
        owner = ProcessOwner()
        paths, barrier, home_fd, state_fd, session_fd = self.prepare()
        handle = profile_api.PersistentProfileRepository().acquire(
            self.binding.context, process_owner=owner, barrier=barrier)
        drain = core.Drain()
        child = core.spawn_tracked(
            owner,
            handle,
            drain,
            [sys.executable, "-c", "import sys; print('c2'); print('e', file=sys.stderr)"],
            {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        deadline = time.monotonic() + 5
        while child.poll() is None and time.monotonic() < deadline:
            drain.pump(.01)

        report, evidence = core.cleanup_owner(owner, drain, handle)
        self.assertTrue(report["complete"])
        self.assertTrue(handle.closed)
        self.assertGreaterEqual(evidence.roots, 1)
        returned_home, returned_state = core._remove_session(
            paths, (home_fd, state_fd, session_fd))
        os.close(returned_state)
        os.close(returned_home)
        drain.close()


if __name__ == "__main__":
    os.umask(0o077)
    names = unittest.defaultTestLoader.getTestCaseNames(RealC2Cases)
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        print(json.dumps(names))
    elif len(sys.argv) == 2 and sys.argv[1] in names:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([RealC2Cases(sys.argv[1])]))
        print(json.dumps({"skipped": [reason for _, reason in result.skipped]}))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    else:
        raise SystemExit(2)
