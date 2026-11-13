# Curved Workspace — D6 feature index

**Registered for deterministic discovery, not accepted/implemented/Quest-verified.** Reviewed against `main` `443d5f740053efc0c55914ed4c556118e54d4220` (2026-11-13). No existing document was moved; the [original reading-order index](../../curved-workspace/README.md) remains the human design entrypoint.

Use the existing [CW00–CW25 task list](../../curved-workspace/08-work-items.md) as the task/status authority; the D6 manifest maps each CW task to a **reading set**, not to a completed implementation. The [original design proposal](../../curved-workspace-immersive-terminal-design-20261111.md), [wire contracts](../../curved-workspace/01-contracts.md), [geometry](../../curved-workspace/02-geometry.md), [runtime](../../curved-workspace/03-runtime.md), [Android input](../../curved-workspace/04-android-input.md), [persistence](../../curved-workspace/05-persistence-presets.md), [Cylinder](../../curved-workspace/06-cylinder.md), and [tests](../../curved-workspace/07-tests.md) retain their original URLs and wording.

```sh
python3 scripts/docs.py context --feature curved-workspace --task CW02
python3 scripts/docs.py context --feature curved-workspace --task CW12
python3 scripts/docs.py context --feature curved-workspace --task CW19
python3 scripts/docs.py affected --path native/core/src/workspace.rs
```

**Boundary:** the existing Flat/Wayland workspace source is a change-impact reference, **not evidence that CW01–CW25 is implemented**. Source baselines and recorded Meta SDK feasibility are historical; real Cylinder API and Quest UX evidence remain unverified. Fixture JSON files are kept byte-for-byte in `docs/curved-workspace/fixtures/` and still trigger conservative product CI when changed.

The `CW00` through `CW25` manifest mapping is a **navigation projection** of sections in `08-work-items.md`; the work-items document, not the manifest, remains the definitive instructions, dependencies and acceptance criteria. `context` returns paths, not document bodies.
