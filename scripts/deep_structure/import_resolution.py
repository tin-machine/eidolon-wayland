"""Conservative repository import candidates, never Python runtime import resolution.

Only top-level unconditional imports and repository source layout are inspected.
The repository is not executed, nor is its layout asserted to be sys.path.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import PurePosixPath
import symtable

from .python_analysis import _bound_names, _module_candidates, _symbol_tables


def _source(row, revision):
    return {"revision": revision, "path": row["path"],
            "git_blob": row["git_blob"], "sha256": row["sha256"]}


def _module_path(importer, name, level, paths, analyzed):
    """Find a unique on-disk *candidate*, never prove a deployment import root."""
    if not name:
        return None, "empty_or_star_module_unsupported", [], None
    parts = name.split(".")
    if any(not part.isidentifier() for part in parts):
        return None, "invalid_module_name", [], None
    parent = PurePosixPath(importer).parent
    rule = "same_directory_hypothetical_import_root_not_sys_path_proof"
    if level:
        rule = "relative_import_verified_init_chain_not_runtime_dispatch"
        if level > len(parent.parts):
            return None, "relative_import_beyond_repository_root", [], rule
        for index in range(level):
            if str(parent / "__init__.py") not in analyzed:
                return None, "namespace_or_missing_package_init", [], rule
            if index < level - 1:
                parent = parent.parent
    else:
        # Other directory layouts can supply the same absolute module name at
        # runtime. Refuse conflicting tracked module basenames, even if excluded.
        competing = sorted(p for p in paths
                           if p.endswith("/" + parts[-1] + ".py")
                           or p == parts[-1] + ".py"
                           or p.endswith("/" + parts[-1] + "/__init__.py")
                           or p == parts[-1] + "/__init__.py")
        if len(competing) > 1:
            return None, "ambiguous_repository_module_basename", competing, rule
    for part in parts[:-1]:
        parent = parent / part
        if str(parent / "__init__.py") not in analyzed:
            return None, "namespace_or_missing_package_init", [], rule
    candidates = sorted({str(parent / (parts[-1] + ".py")),
                         str(parent / parts[-1] / "__init__.py")} & paths)
    if not candidates:
        return None, "outside_repository_or_missing_module", [], rule
    if len(candidates) != 1:
        return None, "ambiguous_module_file_or_package", candidates, rule
    if candidates[0] not in analyzed:
        return None, "target_source_excluded_or_parse_failed", candidates, rule
    return candidates[0], "repository_source_candidate_not_runtime_dispatch", candidates, rule


def _rebindings(tree, names):
    """Over-approximate writes: false negatives preferred to invented edges.

    Function locals with the same spelling can also block a binding. Function
    call scope is checked separately using the compiler, not this AST scan.
    """
    rebound = set()
    direct_import_ids = {id(node) for node in tree.body
                         if isinstance(node, (ast.Import, ast.ImportFrom))}
    for node in ast.walk(tree):
        # The original unconditional import is the candidate's definition, not
        # a mutation. All other bindings, even in an opaque scope, block it.
        if id(node) not in direct_import_ids:
            rebound.update(_bound_names(node) & names)
        if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names):
            rebound.update(names)
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Name) and node.value.id in names):
            rebound.add(node.value.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
                "exec", "eval", "globals", "locals", "vars", "setattr", "delattr"}:
            # Dynamic namespace mutation cannot be ruled out by static analysis.
            rebound.update(names)
    return rebound


def resolve_imports(files, modules, functions, sources, revision):
    """Annotate import records and call sites with bounded, reproducible evidence."""
    file_rows = {row["path"]: row for row in files}
    paths = {path for path, row in file_rows.items() if path.endswith(".py")}
    analyzed = {module["path"] for module in modules
                if file_rows[module["path"]]["analysis_status"] == "analyzed"}
    trees, exports = {}, {}
    for path in sorted(analyzed):
        tree = ast.parse(sources[path], filename=path, type_comments=True)
        trees[path] = tree
        table = symtable.symtable(sources[path], path, "exec")
        globals_ = {sym.get_name() for scope in _symbol_tables(table)
                    for sym in scope.get_symbols() if sym.is_declared_global()}
        exports[path] = _module_candidates(tree, path, globals_)
    module_by_path = {module["path"]: module for module in modules}
    functions_by_path = defaultdict(list)
    for function in functions:
        functions_by_path[function["path"]].append(function)
    totals = Counter()
    for path in sorted(analyzed):
        module = module_by_path[path]
        tree = trees[path]
        direct = {(node.lineno, node.col_offset): node for node in tree.body
                  if isinstance(node, (ast.Import, ast.ImportFrom))}
        bindings = []
        for record in module["imports"]:
            loc = record["location"]
            site = (loc["line"], loc["column"])
            node = direct.get(site)
            for alias in record["names"]:
                name = alias["name"]
                bound = (alias["asname"] or name.split(".")[0]
                         if record["module"] is None else alias["asname"] or name)
                binding = {"source": _source(file_rows[path], revision),
                           "import_location": loc,
                           "scope": "module_unconditional" if node else "unsupported_nested_or_conditional",
                           "bound_name": bound, "imported_name": name,
                           "imported_module": record["module"] if record["module"] is not None else name,
                           "level": record["level"], "resolution": "unresolved"}
                if node is None:
                    binding["reason"] = "conditional_try_local_or_nested_import_unsupported"
                elif isinstance(node, ast.ImportFrom) and (name == "*" or node.module is None):
                    binding["reason"] = "star_or_package_member_import_unsupported"
                elif isinstance(node, ast.Import) and "." in name and not alias["asname"]:
                    binding["reason"] = "dotted_import_without_asname_unsupported"
                elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    binding["reason"] = "future_import_not_repository_dependency"
                else:
                    modname = node.module if isinstance(node, ast.ImportFrom) else name
                    target, reason, options, rule = _module_path(path, modname,
                                                                  record["level"], paths, analyzed)
                    binding.update(reason=reason, import_root_rule=rule, module_candidates=options)
                    if target:
                        binding["module_path"] = target
                        binding["module_source"] = _source(file_rows[target], revision)
                        if isinstance(node, ast.Import):
                            binding["resolution"] = "module_candidate"
                        else:
                            target_id = exports[target].get(name)
                            if target_id:
                                function = next((f for f in functions_by_path[target]
                                                 if f["id"] == target_id), None)
                                if function:
                                    binding.update(resolution="function_candidate", target=target_id,
                                                   target_location=function["location"])
                            if binding["resolution"] == "unresolved":
                                binding["reason"] = "imported_name_not_unique_undecorated_function"
                bindings.append(binding)
        counts = Counter(b["bound_name"] for b in bindings if b["scope"] == "module_unconditional")
        rebound = _rebindings(tree, set(counts))
        for binding in bindings:
            if binding["scope"] == "module_unconditional" and counts[binding["bound_name"]] != 1:
                binding.update(resolution="unresolved", reason="multiple_import_bindings_for_name")
                binding.pop("target", None)
                binding.pop("target_location", None)
            if binding["bound_name"] in rebound:
                if binding["resolution"] == "unresolved":
                    binding["additional_boundaries"] = ["possible_alias_rebinding_or_dynamic_namespace"]
                else:
                    binding.update(resolution="unresolved", reason="possible_alias_rebinding_or_dynamic_namespace")
                    binding.pop("target", None)
                    binding.pop("target_location", None)
            totals[binding["resolution"]] += 1
        # Only module-body imports are lookup bindings. Nested import records
        # remain evidence and can invalidate aliases via _rebindings, but must
        # not erase the module-level binding's reason/provenance.
        index = {name: bs[0] for name, bs in
                 ((name, [b for b in bindings if b["bound_name"] == name
                          and b["scope"] == "module_unconditional"]) for name in counts)
                 if len(bs) == 1}
        for function in functions_by_path[path]:
            for call in function["calls"]:
                if call["resolution"] == "candidate":
                    call["candidate_kind"] = "same_module_function"
                    continue
                alias = call.get("import_root")
                if not alias or alias not in index:
                    continue
                binding = index[alias]
                evidence = {k: binding[k] for k in ("source", "import_location", "scope", "bound_name",
                           "imported_name", "imported_module", "level", "resolution", "reason")}
                for key in ("module_path", "module_source", "module_candidates", "import_root_rule",
                            "target", "target_location"):
                    if key in binding:
                        evidence[key] = binding[key]
                call["import_evidence"] = evidence
                if call.get("import_root_binding") != "compiler_global":
                    call["import_evidence"]["call_boundary"] = "non_global_or_ambiguous_compiler_scope"
                    continue
                # No receiver typing, chained attribute, or runtime dispatch claim.
                expression = call["expression"]
                if binding["resolution"] == "module_candidate":
                    prefix = alias + "."
                    if not expression.startswith(prefix) or "." in expression[len(prefix):]:
                        call["import_evidence"]["call_boundary"] = "dynamic_receiver_or_chained_attribute"
                        continue
                    callee = expression[len(prefix):]
                    target = exports[binding["module_path"]].get(callee)
                    if target:
                        target_fn = next((f for f in functions_by_path[binding["module_path"]]
                                          if f["id"] == target), None)
                    else:
                        target_fn = None
                elif binding["resolution"] == "function_candidate" and expression == alias:
                    target_fn = next((f for f in functions_by_path[binding["module_path"]]
                                      if f["id"] == binding["target"]), None)
                else:
                    call["import_evidence"]["call_boundary"] = "import_not_function_or_unsupported_callee"
                    continue
                if target_fn:
                    call.update(resolution="candidate", reason="repository_import_candidate_not_runtime_dispatch",
                                candidate_kind="repository_import", target=target_fn["id"])
                    call["import_evidence"].update(target=target_fn["id"],
                                                   target_location=target_fn["location"],
                                                   resolution="function_candidate")
                    totals["cross_module_call_candidates"] += 1
                else:
                    call["import_evidence"]["call_boundary"] = "target_not_unique_undecorated_module_function"
        module["import_bindings"] = bindings
    return dict(sorted(totals.items()))
