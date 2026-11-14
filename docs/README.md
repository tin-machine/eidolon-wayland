# Documentation — Eidolon Wayland

> **Finding the correct design matters more than finding the newest file.**

Eidolon Wayland aims to operate Linux GUI applications as independently controllable windows in Meta Quest 3 mixed reality. The documentation is being organized as a **feature-indexed, LLM-first knowledge base**. This page is the stable entry point for people and Codex.

## Read first

| Purpose | Current authoritative entry / evidence |
| --- | --- |
| Project overview and actual high-level state | [Repository README](../README.md) |
| Cross-cutting architecture (historical baselines may be embedded) | [Architecture](architecture.md) |
| Initial Phase 0–6 implementation contract | [D6 feature index](features/initial-phases/README.md), [Design README](design/README.md), [contracts](design/contracts.md), [tasks.json](design/tasks.json) |
| Linux Desktop v3 (D5 registered graph; designs proposed) | [Feature index](features/linux-desktop-v3/README.md), [integration plan](linux-real-app-v3-integration-plan-20261111.md), [I2](linux-real-app-v3-i2-detailed-design.md), [I3 proposal (unregistered)](linux-real-app-v3-i3-detailed-design.md), [I4](linux-real-app-v3-i4-detailed-design.md) |
| Post-I5 source-structure / refactoring evaluation (unregistered proposal) | [Japanese evaluation plan](source-structure-post-i5-evaluation-plan-20261114.md); opt-in and not an I5/I6/I7 gate |
| Curved Workspace detailed design | [D6 CW00–CW25 index](features/curved-workspace/README.md), [original detailed design](curved-workspace/README.md) |
| Spatial Anchor / SA0 proposal | [D6 SA0 feature index](features/spatial-anchor/README.md), [original design](spatial-anchor-linux-design-20261111.md) |
| Android IME / Japanese input | [D6 feature index](features/android-ime/README.md), [IME design](android-ime-wayland-plan-20261107.md) |\n| User operations | [Usage](usage.md) |
| Build and CI behavior | [CI change scope](ci-change-scope-20261113.md), [APT/DNS resilience runbook](ci-apt-dns-reliability-20261112.md), [CodeQL Kotlin probe](codeql-kotlin-manual-probe-20261113.md), [CodeQL Advanced migration](codeql-advanced-migration-20261113.md), [development and debugging](development-automation-and-debugging.md) |
| Implementation history | [Progress](progress.md) |
| Knowledge architecture and future migration | [Full design](documentation-knowledge-base-design-20261113.md), [D1–D2 foundation](knowledge/README.md) |

## What is implemented now?

The LLM-first documentation structure is **being introduced in stages**. D1–D2 inventory/schemas, D3 **read-only resolver**, and D4 **always-on documentation integrity CI** are available. D5 registers [Linux Desktop v3](features/linux-desktop-v3/README.md). D6 adds curated feature indices for [Curved Workspace](features/curved-workspace/README.md), [Spatial Anchor / SA0](features/spatial-anchor/README.md), [Android IME](features/android-ime/README.md), and [initial Phase 0–6](features/initial-phases/README.md) without relocating original documents or fixtures. The Phase 0–6 task status remains authoritative in `docs/design/tasks.json`; **v3 I3/I5 and unreviewed legacy documents remain unregistered**. All added designs use conservative proposed/historical/runbook classifications; a registered ID never proves current implementation or Quest verification. For v3 I4, read **separate I2 and I4 contexts** and check the relevant PR status. See [resolver instructions](knowledge/resolver.md) and run `python3 scripts/docs.py validate` before relying on a graph.

The snapshot inventory is [knowledge/inventory-20261113.csv](knowledge/inventory-20261113.csv). It lists every original `docs/` blob at its recorded commit, with **non-authoritative classification candidates**. It is not an acceptance or implementation report.

### Managed feature quick usage

```bash
python3 scripts/docs.py validate
python3 scripts/docs.py context --feature documentation-system --task D3
python3 scripts/docs.py context --feature linux-desktop-v3 --task I2
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4
python3 scripts/docs.py context --feature curved-workspace --task CW02\npython3 scripts/docs.py context --feature spatial-anchor --task SA0\npython3 scripts/docs.py context --feature android-ime --task NX09\npython3 scripts/docs.py context --feature initial-phases --task B01\npython3 scripts/docs.py affected --path native/core/src/runtime.rs
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
