# Linux Desktop v3 — managed documentation entrypoint (D5)

> **Scope:** this is the first *reviewed registration*, not an approval of the complete design, implementation, or Quest acceptance.
> **D5 source-review baseline:** `151639486d41f6117a7e44c2d977af19ee430ee6` (main, 2026-11-13 JST). This pins the mapping, **not** the historical claims in the linked texts.
> **Last reviewed:** 2026-11-13. Re-check latest GitHub PRs and sources at the start of each implementation task.

## Current provenance and authority boundaries

- The repository has B1 model/presentation, B2 v3 transport/bundle, C1 remote profile, C2 helper and E1 storage sources in `main`; their dated documentation may describe earlier implementation states. **Code present is not Quest evidence.**
- [PR #13 — shared product integration contract](https://github.com/tin-machine/eidolon-wayland/pull/13) and [PR #16 — I3/I4 integration](https://github.com/tin-machine/eidolon-wayland/pull/16) were **open, not merged** at the D5 baseline. PR #16 targets the PR #13 branch. Do not silently treat their APIs or check results as present in `main`.
- B1 contracts, the integration plan, I2 design and I4 design are registered as **`proposed`**. D5 does not infer human acceptance from merge status, a document title, or passing tests.
- B2/C1/C2/E1 documents are registered as **`historical`** implementation/handoff records, not current implementation authority. Their historical statements must be verified against the current source.
- **No** `design_status: accepted`, `quest_verified`, `production_acceptance_passed`, or `available` decision is made by this index. Real-device verification requires independently pinned evidence.

## I3 design proposal (manual discovery; not registered)

- [I3 detailed design — listener origin, WindowHistory and admission](../../linux-real-app-v3-i3-detailed-design.md) is a **proposed**, implementation-oriented design reviewed against I2 [PR #28](https://github.com/tin-machine/eidolon-wayland/pull/28) and I4 [PR #16](https://github.com/tin-machine/eidolon-wayland/pull/16) heads as observed on 2026-11-14. It includes a cross-PR change-request matrix, bounded ownership design, and V06–V08 tests.
- **I3 remains unregistered in this D5 manifest**; do not invoke `context --feature linux-desktop-v3 --task I3` expecting success. Follow the manual document and verify current PR heads. The document is not approved, implemented, or Quest-tested merely because it is linked here.
- Before joining I3 and I4, also read the [I2 design](../../linux-real-app-v3-i2-detailed-design.md), [I4 design](../../linux-real-app-v3-i4-detailed-design.md), and [B1 presentation contract](../../linux-real-app-b1-presentation-20261110.md) independently.

## Use with the deterministic resolver

### I5 implementation design (manual discovery)

The [I5 detailed design](../../linux-real-app-v3-i5-detailed-design.md) compares main
`eca71addc58009b4576644da162d56670eebfaf5` with the storage/barrier/launcher requirements.
PR #16 is merged at this implementation baseline; the D5 observations above retain their original date and meaning.
I5 remains unregistered. Read its explicit source dependencies and separate I2/I4 resolver contexts.
See the [I5 implementation verification and handoff](../../linux-desktop-v3-i5-validation-20261114.md) for local V09/V10 coverage, environment limits, and the separate I6/I7/H acceptance boundary.
The document does not establish product acceptance or Quest evidence.

```sh
python3 scripts/docs.py validate
python3 scripts/docs.py context --feature linux-desktop-v3 --task I2
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4 --format json
python3 scripts/docs.py affected --path native/core/src/runtime.rs
python3 scripts/docs.py drift --feature linux-desktop-v3 --task I4
```

`context` returns a **bounded, dependency-ordered list of paths and metadata**, not the documents' full text. Read the returned files selectively. `clean` means no related source changes since the pinned baseline, **not** implemented/accepted/Quest-tested. `unknown` remains unknown on missing commits, incomplete history, or unsupported source mappings; never upgrade it to `clean`.

Each task includes the [B1 contract](../../linux-real-app-b1-contract-20261110.md) and [overall integration plan](../../linux-real-app-v3-integration-plan-20261111.md). The explicitly registered tasks are `B1`, `B2`, `C1`, `C2`, `E1`, `I1`, `I2`, and `I4`; **I3/I5 are not registered in this manifest**. Use their manual design links and verify current implementation and PR status separately; registration is not an acceptance result.

### Registered document map

| Managed ID suffix | Document | Registered type / status |
| --- | --- | --- |
| `b1-contract` | [B1 shared contract](../../linux-real-app-b1-contract-20261110.md) | contract / proposed |
| `b1-presentation` | [B1 presentation contract](../../linux-real-app-b1-presentation-20261110.md) | contract / proposed |
| `b1-storage-identity` | [B1 Android storage identity](../../linux-real-app-b1-android-identity-20261110.md) | contract / proposed |
| `b2-bundle` | [B2 bundle record](../../linux-real-app-b2-bundle-20261110.md) | historical |
| `c1-profile` | [C1 profile/handoff](../../linux-real-app-c1-handoff-20261110.md) | historical |
| `c2-helper` | [C2 remote helper](../../linux-real-app-c2-helper-20261110.md) | historical |
| `e1-storage` | [E1 Android storage](../../linux-real-app-e1-storage-20261110.md) | historical |
| `integration-plan` | [L7a / v3 integration plan](../../linux-real-app-v3-integration-plan-20261111.md) | architecture / proposed |
| `i2` | [I2 SSH controller detailed design](../../linux-real-app-v3-i2-detailed-design.md) | detailed-design / proposed |
| `i4` | [I4 Startup / SDK evidence detailed design](../../linux-real-app-v3-i4-detailed-design.md) | detailed-design / proposed |

All IDs have the prefix `linux-desktop-v3-`. The 10 originals remain at their existing URLs; D5 adds Front Matter only and does not move or rewrite their dated observations.

### I2 ↔ I4 dependency and bounded context

I4 **depends on I2 behavior** and I3's eventual window admission, but the full I2 and I4 documents plus transitive C1/C2/B2 dependencies exceed D3's default **250,000-byte** per-context limit if combined. The I4 task therefore intentionally resolves its **direct evidence contract** (B1 presentation) plus the shared baseline; it does **not** pretend to include the entire I2 detailed design.

**Before implementing or reviewing I4, first run `context --feature linux-desktop-v3 --task I2` separately**, verify whether PR #13 and I2 source have landed, and separately check I3's branch/merge state. Treat the I2/I4 outputs as **two required reading sets**, not alternatives. If this requirement cannot be met, do not conclude the I4 design is ready for implementation.

### Verification and known limits

- `scripts/check-design` and `docs/design/tasks.json` for legacy Phase 0–6 remain unchanged.
- Unregistered v3 documents (B2 wire/transport, other B1 handoffs, A0 and L7a historical design), other features, and snapshot inventory rows are **not** silently accepted or superseded.
- The `related_sources` links are **change-review associations**, not evidence that every described API is implemented. Shared source files may appear in multiple document matches.
- D3 bounds remain 32 documents and 250,000 bytes by default; each registered task was sized separately under those bounds. A new dependency must be reviewed against the same budget; do not silently truncate.
- The repository's `scripts/check-host` / Android CI results and any future Quest/HMD acceptance reports are distinct. D5 runs no device tests and changes no product code.

See [D3 resolver contract](../../knowledge/resolver.md), [documentation system design](../../documentation-knowledge-base-design-20261113.md), and [CI change-scope contract](../../ci-change-scope-20261113.md).
