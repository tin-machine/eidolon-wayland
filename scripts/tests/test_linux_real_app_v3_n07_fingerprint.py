"""Fingerprint observations remain diagnostic and reject missing/mixed evidence."""
from __future__ import annotations

import copy
import errno
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_real_app_v3_n07_fingerprint as fp
from test_linux_real_app_v3_n07_b2 import candidate


def fixture():
    native = candidate()
    source = {"commit": "1" * 40, "tree_sha256": "2" * 64, "dirty": False,
              "pr_head": None, "build_features": []}
    native.update(source_sha256=source["tree_sha256"], build_source=source,
                  owner_id="3" * 32)
    probe = native["n07_probe"]
    for readback in [probe["before_menu"]["readback"], *(
            probe["b2"][name]["readback"] for name in (
                "first_off_checkmark", "first_on_gutter", "first_on_checkmark"))]:
        for layer in readback["receipt"]["layers"]:
            layer["scale"] = 1
    environment = {"run_id": native["run_id"], "source_sha256": source["tree_sha256"],
                   "app": {"uid": 12002},
                   "before": {"pins": {"waypipe_sha256": "4" * 64}},
                   "after": {"pins": {"waypipe_sha256": "4" * 64}}}
    process_env = dict.fromkeys(fp.ENVIRONMENT)
    process_env.update(LANG="C.UTF-8", LC_ALL="C.UTF-8")
    evidence = {
        "policy": {"kind": "disposable-system-typography-v1",
            "gtk_settings_sha256": fp.sha(fp.GTK_POLICY),
            "fontconfig_sha256": fp.sha(fp.FONT_POLICY),
            "configured_theme": "Adwaita", "configured_font": "DejaVu Sans 10",
            "theme_observation": "configured-input-only-not-effective-property"},
        "source_pins": {"mousepad_commit": fp.pixel.MOUSEPAD_COMMIT,
            "mousepad_executable_sha256": "5" * 64,
            "mousepad_evidence_sha256": "6" * 64,
            "waypipe_commit": fp.WAYPIPE_COMMIT,
            "waypipe_archive_sha256": fp.WAYPIPE_ARCHIVE,
            "waypipe_upstream_inventory_sha256": fp.WAYPIPE_UPSTREAM_INVENTORY,
            "waypipe_tree_sha256": "7" * 64, "waypipe_binary_sha256": "4" * 64,
            "waypipe_patch_sha256": "8" * 64,
            "renderer_source": {"algorithm": fp.RENDERER_ALGORITHM,
                                "file_count": 250, "sha256": "9" * 64}},
        "process": {"pid": 200, "start_ticks": 12345, "uid": 12002,
                    "executable_sha256": "5" * 64, "environment": process_env,
                    "samples": 10},
        "fonts": [{"path": str(fp.FONT_DIRECTORY / "DejaVuSans.ttf"),
                   "size": 54321, "sha256": "a" * 64}],
        "libraries": {role: {"path": "/usr/lib/x86_64-linux-gnu/" + prefix + "0",
                              "size": 123456, "sha256": str(i) * 64,
                              "version": version}
                      for i, (role, prefix, version) in enumerate((
                          ("gtk", fp.LIBRARIES["gtk"], "3.24.41"),
                          ("pango", fp.LIBRARIES["pango"], "1.52.1"),
                          ("cairo", fp.LIBRARIES["cairo"], "1.18.0")))},
        "renderer": fp._renderer(native), "observation_errors": [],
    }
    record = {"schema": 1, "revision": fp.REVISION,
              "binding": fp._binding(native, environment, source), "evidence": evidence}
    values = fp._derive(record, native, environment, source)
    missing = sorted(fp.pixel.FINGERPRINT - set(values))
    record.update(values=values, missing=missing, status="partial")
    return record, native, environment, source


