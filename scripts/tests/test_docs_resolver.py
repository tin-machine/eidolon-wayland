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
                f"baseline_commit: {'1' * 40}\n"
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


if __name__ == "__main__":
    unittest.main()
