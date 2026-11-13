#!/usr/bin/env python3
"""Resolve Eidolon documentation contracts without network or extra Python packages.

D3 is deliberately read-only. It does not infer design acceptance, implementation
progress, or Quest/HMD verification from a filename or a passing CI run.
"""
from __future__ import annotations

import argparse
from datetime import date
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ ]+(.*))?$")
SHA = re.compile(r"^[0-9a-f]{40}$")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
MAX_BYTES = 250_000
MAX_DOCS = 32


class KnowledgeError(Exception):
    """Invalid metadata, ambiguous authority, unsafe reference, or unresolved input."""


def safe_relative(value: str, *, allow_glob: bool = False) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise KnowledgeError(f"unsafe relative path: {value!r}")
    if any(p in ("", ".", "..") for p in value.split("/")):
        raise KnowledgeError(f"unsafe relative path: {value!r}")
    if re.search(r"\s", value) or "\x00" in value or value.startswith("-"):
        raise KnowledgeError(f"unsafe relative path: {value!r}")
    if not allow_glob and any(c in value for c in "*?["):
        raise KnowledgeError(f"glob is not allowed here: {value!r}")
    return value


def inside(root: Path, path: Path) -> Path:
    root_real = root.resolve(strict=True)
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root_real):
        raise KnowledgeError(f"path escapes repository: {path}")
    return path


def scalar(value: str):
    value = value.strip()
    if value in ("{}", "[]"):
        return {} if value == "{}" else []
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        if not body:
            return []
        if "[" in body or "]" in body or "{" in body or "}" in body:
            raise KnowledgeError("nested flow YAML is outside the supported subset")
        return [scalar(part.strip()) for part in body.split(",")]
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise KnowledgeError(f"invalid quoted YAML string: {error}") from error
        if not isinstance(parsed, str):
            raise KnowledgeError("expected quoted string")
        return parsed
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.lower() in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[value.lower()]
    if re.fullmatch(r"(0|[1-9][0-9]*)", value):
        return int(value)
    if not value or any(value.startswith(s) for s in ("!", "&", "*", "|", ">", "{", "[", "---")):
        raise KnowledgeError(f"unsupported YAML scalar: {value!r}")
    if "#" in value or ": " in value:
        raise KnowledgeError("quote YAML scalar containing '#' or ': '")
    return value


def yaml_subset(text: str):
    """Parse a fail-closed YAML subset: 2-space maps, scalar lists, inline lists.

    No YAML tags, anchors, aliases, merges, block strings, custom constructors,
    duplicate keys, implicit timestamps or arbitrary object deserialization.
    """
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise KnowledgeError(f"tabs are disallowed (line {number})")
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise KnowledgeError(f"expected 2-space indentation (line {number})")
        lines.append((indent, raw.strip(), number))
    if not lines or lines[0][0] != 0:
        raise KnowledgeError("empty or indented YAML document")

    def parse(start: int, level: int):
        sequence = lines[start][1].startswith("- ")
        result = [] if sequence else {}
        i = start
        while i < len(lines):
            indent, line, number = lines[i]
            if indent < level:
                break
            if indent != level:
                raise KnowledgeError(f"unexpected indentation (line {number})")
            if sequence:
                if not line.startswith("- "):
                    raise KnowledgeError(f"mixed map and list (line {number})")
                item = line[2:].strip()
                if not item:
                    if i + 1 >= len(lines) or lines[i + 1][0] <= level:
                        raise KnowledgeError(f"missing list value (line {number})")
                    child, i = parse(i + 1, lines[i + 1][0])
                    result.append(child)
                    continue
                result.append(scalar(item))
                i += 1
            else:
                match = KEY.fullmatch(line)
                if not match:
                    raise KnowledgeError(f"invalid YAML mapping (line {number})")
                key, value = match.groups()
                if key in result:
                    raise KnowledgeError(f"duplicate YAML key {key!r} (line {number})")
                if value is None:
                    if i + 1 < len(lines) and lines[i + 1][0] > level:
                        if lines[i + 1][0] != level + 2:
                            raise KnowledgeError(f"indentation jump (line {number})")
                        child, i = parse(i + 1, level + 2)
                        result[key] = child
                        continue
                    raise KnowledgeError(f"missing mapping value for {key!r}")
                if i + 1 < len(lines) and lines[i + 1][0] > level:
                    raise KnowledgeError(f"scalar has unexpected nested block (line {number})")
                result[key] = scalar(value)
                i += 1
        return result, i

    obj, offset = parse(0, 0)
    if offset != len(lines) or not isinstance(obj, dict):
        raise KnowledgeError("expected one YAML mapping")
    return obj