class FingerprintTests(unittest.TestCase):
    def test_waypipe_patch_receipt_tracks_the_bytes_and_order_of_the_build(self):
        root = Path(__file__).resolve().parents[2]
        patches = json.loads((root / "native/deps.lock.json").read_text())["waypipe"]["patches"]
        with patch.object(fp, "_read", side_effect=Path.read_bytes):
            self.assertEqual(fp._waypipe_patch_digest(root, [patches[0]]), patches[0]["sha256"])
            self.assertEqual(fp._waypipe_patch_digest(root, patches), fp.sha(fp.canonical(patches)))
            self.assertNotEqual(fp._waypipe_patch_digest(root, patches),
                                fp._waypipe_patch_digest(root, list(reversed(patches))))
            for invalid in ([], [dict(patches[0], sha256="0" * 64)],
                            [dict(patches[0], file="../unrelated.patch")]):
                with self.assertRaisesRegex(fp.FingerprintError, "N07_FINGERPRINT_WAYPIPE_SOURCE"):
                    fp._waypipe_patch_digest(root, invalid)

    def test_observed_fields_are_bound_and_theme_remains_missing(self):
        record, native, environment, source = fixture()
        self.assertIs(fp.validate_fingerprint(record, native, environment, source), record)
        self.assertEqual(record["missing"], ["theme"])
        self.assertEqual(record["status"], "partial")
        self.assertEqual(len(record["values"]), len(fp.pixel.FINGERPRINT) - 1)
        self.assertEqual(record["values"]["font_sha256"],
                         fp.sha(fp.canonical(record["evidence"]["fonts"])))
        self.assertEqual(record["values"]["renderer_revision"], "sha256:" + "9" * 64)

    def test_mutating_each_contract_field_rejects(self):
        record, native, environment, source = fixture()
        for field in record["values"]:
            with self.subTest(field=field):
                bad = copy.deepcopy(record)
                bad["values"][field] = "changed"
                with self.assertRaises(fp.FingerprintError):
                    fp.validate_fingerprint(bad, native, environment, source)
                bad = copy.deepcopy(record)
                del bad["values"][field]
                with self.assertRaises(fp.FingerprintError):
                    fp.validate_fingerprint(bad, native, environment, source)

    def test_no_configured_theme_promotion_or_fabricated_approval(self):
        for change in (
            lambda r: r.update(status="complete", missing=[]),
            lambda r: r["values"].update(theme="Adwaita"),
            lambda r: r.update(reviewed=True),
            lambda r: r["evidence"]["policy"].update(theme_observation="gtk_getter"),
        ):
            record, native, environment, source = fixture()
            change(record)
            with self.assertRaises(fp.FingerprintError):
                fp.validate_fingerprint(record, native, environment, source)

    def test_identity_capture_generation_sdk_and_renderer_cannot_mix(self):
        record, native, environment, source = fixture()
        for field in record["binding"]:
            with self.subTest(field=field):
                bad = copy.deepcopy(record)
                if field == "target":
                    bad["binding"][field]["generation"] = "2"
                else:
                    bad["binding"][field] = "foreign"
                with self.assertRaises(fp.FingerprintError):
                    fp.validate_fingerprint(bad, native, environment, source)
        for changed in (True, 15.0):
            bad = copy.deepcopy(record)
            bad["binding"]["renderer_owner"] = changed
            with self.assertRaises(fp.FingerprintError):
                fp.validate_fingerprint(bad, native, environment, source)

    def test_font_and_library_inventory_types_drift_and_missing(self):
        changes = (
            lambda r: r["evidence"]["fonts"][0].update(size=True),
            lambda r: r["evidence"]["fonts"][0].update(sha256="bad"),
            lambda r: r["evidence"]["fonts"].append(copy.deepcopy(r["evidence"]["fonts"][0])),
            lambda r: r["evidence"]["fonts"][0].update(path="/home/user/document.ttf"),
            lambda r: r["evidence"]["fonts"].clear(),
            lambda r: r["evidence"]["libraries"]["gtk"].update(version="3.0"),
            lambda r: r["evidence"]["libraries"]["gtk"].update(path="/tmp/libgtk-3.so.0"),
            lambda r: r["evidence"]["libraries"].pop("pango"),
            lambda r: r["evidence"]["process"].update(uid=12001),
            lambda r: r["evidence"]["process"].update(start_ticks=False),
            lambda r: r["evidence"].update(process=None),
            lambda r: r["evidence"]["source_pins"].update(waypipe_commit="f" * 40),
            lambda r: r["evidence"]["source_pins"].update(waypipe_archive_sha256="f" * 64),
        )
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                record, native, environment, source = fixture()
                change(record)
                with self.assertRaises(fp.FingerprintError):
                    fp.validate_fingerprint(record, native, environment, source)

    def test_missing_live_process_is_diagnostic_not_guessed_fonts_or_versions(self):
        record, native, environment, source = fixture()
        record["evidence"].update(process=None, fonts=[], libraries={},
                                  observation_errors=["N07_FINGERPRINT_OBSERVATION_FAILED"])
        record["values"] = fp._derive(record, native, environment, source)
        record["missing"] = sorted(fp.pixel.FINGERPRINT - set(record["values"]))
        fp.validate_fingerprint(record, native, environment, source)
        self.assertEqual(record["missing"], ["cairo_version", "font_sha256", "gtk_version",
                                              "locale", "pango_version", "theme"])

    def test_dynamic_swap_metric_never_enters_environment_identity(self):
        record, native, environment, source = fixture()
        native["n07_probe"]["before_menu"]["readback"]["receipt"]["backend"]["max_swap_us"] = 900
        fp.validate_fingerprint(record, native, environment, source)
        self.assertNotIn("max_swap_us", fp.canonical(record).decode())
        self.assertNotIn("content_frame", fp.canonical(record).decode())

    def test_foreign_mesa_or_mixed_scale_does_not_get_silently_normalized(self):
        record, native, environment, source = fixture()
        record["evidence"]["renderer"]["vendor"] = "other"
        with self.assertRaises(fp.FingerprintError):
            fp.validate_fingerprint(record, native, environment, source)
        record, native, environment, source = fixture()
        native["n07_probe"]["b2"]["first_on_gutter"]["readback"]["receipt"]["layers"][0]["scale"] = True
        with self.assertRaises(fp.FingerprintError):
            fp.validate_fingerprint(record, native, environment, source)
        record, native, environment, source = fixture()
        record["evidence"]["renderer"]["layer_scales"] = [True]
        with self.assertRaises(fp.FingerprintError):
            fp.validate_fingerprint(record, native, environment, source)

    def test_environment_export_is_allowlisted_and_rejects_override(self):
        selected = fp._environment(b"LANG=C.UTF-8\0LC_ALL=C.UTF-8\0SECRET=DO_NOT_EXPORT\0HOME=/private\0")
        self.assertNotIn("SECRET", selected)
        self.assertNotIn("HOME", selected)
        record, native, environment, source = fixture()
        record["evidence"]["process"]["environment"]["GTK3_MODULES"] = "arbitrary-module"
        with self.assertRaises(fp.FingerprintError):
            fp.validate_fingerprint(record, native, environment, source)

    def test_maps_are_bounded_and_do_not_export_arbitrary_paths(self):
        raw = b"100-200 r--p 0 00:00 1 /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf\n"
        raw += b"200-300 r-xp 0 00:00 2 /usr/lib/x86_64-linux-gnu/libgtk-3.so.0\n"
        raw += b"300-400 r--p 0 00:00 3 /home/private-document\n"
        fonts, libraries = fp._mapped_paths(raw)
        self.assertEqual(fonts, [str(fp.FONT_DIRECTORY / "DejaVuSans.ttf")])
        self.assertEqual(set(libraries), {"gtk"})
        for bad in (
            raw + b"400-500 r--p 0 00:00 4 /home/custom.ttf\n",
            raw.replace(b"DejaVuSans.ttf", b"DejaVuSans.ttf (deleted)"),
            b" " * (fp.MAX_MAPS + 1),
        ):
            with self.assertRaises(fp.FingerprintError):
                fp._mapped_paths(bad)

    def test_unreadable_discovery_does_not_discard_valid_mousepad_snapshot(self):
        # Reproduce a same-directory-owner /proc entry whose exe is protected,
        # both before and after a readable actual-app-shaped entry. No GUI or
        # real receipt is synthesized: this exercises only process discovery.
        uid = os.getuid()
        with tempfile.TemporaryDirectory(prefix="n07-proc-discovery-") as temporary:
            first, app = Path(temporary) / "100", Path(temporary) / "200"
            for directory in (first, app):
                directory.mkdir()
                (directory / "status").write_bytes(b"Uid:\t" +
                    b"\t".join(str(uid).encode() for _ in range(4)) + b"\n")
            (app / "stat").write_bytes(b"200 (mousepad) S " + b"0 " * 18 + b"12345\n")
            (app / "environ").write_bytes(b"LANG=C.UTF-8\0LC_ALL=C.UTF-8\0")
            (app / "maps").write_bytes(b"")

            def executable(path):
                if path.parent == first:
                    raise PermissionError(errno.EACCES, "protected process")
                return "/usr/bin/mousepad"

            for order in ([first, app], [app, first]):
                observer = fp.LiveFingerprintObserver.__new__(fp.LiveFingerprintObserver)
                observer.app_uid, observer.pins = uid, {"mousepad_executable_sha256": "f" * 64}
                observer.process, observer.fonts, observer.libraries = None, {}, {}
                observer.errors = set()
                with patch.object(fp.Path, "iterdir", return_value=iter(order)), \
                     patch.object(fp.os, "readlink", side_effect=executable), \
                     patch.object(observer, "_cached_file", return_value={"sha256": "f" * 64}):
                    observer._sample()
                self.assertEqual(observer.process["pid"], 200)
                self.assertEqual(observer.process["start_ticks"], 12345)
                self.assertEqual(observer.process["samples"], 1)
                self.assertEqual(observer.errors, set())

    def test_privilege_transition_is_not_an_ordinary_app_process(self):
        uid = 12002
        self.assertTrue(fp._process_credentials(b"Uid:\t12002\t12002\t12002\t12002\n", uid))
        self.assertFalse(fp._process_credentials(b"Uid:\t0\t12002\t0\t12002\n", uid))
        for invalid in (b"Uid:\t12002\n", b"Uid:\t12002 x 12002 12002\n", b"", b" " * 16385):
            with self.assertRaises(fp.FingerprintError):
                fp._process_credentials(invalid, uid)

    def test_identified_target_access_failure_has_specific_stage_without_private_path(self):
        reason = fp._io_reason("PROCESS_ENVIRONMENT",
                               PermissionError(errno.EACCES, "secret", "/home/private"))
        self.assertEqual(reason, "N07_FINGERPRINT_PROCESS_ENVIRONMENT_ACCESS_DENIED")
        self.assertNotIn("private", reason)

    def test_setup_is_explicitly_disposable_only(self):
        with self.assertRaises(fp.FingerprintError):
            fp.prepare_fixture(disposable=False)
        with patch.object(fp.os, "getuid", return_value=1000):
            with self.assertRaises(fp.FingerprintError):
                fp.prepare_fixture(disposable=True)

    def test_docker_review_font_packages_are_opt_in_and_unknown_modes_reject(self):
        dockerfile = (fp.ROOT / "scripts/fixtures/linux-desktop-v3-acceptance/Dockerfile.n01").read_text()
        default = re.search(r"(?m)^ARG I6_N07_REVIEW_DEPS=(\S+)$", dockerfile).group(1)
        self.assertEqual(default, "0")
        # Execute the actual installation RUN fragment with inert command
        # substitutes. This checks shell array expansion and rejects bad modes
        # before APT, without a Docker daemon, package mutation or network.
        command = re.search(r"(?ms)^RUN (.*?)(?=\n\n)", dockerfile).group(1)
        harness = "timeout() { printf '%s\\0' \"$@\"; }; rm() { :; };\n" + command

        def invoke(mode):
            return subprocess.run(["/bin/bash", "-euo", "pipefail", "-c", harness],
                env={"PATH": "/usr/bin:/bin", "I6_N07_REVIEW_DEPS": mode},
                capture_output=True, timeout=5)

        baseline, review = invoke(default), invoke("1")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(review.returncode, 0, review.stderr)
        baseline_args, review_args = baseline.stdout.split(b"\0"), review.stdout.split(b"\0")
        self.assertNotIn(b"fontconfig", baseline_args)
        self.assertNotIn(b"fonts-dejavu-core", baseline_args)
        self.assertEqual([arg for arg in review_args if arg not in baseline_args],
                         [b"fontconfig", b"fonts-dejavu-core"])
        for bad in ("", "2", "true", "1 other"):
            result = invoke(bad)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")

    @unittest.skipIf(os.getuid() == 0, "worker deliberately rejects root")
    def test_same_uid_worker_exits_on_supervisor_eof_without_runtime_resources(self):
        completed = subprocess.run([
            sys.executable, "-B", str(Path(fp.__file__).resolve()), "--observe",
            str(os.getuid()), str(os.getgid()), "f" * 64,
        ], input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=5, check=True)
        self.assertEqual(json.loads(completed.stdout), {
            "process": None, "fonts": {}, "libraries": {},
            "errors": ["N07_FINGERPRINT_NO_APP_OBSERVATION"]})
        self.assertEqual(completed.stderr, b"")

    def test_failed_observer_does_not_skip_product_cleanup_export(self):
        class FailedWorker:
            stdin = io.BytesIO()
            returncode = 2

            def communicate(self, timeout):
                return b"", b""

        observer = fp.LiveFingerprintObserver.__new__(fp.LiveFingerprintObserver)
        observer.worker = FailedWorker()
        observer.errors = set()
        observer.__exit__(None, None, None)
        self.assertEqual(observer.errors, {"N07_FINGERPRINT_WORKER_FAILED"})

    def test_production_loader_stays_closed(self):
        self.assertIsNone(fp.pixel.APPROVED_SHA256)
        with self.assertRaisesRegex(fp.pixel.PixelEvidenceError, "N07_GOLDEN_UNREVIEWED"):
            fp.pixel.load_reviewed()


if __name__ == "__main__":
    unittest.main()
