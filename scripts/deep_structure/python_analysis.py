"""Read-only Python syntax, normal-flow CFG, and local-binding DFG candidates.

This is deliberately not a Python interpreter, a safety proof, or a PDG.  A
supported graph describes statement-level control flow on successful expression
evaluation, plus explicit return/raise.  Calls, implicit exceptions, heap state,
short-circuit expression control, and predicate feasibility remain unknown.
Unsupported syntax rejects the whole function graph rather than inventing a
fallthrough edge through a cleanup/exception boundary.
"""

import ast
from collections import Counter, deque
import warnings


MAX_CFG_NODES = 1200
MAX_FUNCTION_AST_NODES = 20000
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = _FUNCTIONS + (ast.ClassDef, ast.Lambda)


def _location(node):
    """Lines are 1-based; columns are 0-based UTF-8 byte offsets (CPython AST)."""
    return {
        "line": node.lineno,
        "column": node.col_offset,
        "end_line": node.end_lineno,
        "end_column": node.end_col_offset,
    }


def _own_walk(statements):
    """Include nested scope declarations, but never their bodies or defaults."""
    pending = list(reversed(statements))
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, ast.AnnAssign):
            # Function-local annotations are not evaluated. Their names/calls
            # must not become body reads/call candidates. The target still
            # declares a local even when no value is present.
            children = [node.target] + ([node.value] if node.value is not None else [])
            pending.extend(reversed(children))
        elif not isinstance(node, _SCOPES):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _arguments(function):
    args = function.args
    return [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs] + [
        a.arg for a in (args.vararg, args.kwarg) if a is not None
    ]


def _bound_names(node):
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return {node.id}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {alias.asname or (alias.name.split(".")[0]
                                if isinstance(node, ast.Import) else alias.name)
                for alias in node.names}
    if isinstance(node, _FUNCTIONS + (ast.ClassDef,)):
        return {node.name}
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        return {node.name}
    if isinstance(node, ast.MatchMapping) and node.rest:
        return {node.rest}
    return set()


def _bindings(nodes):
    return {name for node in nodes for name in _bound_names(node)}


def _reads(*nodes):
    return sorted({child.id for node in nodes if node is not None
                   for child in ast.walk(node)
                   if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)})


def _callee(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return (_callee(node.value) + "." + node.attr)[-240:]
    return "<" + type(node).__name__ + ">"


def _index_functions(tree, path):
    result = []

    def visit(node, prefix="", enclosing=()):
        if isinstance(node, _FUNCTIONS):
            qualname = prefix + node.name
            result.append((node, qualname, enclosing))
            for statement in node.body:
                visit(statement, qualname + ".<locals>.", enclosing + (node,))
            return
        if isinstance(node, ast.ClassDef):
            for statement in node.body:
                visit(statement, prefix + node.name + ".", enclosing)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, prefix, enclosing)

    visit(tree)
    return sorted(result, key=lambda item: (item[0].lineno, item[0].col_offset, item[1]))


def _module_candidates(tree, path):
    # Count lexical module bindings even inside conditionals. A candidate is
    # still not a runtime dispatch claim: Python globals can be monkey-patched.
    # Definition defaults/decorators and class bases run in the surrounding
    # scope, unlike their bodies. Module annotations can also bind names;
    # function-local annotations deliberately do not (see _own_walk).
    counts = Counter()
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        counts.update(_bound_names(node))
        if isinstance(node, _FUNCTIONS + (ast.Lambda,)):
            args = node.args
            pending.extend(args.defaults)
            pending.extend(default for default in args.kw_defaults if default is not None)
            # Deferred annotations are conservatively treated as potential
            # bindings here; this can omit a candidate, never invent dispatch.
            pending.extend(arg.annotation for arg in args.posonlyargs + args.args + args.kwonlyargs
                           if arg.annotation is not None)
            pending.extend(arg.annotation for arg in (args.vararg, args.kwarg)
                           if arg is not None and arg.annotation is not None)
            if isinstance(node, _FUNCTIONS):
                pending.extend(node.decorator_list)
                if node.returns is not None:
                    pending.append(node.returns)
        elif isinstance(node, ast.ClassDef):
            pending.extend(node.decorator_list + node.bases + node.keywords)
        else:
            pending.extend(ast.iter_child_nodes(node))
    if "*" in counts:
        return {}
    global_names = {name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names}
    return {node.name: f"{path}::{node.name}@{node.lineno}"
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and counts[node.name] == 1
            and node.name not in global_names and not node.decorator_list}


