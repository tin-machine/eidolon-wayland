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
