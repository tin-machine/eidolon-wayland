# Documentation Knowledge System — D1/D2 foundation

Status: **D1/D2 foundation + D3 read-only resolver; D4 docs CI not yet implemented**  
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

## D1 — Provisional stable-ID seeds (not registered)

These are **candidates** for a future reviewer to reconcile with current source and with any existing Front Matter. They reserve neither authority nor ownership; no D3 resolver currently consumes them. Before D5, check for collisions, actual current scope, and supersession.

| Proposed stable ID | Existing path | Review requirement |
| --- | --- | --- |
| `docs-knowledge-architecture-20261113` | `docs/documentation-knowledge-base-design-20261113.md` | Existing Front Matter; proposed system design, not an accepted contract |
| `eidolon-system-architecture` | `docs/architecture.md` | Review stale phase-specific assertions before claiming current architecture |
| `initial-phase-contracts` | `docs/design/contracts.md` | Must preserve `scripts/check-design` behavior |
| `initial-phase-task-index` | `docs/design/tasks.json` | Task JSON is not YAML Front Matter; support as registered structured source if needed |
| `linux-desktop-v3-integration-plan` | `docs/linux-real-app-v3-integration-plan-20261111.md` | Baseline/source drift and status review |
| `linux-desktop-v3-b1-contract` | `docs/linux-real-app-b1-contract-20261110.md` | Confirm contract precedence with other B1 docs |
| `linux-desktop-v3-i2` | `docs/linux-real-app-v3-i2-detailed-design.md` | Review against merged I2 implementations before acceptance |
| `linux-desktop-v3-i4` | `docs/linux-real-app-v3-i4-detailed-design.md` | Review against current I3/I4 PR and B1 observation API |
| `workspace-curved-design-index` | `docs/curved-workspace/README.md` | Preserve fixtures and explicit 01–08 reading order |
| `workspace-spatial-anchor-sa0` | `docs/spatial-anchor-linux-design-20261111.md` | SA0 is an observation-only proposal, not Quest-proven |

The complete inventory remains the CSV; these **ten seed IDs do not imply that remaining files should be ignored**. Other documents receive stable IDs only when reviewed during feature migration.

## D2 — Proposed machine-readable contracts

- [Document Metadata v1 JSON Schema](schemas/document-metadata.schema.json): the **YAML Front Matter mapping** of a managed document. Only applies after an explicit migration.
- [Feature Manifest v1 JSON Schema](schemas/feature-manifest.schema.json): per-feature `docs/features/<feature>/manifest.yaml` entries, mapping tasks and dependencies to stable document IDs; **only explicitly migrated features are active**.

Both schemas use JSON Schema draft 2020-12. YAML files should be parsed with a **safe** parser and then validated as JSON-compatible mappings; no custom object constructors, executables, external URL imports or arbitrary YAML tags. YAML ISO dates must be quoted so a parser that implicitly converts scalars to date objects cannot change the field type. D3 uses a limited, fail-closed standard-library parser and validator; read [resolver.md](resolver.md) for the accepted YAML subset and tested behavior.

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

### Feature manifest example (illustrative; Linux Desktop v3 file does not exist)

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
| D3 | `scripts/docs.py validate/context/affected/drift`, unit tests, explicitly managed ID graph | **Yes (registered documents only)** |
| D4 | Lightweight docs CI, scope/classifier changes, required check coordination | **No** |
| D5 | Reviewed Linux Desktop v3 metadata/manifest and links | **No** |
| D6 | Complete gradual migration of other feature areas | **No** |

## Migration / compatibility gates

1. Preserve `docs/design/tasks.json`, all spec anchors, and [`scripts/check-design`](../../scripts/check-design); they are **already consumed by CI**.
2. Do not move `docs/curved-workspace/fixtures/*.json` or other fixtures without reviewing test inputs.
3. Existing [CI change scope](../ci-change-scope-20261113.md) treats new `docs/**/*.json` and a root `AGENTS.md` conservatively (expensive jobs may run). D4 must update scope rules only with fail-open regression tests; **do not hide all documentation via workflow path ignores**.
4. Do not assert that historic GitHub `/blob/main/docs/old-path` URLs redirect after renames. Retain a small stub when link consumers need compatibility.
5. Historical probe/acceptance records retain original experiment/build/commit identity. Host tests never substitute for Quest/HMD acceptance.
6. D3 provides deterministic dependency discovery (topological order + ID tie-break), explicit unknown IDs, machine-readable output, and bounded context. Unregistered legacy documents remain outside automatic resolution until D5.
7. Codex instructions are routed from the root `AGENTS.md`; do not assume a document subtree instruction implicitly governs edits in `native/` or `android/`.

## Acceptance / self-check

At the snapshot SHA, the inventory must include **167 distinct paths**, all under `docs/`, matching Git exactly and retaining three JSON fixtures plus the existing `design/tasks.json`. Both schema files must be syntactically valid JSON with unique properties/enums. Repository paths referenced by the two README files and AGENTS.md must exist in the PR. Existing `scripts/check-design` and CI sources must remain byte-for-byte unchanged.

D3 validates **only explicitly registered** features/documents, and cannot validate a legacy feature's complete dependency DAG until its D5 registration is reviewed. D4 CI integration remains separate.
