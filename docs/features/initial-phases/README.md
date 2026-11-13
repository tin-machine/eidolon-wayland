# Initial Phase 0–6 — D6 feature index

**The immutable authority boundary is the existing [design/tasks.json](../../design/tasks.json), [Phase README](../../design/README.md), [shared contracts](../../design/contracts.md), and `scripts/check-design`.** D6 only adds a navigation projection. It does not change any task ID, `depends_on`, spec anchor, status, completion check, or source fixture.

The manifest maps **all 31 existing task IDs** to the same phase Markdown files their `tasks.json[*].spec` fields already identify. It also exposes `TH00`–`TH05` as *supplemental reading pointers* to the [theme/animation design](../../design/theme-animation.md), which has its own work-item status; they are **not** added to `tasks.json`.

```sh
python3 scripts/check-design
python3 scripts/docs.py context --feature initial-phases --task B01
python3 scripts/docs.py context --feature initial-phases --task T01
python3 scripts/docs.py context --feature initial-phases --task W03
python3 scripts/docs.py context --feature initial-phases --task TH00
python3 scripts/docs.py affected --path native/core/src/runtime.rs
```

Registered source design includes [Phase 0/1](../../design/phase-0-1.md), [Phase 2](../../design/phase-2.md), [Phase 3](../../design/phase-3.md), [Phase 4/5](../../design/phase-4-5.md), [Phase 6](../../design/phase-6.md), and the [source references](../../design/sources.md). The metadata status for proposed contract/design text is **not** a claim that all parts of the implementation remain undone: current task state is governed by `tasks.json`, and the actual code+CI+Quest evidence must be checked separately.

**Do not transform a `done` task into HMD verification**: `tasks.json` describes distinct automated and manual conditions. The historic baseline in a design document cannot prove current runtime state. Existing task-spec links and anchors remain untouched. Curved Workspace, Spatial Anchor, and Android IME are separate feature indices; they do not silently modify Phase 0–6 acceptance.
