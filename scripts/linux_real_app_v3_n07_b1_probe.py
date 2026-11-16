"""N07-B1 diagnostic auditor: reject forged pointer, popup and renderer provenance.

This checks internal consistency of an actual saved Host discovery receipt; it
does NOT authenticate producer data, certify a named menu item, compare a
reviewed pixel golden, or set any of the frozen 13 N07 assertions to success.
B2 must consume independent two-session keyfile/pixel/cleanup oracles.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re

VARIANT = "pointer_popup_readback_probe"
SCHEMA = "eidolon-i6-n07-b1-native-probe-v1"
MAX_RGBA_BYTES = 128 * 1024
STAGES = ("before_menu", "menu_open")


class ProbeInvalid(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise ProbeInvalid(code)


def positive(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[1-9][0-9]*", value) is not None


def positive_number(value: object) -> bool:
    return type(value) is int and value > 0


def _pointer(probe: dict, target: dict) -> None:
    pointer = probe.get("pointer")
    need(type(pointer) is dict, "N07_B1_POINTER_MISSING")
    need(pointer.get("target") == target and pointer.get("source") == "1",
         "N07_B1_INPUT_IDENTITY")
    # Input epoch starts at zero and must remain identical across the click.
    epoch = pointer.get("input_epoch")
    need(type(epoch) is int and epoch >= 0
         and pointer.get("epoch_after") == epoch,
         "N07_B1_STALE_INPUT_EPOCH")
    before, after = pointer.get("before"), pointer.get("after")
    need(type(before) is dict and type(after) is dict,
         "N07_B1_POINTER_MISSING")
    a, b = before.get("delivered_buttons"), after.get("delivered_buttons")
    need(type(a) is int and type(b) is int and b >= a + 2,
         "N07_B1_REGISTRY_ACK_ONLY")
    need(after.get("last_delivered_source") == "1"
         and positive(after.get("last_delivered_surface"))
         and positive_number(after.get("last_delivered_serial"))
         and after.get("hover_window") == target["window"]
         and after.get("pressed") == 0,
         "N07_B1_POINTER_NOT_FORWARDED")
    need(pointer.get("provenance") == "normal_registry_command_and_compositor_forward"
         and pointer.get("coordinate_reviewed") is False
         and type(pointer.get("uv")) is list
         and len(pointer["uv"]) == 2
         and all(type(n) in (int, float) and 0 < n < 1 for n in pointer["uv"]),
         "N07_B1_POINTER_UNREVIEWED")


def _readback(stage: dict, target: dict, runtime: str, create: str) -> dict:
    need(type(stage) is dict and stage.get("phase") in STAGES,
         "N07_B1_STAGE_INVALID")
    capture = stage.get("readback")
    need(type(capture) is dict and capture.get("source") == "host_renderer_readback"
         and capture.get("target") == target
         and capture.get("create_seq") == create
         and positive(capture.get("sdk_adapter")),
         "N07_B1_READBACK_ORIGIN")
    receipt = capture.get("receipt")
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
         "N07_B1_READBACK_ORIGIN")
    backend = receipt.get("backend")
    need(type(backend) is dict
         and type(backend.get("vendor")) is str and bool(backend["vendor"])
         and type(backend.get("renderer")) is str and bool(backend["renderer"]),
         "N07_B1_BACKEND_INVALID")
    layers = receipt.get("layers")
    need(type(layers) is list and 0 < len(layers) <= 64,
         "N07_B1_LAYERS_INVALID")
    pixels = capture.get("pixels")
    hashes = receipt.get("pixel_sha256")
    need(type(pixels) is list and len(pixels) == 2
         and type(hashes) is list and len(hashes) == len(pixels),
         "N07_B1_READBACK_MISSING")
    expected = ((1, 0, 64, 48, 128), (2, 64, 16, 192, 128))
    for pixel, digest, fields in zip(pixels, hashes, expected):
        uid, x, y, width, height = fields
        need(type(pixel) is dict and pixel.get("id") == uid
             and pixel.get("rect") == {
                 "x": x, "y": y, "width": width, "height": height,
             } and type(digest) is str
             and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
             and pixel.get("sha256") == digest,
             "N07_B1_ROI_MISMATCH")
        raw = pixel.get("rgba_b64")
        need(type(raw) is str and len(raw) < MAX_RGBA_BYTES * 2,
             "N07_B1_PIXEL_BOUNDS")
        try:
            rgba = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ProbeInvalid("N07_B1_PIXEL_ENCODING") from error
        need(len(rgba) == width * height * 4
             and base64.b64encode(rgba).decode("ascii") == raw
             and hashlib.sha256(rgba).hexdigest() == digest,
             "N07_B1_PIXEL_MISMATCH")
    return receipt


def inspect(native: dict) -> dict:
    """Diagnostic-only result; NEVER returns status=pass or N07 assertion list."""
    need(type(native) is dict and native.get("schema") == SCHEMA
         and native.get("variant") == VARIANT
         and native.get("host_v3_acceptance_passed") is False
         and native.get("n07_assertions_verified") == []
         and native.get("error") is None
         and native.get("cleanup_error") is None,
         "N07_B1_RECEIPT_INVALID")
    probe = native.get("n07_probe")
    need(type(probe) is dict and probe.get("schema") == 1
         and probe.get("variant") == VARIANT
         and probe.get("run_id") == native.get("run_id")
         and type(probe.get("capture_id")) is str
         and re.fullmatch(r"[0-9a-f]{64}", probe["capture_id"]) is not None,
         "N07_B1_CAPTURE_INVALID")
    target = probe.get("main_target")
    need(type(target) is dict and set(target) == {
         "session", "window", "scene", "generation",
         } and all(positive(v) for v in target.values()),
         "N07_B1_TARGET_INVALID")
    trace = native.get("trace")
    need(type(trace) is dict and type(trace.get("events")) is list
         and any(type(event) is dict
                 and type(event.get("capture")) is dict
                 and event["capture"].get("capture_id") == probe["capture_id"]
                 for event in trace["events"]),
         "N07_B1_CROSS_CAPTURE")
    runtime = native.get("runtime")
    create = native.get("readback", {}).get("create")
    need(positive(runtime) and positive(create)
         and native["readback"].get("target") == target,
         "N07_B1_SDK_TARGET_INVALID")
    _pointer(probe, target)
    receipts = {}
    for stage in STAGES:
        item = probe.get(stage)
        need(type(item) is dict and item.get("phase") == stage,
             "N07_B1_STAGE_MISSING")
        receipts[stage] = _readback(item, target, runtime, create)
    before = receipts["before_menu"]
    menu = receipts["menu_open"]
    need(int(menu["content_frame"]) > int(before["content_frame"])
         and menu["renderer_owner"] == before["renderer_owner"]
         and menu["context_epoch"] == before["context_epoch"],
         "N07_B1_STALE_FRAME")
    popup = probe.get("popup")
    need(type(popup) is dict
         and popup.get("role") == "xdg_popup"
         and popup.get("root_window") == target["window"]
         and popup.get("captured_origin") is True
         and popup.get("dismissed") is False
         and positive(popup.get("surface"))
         and positive(popup.get("parent_surface"))
         and popup["surface"] != popup["parent_surface"]
         and positive_number(popup.get("ack_serial")),
         "N07_B1_POPUP_ORIGIN")
    grab = popup.get("grab_serial")
    need(grab is None or positive_number(grab), "N07_B1_POPUP_SERIAL")
    layer = popup.get("layer")
    need(type(layer) is int and 0 <= layer < len(menu["layers"])
         and menu["layers"][layer].get("surface") == popup["surface"],
         "N07_B1_POPUP_LAYER")
    need(probe.get("menu_coordinate_reviewed") is False
         and probe.get("menu_identity_verified") is False,
         "N07_B1_UNREVIEWED_MENU")
    need(native.get("sdk_retired") is True
         and native.get("destroy_status") == 0
         and native.get("registry_counts") == {"runtimes": 0, "workers": 0},
         "N07_B1_CLEANUP_UNVERIFIED")
    final = native.get("last_snapshot", {}).get("runtime", {})
    reclamation = final.get("reclamation", {})
    render = final.get("render", {})
    composition = render.get("composition", {})
    need(final.get("state") == "Stopped"
         and reclamation.get("origin_retired") is True
         and reclamation.get("shutdown_complete") is True
         and reclamation.get("sdk_bindings") == 0
         and reclamation.get("native_targets") == 0
         and reclamation.get("panels") == 0
         and reclamation.get("pending_operations") == 0
         and render.get("targets") == []
         and render.get("queued") == 0
         and composition.get("textures") == 0
         and composition.get("texture_bytes") == 0
         and composition.get("decoration_texture_bytes") == 0
         and composition.get("gl_error") == 0
         and render.get("error") is None,
         "N07_B1_RENDERER_NOT_RETIRED")
    kinds = ("remote_cleanup_observed", "io_retired",
             "journal_unlinked", "finalizer_published")
    trace_events = trace["events"]
    positions = [index for kind in kinds
                 for index, event in enumerate(trace_events)
                 if event.get("kind") == kind]
    need(len(positions) == len(kinds)
         and positions == sorted(positions)
         and [trace_events[index]["kind"] for index in positions] == list(kinds)
         and trace_events[positions[-1]].get("data", {}).get("status") == 0
         and trace.get("overflow") is not True,
         "N07_B1_FINALIZER_MISSING")
    return {
        "status": "probe_only",
        "role": "xdg_popup",
        "renderer_rois": 4,
        "reviewed_golden": False,
        "menu_identity_verified": False,
        "verified_n07_assertions": [],
        "host_v3_acceptance_passed": False,
    }
