"""Import boundary regressions; inspect compiler output but never execute targets."""
import copy
import hashlib
from pathlib import Path
import sys
import types
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from deep_structure.import_resolution import resolve_imports
from deep_structure.python_analysis import analyze_python


class ImportGuardTests(unittest.TestCase):
    def analyze(self, entry, service, extra=None):
        sources = {"entry.py": entry.encode(), "service.py": service.encode()}
        sources.update({path: text.encode() for path, text in (extra or {}).items()})
        files, modules, functions = [], [], []
        for path, source in sorted(sources.items()):
            result = analyze_python(path, source)
            self.assertEqual(result["parse_status"], "analyzed", result["diagnostics"])
            files.append({"path": path, "analysis_status": result["parse_status"],
                          "git_blob": hashlib.sha1(b"blob " + str(len(source)).encode()
                                                   + b"\0" + source).hexdigest(),
                          "sha256": hashlib.sha256(source).hexdigest()})
            modules.append({"path": path, "imports": result["imports"]})
            functions.extend(result["functions"])
        summary = resolve_imports(files, modules, functions, sources, "fixture-revision")
        return {"files": files, "modules": modules, "functions": functions, "summary": summary}

    def call(self, graph, name="run", expression=None):
        function = next(f for f in graph["functions"]
                        if f["path"] == "entry.py" and f["qualname"] == name)
        calls = [c for c in function["calls"]
                 if expression is None or c["expression"] == expression]
        self.assertEqual(len(calls), 1, calls)
        return calls[0]

    def assert_unresolved(self, call):
        self.assertEqual(call["resolution"], "unresolved")
        self.assertNotIn("target", call)
        self.assertNotIn("candidate_kind", call)

    def test_private_attribute_never_guesses_unmangled_or_mangled_target(self):
        service = "def __finish(): return 1\ndef _C__finish(): return 2\n"
        variants = [
            ("class C:\n    def run(self): return core.__finish()\n", "C.run"),
            ("class C:\n    def outer(self):\n        def run(): return core.__finish()\n",
             "C.outer.<locals>.run"),
            ("class C[T]:\n    def run(self): return core.__finish()\n", "C.run"),
            ("class Outer:\n    class C:\n        def run(self): return core.__finish()\n",
             "Outer.C.run"),
            # Class context is not modeled by the resolver. Refuse this spelling
            # outside classes too instead of guessing where mangling applies.
            ("def run(): return core.__finish()\n", "run"),
        ]
        for body, name in variants:
            with self.subTest(name=name, body=body):
                graph = self.analyze("import service as core\n" + body, service)
                call = self.call(graph, name)
                self.assert_unresolved(call)
                self.assertEqual(call["import_evidence"]["call_boundary"],
                                 "private_attribute_mangling_not_modeled")
                self.assertEqual(call["import_evidence"]["module_source"]["path"], "service.py")
                self.assertEqual(call["import_evidence"]["import_location"]["line"], 1)

    def test_private_attribute_fixture_compiler_uses_transformed_name(self):
        # Compile only: no imported source or resulting code object is executed.
        code = compile("import service as core\nclass C:\n"
                       "    def run(self): return core.__finish()\n",
                       "entry.py", "exec", dont_inherit=True)
        pending = [code]
        method = None
        while pending:
            current = pending.pop()
            if current.co_name == "run":
                method = current
            pending.extend(value for value in current.co_consts if isinstance(value, types.CodeType))
        self.assertIsNotNone(method)
        self.assertIn("_C__finish", method.co_names)
        self.assertNotIn("__finish", method.co_names)

    def test_nonprivate_and_dunder_attributes_and_direct_private_import_still_work(self):
        for name in ("finish", "_finish", "__finish__", "_C__finish"):
            with self.subTest(name=name):
                graph = self.analyze("import service as core\nclass C:\n"
                                     f"    def run(self): return core.{name}()\n",
                                     f"def {name}(): return 1\n")
                call = self.call(graph, "C.run")
                self.assertEqual(call["target"], f"service.py::{name}@1")
        graph = self.analyze("from service import __finish as done\n"
                             "class C:\n    def run(self): return done()\n",
                             "def __finish(): return 1\n")
        self.assertEqual(self.call(graph, "C.run")["target"], "service.py::__finish@1")

    def test_compiler_normalized_globals_block_module_and_function_import_aliases(self):
        variants = [
            "class C:\n    global __core\n    __core = None\n",
            "class C:\n    def mutate(self):\n        global __core\n        __core = None\n",
            "class C:\n    def mutate(self):\n        global __core\n        del __core\n",
            "class C:\n    def mutate(self):\n        global __core\n        import other as __core\n",
            "class C[T]:\n    def mutate(self):\n        global __core\n        __core = None\n",
            "class C:\n    def outer(self):\n        def mutate():\n            global __core\n"
            "            __core = None\n",
        ]
        imports = [("import service as _C__core", "_C__core.finish()"),
                   ("from service import finish as _C__core", "_C__core()")]
        for declaration, expression in imports:
            for mutation in variants:
                with self.subTest(declaration=declaration, mutation=mutation):
                    graph = self.analyze(declaration + "\n" + mutation
                                         + f"def run(): return {expression}\n",
                                         "def finish(): return 1\n")
                    call = self.call(graph)
                    self.assert_unresolved(call)
                    self.assertEqual(call["import_evidence"]["bound_name"], "_C__core")
                    self.assertEqual(call["import_evidence"]["reason"],
                                     "possible_alias_rebinding_or_dynamic_namespace")

    def test_compiler_global_guard_does_not_suppress_unrelated_alias(self):
        graph = self.analyze("import service as _C__core\nimport service as live\n"
                             "class C:\n    def mutate(self):\n        global __core\n"
                             "        __core = None\n"
                             "def run():\n    _C__core.finish()\n    live.finish()\n",
                             "def finish(): return 1\n")
        self.assert_unresolved(self.call(graph, expression="_C__core.finish"))
        self.assertEqual(self.call(graph, expression="live.finish")["target"], "service.py::finish@1")
        self.assertEqual(graph["summary"]["cross_module_call_candidates"], 1)

    def test_dynamic_export_namespace_blocks_module_and_from_imports(self):
        operations = ["exec('finish = None')", "eval('0')", "globals()", "locals()",
                      "vars()", "setattr(object(), 'finish', None)", "delattr(object(), 'finish')"]
        for operation in operations:
            with self.subTest(operation=operation):
                graph = self.analyze("import service as core\nfrom service import finish as done\n"
                                     "def run():\n    core.finish()\n    done()\n",
                                     "def finish(): return 1\n" + operation + "\n")
                for expression in ("core.finish", "done"):
                    call = self.call(graph, expression=expression)
                    self.assert_unresolved(call)
                    evidence = call["import_evidence"]
                    self.assertEqual(evidence["reason"], "target_module_dynamic_namespace_unsupported")
                    sites = evidence["target_module_boundaries"]
                    self.assertEqual(len(sites), 1)
                    self.assertEqual(sites[0]["name"], operation.split("(")[0])
                    self.assertEqual(sites[0]["location"]["line"], 2)
                    self.assertEqual(sites[0]["source"]["path"], "service.py")
                    self.assertEqual(sites[0]["source"]["revision"], "fixture-revision")
                    self.assertNotIn("target", evidence)

    def test_nested_dynamic_export_namespace_is_conservatively_blocked(self):
        graph = self.analyze("from service import finish\ndef run(): return finish()\n",
                             "def finish(): return 1\ndef mutate():\n    exec('finish = None')\n")
        call = self.call(graph)
        self.assert_unresolved(call)
        self.assertEqual(call["import_evidence"]["target_module_boundaries"][0]["location"]["line"], 3)

    def test_dynamic_export_evidence_is_deterministic_and_target_specific(self):
        entry = ("import service as core\nfrom safe import finish as safe_finish\n"
                 "def run():\n    core.finish()\n    safe_finish()\n")
        service = "def finish(): return 1\nexec('finish = None')\n"
        extra = {"safe.py": "def finish(): return 2\n"}
        graph = self.analyze(entry, service, extra)
        self.assertEqual(copy.deepcopy(graph), self.analyze(entry, service, extra))
        self.assert_unresolved(self.call(graph, expression="core.finish"))
        self.assertEqual(self.call(graph, expression="safe_finish")["target"], "safe.py::finish@1")
        self.assertEqual(graph["summary"]["cross_module_call_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
