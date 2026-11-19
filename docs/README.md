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
| Post-I5 source-structure / refactoring evaluation (unregistered proposal) | [Japanese evaluation plan and historical contract pilot](source-structure-post-i5-evaluation-plan-20261114.md); current automatic Deep workflow follows the guide below; opt-in and not an I5/I6/I7 gate |
| Curved Workspace detailed design | [D6 CW00–CW25 index](features/curved-workspace/README.md), [original detailed design](curved-workspace/README.md) |
| Spatial Anchor / SA0 proposal | [D6 SA0 feature index](features/spatial-anchor/README.md), [original design](spatial-anchor-linux-design-20261111.md) |
| Android IME / Japanese input | [D6 feature index](features/android-ime/README.md), [IME design](android-ime-wayland-plan-20261107.md) |\n| User operations | [Usage](usage.md) |
| Build and CI behavior | [CI change scope](ci-change-scope-20261113.md), [APT/DNS resilience runbook](ci-apt-dns-reliability-20261112.md), [CodeQL Kotlin probe](codeql-kotlin-manual-probe-20261113.md), [CodeQL Advanced migration](codeql-advanced-migration-20261113.md), [development and debugging](development-automation-and-debugging.md) |
| Early Quest 3 / Mousepad smoke (manual, non-acceptance) | [Four wearer checks](features/linux-desktop-v3/quest-early-smoke-20261119.md) and [Codex preparation / next-session handoff](features/linux-desktop-v3/quest-early-smoke-codex-handoff-20261119.md); preparation and diagnostics precede the short physical check |
| Codex source investigation and refactoring candidates | [On-demand automatic Deep analysis](source-structure-deep-analysis.md): Git discovery, bounded Python CFG / local DFG, provenance and unknowns; [import candidate evaluation](source-structure-deep-import-evaluation-20261115.md); separate from the reviewed contract index |
| Source review / structural analysis pilot | [回収・設定保存のレビュー](source-structure-retirement-review-20261115.md), [Host/JNI・SDK 境界レビューと探索比較](source-structure-boundary-review-20261115.md), [監視範囲・IME所有境界・追加3問](source-structure-watch-ownership-20261115.md), [IME終了証拠の接続案](source-structure-ime-retirement-contract-20261115.md), [専用契約索引の使い方](../scripts/structure/README.md)（手動登録した関係と source 変更監視、実機受入を意味しない） |
| Implementation history | [Progress](progress.md) |
| Knowledge architecture and future migration | [Full design](documentation-knowledge-base-design-20261113.md), [D1–D2 foundation](knowledge/README.md) |

## What is implemented now?

For I6, follow the [evidence validator increment](features/linux-desktop-v3/i6-evidence-validation.md)
and [N01 real SSH / Inspect-only increment](features/linux-desktop-v3/i6-n01-inspect-only.md),
followed by the [real Host EGL / bounded readback / retirement increment](features/linux-desktop-v3/i6-host-egl-readback.md)
and the [N02 real Launch / typed partial-acceptance bridge](features/linux-desktop-v3/i6-n02-typed-acceptance.md),
then [N03 Launch Admission refusals / five-variant Host R/T checkpoint](features/linux-desktop-v3/i6-n03-launch-admission.md).
Their implementation and measured test results remain distinct from full I6, I7 and Quest/H acceptance.

For N07 follow the [independent keyfile/pixel contract](features/linux-desktop-v3/i6-n07-a-independent-oracles.md),
[B3 pixel-review preparation](features/linux-desktop-v3/i6-n07-b3-pixel-review.md)
and [B4 same-storage two-session collection](features/linux-desktop-v3/i6-n07-b4-two-sessions.md).
Actual collection, independent Golden approval and B5 formal acceptance are
separate: N07 remains **0/13 verified**, with the production Golden pin unset.

I5 implementation work starts from main `eca71addc58009b4576644da162d56670eebfaf5`, where PR #16 is merged.
The [I5 detailed design](linux-real-app-v3-i5-detailed-design.md) records the current-source delta, two-stage retirement,
storage transaction, admission epochs, typed StopReceipt, normal launcher and V09/V10 conditions.
I5 remains manually discoverable (not a registered resolver task); this link grants no design approval or Quest acceptance.
The [I5 verification record](linux-desktop-v3-i5-validation-20261114.md) maps V09/V10 tests and records the remaining I6/I7/H acceptance work.

The [I6 detailed design — normal v3 Host acceptance (I3-T20/V11)](linux-real-app-v3-i6-detailed-design.md)
is a **proposed**, manually discoverable design based on main `0e0df43a2ce4734a00a36f830cc24a80e277bec5`.
It specifies the real SSH/C2/Waypipe/Mousepad path, Host rendering, 28 required case families, cleanup evidence,
an independent validator and CI integration. I6 remains unregistered and unimplemented at that baseline;
the design is not Host acceptance, real SDK/Android storage verification, or Quest/H evidence.

The [I6 evidence validation implementation](features/linux-desktop-v3/i6-evidence-validation.md)
adds the fixed 28-family / 87-variant manifest, strict evidence validator and separate CLI stage outcomes.
It describes the implemented contract checks, remaining product oracles and the N01 real-SSH handoff.
I6 remains manually discoverable; this partial implementation cannot report Host acceptance passed.

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
