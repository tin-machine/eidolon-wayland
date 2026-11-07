import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdl_lease_capture import (CaptureError, canonical, capture_remote, decode, read_all,
                               select_snapshot, selected_environment, verify_following)

DIGEST = "c" * 64
NONCE = "d" * 16
BOOT = "00000000-0000-0000-0000-000000000001"


def snapshot():
    uid = os.getuid()
    child = dict(pid=200, start="20", group=200, state="S")
    workload = dict(schema=1, uid=uid, session="486", nonce=NONCE, capability="available", epoch="e" * 32,
                    sequence="1", sampled_monotonic_ms=100000, boot=BOOT,
                    registrations=[dict(terminal_id="1", pid=300, start="30", tty="/dev/pts/2")])
    remote = dict(phase="running", error=None, tools=dict(uid=uid), child=child, workload=workload)
    runtime = dict(schema=1, state="Ready", app=dict(state="running", session="486", nonce=NONCE, mode="foot", error=None),
                   transport=dict(state="Ready", session="486", app_forward=dict(state="running", nonce=NONCE, remote=remote),
                                  pending_cleanup=[dict(nonce=NONCE, session="486", uid=uid, child=copy.deepcopy(child))]))
    return dict(schema=1, sequence="9", process_id=700,
                build_info=dict(schema=1, source_commit="a" * 40, source_digest=DIGEST, source_dirty=False,
                                target="aarch64-linux-android", profile="debug", apk_version_code=149),
                runtime=dict(schema=1, code=0, handle="2", runtime=runtime))


class LeaseCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="eidolon-cap-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home, self.proc = self.root / "h", self.root / "p"
        self.home.mkdir(mode=0o700)
        self.lease = self.home / ".eidolon" / ("s-" + NONCE)
        self.lease.parent.mkdir(mode=0o700)
        self.lease.mkdir(mode=0o700)
        self.binding = select_snapshot(snapshot(), DIGEST, os.getuid())
        self.write(self.lease / "owner.json", canonical(dict(schema=1, nonce=NONCE, uid=os.getuid())))
        self.record = dict(schema=1, nonce=NONCE, uid=os.getuid(), owner=dict(pid=100, start="10", group=100, state="S"),
                           child=dict(pid=200, start="20", group=200, state="R"))
        self.write(self.lease / "lease.json", canonical(self.record))
        self.write(self.proc / "sys/kernel/random/boot_id", (BOOT + "\n").encode())
        for pid, start in ((100, "10"), (200, "20"), (300, "30")):
            self.write_stat(pid, start)
        self.env = b"PRIVATE_SECRET=do-not-save\0WAYLAND_DISPLAY=w\0XDG_RUNTIME_DIR=" + os.fsencode(self.lease) + b"\0"
        self.write(self.proc / "300/environ", self.env)
        self.peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.peer.bind(str(self.lease / "w"))
        self.addCleanup(self.peer.close)

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
        path.chmod(0o600)

    def write_stat(self, pid, start):
        fields = ["0"] * 22
        fields[0], fields[1], fields[2], fields[19] = "S", "1", str(pid), start
        self.write(self.proc / str(pid) / "stat", (f"{pid} (fixture with spaces) " + " ".join(fields)).encode())

    def capture(self, **kwargs):
        return capture_remote(self.binding, self.home, self.proc, clock=kwargs.pop("clock", lambda: 100.5), **kwargs)

    def test_active_journal_entry_is_accepted_and_only_two_environment_values_escape(self):
        self.assertEqual(self.binding["journal"], dict(active=1, unrelated=0))
        report = self.capture()
        self.assertEqual(report["status"], "captured")
        self.assertEqual(report["environment"], dict(WAYLAND_DISPLAY="w", XDG_RUNTIME_DIR=str(self.lease)))
        self.assertNotIn("PRIVATE_SECRET", json.dumps(report))
        self.assertNotIn("do-not-save", json.dumps(report))
        self.assertEqual(report["identities"]["root"], dict(pid=300, start="30"))

    def test_unrelated_old_journal_is_reported_separately(self):
        value = snapshot()
        value["runtime"]["runtime"]["transport"]["pending_cleanup"].append(dict(nonce="f" * 16, session="480", uid=os.getuid(), child=None))
        with self.assertRaisesRegex(CaptureError, "UNRELATED_CLEANUP_RECORDS") as caught:
            select_snapshot(value, DIGEST, os.getuid())
        self.assertEqual(caught.exception.details["journal"], dict(active=1, unrelated=1))

    def test_off_or_missing_registration_is_explicitly_missing(self):
        for field, body in (("capability", "unavailable"), ("registrations", [])):
            value = snapshot()
            value["runtime"]["runtime"]["transport"]["app_forward"]["remote"]["workload"][field] = body
            with self.subTest(field=field), self.assertRaises(CaptureError) as caught:
                select_snapshot(value, DIGEST, os.getuid())
            self.assertEqual(caught.exception.status, "missing")

    def test_source_native_schema_and_session_mismatches_are_rejected(self):
        variants = []
        value = snapshot();value["build_info"]["source_digest"] = "0" * 64;variants.append(value)
        value = snapshot();value["runtime"]["schema"] = True;variants.append(value)
        value = snapshot();value["runtime"]["runtime"]["transport"]["session"] = "487";variants.append(value)
        value = snapshot();value["runtime"]["runtime"]["transport"]["pending_cleanup"][0]["child"]["start"] = "99";variants.append(value)
        for value in variants:
            with self.assertRaises(CaptureError):
                select_snapshot(value, DIGEST, os.getuid())

    def test_second_snapshot_must_advance_and_preserve_owner_binding(self):
        value = snapshot();value["sequence"] = "10"
        after = select_snapshot(value, DIGEST, os.getuid())
        verify_following(self.binding, after)
        with self.assertRaisesRegex(CaptureError, "APK_BINDING_CHANGED_OR_STALE"):
            verify_following(self.binding, self.binding)
        after["registration"]["pid"] += 1
        with self.assertRaisesRegex(CaptureError, "APK_BINDING_CHANGED_OR_STALE"):
            verify_following(self.binding, after)

    def test_short_reads_are_drained_and_bounded(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"abcdef");os.close(write_fd)
        try:
            self.assertEqual(read_all(read_fd, 6, reader=lambda fd, count: os.read(fd, min(count, 1))), b"abcdef")
        finally:
            os.close(read_fd)
        read_fd, write_fd = os.pipe();os.write(write_fd, b"abcdefg");os.close(write_fd)
        try:
            with self.assertRaisesRegex(CaptureError, "READ_LIMIT"):
                read_all(read_fd, 6)
        finally:
            os.close(read_fd)

    def test_sample_expiry_boot_and_pid_reuse_are_rejected(self):
        with self.assertRaisesRegex(CaptureError, "LIVE_SAMPLE_EXPIRED"):
            self.capture(clock=lambda: 106)
        self.write(self.proc / "sys/kernel/random/boot_id", ("1" * 36).encode())
        with self.assertRaisesRegex(CaptureError, "BOOT_CHANGED"):
            self.capture()
        self.write(self.proc / "sys/kernel/random/boot_id", BOOT.encode())
        self.write_stat(300, "31")
        with self.assertRaisesRegex(CaptureError, "PROCESS_CHANGED"):
            self.capture()

    def test_actual_uid_process_owner_and_directory_mode_are_checked(self):
        self.binding["uid"] = os.getuid() + 1
        with self.assertRaisesRegex(CaptureError, "UID_MISMATCH"):
            self.capture()
        self.binding["uid"] = os.getuid()
        inode = (self.proc / "300").stat().st_ino
        actual = os.fstat

        def other_owner(fd):
            value = actual(fd)
            if value.st_ino == inode:
                fields = list(value);fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return value

        with patch("sdl_lease_capture.os.fstat", other_owner), self.assertRaisesRegex(CaptureError, "PROC_UID_MISMATCH"):
            self.capture()
        self.lease.chmod(0o770)
        with self.assertRaisesRegex(CaptureError, "LEASE_DIRECTORY_UNSAFE"):
            self.capture()

    def test_lease_and_environment_mismatch_are_not_repaired_or_guessed(self):
        self.record["child"]["start"] = "21"
        self.write(self.lease / "lease.json", canonical(self.record))
        with self.assertRaisesRegex(CaptureError, "LEASE_CHILD_MISMATCH"):
            self.capture()
        self.record["child"]["start"] = "20"
        self.write(self.lease / "lease.json", canonical(self.record))
        self.write(self.proc / "300/environ", self.env.replace(b"WAYLAND_DISPLAY=w", b"WAYLAND_DISPLAY=other"))
        with self.assertRaisesRegex(CaptureError, "ENVIRONMENT_LEASE_MISMATCH"):
            self.capture()
        with self.assertRaisesRegex(CaptureError, "ENVIRONMENT_MISSING"):
            selected_environment(b"PRIVATE_SECRET=do-not-save\0")
        with self.assertRaisesRegex(CaptureError, "ENVIRONMENT_INVALID"):
            selected_environment(self.env + b"WAYLAND_DISPLAY=w\0")

    def test_duplicate_or_oversize_json_is_rejected(self):
        with self.assertRaisesRegex(CaptureError, "DUPLICATE_FIELD"):
            decode(b'{"a":1,"a":2}')
        with self.assertRaisesRegex(CaptureError, "JSON_SIZE_INVALID"):
            decode(b"x" * 11, 10)

    def test_process_change_after_environment_read_and_late_expiry_are_rejected(self):
        original = selected_environment

        def changed(raw):
            result = original(raw)
            self.write_stat(300, "31")
            return result

        with patch("sdl_lease_capture.selected_environment", changed), self.assertRaisesRegex(CaptureError, "PROCESS_CHANGED"):
            self.capture()
        self.write_stat(300, "30")
        readings = iter([100.5, 106.0])
        with self.assertRaisesRegex(CaptureError, "LIVE_SAMPLE_EXPIRED"):
            self.capture(clock=lambda: next(readings))


if __name__ == "__main__":
    unittest.main()
