# PR-01: Linux Desktop v3 integration contract handoff

Date: 2026-11-11 JST. Scope: **shared types and pure validators only; no v3 product execution**.

## Source of truth and dependency

- Main HEAD at start: `1434f46e7ba95ab71cfcf26040b4fbb4e43756ad`.
- PR-00: [#12](https://github.com/tin-machine/eidolon-wayland/pull/12), validated head `65f9fa38b3cf2f6d99c5c5244e4d4f4d47ce3a4d`; **merged into main** at `f894d796a06bcf0fafb1567718cd180ecf29009b`.
- PR-01 was initially stacked on PR-00. Following that merge, PR #13 was retargeted to **main**, and main was merged into the PR branch without altering its nine-file functional diff. I2–I5 should branch from the eventual merged PR-01, not from this open PR.
- Primary plan: [linux-real-app-v3-integration-plan-20261111.md](linux-real-app-v3-integration-plan-20261111.md), especially §§3, 7–10, 16–21.
- Existing B1/B2/C1/C2/E1 contracts remain authoritative. I1 `ConnectionConfig` and existing v3 `StorageRecord` are reused; no new wire/journal/hash/source format is defined.

## Implemented shared contracts and exact API

### Model: runtime selection and immutable configuration

`native/model/src/commands.rs`:
- `RuntimeConfig::linux_desktop_v3: Option<String>` is an escaped JSON runtime field. It is **not** an Android import or remote response.
- v2 is selected by `linux_profile_v2 + linux_desktop_pins`; v3 by `linux_profile_v2 + linux_desktop_v3`.
- Both selected -> `InvalidArgument`; no profile -> `InvalidArgument`; malformed v3 -> **reject**, never fallback to v2.
- Stored v2/v3 settings may coexist on Android; only one runtime backend may be selected. Android's active-backend marker belongs to I5.
- The existing `COMMAND_BYTES` = 16,384 cap remains for outer runtime JSON; `ConnectionConfig::parse` retains its independent 32,768-byte import cap. Do **not** raise the JNI limit to make an oversized configuration bootable.

`native/model/src/linux_desktop_v3/configuration.rs`:
- `ConnectionConfig::parse(&[u8])` (I1) checks schema/kind/Pins/Manifest/canonical hash and import limit.
- `RuntimeBinding::parse(&[u8])` decodes exactly `{"configuration": ConnectionConfig, "storage": StorageRecord}` through B1 strict JSON (including duplicate-key rejection).
- `RuntimeBinding::matches_profile(&Profile)` checks E1 `StorageRecord::matches_capture(StorageCapture::from_profile(profile))`.
- `RuntimeBinding::configuration()`, `storage()`, `record()`, `canonical()`, `sha256()` provide non-mutating access.
- Neither record authorizes remote execution by itself; I2 must compare compiled source hashes and authenticated UID against the captured binding.

### Transport: one version selection, no execution fallback

`native/transport/src/connect_profile.rs`:
- `DesktopConfig::{V2{profile,pins,files}, V3{profile,configuration,storage,files}}`; `version()` returns existing `linux_desktop::dispatch::Version`.
- `ConnectSpec::linux_desktop(...)` retains v2; `ConnectSpec::linux_desktop_v3(profile, config, storage, files)` checks backend, fixed remote-linux profile ID, absolute filesystem root and storage capture before storing the typed v3 tuple.
- `Controller::connect_spec` currently returns **NotReady for V3**; v3 actor integration belongs to I2. Existing v2 run/recover branches explicitly match V2 rather than interpreting v3 as v2.
- The v2 journal, 11/12 argument vector, pending handling and source table remain unchanged.

### Typed local commands

`native/model/src/commands.rs` adds three command variants:
- `linux_desktop_v3_inspect {schema,request_id,session}`
- `linux_desktop_v3_launch {schema,request_id,session,selection}`
- `linux_desktop_v3_recover {schema,request_id,session}`

The Launch `selection` is **B1 `linux_desktop_v3::Selection`** (scope, revision, fixed app ID `gtk3-desktop` or `mousepad-editor-v1`). Neither arbitrary executable/argv/path/storage/source hashes nor origin/nonce/ready are inputs. `mousepad-editor-v1` is a catalog ID, not Wayland `mousepad`.

`native/core/src/runtime.rs` explicitly returns **NotReady** for these variants until I2/I3/I4 real implementations. Stop remains the existing session ownership stop path. Accepting/queuing Launch is never counted as Started or Ready.

### Reports, B1 origin and reclamation

`native/model/src/linux_desktop_v3/report.rs`:
- `V3Report` provides protocol revision and **existing B1 `NativeOrigin`**, typed `TransportState` / `TransportPhase`, optional `PresentationSummary`, optional `SdkTarget`, `RemoteCleanup`, `LocalRetirement`, `RuntimeReclamation`, `first_failure`, `cleanup_error`, `cleanup_pending`, `relaunch_allowed`.
- `PresentationSummary::from_startup(&Startup)` reads the existing B1 `Startup::summary()`. Do not implement a competing startup state machine.
- `SdkTarget` carries full `RuntimeHandle` and `TargetKey` (including scene/generation), Create command sequence/action, ACK/binding/frame evidence. Its type does not **generate** evidence; I4 must supply observed callback/renderer values.
- `V3Report::validate()` rejects foreign runtime/target, a Ready summary without coherent SDK target evidence, and `relaunch_allowed` without **all** remote/local/runtime cleanup milestones + no pending journal/cleanup.
- The `session::Snapshot::desktop_v3` field is **None until the v3 controller exists**; skipped from v2 serialization when None. V2 `desktop` and `desktop_recovery` fields are unchanged.
- `NativeOrigin::runtime/session/request/app_id` are read-only accessors on the B1 type. I3 uses the actual listener acceptance and role/parent history; NEVER title, app_id strings, or remote claims as authority.

## Next PR ownership: concrete integration checklist

| Owner | APIs/files to consume | Not in PR-01 |
| --- | --- | --- |
| **I2** | `ConnectSpec::linux_desktop_v3`; `DesktopConfig::V3`; `RuntimeBinding`; `dispatch::{Version,Binding,Protocol,RecoveryRequest}`; `V3Report`; `session.rs` | New `native/transport/src/linux_desktop/v3/runtime.rs`, SSH actor, catalog/Launch/Stop/Recover, B2 journal finalizer. Remove NotReady only with real ownership and tests. |
| **I3** | `NativeOrigin` getters; B1 `presentation::{Parent,Window,Observation}`; `TargetKey` | Real accepted Wayland client origin in compositor, WindowHistory, fixed-main/dialog admission, input gating. |
| **I4** | B1 `Startup::begin/advance/summary`; `PresentationSummary::from_startup`; `SdkTarget`, `V3Report` | Actual SDK Create ACK + binding + renderer frame correlation and deadline enforcement, multi-generation retirement. |
| **I5** | `RuntimeConfig::linux_desktop_v3`; `RuntimeBinding`; v3 command names; `V3Report`; E1 `LinuxStorageRepository` | Actual Android v3 import/active-backend, native config passing, launcher UI and **real** E1 ReclamationBarrier/CAS. |

Important ordering: a `Ready` report requires authentic I3/I4 observations; a `relaunch_allowed` verdict requires I2 remote + I3/I4 local + I5 runtime cleanup evidence. None of these are implied by parsing or constructing a report.

## Compatibility/prohibited work

- Untouched: v2 fixed sources, 11/12-arg wire, v3 14/15-arg wire, B1 9-field pins, canonical SHA-256 and golden hashes, B2 journal/Recover, C1/C2 profile/helper, E1 storage identity, Termux route.
- Unimplemented: v3 controller, compositor client admission, WindowHistory, SDK callbacks, Android CAS/UI, physical Quest validation, I6/I7 acceptance.
- Avoid adding new local caches of storage IDs or a competing native owner state machine. The report is a **view**, not the cleanup authority.
- Snapshot must never include SSH stderr, private host paths, credentials, edited Mousepad document contents.

## Test plan / validation evidence

New Rust regression tests cover v2/v3 runtime mutual exclusion; matching and stale E1 identity; strict nested duplicate JSON, malformed Manifest; typed v3 command wire and forbidden fields; frozen version on `ConnectSpec`; ACK/Started/Ready and cleanup barriers.

Run after checkout:
```sh
# Execute from the repository root (the Cargo workspace root).
cargo fmt --all --check
cargo test --locked
cargo clippy --locked --workspace --all-targets -- -D warnings
python3 scripts/check-design
scripts/check-host --suite all
git diff --check
```

Android code and fixtures were not changed by PR-01; still require existing Android CI to complete. **CI results must be taken from the actual PR head**, not PR-00's previously green head. Do not report tests as successful before evidence is available. For failed checks, record the exact run, job and fix separately.
