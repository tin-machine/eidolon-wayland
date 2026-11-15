"""Mutation tests for renderer T evidence; specimens are not execution evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import linux_real_app_v3_renderer as renderer
import linux_real_app_v3_renderer_ci as ci
from linux_real_app_v3_evidence import write_report

SOURCE = {"commit": "c" * 40, "tree_sha256": "d" * 64, "dirty": False,
          "build_features": [], "pr_head": None}
INPUTS = {"inventory_sha256": "a" * 64, "compiled_source_hashes": {"test": "b" * 64}}
UNAVAILABLE = {"returncode": 1, "status": "fail", "reason": "EGL_INIT_FAILED", "successful_receipts": 0}
ROI = [{"id": 1, "x": 624, "y": 624, "width": 32, "height": 32},
       {"id": 2, "x": 8, "y": 8, "width": 2, "height": 2}]
MISSING = object()


def census(stage):
    """Full synthetic census, retaining the global theme until renderer stop."""
    before, final = stage == "before_target", stage == "final_renderer"
    return {"workers": 0 if final else 1, "owner_tid": 7, "queued": 0,
            "contexts": 1 if stage == "retired" else 0, "targets": [],
            "latest_images": 0, "attaches": 0 if before else 2,
            "detaches": 0 if before else 2, "paused": before, "error": None,
            "composition": {"textures": 0, "texture_bytes": 0,
                            "decoration_texture_bytes": 6553604 if stage == "retired" else 0,
                            "gl_error": 0, "error": None}}


def census_mutations():
    """Each yielded change violates the stage's independent census contract."""
    for stage in ("before_target", "retired", "final_renderer"):
        original = census(stage)
        zero_paths = [("queued",), ("latest_images",), ("composition", "textures"),
                      ("composition", "texture_bytes"), ("composition", "gl_error")]
        if stage != "retired":
            zero_paths += [("contexts",), ("composition", "decoration_texture_bytes")]
        if stage == "before_target":
            zero_paths += [("attaches",), ("detaches",)]
        elif stage == "final_renderer":
            zero_paths += [("workers",)]
        for path in zero_paths:
            for changed in (1, 64, -1, False, True, 0.0, "0", None):
                yield (stage, *path), changed
        for path in (("error",), ("composition", "error")):
            for changed in (0, 1, False, True, 0.0, "GL_ERROR"):
                yield (stage, *path), changed
        for changed in (0, -1, False, True, 1.0, "7", None, 2**31):
            yield (stage, "owner_tid"), changed
        for changed in (0, 1, 0.0, None, not original["paused"]):
            yield (stage, "paused"), changed
        yield (stage, "targets"), [{}]
        yield (stage, "targets"), None
        if stage != "final_renderer":
            for changed in (0, 2, 64, False, True, 1.0, None):
                yield (stage, "workers"), changed
        if stage != "before_target":
            for field in ("attaches", "detaches"):
                for changed in (0, 1, 64, False, True, 2.0, None):
                    yield (stage, field), changed
        if stage == "retired":
            for changed in (-1, 0, 2, 64, False, True, 1.0, None):
                yield (stage, "contexts"), changed
            for changed in (-1, 32 * 1024 * 1024 + 1, False, True, 0.0, None):
                yield (stage, "composition", "decoration_texture_bytes"), changed
        for field in original:
            yield (stage, field), MISSING
        for field in original["composition"]:
            yield (stage, "composition", field), MISSING
        yield (stage, "unknown"), 0
        yield (stage, "composition", "unknown"), 0
    yield ("retired", "owner_tid"), 8
    yield ("final_renderer", "owner_tid"), 8


def mutate_path(value, path, changed):
    for key in path[:-1]:
        value = value[key]
    if changed is MISSING:
        del value[path[-1]]
    else:
        value[path[-1]] = changed


def queue_mutations():
    for changed in (0, 1, 7, 9, 64, False, True, 8.0, "8", None, MISSING):
        yield changed


def mutate_queue(native, changed):
    row = next(row for row in native["negative_cases"] if row["case"] == "queue_capacity")
    mutate_path(row, ("accepted",), changed)


