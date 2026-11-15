#!/usr/bin/env python3
"""Eidolon のレビュー済み契約を source/test へ結ぶ、ローカル・読み取り専用の索引。

Rust/Kotlin の構文解析・call resolution・実行検証は行わない。
関係は人が登録し、Git blob、固有の行 anchor、レビューの鮮度を機械で検査する。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "scripts/structure/retirement.json"


class StructureError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise StructureError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    require(result.returncode == 0, "Git object unavailable (fetch the reviewed revision if needed)")
    return result.stdout


def safe_file(root, name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and ".." not in path.parts and "\\" not in name,
            "invalid repository-relative path")
    target = root / name
    require(target.resolve() == root.resolve() / path and target.is_file(),
            "input missing or symlink: " + name)
    return target


def indexed(items, kind):
    result = {}
    for item in items:
        key = item["id"]
        require(isinstance(key, str) and key and key not in result, "duplicate/empty " + kind + " id")
        result[key] = item
    return result


def watch_sources(root, selectors, reviewed, revision):
    """Monitor a wider source inventory without asserting semantic relations or ownership."""
    require(selectors, "source watch selectors are required")
    indexed(selectors, "watch selector")
    for item in selectors:
        name = item["root"]
        path = PurePosixPath(name)
        require(name and str(path) == name and not path.is_absolute() and
                ".." not in path.parts and "\\" not in name and not name.startswith(":") and name != ".",
                "invalid watch root")
        require(item["suffixes"] and all(re.fullmatch(r"\.[a-zA-Z0-9]+", s) for s in item["suffixes"]),
                "invalid watch suffixes")
        require(item["reason"], "watch selector needs a reason")

    def matches(name):
        return [s["id"] for s in selectors if name.startswith(s["root"] + "/") and
                PurePosixPath(name).suffix in s["suffixes"]]

    def tree(ref):
        result = {}
        # Full-tree inventory avoids Git pathspec magic; only literal roots are matched above.
        for record in git(root, "ls-tree", "-rz", "--full-tree", ref).split(b"\0"):
            if not record:
                continue
            metadata, name = record.split(b"\t", 1)
            name = name.decode("utf-8")
            if matches(name):
                mode, kind, oid = metadata.decode().split()
                require(kind == "blob" and mode in ("100644", "100755"), "unsupported watched Git entry: " + name)
                result[name] = {"mode": mode, "oid": oid}
        return result

    baseline, head = tree(reviewed), tree(revision)
    for selector in selectors:
        require(any(selector["id"] in matches(name) for name in baseline),
                "watch selector has no baseline source: " + selector["id"])
    object_format = git(root, "rev-parse", "--show-object-format").decode().strip()
    require(object_format in ("sha1", "sha256"), "unsupported Git object format")
    worktree = {}
    for selector in selectors:
        directory = root / selector["root"]
        require(directory.resolve() == directory and not directory.is_symlink(), "symlink watch root")
        if not directory.exists():
            continue  # Entire directory removal must appear in the inventory diff.
        require(directory.is_dir(), "watch root is not a directory")

        def fail(error):
            raise error

        # Include ignored/untracked files too: a new caller cannot hide behind .gitignore.
        for parent, dirs, names in os.walk(directory, followlinks=False, onerror=fail):
            require(not any((Path(parent) / d).is_symlink() for d in dirs), "symlink in watched directory")
            for name in names:
                relative = (Path(parent) / name).relative_to(root).as_posix()
                if not matches(relative):
                    continue
                source = safe_file(root, relative)
                content = source.read_bytes()
                oid = hashlib.new(object_format, b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
                worktree[relative] = {"mode": "100755" if source.stat().st_mode & 0o100 else "100644", "oid": oid}
    changes, inventory = [], {}
    for name in sorted(set(baseline) | set(head) | set(worktree)):
        old, current, local = baseline.get(name), head.get(name), worktree.get(name)
        inventory[name] = {"selectors": matches(name), "baseline": old, "head": current, "worktree": local}
        for stage, before, after in (("reviewed_to_head", old, current), ("head_to_worktree", current, local)):
            if before != after:
                changes.append({"path": name, "stage": stage,
                                "change": "added" if before is None else "deleted" if after is None else "modified"})
    return {"baseline_revision": reviewed, "selectors": selectors, "inventory": inventory, "changes": changes,
            "status": "needs_review" if changes else "unchanged",
            "meaning": "change detection only; no semantic review, ownership proof or test execution"}


def build(root, profile_name):
    root = Path(root).resolve()
    registry_bytes = safe_file(root, REGISTRY).read_bytes()
    registry = json.loads(registry_bytes)
    require(registry["schema"] == 1, "unsupported registry schema")
    require(profile_name in registry["profiles"], "unknown environment profile")
    profile = registry["profiles"][profile_name]
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    reviewed = registry["reviewed_revision"]
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", reviewed), "reviewed_revision must be an exact SHA")
    watched = watch_sources(root, registry["watch"], reviewed, revision)
    nodes = indexed(registry["nodes"], "node")
    edges = indexed(registry["edges"], "edge")
    files = registry["files"]
    texts, file_evidence = {}, {}
    for key, name in sorted(files.items()):
        current = git(root, "show", f"{revision}:{name}")
        require(safe_file(root, name).read_bytes() == current, "uncommitted scoped source: " + name)
        baseline = git(root, "show", f"{reviewed}:{name}")
        texts[key] = current.decode("utf-8").splitlines()
        file_evidence[key] = {
            "path": name, "sha256": digest(current), "reviewed_sha256": digest(baseline),
            "review": "unchanged" if current == baseline else "needs_review",
        }
    for node in nodes.values():
        require(node["file"] in files and node["role"] in ("source", "test"), "invalid node reference/role")
        require(isinstance(node["anchor"], str) and node["anchor"].strip() and "\n" not in node["anchor"],
                "anchor must be a nonempty single line")
        require(node["domain"] in registry["domains"], "unknown source domain")
    for edge in edges.values():
        require(edge["from"] in nodes and edge["to"] in nodes, "dangling edge")
        require(nodes[edge["from"]]["role"] == nodes[edge["to"]]["role"] == "source", "edge endpoints must be source")
        require(edge["relation"] in ("calls", "requires", "publishes", "bridges"), "unknown relation")
        require(edge["invariant"] and edge["evidence"] and edge["tests"], "edge needs invariant, evidence and tests")
        require(all(key in nodes and nodes[key]["role"] == "source" for key in edge["evidence"]), "invalid source evidence")
        require(all(key in nodes and nodes[key]["role"] == "test" for key in edge["tests"]), "invalid test evidence")
        require(edge["from"] in edge["evidence"] and edge["to"] in edge["evidence"], "both endpoints need evidence")
    require(profile["domains"] and set(profile["domains"]) <= set(registry["domains"]), "invalid profile domains")
    selected = {key for key, node in nodes.items() if node["domain"] in profile["domains"]}
    selected_edges = [edge for edge in edges.values() if edge["from"] in selected and edge["to"] in selected]
    # Test/evidence references remain visible even when their execution environment differs.
    for edge in selected_edges:
        selected.update(edge["evidence"] + edge["tests"])
    resolved = {}
    for key in sorted(selected):
        node = nodes[key]
        matches = [i + 1 for i, line in enumerate(texts[node["file"]]) if line.strip() == node["anchor"].strip()]
        status = "unique_anchor" if len(matches) == 1 else "missing" if not matches else "ambiguous"
        resolved[key] = {**node, "path": files[node["file"]], "lines": matches,
                         "resolution": status, "review": file_evidence[node["file"]]["review"]}
    output_edges = []
    for edge in sorted(selected_edges, key=lambda item: item["id"]):
        refs = edge["evidence"] + edge["tests"]
        valid = all(resolved[key]["resolution"] == "unique_anchor" and resolved[key]["review"] == "unchanged" for key in refs)
        output_edges.append({**edge, "provenance": "manual_source_review",
                             "status": "reviewed" if valid else "needs_review"})
    issues = [{"kind": "source_changed", "file": key} for key, item in file_evidence.items() if item["review"] != "unchanged"]
    issues += [{"kind": node["resolution"], "node": key} for key, node in resolved.items() if node["resolution"] != "unique_anchor"]
    issues += [{"kind": "watched_source_changed", **change} for change in watched["changes"]]
    result = {
        "schema": 1, "kind": "eidolon-reviewed-contract-index", "scope": registry["scope"],
        "revision": revision, "reviewed_revision": reviewed,
        "generator_sha256": digest(Path(__file__).read_bytes()), "registry_sha256": digest(registry_bytes),
        "profile": {"id": profile_name, **profile}, "profile_sha256": digest(encoded(profile)),
        "source_worktree": "semantic files match revision; see watch.changes for wider source inventory",
        "files": file_evidence, "watch": watched,
        "nodes": list(resolved.values()), "edges": output_edges,
        "coverage": {"registered_nodes": len(nodes), "included_nodes": len(resolved),
                     "registered_edges": len(edges), "included_edges": len(output_edges),
                     "excluded_edges": sorted(set(edges) - {edge["id"] for edge in output_edges}),
                     "watched_files": len(watched["inventory"]),
                     "watch_only_files": len(set(watched["inventory"]) - set(files.values())),
                     "watch_changes": len(watched["changes"]),
                     "denominator": "manual registry only; not repository-wide coverage"},
        "unknowns": registry["unknowns"], "issues": issues,
        "tests_execution": "not_run_by_this_tool", "status": "needs_review" if issues else "ok",
    }
    # Refuse a moving source snapshot, including edits arriving during extraction.
    require(watch_sources(root, registry["watch"], reviewed, revision) == watched, "watched source changed during extraction")
    for key, name in files.items():
        require(digest(safe_file(root, name).read_bytes()) == file_evidence[key]["sha256"], "source changed during extraction")
    require(safe_file(root, REGISTRY).read_bytes() == registry_bytes, "registry changed during extraction")
    require(git(root, "rev-parse", "HEAD").decode().strip() == revision, "HEAD changed during extraction")
    result["artifact_sha256"] = digest(encoded(result))
    return result


def bounded_slice(graph, start, hops=1, max_nodes=24, max_edges=32, max_bytes=16000):
    require(graph["status"] == "ok", "source evidence needs review; inspect build output")
    require(0 <= hops <= 3 and 1 <= max_nodes <= 80 and 0 <= max_edges <= 160 and 512 <= max_bytes <= 65536,
            "slice budget outside supported bounds")
    nodes = {node["id"]: node for node in graph["nodes"]}
    require(start in nodes and nodes[start]["role"] == "source", "unknown or profile-excluded source node")
    reached = {start}
    for _ in range(hops):
        reached |= {key for edge in graph["edges"] if edge["from"] in reached or edge["to"] in reached
                    for key in (edge["from"], edge["to"])}
    candidates = [edge for edge in graph["edges"] if edge["from"] in reached and edge["to"] in reached]
    selected, chosen = {start}, []

    def render():
        return {"schema": 1, "kind": "eidolon-contract-slice", "revision": graph["revision"],
                "profile": graph["profile"], "graph_sha256": graph["artifact_sha256"], "start": start,
                "hops": hops, "nodes": [nodes[key] for key in sorted(selected)], "edges": list(chosen),
                "omitted_edges": [edge["id"] for edge in candidates if edge not in chosen],
                "profile_excluded_edges": graph["coverage"]["excluded_edges"],
                "watch": {"files": graph["coverage"]["watched_files"], "status": graph["watch"]["status"],
                          "meaning": graph["watch"]["meaning"]},
                "unknowns": graph["unknowns"], "tests_execution": graph["tests_execution"],
                "complete": len(chosen) == len(candidates)}

    require(len(encoded(render())) <= max_bytes, "byte budget too small for slice metadata")
    for edge in candidates:
        refs = {edge["from"], edge["to"], *edge["evidence"], *edge["tests"]}
        if len(chosen) >= max_edges or len(selected | refs) > max_nodes:
            continue
        previous = selected.copy()
        selected |= refs
        chosen.append(edge)
        if len(encoded(render())) > max_bytes:
            chosen.pop()
            selected = previous
    return render()


def verify(graph, current):
    # Full deterministic comparison also rejects modified evidence and mismatched profiles/generators.
    require(graph == current, "artifact stale or modified; rebuild for this HEAD/profile/generator")
    require(current["status"] == "ok", "artifact evidence needs review")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "check", "slice", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--profile", required=True, help="android-product or host-model")
        if name == "verify":
            cmd.add_argument("artifact", type=Path)
        if name == "slice":
            cmd.add_argument("--node", required=True)
            cmd.add_argument("--hops", type=int, default=1)
            cmd.add_argument("--max-nodes", type=int, default=24)
            cmd.add_argument("--max-edges", type=int, default=32)
            cmd.add_argument("--max-bytes", type=int, default=16000)
    args = parser.parse_args(argv)
    try:
        graph = build(args.root, args.profile)
        if args.command == "verify":
            verify(json.loads(args.artifact.read_bytes()), graph)
        if args.command == "slice":
            output = bounded_slice(graph, args.node, args.hops, args.max_nodes, args.max_edges, args.max_bytes)
        elif args.command == "build":
            output = graph
        else:
            output = {key: graph[key] for key in ("status", "revision", "artifact_sha256", "coverage", "issues", "tests_execution")}
        sys.stdout.buffer.write(encoded(output))
        return 0 if graph["status"] == "ok" else 1
    except (StructureError, KeyError, TypeError, OSError, ValueError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
