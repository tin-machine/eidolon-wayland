"""N07-B1 hostile diagnostic proof tests; no production N07 golden or 13/13."""
from __future__ import annotations

import base64
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linux_real_app_v3_n07_b1_probe import ProbeInvalid, inspect

TARGET = {"session": "1", "window": "7", "scene": "1", "generation": "1"}
RUN_ID = "c" * 32
CAPTURE = "a" * 64


def pixel_region(uid: int, x: int, y: int, width: int, height: int) -> dict:
    rgba = bytes([uid, x & 255, y & 255, 255]) * (width * height)
    return {
        "id": uid,
        "rect": {"x": x, "y": y, "width": width, "height": height},
        "rgba_b64": base64.b64encode(rgba).decode("ascii"),
        "sha256": hashlib.sha256(rgba).hexdigest(),
    }


def readback(phase: str, frame: int) -> dict:
    pixels = (pixel_region(1, 0, 64, 48, 128),
              pixel_region(2, 64, 16, 192, 128))
    return {
        "phase": phase,
        "readback": {
            "source": "host_renderer_readback", "target": TARGET,
            "create_seq": "18", "sdk_adapter": "9",
            "pixels": list(pixels),
            "receipt": {
                "target": TARGET, "runtime": "2", "create": "18",
                "renderer_owner": 15, "context_epoch": 3,
                "present_seq": frame, "content_frame": str(frame),
                "min_content_frame": str(frame - 1), "roi_revision": 1,
                "backend": {"vendor": "Mesa test", "renderer": "software"},
                "layers": [{"surface": "1"}, {"surface": "11"}],
                "pixel_sha256": [p["sha256"] for p in pixels],
            },
        },
    }


def diagnostic() -> dict:
    return {
        "schema": "eidolon-i6-n07-b1-native-probe-v1",
        "variant": "pointer_popup_readback_probe",
        "run_id": RUN_ID, "runtime": "2",
        "host_v3_acceptance_passed": False,
        "n07_assertions_verified": [],
        "readback": {"target": TARGET, "create": "18"},
        "trace": {"events": [
            {"capture": {"capture_id": CAPTURE}},
            {"kind": "remote_cleanup_observed", "data": {}},
            {"kind": "io_retired", "data": {}},
            {"kind": "journal_unlinked", "data": {}},
            {"kind": "finalizer_published", "data": {"status": 0}},
        ]},
        "error": None, "cleanup_error": None,
        "sdk_retired": True, "destroy_status": 0,
        "registry_counts": {"runtimes": 0, "workers": 0},
        "last_snapshot": {"runtime": {
            "state": "Stopped",
            "reclamation": {
                "origin_retired": True, "shutdown_complete": True,
                "sdk_bindings": 0, "native_targets": 0,
                "panels": 0, "pending_operations": 0,
            },
            "render": {
                "targets": [], "queued": 0, "error": None,
                "composition": {"textures": 0, "texture_bytes": 0,
                                "decoration_texture_bytes": 0, "gl_error": 0},
            },
        }},
        "n07_probe": {
            "schema": 1, "run_id": RUN_ID,
            "variant": "pointer_popup_readback_probe",
            "capture_id": CAPTURE, "main_target": TARGET,
            "menu_coordinate_reviewed": False, "menu_identity_verified": False,
            "pointer": {
                "target": TARGET, "source": "1", "input_epoch": 3,
                "epoch_after": 3, "uv": [0.18, 0.021],
                "coordinate_reviewed": False,
                "provenance": "normal_registry_command_and_compositor_forward",
                "before": {"delivered_buttons": 3},
                "after": {
                    "delivered_buttons": 5, "last_delivered_source": "1",
                    "last_delivered_surface": "11", "last_delivered_serial": 40,
                    "last_delivered_press_serial": 39,
                    "hover_window": "7", "pressed": 0,
                },
            },
            "before_menu": readback("before_menu", 20),
            "menu_open": readback("menu_open", 21),
            "popup": {
                "role": "xdg_popup", "root_window": "7",
                "captured_origin": True, "dismissed": False,
                "surface": "11", "parent_surface": "1",
                "ack_serial": 66, "grab_serial": 39, "layer": 1,
            },
        },
    }


