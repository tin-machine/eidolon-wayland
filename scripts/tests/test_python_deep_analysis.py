"""Semantic fixtures for statement CFG and local reaching definitions."""

import importlib.util
import json
from pathlib import Path
import sys
import textwrap
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "python_deep_analysis", REPO / "scripts/deep_structure/python_analysis.py")
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


class PythonDeepTests(unittest.TestCase):
    def analyze(self, source):
        return analysis.analyze_python("fixture.py", textwrap.dedent(source).lstrip().encode())

    def function(self, source, name="f"):
        result = self.analyze(source)
        self.assertEqual(result["parse_status"], "analyzed", result["diagnostics"])
        return next(f for f in result["functions"] if f["qualname"] == name)

    def node(self, function, kind, line=None):
        matches = [node for node in function["cfg"]["nodes"] if node["kind"] == kind
                   and (line is None or node["location"]["line"] == line)]
        self.assertEqual(len(matches), 1, matches)
        return matches[0]

    def definitions(self, function, node, variable):
        return {edge["from"] for edge in function["dfg"]["edges"]
                if edge["to"] == node["id"] and edge["variable"] == variable}

    def unbound(self, function, node, variable):
        return any(item == {"node": node["id"], "variable": variable,
                            "reason": "possibly_unbound_local"}
                   for item in function["dfg"]["unresolved_uses"])

    def edge(self, function, start, end, kind=None):
        return any(edge["from"] == start["id"] and edge["to"] == end["id"]
                   and (kind is None or edge["kind"] == kind)
                   for edge in function["cfg"]["edges"])

    def test_both_branch_definitions_reach_join_but_entry_does_not(self):
        f = self.function("""
            def f(condition, x):
                if condition:
                    x = 1
                else:
                    x = 2
                return x
        """)
        first, second, ret = self.node(f, "assign", 3), self.node(f, "assign", 5), self.node(f, "return")
        self.assertEqual(self.definitions(f, ret, "x"), {first["id"], second["id"]})
        self.assertFalse(self.unbound(f, ret, "x"))
        branch = self.node(f, "if")
        self.assertTrue(self.edge(f, branch, first, "true"))
        self.assertTrue(self.edge(f, branch, second, "false"))

    def test_assignment_kills_old_definitions(self):
        f = self.function("""
            def f(x):
                x = 1
                x = 2
                return x
        """)
        self.assertEqual(self.definitions(f, self.node(f, "return"), "x"),
                         {self.node(f, "assign", 3)["id"]})

    def test_conditional_definition_retains_unbound_path(self):
        f = self.function("""
            def f(flag):
                if flag:
                    value = 3
                return value
        """)
        ret = self.node(f, "return")
        self.assertEqual(self.definitions(f, ret, "value"), {self.node(f, "assign")["id"]})
        self.assertTrue(self.unbound(f, ret, "value"))

    def test_global_and_builtin_reads_are_not_local_dependencies(self):
        f = self.function("""
            def f(x):
                return helper(x) + LIMIT
        """)
        self.assertEqual({u["variable"] for u in f["dfg"]["unresolved_uses"]}, {"helper", "LIMIT"})
        self.assertEqual(self.definitions(f, self.node(f, "return"), "x"), {f["cfg"]["entry"]})

    def test_while_loop_fixed_point_carries_entry_and_backedge(self):
        f = self.function("""
            def f(condition, x):
                while condition:
                    x += 1
                return x
        """)
        update = self.node(f, "augassign")
        expected = {f["cfg"]["entry"], update["id"]}
        self.assertEqual(self.definitions(f, update, "x"), expected)
        self.assertEqual(self.definitions(f, self.node(f, "return"), "x"), expected)

    def test_while_zero_iterations_retains_unbound(self):
        f = self.function("""
            def f(flag):
                while flag:
                    value = 1
                return value
        """)
        self.assertTrue(self.unbound(f, self.node(f, "return"), "value"))

    def test_for_iterable_evaluates_once_and_target_is_not_defined_on_zero_trip(self):
        f = self.function("""
            def f(items):
                for item in items:
                    items = []
                return item
        """)
        iterator, target, ret = self.node(f, "for_iter"), self.node(f, "for_target"), self.node(f, "return")
        self.assertEqual(self.definitions(f, iterator, "items"), {f["cfg"]["entry"]})
        self.assertEqual(self.definitions(f, ret, "item"), {target["id"]})
        self.assertTrue(self.unbound(f, ret, "item"))
        self.assertNotIn(iterator["id"], {e["to"] for e in f["cfg"]["edges"] if e["kind"] == "loop_back"})

    def test_break_bypasses_loop_else(self):
        f = self.function("""
            def f(items):
                value = 0
                for item in items:
                    value = 1
                    break
                else:
                    value = 2
                return value
        """)
        self.assertEqual(self.definitions(f, self.node(f, "return"), "value"),
                         {self.node(f, "assign", 4)["id"], self.node(f, "assign", 7)["id"]})
        self.assertTrue(self.edge(f, self.node(f, "break"), self.node(f, "loop_exit"), "break"))
        self.assertFalse(self.edge(f, self.node(f, "break"), self.node(f, "assign", 7)))

    def test_continue_does_not_bypass_future_loop_else(self):
        f = self.function("""
            def f(items):
                for item in items:
                    continue
                else:
                    result = 2
                return result
        """)
        self.assertTrue(self.edge(f, self.node(f, "continue"), self.node(f, "for_next"), "continue"))
        self.assertEqual(self.definitions(f, self.node(f, "return"), "result"),
                         {self.node(f, "assign")["id"]})
        self.assertFalse(self.unbound(f, self.node(f, "return"), "result"))

    def test_while_loop_else_and_break_preserve_both_definitions(self):
        f = self.function("""
            def f(flag):
                while flag:
                    result = 1
                    break
                else:
                    result = 2
                return result
        """)
        self.assertEqual(self.definitions(f, self.node(f, "return"), "result"),
                         {self.node(f, "assign", 3)["id"], self.node(f, "assign", 6)["id"]})

    def test_branch_at_loop_back_retains_polarity(self):
        f = self.function("""
            def f(flag, skip):
                while flag:
                    if skip:
                        continue
                return 0
        """)
        self.assertTrue(self.edge(f, self.node(f, "if"), self.node(f, "while"), "false"))
        self.assertTrue(self.edge(f, self.node(f, "if"), self.node(f, "continue"), "true"))

    def test_nested_loop_break_targets_nearest_loop(self):
        f = self.function("""
            def f(items, children):
                for item in items:
                    for child in children:
                        break
                return 0
        """)
        self.assertTrue(self.edge(f, self.node(f, "break"), self.node(f, "loop_exit", 3), "break"))
        self.assertFalse(self.edge(f, self.node(f, "break"), self.node(f, "loop_exit", 2)))

    def test_early_return_and_raise_have_no_fallthrough(self):
        f = self.function("""
            def f(flag, x):
                if flag:
                    return x
                raise RuntimeError()
                x = 2
                return x
        """)
        dead = self.node(f, "assign")
        self.assertFalse(dead["reachable"])
        self.assertFalse(self.node(f, "return", 6)["reachable"])
        self.assertFalse(any(e["to"] == dead["id"] for e in f["dfg"]["edges"]))
        first_ret = self.node(f, "return", 3)
        self.assertEqual([e["to"] for e in f["cfg"]["edges"] if e["from"] == first_ret["id"]], [f["cfg"]["exit"]])

    def test_both_returning_branches_do_not_feed_dead_join(self):
        f = self.function("""
            def f(flag):
                if flag:
                    return 1
                else:
                    return 2
                result = 3
        """)
        self.assertFalse(self.node(f, "assign")["reachable"])

    def test_annotations_without_values_do_not_define_and_are_not_executed(self):
        f = self.function("""
            def f():
                value: external_type()
                return value
        """)
        self.assertTrue(self.unbound(f, self.node(f, "return"), "value"))
        self.assertEqual(self.node(f, "annassign")["uses"], [])
        self.assertEqual(self.node(f, "annassign")["defs"], [])
        self.assertEqual(f["calls"], [])

    def test_heap_assignment_preserves_receiver_binding_without_heap_edges(self):
        f = self.function("""
            def f(obj, value):
                obj.field = value
                return obj.field
        """)
        self.assertEqual(self.node(f, "assign")["defs"], [])
        self.assertEqual(self.definitions(f, self.node(f, "return"), "obj"), {f["cfg"]["entry"]})
        self.assertIn("heap_state_not_modeled", {u["kind"] for u in f["unknowns"]})

    def test_short_circuit_is_explicit_may_read_not_expression_cfg(self):
        f = self.function("""
            def f(a, b):
                return a and b
        """)
        self.assertEqual(self.node(f, "return")["uses"], ["a", "b"])
        self.assertIn("expression_control_opaque", {u["kind"] for u in f["unknowns"]})
        self.assertEqual(f["pdg"], {"status": "not_implemented"})

    def test_parameters_include_positional_only_keyword_only_varargs_and_kwargs(self):
        f = self.function("""
            def f(a, /, b, *args, c, **kwargs):
                return (a, b, args, c, kwargs)
        """)
        self.assertEqual(f["dfg"]["local_variables"], ["a", "args", "b", "c", "kwargs"])
        self.assertEqual(len(f["dfg"]["edges"]), 5)

    def test_unsupported_constructs_reject_whole_function(self):
        snippets = [
            "try:\n    x = 1\nfinally:\n    cleanup()", "with resource():\n    pass",
            "x = [v for v in values]", "x = (v for v in values)",
            "x = {v for v in values}", "x = {v:v for v in values}",
            "yield 1", "x = lambda: 2", "if (x := 1):\n    pass",
            "global value\nvalue = 1", "del value", "a, b = values",
            "a = b = 1", "for a, b in values:\n    pass", "assert value",
            "match value:\n    case 1:\n        pass", "def inner():\n    return 1",
            "class Inner:\n    pass", "exec(value)", "eval(value)",
        ]
        for snippet in snippets:
            with self.subTest(snippet=snippet):
                source = "def f():\n" + textwrap.indent(snippet, "    ") + "\n    return 4\n"
                f = self.function(source)
                self.assertEqual(f["cfg"]["status"], "unsupported")
                self.assertEqual(f["cfg"]["edges"], [])
                self.assertEqual(f["dfg"]["edges"], [])
                self.assertTrue(any(u["kind"] == "unsupported_syntax" for u in f["unknowns"]))

    def test_async_await_and_try_star_are_explicitly_unsupported(self):
        for source in ("async def f():\n    await operation()\n",
                       "def f():\n    try:\n        operation()\n    except* Exception:\n        pass\n"):
            f = self.function(source)
            self.assertEqual(f["cfg"]["status"], "unsupported")

    def test_nested_functions_indexed_separately_without_parent_call_contamination(self):
        result = self.analyze("""
            def f():
                def inner():
                    nested_call()
                outer_call()
        """)
        outer, inner = result["functions"]
        self.assertEqual(inner["qualname"], "f.<locals>.inner")
        self.assertEqual([c["expression"] for c in outer["calls"]], ["outer_call"])
        self.assertEqual([c["expression"] for c in inner["calls"]], ["nested_call"])
        self.assertEqual(outer["cfg"]["status"], "unsupported")
        self.assertIn("closure_environment_not_modeled", {u["kind"] for u in inner["unknowns"]})

    def test_class_methods_have_distinct_qualified_names(self):
        result = self.analyze("""
            class A:
                def run(self): return 1
            class B:
                def run(self): return 2
        """)
        self.assertEqual([f["qualname"] for f in result["functions"]], ["A.run", "B.run"])
        self.assertEqual(result["functions"][0]["id"], "fixture.py::A.run@2")

    def test_unshadowed_unique_direct_call_only_candidate(self):
        f = self.function("""
            def helper(x): return x
            def f(x): return helper(x)
        """)
        self.assertEqual(f["calls"][0]["resolution"], "candidate")
        self.assertEqual(f["calls"][0]["target"], "fixture.py::helper@1")

    def test_shadowing_rebinding_decorators_and_dynamic_calls_are_unresolved(self):
        snippets = [
            "def helper(x): return x\ndef f(helper): return helper(1)",
            "def helper(x): return x\nhelper = external\ndef f(): return helper(1)",
            "@decorate\ndef helper(x): return x\ndef f(): return helper(1)",
            "def helper(x): return x\ndef helper(x): return x\ndef f(): return helper(1)",
            "def helper(x): return x\ndef f(obj): return obj.helper(1)",
            "if flag:\n    def helper(x): return x\ndef f(): return helper(1)",
            "def helper(x): return x\ndef f():\n    helper(1)\n    helper = external",
            "def helper(x): return x\nfrom external import *\ndef f(): return helper(1)",
            "def helper(x): return x\ndef f(value):\n    match value:\n        case {'helper': helper}:\n            return helper(1)",
            "def helper(x): return x\ntry:\n    operation()\nexcept Exception as helper:\n    pass\ndef f(): return helper(1)",
        ]
        for snippet in snippets:
            with self.subTest(snippet=snippet):
                f = self.function(snippet)
                self.assertTrue(all(call["resolution"] == "unresolved" for call in f["calls"]))
                self.assertTrue(all("target" not in call for call in f["calls"]))

    def test_enclosing_function_binding_shadows_module_candidate(self):
        result = self.analyze("""
            def helper(x): return x
            def outer(helper):
                def inner():
                    return helper(1)
                return inner
        """)
        inner = next(f for f in result["functions"] if f["name"] == "inner")
        self.assertEqual(inner["calls"][0]["resolution"], "unresolved")

    def test_module_annotations_and_definition_expressions_can_rebind_candidates(self):
        variants = [
            ("value: (helper := external)", True),
            ("def another(value=(helper := external)):\n    pass", False),
            ("def another(value: (helper := external)):\n    pass", True),
            ("class Another((helper := external)):\n    pass", False),
            ("def another():\n    global helper\n    helper = external", False),
            ("class Another:\n    global helper\n    value: (helper := external)", True),
        ]
        for variant, annotation_walrus in variants:
            with self.subTest(variant=variant):
                source = "def helper(): return 1\n" + variant + "\ndef f(): return helper()\n"
                if annotation_walrus and sys.version_info >= (3, 14):
                    result = self.analyze(source)
                    self.assertEqual(result["parse_status"], "parse_failed")
                    self.assertEqual(result["diagnostics"][0]["kind"], "SyntaxError")
                    continue
                f = self.function(source)
                self.assertEqual(f["calls"][0]["resolution"], "unresolved")

    def test_class_local_annotation_binding_does_not_rebind_module_helper(self):
        source = """
            def helper(): return 1
            class Another:
                value: (helper := external)
                def method(self):
                    local: helper()
            def f(): return helper()
        """
        if sys.version_info >= (3, 14):
            result = self.analyze(source)
            self.assertEqual(result["parse_status"], "parse_failed")
            self.assertEqual(result["diagnostics"][0]["kind"], "SyntaxError")
        else:
            f = self.function(source)
            self.assertEqual(f["calls"][0]["resolution"], "candidate")
        method = self.function("class C:\n    def f(self):\n        local: helper()\n", name="C.f")
        self.assertEqual(method["calls"], [])

    def test_type_parameters_shadow_module_calls_in_function_and_class_scopes(self):
        for parameter in ("helper", "*helper", "**helper"):
            variants = [
                (f"def f[{parameter}](): return helper()", "f"),
                (f"def outer[{parameter}]():\n    def f(): return helper()\n    return f", "outer.<locals>.f"),
                (f"class C[{parameter}]:\n    def f(self): return helper()", "C.f"),
                (f"class C:\n    def f[{parameter}](self): return helper()", "C.f"),
                (f"class C[{parameter}]:\n    def outer(self):\n        def f(): return helper()\n        return f", "C.outer.<locals>.f"),
                (f"class C[{parameter}]:\n    class Inner:\n        def f(self): return helper()", "C.Inner.f"),
            ]
            for variant, name in variants:
                with self.subTest(parameter=parameter, name=name):
                    f = self.function("def helper(): return 1\n" + variant + "\n", name=name)
                    self.assertEqual(f["scope_resolution"], "compiler_symtable")
                    self.assertEqual(f["calls"][0]["resolution"], "unresolved")
                    self.assertNotIn("target", f["calls"][0])
                    self.assertEqual(f["cfg"]["status"], "supported")
                    self.assertNotIn("helper", f["dfg"]["local_variables"])
                    self.assertTrue(any(use["variable"] == "helper" and
                                        use["reason"] == "free_variable_environment_not_modeled"
                                        for use in f["dfg"]["unresolved_uses"]))

    def test_type_parameters_do_not_leak_into_sibling_function_scope(self):
        f = self.function("""
            def helper(): return 1
            def other[helper](): return helper()
            class C[helper]:
                def method(self): return helper()
            def f(): return helper()
        """)
        self.assertEqual(f["calls"][0]["resolution"], "candidate")

    def test_nested_definition_time_bindings_shadow_local_and_enclosing_calls(self):
        declarations = [
            "def nested(value=(helper := external)):\n    pass",
            "def nested(*, value=(helper := external)):\n    pass",
            "@(helper := decorator)\ndef nested():\n    pass",
            "class Nested((helper := Base)):\n    pass",
            "class Nested(metaclass=(helper := Meta)):\n    pass",
            "@(helper := decorator)\nclass Nested:\n    pass",
            "nested = lambda value=(helper := external): value",
            "nested = lambda *, value=(helper := external): value",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                source = ("def helper(): return 1\ndef f():\n"
                          + textwrap.indent(declaration, "    ") + "\n    return helper()\n")
                f = self.function(source)
                self.assertEqual(f["calls"][-1]["resolution"], "unresolved")
                self.assertNotIn("target", f["calls"][-1])
                source = ("def helper(): return 1\ndef outer():\n"
                          + textwrap.indent(declaration, "    ")
                          + "\n    def f(): return helper()\n    return f\n")
                f = self.function(source, name="outer.<locals>.f")
                self.assertEqual(f["calls"][0]["resolution"], "unresolved")

    def test_nested_body_and_class_local_bindings_do_not_shadow_outer_calls(self):
        declarations = [
            "def nested():\n    helper = external",
            "nested = lambda: (helper := external)",
            "class Nested:\n    helper = external",
            "class Nested:\n    def method(value=(helper := external)):\n        pass",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                source = ("def helper(): return 1\ndef f():\n"
                          + textwrap.indent(declaration, "    ") + "\n    return helper()\n")
                f = self.function(source)
                self.assertEqual(f["calls"][-1]["resolution"], "candidate")
        f = self.function("""
            def helper(): return 1
            class C:
                helper = external
                def f(self): return helper()
        """, name="C.f")
        self.assertEqual(f["calls"][0]["resolution"], "candidate")

    def test_unevaluated_annotation_walrus_is_lexical_local_or_invalid_by_version(self):
        source = "def helper(): return 1\ndef f():\n    value: (helper := external)\n    return helper()\n"
        if sys.version_info >= (3, 14):
            result = self.analyze(source)
            self.assertEqual(result["parse_status"], "parse_failed")
            self.assertEqual(result["diagnostics"][0]["kind"], "SyntaxError")
        else:
            f = self.function(source)
            self.assertEqual(f["cfg"]["status"], "supported")
            self.assertEqual(f["calls"][0]["resolution"], "unresolved")
            self.assertIn("helper", f["dfg"]["local_variables"])
            self.assertTrue(self.unbound(f, self.node(f, "return"), "helper"))
            self.assertEqual(self.node(f, "annassign")["defs"], [])
            self.assertEqual(self.node(f, "annassign")["uses"], [])

    def test_nested_function_annotation_walrus_binding_is_version_aware(self):
        source = """
            def helper(): return 1
            def outer():
                def declaration(value: (helper := external)):
                    pass
                def f(): return helper()
                return f
        """
        if sys.version_info >= (3, 14):
            result = self.analyze(source)
            self.assertEqual(result["parse_status"], "parse_failed")
            self.assertEqual(result["diagnostics"][0]["kind"], "SyntaxError")
        else:
            f = self.function(source, name="outer.<locals>.f")
            self.assertEqual(f["calls"][0]["resolution"], "unresolved")

    def test_comprehension_calls_cannot_reuse_enclosing_global_binding(self):
        f = self.function("""
            def helper(): return 1
            def f(items):
                helper()
                return [helper() for helper in items]
        """)
        self.assertEqual([call["resolution"] for call in f["calls"]], ["candidate", "unresolved"])
        self.assertEqual(f["calls"][1]["reason"], "nested_expression_scope_unresolved")

    def test_type_alias_values_are_not_enclosing_body_call_sites(self):
        for parameters in ("", "[helper]", "[*helper]", "[**helper]"):
            with self.subTest(parameters=parameters):
                f = self.function("def helper(): return 1\ndef f():\n    type Alias" + parameters
                                  + " = helper()\n    return Alias\n")
                self.assertEqual(f["calls"], [])
                self.assertEqual(f["cfg"]["status"], "unsupported")
                self.assertTrue(any(u["detail"] == "TypeAlias" for u in f["unknowns"]))

    def test_missing_or_ambiguous_compiler_function_scope_fails_closed(self):
        original = analysis._function_scopes

        def duplicate(table):
            return {key: scopes * 2 for key, scopes in original(table).items()}

        for replacement in (lambda table: {}, duplicate):
            with self.subTest(replacement=replacement):
                with patch.object(analysis, "_function_scopes", side_effect=replacement):
                    f = self.function("def helper(): return 1\ndef f(): return helper()\n")
                self.assertEqual(f["scope_resolution"], "unresolved")
                self.assertEqual(f["cfg"]["status"], "unsupported")
                self.assertEqual(f["dfg"]["edges"], [])
                self.assertEqual(f["calls"][0]["resolution"], "unresolved")

    def test_class_private_spelling_mismatch_refuses_dfg_without_guessing_mangling(self):
        variants = [
            ("class C:\n    def f(self, __value): return __value", "C.f"),
            ("class C:\n    def f(self):\n        __value = 1\n        return __value", "C.f"),
            ("class C:\n    def outer(self):\n        def f(__value): return __value\n        return f", "C.outer.<locals>.f"),
            ("class C:\n    def f(self):\n        import external as __value\n        return 1", "C.f"),
        ]
        for source, name in variants:
            with self.subTest(name=name, source=source):
                f = self.function(source, name=name)
                self.assertEqual(f["cfg"]["status"], "unsupported")
                self.assertEqual(f["dfg"]["edges"], [])
                self.assertTrue(any("private-name spelling mismatch" in u["detail"] for u in f["unknowns"]))
        for source, name in [("def f(__value): return __value", "f"),
                             ("class ___:\n    def f(self, __value): return __value", "___.f"),
                             ("class C:\n    def f(self, __value__): return __value__", "C.f")]:
            with self.subTest(name=name):
                f = self.function(source, name=name)
                self.assertEqual(f["cfg"]["status"], "supported")

    def test_compiler_mangled_declared_global_suppresses_module_candidate(self):
        f = self.function("""
            def _C__helper(): return 1
            class C:
                def mutate(self):
                    global __helper
                    __helper = lambda: 2
            def f(): return _C__helper()
        """)
        self.assertEqual(f["calls"][0]["resolution"], "unresolved")

    def test_import_alias_is_local_binding_and_import_provenance_is_syntactic(self):
        f = self.function("""
            def f():
                import external.module as local
                return local.call()
        """)
        self.assertEqual(self.definitions(f, self.node(f, "return"), "local"), {self.node(f, "import")["id"]})
        result = self.analyze("from .helpers import stop as halt\n")
        self.assertEqual(result["imports"][0]["module"], "helpers")
        self.assertEqual(result["imports"][0]["level"], 1)

    def test_source_locations_use_utf8_byte_columns(self):
        f = self.function("def f(値):\n    値 = 値 + 1\n    return 値\n")
        node = self.node(f, "assign")
        self.assertEqual(node["location"], {"line": 2, "column": 4, "end_line": 2, "end_column": 17})

    def test_parser_errors_and_invalid_control_flow_do_not_look_successful(self):
        for source in (b"def invalid(:\n", b"def f():\n    continue\n", b"\xffinvalid\n", b"x = '\x00'\n"):
            with self.subTest(source=source):
                result = analysis.analyze_python("broken.py", source)
                self.assertEqual(result["parse_status"], "parse_failed")
                self.assertEqual(result["functions"], [])
                self.assertTrue(result["diagnostics"])

    def test_source_module_is_never_executed(self):
        result = self.analyze("raise RuntimeError('must not execute')\ndef f(): return 1\n")
        self.assertEqual(result["parse_status"], "analyzed")
        self.assertEqual(len(result["functions"]), 1)

    def test_deterministic_graph_has_no_absolute_path_or_timestamp(self):
        source = "def f(items):\n    for item in items:\n        result = item\n    return result\n"
        first, second = self.analyze(source), self.analyze(source)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertNotIn(str(REPO), json.dumps(first))

    def test_function_cfg_limit_is_explicit_unsupported(self):
        with patch.object(analysis, "MAX_CFG_NODES", 4):
            f = self.function("def f(x):\n    value = x\n    return value\n")
        self.assertEqual(f["cfg"]["status"], "unsupported")
        self.assertEqual(f["cfg"]["edges"], [])
        self.assertTrue(any(u["kind"] == "analysis_limit" for u in f["unknowns"]))


if __name__ == "__main__":
    unittest.main()
