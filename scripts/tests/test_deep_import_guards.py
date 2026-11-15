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

    def test_private_receiver_assignment_forms_block_import_candidates(self):
        operations = [
            '__core.finish = lambda: "replacement"',
            "del __core.finish",
            "__core.finish += replacement",
            "__core.finish: object = replacement",
            "__core.finish, other = replacement, None",
            "__core.finish = other = replacement",
            "for __core.finish in replacements:\n        pass",
            "with manager() as __core.finish:\n        pass",
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                entry = ("import service as _C__core\nclass C:\n    " + operation
                         + "\ndef run(): return _C__core.finish()\n")
                graph = self.analyze(entry, "def finish(): return 'original'\n")
                call = self.call(graph)
                self.assert_unresolved(call)
                evidence = call["import_evidence"]
                self.assertEqual(evidence["reason"], "possible_alias_rebinding_or_dynamic_namespace")
                self.assertEqual(evidence["bound_name"], "_C__core")
                self.assertEqual(evidence["import_location"]["line"], 1)
                self.assertEqual(evidence["source"]["revision"], "fixture-revision")
                self.assertEqual(evidence["module_source"]["path"], "service.py")
                self.assertNotIn("target", evidence)
                self.assertNotIn("target_location", evidence)
                self.assertEqual(graph["summary"].get("cross_module_call_candidates", 0), 0)

    def test_private_receiver_mutations_in_nested_scopes_block_without_global(self):
        bodies = [
            "class C:\n    def mutate(self):\n        {operation}\n",
            "class C[T]:\n    {operation}\n",
            "class C[T]:\n    def mutate(self):\n        {operation}\n",
            "class C:\n    def outer(self):\n        def mutate():\n            {operation}\n",
            "class Outer:\n    class C:\n        {operation}\n",
            "def outer():\n    class C:\n        {operation}\n",
        ]
        for body in bodies:
            for operation in ("__core.finish = replacement", "del __core.finish"):
                with self.subTest(body=body, operation=operation):
                    graph = self.analyze("import service as _C__core\n"
                                         + body.format(operation=operation)
                                         + "def run(): return _C__core.finish()\n",
                                         "def finish(): return 1\n")
                    self.assert_unresolved(self.call(graph))

    def test_private_receiver_class_spellings_use_compiler_normalization(self):
        classes = [("C", "_C__core"), ("_C", "_C__core"), ("__C", "_C__core"),
                   ("C__", "_C____core"), ("___", "__core"), ("型", "_型__core")]
        for class_name, alias in classes:
            with self.subTest(class_name=class_name):
                entry = (f"import service as {alias}\nclass {class_name}:\n"
                         f"    __core.finish = replacement\ndef run(): return {alias}.finish()\n")
                # Inspect the original fixture's compiled class body, not the
                # resolver's normalization probe, and never execute either.
                code = compile(entry, "entry.py", "exec", dont_inherit=True)
                body = next(c for c in code.co_consts
                            if isinstance(c, types.CodeType) and c.co_name == class_name)
                self.assertIn(alias, body.co_names)
                self.assert_unresolved(self.call(self.analyze(entry, "def finish(): return 1\n")))

    def test_private_receiver_reads_alone_keep_import_candidate(self):
        for body in ("class C:\n    saved = __core.finish\n",
                     "class C:\n    def read(self): return __core.finish\n"):
            with self.subTest(body=body):
                graph = self.analyze("import service as _C__core\n" + body
                                     + "def run(): return _C__core.finish()\n",
                                     "def finish(): return 1\n")
                self.assertEqual(self.call(graph)["target"], "service.py::finish@1")

    def test_private_receiver_guard_preserves_unrelated_names_and_modules(self):
        entry = ("import service as _C__core\nimport safe as live\n"
                 "from safe import finish as _D__core\nclass C:\n"
                 "    __core.finish = replacement\n"
                 "def run():\n    _C__core.finish()\n    live.finish()\n    _D__core()\n")
        extra = {"safe.py": "def finish(): return 2\n"}
        service = "raise RuntimeError('must not execute')\ndef finish(): return 1\n"
        graph = self.analyze(entry, service, extra)
        self.assertEqual(graph, self.analyze(entry, service, extra))
        self.assert_unresolved(self.call(graph, expression="_C__core.finish"))
        for expression in ("live.finish", "_D__core"):
            self.assertEqual(self.call(graph, expression=expression)["target"], "safe.py::finish@1")
        self.assertEqual(graph["summary"]["cross_module_call_candidates"], 2)

    def test_private_receiver_context_union_is_only_a_conservative_blocker(self):
        # No AST-to-compiler-scope dispatch claim: a write using this spelling
        # blocks each possible class spelling, even for an unrelated class.
        graph = self.analyze("import service as _C__core\nimport safe as _D__core\n"
                             "class C:\n    __core.finish = replacement\nclass D: pass\n"
                             "def run():\n    _C__core.finish()\n    _D__core.finish()\n",
                             "def finish(): return 1\n", {"safe.py": "def finish(): return 2\n"})
        for expression in ("_C__core.finish", "_D__core.finish"):
            self.assert_unresolved(self.call(graph, expression=expression))

    def test_attribute_mutation_guard_retains_plain_dunder_and_explicit_names(self):
        for alias in ("core", "_core", "__core__", "_C__core", "__core"):
            for operation in (f"{alias}.finish = replacement", f"del {alias}.finish"):
                with self.subTest(alias=alias, operation=operation):
                    graph = self.analyze(f"import service as {alias}\n{operation}\n"
                                         f"def run(): return {alias}.finish()\n",
                                         "def finish(): return 1\n")
                    self.assert_unresolved(self.call(graph))

    def test_private_receiver_compiler_reads_normalized_name_without_global(self):
        import dis
        import symtable

        for operation, opcode in (("__core.finish = replacement", "STORE_ATTR"),
                                  ("del __core.finish", "DELETE_ATTR")):
            with self.subTest(operation=operation):
                entry = "import service as _C__core\nclass C:\n    " + operation + "\n"
                table = symtable.symtable(entry, "entry.py", "exec").get_children()[0]
                symbol = table.lookup("_C__core")
                self.assertTrue(symbol.is_referenced())
                self.assertFalse(symbol.is_declared_global())
                code = compile(entry, "entry.py", "exec", dont_inherit=True)
                body = next(c for c in code.co_consts if isinstance(c, types.CodeType))
                instructions = list(dis.get_instructions(body))
                self.assertTrue(any(i.opname == "LOAD_NAME" and i.argval == "_C__core"
                                    for i in instructions))
                self.assertTrue(any(i.opname == opcode and i.argval == "finish"
                                    for i in instructions))


    def test_unicode_keyword_class_names_keep_mutation_guard_without_text_roundtrip(self):
        import ast
        import keyword

        # Generate valid source spellings whose normalized AST names are keywords.
        # Constants True/False/None are not accepted by the existing AST validator.
        words = sorted({name for name in keyword.kwlist + keyword.softkwlist if name.islower()})
        for word in words:
            spelling = "".join(chr(ord("𝐚") + ord(c) - ord("a"))
                               if "a" <= c <= "z" else c for c in word)
            for operation in ("__core.finish = replacement", "del __core.finish"):
                with self.subTest(word=word, operation=operation):
                    alias = f"_{word}__core"
                    entry = (f"import service as {alias}\nclass {spelling}:\n"
                             f"    {operation}\ndef run(): return {alias}.finish()\n")
                    self.assertEqual(ast.parse(entry).body[1].name, word)
                    # This oracle compiles the *original* source, not the probe.
                    original = compile(entry, "entry.py", "exec", dont_inherit=True)
                    class_code = next(c for c in original.co_consts if isinstance(c, types.CodeType))
                    self.assertIn(alias, class_code.co_names)
                    call = self.call(self.analyze(entry, "def finish(): return 1\n"))
                    self.assert_unresolved(call)
                    self.assertEqual(call["import_evidence"]["reason"],
                                     "possible_alias_rebinding_or_dynamic_namespace")
                    self.assertNotIn("target", call["import_evidence"])

    def test_unicode_keyword_mutation_guard_in_nested_and_generic_classes(self):
        bodies = [
            "class 𝐢𝐟:\n    def mutate(self):\n        {operation}\n",
            "class 𝐢𝐟[T]:\n    {operation}\n",
            "class 𝐢𝐟[T]:\n    def mutate(self):\n        {operation}\n",
            "class Outer:\n    class 𝐢𝐟:\n        {operation}\n",
            "def outer():\n    class 𝐢𝐟:\n        {operation}\n",
            "class 𝐢𝐟:\n    def outer(self):\n        def mutate():\n            {operation}\n",
        ]
        for body in bodies:
            for operation in ("__core.finish = replacement", "del __core.finish"):
                with self.subTest(body=body, operation=operation):
                    entry = ("import service as _if__core\n" + body.format(operation=operation)
                             + "def run(): return _if__core.finish()\n")
                    self.assert_unresolved(self.call(self.analyze(entry, "def finish(): return 1\n")))

    def test_unicode_probe_collects_only_receiver_names_not_class_scaffolding(self):
        import ast
        from deep_structure.import_resolution import _attribute_mutation_receivers

        tree = ast.parse("class 𝐢𝐟:\n    __𝐜𝐨𝐫𝐞.finish = replacement\n"
                         "    del __peer.finish\n    ordinary.finish = replacement\n")
        original = ast.dump(tree, include_attributes=True)
        expected = {"__core", "__peer", "_if__core", "_if__peer", "ordinary"}
        self.assertEqual(_attribute_mutation_receivers(tree), expected)
        self.assertEqual(_attribute_mutation_receivers(tree), expected)
        self.assertEqual(ast.dump(tree, include_attributes=True), original)
        # __module__/__qualname__/version-specific class machinery must not
        # enter the suppression set when extracting names from compiler output.

    def test_unicode_keyword_read_only_class_keeps_candidate(self):
        for body in ("class 𝐢𝐟:\n    saved = __core.finish\n",
                     "class 𝐢𝐟[T]:\n    def read(self): return __core.finish\n"):
            with self.subTest(body=body):
                entry = "import service as _if__core\n" + body + "def run(): return _if__core.finish()\n"
                graph = self.analyze(entry, "def finish(): return 1\n")
                self.assertEqual(self.call(graph)["target"], "service.py::finish@1")

    def test_unicode_keyword_mutation_keeps_other_modules_and_provenance(self):
        entry = ("import service as _if__core\nimport safe as live\n"
                 "class 𝐢𝐟:\n    del __core.finish\n"
                 "def run():\n    _if__core.finish()\n    live.finish()\n")
        service = "raise RuntimeError('must not execute')\ndef finish(): return 1\n"
        extra = {"safe.py": "def finish(): return 2\n"}
        graph = self.analyze(entry, service, extra)
        self.assertEqual(graph, self.analyze(entry, service, extra))
        call = self.call(graph, expression="_if__core.finish")
        self.assert_unresolved(call)
        evidence = call["import_evidence"]
        self.assertEqual(evidence["source"]["path"], "entry.py")
        self.assertEqual(evidence["source"]["sha256"], hashlib.sha256(entry.encode()).hexdigest())
        self.assertEqual(evidence["source"]["revision"], "fixture-revision")
        self.assertEqual(evidence["import_location"]["line"], 1)
        self.assertEqual(evidence["module_source"]["path"], "service.py")
        self.assertEqual(self.call(graph, expression="live.finish")["target"], "safe.py::finish@1")
        self.assertEqual(graph["summary"]["cross_module_call_candidates"], 1)

    def test_unicode_keyword_repository_cli_verify_and_bounded_slice(self):
        import json
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for args in (("init", "-q"), ("config", "user.name", "Deep test"),
                         ("config", "user.email", "test@example.invalid")):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
            (root / "entry.py").write_text(
                "import service as _if__core\nimport safe as live\nclass 𝐢𝐟:\n"
                "    __core.finish = None\ndef run():\n    _if__core.finish()\n    live.finish()\n",
                encoding="utf-8")
            (root / "service.py").write_text(
                "raise RuntimeError('must not execute')\ndef finish(): return 1\n", encoding="utf-8")
            (root / "safe.py").write_text("def finish(): return 2\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"],
                           check=True, capture_output=True)

            def run(*args):
                process = subprocess.run([sys.executable, "-B", str(REPO / "scripts/deep_structure.py"),
                                          *args, "--repo", str(root)],
                                         capture_output=True, text=True, timeout=90)
                self.assertEqual(process.returncode, 0, process.stderr)
                return json.loads(process.stdout)

            artifact = Path(run("repository-deep")["artifact"])
            payload = artifact.read_bytes()
            graph = json.loads(payload)
            self.assertTrue(all(row["analysis_status"] == "analyzed" for row in graph["files"]))
            function = next(f for f in graph["functions"] if f["id"] == "entry.py::run@5")
            self.assert_unresolved(function["calls"][0])
            self.assertEqual(function["calls"][1]["target"], "safe.py::finish@1")
            self.assertEqual(run("verify", "--artifact", str(artifact))["status"], "fresh")
            run("repository-deep")
            self.assertEqual(artifact.read_bytes(), payload)
            sliced = run("targeted-deep", "--artifact", str(artifact), "--symbol", function["id"],
                         "--hops", "0", "--max-nodes", "1", "--max-edges", "0")
            view = json.loads(Path(sliced["artifact"]).read_text())
            self.assert_unresolved(view["call_sites"][0])
            self.assertEqual(view["call_sites"][0]["import_evidence"]["source"]["sha256"],
                             hashlib.sha256((root / "entry.py").read_bytes()).hexdigest())
            self.assertTrue(view["unknowns"])
            (root / "entry.py").write_text("# source changed\n", encoding="utf-8")
            stale = subprocess.run([sys.executable, "-B", str(REPO / "scripts/deep_structure.py"),
                                    "verify", "--repo", str(root), "--artifact", str(artifact)],
                                   capture_output=True, text=True, timeout=90)
            self.assertEqual(stale.returncode, 2)


if __name__ == "__main__":
    unittest.main()
