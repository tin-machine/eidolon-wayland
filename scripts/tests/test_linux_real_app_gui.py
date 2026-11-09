"""Independent disk/coordinate refusal checks for the synthetic GUI oracle."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_wayland as app
import linux_real_app_gui as gui


class DiskOracleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = app.SyntheticProfile.create(Path(self.temp.name) / "profile")
        self.lock = self.profile.acquire()
        self.addCleanup(os.close, self.lock)

    def write(self, data):
        path = self.profile.root / "config/glib-2.0/settings/keyfile"
        path.parent.parent.mkdir(mode=0o700, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def read(self):
        return gui.read_line_numbers(self.profile, self.lock)

    def test_absent_and_real_false_remain_distinct_without_creating_a_file(self):
        before = self.profile.snapshot(self.lock)
        self.assertEqual(self.read(), {"saved_present": False, "saved_value": None})
        self.assertEqual(self.profile.snapshot(self.lock), before)
        path = self.write(b"[org/xfce/mousepad/preferences/view]\nshow-line-numbers=false\n")
        original = path.read_bytes()
        self.assertEqual(self.read(), {"saved_present": True, "saved_value": False})
        self.assertEqual(path.read_bytes(), original)

    def test_only_exact_section_key_and_gvariant_boolean_are_evidence(self):
        self.write(b"[org/xfce/mousepad/preferences/window]\nshow-line-numbers=true\n")
        self.assertEqual(self.read(), {"saved_present": False, "saved_value": None})
        self.write(b"[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n")
        self.assertEqual(self.read(), {"saved_present": True, "saved_value": True})
        for token in (b"1", b"yes", b"True", b"'true'", b"@b true"):
            with self.subTest(token=token):
                self.write(b"[org/xfce/mousepad/preferences/view]\nshow-line-numbers=" + token + b"\n")
                with self.assertRaisesRegex(app.WaylandError, "APP_GUI_SETTINGS_TYPE"):
                    self.read()

    def test_ambiguous_syntax_defaults_and_duplicate_values_cannot_certify_gui(self):
        for data in (b"[invalid", b"\xff", b"[DEFAULT]\nshow-line-numbers=true\n",
                     b"[org/xfce/mousepad/preferences/view]\nshow-line-numbers=false\nshow-line-numbers=true\n"):
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaisesRegex(app.WaylandError, "APP_GUI_SETTINGS_SYNTAX"):
                    self.read()

    def test_symlink_hardlink_and_nonprivate_file_never_become_missing_default(self):
        for kind in ("symlink", "hardlink", "mode"):
            with self.subTest(kind=kind):
                path = self.write(b"[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n")
                outside = self.profile.root / "foreign"
                if kind == "mode":
                    path.chmod(0o644)
                elif kind == "hardlink":
                    os.link(path, outside)
                else:
                    path.rename(outside)
                    path.symlink_to(outside)
                with self.assertRaises((OSError, app.inspect_api.InspectionError)):
                    self.read()
                path.unlink()
                if outside.exists():
                    outside.unlink()


class GuiObservationRefusalTests(unittest.TestCase):
    def test_click_point_subtracts_shadow_origin_once_and_refuses_outside_or_unknown(self):
        widget = {"present": True, "mapped": True, "center": [42, 31], "size": [10, 8]}
        window = {"geometry_origin": {"x": 2, "y": 1}, "logical_size": {"width": 80, "height": 60}}
        self.assertEqual(gui.Experiment.point(widget, window), (.5, .5))
        for invalid in (dict(widget, mapped=False), dict(widget, present=False),
                        dict(widget, center=[1, 1]), dict(widget, center=[float("nan"), 31]),
                        dict(widget, center=[1000, 31])):
            with self.subTest(invalid=invalid), self.assertRaises(app.WaylandError):
                gui.Experiment.point(invalid, window)
        with self.assertRaises(app.WaylandError):
            gui.Experiment.point(widget, dict(window, geometry_origin=None))

    def test_checked_menu_alone_without_matching_actual_source_view_cannot_pass(self):
        experiment = object.__new__(gui.Experiment)
        private = {"main": {"line_numbers": False}, "preferences": {"targets": {"line_numbers": {
            "present": True, "mapped": True, "sensitive": True, "active": True}}}}
        experiment.capture = lambda: (private, {}, {})
        with self.assertRaisesRegex(app.WaylandError, "APP_GUI_LINE_VIEW_DISAGREES"):
            experiment.view()

    def test_unmapped_or_insensitive_positive_widget_cannot_supply_view_evidence(self):
        for field in ("mapped", "sensitive"):
            experiment = object.__new__(gui.Experiment)
            target = {"present": True, "mapped": True, "sensitive": True, "active": False, field: False}
            experiment.capture = lambda: ({"main": {"line_numbers": False},
                    "preferences": {"targets": {"line_numbers": target}}}, {}, {})
            with self.subTest(field=field), self.assertRaisesRegex(app.WaylandError, "APP_GUI_LINE_WIDGET"):
                experiment.view()

    def test_unchanged_value_is_not_a_readonly_widget_observation(self):
        experiment = object.__new__(gui.Experiment)
        target = {"present": True, "mapped": True, "sensitive": True,
                  "expected_value": True, "value_digest": "f" * 64}
        experiment.capture = lambda: ({"preferences": {"targets": {"opening_mode": target}}}, {}, {})
        with self.assertRaisesRegex(app.WaylandError, "APP_GUI_PROTECTED_WIDGET_WRITABLE"):
            experiment.widget("opening_mode", combo=True)

    def test_keyboard_only_delivery_never_proves_pointer_click(self):
        experiment = object.__new__(gui.Experiment)
        experiment.prefs = "2"
        experiment.control = mock.Mock()
        experiment.control.click.return_value = {"input": {"delivered": 100, "pointer_buttons": 4,
                                                           "pressed": 0, "pointer_pressed": 0}}
        before = {"input_delivered": 10, "pointer_buttons": 4}
        experiment.capture = lambda: ({"preferences": {"targets": {"line_numbers": {}}}}, {}, before)
        experiment.point = lambda *args: (.5, .5)
        with self.assertRaisesRegex(app.WaylandError, "APP_GUI_CLICK_NOT_DELIVERED"):
            experiment.click("line_numbers", before)
        experiment.control.settle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
