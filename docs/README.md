# Documentation — Eidolon Wayland

> **Finding the correct design matters more than finding the newest file.**

Eidolon Wayland aims to operate Linux GUI applications as independently controllable windows in Meta Quest 3 mixed reality. The documentation is being organized as a **feature-indexed, LLM-first knowledge base**. This page is the stable entry point for people and Codex.

## Read first

| Purpose | Current authoritative entry / evidence |
| --- | --- |
| Project overview and actual high-level state | [Repository README](../README.md) |
| Cross-cutting architecture (historical baselines may be embedded) | [Architecture](architecture.md) |
| Initial Phase 0–6 implementation contract | [Design README](design/README.md), [contracts](design/contracts.md), [tasks.json](design/tasks.json) |
| Linux Desktop v3 ongoing integration | [v3 integration plan](linux-real-app-v3-integration-plan-20261111.md), [I2](linux-real-app-v3-i2-detailed-design.md), [I4](linux-real-app-v3-i4-detailed-design.md) |
| Curved Workspace detailed design | [Curved Workspace README](curved-workspace/README.md) |
| Spatial Anchor / SA0 proposal | [Spatial Anchor design](spatial-anchor-linux-design-20261111.md) |
| User operations | [Usage](usage.md) |
| Build and CI behavior | [CI change scope](ci-change-scope-20261113.md), [CodeQL Kotlin probe](codeql-kotlin-manual-probe-20261113.md), [development and debugging](development-automation-and-debugging.md) |
| Implementation history | [Progress](progress.md) |
| Knowledge architecture and future migration | [Full design](documentation-knowledge-base-design-20261113.md), [D1–D2 foundation](knowledge/README.md) |

## What is implemented now?

The LLM-first documentation structure is **being introduced in stages**. D1–D2 inventory/schemas and the D3 **read-only resolver** are available. Use [D3 resolver instructions](knowledge/resolver.md) and `python3 scripts/docs.py validate`; only **explicitly registered features** have active manifests (the initial `documentation-system` smoke-test). Linux Desktop v3 and initial Phase 0–6 remain unregistered for the resolver until D5 migration. Do not treat the proposed directory tree as real, or assume context discovery is available for arbitrary legacy features.

The snapshot inventory is [knowledge/inventory-20261113.csv](knowledge/inventory-20261113.csv). It lists every original `docs/` blob at its recorded commit, with **non-authoritative classification candidates**. It is not an acceptance or implementation report.

### D3 quick usage

```bash
python3 scripts/docs.py validate
python3 scripts/docs.py context --feature documentation-system --task D3
python3 scripts/docs.py affected --path scripts/docs.py
```

A missing manifest/ID returns a **nonzero error**. It does not automatically select similarly named legacy files. The original architecture design is still `proposed`, independent of tool implementation.

## How to decide what to read

1. Identify the feature/task and inspect the current code and Git ref. A document's date alone never establishes authority.
2. Select the existing feature entry above (for Phase work use `design/tasks.json` as specified). Read the base contracts before any detailed implementation plan.
3. Compare the document's baseline SHA with the changed source files when available; an unchanged source path does not imply Quest acceptance.
4. Separate **accepted design**, **implemented behavior**, and **host/Android/Quest test evidence**. A passed build does not prove an HMD interaction.
5. If two documents disagree, report the conflict and its source revisions; do not silently pick the newest.

## Contributing documents

Read [knowledge/README.md](knowledge/README.md) for the proposed metadata and manifest schemas and their migration gates. Existing documents without Front Matter are **unregistered legacy documents**, not automatically proposed, accepted, or superseded. Do not mass-move or reclassify them without inventory review and link/CI verification.

**Source of truth:** the current code establishes observed runtime behavior; accepted contracts establish required behavior; dated evidence establishes what was actually verified at a specific revision. None silently overrides another.
