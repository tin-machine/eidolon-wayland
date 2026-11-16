# I6 N04 — 125-second idle / heartbeat / GUI liveness

N04 is **incomplete** until the normal Registry/Runtime/SSH/C2/Waypipe/Mousepad path, two independently observed Ctrl+O/Esc dialog lifecycles, an actual 125-second idle, request-matched heartbeat ACKs, and full retirement all pass a separate oracle. A producer status or a synthetic event must never be accepted as proof.

## Fixed contract

`N04/idle_125s` has exactly the ten assertions in `scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json`. The frozen manifest hash, 28 case families, 87 variants, 533 assertions, and `host_v3_acceptance_passed=false` must remain unchanged. PR #38 is unmerged documentation only and is not an implementation dependency.

## Independent evidence boundaries

The driver and disposable supervisor must capture separately sampled monotonic boundaries around idle **after** the first dialog closes and **before** the second starts; reject durations below 125 seconds on either clock. A heartbeat requires a unique `control_send_attempt(heartbeat)` and a later protocol `heartbeat` ACK with increasing request_sequence on the same capture; check every heartbeat in the idle interval, no gaps exceeding the original 5-second ACK/15-second lease. Do not alter product deadlines or add a total-runtime limit.

The dialog oracle must correlate request IDs with successful command completions, real delivered-key counters, current focus token/epoch, a new mapped child xdg_toplevel with matching parent and GUI admission, and subsequent unmap/destroy. The post-idle child ID and request IDs must be distinct. Stop ACK must precede complete product cleanup, SDK/renderer retirement, finalizer, Runtime stop and Registry destroy; watchdog/rescue is not product success.

The Host synthetic SDK can only provide S-class evidence; it is not Meta Spatial SDK/Quest evidence. While N04 is not fully implemented and independently validated, this PR must not set N04 PASS or mark formal I6 complete.

## ESC and Wayland destruction ordering

The first real Mousepad Open dialog may destroy its xdg_toplevel as soon as the
**ESC press** is delivered. The product input gate correctly rejects a later
keyup command against the retired dialog TargetKey as `NOT_READY`. Acceptance
must never count that stale-target rejection as a successful dispatch, and
must not require a new input command against the removed surface. Instead N04
requires the accepted ESC press, actual disappearance from the verified
WindowHistory/GUI admission, the compositor input reset counter advancing,
all held keys retired (`pressed == 0`), and the main window remaining live.
This keeps the keyboard-cleanup proof without weakening the input gate.

The saved N04 receipt projects only immutable B1-compatible Wayland fields
(window, parent, life, frame, app_id). Raw WindowReport contains floating-point
physical sizing and is not valid B1 evidence. A failed strict parse is
**missing evidence**, never a successful GUI observation.
