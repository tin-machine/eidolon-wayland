"""B4 lifetime-bound filesystem observations; no GUI acceptance is inferred."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_n07_settings as settings


class RetainedStorageObservation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory = self.root.joinpath(*settings.COMPONENTS)
        self.path = self.directory / settings.LEAF

    def tearDown(self):
        self.temporary.cleanup()

    def probe(self):
        return settings.StorageKeyfileProbe(self.root, os.getuid(), "a" * 64)

    def write_true(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.write_text(
            "[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n",
            encoding="utf-8")
        self.path.chmod(0o600)

    def test_each_previously_observed_directory_replacement_is_rejected(self):
        for index in range(len(settings.COMPONENTS)):
            with self.subTest(component=settings.COMPONENTS[index]):
                self.write_true()
                with self.probe() as probe:
                    before = probe.snapshot()
                    directory = self.root.joinpath(*settings.COMPONENTS[:index + 1])
                    old = directory.with_name(directory.name + "-old")
                    directory.rename(old)
                    shutil.copytree(old, directory)
                    # Equal settings bytes/root identity cannot authorize a
                    # replacement of an intermediate persistent directory.
                    self.assertEqual(settings.sha(self.path.read_bytes()),
                                     before["file"]["sha256"])
                    with self.assertRaisesRegex(ValueError, "DIRECTORY_REPLACED"):
                        probe.snapshot()
                    shutil.rmtree(directory)
                    old.rename(directory)
                shutil.rmtree(self.root / settings.COMPONENTS[0])

    def test_observed_directory_disappearance_is_rejected(self):
        self.write_true()
        with self.probe() as probe:
            probe.snapshot()
            self.directory.rename(self.directory.with_name("old"))
            with self.assertRaisesRegex(ValueError, "DIRECTORY_REPLACED"):
                probe.snapshot()

    def test_first_creation_and_atomic_keyfile_close_flush_are_allowed(self):
        with self.probe() as probe:
            before = probe.snapshot()
            self.write_true()
            enabled = probe.snapshot()
            atomic = self.directory / "atomic-keyfile"
            atomic.write_bytes(self.path.read_bytes())
            atomic.chmod(0o600)
            atomic.replace(self.path)
            restarted = probe.snapshot()
            settings.validate_file_sequence(before, enabled, restarted)
            self.assertNotEqual(enabled["file"]["inode"], restarted["file"]["inode"])
            retained_fds = [fd for fd, _ in probe.directory_fds.values()]
        for fd in retained_fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_directory_one_link_is_valid_zero_links_and_regular_hardlinks_are_not(self):
        self.write_true()
        real_fstat = os.fstat

        def directory_links(fd, nlink):
            info = real_fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                return info
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                st_mode=info.st_mode, st_uid=info.st_uid, st_nlink=nlink)

        # Exercise every actual opened root/intermediate FD with the link
        # count reported by union filesystems, keeping identities unchanged.
        with patch.object(settings.os, "fstat", side_effect=lambda fd: directory_links(fd, 1)):
            with self.probe() as probe:
                self.assertTrue(probe.snapshot()["value"])
                os.link(self.path, self.path.with_name("hardlink"))
                with self.assertRaisesRegex(ValueError, "UNSAFE_KEYFILE"):
                    probe.snapshot()
        with patch.object(settings.os, "fstat", side_effect=lambda fd: directory_links(fd, 0)):
            with self.assertRaisesRegex(ValueError, "UNSAFE_DIRECTORY"):
                self.probe()

    def test_strict_saved_metadata_types_and_bounds(self):
        with self.probe() as probe:
            before = probe.snapshot()
            self.write_true()
            enabled = probe.snapshot()
            restarted = probe.snapshot()
        mutations = [
            ("root_dev", True), ("root_ino", True), ("root_ino", "7"),
            ("root_dev", -1), ("root_ino", 0), ("key_present", 1),
            ("value", 1), ("storage_digest", 0), ("others_sha256", "broken"),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                altered = copy.deepcopy(restarted)
                altered[field] = value
                with self.assertRaises(ValueError):
                    settings.validate_file_sequence(before, enabled, altered)
        for field in ("dev", "inode", "uid", "nlink", "size", "mtime_ns", "ctime_ns"):
            for value in (True, "1", -1):
                with self.subTest(file_field=field, value=value):
                    altered = copy.deepcopy(restarted)
                    altered["file"][field] = value
                    with self.assertRaises(ValueError):
                        settings.validate_file_sequence(before, enabled, altered)
        for value in (0, settings.MAX_BYTES + 1):
            altered = copy.deepcopy(restarted)
            altered["file"]["size"] = value
            with self.assertRaises(ValueError):
                settings.validate_file_sequence(before, enabled, altered)


if __name__ == "__main__":
    unittest.main()