def _call_sites(nodes, candidates, shadowed):
    calls = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        expression = _callee(node.func)
        name = node.func.id if isinstance(node.func, ast.Name) else None
        target = candidates.get(name) if name not in shadowed else None
        item = {
            "location": _location(node), "expression": expression,
            "callee_name": name, "resolution": "candidate" if target else "unresolved",
            "reason": ("unique_unshadowed_module_definition_not_runtime_dispatch"
                       if target else "dynamic_external_or_shadowed_call"),
        }
        if target:
            item["target"] = target
        calls.append(item)
    return sorted(calls, key=lambda call: (call["location"]["line"],
                                         call["location"]["column"], call["expression"]))


def _unknown(kind, node, detail):
    return {"kind": kind, "location": _location(node), "detail": detail}


def _unsupported(function, nodes):
    reasons = []
    statements = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Pass,
                  ast.Import, ast.ImportFrom, ast.If, ast.While, ast.For,
                  ast.Break, ast.Continue, ast.Return, ast.Raise)
    expressions = (ast.Await, ast.Yield, ast.YieldFrom, ast.Lambda, ast.ListComp,
                   ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.NamedExpr)
    if isinstance(function, ast.AsyncFunctionDef):
        reasons.append(_unknown("unsupported_syntax", function, "AsyncFunctionDef"))
    for node in nodes:
        reason = None
        if isinstance(node, ast.stmt) and not isinstance(node, statements):
            reason = type(node).__name__
        elif isinstance(node, expressions):
            reason = type(node).__name__
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], (ast.Name, ast.Attribute, ast.Subscript)):
                reason = "chained_or_unpacking_assignment"
        elif isinstance(node, ast.AnnAssign) and not isinstance(node.target, ast.Name):
            reason = "non_name_annotated_assignment"
        elif isinstance(node, ast.AugAssign) and not isinstance(node.target, (ast.Name, ast.Attribute, ast.Subscript)):
            reason = "non_simple_augmented_assignment"
        elif isinstance(node, ast.For) and not isinstance(node.target, ast.Name):
            reason = "unpacking_for_target"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "exec", "eval", "locals", "globals", "vars",
        }:
            reason = "dynamic_namespace:" + node.func.id
        if reason:
            reasons.append(_unknown("unsupported_syntax", node, reason))
    if len(nodes) > MAX_FUNCTION_AST_NODES:
        reasons.append(_unknown("analysis_limit", function, "function AST node budget exceeded"))
    return reasons


class _GraphLimit(Exception):
    pass


