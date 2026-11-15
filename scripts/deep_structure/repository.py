"""Git discovery, provenance, verification and bounded candidate graph navigation.

No target module is imported or executed. Git's committed tree is the discovery
authority; every tracked worktree blob and mode must match that tree. A successful
analysis says nothing about product correctness or test/Quest acceptance.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict, deque
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile

from .python_analysis import analyze_python


class DeepError(ValueError):
    """An invalid, changing or stale analysis input; never a successful graph."""


MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
LANGUAGES = {
    ".py": "python", ".pyi": "python_stub", ".rs": "rust",
    ".kt": "kotlin", ".kts": "kotlin", ".java": "java",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".fish": "shell",
    ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rb": "ruby",
    ".lua": "lua", ".swift": "swift", ".pl": "perl", ".cs": "csharp",
    ".vert": "shader", ".frag": "shader", ".glsl": "shader", ".wgsl": "shader",
}
GENERATED_PARTS = frozenset({"vendor", "third_party", "third-party", "node_modules",
                             "generated", "build", "target", ".gradle", ".kotlin",
                             ".venv", "venv", "__pycache__"})
TEST_PARTS = frozenset({"test", "tests", "fixture", "fixtures", "androidtest", "probes"})
KINDS = {"eidolon-repository-deep", "eidolon-targeted-deep"}
SUPPORTED_PYTHON_MINORS = {(3, 12), (3, 13), (3, 14)}


def require(condition, message):
    if not condition:
        raise DeepError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root),
                                 "-c", "core.fsmonitor=false", *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeepError("Git unavailable or timed out") from exc
    require(result.returncode == 0, "Git failed: " + " ".join(args[:2]))
    return result.stdout


def repository_root(path):
    root = Path(path).resolve()
    actual = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    require(root == actual, "--repo must name the repository root")
    return root


def profile(excludes=()):
    require(len(excludes) <= 64, "too many exclusion patterns")
    require(all(isinstance(p, str) and 0 < len(p) <= 256 for p in excludes),
            "invalid exclusion pattern")
    return {"id": "python-local-v1", "selection": "all committed Git paths; suffix or shebang",
            "excludes": sorted(set(excludes)), "exclude_syntax": "Python fnmatchcase; * includes /",
            "roles": "path heuristics, not deployment or test coverage evidence",
            "languages": LANGUAGES, "generated_parts": sorted(GENERATED_PARTS),
            "test_parts": sorted(TEST_PARTS), "max_source_bytes": MAX_SOURCE_BYTES,
            "deep_model": "Python AST; intraprocedural normal flow; local Name reaching definitions",
            "pdg": "not_implemented", "contract_graph": "not_read_or_modified"}


def _generator():
    directory = Path(__file__).resolve().parent
    files = [directory.parent / "deep_structure.py", directory / "__init__.py",
             directory / "python_analysis.py", directory / "repository.py"]
    names = ["__main__", "deep_structure", "deep_structure.python_analysis", "deep_structure.repository"]
    hashes = {}
    for path, name in zip(files, names):
        current = digest(path.read_bytes())
        module = sys.modules.get(name)
        loaded = getattr(module, "__deep_source_sha256__", None)
        if loaded is not None:
            require(loaded == current, "generator changed while loading: " + path.name)
        hashes[str(path.relative_to(directory.parent))] = current
    return {"id": "eidolon-python-deep-v1",
            "files": hashes,
            "python": {"implementation": sys.implementation.name,
                       "version": list(sys.version_info)},
            "location_columns": "zero-based UTF-8 byte offsets; end exclusive"}


# Bind the on-disk generator to the implementation actually loaded in this process.
LOADED_GENERATOR = _generator()


def generator():
    require(_generator() == LOADED_GENERATOR, "generator changed after import; restart analysis")
    return LOADED_GENERATOR


def classify(path, prefix, exclusions):
    parts = PurePosixPath(path).parts
    name = parts[-1]
    language = LANGUAGES.get(PurePosixPath(path).suffix.lower())
    reason = "recognized suffix" if language else "not recognized as source"
    if prefix.startswith(b"#!"):
        first = prefix.split(b"\n", 1)[0].decode("ascii", "replace")
        language = "python" if re.search(r"\bpython(?:\d+(?:\.\d+)*)?\b", first) else language or "shebang_other"
        reason = "interpreter shebang"
    lower_parts = {p.lower() for p in parts[:-1]}
    role = "production_candidate" if language else "non_source"
    role_reason = "source path outside test/generated heuristics" if language else "non-source path"
    if lower_parts & TEST_PARTS or name.startswith("test_") or name in {"tests.rs", "test_support.rs"}:
        role, role_reason = "test_fixture", "test/fixture/probe directory or test filename"
    generated = lower_parts & GENERATED_PARTS or name.endswith(("_pb2.py", "_pb2_grpc.py"))
    if generated:
        role, role_reason = "generated_vendor", "generated/vendor path or generated filename"
    exclusion = next((p for p in exclusions if fnmatch.fnmatchcase(path, p)), None)
    status = "eligible" if language == "python" else "unsupported" if language else "not_source"
    decision = "Python AST adapter" if status == "eligible" else "no language adapter" if language else reason
    if generated or exclusion:
        status = "excluded"
        decision = "explicit exclusion: " + exclusion if exclusion else role_reason
    return {"language": language, "role": role, "role_reason": role_reason,
            "selection_reason": reason, "analysis_status": status,
            "decision_reason": decision, "exclusion_rule": exclusion}


def _safe_path(root, name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and path.parts and all(x not in (".", "..") for x in path.parts),
            "unsafe Git path")
    current = root
    for part in path.parts[:-1]:
        current = current / part
        require(current.is_dir() and not current.is_symlink(), "symlink or missing source parent: " + name)
    return root / name


def capture(root, policy):
    """Read and fingerprint a clean tracked snapshot, without following symlinks."""
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    entries = []
    for raw in git(root, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if raw:
            meta, name = raw.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            entries.append((os.fsdecode(name), mode, kind, oid))
    entries.sort()
    expected_index = {(name, mode, oid, "0") for name, mode, _, oid in entries}
    actual_index = set()
    for raw in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if raw:
            meta, name = raw.split(b"\t", 1)
            mode, oid, stage = meta.decode().split()
            actual_index.add((os.fsdecode(name), mode, oid, stage))
    require(actual_index == expected_index, "index differs from HEAD; use a clean committed checkout")
    inventory, sources = [], {}
    for name, mode, kind, oid in entries:
        path = _safe_path(root, name)
        if mode == "160000":
            inventory.append({"path": name, "mode": mode, "git_blob": oid, "sha256": None,
                              **classify(name, b"", policy["excludes"]),
                              "analysis_status": "excluded", "decision_reason": "gitlink; not traversed"})
            continue
        try:
            info = path.lstat()
            if mode == "120000":
                require(stat.S_ISLNK(info.st_mode), "tracked symlink changed type: " + name)
                data = os.fsencode(os.readlink(path))
            else:
                require(stat.S_ISREG(info.st_mode), "non-regular tracked file: " + name)
                actual_mode = "100755" if info.st_mode & 0o111 else "100644"
                require(actual_mode == mode, "tracked executable mode changed: " + name)
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    data = stream.read()
        except OSError as exc:
            raise DeepError("missing or unreadable tracked file: " + name) from exc
        hash_type = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
        actual_blob = hash_type(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        require(actual_blob == oid, "tracked content differs from HEAD: " + name)
        row = {"path": name, "mode": mode, "git_blob": oid, "sha256": digest(data),
               **classify(name, data[:512], policy["excludes"])}
        if mode == "120000":
            row.update(analysis_status="excluded", decision_reason="symlink; not followed")
        elif row["analysis_status"] == "eligible":
            if len(data) > MAX_SOURCE_BYTES:
                row.update(analysis_status="excluded", decision_reason="source exceeds parser byte limit")
            else:
                sources[name] = data
        inventory.append(row)
    require(git(root, "rev-parse", "HEAD").decode().strip() == revision, "HEAD changed during capture")
    return {"revision": revision, "tree": tree, "files": inventory,
            "inventory_sha256": digest(encoded(inventory)),
            "source_contract": "all tracked blobs and modes match HEAD; gitlinks not traversed",
            "untracked_ignored": "outside committed analysis scope; never imported or executed"}, sources


def _location(node):
    return {"line": node.lineno, "column": node.col_offset,
            "end_line": node.end_lineno, "end_column": node.end_col_offset}


def test_references(files, sources):
    """Lexical module/path references only; deliberately not test coverage edges."""
    candidates = defaultdict(list)
    for row in files:
        if row["language"] == "python" and row["role"] == "production_candidate":
            candidates[PurePosixPath(row["path"]).name].append(row["path"])
    refs = []
    for row in files:
        if row["role"] != "test_fixture" or row["analysis_status"] != "analyzed":
            continue
        tree = ast.parse(sources[row["path"]], filename=row["path"])
        for node in ast.walk(tree):
            names, basis = [], None
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(".py"):
                names, basis = [PurePosixPath(node.value).name], "string_path_basename"
            elif isinstance(node, ast.Import):
                names, basis = [a.name.rsplit(".", 1)[-1] + ".py" for a in node.names], "import_module_basename"
            elif isinstance(node, ast.ImportFrom) and node.module:
                names, basis = [node.module.rsplit(".", 1)[-1] + ".py"], "import_module_basename"
            for name in names:
                if name in candidates:
                    refs.append({"test_path": row["path"], "location": _location(node),
                                 "candidate_paths": sorted(candidates[name]), "basis": basis,
                                 "resolution": "lexical_candidate", "tests_execution": "not_run"})
    return sorted(refs, key=lambda r: (r["test_path"], r["location"]["line"], r["location"]["column"], r["basis"]))


def summary(files, functions):
    source_files = [f for f in files if f["language"] is not None]
    deep = [f for f in functions if f["cfg"]["status"] == "supported"]
    unsupported = Counter()
    affected_functions = Counter()
    for f in functions:
        if f["cfg"]["status"] != "supported":
            blocking = [r["kind"] + ":" + r["detail"] for r in f["unknowns"]
                        if r["kind"] in {"unsupported_syntax", "analysis_limit"}]
            unsupported.update(blocking)
            affected_functions.update(set(blocking))
    return {"tracked_files": len(files), "source_candidates": len(source_files),
            "source_by_role": dict(sorted(Counter(f["role"] for f in source_files).items())),
            "source_by_language": dict(sorted(Counter(f["language"] for f in source_files).items())),
            "source_by_status": dict(sorted(Counter(f["analysis_status"] for f in source_files).items())),
            "all_files_by_status": dict(sorted(Counter(f["analysis_status"] for f in files).items())),
            "parsed_python_files": sum(f["analysis_status"] == "analyzed" for f in files),
            "files_with_supported_deep_function": len({f["path"] for f in deep}),
            "functions": len(functions), "cfg_dfg_supported_functions": len(deep),
            "cfg_dfg_unsupported_functions": len(functions) - len(deep),
            "unsupported_function_reason_occurrences": dict(sorted(unsupported.items())),
            "functions_by_blocking_reason": dict(sorted(affected_functions.items())),
            "pdg": "not_implemented"}


def build(repo, excludes=()):
    root = repository_root(repo)
    policy, identity = profile(excludes), generator()
    initial, sources = capture(root, policy)
    # Never mutate the captured fingerprint when adding analysis status.
    files = [dict(row) for row in initial["files"]]
    functions, modules = [], []
    for row in files:
        if row["analysis_status"] != "eligible":
            continue
        result = analyze_python(row["path"], sources[row["path"]])
        row["analysis_status"] = result["parse_status"]
        row["decision_reason"] = "AST parsed; per-function deep coverage below" if result["parse_status"] == "analyzed" else "Python parse failed"
        row["diagnostics"] = result.get("diagnostics", [])
        row["functions"] = len(result.get("functions", []))
        row["cfg_dfg_supported_functions"] = sum(f["cfg"]["status"] == "supported"
                                                  for f in result.get("functions", []))
        modules.append({"path": row["path"], "imports": result.get("imports", [])})
        for function in result.get("functions", []):
            function["role"] = row["role"]
            function["source"] = {"revision": initial["revision"], "path": row["path"],
                                  "git_blob": row["git_blob"], "sha256": row["sha256"]}
            functions.append(function)
    functions.sort(key=lambda f: f["id"])
    counts = summary(files, functions)
    callers = defaultdict(set)
    for function in functions:
        for call in function["calls"]:
            if call.get("target"):
                callers[call["target"]].add(function["id"])
    # Ranking is for navigation. Every automatic complexity judgment is Unknown.
    hotspots = sorted(({
        "id": f["id"], "path": f["path"], "location": f["location"],
        "role": f["role"], "cfg_status": f["cfg"]["status"],
        "call_sites": len(f["calls"]), "cfg_nodes": len(f["cfg"]["nodes"]),
        "candidate_fan_in": len(callers[f["id"]]),
        "candidate_fan_out": len({c["target"] for c in f["calls"] if c.get("target")}),
        "unresolved_call_sites": sum(c["resolution"] == "unresolved" for c in f["calls"]),
        "dfg_edges": len(f["dfg"]["edges"]),
        "source_lines": f["location"]["end_line"] - f["location"]["line"] + 1,
        "complexity_classification": "unknown", "recommendation": "source_and_contract_review_required"
    } for f in functions if f["role"] == "production_candidate"),
        key=lambda x: (-x["call_sites"], -x["cfg_nodes"], -x["source_lines"], x["id"]))
    result = {"schema": 1, "kind": "eidolon-repository-deep", "snapshot": initial,
              "generator": identity, "generator_sha256": digest(encoded(identity)),
              "profile": policy, "profile_sha256": digest(encoded(policy)),
              "files": files, "modules": modules, "functions": functions,
              "related_tests": test_references(files, sources), "summary": counts,
              "hotspots": hotspots[:100], "hotspot_ranking": "call_sites, CFG nodes, source lines; navigation only",
              "tests_execution": "not_run_by_this_tool", "contract_graph": "not_read_or_modified",
              "limits": ["candidate call graph is not runtime dispatch", "normal-flow CFG excludes implicit exceptions",
                         "DFG covers local Name bindings, not heap, alias or interprocedural effects",
                         "Rust/Kotlin/JNI/SDK/async ownership semantics are unsupported", "PDG is not implemented"]}
    final, _ = capture(root, policy)
    require(final == initial, "source snapshot changed during extraction")
    require(generator() == identity, "generator changed during extraction")
    result["artifact_sha256"] = digest(encoded(result))
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def load_artifact(path):
    try:
        path = Path(path)
        require(path.is_file() and not path.is_symlink(), "artifact must be a regular non-symlink file")
        require(path.stat().st_size <= MAX_ARTIFACT_BYTES, "artifact exceeds byte limit")
        data = json.loads(path.read_bytes(), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(DeepError("invalid JSON constant")))
        require(isinstance(data, dict), "artifact must be an object")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DeepError("corrupt or unreadable artifact") from exc


def verify(repo, artifact, excludes=()):
    require(isinstance(artifact, dict) and artifact.get("kind") == "eidolon-repository-deep"
            and artifact.get("schema") == 1, "unsupported repository artifact")
    payload = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    require(artifact.get("artifact_sha256") == digest(encoded(payload)), "artifact integrity mismatch")
    require(artifact.get("profile") == profile(excludes), "artifact profile mismatch; repeat the selection options")
    current = build(repo, excludes)
    require(current == artifact, "stale or modified artifact: source/generator/parser/profile/payload differs")
    return current


def _function_node(function):
    return {"id": function["id"], "kind": "function", "name": function["qualname"],
            "location": function["location"], "source": function["source"], "role": function["role"],
            "cfg_status": function["cfg"]["status"], "dfg_status": function["dfg"]["status"],
            "unknown_kinds": sorted({u["kind"] for u in function["unknowns"]}),
            "blocking_reasons": sorted({u["detail"] for u in function["unknowns"]
                                        if u["kind"] in {"unsupported_syntax", "analysis_limit"}})}


def bounded_slice(artifact, symbol, hops=1, max_nodes=80, max_edges=160, max_bytes=65536):
    """An undirected structural neighborhood, not a semantic/program slice.

    Call candidates are function-level edges; only the chosen function expands
    into CFG/DFG nodes. Containment is labeled explicitly, never CONTROL_DEP.
    Call verify() before using this in-memory helper against current source.
    """
    require(0 <= hops <= 3 and 1 <= max_nodes <= 200 and 0 <= max_edges <= 600
            and 1024 <= max_bytes <= 262144, "slice budget outside supported bounds")
    functions = {f["id"]: f for f in artifact["functions"]}
    matches = [f for f in functions.values() if f["id"] == symbol or
               f["path"] + "::" + f["qualname"] == symbol]
    require(len(matches) == 1, "symbol must identify exactly one function; use its full id from repository-deep")
    selected = matches[0]
    start = selected["id"]
    nodes = {key: _function_node(f) for key, f in functions.items()}
    edges = []
    for f in functions.values():
        for call in f["calls"]:
            if call.get("target") in functions:
                edges.append({"from": f["id"], "to": call["target"], "kind": "call_candidate",
                              "location": call["location"], "source": f["source"],
                              "resolution": call["resolution"]})
    for node in selected["cfg"]["nodes"]:
        nodes[node["id"]] = {**node, "source": selected["source"], "kind": "cfg_" + node["kind"]}
        edges.append({"from": start, "to": node["id"], "kind": "contains_statement"})
    for relation in ("cfg", "dfg"):
        for edge in selected[relation]["edges"]:
            edges.append({**edge, "kind": relation + ":" + edge.get("kind", "reaching_definition")})
    for ref in artifact["related_tests"]:
        if selected["path"] not in ref["candidate_paths"]:
            continue
        key = "test-reference:" + ref["test_path"] + ":" + str(ref["location"]["line"]) + ":" + str(ref["location"]["column"])
        row = next(f for f in artifact["files"] if f["path"] == ref["test_path"])
        nodes[key] = {"id": key, "kind": "test_reference_candidate", "location": ref["location"],
                      "source": {"revision": artifact["snapshot"]["revision"], "path": ref["test_path"],
                                 "git_blob": row["git_blob"], "sha256": row["sha256"]},
                      "basis": ref["basis"], "candidate_paths": ref["candidate_paths"],
                      "ambiguous": len(ref["candidate_paths"]) > 1,
                      "tests_execution": "not_run"}
        edges.append({"from": key, "to": start, "kind": "test_file_reference_candidate",
                      "resolution": "lexical_candidate_not_coverage"})
    edges = sorted({encoded(e): e for e in edges}.values(), key=encoded)
    neighbors = defaultdict(set)
    for edge in edges:
        neighbors[edge["from"]].add(edge["to"])
        neighbors[edge["to"]].add(edge["from"])
    distances, queue = {start: 0}, deque([start])
    while queue:
        key = queue.popleft()
        if distances[key] >= hops:
            continue
        for neighbor in sorted(neighbors[key]):
            if neighbor not in distances:
                distances[neighbor] = distances[key] + 1
                queue.append(neighbor)
    order = sorted(distances, key=lambda n: (distances[n], n != start, n))
    relevant = [e for e in edges if e["from"] in distances and e["to"] in distances]
    result = {"schema": 1, "kind": "eidolon-targeted-deep", "parent_artifact_sha256": artifact["artifact_sha256"],
              "revision": artifact["snapshot"]["revision"], "generator_sha256": artifact["generator_sha256"],
              "profile_sha256": artifact["profile_sha256"], "symbol": start,
              "scope": "undirected bounded candidate neighborhood; target-only CFG/DFG expansion",
              "limits": artifact["limits"], "unknowns": selected["unknowns"],
              "unresolved_data_uses": [
                  {**use, "location": next(n["location"] for n in selected["cfg"]["nodes"]
                                             if n["id"] == use["node"]),
                   "source": selected["source"]}
                  for use in selected["dfg"].get("unresolved_uses", [])],
              "unresolved_call_sites": sum(c["resolution"] == "unresolved" for c in selected["calls"]),
              "call_sites": selected["calls"], "nodes": [], "edges": [],
              "budgets": {"hops": hops, "nodes": max_nodes, "edges": max_edges, "bytes": max_bytes},
              "complete": False, "omitted_nodes": len(order), "omitted_edges": len(relevant),
              "completeness_scope": "modeled nodes and edges within hop budget, not language/semantic completeness",
              "outside_hop_edges": sum((e["from"] in distances) != (e["to"] in distances) for e in edges),
              "tests_execution": "not_run_by_this_tool", "pdg": "not_implemented"}
    # Unresolved evidence is mandatory. A too-small metadata budget fails rather
    # than dropping uncertainty to make an attractive but misleading graph fit.
    def finish():
        result["omitted_nodes"] = len(order) - len(result["nodes"])
        result["omitted_edges"] = len(relevant) - len(result["edges"])
        result["complete"] = result["omitted_nodes"] == result["omitted_edges"] == 0
    result["nodes"] = [nodes[start]]
    finish()
    require(len(encoded(result)) <= max_bytes, "slice byte budget cannot hold seed and required uncertainty metadata")
    for key in order[1:max_nodes]:
        result["nodes"].append(nodes[key])
        finish()
        if len(encoded(result)) > max_bytes:
            result["nodes"].pop()
            break
    included = {n["id"] for n in result["nodes"]}
    for edge in relevant:
        if len(result["edges"]) >= max_edges:
            break
        if edge["from"] not in included or edge["to"] not in included:
            continue
        result["edges"].append(edge)
        finish()
        if len(encoded(result)) > max_bytes:
            result["edges"].pop()
            break
    finish()
    require(len(encoded(result)) <= max_bytes, "slice metadata exceeds byte budget")
    return result


def cache_directory(root):
    name = os.fsdecode(git(root, "rev-parse", "--git-path", "eidolon-deep")).strip()
    path = Path(name)
    return path if path.is_absolute() else root / path


def write_artifact(root, result, output=None):
    cache = cache_directory(root).absolute()
    name = "repository.json" if result["kind"] == "eidolon-repository-deep" else "slice.json"
    path = Path(os.path.abspath(output)) if output else cache / name
    require(path.suffix == ".json", "artifact output must have .json suffix")
    for option in ("--git-dir", "--git-common-dir"):
        raw = Path(os.fsdecode(git(root, "rev-parse", option)).strip())
        directory = (raw if raw.is_absolute() else root / raw).absolute()
        require(not path.is_relative_to(directory) or path.is_relative_to(cache),
                "output may not overwrite Git metadata outside the analysis cache")
    # Protect both tracked source and Git internals. Only our dedicated Git cache
    # or an explicitly ignored directory may receive output inside the checkout.
    if path.is_relative_to(root) and not path.is_relative_to(cache):
        relative = str(path.relative_to(root))
        require(not relative.startswith(".git/"), "output may not overwrite Git metadata")
        tracked = git(root, "ls-files", "--", relative).strip()
        require(not tracked, "output may not overwrite tracked files")
        check = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", relative], check=False)
        require(check.returncode == 0, "output inside checkout must be in an ignored cache")
    for parent in (path, *path.parents):
        require(not parent.is_symlink(), "symlink artifact output or parent refused")
    if path.exists():
        old = load_artifact(path)
        require(old.get("kind") in KINDS, "refusing to overwrite a non-analysis file")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".deep-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(result))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     epilog="Runtime: CPython 3.12, 3.13 or 3.14; use the same interpreter for each artifact.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("repository-deep", "verify", "targeted-deep"):
        sub = subcommands.add_parser(command)
        sub.add_argument("--repo", default=".")
        sub.add_argument("--exclude", action="append", default=[])
        if command != "repository-deep":
            sub.add_argument("--artifact", required=True)
        if command != "verify":
            sub.add_argument("--output")
        if command == "targeted-deep":
            sub.add_argument("--symbol", required=True)
            sub.add_argument("--hops", type=int, default=1)
            sub.add_argument("--max-nodes", type=int, default=80)
            sub.add_argument("--max-edges", type=int, default=160)
            sub.add_argument("--max-bytes", type=int, default=65536)
    args = parser.parse_args(argv)
    try:
        require(sys.implementation.name == "cpython"
                and sys.version_info[:2] in SUPPORTED_PYTHON_MINORS,
                "unsupported Python runtime; use CPython 3.12, 3.13 or 3.14")
        root = repository_root(args.repo)
        if args.command == "repository-deep":
            result = build(root, args.exclude)
        else:
            result = verify(root, load_artifact(args.artifact), args.exclude)
            if args.command == "targeted-deep":
                verified = result
                result = bounded_slice(verified, args.symbol, args.hops, args.max_nodes, args.max_edges, args.max_bytes)
                latest, _ = capture(root, profile(args.exclude))
                require(latest == verified["snapshot"], "source snapshot changed during slicing")
                require(generator() == verified["generator"], "generator changed during slicing")
        if args.command == "verify":
            response = {"status": "fresh", "artifact_sha256": result["artifact_sha256"],
                        "revision": result["snapshot"]["revision"]}
        else:
            output = write_artifact(root, result, args.output)
            response = {"status": "written", "artifact": str(output),
                        "summary": result.get("summary", {k: result[k] for k in ("complete", "omitted_nodes", "omitted_edges") if k in result})}
            if args.command == "repository-deep":
                response["candidates"] = result["hotspots"][:8]
        print(json.dumps(response, sort_keys=True, ensure_ascii=True))
        return 0
    except (DeepError, OSError, RecursionError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