class N07B1DiagnosticTests(unittest.TestCase):
    def test_good_synthetic_diagnostic_is_probe_only_never_acceptance(self):
        result = inspect(diagnostic())
        self.assertEqual(result["status"], "probe_only")
        self.assertFalse(result["reviewed_golden"])
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(result["verified_n07_assertions"], [])

    def test_registrys_ack_cannot_replace_pointer_delivery(self):
        invalid = diagnostic()
        invalid["n07_probe"]["pointer"]["after"]["delivered_buttons"] = 3
        self.reject(invalid)

    def test_stale_input_epoch_is_rejected(self):
        invalid = diagnostic()
        invalid["n07_probe"]["pointer"]["epoch_after"] = 4
        self.reject(invalid)

    def test_wrong_pointer_source_surface_and_capture_are_rejected(self):
        for path, value in (
            (("pointer", "after", "last_delivered_source"), "2"),
            (("pointer", "after", "last_delivered_surface"), None),
            (("pointer", "after", "last_delivered_press_serial"), 0),
            (("pointer", "after", "pressed"), 1),
            (("capture_id",), "b" * 64),
        ):
            with self.subTest(path=path):
                invalid = diagnostic()
                parent = invalid["n07_probe"]
                for part in path[:-1]:
                    parent = parent[part]
                parent[path[-1]] = value
                self.reject(invalid)

    def test_popup_role_serial_parent_layer_origin_and_target_are_rejected(self):
        for field, value in (
            ("role", "xdg_toplevel"),
            ("root_window", "8"),
            ("captured_origin", False),
            ("dismissed", True),
            ("ack_serial", None),
            ("grab_serial", 0),
            ("grab_serial", 777),
            ("layer", 4),
            ("parent_surface", "11"),
        ):
            with self.subTest(field=field):
                invalid = diagnostic()
                invalid["n07_probe"]["popup"][field] = value
                self.reject(invalid)

    def test_renderer_readback_absence_stale_frame_and_mutated_bytes_rejected(self):
        cases = [
            lambda n: n["n07_probe"]["menu_open"].update(readback=None),
            lambda n: n["n07_probe"]["menu_open"]["readback"]["receipt"].update(
                content_frame="20"),
            lambda n: n["n07_probe"]["menu_open"]["readback"]["receipt"].update(
                context_epoch=8),
            lambda n: n["n07_probe"]["menu_open"]["readback"]["receipt"].update(
                runtime="999"),
            lambda n: n["n07_probe"]["menu_open"]["readback"]["receipt"].update(
                target={**TARGET, "generation": "2"}),
            lambda n: n["n07_probe"]["menu_open"]["readback"]["pixels"][0].update(
                rgba_b64="AAAA"),
            lambda n: n["n07_probe"]["menu_open"]["readback"]["pixels"][0].update(
                sha256="0" * 64),
            lambda n: n["n07_probe"]["menu_open"]["readback"]["receipt"].update(
                layers=[{"surface": "1"}, {"surface": "100"}]),
        ]
        for index, mutate in enumerate(cases):
            with self.subTest(case=index):
                invalid = diagnostic()
                mutate(invalid)
                self.reject(invalid)

    def test_unreviewed_pixel_is_never_self_approved_and_cleanup_is_mandatory(self):
        for path, value in (
            (("n07_probe", "menu_coordinate_reviewed"), True),
            (("n07_probe", "menu_identity_verified"), True),
            (("host_v3_acceptance_passed",), True),
            (("n07_assertions_verified",), ["pointer_toggled_setting"]),
            (("sdk_retired",), False),
            (("destroy_status",), None),
            (("cleanup_error",), "INCOMPLETE"),
        ):
            with self.subTest(path=path):
                invalid = diagnostic()
                parent = invalid
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                self.reject(invalid)

    def test_renderer_retirement_registry_census_and_finalizer_are_required(self):
        cases = [
            lambda n: n["registry_counts"].update(runtimes=1),
            lambda n: n["last_snapshot"]["runtime"]["reclamation"].update(
                origin_retired=False),
            lambda n: n["last_snapshot"]["runtime"]["reclamation"].update(
                sdk_bindings=1),
            lambda n: n["last_snapshot"]["runtime"]["render"].update(
                targets=[{"key": TARGET}]),
            lambda n: n["last_snapshot"]["runtime"]["render"]["composition"].update(
                texture_bytes=4096),
            lambda n: n["last_snapshot"]["runtime"]["render"]["composition"].update(
                gl_error=1282),
            lambda n: n["trace"]["events"].pop(),
            lambda n: n["trace"]["events"].reverse(),
            lambda n: n["trace"]["events"][-1]["data"].update(status=1),
            lambda n: n["trace"].update(overflow=True),
        ]
        for index, mutate in enumerate(cases):
            with self.subTest(case=index):
                invalid = diagnostic()
                mutate(invalid)
                self.reject(invalid)

    def reject(self, value: dict) -> None:
        with self.assertRaises(ProbeInvalid):
            inspect(value)


if __name__ == "__main__":
    unittest.main()
