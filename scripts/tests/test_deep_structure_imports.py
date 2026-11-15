"""Cross-module candidate/negative fixtures: no target source import or execution."""
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from deep_structure import repository as deep


class ImportCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Deep test")
        self.git("config", "user.email", "test@example.invalid")
        self.write("pkg/__init__.py", "")
        self.write("pkg/service.py", """raise RuntimeError('do not execute')
def finish(x): return x
""")
        self.write("pkg/entry.py", """import service as core
from service import finish as stop
from .service import finish as relative_stop

def run(x):
    core.finish(x)
    stop(x)
    relative_stop(x)
""")
        self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def graph(self):
        return deep.build(self.root)

    @staticmethod
    def fn(graph, path, name):
        return next(f for f in graph["functions"] if f["path"] == path and f["qualname"] == name)

    def test_import_module_alias_from_and_relative_are_candidates_with_evidence(self):
        graph = self.graph()
        calls = self.fn(graph, "pkg/entry.py", "run")["calls"]
        self.assertEqual([c["candidate_kind"] for c in calls], ["repository_import"] * 3)
        self.assertTrue(all(c["target"] == "pkg/service.py::finish@2" for c in calls))
        self.assertEqual([c["import_evidence"]["import_location"]["line"] for c in calls], [1, 2, 3])
        self.assertEqual([c["location"]["line"] for c in calls], [6, 7, 8])
        for c in calls:
            evidence = c["import_evidence"]
            self.assertEqual(evidence["module_source"]["revision"], graph["snapshot"]["revision"])
            self.assertEqual(evidence["target_location"]["line"], 2)
            self.assertIn("not_runtime_dispatch", c["reason"])
        self.assertIn("relative_import_verified_init_chain", calls[2]["import_evidence"]["import_root_rule"])

    def test_bare_import_syntax_without_alias_and_direct_from(self):
        self.write("pkg/entry.py", "import service\nfrom service import finish\ndef run():\n    service.finish(1)\n    finish(2)\n")
        self.commit()
        self.assertEqual([c["candidate_kind"] for c in self.fn(self.graph(), "pkg/entry.py", "run")["calls"]],
                         ["repository_import"] * 2)

    def test_local_shadowing_rebind_and_type_parameter_fail_closed(self):
        self.write("pkg/entry.py", """import service as core
from service import finish as stop
def a(core): return core.finish(1)
def b():
    core = object()
    return core.finish(1)
def c[core](): return core.finish(1)
def d():
    return stop(1)
""")
        self.commit()
        graph = self.graph()
        for name in ("a", "b", "c"):
            self.assertEqual(self.fn(graph, "pkg/entry.py", name)["calls"][0]["resolution"], "unresolved")
        self.assertEqual(self.fn(graph, "pkg/entry.py", "d")["calls"][0]["resolution"], "candidate")

    def test_module_reassignment_and_monkey_patch_fail_closed(self):
        self.write("pkg/entry.py", "import service as core\ncore = object()\ndef run(): return core.finish(1)\n")
        self.commit()
        graph = self.graph()
        c = self.fn(graph, "pkg/entry.py", "run")["calls"][0]
        self.assertEqual(c["resolution"], "unresolved")
        self.assertEqual(c["import_evidence"]["reason"], "possible_alias_rebinding_or_dynamic_namespace")

    def test_conditional_local_star_and_dotted_without_asname_are_unsupported(self):
        self.write("pkg/entry.py", """if True:
    import service as conditional
try:
    import service as guarded
except Exception:
    pass
from service import *
import pkg.service
def run():
    import service as local
    conditional.finish(1)
    guarded.finish(1)
    local.finish(1)
    pkg.service.finish(1)
""")
        self.commit()
        graph = self.graph()
        module = next(m for m in graph["modules"] if m["path"] == "pkg/entry.py")
        reasons = {b["reason"] for b in module["import_bindings"]}
        self.assertIn("conditional_try_local_or_nested_import_unsupported", reasons)
        self.assertIn("star_or_package_member_import_unsupported", reasons)
        self.assertIn("dotted_import_without_asname_unsupported", reasons)
        self.assertFalse(any(c.get("target") for c in self.fn(graph, "pkg/entry.py", "run")["calls"]))

    def test_duplicate_basenames_are_not_disambiguated_by_guess(self):
        self.write("other/service.py", "def finish(x): return x\n")
        self.commit()
        graph = self.graph()
        calls = self.fn(graph, "pkg/entry.py", "run")["calls"]
        self.assertEqual([c["resolution"] for c in calls], ["unresolved", "unresolved", "candidate"])
        self.assertEqual(calls[0]["import_evidence"]["reason"], "ambiguous_repository_module_basename")
        self.assertEqual(len(calls[0]["import_evidence"]["module_candidates"]), 2)

    def test_missing_excluded_and_parse_failed_modules_do_not_produce_targets(self):
        self.write("pkg/entry.py", "import missing as m\nimport service as core\ndef run(): m.finish(1); core.finish(2)\n")
        self.commit()
        graph = deep.build(self.root, ("pkg/service.py",))
        calls = self.fn(graph, "pkg/entry.py", "run")["calls"]
        self.assertTrue(all(c["resolution"] == "unresolved" for c in calls))
        self.assertIn("outside_repository", calls[0]["import_evidence"]["reason"])
        self.assertEqual(calls[1]["import_evidence"]["reason"], "target_source_excluded_or_parse_failed")
        self.write("pkg/service.py", "def bad(:\n")
        self.commit()
        graph = self.graph()
        self.assertEqual(self.fn(graph, "pkg/entry.py", "run")["calls"][1]["resolution"], "unresolved")

    def test_relative_without_init_namespace_is_unresolved(self):
        self.write("other/service.py", "def finish(x): return x\n")
        self.write("other/entry.py", "from .service import finish\ndef run(): return finish(1)\n")
        self.commit()
        graph = self.graph()
        call = self.fn(graph, "other/entry.py", "run")["calls"][0]
        self.assertEqual(call["resolution"], "unresolved")
        self.assertEqual(call["import_evidence"]["reason"], "namespace_or_missing_package_init")

    def test_same_named_packages_in_different_roots_are_unresolved(self):
        self.write("pkg/tool/__init__.py", "def finish(): return 1\n")
        self.write("other/tool/__init__.py", "def finish(): return 2\n")
        self.write("pkg/entry.py", "import tool as core\ndef run(): return core.finish()\n")
        self.commit()
        call = self.fn(self.graph(), "pkg/entry.py", "run")["calls"][0]
        self.assertEqual(call["resolution"], "unresolved")
        self.assertEqual(call["import_evidence"]["reason"], "ambiguous_repository_module_basename")
        self.assertEqual(len(call["import_evidence"]["module_candidates"]), 2)

    def test_relative_import_cannot_escape_root_even_with_init_present(self):
        self.write("__init__.py", "")
        self.write("service.py", "def finish(x): return x\n")
        self.write("pkg/entry.py", "from ..service import finish\ndef run(): return finish(1)\n")
        self.commit()
        call = self.fn(self.graph(), "pkg/entry.py", "run")["calls"][0]
        self.assertEqual(call["resolution"], "unresolved")
        self.assertEqual(call["import_evidence"]["reason"], "relative_import_beyond_repository_root")

    def test_dynamic_receiver_and_reexport_are_unresolved(self):
        self.write("pkg/entry.py", """import service as core
from service import not_exported as other
def run():
    core.finish.extra(1)
    other(2)
""")
        self.commit()
        calls = self.fn(self.graph(), "pkg/entry.py", "run")["calls"]
        self.assertTrue(all(c["resolution"] == "unresolved" for c in calls))
        self.assertEqual(calls[0]["import_evidence"]["call_boundary"], "dynamic_receiver_or_chained_attribute")

    def test_cycle_is_bounded_and_deterministic(self):
        self.write("pkg/service.py", """import entry as entry

def finish(x): return entry.run(x)
""")
        self.commit()
        a, b = self.graph(), self.graph()
        self.assertEqual(deep.encoded(a), deep.encoded(b))
        s = deep.bounded_slice(a, "pkg/entry.py::run", hops=3)
        self.assertLessEqual(len(s["nodes"]), 80)
        self.assertTrue(any(e["kind"] == "call_candidate" and
                            e.get("candidate_kind") == "repository_import" for e in s["edges"]))

    def test_bounded_slice_keeps_provenance_unknowns_and_omission(self):
        graph = self.graph()
        selected = deep.bounded_slice(graph, "pkg/entry.py::run", hops=2)
        self.assertTrue(any(e.get("import_evidence") for e in selected["edges"]))
        self.assertTrue(selected["unknowns"])
        limited = deep.bounded_slice(graph, "pkg/entry.py::run", max_nodes=1, max_edges=0)
        self.assertFalse(limited["complete"])
        self.assertGreater(limited["omitted_edges"], 0)
        self.assertTrue(limited["call_sites"][0]["import_evidence"])

    def test_profile_generator_source_and_payload_changes_rejected(self):
        artifact = self.graph()
        self.assertEqual(deep.verify(self.root, artifact), artifact)
        for key in ("generator", "profile", "modules", "functions"):
            mutated = copy.deepcopy(artifact)
            mutated[key] = {"changed": True}
            mutated["artifact_sha256"] = deep.digest(deep.encoded({k: v for k, v in mutated.items()
                                                                  if k != "artifact_sha256"}))
            with self.subTest(key=key), self.assertRaises(deep.DeepError):
                deep.verify(self.root, mutated)
        self.write("pkg/service.py", "def replaced(): return 0\n")
        with self.assertRaises(deep.DeepError):
            deep.verify(self.root, artifact)


    def test_module_level_def_nested_global_import_and_attribute_patch_block_candidates(self):
        variants = [
            "def core(): return 1",
            "def rename():\n    global core\n    import other as core",
            "core.finish = lambda value: value",
            "try:\n    1 / 0\nexcept Exception as core:\n    pass",
            "from unknown import *",
        ]
        for change in variants:
            with self.subTest(change=change):
                self.write("pkg/entry.py",
                           "import service as core\n" + change +
                           "\ndef run(): return core.finish(1)\n")
                self.commit()
                graph = self.graph()
                call = self.fn(graph, "pkg/entry.py", "run")["calls"][0]
                self.assertEqual(call["resolution"], "unresolved")
                self.assertEqual(call["import_evidence"]["reason"],
                                 "possible_alias_rebinding_or_dynamic_namespace")

    def test_real_linux_sources_follow_two_cross_module_calls_without_execution(self):
        for path in (
            "scripts/linux/eidolon_linux_desktop_session_v3.py",
            "scripts/linux/eidolon_linux_desktop_v3.py",
            "scripts/linux/eidolon_linux_process_owner.py",
        ):
            self.write(path, (REPO / path).read_text())
        self.commit()
        graph = self.graph()
        session = "scripts/linux/eidolon_linux_desktop_session_v3.py"
        cleanup = self.fn(graph, session, "_cleanup")
        call = next(c for c in cleanup["calls"] if c["expression"] == "core.cleanup_owner")
        self.assertEqual(call["target"], "scripts/linux/eidolon_linux_desktop_v3.py::cleanup_owner@482")
        self.assertEqual(call["location"]["line"], 105)
        self.assertEqual(call["import_evidence"]["import_location"]["line"], 16)
        serve = self.fn(graph, session, "serve")
        identity = next(c for c in serve["calls"] if c["expression"] == "process_identity")
        self.assertEqual(identity["target"], "scripts/linux/eidolon_linux_process_owner.py::identity@32")
        self.assertEqual(identity["location"]["line"], 286)
        self.assertEqual(identity["import_evidence"]["import_location"]["line"], 14)
        result = deep.bounded_slice(graph, cleanup["id"], hops=2)
        self.assertTrue(any(e["to"] == call["target"] for e in result["edges"]))
        self.assertEqual(deep.verify(self.root, graph), graph)

if __name__ == "__main__":
    unittest.main()
