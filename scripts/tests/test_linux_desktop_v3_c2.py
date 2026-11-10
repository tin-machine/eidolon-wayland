"""C2 normal-v3 helper ownership, recovery and B2 receipt regressions."""
from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/linux"))

import linux_desktop_v3_contract as C
import linux_desktop_v3_wire as W
import eidolon_linux_desktop_v3 as core
import eidolon_linux_persistent_profile as profile_api
from eidolon_linux_process_owner import ProcessOwner

GOLDEN = json.loads((ROOT / "scripts/tests/fixtures/linux-desktop-v3-contract.json").read_bytes())
RECORDS = {item["id"]: item for item in GOLDEN["records"]}
ORIGIN = "11111111-1111-4111-8111-111111111111"
CURRENT = "22222222-2222-4222-8222-222222222222"
SOURCES = {"linux_desktop_v3_contract.py": "1" * 64}


def binding(uid=None):
    pins = C.Pins.parse(RECORDS["pins"]["raw"].encode())
    value = C.parse_json(RECORDS["context"]["raw"].encode())
    if uid is not None:
        value["uid"] = uid
    context = C.Context.parse(C.canonical_json(value))
    return W.Binding(context, pins)


class PureC2Tests(unittest.TestCase):
    def test_receipt_shapes_are_exact_b2_contract(self):
        item = binding()
        complete = core.receipt(item, SOURCES, "-", ORIGIN, CURRENT, "receipt", "complete",
                                True, True, True, True)
        raw = C.canonical_json(complete) + b"\n"
        self.assertEqual(W.parse_receipt(raw, item, SOURCES, "-", 0), complete)

        changed = core.receipt(item, SOURCES, ORIGIN, ORIGIN, CURRENT, "boot_changed",
                               "complete", True, False, True, True)
        raw = C.canonical_json(changed) + b"\n"
        self.assertEqual(W.parse_receipt(raw, item, SOURCES, ORIGIN, 0), changed)

        unknown = core.receipt(item, SOURCES, "-", None, CURRENT, "unknown", "pending",
                               False, False, False, False)
        raw = C.canonical_json(unknown) + b"\n"
        self.assertEqual(W.parse_receipt(raw, item, SOURCES, "-", 1), unknown)

    def test_product_source_has_no_saved_pid_signal_or_dynamic_import(self):
        source = (ROOT / "scripts/linux/eidolon_linux_desktop_v3.py").read_text()
        session = (ROOT / "scripts/linux/eidolon_linux_desktop_session_v3.py").read_text()
        for forbidden in ("__import__(", "os.kill(", "os.killpg(", "subprocess.Popen("):
            self.assertNotIn(forbidden, source + session)
        self.assertIn('FIXED_APP = ("/usr/bin/mousepad", "--disable-server")', source)
        self.assertIn("profile.track_process(child)", source)
        self.assertIn("profile.release()", source)

    def test_bus_template_has_one_fixed_socket_placeholder(self):
        raw = (ROOT / "scripts/linux/linux-desktop-v3-bus.conf.in").read_bytes()
        self.assertEqual(raw.count(b"@EIDOLON_BUS_SOCKET@"), 1)
        self.assertNotIn(b"<include", raw)
        self.assertNotIn(b"<includedir", raw)


@unittest.skipUnless(
    sys.platform == "linux" and os.getuid() != 0 and hasattr(os, "pidfd_open"),
    "C2 real ownership tests require a non-root Linux account with pidfd",
)
class RealC2Tests(unittest.TestCase):
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
    unittest.main()
