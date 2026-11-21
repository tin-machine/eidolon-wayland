"""Exercise the installed menu and its launch resolver under fresh XDG lookups."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "linux" / "eidolon_desktop_entries.py"
PYTHON = "/usr/bin/python3"


class InstalledMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        found = subprocess.run(
            [PYTHON, "-I", "-B", "-c", "from gi.repository import Gio, GioUnix"],
            capture_output=True,
        )
        if found.returncode:
            raise unittest.SkipTest("the Ubuntu discovery interpreter needs Gio and GioUnix")

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.user = self.root / "user" / "applications"
        self.system = self.root / "system" / "applications"
        self.user.mkdir(parents=True)
        self.system.mkdir(parents=True)
        self.env = dict(os.environ, XDG_DATA_HOME=str(self.user.parent),
                        XDG_DATA_DIRS=str(self.system.parent), LC_ALL="C")

    def entry(self, directory, desktop_id, fields=""):
        (directory / desktop_id).write_text(
            "[Desktop Entry]\nType=Application\nName=Sample\nExec=/usr/bin/true %U\n" + fields
        )

    def call(self, *args):
        return subprocess.run([PYTHON, "-I", "-B", str(SCRIPT), *args],
                              env=self.env, capture_output=True, timeout=5)

    def menu(self):
        listed = self.call()
        self.assertEqual(listed.returncode, 0, listed.stderr)
        return {row["id"]: row for row in json.loads(listed.stdout)["entries"]}

    def test_refresh_follows_add_removal_and_the_users_hidden_override(self):
        self.assertEqual(self.menu(), {})
        self.entry(self.system, "sample.desktop")
        original = self.menu()["sample.desktop"]["revision"]
        self.assertEqual(self.call("resolve", original).returncode, 0)

        self.entry(self.user, "sample.desktop", "Hidden=true\n")
        self.assertNotIn("sample.desktop", self.menu())
        self.assertNotEqual(self.call("resolve", original).returncode, 0)
        (self.user / "sample.desktop").unlink()
        self.assertIn("sample.desktop", self.menu())
        (self.system / "sample.desktop").unlink()
        self.assertEqual(self.menu(), {})
        self.assertNotEqual(self.call("resolve", original).returncode, 0)

    def test_removed_or_replaced_workdir_invalidates_selection(self):
        cwd = self.root / "app-workdir"
        cwd.mkdir()
        self.entry(self.user, "sample.desktop", f"Path={cwd}\n")
        original = self.menu()["sample.desktop"]
        result = self.call("resolve", original["revision"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["cwd"], str(cwd))

        cwd.rename(self.root / "old-workdir")
        unavailable = self.menu()["sample.desktop"]
        self.assertEqual(unavailable["disabled_reason"], "workdir_unavailable")
        self.assertNotEqual(unavailable["revision"], original["revision"])
        for item in (original, unavailable):
            self.assertNotEqual(self.call("resolve", item["revision"]).returncode, 0)

        cwd.mkdir()
        replacement = self.menu()["sample.desktop"]
        self.assertIsNone(replacement["disabled_reason"])
        self.assertNotEqual(replacement["revision"], original["revision"])
        self.assertNotEqual(self.call("resolve", original["revision"]).returncode, 0)
        self.assertEqual(self.call("resolve", replacement["revision"]).returncode, 0)

    def test_try_exec_tracks_executable_removal_and_replacement(self):
        binary = self.root / "fixture"
        shutil.copy2("/usr/bin/true", binary)
        (self.user / "sample.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName=Sample\nExec={binary} %U\nTryExec={binary}\n"
        )
        original = self.menu()["sample.desktop"]["revision"]

        binary.rename(self.root / "old-fixture")
        self.assertNotIn("sample.desktop", self.menu())
        self.assertNotEqual(self.call("resolve", original).returncode, 0)

        shutil.copy2("/usr/bin/true", binary)
        replacement = self.menu()["sample.desktop"]["revision"]
        self.assertNotEqual(replacement, original)
        self.assertNotEqual(self.call("resolve", original).returncode, 0)
        self.assertEqual(self.call("resolve", replacement).returncode, 0)
