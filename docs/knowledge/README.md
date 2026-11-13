# Documentation Knowledge System — D1/D2 foundation

Status: **Foundation prepared; D3 resolver and D4 CI integration not yet implemented**  
Snapshot commit: `78e760a5e3d15d8e7002229948d665260c4f1484` (2026-11-13 JST)  
Parent design: [LLM-first Documentation Knowledge Architecture](../documentation-knowledge-base-design-20261113.md)

## D1 — Complete read-only inventory

[Inventory CSV](inventory-20261113.csv) contains **167 rows** (excluding comments and header), one per original `docs/` Git blob at the snapshot SHA. Each row holds:

- `path`: original repository-relative path; the only factual classification input.
- `candidate_area`: possible feature/topic destination, **not confirmed authority**.
- `candidate_type`: heuristic document purpose, including `unclassified`; **not confirmed type**.
- `registration_status`: all `unregistered` until D5+ review applies a managed ID and validates its references.

The snapshot includes 3 Curved Workspace fixture JSON files and `docs/design/tasks.json`; it is not restricted to Markdown. No files are moved or edited. The historical `docs/documentation-knowledge-base-design-20261113.md` has a provisional Front Matter ID but is still `unregistered` in this D1 registry; registering requires a reviewed manifest/ID graph.

The original directory layout is **not** an authority ranking. For current tasks use [the documentation entry](../README.md). Candidate sorting does **not** indicate `accepted`, `implemented`, or `verified`.

Do not treat this dated snapshot as live state. D3 should regenerate/compare the Git inventory using `git ls-tree -r --name-only <commit> docs/` (or equivalent) and flag additions, deletions, and renames.

## D2 — Proposed machine-readable contracts

- [Document Metadata v1 JSON Schema](schemas/document-metadata.schema.json): the **YAML Front Matter mapping** of a managed document. Only applies after an explicit migration.
- [Feature Manifest v1 JSON Schema](schemas/feature-manifest.schema.json): a **future** `docs/features/<feature>/manifest.yaml` entry, mapping tasks and dependencies to stable document IDs.

Both schemas use JSON Schema draft 2020-12. YAML files should be parsed with a **safe** parser and then validated as JSON-compatible mappings; no custom object constructors, executables, external URL imports or arbitrary YAML tags. YAML ISO dates must be quoted so a parser that implicitly converts scalars to date objects cannot change the field type. D3 chooses a parser/validator and tests interoperability.

The schemas intentionally cover **shape and enums** only. Syntax-level acceptance does **not** establish authority, implementation, or verification; the D3 semantic validator must enforce referential integrity, source path traversal/symlink safety, Git SHA existence/ancestry, duplicate IDs, dependency cycles, supersession, authority overlap, Markdown anchors and stale scope.

### Metadata example (illustrative, NOT the current I4 status)

~~~yaml
---
id: linux-desktop-v3-i4
title: Startup Observation / Spatial SDK Evidence
type: detailed-design
design_status: proposed
feature: linux-desktop-v3
component: startup-observation
phase: I4
created: "2026-11-12"
last_reviewed: "2026-11-13"
baseline_commit: f894d796a06bcf0fafb1567718cd180ecf29009b
depends_on: [linux-desktop-v3-b1-contract]
supersedes: []
related_sources:
  - native/core/src/runtime.rs
---
~~~

**Important:** `design_status` is about the design only. Task implementation status remains in the task's source of truth, such as the existing `docs/design/tasks.json` for Phase work. Verification results require separate evidence with a pinned commit, build and runtime environment. No `verified: true` shortcut.

### Feature manifest example (illustrative; file does not exist)

~~~yaml
schema: 1
feature: linux-desktop-v3
entrypoint: README.md
contracts: [linux-desktop-v3-b1-contract]
tasks:
  I4:
    document: linux-desktop-v3-i4
    requires: [linux-desktop-v3-b1-contract]
    test_plans: []
source_paths:
  - native/model/src/linux_desktop_v3/**
  - native/core/src/**
~~~

Document links in a manifest are **IDs**, not paths. A managed document ID is unique, stable through path moves, and never reassigned. D3 resolves IDs from registered front matter. A missing ID or a cycle must be an error, not a fallback to a similarly named file. `source_paths` means search/index association, not exclusive code ownership.

### Authority and drift

A document is not an accepted contract just because it appears in the inventory or manifest. A proposal never overrides an accepted contract without review; a supersession must name its predecessor, and an unresolved conflict blocks automatic context selection. A document's baseline commit identifies its review context, not its freshness or HMD success. D3 will distinguish `clean`, `check_required`, `unknown`, and `conflict`, and must never translate unknown into clean.

### Validation responsibility boundary

| Stage | Required semantics | Implemented in this PR? |
| --- | --- | --- |
| D1 | Static Git inventory and candidate labels | Yes |
| D2 | Schema definitions, docs entrance, root `AGENTS.md` | Yes |
| D3 | `scripts/docs.py validate/context/affected/drift`, unit tests, complete ID graph | **No** |
| D4 | Lightweight docs CI, scope/classifier changes, required check coordination | **No** |
| D5 | Reviewed Linux Desktop v3 metadata/manifest and links | **No** |
| D6 | Complete gradual migration of other feature areas | **No** |

## Migration / compatibility gates

1. Preserve `docs/design/tasks.json`, all spec anchors, and [`scripts/check-design`](../../scripts/check-design); they are **already consumed by CI**.
2. Do not move `docs/curved-workspace/fixtures/*.json` or other fixtures without reviewing test inputs.
3. Existing [CI change scope](../ci-change-scope-20261113.md) treats new `docs/**/*.json` and a root `AGENTS.md` conservatively (expensive jobs may run). D4 must update scope rules only with fail-open regression tests; **do not hide all documentation via workflow path ignores**.
4. Do not assert that historic GitHub `/blob/main/docs/old-path` URLs redirect after renames. Retain a small stub when link consumers need compatibility.
5. Historical probe/acceptance records retain original experiment/build/commit identity. Host tests never substitute for Quest/HMD acceptance.
6. D3 should add deterministic discovery (topological order + ID tie-break), explicit unknown IDs, machine-readable output, and maximum context bounds before enabling agent automation.
7. Codex instructions are routed from the root `AGENTS.md`; do not assume a document subtree instruction implicitly governs edits in `native/` or `android/`.

## Acceptance / self-check

At the snapshot SHA, the inventory must include **167 distinct paths**, all under `docs/`, matching Git exactly and retaining three JSON fixtures plus the existing `design/tasks.json`. Both schema files must be syntactically valid JSON with unique properties/enums. Repository paths referenced by the two README files and AGENTS.md must exist in the PR. Existing `scripts/check-design` and CI sources must remain byte-for-byte unchanged.

This D2 foundation is **not a verified D3 resolver** and cannot validate a feature's full design dependency DAG. It only prepares a safe migration substrate.
