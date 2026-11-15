"""Regression tests for evidence freshness, environment boundaries and bounded slices."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("eidolon_structure", REPO / "scripts/structure.py")
structure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(structure)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("core.rs", "fn stop() {\n    gate();\n}\nfn gate() {}\n")
        self.write("Host.kt", "fun bridge() {}\n")
        self.write("test.rs", "fn refuses_foreign_owner() {}\n")
        self.write("effects/Scope.kt", "fun allocate() { check(ticket.valid()) }\n")
        self.commit()
        self.baseline = self.git("rev-parse", "HEAD").strip()
        self.registry = {
            "schema": 1, "scope": "fixture", "reviewed_revision": self.baseline,
            "domains": ["native", "adapter"],
            "profiles": {"host-model": {"domains": ["native"]},
                         "android-product": {"domains": ["native", "adapter"]}},
            "files": {"core": "core.rs", "adapter": "Host.kt", "test": "test.rs"},
            "nodes": [
                {"id": "stop", "file": "core", "anchor": "fn stop() {", "domain": "native", "role": "source"},
                {"id": "gate", "file": "core", "anchor": "fn gate() {}", "domain": "native", "role": "source"},
                {"id": "bridge", "file": "adapter", "anchor": "fun bridge() {}", "domain": "adapter", "role": "source"},
                {"id": "test", "file": "test", "anchor": "fn refuses_foreign_owner() {}", "domain": "native", "role": "test"},
            ],
            "edges": [
                {"id": "guard", "from": "stop", "to": "gate", "relation": "requires", "invariant": "same owner",
                 "evidence": ["stop", "gate"], "tests": ["test"]},
                {"id": "jni", "from": "bridge", "to": "stop", "relation": "bridges", "invariant": "same handle",
                 "evidence": ["bridge", "stop"], "tests": ["test"]},
            ],
            "unknowns": [{"id": "device", "detail": "SDK not executed"}],
            "watch": [{"id": "effect-sites", "root": "effects", "suffixes": [".kt"],
                       "reason": "SDK callers outside semantic graph"}],
        }
        self.save_registry()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def save_registry(self):
        self.write(structure.REGISTRY, json.dumps(self.registry))

    def build(self, profile="android-product"):
        return structure.build(self.root, profile)

    def test_deterministic_and_test_reference_never_becomes_test_pass(self):
        before = self.git("status", "--porcelain")
        first, second = self.build(), self.build()
        self.assertEqual(structure.encoded(first), structure.encoded(second))
        self.assertEqual(first["tests_execution"], "not_run_by_this_tool")
        self.assertEqual(before, self.git("status", "--porcelain"))
        self.assertEqual(first["edges"][0]["provenance"], "manual_source_review")

    def test_host_profile_excludes_android_bridge_and_keeps_boundary_visible(self):
        host, android = self.build("host-model"), self.build()
        self.assertEqual(host["coverage"]["excluded_edges"], ["jni"])
        self.assertNotIn("bridge", [n["id"] for n in host["nodes"]])
        self.assertIn("bridge", [n["id"] for n in android["nodes"]])
        self.assertNotEqual(host["artifact_sha256"], android["artifact_sha256"])
        self.assertEqual(host["unknowns"], android["unknowns"])

    def test_dirty_scoped_source_is_rejected(self):
        self.write("core.rs", "fn stop() {\n    bypass();\n}\nfn gate() {}\n")
        with self.assertRaisesRegex(structure.StructureError, "uncommitted scoped source"):
            self.build()

    def test_changed_body_with_same_symbol_demands_new_review(self):
        self.write("core.rs", "fn stop() {\n    bypass();\n}\nfn gate() {}\n")
        self.commit()
        graph = self.build()
        self.assertEqual(graph["status"], "needs_review")
        self.assertEqual(graph["nodes"][2]["resolution"], "unique_anchor")
        self.assertTrue(all(e["status"] == "needs_review" for e in graph["edges"]))
        with self.assertRaisesRegex(structure.StructureError, "needs review"):
            structure.bounded_slice(graph, "stop")

    def test_changed_test_body_also_invalidates_relation(self):
        self.write("test.rs", "fn refuses_foreign_owner() {}\n// assertion removed\n")
        self.commit()
        self.assertTrue(all(e["status"] == "needs_review" for e in self.build()["edges"]))

    def test_missing_anchor_and_ambiguous_anchor_are_not_resolved_by_guessing(self):
        for source, expected in [("fn gate() {}\n", "missing"), ("fn stop() {\nfn stop() {\nfn gate() {}\n", "ambiguous")]:
            with self.subTest(expected=expected):
                self.write("core.rs", source)
                self.commit()
                node = next(n for n in self.build()["nodes"] if n["id"] == "stop")
                self.assertEqual(node["resolution"], expected)

    def test_registry_change_cannot_reuse_cached_artifact(self):
        old = self.build()
        self.registry["edges"][0]["invariant"] = "new condition"
        self.save_registry()
        with self.assertRaisesRegex(structure.StructureError, "stale or modified"):
            structure.verify(old, self.build())

    def test_new_head_invalidates_artifact_even_when_scoped_files_unchanged(self):
        old = self.build()
        self.write("unrelated.md", "new head\n")
        self.commit()
        current = self.build()
        self.assertEqual(current["status"], "ok")
        with self.assertRaises(structure.StructureError):
            structure.verify(old, current)

    def test_profile_and_generator_and_payload_tampering_rejected(self):
        current = self.build()
        for key in ("revision", "generator_sha256", "artifact_sha256"):
            with self.subTest(key=key):
                changed = copy.deepcopy(current)
                changed[key] = "changed"
                with self.assertRaises(structure.StructureError):
                    structure.verify(changed, current)
        with self.assertRaises(structure.StructureError):
            structure.verify(current, self.build("host-model"))
        changed = copy.deepcopy(current)
        changed["nodes"][0]["lines"] = [999]
        with self.assertRaises(structure.StructureError):
            structure.verify(changed, current)

    def test_slice_keeps_source_test_evidence_closed_and_honors_budgets(self):
        graph = self.build()
        result = structure.bounded_slice(graph, "stop", max_edges=1, max_nodes=3, max_bytes=4000)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(len(result["omitted_edges"]), 1)
        self.assertLessEqual(len(result["nodes"]), 3)
        self.assertLessEqual(len(structure.encoded(result)), 4000)
        ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            self.assertTrue(set(edge["evidence"] + edge["tests"]) <= ids)

    def test_too_small_budget_and_bad_hops_fail_explicitly(self):
        for args in ({"max_bytes": 512}, {"hops": -1}, {"hops": 4}, {"max_nodes": 0}):
            with self.subTest(args=args), self.assertRaises(structure.StructureError):
                structure.bounded_slice(self.build(), "stop", **args)

    def test_zero_edge_budget_reports_omission(self):
        result = structure.bounded_slice(self.build(), "stop", max_edges=0)
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["omitted_edges"], ["guard", "jni"])
        self.assertFalse(result["complete"])

    def test_profile_excluded_node_is_not_available_for_slice(self):
        with self.assertRaisesRegex(structure.StructureError, "profile-excluded"):
            structure.bounded_slice(self.build("host-model"), "bridge")

    def test_duplicate_node_and_missing_source_test_evidence_fail(self):
        initial = copy.deepcopy(self.registry)
        changes = [lambda r: r["nodes"].append(r["nodes"][0]),
                   lambda r: r["edges"][0].update(to="missing"),
                   lambda r: r["edges"][0].update(tests=[]),
                   lambda r: r["edges"][0].update(tests=["stop"]),
                   lambda r: r["edges"][0].update(evidence=["stop"])]
        for change in changes:
            self.registry = copy.deepcopy(initial)
            change(self.registry)
            self.save_registry()
            with self.assertRaises(structure.StructureError):
                self.build()

    def test_symbolic_review_ref_and_unknown_profile_fail(self):
        with self.assertRaisesRegex(structure.StructureError, "unknown environment"):
            self.build("production")
        self.registry["reviewed_revision"] = "HEAD"
        self.save_registry()
        with self.assertRaisesRegex(structure.StructureError, "exact SHA"):
            self.build()

    def test_source_symlink_rejected_even_when_content_matches(self):
        original = self.root / "core.rs"
        target = self.root / "copy.rs"
        target.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(target)
        with self.assertRaisesRegex(structure.StructureError, "symlink"):
            self.build()

    def test_cli_unknown_profile_is_nonzero_and_has_no_artifact(self):
        result = subprocess.run(["python3", str(REPO / "scripts/structure.py"), "--root", str(self.root),
                                 "build", "--profile", "typo"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(json.loads(result.stderr)["status"], "error")

    def test_watch_only_change_blocks_reuse_and_slice_without_inventing_edges(self):
        old = self.build()
        self.write("effects/Scope.kt", "fun allocate() {}\n")
        current = self.build()
        self.assertEqual(current["status"], "needs_review")
        self.assertEqual(current["nodes"], old["nodes"])
        self.assertEqual(current["edges"], old["edges"])
        self.assertEqual(current["watch"]["changes"], [
            {"path": "effects/Scope.kt", "stage": "head_to_worktree", "change": "modified"}])
        with self.assertRaises(structure.StructureError):
            structure.verify(old, current)
        with self.assertRaises(structure.StructureError):
            structure.bounded_slice(current, "stop")

    def test_committed_watch_only_change_requires_baseline_review(self):
        self.write("effects/Scope.kt", "fun allocate() {}\n")
        self.commit()
        current = self.build()
        self.assertEqual(current["status"], "needs_review")
        self.assertEqual(current["watch"]["changes"][0]["stage"], "reviewed_to_head")
        self.assertEqual(self.build("host-model")["status"], "needs_review")

    def test_new_ignored_caller_is_detected_and_cannot_reuse_artifact(self):
        old = self.build()
        self.write(".gitignore", "effects/new/\n")
        self.write("effects/new/Caller.kt", "fun makeAnotherEntity() {}\n")
        self.assertNotIn("Caller.kt", self.git("status", "--porcelain", "--untracked-files=all"))
        current = self.build()
        self.assertEqual(current["watch"]["changes"], [
            {"path": "effects/new/Caller.kt", "stage": "head_to_worktree", "change": "added"}])
        with self.assertRaises(structure.StructureError):
            structure.verify(old, current)

    def test_deleted_entire_watch_directory_is_detected(self):
        (self.root / "effects/Scope.kt").unlink()
        (self.root / "effects").rmdir()
        current = self.build()
        self.assertEqual(current["watch"]["changes"], [
            {"path": "effects/Scope.kt", "stage": "head_to_worktree", "change": "deleted"}])

    def test_committed_rename_is_reported_as_delete_and_add(self):
        (self.root / "effects/Scope.kt").rename(self.root / "effects/Scope2.kt")
        self.commit()
        changes = self.build()["watch"]["changes"]
        self.assertEqual([(c["path"], c["change"]) for c in changes],
                         [("effects/Scope.kt", "deleted"), ("effects/Scope2.kt", "added")])
        self.assertTrue(all(c["stage"] == "reviewed_to_head" for c in changes))

    def test_change_outside_declared_watch_and_semantic_files_is_explicitly_out_of_scope(self):
        old = self.build()
        self.write("other/Unwatched.kt", "fun caller() {}\n")
        self.write("effects/README.md", "not source\n")
        self.assertEqual(self.build(), old)
        self.assertEqual(old["coverage"]["watch_only_files"], 1)

    def test_watch_cannot_escape_repository_or_silently_miss_typo(self):
        initial = copy.deepcopy(self.registry)
        for root in ("../effects", "/effects", "effects/missing", ".", ":(glob)*"):
            with self.subTest(root=root):
                self.registry = copy.deepcopy(initial)
                self.registry["watch"][0]["root"] = root
                self.save_registry()
                with self.assertRaises(structure.StructureError):
                    self.build()

    def test_symlink_directory_and_watched_file_are_rejected(self):
        linked = self.root / "effects/linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(structure.StructureError, "symlink"):
            self.build()
        linked.unlink()
        original = self.root / "effects/Scope.kt"
        original.unlink()
        original.symlink_to(self.root / "Host.kt")
        with self.assertRaisesRegex(structure.StructureError, "symlink"):
            self.build()

    def test_new_caller_during_extraction_rejected(self):
        original = structure.watch_sources
        calls = 0

        def changing(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.write("effects/Late.kt", "fun anotherCaller() {}\n")
            return original(*args)

        with patch.object(structure, "watch_sources", side_effect=changing):
            with self.assertRaisesRegex(structure.StructureError, "watched source changed during extraction"):
                self.build()


if __name__ == "__main__":
    unittest.main()
