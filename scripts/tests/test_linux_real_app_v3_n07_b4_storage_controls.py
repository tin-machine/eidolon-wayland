"""Actual FD/lock behavior of the B4 read-only persistent-control observer."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_desktop_v3_contract as contract
import linux_real_app_v3_n07_b4_storage as storage
from linux_real_app_v3_n07_b4_storage import DIR_FLAGS, TrustedStorage, directory_identity
from linux_real_app_v3_n07_settings import StorageKeyfileProbe


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
        self.observer.root = self.root
        fd = os.open(self.root, DIR_FLAGS)
        self.observer.chain = [(None, "/", fd, directory_identity(os.fstat(fd)))]
        self.observer.control_files = {}
        self.observer.data_dirs = {}
        self.observer.metadata = None
        self.observer.probe = None

    def test_linked_ancestor_accepts_one_link_and_preserves_bounded_failure_stats(self):
        info = os.fstat(self.observer.chain[-1][2])
        fields = {name: getattr(info, name) for name in (
            "st_dev", "st_ino", "st_uid", "st_mode", "st_nlink")}
        index, component = len(self.root.parts) - 1, self.root.name
        self.observer._ancestor_info(SimpleNamespace(**{**fields, "st_nlink": 1}),
                                     self.observer.uid, index, component)
        self.assertIsNone(self.observer.metadata)
        with self.assertRaisesRegex(ValueError, "UNSAFE_ANCESTOR"):
            self.observer._ancestor_info(SimpleNamespace(**{**fields, "st_nlink": 0}),
                                         self.observer.uid, index, component)
        failed = self.observer.metadata
        self.assertEqual(failed["revision"], "eidolon-i6-n07-b4-storage-rejected-v1")
        self.assertEqual(failed["status"], "rejected")
        self.assertEqual(failed["component_index"], index)
        self.assertEqual(failed["component"], component)
        self.assertEqual(failed["observed"]["nlink"], 0)
        self.assertEqual(set(failed["observed"]), {"dev", "ino", "uid", "mode", "nlink", "file_type"})

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


class ProbeRootBinding(unittest.TestCase):
    """Fault injection around real directory FDs, not a product C1 receipt."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root, self.foreign = base / "retained", base / "foreign"
        self.uid = os.getuid() or 12002
        for path in (self.root, self.foreign):
            path.mkdir(mode=0o700)
            if os.getuid() == 0:
                os.chown(path, self.uid, self.uid)
        record = contract.StorageRecord.parse(contract.canonical_json({
            "schema": 1, "legacy_installation": "1" * 32, "installation_id": "2" * 32,
            "profile_id": "remote-linux", "profile_binding": "3" * 64,
            "profile_storage_id": "4" * 32, "host_user_binding": "5" * 64}))
        self.context = {"schema": 1, "backend": "linux_desktop", "protocol_revision": contract.REVISION,
            "session": "1", "profile_revision": "1", "profile_binding": "3" * 64,
            "nonce": "6" * 16, "uid": self.uid, "installation_id": "2" * 32,
            "profile_storage_id": "4" * 32}
        self.owner = contract.StorageOwner.parse(contract.canonical_json({
            "schema": 1, "uid": self.uid, "installation_id": "2" * 32,
            "profile_storage_id": "4" * 32, "app_id": contract.EDITOR,
            "state_policy": "persistent-keyfile-locked-v1"})).canonical()
        # Only the previously authenticated C1 chain setup is substituted.
        # All probe opens, fstat comparisons and cleanup use actual FDs.
        self.observer = TrustedStorage.__new__(TrustedStorage)
        self.observer.uid, self.observer.record = self.uid, record
        self.observer.home = Path("/home/eidolon-app")
        self.observer.root = self.root
        fd = os.open(self.root, DIR_FLAGS)
        self.observer.chain = [(None, "/", fd, directory_identity(os.fstat(fd)))]
        self.observer.control_files, self.observer.data_dirs = {}, {}
        self.observer.metadata, self.observer.probe = None, None

    def tearDown(self):
        self.observer.close()
        self.temporary.cleanup()

    def test_foreign_probe_open_is_rejected_and_both_fds_are_closed(self):
        held = self.observer.chain[-1][2]
        opened = []

        def redirected_probe(_path, uid, digest, *, diagnostic=False):
            probe = StorageKeyfileProbe(self.foreign, uid, digest, diagnostic=diagnostic)
            opened.append(probe.fd)
            return probe

        with patch.object(self.observer, "_pin_chain"), \
                patch.object(self.observer, "_read_owner", return_value=self.owner), \
                patch.object(storage, "StorageKeyfileProbe", side_effect=redirected_probe):
            with self.assertRaisesRegex(ValueError, "PROBE_ROOT_MISMATCH"):
                self.observer.open_after_inspect(self.context)
        self.assertIsNone(self.observer.probe)
        self.assertEqual(self.observer.chain, [])
        self.assertEqual(len(opened), 1)
        for fd in (held, opened[0]):
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_every_snapshot_binds_actual_fd_and_returned_proof(self):
        digest = self.observer.record.sha256()
        self.observer.probe = StorageKeyfileProbe(self.root, self.uid, digest)
        good = self.observer.snapshot()
        self.assertEqual((good["root_dev"], good["root_ino"]),
                         (self.root.stat().st_dev, self.root.stat().st_ino))
        for changed in ({"root_dev": good["root_dev"] + 1},
                        {"root_ino": good["root_ino"] + 1},
                        {"root_ino": True}, {"storage_digest": "f" * 64}):
            with self.subTest(changed=changed), \
                    patch.object(self.observer.probe, "snapshot", return_value={**good, **changed}):
                with self.assertRaisesRegex(ValueError, "PROBE_ROOT_MISMATCH"):
                    self.observer.snapshot()
        self.observer.probe.close()
        self.observer.probe = StorageKeyfileProbe(self.foreign, self.uid, digest)
        with self.assertRaisesRegex(ValueError, "PROBE_ROOT_MISMATCH"):
            self.observer.snapshot()


if __name__ == "__main__":
    unittest.main()
