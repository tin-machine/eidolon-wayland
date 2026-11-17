"""N07-B2 independent four-state *diagnostic* verifier.

Reads the saved normal-Registry/SDK/renderer receipts, but never approves
a Mousepad menu label, a pixel golden or a frozen N07 acceptance assertion.
A second nonce is explicitly OUT OF SCOPE. Do not infer GUI correctness from
producer booleans or from identical hashes of a screenshot and its 'golden'.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import re

from linux_real_app_v3_n07_b1_probe import (
    ProbeInvalid, inspect as inspect_b1, need, positive, positive_number,
)

SCHEMA = "eidolon-i6-n07-b2-native-probe-v1"
VARIANT = "line_numbers_four_rois_probe"
GUTTER = (1, 0, 64, 48, 128)
CHECK = (2, 112, 96, 112, 128)
MAX_RGBA = 128 * 1024


def _forwarded(proof: dict, target: dict, epoch: int, uv: list[int]) -> None:
    need(type(proof) is dict and proof.get("target") == target
         and proof.get("source") == "1"
         and proof.get("uv_milli") == uv and "uv" not in proof
         and proof.get("coordinate_reviewed") is False
         and proof.get("input_epoch") == epoch
         and proof.get("epoch_after") == epoch
         and proof.get("provenance") ==
         "normal_registry_command_and_compositor_forward",
         "N07_B2_POINTER_IDENTITY")
    before, after = proof.get("before"), proof.get("after")
    need(type(before) is dict and type(after) is dict,
         "N07_B2_POINTER_MISSING")
    a, b = before.get("delivered_buttons"), after.get("delivered_buttons")
    need(type(a) is int and type(b) is int and b >= a + 2
         and after.get("last_delivered_source") == "1"
         and positive(after.get("last_delivered_surface"))
         and positive_number(after.get("last_delivered_press_serial"))
         and positive_number(after.get("last_delivered_serial"))
         and after["last_delivered_press_serial"] !=
         before.get("last_delivered_press_serial")
         and after.get("hover_window") == target["window"]
         and after.get("pressed") == 0,
         "N07_B2_REGISTRY_ACK_ONLY")


def _state(stage: dict, expected_phase: str, target: dict, runtime: str,
           create: str, expected: tuple, sdk: str) -> tuple[dict, bytes]:
    need(type(stage) is dict and stage.get("phase") == expected_phase,
         "N07_B2_STATE_MISSING")
    readback = stage.get("readback")
    need(type(readback) is dict
         and readback.get("source") == "host_renderer_readback"
         and readback.get("target") == target
         and readback.get("create_seq") == create
         and readback.get("sdk_adapter") == sdk,
         "N07_B2_SDK_ORIGIN")
    receipt = readback.get("receipt")
    need(type(receipt) is dict
         and receipt.get("target") == target
         and receipt.get("runtime") == runtime
         and receipt.get("create") == create
         and positive_number(receipt.get("renderer_owner"))
         and positive_number(receipt.get("context_epoch"))
         and positive_number(receipt.get("present_seq"))
         and positive(receipt.get("content_frame"))
         and positive(receipt.get("min_content_frame"))
         and int(receipt["content_frame"]) >= int(receipt["min_content_frame"])
         and receipt.get("roi_revision") == 1,
         "N07_B2_RENDERER_ORIGIN")
    backend = receipt.get("backend")
    need(type(backend) is dict
         and type(backend.get("vendor")) is str and bool(backend["vendor"])
         and type(backend.get("renderer")) is str and bool(backend["renderer"]),
         "N07_B2_BACKEND")
    layers = receipt.get("layers")
    need(type(layers) is list and 0 < len(layers) <= 64,
         "N07_B2_LAYERS")
    pixels, hashes = readback.get("pixels"), receipt.get("pixel_sha256")
    need(type(pixels) is list and len(pixels) == 1
         and type(hashes) is list and len(hashes) == 1,
         "N07_B2_ROI_COUNT")
    pixel = pixels[0]
    uid, x, y, w, h = expected
    need(type(pixel) is dict and pixel.get("id") == uid
         and pixel.get("rect") == {
             "x": x, "y": y, "width": w, "height": h,
         } and type(pixel.get("sha256")) is str
         and re.fullmatch(r"[0-9a-f]{64}", pixel["sha256"]) is not None
         and pixel["sha256"] == hashes[0],
         "N07_B2_ROI_GEOMETRY")
    encoded = pixel.get("rgba_b64")
    need(type(encoded) is str and len(encoded) <= (w*h*4+2)//3*4+4
         and w*h*4 <= MAX_RGBA,
         "N07_B2_PIXEL_BOUNDS")
    try:
        rgba = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ProbeInvalid("N07_B2_PIXEL_ENCODING") from error
    need(len(rgba) == w*h*4
         and base64.b64encode(rgba).decode("ascii") == encoded
         and hashlib.sha256(rgba).hexdigest() == pixel["sha256"],
         "N07_B2_PIXEL_MISMATCH")
    return receipt, rgba


def _contrast(off: bytes, on: bytes) -> int:
    need(len(off) == len(on) and len(off) % 4 == 0, "N07_B2_PIXEL_LENGTH")
    return sum(any(a != b for a, b in zip(off[i:i+3], on[i:i+3]))
               for i in range(0, len(off), 4))


def _popup(popup: dict, target: dict, pointer: dict, layers: list) -> None:
    need(type(popup) is dict and popup.get("role") == "xdg_popup"
         and popup.get("root_window") == target["window"]
         and popup.get("captured_origin") is True
         and popup.get("dismissed") is False
         and positive(popup.get("surface"))
         and positive(popup.get("parent_surface"))
         and popup["surface"] != popup["parent_surface"]
         and positive_number(popup.get("ack_serial")),
         "N07_B2_POPUP_ORIGIN")
    grab = popup.get("grab_serial")
    need(grab is None or (positive_number(grab) and grab in {
        pointer["after"]["last_delivered_press_serial"],
        pointer["after"]["last_delivered_serial"],
    }), "N07_B2_POPUP_SERIAL")
    layer = popup.get("layer")
    need(type(layer) is int and 0 <= layer < len(layers)
         and layers[layer].get("surface") == popup["surface"]
         and any(type(item) is dict and item.get("surface") ==
                 popup["parent_surface"] for item in layers),
         "N07_B2_POPUP_LAYER")


def inspect(native: dict) -> dict:
    """A successful result is probe_only, never formal N07 acceptance."""
    need(type(native) is dict and native.get("schema") == SCHEMA
         and native.get("variant") == VARIANT
         and native.get("n07_assertions_verified") == []
         and native.get("host_v3_acceptance_passed") is False,
         "N07_B2_RECEIPT_INVALID")
    probe = native.get("n07_probe")
    need(type(probe) is dict and probe.get("variant") == VARIANT,
         "N07_B2_PROBE_INVALID")
    b2 = probe.get("b2")
    need(type(b2) is dict and b2.get("schema") == 1
         and b2.get("source") == "normal_registry_pointer_and_sdk_renderer"
         and b2.get("coordinate_reviewed") is False
         and b2.get("menu_identity_verified") is False
         and b2.get("candidate_item_uv_milli") == [195, 198]
         and not any(k in b2 for k in (
             "second_on_numbers_1_8", "second_on_checkmark",
             "reviewed_golden", "verified_n07_assertions")),
         "N07_B2_UNREVIEWED_CANDIDATE")
    # Reuse the strictly independent B1 receipt/protocol/cleanup/finalizer
    # verifier, not the B1 producer's own 'status'. Remove only B2 envelope.
    base = copy.deepcopy(native)
    base["schema"] = "eidolon-i6-n07-b1-native-probe-v1"
    base["variant"] = "pointer_popup_readback_probe"
    base["n07_probe"]["variant"] = "pointer_popup_readback_probe"
    base["n07_probe"].pop("b2")
    need(inspect_b1(base)["status"] == "probe_only", "N07_B2_BASE_INVALID")
    target, runtime = probe["main_target"], native["runtime"]
    create = native["readback"]["create"]
    sdk = probe["before_menu"]["readback"]["sdk_adapter"]
    need(b2.get("initial_popup") == probe["popup"]
         and type(b2.get("first_off_gutter")) is dict
         and b2["first_off_gutter"].get("source_stage") == "before_menu"
         and b2["first_off_gutter"].get("readback") ==
         probe["before_menu"]["readback"], "N07_B2_OFF_PROVENANCE")
    off_readback = probe["before_menu"]["readback"]
    # The B1 independent validator checked both ROIs and digest above.
    off_gutter = base64.b64decode(off_readback["pixels"][0]["rgba_b64"], validate=True)
    off_receipt = off_readback["receipt"]
    off_check, off_pixels = _state(
        b2.get("first_off_checkmark"), "first_off_checkmark",
        target, runtime, create, CHECK, sdk)
    on_gutter, on_pixels = _state(
        b2.get("first_on_gutter"), "first_on_gutter",
        target, runtime, create, GUTTER, sdk)
    on_check, checked_pixels = _state(
        b2.get("first_on_checkmark"), "first_on_checkmark",
        target, runtime, create, CHECK, sdk)
    # A single SDK Create, renderer allocation and EGL context must survive
    # all states. Reuse/replacement across observations invalidates the probe.
    receipts = (off_receipt, off_check, on_gutter, on_check)
    need(all(r["renderer_owner"] == off_receipt["renderer_owner"]
             and r["context_epoch"] == off_receipt["context_epoch"]
             and r["backend"] == off_receipt["backend"] for r in receipts),
         "N07_B2_RENDERER_CHANGED")
    frames = [int(r["content_frame"]) for r in receipts]
    need(frames[0] < frames[1] < frames[2] < frames[3],
         "N07_B2_STALE_FRAME")
    _popup(probe["popup"], target, probe["pointer"], off_check["layers"])
    epoch = probe["pointer"]["input_epoch"]
    item = b2.get("item_pointer")
    _forwarded(item, target, epoch, [195, 198])
    need(item["after"]["last_delivered_surface"] == probe["popup"]["surface"]
         and item["before"]["delivered_buttons"] >=
         probe["pointer"]["after"]["delivered_buttons"],
         "N07_B2_ITEM_WRONG_SURFACE")
    reopen = b2.get("reopen_pointer")
    _forwarded(reopen, target, epoch, [160, 63])
    need(reopen["before"]["delivered_buttons"] >=
         item["after"]["delivered_buttons"], "N07_B2_POINTER_ORDER")
    fresh = b2.get("reopened_popup")
    _popup(fresh, target, reopen, on_check["layers"])
    need(fresh["parent_surface"] == probe["popup"]["parent_surface"],
         "N07_B2_POPUP_PARENT_CHANGED")
    need(fresh["surface"] != probe["popup"]["surface"]
         or fresh["ack_serial"] != probe["popup"]["ack_serial"],
         "N07_B2_OLD_POPUP_REUSED")
    # No unreviewed observed pixel is ever used as its own expected golden.
    # Pixel contrasts are diagnostic-only; B3 review fixes masks and thresholds.
    delta_gutter = _contrast(off_gutter, on_pixels)
    delta_check = _contrast(off_pixels, checked_pixels)
    need(delta_gutter >= 8 and delta_check >= 8,
         "N07_B2_NO_VISUAL_TRANSITION")
    return {
        "status": "probe_only", "real_pointer_dispatches": 3,
        "renderer_states": 4,
        "changed_gutter_pixels": delta_gutter,
        "changed_checkmark_pixels": delta_check,
        "menu_identity_verified": False, "reviewed_golden": False,
        "verified_n07_assertions": [],
        "host_v3_acceptance_passed": False,
    }
