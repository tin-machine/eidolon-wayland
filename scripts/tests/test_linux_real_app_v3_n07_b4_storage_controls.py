"""Actual FD/lock behavior of the B4 read-only persistent-control observer."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linux_real_app_v3_n07_b4_storage import DIR_FLAGS, TrustedStorage, directory_identity


class RetainedControlFiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.owner = self.root / "owner.json"
        self.lock = self.root / "profile.lock"
        self.owner.write_bytes(b'{"controlled":"unit-fixture"}')
        self.owner.chmod(0o600)
        self.lock.touch(mode=0o600)
        # Exercise the FD primitive in an isolated synthetic directory. This
        # deliberately bypasses constructor authentication; it is not a
        # trusted C1 root or a positive product-acquisition receipt.
        self.observer = TrustedStorage.__new__(TrustedStorage)
        self.observer.uid = os.getuid()
        fd = os.open(self.root, DIR_FLAGS)
        self.observer.chain = [(None, "/", fd, directory_identity(os.fstat(fd)))]
        self.observer.control_files = {}
        self.observer.data_dirs = {}
        self.observer.metadata = None
        self.observer.probe = None

    def tearDown(self):
        self.observer.close()
        self.temporary.cleanup()

    def test_same_bytes_replacement_of_owner_or_lock_is_rejected(self):
        for path in (self.owner, self.lock):
            with self.subTest(path=path.name):
                self.observer._control_file(path.name)
                saved = path.read_bytes()
                replacement = path.with_name(path.name + ".new")
                replacement.write_bytes(saved)
                replacement.chmod(0o600)
                replacement.replace(path)
                self.assertEqual(path.read_bytes(), saved)
                with self.assertRaisesRegex(ValueError, "CONTROL_FILE_CHANGED"):
                    self.observer._control_file(path.name)

    def test_same_inode_changed_stamp_and_disappearance_are_rejected(self):
        _, pinned = self.observer._control_file("owner.json")
        os.utime(self.owner, ns=(pinned["mtime_ns"] + 100, pinned["mtime_ns"] + 100))
        with self.assertRaisesRegex(ValueError, "CONTROL_FILE_CHANGED"):
            self.observer._read_owner()
        self.observer._control_file("profile.lock")
        self.lock.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.observer._control_file("profile.lock")

    def test_observation_neither_acquires_nor_releases_product_flock(self):
        with self.lock.open("rb") as product:
            fcntl.flock(product, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed, _ = self.observer._control_file("profile.lock")
            self.assertEqual(self.observer._read_owner(), self.owner.read_bytes())
            self.observer.assert_current()
            self.observer.close()
            with self.assertRaises(OSError):
                os.fstat(observed)
            with self.lock.open("rb") as competing_product:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(competing_product, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(product, fcntl.LOCK_UN)

    def test_control_files_require_original_private_regular_single_link_shape(self):
        self.lock.write_bytes(b"not empty")
        with self.assertRaisesRegex(ValueError, "CONTROL_FILE_INVALID"):
            self.observer._control_file("profile.lock")
        self.owner.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "CONTROL_FILE_INVALID"):
            self.observer._read_owner()
        self.owner.chmod(0o600)
        alias = self.root / "owner-alias"
        os.link(self.owner, alias)
        with self.assertRaisesRegex(ValueError, "CONTROL_FILE_INVALID"):
            self.observer._read_owner()


if __name__ == "__main__":
    unittest.main()
