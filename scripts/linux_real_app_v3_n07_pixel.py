"""N07-A independently locked pixel oracle for future real Host renderer ROIs.

No reference golden is shipped yet. Until a separately reviewed golden is
committed AND its exact SHA-256 is pinned below, load_reviewed() fails closed.
Unit-test reference images are deliberately not production goldens.

This compares pixel bytes, NOT proof that those bytes came from a current
Registry/SDK target. The N07 live acceptance oracle must bind every ROI to
a real native readback ticket and verify both product sessions independently.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "scripts/fixtures/linux-desktop-v3-acceptance/n07-pixel-golden-v1.json"
# Assigned only in a separately reviewed PR after the independent fixture exists.
# A producer-supplied reviewed=true or the image's own digest has no authority.
APPROVED_SHA256 = None
MOUSEPAD_COMMIT = "65fbfe0459259f820fcc8b9c4dd454228304842a"
STATES = ("first_off_gutter", "first_off_checkmark",
          "first_on_gutter", "first_on_checkmark",
          "second_on_numbers_1_8", "second_on_checkmark")
NEGATIVE_PAIRS = (("first_off_gutter", "first_on_gutter"),
                  ("first_off_checkmark", "first_on_checkmark"))
FINGERPRINT = frozenset((
    "mousepad_commit", "waypipe_commit", "gtk_version", "pango_version",
    "cairo_version", "font_sha256", "fontconfig_sha256", "mesa_vendor",
    "mesa_renderer", "renderer_revision", "theme", "locale", "scale",
))
MAX_FILE = 1024 * 1024
MAX_ROI_PIXELS = 32768


class PixelEvidenceError(ValueError):
    pass


def need(ok: bool, reason: str = "N07_PIXEL_INVALID") -> None:
    if not ok:
        raise PixelEvidenceError(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_b64(value: str, length: int) -> bytes:
    need(type(value) is str and len(value) <= (length + 2) // 3 * 4 + 4,
         "N07_PIXEL_ENCODING")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise PixelEvidenceError("N07_PIXEL_ENCODING") from error
    need(len(data) == length and base64.b64encode(data).decode("ascii") == value,
         "N07_PIXEL_ENCODING")
    return data


def _json(raw: bytes) -> dict:
    need(type(raw) is bytes and 0 < len(raw) <= MAX_FILE, "N07_GOLDEN_BOUNDS")

    def unique(items):
        result = {}
        for key, value in items:
            need(key not in result, "N07_JSON_DUPLICATE")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _v: need(False, "N07_JSON_NONFINITE"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PixelEvidenceError("N07_JSON_INVALID") from error
    need(type(value) is dict, "N07_GOLDEN_INVALID")
    return value


def _fingerprint(value: dict) -> None:
    need(type(value) is dict and set(value) == FINGERPRINT
         and all(type(v) is str and 0 < len(v) <= 160 for v in value.values())
         and value["mousepad_commit"] == MOUSEPAD_COMMIT
         and re.fullmatch(r"[0-9a-f]{40}", value["waypipe_commit"]) is not None
         and re.fullmatch(r"[0-9a-f]{64}", value["font_sha256"]) is not None
         and re.fullmatch(r"[0-9a-f]{64}", value["fontconfig_sha256"]) is not None
         and value["scale"] in ("1", "2"), "N07_FINGERPRINT_INVALID")


def _rect(value: dict) -> tuple:
    need(type(value) is dict and set(value) == {"x", "y", "width", "height"}
         and all(type(v) is int and v >= 0 for v in value.values())
         and value["width"] > 0 and value["height"] > 0
         and value["width"] * value["height"] <= MAX_ROI_PIXELS
         and value["x"] + value["width"] <= 16384
         and value["y"] + value["height"] <= 16384,
         "N07_ROI_INVALID")
    return tuple(value[k] for k in ("x", "y", "width", "height"))


def _expected_state(value: dict) -> tuple:
    need(type(value) is dict and set(value) == {
         "rect", "rgba_b64", "mask_b64", "max_channel_delta"}, "N07_GOLDEN_INVALID")
    _, _, width, height = _rect(value["rect"])
    pixels = width * height
    rgba = _decode_b64(value["rgba_b64"], 4 * pixels)
    mask = _decode_b64(value["mask_b64"], pixels)
    need(all(v in (0, 1) for v in mask) and sum(mask) >= min(8, pixels),
         "N07_MASK_INVALID")
    delta = value["max_channel_delta"]
    need(type(delta) is int and 0 <= delta <= 32, "N07_TOLERANCE_INVALID")
    return rgba, mask, delta


def _decode_fixture(raw: bytes, approved_sha256: str) -> dict:
    """Contract-test seam; production must instead use load_reviewed()."""
    need(type(approved_sha256) is str
         and re.fullmatch(r"[0-9a-f]{64}", approved_sha256) is not None
         and digest(raw) == approved_sha256, "N07_GOLDEN_UNREVIEWED")
    reference = _json(raw)
    need(set(reference) == {"schema", "revision", "origin", "fingerprint",
                           "states", "must_differ"}
         and type(reference["schema"]) is int and reference["schema"] == 1
         and reference["revision"] == "eidolon-i6-n07-pixel-v1",
         "N07_GOLDEN_INVALID")
    origin = reference["origin"]
    need(type(origin) is dict and set(origin) == {
        "source", "review_run_id", "review_commit", "reviewer"}
         and origin["source"] == "independent_reference_capture"
         and type(origin["review_run_id"]) is str and len(origin["review_run_id"]) > 0
         and type(origin["reviewer"]) is str and len(origin["reviewer"]) > 0
         and re.fullmatch(r"[0-9a-f]{40}", origin["review_commit"]) is not None,
         "N07_GOLDEN_UNREVIEWED")
    _fingerprint(reference["fingerprint"])
    states = reference["states"]
    need(type(states) is dict and set(states) == set(STATES), "N07_GOLDEN_INCOMPLETE")
    for value in states.values():
        _expected_state(value)
    contrasts = reference["must_differ"]
    need(type(contrasts) is list and len(contrasts) == len(NEGATIVE_PAIRS),
         "N07_NEGATIVE_ROI_INVALID")
    for comparison, pair in zip(contrasts, NEGATIVE_PAIRS):
        need(type(comparison) is dict and set(comparison) == {
            "a", "b", "min_changed_pixels", "min_channel_delta"}
            and (comparison["a"], comparison["b"]) == pair
            and type(comparison["min_changed_pixels"]) is int
            and type(comparison["min_channel_delta"]) is int,
            "N07_NEGATIVE_ROI_INVALID")
        left, right = states[pair[0]], states[pair[1]]
        need(_rect(left["rect"]) == _rect(right["rect"])
             and left["mask_b64"] == right["mask_b64"]
             and 1 <= comparison["min_changed_pixels"] <= sum(
                 _expected_state(left)[1])
             and 1 <= comparison["min_channel_delta"] <= 128,
             "N07_NEGATIVE_ROI_INVALID")
    return reference


def load_reviewed(path: Path = GOLDEN) -> dict:
    """Read only the repository's fixed, independently reviewed fixture.

    Never substitute the observed run's screenshot or manifest in this API.
    """
    need(APPROVED_SHA256 is not None, "N07_GOLDEN_UNREVIEWED")
    need(path == GOLDEN, "N07_GOLDEN_PATH_CHANGED")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError as error:
        raise PixelEvidenceError("N07_GOLDEN_MISSING") from error
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and before.st_size <= MAX_FILE, "N07_GOLDEN_INVALID")
        raw = os.read(fd, MAX_FILE + 1)
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) ==
             (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns),
             "N07_GOLDEN_RACE")
        return _decode_fixture(raw, APPROVED_SHA256)
    finally:
        os.close(fd)


def compare(reference: dict, observed: dict) -> dict:
    """Pixel-only verdict. Does not promote producer pixels to product proof."""
    need(type(observed) is dict and set(observed) == {
        "schema", "run_id", "fingerprint", "sessions", "states"},
        "N07_PIXEL_RECEIPT_INVALID")
    need(observed["schema"] == 1 and type(observed["run_id"]) is str
         and observed["run_id"] != reference["origin"]["review_run_id"],
         "N07_OBSERVED_AS_EXPECTED")
    _fingerprint(observed["fingerprint"])
    need(observed["fingerprint"] == reference["fingerprint"],
         "N07_FINGERPRINT_MISMATCH")
    sessions = observed["sessions"]
    need(type(sessions) is dict and set(sessions) == {"first", "second"},
         "N07_SESSION_MISSING")
    for s in sessions.values():
        need(type(s) is dict and set(s) == {
            "nonce", "capture_id", "runtime", "owner_id", "storage_digest"}
             and all(type(v) is str and v for v in s.values()),
             "N07_SESSION_INVALID")
    first, second = sessions["first"], sessions["second"]
    need(first["storage_digest"] == second["storage_digest"]
         and all(first[k] != second[k] for k in (
             "nonce", "capture_id", "runtime", "owner_id")),
         "N07_SESSION_REUSED")
    states = observed["states"]
    need(type(states) is dict and set(states) == set(STATES),
         "N07_READBACK_MISSING")
    matched = {}
    selected = {}
    for name in STATES:
        item = states[name]
        session = "first" if name.startswith("first_") else "second"
        need(type(item) is dict and set(item) == {
            "source", "session", "capture_id", "runtime", "owner_id",
            "content_frame", "rect", "rgba_b64"}
             and item["source"] == "host_renderer_readback"
             and item["session"] == session
             and all(item[key] == sessions[session][key] for key in (
                 "capture_id", "runtime", "owner_id"))
             and type(item["content_frame"]) is int
             and item["content_frame"] > 0, "N07_READBACK_ORIGIN_INVALID")
        wanted = reference["states"][name]
        need(_rect(item["rect"]) == _rect(wanted["rect"]),
             "N07_READBACK_ROI_MISMATCH")
        expected, mask, tolerance = _expected_state(wanted)
        actual = _decode_b64(item["rgba_b64"], len(expected))
        for i, enabled in enumerate(mask):
            if enabled:
                need(all(abs(actual[4 * i + c] - expected[4 * i + c]) <= tolerance
                         for c in range(4)), "N07_PIXEL_MISMATCH")
        matched[name], selected[name] = actual, mask
    for rule in reference["must_differ"]:
        a, b = rule["a"], rule["b"]
        mask = selected[a]
        count = sum(
            bool(mask[i]) and any(
                abs(matched[a][4 * i + c] - matched[b][4 * i + c])
                >= rule["min_channel_delta"] for c in range(3))
            for i in range(len(mask))
        )
        need(count >= rule["min_changed_pixels"],
             "N07_NEGATIVE_ROI_NOT_DISTINCT")
    return {"kind": "n07-pixel-only-v1", "states_compared": list(STATES),
            "fingerprint_matched": True, "negative_pairs": len(NEGATIVE_PAIRS),
            "product_acceptance": False}
