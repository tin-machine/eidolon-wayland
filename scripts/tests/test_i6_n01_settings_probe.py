"""C2 synthetic settings environment regressions, independent of N01 acceptance.

The observation helper stops before an executable runs. The existing GIO test
suite separately executes the actual module/probe with that captured environment.
"""
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts/linux")]
import eidolon_linux_desktop_v3 as helper


class SpawnCaptured(Exception):
    """Stop at the observation boundary; never replace the real probe result."""


def capture_probe_environment(environment, bundle_directory, *, synthetic=True):
    """Observe real C2 setup without substituting a successful child status."""
    observed = {}

    def capture(_owner, _profile, _drain, command, child_environment):
        observed.update(command=command, environment=child_environment)
        raise SpawnCaptured

    with mock.patch.object(helper, "spawn_tracked", side_effect=capture):
        try:
            helper.run_settings_probe(None, None, None, environment,
                                      SimpleNamespace(directory=bundle_directory), synthetic)
        except SpawnCaptured:
            return observed
    raise AssertionError("settings probe did not reach its spawn boundary")


class C2SettingsProbeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="eidolon-n01-settings-probe-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.persistent = self.root / "persistent"
        self.persistent.mkdir(mode=0o700)
        for name in ("home", "config", "data", "state"):
            (self.persistent / name).mkdir(mode=0o700)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.environment = {"HOME": str(self.persistent / "home"),
                            "XDG_CONFIG_HOME": str(self.persistent / "config"),
                            "XDG_DATA_HOME": str(self.persistent / "data"),
                            "XDG_STATE_HOME": str(self.persistent / "state"),
                            "XDG_RUNTIME_DIR": str(self.runtime)}
        self.saved = self.persistent / "config" / "saved-settings"
        self.saved.write_bytes(b"preserve-existing-settings")

    def test_synthetic_probe_matches_backend_home_without_changing_persistent_environment(self):
        before = dict(self.environment)
        observed = capture_probe_environment(self.environment, self.root / "bundle")
        self.assertEqual(observed["command"], [str(self.root / "bundle/linux-settings-probe"), "--check"])
        environment = observed["environment"]
        self.assertEqual(environment["XDG_CONFIG_HOME"], str(self.runtime / "probe/config"))
        self.assertEqual(environment["HOME"], str(self.runtime / "probe/home"))
        self.assertEqual(Path(environment["HOME"]), Path(environment["XDG_CONFIG_HOME"]).parent / "home")
        for directory in (self.runtime / "probe", Path(environment["HOME"]), Path(environment["XDG_CONFIG_HOME"])):
            info = directory.lstat()
            self.assertTrue(stat.S_ISDIR(info.st_mode))
            self.assertEqual((info.st_uid, stat.S_IMODE(info.st_mode)), (os.getuid(), 0o700))
        marker = self.runtime / "probe/settings-probe-v1"
        self.assertEqual(marker.read_bytes(), b"eidolon-settings-probe-v1\n")
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(self.environment, before)
        for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
            self.assertEqual(environment[name], before[name])
        self.assertEqual(self.saved.read_bytes(), b"preserve-existing-settings")
        self.assertFalse((self.persistent / "config/glib-2.0/settings/keyfile").exists())

    def test_nonsynthetic_probe_keeps_the_persistent_environment(self):
        before = dict(self.environment)
        observed = capture_probe_environment(self.environment, self.root / "bundle", synthetic=False)
        self.assertEqual(observed["environment"], before)
        self.assertEqual(self.environment, before)
        self.assertFalse((self.runtime / "probe").exists())
        self.assertEqual(self.saved.read_bytes(), b"preserve-existing-settings")


if __name__ == "__main__":
    unittest.main()