class _CFG:
    def __init__(self, function, function_id):
        self.function = function
        self.function_id = function_id
        self.nodes = []
        self.edges = []
        self.count = 0
        self.entry = self.add("entry", function, defs=_arguments(function), suffix="entry", synthetic=True)
        self.exit = self.add("exit", function, suffix="exit", synthetic=True)
        self.raise_exit = self.add("raise_exit", function, suffix="raise_exit", synthetic=True)

    def add(self, kind, source, uses=(), defs=(), suffix=None, synthetic=False):
        if len(self.nodes) >= MAX_CFG_NODES:
            raise _GraphLimit("function CFG node budget exceeded")
        self.count += 1
        node_id = self.function_id + "#" + (suffix or f"n{self.count:04d}")
        self.nodes.append({
            "id": node_id, "kind": kind, "location": _location(source),
            "uses": sorted(set(uses)), "defs": sorted(set(defs)), "synthetic": synthetic,
        })
        return node_id

    def edge(self, start, end, kind):
        self.edges.append({"from": start, "to": end, "kind": kind})

    def attach(self, incoming, target):
        for start, kind in incoming:
            self.edge(start, target, kind)

    def block(self, statements, incoming, loop=None):
        frontier = incoming
        for statement in statements:
            frontier = self.statement(statement, frontier, loop)
        return frontier

    def statement(self, statement, incoming, loop):
        if isinstance(statement, ast.If):
            test = self.add("if", statement.test, uses=_reads(statement.test))
            self.attach(incoming, test)
            return (self.block(statement.body, [(test, "true")], loop)
                    + self.block(statement.orelse, [(test, "false")], loop))
        if isinstance(statement, (ast.While, ast.For)):
            if isinstance(statement, ast.While):
                test = self.add("while", statement.test, uses=_reads(statement.test))
                self.attach(incoming, test)
                body_incoming = [(test, "true")]
            else:
                iterator = self.add("for_iter", statement.iter, uses=_reads(statement.iter))
                self.attach(incoming, iterator)
                test = self.add("for_next", statement.iter, synthetic=True)
                self.edge(iterator, test, "next")
                target = self.add("for_target", statement.target, defs=[statement.target.id])
                self.edge(test, target, "iteration")
                body_incoming = [(target, "next")]
            after = self.add("loop_exit", statement, synthetic=True)
            normal = self.block(statement.body, body_incoming, (after, test))
            for start, kind in normal:
                self.edge(start, test, "loop_back" if kind == "next" else kind)
            otherwise = self.block(statement.orelse, [(test, "false" if isinstance(statement, ast.While) else "exhausted")], loop)
            self.attach(otherwise, after)
            return [(after, "next")]
        if isinstance(statement, (ast.Break, ast.Continue)):
            node = self.add("break" if isinstance(statement, ast.Break) else "continue", statement)
            self.attach(incoming, node)
            if loop is None:
                # compile() validates this, retained as a fail-closed invariant.
                raise _GraphLimit("loop control outside a supported loop")
            self.edge(node, loop[0 if isinstance(statement, ast.Break) else 1],
                      "break" if isinstance(statement, ast.Break) else "continue")
            return []
        if isinstance(statement, ast.Return):
            node = self.add("return", statement, uses=_reads(statement.value))
            self.attach(incoming, node)
            self.edge(node, self.exit, "return")
            return []
        if isinstance(statement, ast.Raise):
            node = self.add("raise", statement, uses=_reads(statement.exc, statement.cause))
            self.attach(incoming, node)
            self.edge(node, self.raise_exit, "raise")
            return []
        uses, defs = [], []
        if isinstance(statement, ast.Assign):
            target = statement.targets[0]
            uses = _reads(statement.value, target)
            defs = [target.id] if isinstance(target, ast.Name) else []
        elif isinstance(statement, ast.AnnAssign):
            # A function-local annotation is not evaluated, and an annotation
            # without a value does not bind its target at runtime.
            uses = _reads(statement.value)
            defs = [statement.target.id] if statement.value is not None else []
        elif isinstance(statement, ast.AugAssign):
            uses = _reads(statement.value, statement.target)
            if isinstance(statement.target, ast.Name):
                uses = sorted(set(uses) | {statement.target.id})
                defs = [statement.target.id]
        elif isinstance(statement, (ast.Import, ast.ImportFrom)):
            defs = sorted(_bindings([statement]))
        elif isinstance(statement, ast.Expr):
            uses = _reads(statement.value)
        node = self.add(type(statement).__name__.lower(), statement, uses=uses, defs=defs)
        self.attach(incoming, node)
        return [(node, "next")]

    def build(self):
        self.attach(self.block(self.function.body, [(self.entry, "next")]), self.exit)
        outgoing = {}
        for edge in self.edges:
            outgoing.setdefault(edge["from"], []).append(edge["to"])
        reachable, pending = set(), [self.entry]
        while pending:
            item = pending.pop()
            if item not in reachable:
                reachable.add(item)
                pending.extend(outgoing.get(item, []))
        for node in self.nodes:
            node["reachable"] = node["id"] in reachable
        return {
            "status": "supported", "model": "statement_normal_completion_with_explicit_raise",
            "entry": self.entry, "exit": self.exit, "raise_exit": self.raise_exit,
            "nodes": self.nodes,
            "edges": sorted(self.edges, key=lambda e: (e["from"], e["to"], e["kind"])),
        }


