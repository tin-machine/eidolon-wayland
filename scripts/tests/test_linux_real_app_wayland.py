"""Controller invariants; live Mousepad feasibility is a separate explicit gate."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_wayland as owner

driver = owner.driver
APP_ID = "org.xfce.mousepad"


def window(identifier="1", parent=None, *, app_id=APP_ID, valid=True, frame="1", life="Mapped"):
    return {"window": identifier, "parent": parent, "app_id": app_id, "app_id_valid": valid,
            "life": life, "frame": frame, "logical_size": {"width": 800, "height": 600}, "layers": 1}


def snapshot(*windows):
    return {"windows": list(windows), "observed_mapped": [{"window": w["window"], "parent": w["parent"],
              "app_id": w["app_id"], "app_id_valid": w["app_id_valid"], "dispatch_batch": 1}
              for w in windows], "input": {"delivered": 0, "pressed": 0}, "counts": {"clients": 1}}


def controller(*snapshots):
    value = object.__new__(driver.Control)
    value.main = None
    value.expected_app_id = None
    value.pump = lambda: None
    values = iter(snapshots)
    last = snapshots[-1]
    value.snapshot = lambda: next(values, last)
    value.focus = lambda _: None
    value.settle = lambda: None
    return value


class WindowEvidenceTests(unittest.TestCase):
    def test_main_requires_owned_frame_and_latches_identity(self):
        value = controller(snapshot(window(frame=None)), snapshot(window()))
        self.assertEqual(value.wait_main(APP_ID), "1")
        with self.assertRaisesRegex(driver.ProbeError, "MAIN_ALREADY_LATCHED"):
            value.wait_main(APP_ID)
        with self.assertRaisesRegex(driver.ProbeError, "MAIN_LIFETIME_LOST"):
            value.assert_topology(snapshot(window("2")))

    def test_main_first_rejects_prior_destroyed_or_initial_second_toplevel(self):
        prior = snapshot(window())
        prior["observed_mapped"].insert(0, {"window": "9"})
        for value in [prior, snapshot(window(), window("2", "1")), snapshot(window(parent="2"))]:
            with self.assertRaises(driver.ProbeError):
                controller(value).wait_main(APP_ID)

    def test_dialog_uses_actual_transient_chain_and_accepts_absent_appid(self):
        value = controller(snapshot(window()))
        value.wait_main(APP_ID)
        value.assert_topology(snapshot(window(), window("2", "1", app_id=None), window("3", "2", app_id="")))
        invalid = [window("2"), window("2", "99"), window("2", "2"),
                   window("2", "1", app_id="org.other.app"), window("2", "1", app_id=None, valid=False)]
        for bad in invalid:
            with self.assertRaises(driver.ProbeError):
                value.assert_topology(snapshot(window(), bad))

    def test_metadata_alone_does_not_prove_frame_and_ids_are_not_lossy_numbers(self):
        self.assertFalse(driver.has_frame(window(frame=None)))
        self.assertFalse(driver.has_frame(window(frame=1)))
        self.assertTrue(driver.has_frame(window(frame="9223372036854775807")))
        for identifier in ["0", "01", "-1", 1, "9223372036854775808", "١"]:
            with self.assertRaises(driver.ProbeError):
                driver.mapped(snapshot(window(identifier)))
        with self.assertRaisesRegex(driver.ProbeError, "DUPLICATE"):
            driver.mapped(snapshot(window(), window()))


class InputAndOracleTests(unittest.TestCase):
    def test_failed_chord_releases_already_pressed_modifiers(self):
        events = []
        class Fault:
            def key(self, window, code, pressed):
                events.append((code, pressed))
                if code == 31 and pressed:
                    raise driver.ProbeError("TEST_KEY_FAILED")
        with self.assertRaisesRegex(driver.ProbeError, "TEST_KEY_FAILED"):
            driver.chord(Fault(), "1", driver.CTRL, driver.SHIFT, 31)
        self.assertEqual(events[-2:], [(driver.SHIFT, False), (driver.CTRL, False)])

    def test_ascii_is_real_bounded_evdev_sequence(self):
        events = []
        class Keys:
            def key(self, window, code, pressed):
                events.append((window, code, pressed))
        driver.type_ascii(Keys(), "1", "a_A/\n")
        self.assertIn(("1", 30, True), events)
        self.assertIn(("1", driver.SHIFT, True), events)
        self.assertEqual(events[-2:], [("1", driver.ENTER, True), ("1", driver.ENTER, False)])
        for text in ["", "日本語", "a" * 1025, "\\"]:
            with self.assertRaisesRegex(driver.ProbeError, "TEXT_UNSUPPORTED"):
                driver.type_ascii(Keys(), "1", text)

    def test_file_oracle_rejects_unchanged_data_extra_bytes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "output.txt"
            path.write_bytes(b"before\n")
            with self.assertRaisesRegex(driver.ProbeError, "FILE_ORACLE_TIMEOUT"):
                driver.wait_file_bytes(path, b"after\n", .025)
            path.write_bytes(b"after\nextra")
            with self.assertRaisesRegex(driver.ProbeError, "FILE_ORACLE_TIMEOUT"):
                driver.wait_file_bytes(path, b"after\n", .025)
            path.write_bytes(b"after\n")
            driver.wait_file_bytes(path, b"after\n", .025)
            link = path.with_name("link.txt")
            link.symlink_to(path)
            with self.assertRaisesRegex(driver.ProbeError, "FILE_ORACLE_SYMLINK"):
                driver.wait_file_bytes(link, b"after\n", .025)

    def test_no_api_probe_can_make_gui_checks_pass(self):
        self.assertIn("preferences_widgets_read_only", driver.PENDING_CHECKS)
        self.assertIn("settings_persist_after_restart", driver.PENDING_CHECKS)
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary)
            # No runtime dependency is invoked, and unsupported prerequisites
            # must be a failure receipt rather than a skipped successful gate.
            # Keep the actual temp directory owner/UID relationship intact on
            # both root local environments and non-root GitHub runners.
            with mock.patch.object(owner.sys, "platform", "unsupported"):
                result = owner.run_case(ROOT, artifact, artifact / "missing-settings")
            self.assertEqual(result["state"], "fail")
            self.assertFalse(result["feasibility_verified"])
            self.assertEqual(result["error"], "APP_WAYLAND_NONROOT_REQUIRED")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["pending"], list(owner.REQUIRED_CHECKS))
            self.assertEqual(set(result["checks"]), set(owner.REQUIRED_CHECKS))
            self.assertFalse(result["checks"]["protected_settings_gui_readonly"])
            self.assertFalse(result["checks"]["gui_setting_persistence"])
            self.assertFalse(result["checks"]["clean_close_restart"])
            self.assertTrue((artifact / "wayland.json").is_file())


class ControlTransportTests(unittest.TestCase):
    def child(self, behavior):
        ready = {"schema": 1, "event": "ready", "socket_name": "wayland-l7a",
                 "protocol": "eidolon-a0-control-v1", "server": "Eidolon ProtocolState"}
        script = "import json,sys,time\nprint(" + repr(json.dumps(ready)) + ",flush=True)\n" + behavior
        child = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(self.cleanup, child)
        return child

    @staticmethod
    def cleanup(child):
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()

    def test_transport_requires_matching_reply_and_drains_auxiliary_pump(self):
        child = self.child("for line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({'id':r['id'],'ok':True,'snapshot':{'windows':[]}}),flush=True)\n")
        pumped = []
        control = driver.Control(child, pump=lambda: pumped.append(True))
        self.assertEqual(control.snapshot(), {"windows": []})
        self.assertTrue(pumped)

    def test_sequence_mismatch_rejected(self):
        child = self.child("sys.stdin.readline()\nprint('{\"id\":99,\"ok\":true,\"snapshot\":{\"windows\":[]}}',flush=True)\n")
        with self.assertRaisesRegex(driver.ProbeError, "REPLY_SEQUENCE"):
            driver.Control(child).snapshot()

    def test_stalled_reply_has_deadline(self):
        child = self.child("sys.stdin.readline()\ntime.sleep(2)\n")
        control = driver.Control(child, timeout=.2)
        with self.assertRaisesRegex(driver.ProbeError, "CONTROL_TIMEOUT"):
            control.snapshot()

    def test_duplicate_json_fields_rejected(self):
        child = self.child("sys.stdin.readline()\nprint('{\"id\":1,\"id\":1,\"ok\":true,\"snapshot\":{\"windows\":[]}}',flush=True)\n")
        with self.assertRaisesRegex(driver.ProbeError, "DUPLICATE_FIELD"):
            driver.Control(child).snapshot()


if __name__ == "__main__":
    unittest.main()
