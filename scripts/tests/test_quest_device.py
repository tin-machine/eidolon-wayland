import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("quest_device", Path(__file__).parents[1] / "quest_device.py")
device = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device)


class PackageTests(unittest.TestCase):
    def test_probe_prefix_does_not_select_another_apps_logs(self):
        text = "package:io.github.tinmachine.eidolon.probe uid:123\npackage:io.github.tinmachine.eidolon uid:456\n"
        self.assertEqual(device.package_uid(text), 456)
        for invalid in ["", text.splitlines()[0], text + text]:
            with self.assertRaises(RuntimeError):
                device.package_uid(invalid)