def _dfg(cfg, local_variables, arguments):
    nodes = {node["id"]: node for node in cfg["nodes"] if node["reachable"]}
    predecessors = {key: [] for key in nodes}
    successors = {key: [] for key in nodes}
    for edge in cfg["edges"]:
        if edge["from"] in nodes and edge["to"] in nodes:
            predecessors[edge["to"]].append(edge["from"])
            successors[edge["from"]].append(edge["to"])
    # None is a real unbound possibility, not an omitted incoming definition.
    initial = {name: frozenset({cfg["entry"] if name in arguments else None})
               for name in local_variables}
    incoming, outgoing = {}, {}
    queue, queued = deque([cfg["entry"]]), {cfg["entry"]}
    while queue:
        node_id = queue.popleft()
        queued.remove(node_id)
        if node_id == cfg["entry"]:
            before = initial
        else:
            parents = [outgoing[p] for p in predecessors[node_id] if p in outgoing]
            before = {name: frozenset().union(*(p[name] for p in parents))
                      for name in local_variables}
        after = dict(before)
        for name in nodes[node_id]["defs"]:
            after[name] = frozenset({node_id})
        incoming[node_id] = before
        if after != outgoing.get(node_id):
            outgoing[node_id] = after
            for child in successors[node_id]:
                if child not in queued:
                    queue.append(child)
                    queued.add(child)
    edges, unresolved = [], []
    for node in cfg["nodes"]:
        if not node["reachable"]:
            continue
        for name in node["uses"]:
            if name not in local_variables:
                unresolved.append({"node": node["id"], "variable": name,
                                   "reason": "nonlocal_global_or_builtin"})
                continue
            for definition in sorted(incoming[node["id"]][name], key=lambda value: value or ""):
                if definition is None:
                    unresolved.append({"node": node["id"], "variable": name,
                                       "reason": "possibly_unbound_local"})
                else:
                    edges.append({"from": definition, "to": node["id"], "variable": name})
    return {
        "status": "supported", "model": "local_name_may_reaching_definitions",
        "local_variables": sorted(local_variables), "nodes": [node["id"] for node in cfg["nodes"]],
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"], e["variable"])),
        "unresolved_uses": sorted(unresolved, key=lambda u: (u["node"], u["variable"], u["reason"])),
    }


