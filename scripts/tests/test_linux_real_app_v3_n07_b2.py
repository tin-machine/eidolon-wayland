"""N07-B2 adversarial four-stage diagnostic tests (never typed N07 receipt)."""
from __future__ import annotations

import base64
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linux_real_app_v3_n07_b2_probe import inspect, ProbeInvalid
from test_linux_real_app_v3_n07_b1 import diagnostic


def readback(template: dict, phase: str, frame: int,
             uid: int, rect: tuple[int, int, int, int], change: int = 0,
             popup_surface: str = "11") -> dict:
    source = template["n07_probe"]["before_menu"]["readback"]
    x, y, width, height = rect
    rgba = bytearray([uid, x & 255, y & 255, 255]) * (width * height)
    for offset in range(0, change * 4, 4):
        rgba[offset] ^= 127
    result = copy.deepcopy(source)
    digest = hashlib.sha256(rgba).hexdigest()
    result["pixels"] = [{
        "id": uid,
        "rect": {"x": x, "y": y, "width": width, "height": height},
        "rgba_b64": base64.b64encode(rgba).decode("ascii"),
        "sha256": digest,
    }]
    result["receipt"]["pixel_sha256"] = [digest]
    result["receipt"]["content_frame"] = str(frame)
    result["receipt"]["min_content_frame"] = str(frame - 1)
    result["receipt"]["present_seq"] = frame
    result["receipt"]["layers"] = [
        {"surface": "1"}, {"surface": popup_surface},
    ]
    return {"phase": phase, "readback": result}


def pointer(target: dict, before: int, press: int, surface: str,
            uv: list[int]) -> dict:
    return {
        "target": target, "source": "1", "input_epoch": 3,
        "epoch_after": 3, "uv_milli": uv, "coordinate_reviewed": False,
        "provenance": "normal_registry_command_and_compositor_forward",
        "before": {"delivered_buttons": before},
        "after": {
            "delivered_buttons": before + 2,
            "last_delivered_source": "1",
            "last_delivered_surface": surface,
            "last_delivered_serial": press + 1,
            "last_delivered_press_serial": press,
            "hover_window": target["window"], "pressed": 0,
        },
    }


def candidate() -> dict:
    n = diagnostic()
    n["schema"] = "eidolon-i6-n07-b2-native-probe-v1"
    n["variant"] = "line_numbers_four_rois_probe"
    probe = n["n07_probe"]
    probe["variant"] = "line_numbers_four_rois_probe"
    # Match the exact real Host EGL backend report shape, not a shortened
    # vendor/renderer-only mock that hides mutable vs immutable fields.
    egl = {
        "vendor": "Mesa test", "renderer": "software",
        "version": "OpenGL ES 3.2 Mock", "config_id": 21,
        "width": 1024, "height": 768, "rgba_bits": [8, 8, 8, 8],
        "readback": True, "max_swap_us": 5,
    }
    for stage in ("before_menu", "menu_open"):
        probe[stage]["readback"]["receipt"]["backend"] = copy.deepcopy(egl)
    probe["menu_open"]["readback"]["receipt"]["backend"]["max_swap_us"] = 12
    t = probe["main_target"]
    probe["b2"] = {
        "schema": 1,
        "source": "normal_registry_pointer_and_sdk_renderer",
        "coordinate_reviewed": False, "menu_identity_verified": False,
        "initial_popup": copy.deepcopy(probe["popup"]),
        "first_off_gutter": {"source_stage": "before_menu", "roi_id": 1},
        "candidate_item_uv_milli": [195, 164],
        "first_off_checkmark": readback(
            n, "first_off_checkmark", 22, 2, (112, 96, 112, 128)),
        "item_pointer": pointer(t, 5, 41, "11", [195, 164]),
        "first_on_gutter": readback(
            n, "first_on_gutter", 23, 1, (0, 64, 48, 128), change=32),
        "reopen_pointer": pointer(t, 7, 43, "1", [160, 63]),
        "reopened_popup": {
            "role": "xdg_popup", "root_window": "7",
            "captured_origin": True, "dismissed": False,
            "surface": "12", "parent_surface": "1",
            "ack_serial": 67, "grab_serial": 43, "layer": 1,
        },
        "first_on_checkmark": readback(
            n, "first_on_checkmark", 24, 2,
            (112, 96, 112, 128), change=32, popup_surface="12"),
    }
    # The same renderer works harder as the menu and text are rendered. A
    # monotonic maximum is valid evidence, never an identity mismatch.
    for stage, maximum in (
        ("first_off_checkmark", 12),
        ("first_on_gutter", 13),
        ("first_on_checkmark", 13),
    ):
        probe["b2"][stage]["readback"]["receipt"]["backend"]["max_swap_us"] = maximum
    return n


