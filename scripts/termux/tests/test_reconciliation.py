import concurrent.futures
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_supervisor import launcher


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home, self.nonce = Path(temporary.name), "c" * 16
        self.directory = Path(launcher.prepare(self.home, self.nonce)["socket"]).parent
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True)
        try:
            self.child = launcher.process_identity(process.pid)
        finally:
            process.terminate()
            process.wait()
        self.lease = {**launcher.marker(self.nonce), "owner": self.child, "child": self.child}
        self.expected = (str(os.getuid()), str(self.child["pid"]), self.child["start"])
        self.leasefile = self.directory / "lease.json"
        launcher.write_record(self.leasefile, self.lease)

    def audit(self, expected=None):
        return launcher.recovery_audit(self.home, self.nonce, expected)

    def test_legacy_requires_observed_boot_change_and_exact_origin(self):
        self.assertFalse(self.audit()["cleaned"])
        self.assertEqual(launcher.read_record(self.leasefile, self.nonce)["boot"], launcher.observation_boot())
        self.assertFalse(self.audit()["cleaned"])
        with mock.patch.object(launcher, "observation_boot", return_value="00000000-0000-4000-8000-000000000000"):
            self.assertTrue(self.audit()["recoverable"])
            for mismatch in [("0" if os.getuid() else "1", *self.expected[1:]), (*self.expected[:2], "0")]:
                with self.assertRaisesRegex(ValueError, "IDENTITY_MISMATCH"):
                    self.audit(mismatch)
                self.assertTrue(self.leasefile.is_file())
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                proofs = list(pool.map(self.audit, [self.expected] * 2))
        self.assertEqual(proofs[0], proofs[1])
        self.assertEqual(proofs[0]["reconciliation"]["basis"], "HostRebooted")
        self.assertNotIn("receipt", proofs[0])
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.audit(), proofs[0])

    def test_dead_before_first_spawn_can_be_recovered_on_same_boot(self):
        launcher.write_record(self.leasefile, {**self.lease, "phase": "before_launch", "child": None})
        self.assertTrue(self.audit()["recoverable"])
        self.assertEqual(self.audit((str(os.getuid()), "0", "0"))["reconciliation"]["basis"], "BeforeLaunch")

    def test_live_owner_and_uncertain_spawn_stay_pending(self):
        launcher.write_record(self.leasefile, {**self.lease, "owner": launcher.process_identity(os.getpid())})
        self.assertTrue(self.audit()["active"])
        launcher.write_record(self.leasefile, {**self.lease, "phase": "starting", "child": None})
        self.assertFalse(self.audit()["cleaned"])
        self.assertTrue(self.directory.exists())

    def test_retry_after_owner_verified_exit_preserves_live_socket_and_unknown_file(self):
        unknown = self.directory / "unexpected"
        unknown.touch()
        with self.assertRaisesRegex(ValueError, "DIRECTORY_NOT_EMPTY"):
            launcher.finish_retirement(self.home, self.nonce, {"child": self.child})
        self.assertTrue(self.audit()["recoverable"])
        with self.assertRaisesRegex(ValueError, "DIRECTORY_NOT_EMPTY"):
            self.audit(self.expected)
        self.assertTrue(unknown.exists())
        unknown.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.directory / "p"))
            server.listen(1)
            with self.assertRaisesRegex(ValueError, "SOCKET_STILL_IN_USE"):
                self.audit(self.expected)
            self.assertTrue((self.directory / "p").is_socket())
        self.assertEqual(self.audit(self.expected)["reconciliation"]["basis"], "OwnerRetired")

    def test_crash_after_directory_removed_resumes_from_durable_intent(self):
        launcher.write_record(self.leasefile, {**self.lease, "phase": "before_launch", "child": None})
        original = launcher.save_proof
        def fail_receipt(path, proof):
            if path.name.startswith("r-"):
                raise OSError("injected")
            original(path, proof)
        expected = (str(os.getuid()), "0", "0")
        with mock.patch.object(launcher, "save_proof", side_effect=fail_receipt), self.assertRaises(OSError):
            self.audit(expected)
        self.assertFalse(self.directory.exists())
        with self.assertRaises(FileExistsError):
            launcher.prepare(self.home, self.nonce)
        self.assertEqual(self.audit(expected)["reconciliation"]["basis"], "BeforeLaunch")
