# PR #16 — I3/I4 P1 verification and outstanding real-SDK evidence (2026-11-14)

Status: **Draft / NOT merge-ready**. This report distinguishes inspection, source changes, CI and real Quest evidence. No simulated SDK callback is an acceptance proof.

## Verified code-level contracts

- `native/core/src/compositor/snapshot.rs::register_toplevel` takes `ProtocolState.next_window` and advances via `WindowId::checked_next()`. Its `ProtocolState::new` allocator begins at 1. Numeric IDs are non-reused within a compositor Session, but **not globally unique across Sessions**. A retired high-water mark is sound only for previously unknown IDs, never as a filter for older *live* IDs.
- `WindowHistory` preserves pending native/SDK tombstones, removes only double-confirmed retirements, and now excludes still-live entries from the retired high-water check. A bounded 64-record unresolved ledger fails closed rather than discarding unacknowledged receipts. This is an owner safety budget, **not a successful reclamation**.
- `Session::set_v3_input_allowed` intersects the supplied set with the current I3 admitted cut. Explicit target-less IME paths also check current window admission. The I4 runtime owner still supplies an **empty** SDK-approved input set.
- Actual `SpatialPanelAdapter` is not presently wired into the I4 `SdkEvidenceBridge` lease/Scene/Entity receipt producer. The `V3PresentationObserver` is called from the native owner with verified transport/I3 cuts but no trusted SDK ACK, binding, renderer frame.
- `WindowHistory::confirm_retired` has no production caller verified in this inspection. It must be reached only by evidence from **both** real native + SDK retirement, not by an observed Destroy/unmap or timeout.

## P1 status, mandatory before converting PR to Ready

| Scope | Current source inspection | Required proof before merge |
| --- | --- | --- |
| I3 ID churn / tombstones | Source fix and targeted Rust tests committed; **CI pending** | Rust format/build/clippy/test on new HEAD; actual 1001-cycle ownership path |
| I3 Wayland lifecycle | Real Unix listener accept-origin tests exist; most xdg parent/destroy cases are model-level | Real client/Smithay xdg_toplevel lifecycle dispatch (I3-T01–T10, T18); rejected role does not disrupt other clients |
| I4 SDK current evidence | Typed builder/FSM, Kotlin ledger are present; no live adapter evidence path | Reserve CreateIdentity before SDK work; immutable callback lease; ACK matches exact Create command; actual Entity/Surface/listener; renderer target/frame generation; B1-only Ready |
| I2→I4 all-generation retirement | I2 finalizer and Kotlin retirement ledger exist in isolation | Enumerate all origin generations and SDK objects; native detach/fence, Entity delete listener and later same-Scene pass; double proof to `ReclamationAuthority::confirm`, `finalize_reclaimed`; preserve pending on unknown/late ACK |
| Input | v3 default deny, Session gates, new IME + admission intersection changes | Host regressions for keyboard, pointer, focus and IME, including stale scene/target, then Quest confirmation |

### I3-T01–T20 acceptance map

- **Source-level only / partial host fixtures:** T01 (accept origin), T02 (session mismatch), T04 (Started versus Mapped), T05 (second root), T06 (unknown parent), T07 (basic same-origin chain), T08 (fixed main), T10 (9 limit), T18 (1001 dialog churn with explicit *test-supplied* ACK). The churn fixture is not an SDK receipt.
- **Requires real Smithay protocol integration:** T03, T06–T11 (parent changes, unmap and destroy), T18 (real ACK and budget pressure). Do not treat WindowHistory mock samples as real Wayland lifecycle proof.
- **Requires actual SDK + owner / end-to-end verification:** T12–T17, T19 (v2/Termux, IME) and T20 (real SSH+C2+Waypipe+Mousepad 0.6.3); especially no Quest acceptance is recorded.

## Quest 3 runbook (to execute only after real SDK wiring)