def _analyze_function(function, qualname, enclosing, path, candidates):
    function_id = f"{path}::{qualname}@{function.lineno}"
    nodes = list(_own_walk(function.body))
    arguments = set(_arguments(function))
    locals_ = _bindings(nodes) | arguments
    shadowed = set(locals_)
    for outer in enclosing:
        shadowed.update(_bindings(_own_walk(outer.body)))
        shadowed.update(_arguments(outer))
    for node in nodes:
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            shadowed.update(node.names)
    calls = _call_sites(nodes, candidates, shadowed)
    unknowns = _unsupported(function, nodes)
    if unknowns:
        cfg = {"status": "unsupported", "nodes": [], "edges": []}
        dfg = {"status": "unsupported", "nodes": [], "edges": [], "unresolved_uses": []}
    else:
        try:
            cfg = _CFG(function, function_id).build()
            dfg = _dfg(cfg, locals_, arguments)
        except _GraphLimit as error:
            unknowns.append(_unknown("analysis_limit", function, str(error)))
            cfg = {"status": "unsupported", "nodes": [], "edges": []}
            dfg = {"status": "unsupported", "nodes": [], "edges": [], "unresolved_uses": []}
    unknowns.append(_unknown("implicit_exceptions_not_modeled", function,
                             "Only successful expression evaluation and explicit raise are represented."))
    unknowns.append(_unknown("predicate_feasibility_not_solved", function,
                             "CFG reachability is graph reachability; branch predicates are not value-solved."))
    for types, kind, detail in [
        ((ast.Call,), "call_effects_not_modeled", "No interprocedural, call exception, or dynamic dispatch proof."),
        ((ast.Attribute, ast.Subscript), "heap_state_not_modeled", "Receiver/index Name reads only; no heap, alias, property or mutation dependencies."),
        ((ast.BoolOp, ast.IfExp, ast.Compare), "expression_control_opaque", "Expression reads are may-reads; short-circuit and expression-level control are not CFG nodes."),
        ((ast.For,), "iteration_value_not_modeled", "Iterator is evaluated once; element binding is represented, iterator value provenance is not."),
        ((ast.AugAssign,), "in_place_effects_not_modeled", "Local binding update is represented; in-place heap mutation is not."),
    ]:
        first = next((node for node in nodes if isinstance(node, types)), None)
        if first:
            unknowns.append(_unknown(kind, first, detail))
    if enclosing:
        unknowns.append(_unknown("closure_environment_not_modeled", function,
                                 "Outer-scope values remain unresolved; nested function body is indexed independently."))
    if function.decorator_list:
        unknowns.append(_unknown("decorator_dispatch_not_modeled", function,
                                 "Graph describes the lexical function body, not decorator-generated runtime behavior."))
    unknowns.sort(key=lambda u: (u["location"]["line"], u["location"]["column"], u["kind"], u["detail"]))
    return {
        "id": function_id, "name": function.name, "qualname": qualname, "path": path,
        "location": _location(function), "calls": calls, "unknowns": unknowns,
        "cfg": cfg, "dfg": dfg, "pdg": {"status": "not_implemented"},
        "metrics": {
            "lines": function.end_lineno - function.lineno + 1,
            "statements": sum(isinstance(node, ast.stmt) for node in nodes),
            "branches": sum(isinstance(node, (ast.If, ast.While, ast.For, ast.Try, ast.Match)) for node in nodes),
            "calls": len(calls), "candidate_calls": sum(call["resolution"] == "candidate" for call in calls),
            "cfg_nodes": len(cfg["nodes"]), "cfg_edges": len(cfg["edges"]), "dfg_edges": len(dfg["edges"]),
        },
    }


def analyze_python(path, source):
    """Analyze bytes without importing or executing the analyzed module.

    compile() below checks context-sensitive syntax (e.g. an invalid continue)
    without executing its code object. Python's encoding declaration is honored.
    Syntax/parser resource errors are evidence, never an empty successful graph.
    """
    base = {"parse_status": "analyzed", "imports": [], "functions": [], "diagnostics": [],
            "location_convention": "1-based lines; 0-based UTF-8 byte columns; end-exclusive"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=path, type_comments=True)
            compile(tree, path, "exec", dont_inherit=True, optimize=0)
    except (SyntaxError, UnicodeError, ValueError, RecursionError, MemoryError) as error:
        base["parse_status"] = "parse_failed"
        base["diagnostics"] = [{"kind": type(error).__name__,
                                "line": getattr(error, "lineno", None),
                                "column": getattr(error, "offset", None),
                                "detail": (getattr(error, "msg", None) or type(error).__name__)}]
        return base
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            base["imports"].append({
                "module": node.module if isinstance(node, ast.ImportFrom) else None,
                "level": node.level if isinstance(node, ast.ImportFrom) else 0,
                "names": [{"name": alias.name, "asname": alias.asname} for alias in node.names],
                "location": _location(node), "resolution": "syntactic_import_only",
            })
    base["imports"].sort(key=lambda i: (i["location"]["line"], i["location"]["column"]))
    candidates = _module_candidates(tree, path)
    base["functions"] = [_analyze_function(function, qualname, enclosing, path, candidates)
                         for function, qualname, enclosing in _index_functions(tree, path)]
    return base
