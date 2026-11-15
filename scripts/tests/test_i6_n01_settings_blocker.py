"""Record the current frozen C2 settings-probe blocker without claiming N01 pass.

This observes the unchanged helper's spawn environment before any executable
runs. It is not an actual GIO test, profile admission, or SSH acceptance result.
"""
from pathlib import Path
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


class FrozenC2SettingsBlockerTests(unittest.TestCase):
    def test_synthetic_probe_breaks_the_backend_home_contract(self):
        with tempfile.TemporaryDirectory(prefix="eidolon-n01-settings-blocker-") as temporary:
            root = Path(temporary)
            persistent = root / "persistent"
            persistent.mkdir(mode=0o700)
            for name in ("home", "config", "data", "state"):
                (persistent / name).mkdir(mode=0o700)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            original = {"HOME": str(persistent / "home"),
                        "XDG_CONFIG_HOME": str(persistent / "config"),
                        "XDG_DATA_HOME": str(persistent / "data"),
                        "XDG_STATE_HOME": str(persistent / "state"),
                        "XDG_RUNTIME_DIR": str(runtime)}
            observed = {}

            def capture(_owner, _profile, _drain, command, environment):
                observed.update(command=command, environment=environment)
                raise SpawnCaptured

            bundle = SimpleNamespace(directory=root / "bundle")
            with mock.patch.object(helper, "spawn_tracked", side_effect=capture):
                with self.assertRaises(SpawnCaptured):
                    helper.run_settings_probe(None, None, None, original, bundle, True)

            self.assertEqual(observed["command"], [str(bundle.directory / "linux-settings-probe"), "--check"])
            environment = observed["environment"]
            self.assertEqual(environment["XDG_CONFIG_HOME"], str(runtime / "probe/config"))
            self.assertEqual(environment["HOME"], str(persistent / "home"))
            # The unchanged eidolon_settings_backend.c::profile_filename()
            # requires this exact HOME string and an existing private sibling
            # directory. A passing regression records the blocker, not a
            # successful settings backend or available application catalog.
            required_home = Path(environment["XDG_CONFIG_HOME"]).parent / "home"
            self.assertNotEqual(environment["HOME"], str(required_home))
            self.assertFalse(required_home.exists())
            self.assertEqual(original["XDG_CONFIG_HOME"], str(persistent / "config"))
            self.assertFalse((persistent / "config/glib-2.0/settings/keyfile").exists())


if __name__ == "__main__":
    unittest.main()