1. Pin APK SHA, native/bundle build ID, host Mousepad/GTK/Waypipe versions, target host SSH public key identity, Runtime/Session/LaunchRequest/WindowId, SceneEpoch and SurfaceGeneration; redact credentials and nonce.
2. Launch one Mousepad; prove transport Started and a single B1 main adoption before any panel Create. Capture *actual* SDK Create command/ACK, Entity ID, Surface and listener lifetime, current renderer target and first frame.
3. Map an actual dialog; verify parent chain and 9-window ceiling. Perform keyboard, pointer, focus-switch and IME on both windows; deny input before current evidence, after unmap, and when old Scene/target ACK arrives.
4. Replace Scene / re-create a surface while an old callback is in flight. Confirm old callback cannot register, attach, signal Ready or release a new-generation binding.
5. Stop normally and by forced disconnect. Track each generation, including dialogs and fallback targets, from native render detach through SDK deletion callback and subsequent cleanup pass. A lost ACK leaves journal pending and blocks relaunch.
6. Prove a clean recovery/Stop yields a unique I2 finalizer receipt and durable journal clearing. Reboot/recover with an intentionally unresolved old generation: no Ready or relaunch until old ownership is resolved. Verify v2/Termux smoke separately.

Expected evidence: exact tuples and monotonic sequence stamps for each ACK/frame; current B1 state transitions; per-generation `pending/confirmed` retirement counts and journal stage; no fabricated zero-count success. Quest physical acceptance remains **NOT RUN**.

## Additional source changes after this record