def metadata(path: Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        raise KnowledgeError(f"unterminated Front Matter: {path}")
    return yaml_subset(text[4:end])


def schema_check(value, rule: dict, root: dict, where: str = "$"):
    """Evaluate every keyword used by the two pinned v1 schemas."""
    if "$ref" in rule:
        ref = rule["$ref"]
        if not ref.startswith("#/$defs/") or "/" in ref[len("#/$defs/"):]:
            raise KnowledgeError(f"unsupported schema reference: {ref}")
        return schema_check(value, root["$defs"][ref[len("#/$defs/"):]], root, where)
    typ = rule.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if typ:
        cls = types.get(typ)
        if cls is None or not isinstance(value, cls) or (typ == "integer" and isinstance(value, bool)):
            raise KnowledgeError(f"{where}: expected {typ}")
    if "enum" in rule and value not in rule["enum"]:
        raise KnowledgeError(f"{where}: invalid enum {value!r}")
    if "const" in rule and value != rule["const"]:
        raise KnowledgeError(f"{where}: value differs from required constant")
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", 1 << 30):
            raise KnowledgeError(f"{where}: invalid string length")
        if "pattern" in rule and not re.search(rule["pattern"], value):
            raise KnowledgeError(f"{where}: pattern mismatch")
        if rule.get("format") == "date":
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError("not canonical")
            except ValueError as error:
                raise KnowledgeError(f"{where}: invalid ISO date") from error
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0):
            raise KnowledgeError(f"{where}: too few array items")
        if rule.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise KnowledgeError(f"{where}: duplicate array item")
        for i, item in enumerate(value):
            if "items" in rule:
                schema_check(item, rule["items"], root, f"{where}[{i}]")
    if isinstance(value, dict):
        for required in rule.get("required", []):
            if required not in value:
                raise KnowledgeError(f"{where}: missing {required}")
        for key, item in value.items():
            chosen = rule.get("properties", {}).get(key)
            if chosen is None:
                for pattern, selected in rule.get("patternProperties", {}).items():
                    if re.search(pattern, key):
                        chosen = selected
                        break
            if chosen is None:
                if rule.get("additionalProperties") is False:
                    raise KnowledgeError(f"{where}: unknown field {key!r}")
            else:
                schema_check(item, chosen, root, f"{where}.{key}")
    for restriction in rule.get("allOf", []):
        if "if" in restriction:
            try:
                schema_check(value, restriction["if"], root, where)
            except KnowledgeError:
                continue
            schema_check(value, restriction.get("then", {}), root, where)
        else:
            schema_check(value, restriction, root, where)


def git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False, timeout=30)
    if proc.returncode:
        raise KnowledgeError(f"git {' '.join(args[:2])} failed: {proc.stderr.decode(errors='replace').strip()[:250]}")
    return proc.stdout


def valid_source_patterns(root: Path, patterns: list[str], owner: str):
    for pattern in patterns:
        safe_relative(pattern, allow_glob=True)
        hits = list(root.glob(pattern))
        if not hits:
            raise KnowledgeError(f"{owner}: source pattern has no matches: {pattern}")
        for path in hits:
            inside(root, path)