def specimen():
    """Synthetic parser specimen with no origin in a product run."""
    negative_statuses = {
        "foreign_owner": "Stale", "ticket_replay": "Stale", "wrong_session": "StaleOrNotReady",
        "wrong_window": "StaleOrNotReady", "uncreated_generation": "StaleOrNotReady",
        "roi_count": "InvalidArgument", "roi_bytes": "InvalidArgument", "roi_bounds": "InvalidArgument",
        "min_content_frame": "Pending", "queue_capacity": "Busy", "foreign_fence": "Rejected",
        "late_attach": "Stale", "retired_generation": "StaleOrNotReady",
        "old_target_new_generation": "Preserved", "retired_window_attach": "Stale",
        "after_stop": "NotReadyOrStale",
    }
    rows = []
    for index, label in enumerate(renderer.LABELS):
        frame, focused = [(0, False), (0, False), (0, True), (1, True), (2, True)][index]
        rows.append({"label": label, "fixture_frame": frame, "focused": focused,
                     "receipt": {"renderer_owner": 1, "context_epoch": 1, "request_seq": [1, 2, 4, 5, 91][index],
                                 "target": {"session": "1", "window": "1", "scene": "1",
                                            "generation": "2" if index == 4 else "1"},
                                 "content_frame": str(frame + 1), "min_content_frame": str(frame + 1),
                                 "present_seq": index + 1,
                                 "snapshot_id": f"1:1:1:{frame + 1}",
                                 "roi_revision": 1, "roi_sha256": renderer.roi_digest(),
                                 "pixel_sha256": [renderer.sha(renderer.expected_bytes(frame, roi["id"], focused)) for roi in ROI],
                                 "logical_size": {"width": 32, "height": 32},
                                 "geometry": {"origin": {"x": 0, "y": 0}, "size": {"width": 32, "height": 32}},
                                 "layers": [{"surface": "1", "content_seq": str(frame + 1),
                                             "buffer_size": {"width": 32, "height": 32},
                                             "offset": {"x": 0, "y": 0},
                                             "clip": {"origin": {"x": 0, "y": 0}, "size": {"width": 32, "height": 32}},
                                             "scale": 1, "transform": "Normal"}],
                                 "content_rect": {"origin": [624, 624], "size": [32, 32]},
                                 "backend": {"vendor": "Mesa", "renderer": "llvmpipe (test specimen)",
                                             "version": "OpenGL ES 3.2 specimen", "readback": True,
                                             "rgba_bits": [8, 8, 8, 8], "width": 1280, "height": 1280, "config_id": 1},
                                 "rois": [{"roi": deepcopy(roi)} for roi in ROI]},
                     "comparisons": [{"roi_id": roi["id"], "bytes": len(renderer.expected_bytes(frame, roi["id"], focused)),
                                      "actual_sha256": renderer.sha(renderer.expected_bytes(frame, roi["id"], focused)),
                                      "expected_sha256": renderer.sha(renderer.expected_bytes(frame, roi["id"], focused))}
                                     for roi in ROI]})
    return {"schema": 1, "revision": renderer.REVISION, "status": "pass", "evidence_class": "T",
            "host_v3_acceptance_passed": False, "coverage": deepcopy(renderer.COVERAGE),
            "identity": {"source": {**SOURCE, "build_features": ["host-acceptance"]}, "inputs": deepcopy(INPUTS)},
            "before_target": census("before_target"),
            "readbacks": rows,
            "negative_cases": [{"case": case, "status": status, **({"accepted": 8} if case == "queue_capacity" else {})}
                               for case, status in negative_statuses.items()],
            "client": {"frames": 100, "protocol_error": False},
            "retired": census("retired"),
            "final_renderer": census("final_renderer"),
            "owner_reclaimed": True}