- Real UnixStream + Smithay xdg_toplevel lifecycle test added: `native/core/src/compositor/protocol.rs::v3_accept_tests::real_xdg_toplevel_propagates_captured_origin_through_map_and_destroy`, using the native-controlled fixture `eidolon_shm_fixture::run_with_app_id`. Tests actual map/destroy and pending native+SDK retirement tombstone; SDK evidence itself is **not** mocked as success. This covers a part of I3-T01/T04/T08/T09 and does **not** cover all dialog-parent transitions or SDK integration.
- B1 `Startup.roles` still lacks a certified retirement callback. `V3PresentationObserver::tick` now fails closed at 65 distinct retained roles, keeping memory bounded and preserving the pure B1 model/golden contract. No successful production GC claim is made. The threshold is temporary protective admission limiting, not a substitute for native + SDK retirement callbacks.
- Rust 1.95 rustfmt corrections applied from the CI Host job of run [#43567443395](https://github.com/tin-machine/eidolon-wayland/actions/runs/43567443395). The job stopped before compile/testing, so these changes and the additional host cases remain **not verified by build**.

## 2026-11-14 PR #16 incremental I3 checkpoint — not yet complete

Commits on the same Draft branch, all ordinary non-force updates:

- `de83dca`: preserve implicit Smithay reparent/unmap as Unresolved and require mapped ancestors before allowing dialog input. Added pure model regression coverage.
- `84dfd28`: keep retiring/unmapped window slots reserved until certified double retirement; extend the existing real Unix Wayland capacity fixture to verified-parent v3 topology, and add 9→10 protocol test. Its manually supplied native/SDK ACK is **fixture-only**, not product evidence.
- `48580b9`: seal new v3 Wayland clients/xdg roles/popups on Stop or fatal failure while keeping existing protocol state for cleanup; add listener-seal test.
- Subsequent host test: real Wayland app_id/title mutation on an already-mapped main, assert immutable accepted origin and revocation of inconsistent main, without inventing SDK evidence.

This change set does **not** replace the current runtime's `set_v3_input_allowed(Default::default())`. The real `SpatialPanelAdapter` still has no complete immutable callback lease, delete-observed fence or same-scene cleanup pass connected to `SdkEvidenceBridge`. Therefore real SDK ACK/Entity/Surface/current-frame proof and production `WindowHistory::confirm_retired` / B1 role GC / I2 journal finalization remain **P1 blocking**.

### I3-T01–T20 review matrix (test source present ≠ verified full acceptance)

| ID | Status | Remaining full acceptance oracle |
| --- | --- | --- |
| T01 | PARTIAL | Real v3/v2 accept separation present; multi-socket lifecycle not fully exercised |
| T02 | PARTIAL | Foreign-session origin reject + listener seal; closed socket and cross-runtime rebinding not fully exercised |
| T03 | PARTIAL | New real app_id/title mutation fixture; full UI revoke/seat behavior not verified |
| T04 | PARTIAL | Started/Map permutations in model and real main map, B1/transport sequence incomplete |
| T05 | PARTIAL | Second root model guard, real simultaneous roots not tested |
| T06 | PARTIAL | Parent unmap and implicit reparent handling improved, real reparent/destroy fixture incomplete |
| T07 | PARTIAL | Ancestor must be mapped; real two-level dialog/self/cycle/foreign-parent set incomplete |
| T08 | PARTIAL | Fixed-main/destroy tests; real replacement refusal still needs host integration |
| T09 | PARTIAL | Model WindowId history preservation; actual unmap/remap + SDK generation test incomplete |
| T10 | PARTIAL | Real 9→10 fixture and ACK-held slot test added; no product SDK retirement proof |
| T11 | PARTIAL | Legacy popup/subsurface fixture exists; v3 inherited input admission test incomplete |
| T12 | PARTIAL | Native default-deny input guards exist, full seat/pointer/IME/same-target matrix incomplete |
| T13 | BLOCKED | Positive real SDK Create ACK/Entity/Surface/renderer content frame absent |
| T14 | BLOCKED | Real scene/generation stale callback integration absent |
| T15 | PARTIAL | Move-only I2 permit and Stop transport logic exists; full lost-ACK race unverified |
| T16 | BLOCKED | Real SDK/native retirement and same-capture finalizer not connected |
| T17 | BLOCKED | Cold-crash unknown-owner durable roster/proof not connected |
| T18 | PARTIAL | 1001-cycle model test with supplied ACK and 64 pending fail closed; no certified production GC |
| T19 | PARTIAL | v2/Termux legacy paths remain; full mixed input/compatibility regression unverified |
| T20 | NOT RUN | Actual SSH+C2+Waypipe+Mousepad 0.6.3 acceptance and file oracle unavailable |

Build verification status: the original `7ed8774` passed [Host/Android #43570854064](https://github.com/tin-machine/eidolon-wayland/actions/runs/43570854064), [L7a #43570854013](https://github.com/tin-machine/eidolon-wayland/actions/runs/43570854013), [CodeQL #43570854058](https://github.com/tin-machine/eidolon-wayland/actions/runs/43570854058). New source commits require **new HEAD** CI results. Intermediate [Host #43574185633](https://github.com/tin-machine/eidolon-wayland/actions/runs/43574185633) and [#43574852420](https://github.com/tin-machine/eidolon-wayland/actions/runs/43574852420) stopped at rustfmt before compilation; reported rustfmt diffs were repaired in subsequent commits. Do not infer Rust, Android, SDK or Quest success from these prior jobs.

P1 outstanding: verified SDK registration/Entity/Surface/callback and same-target B1 evidence; Scene/generation revocation of actual input; all-generation native+SDK retirement and production `confirm_retired`/`retire_confirmed_dialog`; I2 `take_v3_reclamation → confirm → finalize_reclaimed → publish_v3_finalized`; real multi-dialog/reparent/popup cases; real SSH/Waypipe/Mousepad and Quest validation. P2: add long-running timing/capacity/IME regression once P1 evidence owners work; keep Issue #27 (lease timing) tracked without duplicates. Never convert to Ready or merge until P1 resolved and real evidence is captured.

### I3 listener-owner correction after checkpoint

The actual `ProtocolState` now binds exactly one `NativeOrigin` from the I2-owned `Session::start_v3` **before** any listener accept. The same-session different-request origin and unverified `accept()` into that bound display are rejected. Separately owned v2/Termux ProtocolState remains untagged. This tightens I3-INV-01/02 and T01/T02 but does not create SDK evidence or complete acceptance.