def check_links(root: Path, path: Path):
    # Only managed Markdown is checked; legacy files must remain unmodified.
    for match in LINK.finditer(path.read_text(encoding="utf-8")):
        address = match.group(1).strip().split()[0].strip("<>")
        parsed = urlsplit(address)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        relative = unquote(parsed.path)
        if relative.startswith("/"):
            continue  # external/site-root GitHub links are not local paths
        dest = inside(root, path.parent / relative)
        if not dest.is_file():
            raise KnowledgeError(f"{path.relative_to(root)}: broken local link {address}")


def docs_and_manifests(root: Path):
    meta_schema = json.loads((root / "docs/knowledge/schemas/document-metadata.schema.json").read_text())
    manifest_schema = json.loads((root / "docs/knowledge/schemas/feature-manifest.schema.json").read_text())
    if meta_schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise KnowledgeError("metadata schema version changed without D3 review")
    if manifest_schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise KnowledgeError("manifest schema version changed without D3 review")
    docs = {}
    for path in sorted((root / "docs").rglob("*.md")):
        inside(root, path)
        data = metadata(path)
        if data is None:
            continue
        schema_check(data, meta_schema, meta_schema)
        docid = data["id"]
        if docid in docs:
            raise KnowledgeError(f"duplicate document ID {docid}: {path} and {docs[docid][0]}")
        check_links(root, path)
        valid_source_patterns(root, data.get("related_sources", []), docid)
        docs[docid] = (path, data)
    manifests = {}
    for path in sorted((root / "docs/features").glob("**/manifest.yaml")) if (root / "docs/features").exists() else []:
        inside(root, path)
        data = yaml_subset(path.read_text(encoding="utf-8"))
        schema_check(data, manifest_schema, manifest_schema)
        feature = data["feature"]
        if feature in manifests:
            raise KnowledgeError(f"duplicate feature manifest: {feature}")
        entrypoint = inside(root, path.parent / safe_relative(data["entrypoint"]))
        if not entrypoint.is_file():
            raise KnowledgeError(f"{feature}: missing entrypoint {entrypoint}")
        valid_source_patterns(root, data["source_paths"], feature)
        manifests[feature] = (path, data)
    # Cross-reference all managed docs and active manifests; no silently missing edges.
    for docid, (_, data) in docs.items():
        for target in data.get("depends_on", []) + data.get("supersedes", []):
            if target not in docs:
                raise KnowledgeError(f"{docid}: unknown document ID {target}")
            if target == docid:
                raise KnowledgeError(f"{docid}: self-reference")
        if data.get("design_status") == "superseded" and not data.get("supersedes"):
            raise KnowledgeError(f"{docid}: missing supersedes")
    for feature, (_, item) in manifests.items():
        for docid in item["contracts"]:
            if docid not in docs or docs[docid][1]["feature"] != feature:
                raise KnowledgeError(f"{feature}: missing or cross-feature contract {docid}")
        for task, record in item["tasks"].items():
            docid = record["document"]
            if docid not in docs or docs[docid][1]["feature"] != feature:
                raise KnowledgeError(f"{feature}/{task}: missing or cross-feature task document {docid}")
            for target in record["requires"] + record.get("test_plans", []):
                if target not in docs:
                    raise KnowledgeError(f"{feature}/{task}: unknown dependency {target}")
    seen_authority = {}
    for docid, (_, data) in docs.items():
        if data.get("design_status") == "accepted":
            for key in data.get("authority_keys", []):
                scoped = (data["feature"], key)
                if scoped in seen_authority:
                    raise KnowledgeError(f"authority conflict: {scoped}, {seen_authority[scoped]} vs {docid}")
                seen_authority[scoped] = docid
    # Global cycle guard, including supersession edges.
    adjacency = {docid: data.get("depends_on", []) + data.get("supersedes", []) for docid, (_, data) in docs.items()}
    visiting, finished = set(), set()
    def visit(docid):
        if docid in visiting:
            raise KnowledgeError(f"documentation dependency cycle: {docid}")
        if docid in finished:
            return
        visiting.add(docid)
        for other in sorted(adjacency[docid]):
            visit(other)
        visiting.remove(docid)
        finished.add(docid)
    for docid in sorted(docs):
        visit(docid)
    return docs, manifests


