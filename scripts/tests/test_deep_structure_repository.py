"""Independent Git/provenance/slice fixtures for the on-demand deep analyzer."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from deep_structure import repository as deep


class RepositoryDeepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Deep fixture")
        self.git("config", "user.email", "deep@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.filemode", "true")
        self.write(".gitignore", ".cache/\nignored.py\n")
        self.write("app/main.py", """raise RuntimeError('target code must never execute')

def helper(value):
    return value + 1

def choose(flag, original):
    value = original
    if flag:
        value = helper(original)
    return value

def cleanup(resource):
    try:
        return helper(resource)
    finally:
        resource.close()

def conditional(flag):
    if flag:
        result = 1
    return result
""")
        self.write("tests/test_main.py", "import main\nPATH = 'app/main.py'\ndef test_choice():\n    return main.choose(True, 1)\n")
        self.write("arbitrary/newroot/tool", "#!/usr/bin/env -S python3 -B\ndef run():\n    return 1\n")
        self.write("native/core.rs", "fn run() {}\n")
        self.write("android/Runtime.kt", "fun run() {}\n")
        self.write("native/ffi.c", "int run(void) { return 0; }\n")
        self.write("vendor/dependency.py", "def external():\n    return 1\n")
        self.write("generated/message_pb2.py", "def generated():\n    return 2\n")
        self.write("tests/fixtures/value.py", "def fixture():\n    return 3\n")
        self.write("broken.py", "def invalid(:\n")
        self.write("README.md", "fixture\n")
        self.write("data.json", "{}\n")
        self.commit()

    def git(self, *args, root=None):
        return subprocess.check_output(["git", "-C", str(root or self.root), *args],
                                       stderr=subprocess.PIPE, text=True)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def build(self, excludes=()):
        return deep.build(self.root, excludes)

    def function(self, graph, name):
        return next(f for f in graph["functions"] if f["qualname"] == name)

    def verify_rejection(self, artifact):
        with self.assertRaises(deep.DeepError):
            deep.verify(self.root, artifact)

    def test_discovery_has_independent_role_language_and_status_axes(self):
        graph = self.build()
        files = {f["path"]: f for f in graph["files"]}
        self.assertEqual(files["app/main.py"]["role"], "production_candidate")
        self.assertEqual(files["app/main.py"]["analysis_status"], "analyzed")
        self.assertEqual(files["tests/test_main.py"]["role"], "test_fixture")
        self.assertEqual(files["tests/test_main.py"]["analysis_status"], "analyzed")
        self.assertEqual(files["tests/fixtures/value.py"]["role"], "test_fixture")
        for path in ("vendor/dependency.py", "generated/message_pb2.py"):
            self.assertEqual(files[path]["role"], "generated_vendor")
            self.assertEqual(files[path]["analysis_status"], "excluded")
            self.assertTrue(files[path]["decision_reason"])
        for path, language in (("native/core.rs", "rust"), ("android/Runtime.kt", "kotlin"), ("native/ffi.c", "c")):
            self.assertEqual(files[path]["language"], language)
            self.assertEqual(files[path]["analysis_status"], "unsupported")
        self.assertEqual(files["README.md"]["analysis_status"], "not_source")
        self.assertEqual(files["arbitrary/newroot/tool"]["language"], "python")
        self.assertEqual(files["arbitrary/newroot/tool"]["selection_reason"], "interpreter shebang")
        self.assertEqual(graph["summary"]["tracked_files"], len(files))
        self.assertEqual(graph["summary"]["source_candidates"], sum(f["language"] is not None for f in files.values()))
        self.assertEqual(graph["tests_execution"], "not_run_by_this_tool")
        self.assertEqual(graph["contract_graph"], "not_read_or_modified")

    def test_new_committed_source_is_discovered_without_registry_update(self):
        before = self.build()
        self.write("previously/unknown/location.py", "def new():\n    return 12\n")
        self.commit()
        after = self.build()
        self.assertEqual(after["summary"]["source_candidates"], before["summary"]["source_candidates"] + 1)
        self.assertTrue(any(f["path"] == "previously/unknown/location.py" for f in after["functions"]))
        self.verify_rejection(before)

    def test_explicit_exclusion_reason_and_profile_are_deterministic(self):
        first = self.build(("app/*", "tests/fixtures/*"))
        second = self.build(("tests/fixtures/*", "app/*", "app/*"))
        self.assertEqual(deep.encoded(first), deep.encoded(second))
        row = next(f for f in first["files"] if f["path"] == "app/main.py")
        self.assertEqual(row["role"], "production_candidate")
        self.assertEqual(row["analysis_status"], "excluded")
        self.assertEqual(row["exclusion_rule"], "app/*")
        self.assertFalse(any(f["path"] == "app/main.py" for f in first["functions"]))
        with self.assertRaisesRegex(deep.DeepError, "profile"):
            deep.verify(self.root, first)
        self.assertEqual(deep.verify(self.root, first, ("app/*", "tests/fixtures/*")), first)

    def test_parse_failure_is_distinct_from_supported_and_unsupported_functions(self):
        graph = self.build()
        row = next(f for f in graph["files"] if f["path"] == "broken.py")
        self.assertEqual(row["analysis_status"], "parse_failed")
        self.assertEqual(row["diagnostics"][0]["kind"], "SyntaxError")
        self.assertFalse(any(f["path"] == "broken.py" for f in graph["functions"]))
        self.assertEqual(self.function(graph, "cleanup")["cfg"]["status"], "unsupported")
        self.assertEqual(self.function(graph, "choose")["cfg"]["status"], "supported")
        self.assertEqual(graph["summary"]["source_by_status"]["parse_failed"], 1)
        self.assertGreater(graph["summary"]["cfg_dfg_unsupported_functions"], 0)

    def test_same_input_is_byte_identical_across_relocated_checkout(self):
        first, second = self.build(), self.build()
        self.assertEqual(deep.encoded(first), deep.encoded(second))
        clone = self.directory / "clone"
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(self.root), str(clone)], check=True)
        self.assertEqual(deep.encoded(first), deep.encoded(deep.build(clone)))
        self.assertNotIn(str(self.root), deep.encoded(first).decode())

    def test_untracked_and_ignored_sources_are_explicitly_outside_committed_scope(self):
        artifact = self.build()
        self.write("not_committed.py", "raise RuntimeError('must not run')\n")
        self.write("ignored.py", "this is not valid Python\n")
        self.assertEqual(self.build(), artifact)
        self.assertIn("outside committed analysis scope", artifact["snapshot"]["untracked_ignored"])

    def test_provenance_returns_to_exact_function_source(self):
        graph = self.build()
        function = self.function(graph, "choose")
        source = function["source"]
        self.assertEqual(source["revision"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(source["sha256"], deep.digest((self.root / source["path"]).read_bytes()))
        self.assertEqual(source["git_blob"], self.git("rev-parse", "HEAD:" + source["path"]).strip())
        line = (self.root / source["path"]).read_text().splitlines()[function["location"]["line"] - 1]
        self.assertTrue(line.startswith("def choose("))

    def test_dirty_tracked_source_and_non_source_refused(self):
        artifact = self.build()
        for name in ("app/main.py", "README.md"):
            with self.subTest(name=name):
                original = (self.root / name).read_bytes()
                (self.root / name).write_bytes(original + b"\n# changed\n")
                self.verify_rejection(artifact)
                (self.root / name).write_bytes(original)

    def test_assume_unchanged_cannot_hide_modified_source(self):
        artifact = self.build()
        self.git("update-index", "--assume-unchanged", "app/main.py")
        with (self.root / "app/main.py").open("a") as stream:
            stream.write("\n# hidden from ordinary git diff\n")
        self.assertEqual(self.git("diff", "--name-only"), "")
        self.verify_rejection(artifact)

    def test_staged_changes_refused_even_when_worktree_restored_to_head(self):
        artifact = self.build()
        original = (self.root / "README.md").read_bytes()
        self.write("README.md", "staged\n")
        self.git("add", "README.md")
        (self.root / "README.md").write_bytes(original)
        with self.assertRaisesRegex(deep.DeepError, "index differs"):
            deep.verify(self.root, artifact)

    def test_new_head_invalidates_even_when_python_sources_unchanged(self):
        artifact = self.build()
        self.write("README.md", "new documentation commit\n")
        self.commit()
        with self.assertRaisesRegex(deep.DeepError, "stale or modified"):
            deep.verify(self.root, artifact)

    def test_deleted_and_renamed_source_fail_without_partial_success(self):
        artifact = self.build()
        path = self.root / "app/main.py"
        renamed = self.root / "app/renamed.py"
        path.rename(renamed)
        self.verify_rejection(artifact)
        self.git("add", "-A")
        self.verify_rejection(artifact)

    def test_executable_mode_change_detected_when_git_filemode_disabled(self):
        artifact = self.build()
        self.git("config", "core.filemode", "false")
        path = self.root / "app/main.py"
        path.chmod(path.stat().st_mode | 0o111)
        with self.assertRaisesRegex(deep.DeepError, "mode changed"):
            deep.verify(self.root, artifact)

    def test_tracked_symlink_is_recorded_but_never_followed(self):
        outside = self.directory / "outside.py"
        outside.write_text("not even Python\n")
        (self.root / "linked.py").symlink_to(outside)
        self.commit()
        first = self.build()
        row = next(f for f in first["files"] if f["path"] == "linked.py")
        self.assertEqual(row["analysis_status"], "excluded")
        self.assertEqual(row["decision_reason"], "symlink; not followed")
        self.assertFalse(any(f["path"] == "linked.py" for f in first["functions"]))
        outside.unlink()
        self.assertEqual(self.build(), first)

    def test_symlink_parent_cannot_redirect_tracked_source_reads(self):
        artifact = self.build()
        moved = self.directory / "moved-app"
        (self.root / "app").rename(moved)
        (self.root / "app").symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(deep.DeepError, "symlink"):
            deep.verify(self.root, artifact)

    def test_source_change_during_extraction_is_refused(self):
        original = deep.analyze_python
        changed = False

        def mutate(path, source):
            nonlocal changed
            result = original(path, source)
            if not changed:
                self.write("README.md", "changed during extraction\n")
                changed = True
            return result

        with patch.object(deep, "analyze_python", side_effect=mutate):
            with self.assertRaisesRegex(deep.DeepError, "differs from HEAD"):
                self.build()

    def test_generator_changed_after_loading_cannot_claim_new_identity(self):
        identity = copy.deepcopy(deep.LOADED_GENERATOR)
        identity["id"] = "changed-loaded-code"
        with patch.object(deep, "_generator", return_value=identity):
            with self.assertRaisesRegex(deep.DeepError, "generator changed after import"):
                self.build()

    def test_artifact_tamper_detected_even_after_recomputing_checksum(self):
        artifact = self.build()
        changed = copy.deepcopy(artifact)
        changed["functions"][0]["location"]["line"] = 999
        self.verify_rejection(changed)
        changed["artifact_sha256"] = deep.digest(deep.encoded({k: v for k, v in changed.items() if k != "artifact_sha256"}))
        self.verify_rejection(changed)

    def test_generator_parser_profile_and_source_metadata_tampering_rejected(self):
        artifact = self.build()
        for field in ("generator_sha256", "profile_sha256", "snapshot", "summary"):
            with self.subTest(field=field):
                changed = copy.deepcopy(artifact)
                changed[field] = "altered"
                changed["artifact_sha256"] = deep.digest(deep.encoded({k: v for k, v in changed.items() if k != "artifact_sha256"}))
                self.verify_rejection(changed)

    def test_corrupt_duplicate_nonfinite_and_nonobject_json_rejected(self):
        path = self.directory / "broken.json"
        for value in (b'{"schema":', b'{"schema":1,"schema":2}', b'{"value":NaN}', b'[]', b'\xff'):
            with self.subTest(value=value):
                path.write_bytes(value)
                with self.assertRaises(deep.DeepError):
                    deep.load_artifact(path)

    def test_artifact_reader_rejects_symlinks_and_byte_limit(self):
        path = self.directory / "artifact.json"
        path.write_text('{"kind":"fixture"}')
        link = self.directory / "alias.json"
        link.symlink_to(path)
        with self.assertRaises(deep.DeepError):
            deep.load_artifact(link)
        with patch.object(deep, "MAX_ARTIFACT_BYTES", 4):
            with self.assertRaises(deep.DeepError):
                deep.load_artifact(path)

    def test_test_references_remain_lexical_candidates_not_executed_coverage(self):
        graph = self.build()
        refs = [r for r in graph["related_tests"] if r["test_path"] == "tests/test_main.py"]
        self.assertTrue(refs)
        for ref in refs:
            self.assertIn("app/main.py", ref["candidate_paths"])
            self.assertEqual(ref["resolution"], "lexical_candidate")
            self.assertEqual(ref["tests_execution"], "not_run")
        self.assertTrue(all(h["complexity_classification"] == "unknown" for h in graph["hotspots"]))

    def test_slice_keeps_all_ambiguous_test_reference_candidates_visible(self):
        self.write("other/main.py", "def alternate():\n    return 9\n")
        self.commit()
        graph = self.build()
        result = deep.bounded_slice(graph, "app/main.py::choose")
        references = [node for node in result["nodes"] if node["kind"] == "test_reference_candidate"]
        self.assertTrue(references)
        for reference in references:
            self.assertTrue(reference["ambiguous"])
            self.assertEqual(reference["candidate_paths"], ["app/main.py", "other/main.py"])
            self.assertEqual(reference["tests_execution"], "not_run")

    def test_full_slice_has_cfg_dfg_calls_and_closed_source_endpoints(self):
        graph = deep.verify(self.root, self.build())
        result = deep.bounded_slice(graph, "app/main.py::choose")
        kinds = {e["kind"] for e in result["edges"]}
        self.assertTrue(any(k.startswith("cfg:") for k in kinds))
        self.assertTrue(any(k.startswith("dfg:") for k in kinds))
        self.assertIn("call_candidate", kinds)
        self.assertIn("test_file_reference_candidate", kinds)
        ids = {n["id"] for n in result["nodes"]}
        self.assertTrue(all(e["from"] in ids and e["to"] in ids for e in result["edges"]))
        self.assertTrue(all(n["source"]["revision"] == graph["snapshot"]["revision"] for n in result["nodes"]))
        self.assertTrue(result["complete"])
        self.assertEqual(result["omitted_edges"], 0)
        self.assertEqual(result["pdg"], "not_implemented")

    def test_slice_node_edge_and_byte_budgets_report_omission(self):
        graph = self.build()
        symbol = self.function(graph, "choose")["id"]
        seed = deep.bounded_slice(graph, symbol, max_nodes=1, max_edges=0)
        self.assertEqual([n["id"] for n in seed["nodes"]], [symbol])
        self.assertFalse(seed["complete"])
        self.assertGreater(seed["omitted_nodes"], 0)
        self.assertGreater(seed["omitted_edges"], 0)
        limited = deep.bounded_slice(graph, symbol, max_edges=1)
        self.assertLessEqual(len(limited["edges"]), 1)
        self.assertGreater(limited["omitted_edges"], 0)
        budget = len(deep.encoded(seed)) + 700
        bounded = deep.bounded_slice(graph, symbol, max_bytes=budget)
        self.assertLessEqual(len(deep.encoded(bounded)), budget)
        self.assertFalse(bounded["complete"])
        ids = {n["id"] for n in bounded["nodes"]}
        self.assertTrue(all(e["from"] in ids and e["to"] in ids for e in bounded["edges"]))

    def test_slice_invalid_budgets_and_too_small_metadata_fail(self):
        graph = self.build()
        for options in ({"hops": -1}, {"hops": 4}, {"max_nodes": 0}, {"max_nodes": 201},
                        {"max_edges": -1}, {"max_edges": 601}, {"max_bytes": 1024}):
            with self.subTest(options=options), self.assertRaises(deep.DeepError):
                deep.bounded_slice(graph, "app/main.py::choose", **options)

    def test_unsupported_target_retains_unknowns_and_no_trusted_cfg(self):
        graph = self.build()
        result = deep.bounded_slice(graph, "app/main.py::cleanup")
        seed = next(n for n in result["nodes"] if n["id"] == result["symbol"])
        self.assertEqual(seed["cfg_status"], "unsupported")
        self.assertTrue(result["unknowns"])
        self.assertFalse(any(e["kind"].startswith(("cfg:", "dfg:")) for e in result["edges"]))
        self.assertEqual(result["call_sites"], self.function(graph, "cleanup")["calls"])

    def test_slice_keeps_possibly_unbound_data_dependency_visible(self):
        graph = self.build()
        result = deep.bounded_slice(graph, "app/main.py::conditional")
        self.assertIn("possibly_unbound_local", deep.encoded(result).decode())

    def test_unknown_and_ambiguous_symbols_are_never_guessed(self):
        graph = self.build()
        with self.assertRaises(deep.DeepError):
            deep.bounded_slice(graph, "choose")
        with (self.root / "app/main.py").open("a") as stream:
            stream.write("\ndef choose():\n    return 7\n")
        self.commit()
        graph = self.build()
        with self.assertRaises(deep.DeepError):
            deep.bounded_slice(graph, "app/main.py::choose")
        options = [f for f in graph["functions"] if f["qualname"] == "choose"]
        self.assertEqual(len(options), 2)
        self.assertEqual(deep.bounded_slice(graph, options[0]["id"])["symbol"], options[0]["id"])

    def test_cache_write_is_repeatable_and_does_not_change_source_or_index(self):
        before = self.git("status", "--porcelain=v1", "--untracked-files=all")
        artifact = self.build()
        path = deep.write_artifact(self.root, artifact)
        self.assertEqual(deep.load_artifact(path), artifact)
        self.assertEqual(deep.write_artifact(self.root, artifact), path)
        self.assertEqual(before, self.git("status", "--porcelain=v1", "--untracked-files=all"))
        self.assertFalse(list(self.root.rglob("__pycache__")))
        self.assertEqual(self.build(), artifact)

    def test_outputs_cannot_overwrite_source_git_metadata_or_unignored_paths(self):
        artifact = self.build()
        original = (self.root / "data.json").read_bytes()
        for output in (self.root / "data.json", self.root / ".git/config.json", self.root / "new.json"):
            with self.subTest(output=output), self.assertRaises(deep.DeepError):
                deep.write_artifact(self.root, artifact, output)
        self.assertEqual((self.root / "data.json").read_bytes(), original)
        ignored = deep.write_artifact(self.root, artifact, self.root / ".cache/result.json")
        self.assertEqual(deep.load_artifact(ignored), artifact)

    def test_output_refuses_nonanalysis_file_symlink_and_symlink_parent(self):
        artifact = self.build()
        other = self.directory / "other.json"
        other.write_text('{"settings":1}')
        link = self.directory / "alias.json"
        link.symlink_to(other)
        folder = self.directory / "alias-dir"
        folder.symlink_to(self.directory, target_is_directory=True)
        for output in (other, link, folder / "result.json"):
            with self.subTest(output=output), self.assertRaises(deep.DeepError):
                deep.write_artifact(self.root, artifact, output)
        self.assertEqual(other.read_text(), '{"settings":1}')

    def test_worktree_cache_is_resolved_and_common_git_metadata_is_protected(self):
        worktree = self.directory / "worktree"
        self.git("worktree", "add", "--detach", "-q", str(worktree), "HEAD")
        artifact = deep.build(worktree)
        path = deep.write_artifact(worktree, artifact)
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_relative_to(worktree))
        self.assertEqual(deep.load_artifact(path), artifact)
        with self.assertRaises(deep.DeepError):
            deep.write_artifact(worktree, artifact, self.root / ".git/unsafe.json")

    def test_cli_build_verify_slice_and_stale_rejection_leave_previous_slice_intact(self):
        command = [sys.executable, "-B", str(REPO / "scripts/deep_structure.py")]

        def run(*args):
            return subprocess.run([*command, *args, "--repo", str(self.root)],
                                  capture_output=True, text=True, timeout=30)

        result = run("repository-deep")
        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = json.loads(result.stdout)["artifact"]
        result = run("verify", "--artifact", artifact)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "fresh")
        result = run("targeted-deep", "--artifact", artifact, "--symbol", "app/main.py::choose")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = Path(json.loads(result.stdout)["artifact"])
        before = output.read_bytes()
        self.write("README.md", "dirty source\n")
        result = run("targeted-deep", "--artifact", artifact, "--symbol", "app/main.py::choose")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "error")
        self.assertEqual(output.read_bytes(), before)

    def test_targeted_cli_refuses_new_source_snapshot_during_slicing(self):
        graph = self.build()
        artifact = deep.write_artifact(self.root, graph)
        output = deep.write_artifact(self.root, deep.bounded_slice(graph, "app/main.py::choose"))
        before = output.read_bytes()
        original = deep.bounded_slice

        def change_source(*args, **kwargs):
            result = original(*args, **kwargs)
            self.write("README.md", "concurrent committed source snapshot\n")
            self.commit()
            return result

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(deep, "bounded_slice", side_effect=change_source), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = deep.main(["targeted-deep", "--repo", str(self.root),
                              "--artifact", str(artifact), "--symbol", "app/main.py::choose"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("snapshot changed during slicing", json.loads(stderr.getvalue())["error"])
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
