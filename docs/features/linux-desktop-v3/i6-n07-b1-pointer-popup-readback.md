# I6 N07-B1 — live pointer, xdg_popup and renderer readback diagnostic

> Implementation checkpoint: **Host diagnostic, not N07 acceptance**.
> N07/same_storage_restart remains **0/13 verified** until B2's two
> independent C2 sessions, pointer-operated Line Numbers, reviewed pixel
> reference and fully independently revalidated typed receipt exist.
> Maintain 28 families / 87 variants / 533 assertions, manifest
> e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33
> and `host_v3_acceptance_passed=false`.

## Authority and boundaries

Based on merged N07-A (PR #46, `9aaac53`). The N07-B implementation
ordering from PR #47 was read as draft guidance; no unmerged code dependency
is introduced. This PR does not change N01–N06 independent oracles, C1/C2
profiles or v2/v3 isolation. Quest physical-device / actual Meta SDK
verification is still I7/V12 and H, not a prerequisite for this Host probe.

Mousepad fixed upstream
[`menus.ui`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/gtk/menus.ui)
contains `_View`, `Line N_umbers` and
`app.preferences.view.show-line-numbers`. No keyboard shortcut is invented.

## Added product read-only seams

- The Smithay `XdgShellHandler` records actual `xdg_popup` creation,
  parent `wl_surface`, real `ack_configure` serial and valid grab serial
  (if grabbed). `popup_reports()` emits owned scalar data only: role,
  surface, parent, root WindowId, configure ACK serial, grab serial,
  snapshot layer index, captured origin match, and dismissed state. When a
  GTK popup explicitly grabs, its grab serial must match the actual
  initiating pointer press/release delivery serial. Popup
  state disappears on actual protocol destroy. It is **not** a synthetic
  `xdg_toplevel`, a new SDK panel, or an admission bypass.
- `PointerReport::delivered_buttons` counts actual `pointer.button`
  dispatches targeted at live surfaces, excluding synthetic reset releases.
  `input.delivered` remains the existing keyboard counter.
- The Host SDK diagnostic uses its pre-existing native Create lease and
  `Registry::guarded(Runtime::{request,poll}_host_readback)`, selecting
  explicit bounded non-center ROIs. Renderer remains authoritative for
  target generation, actual presented content frame, ROI bounds, backend
  identity and digest. The raw RGBA is exported **only** into a private
  bounded Host diagnostic artifact, never a normal Runtime or Android
  snapshot. This does not create or approve a golden.

## Host diagnostic flow and guards

`--n07-probe-variant pointer_popup_readback_probe` on the existing Rust
Host runner performs ordinary Inspect/Selection/Launch and SDK admission,
captures the pre-menu renderer ROIs, focuses current main target, sends
pointer move/button down/button up through ordinary Registry command
processing, demands real delivery count and current input epoch, then looks
for actual owned `xdg_popup` and captures renderer ROIs after popup
presentation. It follows N02 normal product cleanup, never direct Mousepad
execution or `GTK3_MODULES` injection.

**The candidate View-coordinate UV `[0.16, 0.063]` is deliberately
unreviewed.** This location was calibrated from a real Host renderer ROI in
PR #48's first Docker probe: the screenshot shows the View label near
content pixel (160, 48) on a 1024×768 main surface, whereas the first
candidate y=16 missed the menu bar. Observed pixels are *not* the golden. This first discovery must not claim the actual View menu
was opened merely because a popup appeared. A separate observed visual
calibration, precise named GTK menu item selection, and pixel checkmark
readback are still required. A missing xdg_popup is a diagnostic failure:
actual protocol traces must decide whether GTK chose another menu strategy.

`scripts/linux_real_app_v3_n07_b1_probe.py` validates saved Host receipts
as a **probe-only** verdict, with no N07 assertion promotion. It rejects
Registry-only ACK, wrong/stale epochs, foreign targets, different
capture, popup parent/serial/layer mismatches, missing or stale renderer
readback and pixel mismatch. `scripts/linux_real_app_v3_n07_b1_ci.py`
reopens saved receipts and reruns hostile mutations. The container runner
uses the existing source-pinned N01 Docker build recipe, non-root app and
driver accounts, loopback-only SSH with pinned host key, PR source SHA
distinguished from `CI_PR_HEAD`, fixed bounded artifact export, explicit
rescue distinction, and normal product cleanup. Unit-only comparator tests
also run without Docker.

### Operator command

```bash
bash scripts/ci/check-linux-desktop-v3-n07-b1 --artifact-root /tmp/i6-n07-b1-private-empty
```

This is intentionally an on-demand real diagnostic, not an assertion
pass; final report `status=probe_only`, `verified_n07_assertions=[]`
and `host_v3_acceptance_passed=false` even after a successful probe.
A failed exploratory click must fail closed rather than silently create
a golden or a successful typed receipt.

## Boundaries remaining for N07-B2 and golden review

1. Observe real GTK popup trace and **independently calibrate actual menu
   position**. Confirm `_View`, then use normal pointer to click
   `Line N_umbers` and verify delivered button source, exact menu popup,
   capture, epoch, parent chain, configure/ack/grab serial and layer.
   Read all six N07 ROIs, not B1's two exploration regions.
2. Independent golden review: pin Mousepad/Waypipe/GTK/Pango/Cairo,
   font/fontconfig/Mesa/theme/locale/scale, ROI/mask/tolerance and
   accepted SHA in N07-A's `load_reviewed()`. Never use the acceptance
   run's observed pixels as its expected fixture.
3. New first C1 StorageRecord, independently read key absent, GUI OFF,
   pointer change ON, GUI ON, persistent key true. Finish normal Close,
   all C2/renderer/SDK/native/Registry cleanup and journal finalizer.
4. Reuse the **same C1 StorageRecord**, new nonce/capture/Runtime/app owner,
   with no forced C2 lock release. Inspect/Launch, open fixed eight-line
   text, prove 1–8 line numbers and checked menu from *actual renderer*,
   independent keyfile true; full second cleanup and origin retirement.
5. Build an independent N07 acceptance oracle, two-session typed receipt,
   exporter/revalidator and `linux_real_app_v3_evidence.py` integration.
   Only actual saved evidence may satisfy the 13 frozen assertions.
   Even 13/13 does not make overall I6 complete; Quest remains separate.

Natural Close versus abnormal termination remains Issue #45. N08 must
create fresh StorageRecord isolation and may reuse **only read-only
probes**, not N07 receipt ownership.

Primary references:
[Wayland xdg_popup](https://wayland.app/protocols/xdg-shell),
[Wayland book popup lifecycle](https://wayland-book.com/xdg-shell-in-depth/popups.html),
[Mousepad fixed menu](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/gtk/menus.ui).
