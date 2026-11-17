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
presentation. The diagnostic Host keeps the press active for at most two
seconds while checking actual configured/mapped popup state, before sending
release: GTK can require a remote Waypipe round trip to request
`xdg_popup.grab`, and the compositor's current grab policy checks that the
initiating button remains pressed. The timeout always permits release; it
never treats a Registry ACK, elapsed time, or a transient popup as success.
The Host records `popup_visible_before_release` only as additional
diagnostic evidence. It follows N02 normal product cleanup, never direct Mousepad
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

## N07-B2 implementation checkpoint — four real GUI candidate ROIs

This checkpoint is **not** N07 acceptance, does **not** approve the N07-A
pixel golden and does **not** run a second C2 session. It extends the
merged N07-B1 product read-only seams rather than rewriting ownership or
adding GTK introspection. The new Host variant is
`line_numbers_four_rois_probe` and the independent saved-artifact gate is
`scripts/linux_real_app_v3_n07_b2_probe.py`.

### Deterministic transaction and measurements

The same Registry/Runtime/SDK Create target is held throughout. N07-B1
obtains `before_menu`, opens the real View xdg_popup via its ordinary
pointer commands (including bounded press/grab delay), binds popup
parent/root, ACK, grab serial and renderer layer, and obtains `menu_open`.
B2 then measures:

| B2 observation | Real readback ROI | Timing |
| --- | --- | --- |
| `first_off_gutter` | B1's initial real ROI 1, (0,64,48,128) | Before first View click |
| `first_off_checkmark` | (112,96,112,128) | While initial owned popup is visible |
| `first_on_gutter` | (0,64,48,128) | After candidate Line Numbers pointer and popup dismissal |
| `first_on_checkmark` | (112,96,112,128) | After ordinary Registry pointer reopens View |

The initial pointer position is the B1 measured **unreviewed** View
candidate UV (0.160,0.063), corresponding to (164,48) at 1024×768.
The Line Numbers candidate is UV (0.195,0.164), corresponding to
(200,126). CI diagnostic run `44089737962` retained a real View popup crop:
the previous y=152 click was actually on **Menubar**, not Line Numbers;
the compositor forwarded the click to its popup surface, but the native
probe rejected missing post-action content-frame progress. The
visible Line Numbers row is near y=126, so B2 calibrates this coordinate
from real renderer data. This is a **discovery**, not a reviewed golden,
a verified checkbox toggle, or formal N07 evidence.

The proposed menu item is third in the fixed Mousepad
0.6.3 upstream View menu (following Select Font and Color Scheme), but
**neither coordinate nor observed glyph has been independently reviewed**.
The region is broad enough to retain post-run diagnostics. No shortcut,
private GTK call, direct settings mutation, or replacement renderer is used.

The Host demands actual compositor forwarded button deltas for both
press and release, actual popup-surface delivery for the item, unchanged
input epoch and capture/target generation, popup dismissal, newer content
frame, an independently created second View popup and newer after-state
readbacks. Any failure leaves a partial bounded native diagnostic and
normal N02 product retirement. B2 does **not** manufacture a successful
Line Numbers observation if a candidate click opens a different item.

`linux_real_app_v3_n07_b2_probe.py` independently reopens the saved
B1 provenance/cleanup evidence, validates the four staged RGBA/SHA-256
payloads, SDK Create lease, renderer owner/context, target generation,
backend, exact ROI rectangles, frame ordering, popup parent/serial/layer,
and a nonzero OFF/ON pixel contrast. The same observer compares real
bytes only; an observed frame is never an expected golden. The test
includes malicious receipt mutations and the source-bound disposable
non-root Mousepad job. No CI green status is permitted to update
`host_v3_acceptance_passed=false` or N07's **0/13 verified**.

### Trust boundary / handoff

**Only diagnostic**: without independent review of the candidate
coordinates, exact menu item identity, checkmark/gutter glyphs, and a
reviewed pixel golden, OFF/ON semantic labels remain hypotheses.
No keyfile boolean measurement, verified second session, or typed N07
receipt is produced by this stage. Pixel readback remains bound to
the *first* capture only; `second_on_numbers_1_8` and
`second_on_checkmark` are expressly forbidden in the B2 record.

B3 must independently review the actual image pixels, refine the menu
coordinates/ROIs as needed, freeze Mousepad/GTK/Waypipe/fonts/Mesa
fingerprint and golden SHA; B4 must use a second nonce and same C1
StorageRecord to test persistence; B5 can only then integrate the
independent 13-assertion N07 acceptance oracle. Quest 3 Meta SDK
proof remains a separate I7/H milestone.