def closure(docs, roots: list[str]):
    ordered, visiting, completed = [], set(), set()
    def visit(docid):
        if docid not in docs:
            raise KnowledgeError(f"unknown managed document: {docid}")
        if docid in visiting:
            raise KnowledgeError(f"dependency cycle at {docid}")
        if docid in completed:
            return
        visiting.add(docid)
        for dep in sorted(docs[docid][1].get("depends_on", [])):
            visit(dep)
        visiting.remove(docid)
        completed.add(docid)
        ordered.append(docid)
    for docid in sorted(set(roots)):
        visit(docid)
    return ordered


def source_hit(pattern: str, path: str) -> bool:
    # fnmatch is conservative: '*' may include '/'. This yields false positives,
    # never the false negatives that would suppress a required review.
    return fnmatch.fnmatchcase(path, pattern)


def drift(root: Path, document: dict, head: str = "HEAD"):
    baseline = document.get("baseline_commit")
    patterns = document.get("related_sources", [])
    if not baseline or not patterns:
        return {"status": "unknown", "reason": "baseline or related_sources is unspecified",
                "baseline": baseline, "changed_paths": []}
    try:
        git(root, "cat-file", "-e", baseline + "^{commit}")
        git(root, "merge-base", "--is-ancestor", baseline, head)
        raw = git(root, "diff", "--name-status", "-z", "--find-renames", baseline, head)
        fields = raw.split(b"\x00")
        if fields and fields[-1] == b"":
            fields.pop()
        paths, i = [], 0
        while i < len(fields):
            status = fields[i].decode("ascii")
            i += 1
            if not status or status[0] not in "ACDMRTUXB":
                raise KnowledgeError("unknown git diff status")
            count = 2 if status[0] in "RC" else 1
            if i + count > len(fields):
                raise KnowledgeError("malformed git diff")
            paths.extend(os.fsdecode(x) for x in fields[i:i + count])
            i += count
    except (KnowledgeError, UnicodeError, subprocess.TimeoutExpired) as error:
        return {"status": "unknown", "reason": str(error), "baseline": baseline, "changed_paths": []}
    hits = sorted({p for p in paths if any(source_hit(pattern, p) for pattern in patterns)})
    return {"status": "check_required" if hits else "clean",
            "reason": "related source changed" if hits else "no tracked related-source changes",
            "baseline": baseline, "changed_paths": hits}


def context(root: Path, docs, manifests, feature: str, task: str | None,
            max_docs: int = MAX_DOCS, max_bytes: int = MAX_BYTES):
    if feature not in manifests:
        raise KnowledgeError(f"feature {feature!r} has no registered manifest (D5 migration may be pending)")
    manifest_path, manifest = manifests[feature]
    requested = list(manifest["contracts"])
    if task is not None:
        if task not in manifest["tasks"]:
            raise KnowledgeError(f"unregistered task: {feature}/{task}")
        record = manifest["tasks"][task]
        requested += record["requires"] + record.get("test_plans", []) + [record["document"]]
    ids = closure(docs, requested)
    size = sum(docs[docid][0].stat().st_size for docid in ids)
    if len(ids) > max_docs or size > max_bytes:
        raise KnowledgeError(f"context exceeds bounds ({len(ids)} docs / {size} bytes); review narrower task")
    output, warnings = [], []
    for docid in ids:
        path, data = docs[docid]
        status = data.get("design_status")
        if status != "accepted" and data["type"] in ("contract", "detailed-design", "architecture", "decision"):
            warnings.append(f"{docid}: design status {status or 'unclassified'}, not an accepted authority")
        output.append({"id": docid, "path": path.relative_to(root).as_posix(),
                       "type": data["type"], "design_status": status,
                       "baseline_commit": data.get("baseline_commit"),
                       "depends_on": data.get("depends_on", []),
                       "drift": drift(root, data)})
    return {"schema": 1, "feature": feature, "task": task,
            "entrypoint": (manifest_path.parent / manifest["entrypoint"]).relative_to(root).as_posix(),
            "documents": output, "total_bytes": size, "warnings": sorted(warnings)}


