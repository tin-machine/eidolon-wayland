"""Offline contract tests for docs.py: ID graph, parser, drift, authority and safety."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("eidolon_docs_resolver", REPO / "scripts/docs.py")
docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        schemas = self.root / "docs/knowledge/schemas"
        schemas.mkdir(parents=True)
        for path in (REPO / "docs/knowledge/schemas").glob("*.json"):
            shutil.copy(path, schemas / path.name)
        (self.root / "docs/features/sample").mkdir(parents=True)
        (self.root / "native").mkdir()
        (self.root / "native/app.rs").write_text("fn main() {}\n")
        (self.root / "docs/features/sample/README.md").write_text("# Sample\n")
        self.write_doc("docs/contract.md", "sample-contract", "contract")
        self.write_doc("docs/implementation.md", "sample-implementation", "detailed-design",
                       depends=["sample-contract"])
        self.write_manifest()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.baseline = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True)

    def write_doc(self, path, id, kind, *, depends=(), status="accepted", related="native/app.rs"):
        text = ("---\n"
                f"id: {id}\n"
                f"title: {id}\n"
                f"type: {kind}\n"
                f"design_status: {status}\n"
                "feature: sample\n"
                'created: "2026-11-13"\n'
                'last_reviewed: "2026-11-13"\n'
                f"baseline_commit: \"{'1' * 40}\"\n"
                f"depends_on: [{', '.join(depends)}]\n"
                f"related_sources:\n  - {related}\n"
                "---\n"
                f"# {id}\n")
        (self.root / path).write_text(text)

    def write_manifest(self):
        (self.root / "docs/features/sample/manifest.yaml").write_text(
            "schema: 1\nfeature: sample\nentrypoint: README.md\n"
            "contracts: [sample-contract]\n"
            "tasks:\n"
            "  I4:\n"
            "    document: sample-implementation\n"
            "    requires: [sample-contract]\n"
            "    test_plans: []\n"
            "source_paths:\n"
            "  - native/**\n")

    def load(self):
        return docs.docs_and_manifests(self.root)

    def test_valid_context_order(self):
        registered, features = self.load()
        result = docs.context(self.root, registered, features, "sample", "I4")
        self.assertEqual([d["id"] for d in result["documents"]],
                         ["sample-contract", "sample-implementation"])
        self.assertIn("entrypoint", result)
        self.assertEqual(result["documents"][0]["drift"]["status"], "unknown")

    def test_proposed_is_warning_not_accepted(self):
        self.write_doc("docs/implementation.md", "sample-implementation", "detailed-design",
                       depends=["sample-contract"], status="proposed")
        registered, features = self.load()
        ctx = docs.context(self.root, registered, features, "sample", "I4")
        self.assertTrue(any("not an accepted" in w for w in ctx["warnings"]))

    def test_legacy_unregistered_and_missing_id_fails(self):
        (self.root / "docs/legacy.md").write_text("# Old document\n")
        registered, features = self.load()
        self.assertNotIn("legacy", registered)
        self.write_manifest()
        path = self.root / "docs/features/sample/manifest.yaml"
        path.write_text(path.read_text().replace("sample-contract]", "missing-contract]"))
        with self.assertRaises(docs.KnowledgeError):
            self.load()

    def test_duplicate_ids_rejected(self):
        self.write_doc("docs/duplicate.md", "sample-contract", "contract")
        with self.assertRaisesRegex(docs.KnowledgeError, "duplicate"):
            self.load()

    def test_cycle_rejected(self):
        self.write_doc("docs/contract.md", "sample-contract", "contract",
                       depends=["sample-implementation"])
        with self.assertRaisesRegex(docs.KnowledgeError, "cycle"):
            self.load()

    def test_unknown_dependency_rejected(self):
        self.write_doc("docs/contract.md", "sample-contract", "contract", depends=["missing"])
        with self.assertRaisesRegex(docs.KnowledgeError, "unknown document ID"):
            self.load()

    def test_numeric_only_sha_requires_quotes(self):
        raw = "1" * 40
        self.assertIsInstance(docs.yaml_subset("baseline_commit: " + raw + "\n")["baseline_commit"], int)
        self.assertEqual(docs.yaml_subset('baseline_commit: "' + raw + '"\n')["baseline_commit"], raw)

    def test_unsafe_yaml_constructs_and_duplicates(self):
        for text in ("id: one\nid: two\n", "value: !!python/object/x\n",
                     "value: &anchor value\n", "outer:\n    invalid: indentation\n"):
            with self.subTest(text=text), self.assertRaises(docs.KnowledgeError):
                docs.yaml_subset(text)

    def test_traversal_and_path_validation(self):
        for path in ("../secrets", "native/../passwd", "/tmp/secrets", "a//b", "a b"):
            with self.subTest(path=path), self.assertRaises(docs.KnowledgeError):
                docs.safe_relative(path)
        self.assertEqual(docs.safe_relative("native/core/**", allow_glob=True),
                         "native/core/**")

    def test_symlink_escape(self):
        (self.root / "native/app.rs").unlink()
        (self.root / "native/app.rs").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(docs.KnowledgeError, "escapes repository"):
            self.load()

    def test_source_reverse_matching(self):
        self.assertTrue(docs.source_hit("native/**", "native/app.rs"))
        self.assertFalse(docs.source_hit("native/**", "android/app.kt"))

    def test_drift_clean_and_changed(self):
        self.write_doc("docs/contract.md", "sample-contract", "contract")
        # Fix baseline to the current committed ancestor in memory without
        # changing the committed HEAD (metadata after baseline is intentionally dirty).
        original = docs.metadata(self.root / "docs/contract.md")
        original["baseline_commit"] = self.baseline
        self.assertEqual(docs.drift(self.root, original)["status"], "clean")
        (self.root / "native/app.rs").write_text("fn main() { println!(\"x\"); }\n")
        self.git("add", "native/app.rs")
        self.git("commit", "-qm", "change")
        report = docs.drift(self.root, original)
        self.assertEqual(report["status"], "check_required")
        self.assertIn("native/app.rs", report["changed_paths"])

    def test_drift_invalid_base_is_unknown(self):
        self.assertEqual(docs.drift(self.root, {"baseline_commit": "e" * 40,
                                                 "related_sources": ["native/**"]})["status"], "unknown")

    def test_context_bounds_no_silent_truncation(self):
        registered, features = self.load()
        with self.assertRaisesRegex(docs.KnowledgeError, "exceeds bounds"):
            docs.context(self.root, registered, features, "sample", "I4", max_docs=1)

    def test_schema_unknown_field_and_invalid_enum(self):
        schema = json.loads((self.root / "docs/knowledge/schemas/document-metadata.schema.json").read_text())
        data = docs.metadata(self.root / "docs/contract.md")
        data["unsafe_field"] = 1
        with self.assertRaisesRegex(docs.KnowledgeError, "unknown field"):
            docs.schema_check(data, schema, schema)
        del data["unsafe_field"]
        data["design_status"] = "verified"
        with self.assertRaisesRegex(docs.KnowledgeError, "invalid enum"):
            docs.schema_check(data, schema, schema)

    def test_authority_overlap_rejected(self):
        x = self.root / "docs/contract.md"
        y = self.root / "docs/implementation.md"
        for path in (x, y):
            path.write_text(path.read_text().replace("---\n# ", "authority_keys: [shared-api]\n---\n# "))
        with self.assertRaisesRegex(docs.KnowledgeError, "authority conflict"):
            self.load()



class LinuxDesktopV3RegistryTests(unittest.TestCase):
    """D5 smoke tests against actual tracked docs, not generated fixture authority."""

    @classmethod
    def setUpClass(cls):
        cls.registry, cls.features = docs.docs_and_manifests(REPO)

    def test_registered_manifest_and_real_document_identities(self):
        self.assertIn("linux-desktop-v3", self.features)
        expected = {
            "b1-contract", "b1-presentation", "b1-storage-identity",
            "b2-bundle", "c1-profile", "c2-helper", "e1-storage",
            "integration-plan", "i2", "i4",
        }
        actual = {
            docid.removeprefix("linux-desktop-v3-")
            for docid, (_, meta) in self.registry.items()
            if meta["feature"] == "linux-desktop-v3"
        }
        self.assertEqual(actual, expected)
        for docid in ("linux-desktop-v3-i2", "linux-desktop-v3-i4"):
            self.assertEqual(self.registry[docid][1]["design_status"], "proposed")
        for docid in ("linux-desktop-v3-b2-bundle",
                      "linux-desktop-v3-c1-profile",
                      "linux-desktop-v3-c2-helper",
                      "linux-desktop-v3-e1-storage"):
            self.assertEqual(self.registry[docid][1]["type"], "historical")
            self.assertNotEqual(self.registry[docid][1].get("design_status"), "accepted")

    def test_every_registered_task_resolves_under_default_context_bounds(self):
        tasks = self.features["linux-desktop-v3"][1]["tasks"]
        self.assertEqual(set(tasks), {"B1", "B2", "C1", "C2", "E1", "I1", "I2", "I4"})
        for task in sorted(tasks):
            with self.subTest(task=task):
                ctx = docs.context(REPO, self.registry, self.features,
                                   "linux-desktop-v3", task)
                ids = [item["id"] for item in ctx["documents"]]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertIn(tasks[task]["document"], ids)
                self.assertLessEqual(ctx["total_bytes"], docs.MAX_BYTES)
                self.assertLessEqual(len(ids), docs.MAX_DOCS)
                self.assertEqual(ctx["feature"], "linux-desktop-v3")

    def test_i2_i4_reading_sets_do_not_claim_unmerged_implementation(self):
        i2 = docs.context(REPO, self.registry, self.features,
                          "linux-desktop-v3", "I2")
        i4 = docs.context(REPO, self.registry, self.features,
                          "linux-desktop-v3", "I4")
        i2ids = {item["id"] for item in i2["documents"]}
        i4ids = {item["id"] for item in i4["documents"]}
        self.assertIn("linux-desktop-v3-i2", i2ids)
        self.assertIn("linux-desktop-v3-c2-helper", i2ids)
        self.assertIn("linux-desktop-v3-i4", i4ids)
        self.assertIn("linux-desktop-v3-b1-presentation", i4ids)
        self.assertNotIn("linux-desktop-v3-i2", i4ids)
        self.assertTrue(i2["warnings"] and i4["warnings"])
        index = (REPO / "docs/features/linux-desktop-v3/README.md").read_text()
        self.assertIn("first run", index)
        self.assertIn("task I2", index.replace("--task I2", "task I2"))

    def test_source_reverse_lookup_and_unregistered_task_fail_closed(self):
        answer = docs.affected(self.registry, self.features,
                               "native/core/src/runtime.rs")
        self.assertIn("linux-desktop-v3", answer["features"])
        self.assertIn("linux-desktop-v3-i4",
                      {item["document_id"] for item in answer["documents"]})
        with self.assertRaisesRegex(docs.KnowledgeError, "unregistered task"):
            docs.context(REPO, self.registry, self.features, "linux-desktop-v3", "I3")



class AdditionalFeatureRegistryTests(unittest.TestCase):
    """D6 original-file registration, task projection and bounded contexts."""

    @classmethod
    def setUpClass(cls):
        cls.registered, cls.manifests = docs.docs_and_manifests(REPO)

    def test_curated_features_and_proposed_status_are_explicit(self):
        expected = {"curved-workspace": 10, "spatial-anchor": 1,
                    "android-ime": 6, "initial-phases": 9}
        for feature, count in expected.items():
            with self.subTest(feature=feature):
                self.assertIn(feature, self.manifests)
                items = [(key, data) for key, (_, data) in self.registered.items()
                         if data["feature"] == feature]
                self.assertEqual(len(items), count)
                for key, meta in items:
                    if meta["type"] in ("contract", "architecture", "detailed-design"):
                        self.assertEqual(meta.get("design_status"), "proposed", key)
                    self.assertNotEqual(meta.get("design_status"), "accepted", key)

    def test_curved_cw00_to_cw25_are_exact_reading_pointers(self):
        manifest = self.manifests["curved-workspace"][1]
        expected = {f"CW{n:02}" for n in range(26)}
        self.assertEqual(set(manifest["tasks"]), expected)
        task_text = (REPO / "docs/curved-workspace/08-work-items.md").read_text()
        for task in sorted(expected):
            with self.subTest(task=task):
                self.assertIn(f"### {task} ", task_text)
                self.assertIn(manifest["tasks"][task]["document"], self.registered)
        for fixture in ("geometry-v1.json", "wire-v1.json", "lifecycle-v1.json"):
            with self.subTest(fixture=fixture):
                path = REPO / "docs/curved-workspace/fixtures" / fixture
                self.assertIsInstance(json.loads(path.read_text()), dict)

    def test_initial_phase_manifest_uses_tasks_json_without_mutation(self):
        spec = json.loads((REPO / "docs/design/tasks.json").read_text())
        manifest = self.manifests["initial-phases"][1]
        task_ids = {task["id"] for task in spec["tasks"]}
        self.assertEqual(len(task_ids), 31)
        self.assertEqual(set(manifest["tasks"]),
                         task_ids | {f"TH0{n}" for n in range(6)})
        for task in spec["tasks"]:
            with self.subTest(task=task["id"]):
                record = manifest["tasks"][task["id"]]
                path = self.registered[record["document"]][0]
                self.assertEqual(path.relative_to(REPO).as_posix(),
                                 task["spec"].split("#", 1)[0])
                # Runtime acceptance status is owned by tasks.json, not the index.
                self.assertIn(task["status"],
                              {"planned", "in_progress", "blocked", "done"})
        self.assertIn("docs/design/tasks.json", manifest["source_paths"])

    def test_d6_task_contexts_fit_fixed_limits_without_truncation(self):
        feature_sizes = {"curved-workspace": 26, "spatial-anchor": 1,
                         "android-ime": 6, "initial-phases": 37}
        for feature, count in feature_sizes.items():
            manifest = self.manifests[feature][1]
            self.assertEqual(len(manifest["tasks"]), count)
            for task, record in sorted(manifest["tasks"].items()):
                with self.subTest(feature=feature, task=task):
                    selected = docs.closure(self.registered,
                                            manifest["contracts"]
                                            + record["requires"]
                                            + record.get("test_plans", [])
                                            + [record["document"]])
                    total = sum(self.registered[key][0].stat().st_size
                                for key in selected)
                    self.assertLessEqual(len(selected), docs.MAX_DOCS)
                    self.assertLessEqual(total, docs.MAX_BYTES)
                    self.assertIn(record["document"], selected)
        for feature, task in (("curved-workspace", "CW25"),
                              ("spatial-anchor", "SA0"),
                              ("android-ime", "NX09"),
                              ("initial-phases", "W03")):
            with self.subTest(feature=feature):
                result = docs.context(REPO, self.registered, self.manifests,
                                      feature, task)
                self.assertEqual(result["task"], task)
                self.assertLessEqual(result["total_bytes"], docs.MAX_BYTES)

    def test_source_reverse_lookup_and_unregistered_tasks_fail_closed(self):
        overlap = docs.affected(self.registered, self.manifests,
                                "native/core/src/runtime.rs")
        self.assertTrue({"curved-workspace", "initial-phases",
                         "linux-desktop-v3"} <= set(overlap["features"]))
        self.assertIn("android-ime",
                      docs.affected(self.registered, self.manifests,
                                    "native/core/src/compositor/ime.rs")["features"])
        self.assertIn("spatial-anchor",
                      docs.affected(self.registered, self.manifests,
                                    "native/core/src/workspace.rs")["features"])
        with self.assertRaisesRegex(docs.KnowledgeError, "unregistered task"):
            docs.context(REPO, self.registered, self.manifests,
                         "spatial-anchor", "SA1")


if __name__ == "__main__":
    unittest.main()