class B2ProofTests(unittest.TestCase):
    def reject(self, change):
        native = candidate()
        change(native)
        with self.assertRaises(ProbeInvalid):
            inspect(native)

    def test_saved_candidate_is_only_diagnostic(self):
        result = inspect(candidate())
        self.assertEqual(result["status"], "probe_only")
        self.assertFalse(result["reviewed_golden"])
        self.assertEqual(result["renderer_states"], 4)
        self.assertEqual(result["verified_n07_assertions"], [])
        self.assertFalse(result["host_v3_acceptance_passed"])

    def test_live_evidence_swap_max_changes_dont_replace_renderer(self):
        native = candidate()
        backend = native["n07_probe"]["before_menu"]["readback"]["receipt"]["backend"]
        self.assertEqual(backend["max_swap_us"], 5)
        values = [native["n07_probe"]["b2"][s]["readback"]["receipt"]["backend"]
                  ["max_swap_us"] for s in (
                      "first_off_checkmark", "first_on_gutter",
                      "first_on_checkmark")]
        self.assertEqual(values, [12, 13, 13])
        self.assertEqual(inspect(native)["status"], "probe_only")

    def test_every_frozen_backend_identity_field_rejects_mutation(self):
        mutations = {
            "vendor": "foreign", "renderer": "unrelated",
            "version": "OpenGL ES 0", "config_id": 22,
            "width": 1000, "height": 700,
            "rgba_bits": [8, 8, 8, 16], "readback": False,
        }
        for field, invalid in mutations.items():
            with self.subTest(field=field):
                self.reject(lambda n, k=field, v=invalid:
                            n["n07_probe"]["b2"]["first_on_gutter"]
                             ["readback"]["receipt"]["backend"].update({k: v}))
        self.reject(lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
                    ["readback"]["receipt"]["backend"].pop("version"))
        self.reject(lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
                    ["readback"]["receipt"]["backend"].update(fake_owner="1"))
        self.reject(lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
                    ["readback"]["receipt"]["backend"].update(width=True))

    def test_dynamic_backend_measurement_is_bounded_and_monotonic(self):
        for bad in (None, -1, "13", 2.0, True):
            with self.subTest(value=bad):
                self.reject(lambda n, value=bad:
                            n["n07_probe"]["b2"]["first_on_checkmark"]
                             ["readback"]["receipt"]["backend"].update(
                                 max_swap_us=value))
        self.reject(lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
                    ["readback"]["receipt"]["backend"].update(max_swap_us=0))

    def test_pointer_delivery_epoch_and_popup_surface_must_join(self):
        changes = [
            lambda n: n["n07_probe"]["b2"]["item_pointer"]["after"].update(
                delivered_buttons=5),
            lambda n: n["n07_probe"]["b2"]["item_pointer"]["after"].update(
                last_delivered_surface="1"),
            lambda n: n["n07_probe"]["b2"]["item_pointer"].update(
                epoch_after=4),
            lambda n: n["n07_probe"]["b2"]["reopen_pointer"]["after"].update(
                last_delivered_press_serial=0),
            lambda n: n["n07_probe"]["b2"]["reopen_pointer"]["before"].update(
                delivered_buttons=0),
            lambda n: n["n07_probe"]["b2"]["item_pointer"].update(
                uv_milli=[200, 198]),
            lambda n: n["n07_probe"]["b2"]["reopened_popup"].update(
                ack_serial=None),
            lambda n: n["n07_probe"]["b2"]["reopened_popup"].update(
                grab_serial=999),
            lambda n: n["n07_probe"]["b2"]["reopened_popup"].update(
                parent_surface="12"),
            lambda n: n["n07_probe"]["b2"]["reopened_popup"].update(
                layer=8),
            lambda n: n["n07_probe"]["b2"]["reopened_popup"].update(
                surface="11", ack_serial=66),
        ]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                self.reject(change)

    def test_renderer_provenance_and_four_pixel_states(self):
        changes = [
            lambda n: n["n07_probe"]["b2"].update(first_off_checkmark=None),
            lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
            ["readback"]["receipt"].update(content_frame="22"),
            lambda n: n["n07_probe"]["b2"]["first_on_checkmark"]
            ["readback"]["receipt"].update(renderer_owner=99),
            lambda n: n["n07_probe"]["b2"]["first_on_checkmark"]
            ["readback"]["receipt"].update(runtime="99"),
            lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
            ["readback"]["receipt"]["target"].update(generation="99"),
            lambda n: n["n07_probe"]["b2"]["first_on_checkmark"]
            ["readback"]["pixels"][0]["rect"].update(y=90),
            lambda n: n["n07_probe"]["b2"]["first_on_checkmark"]
            ["readback"]["pixels"][0].update(rgba_b64="AAAA"),
            lambda n: n["n07_probe"]["b2"]["first_on_checkmark"]
            ["readback"]["receipt"].update(
                layers=[{"surface": "1"}, {"surface": "99"}]),
            lambda n: n["n07_probe"]["b2"]["first_off_gutter"]
            .update(roi_id=2),
            lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
            ["readback"]["pixels"][0].update(
                sha256=n["n07_probe"]["before_menu"]
                ["readback"]["pixels"][0]["sha256"]),
            lambda n: n["n07_probe"]["b2"]["first_on_gutter"]
            ["readback"]["pixels"][0].update(
                rgba_b64=n["n07_probe"]["before_menu"]
                ["readback"]["pixels"][0]["rgba_b64"]),
        ]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                self.reject(change)

    def test_never_promote_or_fabricate_second_session(self):
        changes = [
            lambda n: n.update(host_v3_acceptance_passed=True),
            lambda n: n.update(n07_assertions_verified=["toggle"]),
            lambda n: n["n07_probe"]["b2"].update(
                menu_identity_verified=True),
            lambda n: n["n07_probe"]["b2"].update(
                second_on_checkmark={"checked": True}),
            lambda n: n.update(sdk_retired=False),
            lambda n: n["trace"]["events"].reverse(),
            lambda n: n["n07_probe"].update(capture_id="f" * 64),
        ]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                self.reject(change)


if __name__ == "__main__":
    unittest.main()