def affected(docs, manifests, source: str):
    safe_relative(source)
    matches = []
    for docid, (path, data) in sorted(docs.items()):
        if any(source_hit(pattern, source) for pattern in data.get("related_sources", [])):
            matches.append({"document_id": docid, "path": path.relative_to(ROOT).as_posix(),
                            "feature": data["feature"]})
    features = sorted(feature for feature, (_, item) in manifests.items()
                      if any(source_hit(pattern, source) for pattern in item["source_paths"]))
    return {"source_path": source, "features": features, "documents": matches}


def execute(args):
    root = Path(args.root).resolve()
    if not (root / "docs/knowledge/schemas").is_dir():
        raise KnowledgeError(f"not an Eidolon documentation repository: {root}")
    docs, manifests = docs_and_manifests(root)
    if args.command == "validate":
        return {"schema": 1, "status": "pass", "managed_documents": len(docs),
                "feature_manifests": len(manifests),
                "legacy_documents": sum(1 for p in (root / "docs").rglob("*.md")
                                        if p.is_file()) - len(docs)}
    if args.command == "context":
        return context(root, docs, manifests, args.feature, args.task,
                       args.max_docs, args.max_bytes)
    if args.command == "drift":
        ctx = context(root, docs, manifests, args.feature, args.task, args.max_docs, args.max_bytes)
        return {"feature": args.feature, "task": args.task,
                "documents": [{"id": item["id"], "drift": item["drift"]} for item in ctx["documents"]]}
    if args.command == "affected":
        safe_relative(args.path)
        matches = []
        for docid, (p, data) in sorted(docs.items()):
            if any(source_hit(pattern, args.path) for pattern in data.get("related_sources", [])):
                matches.append({"document_id": docid, "path": p.relative_to(root).as_posix(),
                                "feature": data["feature"]})
        features = sorted(feature for feature, (_, item) in manifests.items()
                          if any(source_hit(pattern, args.path) for pattern in item["source_paths"]))
        return {"source_path": args.path, "features": features, "documents": matches}
    raise KnowledgeError("unsupported command")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="repository root (for isolated tests)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "context", "affected", "drift"):
        subparser = sub.add_parser(name)
        subparser.add_argument("--format", choices=("text", "json"), default="text")
        if name in ("context", "drift"):
            subparser.add_argument("--feature", required=True)
            subparser.add_argument("--task")
            subparser.add_argument("--max-docs", type=int, default=MAX_DOCS)
            subparser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
        if name == "affected":
            subparser.add_argument("--path", required=True)
    args = parser.parse_args(argv)
    try:
        payload = execute(args)
    except (KnowledgeError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        if args.command == "validate":
            print(f"PASS: {payload['managed_documents']} managed docs, "
                  f"{payload['feature_manifests']} manifests, "
                  f"{payload['legacy_documents']} unregistered legacy Markdown docs")
        elif args.command == "affected":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.command == "drift":
            for item in payload["documents"]:
                print(f"{item['id']}: {item['drift']['status']} ({item['drift']['reason']})")
        else:
            print(f"{payload['feature']} / {payload['task'] or 'feature'}: "
                  f"{len(payload['documents'])} documents; entry: {payload['entrypoint']}")
            for item in payload["documents"]:
                print(f"  {item['id']}: {item['path']} [{item['design_status'] or item['type']}; "
                      f"drift={item['drift']['status']}]")
            for warning in payload["warnings"]:
                print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
