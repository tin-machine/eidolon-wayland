import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from pairing import PairError, append_authorized, canonical_key, profile, update_authorized, verify_device, fresh_runtime


def key(byte):
    wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([byte]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode()


class PairTests(unittest.TestCase):
    def test_restart_needs_new_pid_and_its_own_current_build_snapshot(self):
        build = {"profile": "debug", "apk_version_code": 20}
        snapshot = {"process_id": 123, "build_info": build, "runtime": {"runtime": {"state": "Ready", "activity_visible": True}}}
        self.assertTrue(fresh_runtime(snapshot, "123", "122", build))
        for pid, old_pid in [("122", "122"), ("124", "122"), ("", "122"), ("0", "122")]:
            self.assertFalse(fresh_runtime(snapshot, pid, old_pid, build))
        self.assertFalse(fresh_runtime(snapshot, "123", "122", {"apk_version_code": 19}))
        snapshot["runtime"]["runtime"]["activity_visible"] = False
        self.assertFalse(fresh_runtime(snapshot, "123", "122", build))

    def test_device_and_host_pin_must_both_match_previous_pc_enrollment(self):
        physical = "synthetic-device"
        alias = "eidolon-quest-" + hashlib.sha256(physical.encode()).hexdigest()[:12]
        hosts = alias + " " + key(1) + "\n"
        verify_device(physical, hosts, key(1))
        for device, candidate in [("other-device", key(1)), (physical, key(2)), ("unknown", key(1))]:
            with self.assertRaises(PairError): verify_device(device, hosts, candidate)

    def test_installation_pin_and_username_are_validated_before_writing(self):
        public = {"schema": 1, "installation": "a" * 32, "public_key": key(1)}
        value = profile(public, "u0_a123", key(2))
        self.assertEqual(value, profile(public, "u0_a123", key(2), value))
        for changed in [dict(value, host_key=key(3)), dict(value, installation="b" * 32)]:
            with self.assertRaises(PairError): profile(public, "u0_a123", key(2), changed)
        for user in ["bad;command", "", "user\nother"]:
            with self.assertRaises(PairError): profile(public, user, key(2))
        for bad in ["ssh-rsa AAAA", "ssh-ed25519 invalid", key(1) + "\n" + key(2)]:
            with self.assertRaises(PairError): canonical_key(bad)

    def test_authorized_keys_preserves_existing_bytes_options_and_duplicates(self):
        previous = '# existing PC entry\r\nfrom="127.0.0.1" ' + key(1) + ' pc comment\r\n'
        self.assertEqual((previous, False), append_authorized(previous, key(1)))
        updated, changed = append_authorized(previous, key(2))
        self.assertTrue(changed and updated.startswith(previous))
        with tempfile.TemporaryDirectory(prefix="eidolon-pair-") as folder:
            parent = Path(folder) / ".ssh"
            parent.mkdir(mode=0o700)
            target = parent / "authorized_keys"
            target.write_bytes(previous.encode())
            self.assertTrue(update_authorized(target, key(2)))
            self.assertEqual(target.read_bytes(), updated.encode())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertFalse(update_authorized(target, key(2)))
            target.unlink()
            sentinel = Path(folder) / "sentinel"
            sentinel.write_text("another owner")
            target.symlink_to(sentinel)
            with self.assertRaises(PairError): update_authorized(target, key(2))
            self.assertEqual(sentinel.read_text(), "another owner")