class RendererEvidenceTests(unittest.TestCase):
    def checked(self, value):
        return renderer.validate(value, SOURCE, INPUTS, UNAVAILABLE)

    def test_renderer_evidence_retains_full_i6_and_n02_as_unperformed(self):
        result = self.checked(specimen())
        self.assertTrue(result["renderer_t_passed"])
        self.assertFalse(result["host_v3_acceptance_passed"])
        self.assertEqual(result["coverage"], renderer.COVERAGE)
        self.assertEqual([result["i6_manifest"][k] for k in ("families", "required_variants", "assertion_references")], [28, 87, 533])
        self.assertEqual(result["environment_disposal"], "not_yet")

    def test_wrong_owner_target_generation_and_replayed_request_rejected(self):
        mutations = [
            lambda v: v["readbacks"][1]["receipt"].update(renderer_owner=2),
            lambda v: v["readbacks"][1]["receipt"]["target"].update(session="2"),
            lambda v: v["readbacks"][1]["receipt"]["target"].update(window="2"),
            lambda v: v["readbacks"][1]["receipt"]["target"].update(generation="2"),
            lambda v: v["readbacks"][1]["receipt"].update(request_seq=1),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                value = specimen()
                mutation(value)
                with self.assertRaises(ValueError):
                    self.checked(value)

    def test_idle_and_decoration_cannot_inflate_content_sequence(self):
        for index in (1, 2):
            value = specimen()
            value["readbacks"][index]["receipt"]["content_frame"] = "2"
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.checked(value)

    def test_minimum_frame_and_present_sequence_are_checked(self):
        for field, value in (("min_content_frame", "5"), ("present_seq", 1)):
            sample = specimen()
            sample["readbacks"][1]["receipt"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.checked(sample)

    def test_receipt_must_match_an_actual_fixed_request_and_its_minimum(self):
        for field, changed in (("request_seq", 999), ("min_content_frame", "1")):
            native = specimen()
            native["readbacks"][3]["receipt"][field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.checked(native)

    def test_pixels_are_compared_to_independent_input_formula(self):
        value = specimen()
        comparison = value["readbacks"][3]["comparisons"][0]
        comparison["expected_sha256"] = comparison["actual_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            self.checked(value)

    def test_receipt_digest_snapshot_and_context_metadata_are_not_ignored(self):
        for field, changed in (("pixel_sha256", ["f" * 64] * 2), ("roi_sha256", "f" * 64),
                               ("roi_revision", 2), ("snapshot_id", "2:1:1:1"), ("context_epoch", 2)):
            native = specimen()
            native["readbacks"][1]["receipt"][field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.checked(native)

    def test_every_required_receipt_field_is_mandatory(self):
        for field in renderer.RECEIPT_FIELDS:
            native = specimen()
            del native["readbacks"][0]["receipt"][field]
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                self.checked(native)

    def test_layer_identity_buffer_geometry_and_content_sequence_are_checked(self):
        for field, changed in (("surface", "2"), ("content_seq", "2"), ("scale", 2),
                               ("transform", "Rotate90"), ("offset", {"x": 1, "y": 0}),
                               ("buffer_size", {"width": 31, "height": 32}), ("clip", {})):
            native = specimen()
            native["readbacks"][1]["receipt"]["layers"][0][field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.checked(native)

    def test_raw_pixels_and_boolean_integer_substitution_are_rejected(self):
        native = specimen()
        native["readbacks"][0]["receipt"]["rois"][0]["rgba"] = [0, 0, 0, 255]
        with self.assertRaises(ValueError):
            self.checked(native)
        for changed in (True, 1.0):
            native = specimen()
            native["readbacks"][0]["receipt"]["roi_revision"] = changed
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.checked(native)

    def test_changed_geometry_or_roi_cannot_reuse_same_pixel_digest(self):
        for change in ("geometry", "content_rect", "roi"):
            value = specimen()
            receipt = value["readbacks"][0]["receipt"]
            if change == "roi":
                receipt["rois"][0]["roi"]["x"] -= 1
            elif change == "geometry":
                receipt[change]["origin"]["x"] = 1
            else:
                receipt[change]["origin"][0] = 0
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.checked(value)

    def test_stub_backend_or_missing_backend_success_is_rejected(self):
        value = specimen()
        value["readbacks"][0]["receipt"]["backend"]["renderer"] = "host stub"
        with self.assertRaises(ValueError):
            self.checked(value)
        for changed in ({"returncode": 0}, {"status": "skip"}, {"successful_receipts": 1}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                renderer.validate(specimen(), SOURCE, INPUTS, {**UNAVAILABLE, **changed})

    def test_backend_fingerprint_cannot_change_between_observations(self):
        value = specimen()
        value["readbacks"][4]["receipt"]["backend"]["version"] = "OpenGL ES 3.2 another Mesa"
        with self.assertRaises(ValueError):
            self.checked(value)

    def test_backend_config_id_requires_a_positive_exact_integer(self):
        for config_id in (False, True, 0, -1, 1.0, "1"):
            value = specimen()
            for row in value["readbacks"]:
                row["receipt"]["backend"]["config_id"] = config_id
            with self.subTest(config_id=config_id), self.assertRaises(ValueError):
                self.checked(value)

    def test_unavailable_exit_and_receipt_count_reject_boolean_and_float_aliases(self):
        for changed in ({"returncode": True}, {"returncode": 1.0},
                        {"successful_receipts": False}, {"successful_receipts": 0.0},
                        {"returncode": True, "successful_receipts": False}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                renderer.validate(specimen(), SOURCE, INPUTS, {**UNAVAILABLE, **changed})

    def test_eager_egl_allocation_and_unreclaimed_resources_rejected(self):
        for section, field, value in (("before_target", "contexts", 1), ("retired", "latest_images", 1),
                                      ("final_renderer", "workers", 1), ("final_renderer", "contexts", 1)):
            native = specimen()
            native[section][field] = value
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                self.checked(native)

    def test_stage_census_requires_quiescent_owned_resources_and_strict_types(self):
        for path, changed in census_mutations():
            native = specimen()
            mutate_path(native, path, changed)
            with self.subTest(path=path, changed=changed), self.assertRaises((ValueError, KeyError)):
                self.checked(native)

    def test_retired_global_theme_and_context_remain_legal_until_stop(self):
        for contexts, theme_bytes in ((1, 6553604), (1, 0), (0, 0)):
            native = specimen()
            native["retired"]["contexts"] = contexts
            native["retired"]["composition"]["decoration_texture_bytes"] = theme_bytes
            with self.subTest(contexts=contexts, theme_bytes=theme_bytes):
                self.assertTrue(self.checked(native)["renderer_t_passed"])

    def test_capacity_requires_exactly_eight_pending_requests_before_busy(self):
        for changed in queue_mutations():
            native = specimen()
            mutate_queue(native, changed)
            with self.subTest(changed=changed), self.assertRaises((ValueError, KeyError)):
                self.checked(native)

    def test_missing_negative_case_and_unbounded_queue_are_rejected(self):
        native = specimen()
        native["negative_cases"].pop()
        with self.assertRaises(ValueError):
            self.checked(native)
        native = specimen()
        next(r for r in native["negative_cases"] if r["case"] == "queue_capacity")["accepted"] = 65
        with self.assertRaises(ValueError):
            self.checked(native)

    def test_source_feature_or_content_mismatch_rejected(self):
        for field, changed in (("commit", "e" * 40), ("tree_sha256", "e" * 64),
                                ("build_features", []), ("dirty", True)):
            native = specimen()
            native["identity"]["source"][field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.checked(native)

    def test_cannot_promote_renderer_t_to_n02_or_sdk_success(self):
        for field in renderer.COVERAGE:
            native = specimen()
            native["coverage"][field] = "pass"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.checked(native)


class RendererExportTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.incoming = self.root / "incoming"
        self.output = self.root / "output"
        self.incoming.mkdir(mode=0o700)
        self.output.mkdir(mode=0o700)
        self.image = self.root / "image.id"
        self.image.write_text("sha256:" + "e" * 64)
        native = specimen()
        for name, value in (
                ("renderer-native.json", native), ("renderer-unavailable.json", UNAVAILABLE),
                ("renderer-environment.json", {"source": SOURCE, "inputs": INPUTS, "uid": 12001,
                                               "renderer_packages": ["egl=1", "mesa=2", "gles=3", "dri=4"],
                                               "environment": {"LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe",
                                                               "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe", "EGL_PLATFORM": "surfaceless"}}),
                ("renderer-validation.json", renderer.validate(native, SOURCE, INPUTS, UNAVAILABLE))):
            write_report(self.incoming / name, value)

    def export(self, attached="0", exited="0", running="false"):
        with mock.patch.object(ci, "source_snapshot", return_value=SOURCE), \
                mock.patch.object(ci, "source_input_hashes", return_value=INPUTS):
            ci.export(self.incoming, self.output, self.image, SOURCE["commit"], attached, exited,
                      running, self.root, "")

    def test_export_recomputes_verdict_and_keeps_checkout_distinct_from_pr_head(self):
        self.export()
        report = json.loads((self.output / "renderer-container.json").read_text())
        self.assertEqual(report["checkout_commit"], SOURCE["commit"])
        self.assertIsNone(report["pr_head"])
        self.assertFalse(report["host_v3_acceptance_passed"])

    def test_failed_container_or_external_rescue_cannot_pass(self):
        for arguments in (("124", "0", "false"), ("0", "1", "false"), ("0", "0", "true")):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.export(*arguments)

    def test_saved_verdict_cannot_override_independent_recomputation(self):
        path = self.incoming / "renderer-validation.json"
        value = json.loads(path.read_text())
        value["renderer_t_passed"] = False
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.export()

    def test_saved_verdict_booleans_cannot_be_replaced_by_numeric_aliases(self):
        path = self.incoming / "renderer-validation.json"
        original = json.loads(path.read_text())
        for index, changed in enumerate(({"renderer_t_passed": 1}, {"host_v3_acceptance_passed": 0},
                                        {"renderer_t_passed": 1, "host_v3_acceptance_passed": 0})):
            self.output = self.root / f"output-mutated-verdict-{index}"
            self.output.mkdir(mode=0o700)
            path.write_text(json.dumps({**original, **changed}))
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.export()

    def test_outer_export_rejects_every_stage_census_mutation(self):
        path = self.incoming / "renderer-native.json"
        for index, (fields, changed) in enumerate(census_mutations()):
            native = specimen()
            mutate_path(native, fields, changed)
            # Deliberately write arbitrary JSON here: float and type failures
            # must be rejected by the real outer reader, not this test writer.
            path.write_text(json.dumps(native))
            self.output = self.root / f"census-mutation-{index}"
            self.output.mkdir(mode=0o700)
            with self.subTest(fields=fields, changed=changed), self.assertRaises((ValueError, KeyError)):
                self.export()
            self.assertFalse((self.output / "renderer-container.json").exists())

    def test_outer_export_rejects_capacity_underfill_overflow_and_type_aliases(self):
        path = self.incoming / "renderer-native.json"
        for index, changed in enumerate(queue_mutations()):
            native = specimen()
            mutate_queue(native, changed)
            path.write_text(json.dumps(native))
            self.output = self.root / f"capacity-mutation-{index}"
            self.output.mkdir(mode=0o700)
            with self.subTest(changed=changed), self.assertRaises((ValueError, KeyError)):
                self.export()
            self.assertFalse((self.output / "renderer-container.json").exists())

    def test_missing_and_symlink_receipts_cannot_pass(self):
        path = self.incoming / "renderer-unavailable.json"
        path.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.export()
        path.symlink_to(self.image)
        with self.assertRaises((ValueError, OSError)):
            self.export()

    def test_failed_run_keeps_diagnostics_and_explicit_failure_without_saved_success(self):
        arguments = ["renderer-ci", str(self.incoming), str(self.output), str(self.image),
                     SOURCE["commit"], "1", "1", "false", str(self.root), ""]
        with mock.patch.object(sys, "argv", arguments), \
                mock.patch.object(ci, "source_snapshot", return_value=SOURCE):
            self.assertEqual(ci.main(), 1)
        self.assertTrue((self.output / "renderer-native.json").is_file())
        self.assertFalse((self.output / "renderer-validation.json").exists())
        self.assertFalse(json.loads((self.output / "renderer-container.json").read_text())["renderer_t_passed"])


if __name__ == "__main__":
    unittest.main()
