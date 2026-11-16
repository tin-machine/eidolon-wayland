"""N07-A negative-first suboracle tests; NO real GUI acceptance is claimed."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_evidence as evidence
import linux_real_app_v3_n07_settings as settings
import linux_real_app_v3_n07_pixel as pixel


class FrozenN07(unittest.TestCase):
    def test_manifest_not_changed_and_no_acceptance_claim(self):
        manifest = evidence.load_manifest()
        rows = [row for row in manifest["cases"] if row["case_id"] == "N07"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["variant"], "same_storage_restart")
        self.assertEqual(row["expected_product_outcome"], "complete")
        self.assertEqual(row["evidence_classes"], ["R", "S"])
        self.assertEqual(tuple(row["assertions"]), (
            "same_storage_new_nonce", "first_gui_off", "first_key_absent",
            "pointer_toggled_setting", "first_gui_on", "first_key_true",
            "second_gui_on", "second_key_true", "both_sessions_complete",
            "product_census_complete", "journal_finalized",
            "origin_retirement_before_runtime_stop", "registry_destroy_accepted",
        ))
        self.assertEqual(evidence.MANIFEST_SHA256,
                         "e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33")


class KeyfileSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.uid = os.getuid()
        self.storage = "a" * 64
        self.folder = self.root / "config" / "glib-2.0" / "settings"

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, storage=None):
        return settings.StorageKeyfileProbe(
            self.root, self.uid, storage or self.storage)

    def write(self, text):
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.folder / "keyfile").write_text(text, encoding="utf-8")
        os.chmod(self.folder / "keyfile", 0o600)

    def test_fresh_absence_then_true_then_new_probe_same_storage(self):
        with self.probe() as supervisor:
            before = supervisor.snapshot()
            self.assertFalse(before["key_present"])
            self.assertIsNone(before["file"])
            self.write("[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n")
            enabled = supervisor.snapshot()
        with self.probe() as second_supervisor:
            restarted = second_supervisor.snapshot()
        settings.validate_file_sequence(before, enabled, restarted)
        self.assertTrue(enabled["value"])
        self.assertEqual(enabled["file"], restarted["file"])
        self.assertNotIn("bytes", str(enabled))
        # The file-only proof remains true even if a test directly writes it.
        # Such a mutation MUST NOT be promoted to a GUI/product assertion.
        self.assertFalse(hasattr(settings, "accept_n07"))

    def test_initial_key_true_or_false_is_not_fresh(self):
        for value in ("true", "false"):
            with self.subTest(value=value):
                self.write("[org/xfce/mousepad/preferences/view]\n"
                           "show-line-numbers=" + value + "\n")
                with self.probe() as supervisor:
                    before = supervisor.snapshot()
                with self.assertRaisesRegex(ValueError, "INITIAL_KEY_PRESENT"):
                    settings.validate_file_sequence(before, before, before)

    def test_different_storage_binding_and_root_fail(self):
        with self.probe() as supervisor:
            before = supervisor.snapshot()
            self.write("[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n")
            enabled = supervisor.snapshot()
        with self.probe("b" * 64) as other:
            changed = other.snapshot()
        with self.assertRaisesRegex(ValueError, "DIFFERENT_STORAGE"):
            settings.validate_file_sequence(before, enabled, changed)
        replaced = copy.deepcopy(changed)
        replaced["storage_digest"] = self.storage
        replaced["root_ino"] += 1
        with self.assertRaisesRegex(ValueError, "DIFFERENT_STORAGE_ROOT"):
            settings.validate_file_sequence(before, enabled, replaced)

    def test_extra_setting_or_changed_keyfile_on_restart_fail(self):
        with self.probe() as supervisor:
            before = supervisor.snapshot()
            self.write("[org/xfce/mousepad/preferences/view]\n"
                       "show-line-numbers=true\nother-value=8\n")
            enabled = supervisor.snapshot()
            with self.assertRaisesRegex(ValueError, "UNEXPECTED_SETTING_CHANGE"):
                settings.validate_file_sequence(before, enabled, enabled)
            self.write("[org/xfce/mousepad/preferences/view]\n"
                       "show-line-numbers=true\nother-value=9\n")
            restarted = supervisor.snapshot()
            with self.assertRaisesRegex(ValueError, "UNEXPECTED_SETTING_CHANGE"):
                settings.validate_file_sequence(before, enabled, restarted)

    def test_atomic_close_flush_same_bytes_allowed_but_rewrite_denied(self):
        content = "[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n"
        with self.probe() as supervisor:
            before = supervisor.snapshot()
            self.write(content)
            enabled = supervisor.snapshot()
            replacement = self.folder / "atomic-new"
            replacement.write_text(content, encoding="utf-8")
            os.chmod(replacement, 0o600)
            os.replace(replacement, self.folder / "keyfile")
            restarted = supervisor.snapshot()
            settings.validate_file_sequence(before, enabled, restarted)
            self.assertNotEqual(enabled["file"]["inode"], restarted["file"]["inode"])
            self.write("[org/xfce/mousepad/preferences/view]\n"
                       "show-line-numbers = true\n")
            rewritten = supervisor.snapshot()
            with self.assertRaisesRegex(ValueError, "RESTART_KEYFILE_CHANGED"):
                settings.validate_file_sequence(before, enabled, rewritten)

    def test_intermediate_directory_replaced_during_read_is_denied(self):
        from unittest import mock
        self.write("[org/xfce/mousepad/preferences/view]\nshow-line-numbers=true\n")
        original = settings._read_keyfile

        def swap_after_read(fd, uid):
            result = original(fd, uid)
            self.folder.rename(self.folder.with_name("settings-old"))
            self.folder.mkdir(mode=0o700)
            return result

        with self.probe() as supervisor:
            with mock.patch.object(settings, "_read_keyfile", side_effect=swap_after_read):
                with self.assertRaisesRegex(ValueError, "DIRECTORY_REPLACED"):
                    supervisor.snapshot()

    def test_symlink_hardlink_and_duplicate_key_are_rejected(self):
        self.folder.mkdir(mode=0o700, parents=True)
        target = self.folder / "keyfile"
        outside = self.root / "outside"
        outside.write_text("[org/xfce/mousepad/preferences/view]\n"
                           "show-line-numbers=true\n")
        target.symlink_to(outside)
        with self.probe() as supervisor, self.assertRaises(OSError):
            supervisor.snapshot()
        target.unlink()
        os.link(outside, target)
        with self.probe() as supervisor, self.assertRaisesRegex(ValueError, "UNSAFE_KEYFILE"):
            supervisor.snapshot()
        target.unlink()
        self.write("[org/xfce/mousepad/preferences/view]\n"
                   "show-line-numbers=true\nshow-line-numbers=false\n")
        with self.probe() as supervisor, self.assertRaisesRegex(ValueError, "SYNTAX"):
            supervisor.snapshot()

    def test_symlink_path_component_and_invalid_boolean(self):
        self.folder.parent.mkdir(mode=0o700, parents=True)
        (self.folder.parent / "settings").symlink_to(self.root)
        with self.probe() as supervisor, self.assertRaises(OSError):
            supervisor.snapshot()
        (self.folder.parent / "settings").unlink()
        self.write("[org/xfce/mousepad/preferences/view]\nshow-line-numbers=TRUE\n")
        with self.probe() as supervisor, self.assertRaisesRegex(ValueError, "TYPE"):
            supervisor.snapshot()

    def test_oversized_or_replaced_storage_root_is_not_accepted(self):
        self.write("[org/xfce/mousepad/preferences/view]\n"
                   "show-line-numbers=true\n" + ("x" * 70000))
        with self.probe() as supervisor, self.assertRaisesRegex(ValueError, "UNSAFE_KEYFILE"):
            supervisor.snapshot()
        with self.probe() as supervisor:
            moved = self.root.with_name(self.root.name + "-moved")
            self.root.rename(moved)
            try:
                with self.assertRaises((ValueError, OSError)):
                    supervisor.snapshot()
            finally:
                moved.rename(self.root)


def b64(data):
    return base64.b64encode(data).decode("ascii")


def synthetic_pixel_contract():
    """Known independent unit constants, never a production golden fixture."""
    fingerprint = {
        "mousepad_commit": pixel.MOUSEPAD_COMMIT,
        "waypipe_commit": "1" * 40,
        "gtk_version": "3.24.test", "pango_version": "1.5.test",
        "cairo_version": "1.18.test", "font_sha256": "2" * 64,
        "fontconfig_sha256": "3" * 64, "mesa_vendor": "unit",
        "mesa_renderer": "unit", "renderer_revision": "unit-1",
        "theme": "unit", "locale": "C.UTF-8", "scale": "1",
    }
    states, observed = {}, {}
    for name in pixel.STATES:
        if name.startswith("first_off_"):
            shade = 0
        elif name.endswith("checkmark"):
            shade = 100
        else:
            shade = 120
        rgba = bytes([shade, shade, shade, 255] * 16)
        rect = {"x": 4, "y": 8, "width": 4, "height": 4}
        states[name] = {"rect": rect, "rgba_b64": b64(rgba),
                        "mask_b64": b64(b"\x01" * 16),
                        "max_channel_delta": 0}
        observed[name] = {
            "source": "host_renderer_readback",
            "session": "first" if name.startswith("first_") else "second",
            "capture_id": ("a" if name.startswith("first_") else "b") * 64,
            "runtime": "1" if name.startswith("first_") else "2",
            "owner_id": "first-owner" if name.startswith("first_") else "second-owner",
            "content_frame": 1,
            "rect": rect, "rgba_b64": b64(rgba),
        }
    fixture = {
        "schema": 1, "revision": "eidolon-i6-n07-pixel-v1",
        "origin": {"source": "independent_reference_capture",
                   "review_run_id": "separate-reviewed-run",
                   "review_commit": "a" * 40, "reviewer": "test-reviewer"},
        "fingerprint": fingerprint, "states": states,
        "must_differ": [
            {"a": a, "b": b, "min_changed_pixels": 8,
             "min_channel_delta": 50} for a, b in pixel.NEGATIVE_PAIRS],
    }
    run = {"schema": 1, "run_id": "real-run",
           "fingerprint": copy.deepcopy(fingerprint),
           "sessions": {
               "first": {"nonce": "a" * 16, "capture_id": "a" * 64,
                         "runtime": "1", "owner_id": "first-owner",
                         "storage_digest": "e" * 64},
               "second": {"nonce": "b" * 16, "capture_id": "b" * 64,
                          "runtime": "2", "owner_id": "second-owner",
                          "storage_digest": "e" * 64},
           }, "states": observed}
    raw = json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
    reference = pixel._decode_fixture(raw, hashlib.sha256(raw).hexdigest())
    return reference, run, raw


class PixelGoldenTests(unittest.TestCase):
    def setUp(self):
        self.ref, self.run, self.raw = synthetic_pixel_contract()

    def test_contract_only_positive_has_no_product_authority(self):
        check = pixel.compare(self.ref, self.run)
        self.assertEqual(len(check["states_compared"]), 6)
        self.assertEqual(check["negative_pairs"], 2)
        self.assertFalse(check["product_acceptance"])

    def test_production_missing_approval_fails_even_with_pixels(self):
        self.assertIsNone(pixel.APPROVED_SHA256)
        with self.assertRaisesRegex(ValueError, "GOLDEN_UNREVIEWED"):
            pixel.load_reviewed()

    def test_hash_fingerprint_and_self_reference_denied(self):
        with self.assertRaisesRegex(ValueError, "GOLDEN_UNREVIEWED"):
            pixel._decode_fixture(self.raw, "0" * 64)
        altered = copy.deepcopy(self.run)
        altered["fingerprint"]["theme"] = "other"
        with self.assertRaisesRegex(ValueError, "FINGERPRINT_MISMATCH"):
            pixel.compare(self.ref, altered)
        same_run = copy.deepcopy(self.run)
        same_run["run_id"] = self.ref["origin"]["review_run_id"]
        with self.assertRaisesRegex(ValueError, "OBSERVED_AS_EXPECTED"):
            pixel.compare(self.ref, same_run)

    def test_missing_readback_or_wrong_state_does_not_pass(self):
        altered = copy.deepcopy(self.run)
        del altered["states"]["second_on_checkmark"]
        with self.assertRaisesRegex(ValueError, "READBACK_MISSING"):
            pixel.compare(self.ref, altered)
        altered = copy.deepcopy(self.run)
        altered["states"]["first_on_gutter"]["content_frame"] = 0
        with self.assertRaisesRegex(ValueError, "READBACK_ORIGIN_INVALID"):
            pixel.compare(self.ref, altered)
        altered = copy.deepcopy(self.run)
        altered["states"]["first_on_gutter"]["capture_id"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "READBACK_ORIGIN_INVALID"):
            pixel.compare(self.ref, altered)

    def test_wrong_pixels_masks_and_borrowed_observed_image(self):
        altered = copy.deepcopy(self.run)
        altered["states"]["first_on_gutter"]["rgba_b64"] = altered["states"]["first_off_gutter"]["rgba_b64"]
        with self.assertRaisesRegex(ValueError, "PIXEL_MISMATCH"):
            pixel.compare(self.ref, altered)
        modified = json.loads(self.raw)
        modified["states"]["first_on_gutter"]["mask_b64"] = b64(b"\x00" * 16)
        raw = json.dumps(modified, sort_keys=True).encode()
        with self.assertRaisesRegex(ValueError, "MASK_INVALID"):
            pixel._decode_fixture(raw, hashlib.sha256(raw).hexdigest())
        # A candidate fixture taken from the observed run cannot use the
        # independently pinned production digest.
        with self.assertRaisesRegex(ValueError, "GOLDEN_UNREVIEWED"):
            pixel._decode_fixture(
                json.dumps(self.run, sort_keys=True).encode(),
                hashlib.sha256(self.raw).hexdigest())

    def test_pixel_difference_negative_guard_is_not_a_dummy(self):
        ref = copy.deepcopy(self.ref)
        run = copy.deepcopy(self.run)
        ref["states"]["first_on_gutter"]["max_channel_delta"] = 32
        ref["must_differ"][0]["min_channel_delta"] = 50
        # First-on pixels can be modified within tolerance, but the explicit
        # negative comparison still detects when OFF and ON were not distinct.
        run["states"]["first_on_gutter"]["rgba_b64"] = b64(bytes([100, 100, 100, 255] * 16))
        # 120 reference versus 100 observed is within tolerance, while OFF=0
        # versus ON=100 is still distinct; choose a higher negative threshold.
        ref["must_differ"][0]["min_channel_delta"] = 110
        with self.assertRaisesRegex(ValueError, "NEGATIVE_ROI_NOT_DISTINCT"):
            pixel.compare(ref, run)

    def test_storage_nonce_reuse_and_unreviewed_mask_rejected(self):
        changed = copy.deepcopy(self.run)
        changed["sessions"]["second"]["nonce"] = changed["sessions"]["first"]["nonce"]
        with self.assertRaisesRegex(ValueError, "SESSION_REUSED"):
            pixel.compare(self.ref, changed)
        changed = copy.deepcopy(self.ref)
        changed["states"]["first_on_gutter"]["mask_b64"] = b64(b"\x01" * 15 + b"\x00")
        raw = json.dumps(changed, sort_keys=True).encode()
        with self.assertRaisesRegex(ValueError, "NEGATIVE_ROI_INVALID"):
            pixel._decode_fixture(raw, hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    unittest.main()
