# I6 N07 — settings lifecycle contract v2

> **Explicit settings-contract revision, authorized on 2026-11-19 JST.**
> The user approved changing the earlier whole-keyfile / other-settings
> immutability requirement after reviewing its conflict with normal Mousepad
> and GTK persistence. This authorization applies to the bounded contract
> below; it does not approve a Golden, an N07 assertion or Host/Quest acceptance.
> This document is manually discoverable and is not a resolver registration.

The implementation baseline is PR #52 HEAD
`0a89dd7930d47a19a67b356491fbe564a0b3882c`, with main still at
`ee9192d12957a17aaa3d61bd6e8ba1ca86a99d6f`.
The [original B4 acquisition](i6-n07-b4-two-sessions.md) and
[dated settings-conflict analysis](i6-remaining-work-20261119.md#31-b4-の設定契約の整合)
retain their original results. The old rejected artifacts are not evidence
for this revision. New source-bound acquisition and independent saved-evidence
validation are required; this design record claims no new successful CI run.

## 1. Scope and retained contracts

The original [`validate_file_sequence()`](../../../scripts/linux_real_app_v3_n07_settings.py)
remains unchanged. Its v1 predicate still requires other-setting digest
invariance at all three canonical snapshots and identical post-toggle /
post-restart file bytes, size and owner. Its old failure remains a failure.

The new [`settings_lifecycle` oracle](../../../scripts/linux_real_app_v3_n07_settings_lifecycle.py)
is a separate, explicit revision for the **fresh, managed B4 fixture**. It
requires an initially absent keyfile and admits only one target key, four
Mousepad window-state keys and twelve GTK chooser-state keys. It is not a
general policy for importing an existing user profile or ignoring an entire
settings subtree.

The following remain mandatory:

- One trusted C1 context / StorageRecord / persistent root, retained by the
  independent supervisor across two real sessions.
- Initial target-key absence; exact lowercase `true` after the normal Registry
  Pointer toggle and at every subsequent observation.
- Independent nonce / capture / Runtime / application owner and per-process
  fingerprint observers; complete first product retirement before the second
  Runtime is created.
- The three canonical observations at their original lifecycle points. Extra
  observations are supplemental and never replace a missing canonical proof.
- Ordinary Registry Pointer, admitted Open / keyboard, Close and Stop paths;
  no direct settings edits, preseed, GTK API or D-Bus control, injected module,
  unverified Line Numbers shortcut, lock force-release or rescue substitution.
- Existing no-follow / ownership / type / single-link / bounded-input /
  duplicate-key checks, retained root and intermediate directory identities,
  C1 control-file identity checks, and independent cleanup validation.
- N01–N06 oracles, product deadlines, reason classification and v2/v3 separation.

The fixed inventory remains **28 families / 87 variants / 533 assertion
references**, with manifest SHA-256
`e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33`.

## 2. Why these automatic writes are part of the contract

The writer set comes from the pinned application and toolkit source, not from
an arbitrary list of differences observed in a failed run.

| Writer | Source-derived lifecycle | Allowed section / keys |
| --- | --- | --- |
| Mousepad `mousepad_window_save_geometry()` | Configure schedules a one-second save; normal Close also saves geometry/state | `org/xfce/mousepad/state/window`: `width`, `height`, `maximized`, `fullscreen` |
| GTK chooser widget `settings_save()` | Widget unmap applies the chooser's UI settings | Ten chooser keys listed in §3 |
| GTK chooser dialog `save_dialog_geometry()` | Drawable size allocation and unmap save position/size | `window-position`, `window-size` |

Mousepad is pinned to
[`65fbfe0459259f820fcc8b9c4dd454228304842a`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c).
Its [checked-in schema](../../../scripts/tests/fixtures/mousepad-0.6.3.gschema.xml)
defaults remember-size / remember-state to true and remember-position to
false. Consequently, `top` / `left` are not permitted by this fixture contract.
Individual setting writes need not form one four-key transaction, so a
partial window-key set may be observed while the first GUI remains live.
All four keys are required after its normal Close and complete retirement.

The GTK 3.24.41 source is pinned to
`77ebdd85091833a7869ece48c3114fa6d9966321`:
[widget persistence and location entry](https://github.com/GNOME/gtk/blob/77ebdd85091833a7869ece48c3114fa6d9966321/gtk/gtkfilechooserwidget.c),
[dialog geometry persistence](https://github.com/GNOME/gtk/blob/77ebdd85091833a7869ece48c3114fa6d9966321/gtk/gtkfilechooserdialog.c),
and [chooser schema](https://github.com/GNOME/gtk/blob/77ebdd85091833a7869ece48c3114fa6d9966321/gtk/org.gtk.Settings.FileChooser.gschema.xml).
The actual admitted location-entry action explains the expected
`filename-entry` mode. No chooser-state key may appear before the second
session's Open begins, and all twelve are required after that dialog closes.

These are expectations of this controlled fixture. A different toolkit
writer/schema/default is an explicit incompatibility, not a reason to learn
new permitted keys or literals from the current run.

## 3. Exact schemas and value boundaries

The only sections and keys accepted for byte export or v2 validation are:

| Section | Key(s) | Exact lexical form / predicate |
| --- | --- | --- |
| `org/xfce/mousepad/preferences/view` | `show-line-numbers` | `true` after the actual toggle; prelaunch file/key absent |
| `org/xfce/mousepad/state/window` | `width`, `height` | Positive canonical unsigned-integer text within the source schema's range; bounded by that session's actual main logical geometry |
| Same | `maximized`, `fullscreen` | Exact `false`, a non-maximizing fixture expectation |
| `org/gtk/settings/file-chooser` | `date-format` | Exact `'regular'` |
| Same | `location-mode` | Exact `'filename-entry'` after admitted location entry |
| Same | `show-hidden` | Exact `false` |
| Same | `show-size-column`, `show-type-column` | Exact `true` |
| Same | `sort-column` | Exact `'name'` |
| Same | `sort-directories-first` | Exact `false` |
| Same | `sort-order` | Exact `'ascending'` |
| Same | `type-format` | Exact `'category'` |
| Same | `sidebar-width` | Positive canonical signed-integer text, no larger than the saved chooser width |
| Same | `window-position` | Canonical signed 32-bit pair `(x, y)`; preserved after first appearance |
| Same | `window-size` | Canonical positive signed 32-bit pair `(width, height)`; bounded by actual chooser logical geometry |

The file is at most **4096 bytes**, ASCII, with an exact restricted section /
`key=value` grammar. Duplicate sections/keys, comments, empty sections,
escapes, continuation lines, unknown names, noncanonical numbers, extra
whitespace, trailing extra blank lines and out-of-range values are rejected.
An unknown user preference cannot be hidden in a permitted subtree.

### Geometry is bounded evidence, not GTK allocation introspection

GTK's
[`gtk_window_get_size()` / `gtk_window_update_csd_size()`](https://github.com/GNOME/gtk/blob/77ebdd85091833a7869ece48c3114fa6d9966321/gtk/gtkwindow.c)
exclude client-side decorations, including titlebar allocation. The Renderer
receipt contains real logical geometry, but does not independently expose
that GTK allocation. The oracle therefore validates positive, bounded sizes
and exact persistence after first appearance. It does **not** assert exact
equality to the Renderer height, subtract a historical titlebar offset, or
pin a size observed in a previous run. SHM buffer dimensions include further
decoration bounds and are not a replacement for logical geometry.

Similarly, the schema's sidebar default is not the actual allocated paned
position. The latter is validated as a bounded, persisted value. Maximized /
fullscreen `false` and chooser literals are controlled-fixture policy
expectations, not independently observed GTK internal state. These limits
are explicit in the settings verdict and are not promoted into pixel or
menu-identity assertions.

## 4. Seven actual observations and seven barriers

The existing socketpair handshake remains bounded and binds the exact
request / observation digests, session identity, transport watermark and
monotonic timestamps. A new `second_before_open` barrier is inserted after
the actual main-only startup readback, before even the Open-dialog baseline
and first Ctrl+O command. A refusal prevents Open and proceeds to normal
failure retirement.

| Phase | Snapshot role | Required state and allowed change |
| --- | --- | --- |
| `first_before_launch` | Canonical `before_first_launch` | Keyfile and target key absent. Trusted root and fixed document established before Mousepad Start |
| `first_after_toggle` | Canonical `after_first_toggle` | Target exact `true`; zero or a subset of the four automatic window keys; no chooser keys |
| `first_retired` | Supplemental | After normal Close and complete product retirement: all four window keys present; previously observed values unchanged; no chooser keys |
| `second_before_launch` | Supplemental | Same bytes, size, owner and parsed values as `first_retired`; second Start has not occurred |
| `second_before_open` | Supplemental | Same bytes and parsed values after second Ready but before Open; chooser still absent |
| `second_after_observation` | Canonical `second_session` | Target/window values unchanged; exactly twelve chooser keys added after the actual admitted Open / dialog close and real six-state acquisition |
| `second_retired` | Supplemental | After normal Stop and complete product retirement: identical bytes, size, owner and all parsed values to the second canonical snapshot |

The supplemental first-retired observation is taken inside the existing
retirement gate, not after the second Runtime has begun. The second Runtime
must still be created only after the supervisor's successful first-retired
ACK. The second prelaunch observation independently covers the intervening
Runtime/C2/Inspect interval. No additional Runtime, observer reuse or second
product storage lock is introduced.

The same canonical storage digest, root device/inode and expected UID must
hold at every observation. Once a window/chooser key appears, its exact
lexical value cannot change or disappear. By the first retirement all four
window keys must exist; by the post-Open observation all twelve chooser keys
must exist. New keys are permitted only in their defined write interval.

Existing serialized entries must retain their order and spelling. A changed
file must be fully explained by newly permitted entries. If no permitted
entry was added, the raw bytes must be identical. In intervals without a
permitted write, even a semantically equivalent reorder or whitespace edit
is rejected. The file's SHA and size are recomputed at every point.

GLib may atomically replace the regular keyfile during flush, changing its
inode / timestamps. Those metadata changes do not themselves alter the
settings, but never excuse a byte/value/owner change, a hardlink or symlink,
or replacement of the root, any pinned intermediate directory, `owner.json`
or `profile.lock`.

## 5. Same-read byte evidence and independent revalidation

The legacy three canonical proofs remain available. v2 additionally records
`eidolon-i6-n07-keyfile-snapshot-v2` observations from the **same bounded
no-follow FD read** that produced each proof. This is not a later reread.

Only after the complete restricted grammar / exact key / value policy has
succeeded may the bounded bytes be encoded into the exported observation.
An unknown key or value is rejected before its raw bytes can be published.
The byte envelope contains the source marker, revision, canonical proof SHA
and Base64 bytes, or null for an absent file. Partial failure receipts may
retain bounded proof metadata and the fixed rejection reason; they must not
export an arbitrary settings file as a diagnostic repair.

The saved verifier independently checks canonical Base64, length, SHA and
proof binding, reparses the bytes, and recomputes target value and the full
legacy other-settings digest. It then validates additions, immutable values,
serialization, geometry bounds and lifecycle sequence. It does not trust a
producer's `status=pass` or a supplied inventory/hash in place of bytes.

The independent B4 product validator supplies the separate causal evidence:
real Pointer delivery / popup / parent-buffer settling; normal Open shortcut,
location entry and dialog closure; source-bound actual Renderer readbacks;
first-retired admission; and final product census / locks / journal. In
particular it checks:

```text
second before_open readback
  < second_before_open barrier acknowledgement
  < Open baseline
  < actual Open keyboard delivery
```

The byte predicate alone does not prove a UI action, exact menu identity or
valid Golden comparison. Failure at any phase retains the partial receipt,
observed images and cleanup outcome, and prevents a successful collection.

## 6. Revision and compatibility boundary

| Artifact / contract | New explicit revision |
| --- | --- |
| Settings predicate | `eidolon-i6-n07-settings-lifecycle-v2` |
| Same-read keyfile bytes | `eidolon-i6-n07-keyfile-snapshot-v2` |
| Host acquisition summary | `eidolon-i6-n07-b4-host-v2` |
| Second-session native receipt | `eidolon-i6-n07-b4-second-native-v2` |
| Supervisor | `eidolon-i6-n07-b4-supervisor-v2` |
| Validated collection | `eidolon-i6-n07-b4-collection-v2` |
| Six-state review candidate | `eidolon-i6-n07-review-candidate-v3` |

v1 Host/supervisor/collection artifacts and their six-state candidate v2
remain readable under their original strict predicate. B3's original
four-state candidate is also unchanged. A revision mismatch, missing new
snapshot/barrier, or attempted relabel of old evidence is rejected. There is
no fallback from a v1 failure to v2 and no reconstruction of missing bytes
from old value hashes, images or conversation records.

The v3 candidate records the settings revision and source-bound settings
evidence alongside its six-state session mapping. Each image retains its
actual capture / Runtime / application owner / TargetKey generation / SDK
Create / Renderer owner / context epoch. Reference acquisition and later
Acceptance execution remain separate; no observation may become its own
expected reference.

## 7. Verification and remaining acceptance boundary

Required checks include the unchanged v1 tests and rejection behavior; valid
fresh v2 lifecycle; foreign/replaced storage roots and control files; missing,
replayed or reordered barriers; premature window/chooser writes; unknown keys
or wrong literals/types; partial/missing final key sets; later value changes;
bytes-only changes; wrong Base64/SHA/size/UID; geometry overflow; late Stop
mutation; mixed artifact revisions; image relabel / foreign Renderer leases;
and incomplete product retirement with partial evidence preserved.

Host fixture Rust tests, rustfmt, real GUI CI and independent saved-packet
mutation tests are required after implementation. Their actual source / run /
artifact identities and failures belong in the execution record once run;
they are not implied by this contract or its authorization.

The formal boundary remains:

```text
APPROVED_SHA256=None
reviewed_golden=false
menu_identity_verified=false
verified_n07_assertions=[]
N07 Acceptance: 0/13 verified
host_v3_acceptance_passed=false
```

Effective-theme read-only observation, independent reference review and
Golden pin, B5 typed N07 receipt / Acceptance integration, and Quest / I7 / H
validation remain separate work. No production six-state requirement or
fail-closed pixel behavior is relaxed by this settings revision.

## 8. First v2 execution: preserved partial failure

The first new acquisition on 2026-11-19 JST failed at the final
`second_retired` barrier with `N07_FINGERPRINT_SESSION_SCHEMA`:

- PR HEAD: `a6c36581de72a0007ccf4f4857c90e613b15cf2d`.
- Actual CI checkout/source: `92a81ad2783cef23a534bda9676b4ed101dc902f`.
  Both commits have tree `bc1128dd713b41e91af9401eeb1df8fe7746e291`;
  the CI checkout is a PR merge checkout, not a merge into main.
- [L7a run 44414933333, attempt 1, B4 job 135192697859](https://github.com/tin-machine/eidolon-wayland/actions/runs/44414933333/job/135192697859).
- Artifact `14477904906`,
  `linux-desktop-v3-n07-b4-92a81ad2783cef23a534bda9676b4ed101dc902f`.
- Original ZIP: 187,019 bytes; SHA-256
  `557f139c2e14445b12644ab6fbfc25949f4edf77baee9adfba08979074ace1e1`.

The fingerprint reader dispatched only `second-native-v1`. It rejected the
explicit new `second-native-v2` after independently verified second product
retirement, before the seventh settings snapshot. The correction adds that
specific schema to the same per-process reader and tests both revisions,
foreign bindings, unknown revisions and wrong variants. It changes no
settings, identity, cleanup, pixel or fingerprint predicate.

Independent rereading of the saved packet verified both native sessions and
their product retirements, all six actual raw Renderer states and contexts,
and the first six settings observations with actual geometry bounds. Full
seven-point validation correctly rejected the missing final snapshot with
`N07_SETTINGS_PHASE_MISSING`; old strict v1 still rejected the changed other
settings. The first fingerprint contained 12/13 items, with theme missing.
The second process's partial observations had `observation_errors=[]`, but
its typed fingerprint receipt was not produced. No PNG candidate was made.

Both native errors / cleanup errors were null; final process and journal
censuses were zero, locks were released, and external rescue was false.
The existing first Close reason classification was retained. Missing final
observations are not reconstructed from these partial results: a new
source-bound acquisition is required after the dispatch fix. The original
failure ZIP and independent audit remain separate from that later run.
