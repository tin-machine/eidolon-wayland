# I6 N07-B4 — one persistent storage, two actual GUI sessions

> **Diagnostic acquisition and independent saved-evidence validation.**
> N07 remains **0/13 verified**, `APPROVED_SHA256=None`,
> `reviewed_golden=false`, `menu_identity_verified=false`,
> `verified_n07_assertions=[]` and `host_v3_acceptance_passed=false`.
> A six-state candidate, successful file predicate or green CI does not approve
> the pixel reference or complete the formal N07 acceptance case.

## 1. Baseline and responsibility

Work starts from main `ee9192d12957a17aaa3d61bd6e8ba1ca86a99d6f`, the squash
merge of [PR #51](https://github.com/tin-machine/eidolon-wayland/pull/51).
The merged B3 PR HEAD was `794a3fadaa43560558825bfb3314a43748a8f32f`.
The existing B2/B3 implementation is reused through its normal product path;
it is not copied or cherry-picked into a second implementation.

The [N07-A keyfile and pixel contracts](i6-n07-a-independent-oracles.md),
[B1/B2 pointer/popup/readback](i6-n07-b1-pointer-popup-readback.md),
[B3 review preparation](i6-n07-b3-pixel-review.md),
[I6 detailed design](../../linux-real-app-v3-i6-detailed-design.md),
[C1 persistent-profile ownership](../../linux-real-app-c1-handoff-20261110.md)
and [C2 remote-helper retirement](../../linux-real-app-c2-helper-20261110.md)
remain applicable. I6 is still manually discoverable; this document does not
register a new resolver task or declare its design accepted.

The frozen inventory remains **28 families / 87 variants / 533 assertions**,
with manifest SHA-256
`e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33`.
N01–N06 independent oracles, v2/v3 separation, C1/C2 locks, product cleanup
deadlines, and the separate Quest/Meta SDK/Android/H evidence are unchanged.
B5 owns the formal typed N07 receipt and its connection to
`linux_real_app_v3_evidence.py`.

## 2. Two ownership lifetimes inside one acquisition

Calling B2's `prepare_and_run()` twice would create two independent
installation/storage identities and reset the Host process. B4 instead
prepares one disposable fixture and one canonical StorageRecord, then runs
two product sessions through a retained Host Registry. The first Runtime is
destroyed before that same Registry creates the second Runtime. The resulting
Runtime handles are real Registry allocations, not labels attached to reused
handles.

| Retained for both sessions | Recreated for the second session |
| --- | --- |
| Installation and `installation_id` | Session nonce and transport capture |
| `profile_storage_id`, profile binding and canonical StorageRecord digest | Runtime and application owner |
| Verified app account/passwd HOME and C1 persistent root | C2 helper, private bus, app process and process start identity |
| Supervisor's root and first-observed intermediate-directory FDs | SDK Create leases, renderer lifetime and per-session fingerprint observer |
| Fixed eight-line fixture document | Normal Inspect, Selection, Registry Launch and input lifecycle |

The supervisor never acquires a second product C1 handle while a live helper
owns it. Its retained descriptors are read-only observations; they neither
replace the product locks nor force their release. Product C2 continues to
own all children, pipe drainage, C1 release and remote receipt publication.

Read-only FDs for `owner.json` and `profile.lock` also survive both sessions.
Their exact device/inode/UID/mode/link/size/mtime/ctime stamps must remain
unchanged, matching C1's own control-file rules; the lock file remains empty.
The supervisor records initial and final observed stamps without taking a
product flock. Replacing either file with equal bytes is rejected. The
keyfile's explicitly allowed atomic replacement does not extend to these
control files.

### Trusted persistent root

The root is derived from the supervisor's own installation/storage inputs,
the verified app UID and its passwd HOME using the fixed C1 layout:

`<passwd-home>/.local/share/eidolon/apps/<installation_id>/<profile_storage_id>/mousepad-editor-v1/`

Normal C2 preparation and Inspect create/acquire this profile before Mousepad
Start. At the first prelaunch observation barrier the supervisor validates
the canonical storage/context binding, C1 `owner.json`, expected UID,
no-follow directory chain and fixed C1 directory identities. It opens and
retains that exact root across both sessions. A path supplied by the GUI
producer cannot select an alternative trusted profile. Missing, unsafe or
replaced components are failures rather than permission to adopt another
root.

The supervisor prepares only the managed, fixed eight-line document below
the profile's `Documents/` at this first barrier, before either GUI launch.
Its bytes, size, SHA-256, owner and creation observation are evidence. Later
observations check its stability. The supervisor does not rewrite documents
or settings during either GUI session.

## 3. Lifecycle and observation barriers

Every barrier is a bounded handshake with an independently running
supervisor. The Host records the actual request/acknowledgement and session
identity; the supervisor records when it performed the observation. A late
snapshot cannot be relabelled as a prelaunch observation.

The transport is a private inherited socketpair FD. The six ordered phases
are `first_before_launch`, `first_after_toggle`, `first_retired`,
`second_before_launch`, `second_after_observation`, and `second_retired`.
The response binds the exact request digest and independent observation
digest. Both processes record actual `CLOCK_MONOTONIC` nanoseconds; the saved
validator checks request/observation/acknowledgement order and matching
session identities. An unexpected phase, replayed request or mismatched
digest fails the handshake.

| Order | Normal product action | Independent observation/gate |
| --- | --- | --- |
| 1 | First Runtime and C2 preparation, normal Inspect/catalog | Establish trusted C1 root; create fixed document; first canonical keyfile snapshot must be key **absent** |
| 2 | Normal Selection/Registry Launch, Ready and SDK Create | Start first process-specific fingerprint observer before app launch |
| 3 | Real View pointer/popup, OFF gutter/checkmark, real Line Numbers candidate click, ON gutter/checkmark | Capture four real Renderer states and B3 context/parent-buffer settling proof |
| 4 | Hold first GUI alive before Close | Canonical `enabled` keyfile snapshot must contain exact lowercase `true`; retain first fingerprint evidence |
| 5 | Normal Registry `close_window_target`, product Stop/retirement/Registry destroy | Verify remote/native/SDK/renderer/runtime/registry cleanup and independent process/filesystem census |
| 6 | No second Runtime or launch before cleanup gate | Require no live product processes, journal work or held product locks; finalize first observer |
| 7 | Second Runtime from the same Registry; normal Inspect and new Selection/Launch | Recheck same root/storage and start a fresh fingerprint observer for the second app process |
| 8 | Without clicking Line Numbers, open the fixed document through admitted keyboard/Open dialog | Capture actual `second_on_numbers_1_8` and actual View-menu `second_on_checkmark` |
| 9 | Hold second GUI alive before Stop | Canonical `restarted` keyfile snapshot must retain exact `true`; compare three canonical observations |
| 10 | Normal Stop, full product retirement and Registry destroy | Final independent census, second fingerprint finalization and unchanged-document observation |

Supplemental snapshots (for example after Close or before second Launch)
remain separate from the three canonical keyfile observations. They cannot
replace an unavailable canonical observation or conceal a changed file.

The first completed cleanup is a required admission gate for the second
session. A failed gate saves partial first-session evidence and diagnostics;
it does not proceed with a forced lock release, process kill or external
container rescue. The original failure remains visible even when subsequent
cleanup succeeds. Emergency fixture/container rescue is recorded separately
and cannot count as normal product cleanup.

At `first_retired`, the supervisor independently reopens the first native
receipt/context and requires complete product retirement before admitting
the next Driver creation. The process census records raw counts and the
explicit exclusion of the still-running, pidfd-owned Host controller's
PID/start identity/UID. It does not subtract arbitrary children or observers.
Each fingerprint worker is stopped and reaped before its session census.
After the Host controller itself exits, the final census requires actual
zero driver children without that intermediate exclusion.

### Natural Close is not a new reason classification

First-session Close uses the existing Registry `close_window_target` command
with the current main TargetKey and live `controls_revision`. Product code
resets input and sends the ordinary Wayland close request; the fixture does
not claim an additional Escape/menu-dismissal action. Its
observed terminal reason retains current product semantics. This increment
does not resolve [Issue #45](https://github.com/tin-machine/eidolon-wayland/issues/45)
or relax the N06 oracle. N06's intermittent dialog-detection failure
([Issue #50](https://github.com/tin-machine/eidolon-wayland/issues/50)) is
separate from the B3 HEAD's observed `I6_TRACE_OVERFLOW` during Close Confirm
cleanup. Failed artifacts and exact signatures must be retained when either
recurs; a successful same-SHA retry is not a root-cause fix.

## 4. Independent keyfile contract

[`StorageKeyfileProbe`](../../../scripts/linux_real_app_v3_n07_settings.py)
continues to read only the fixed C1-relative path
`config/glib-2.0/settings/keyfile`, with no GTK API, D-Bus request, injected
module or settings writer. `validate_file_sequence()` still requires:

- Initial **key absence**; existing `false` and `true` are both refused.
- Exact boolean syntax `true` after the actual pointer toggle and in the
  second session.
- The same canonical storage digest and persistent root device/inode at all
  three observations.
- The same digest of all other keys and sections at all three observations.
- The same keyfile bytes SHA, size and owner after enabling and after restart.

An atomic GLib Close flush may replace the keyfile inode and ctime. That is
allowed only when the bytes/size/owner comparison succeeds; it does not allow
replacement of the persistent root or an intermediate directory.

B4 strengthens the observer's **lifetime** boundary. The original probe
already rejected a directory swapped during a single read. It now retains
each first-observed intermediate directory FD until the probe closes, and
rejects replacement or disappearance between observations. Directories that
are initially absent may first appear through normal GLib persistence; once
observed, their identity is pinned. Retained FDs prevent inode reuse from
disguising a replacement. No file bytes are exported.

Saved proofs also receive strict type/bounds checks: booleans cannot stand
in for inode/UID/size values, decimal strings cannot stand in for integers,
hashes must be lowercase SHA-256, and file size remains bounded to 64 KiB.
The existing no-follow, owner, mode, type, single-link, duplicate-key and
concurrent-change guards are preserved.

These are file observations. Even a successful three-point file predicate
cannot establish that a particular menu item caused the change; independent
input, GUI semantics, readback and eventual approved Golden evidence remain
necessary for formal N07 assertions.

There is a concrete source-level risk in applying the strict other-keys rule
to a fresh profile. The pinned Mousepad
[`mousepad_window_save_geometry()`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c)
writes window size/state after configure and on normal Close; the checked-in
[schema](../../../scripts/tests/fixtures/mousepad-0.6.3.gschema.xml) defaults
`remember-size` and `remember-state` to true. Its
[`mousepad_setting_set()`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-settings.c)
delegates to GSettings, whose
[keyfile backend](https://github.com/GNOME/glib/blob/2.80.5/gio/gkeyfilesettingsbackend.c)
persists supplied values. The actual three snapshots determine whether that
changes this run's other-keys digest. If it does, the existing predicate must
fail and the diagnostic evidence must retain the mismatch. Pre-seeding
settings, changing schema defaults, omitting geometry keys or moving the
canonical initial snapshot after launch is not an allowed repair.

## 5. Renderer, input and session identity

The first four observations reuse the B2/B3 normal Registry pointer,
compositor delivery, `xdg_popup` parent/configure/ACK/grab and layer checks.
Registry Accepted status alone does not prove input delivery. The View-Press
grab race barrier is preserved.

The B3 parent-buffer settling condition remains mandatory: popup disappearance
and a newer composed frame alone are insufficient. The same main surface
must publish a newer `content_seq`, have no popup layer and remain stable for
200 ms in actual readbacks, with the existing 12-second/32-sample bounds.
The saved proof is revalidated independently.

Second-session document loading uses the existing admitted Open/keyboard
route. The new image states are captured from that second Runtime's actual
readback tickets. Every image remains joined to its session, capture,
application owner, target generation, SDK Create lease, Renderer owner and
context epoch. A renamed first-session image cannot supply a second state.

`content_frame` in raw Renderer snapshots remains a positive decimal string.
Any schema conversion is explicit and type-checked. Frame/request/present
ordering is validated within each actual Runtime/Renderer lifetime; there is
no expectation that second-session frame numbers exceed first-session
numbers. Historical surface IDs, frame numbers and changed-pixel counts are
never fixed expectations for another execution.

Backend immutable identity is compared separately from live measurements.
In particular, `max_swap_us` remains a dynamic nonnegative timing observation,
not part of a fixed Renderer identity.

## 6. Fingerprint and candidate compatibility

Each Mousepad PID/start identity gets its own
[`LiveFingerprintObserver`](../../../scripts/linux_real_app_v3_n07_fingerprint.py).
The second process is never accepted as a continuation of the first. Both
observers collect actual loaded libraries/fonts and environment, and both
must be stopped and reaped before the corresponding final census.

The collected fingerprint fields must match across sessions. B3's historical
GTK/Pango/Cairo versions, font inventory and Mesa observations are not copied
into a new run. Effective theme remains missing unless a safe read-only
observation actually establishes it; configured Adwaita is auxiliary policy
input only. The known `theme` gap is explicit and can leave a six-state
candidate diagnostic-only. Unexpected missing fingerprint fields or
observation errors fail the collection after preserving diagnostic evidence.

B3's four-state candidate revision remains a single-session contract. B4
uses `eidolon-i6-n07-review-candidate-v2` with session mapping and provenance
for all six states. It does not reinterpret a B3 packet as a six-state packet
or alter the production Golden schema.

| State | Required acquisition session |
| --- | --- |
| `first_off_gutter` | First, before real setting toggle |
| `first_off_checkmark` | First, initial real View popup |
| `first_on_gutter` | First, after pointer toggle and parent settling |
| `first_on_checkmark` | First, fresh reopened View popup |
| `second_on_numbers_1_8` | Second, fixed document opened without toggling |
| `second_on_checkmark` | Second, real View popup without setting click |

The second numbers ROI has ID `3`, at `(0,64)` with size `64×192`; the
second menu ROI has ID `2`, at `(112,96)` with size `112×128`. These are
unapproved acquisition coordinates, not a reviewed claim that the images
contain the correct glyphs. Independent review must inspect the actual
eight-line view and menu row.

| Saved input | Boundary |
| --- | --- |
| `first-native.json` | Existing B2 native receipt shape, with ordinary B3 context proof |
| `second-native.json` | `eidolon-i6-n07-b4-second-native-v1`, variant `same_storage_restart_collection`, explicit `n07_second` evidence |
| `first-context.json`, `second-context.json` | Exact separately SHA-bound Renderer context bytes |
| `first-fingerprint.json`, `second-fingerprint.json` | Separate process/start identities and actual environment observations |
| `first-environment.json`, `second-environment.json` | Per-session source/account/cleanup environment |
| `n07-b4-native.json` | Host's two-session lifecycle and barrier summary |
| `n07-b4-supervisor.json` | Independent root/storage, canonical and supplemental keyfile snapshots, document, barriers, fingerprint and failure evidence |
| `n07-b4-validation.json`, `container.json` | Live diagnostic outcome and separate container/source/rescue disposition |
| `n07-b4-review/` | Explicit v2 candidate, exact raw images, review aids and bounded source evidence |

Each native receipt retains the 1 MiB bound. Each session's context sidecar
retains a 4 MiB bound. The v2 packet's explicit bound is 8 MiB to accommodate
six context phases; B3 v1 keeps its existing 4 MiB contract. New limits apply
only to the new envelope and do not enlarge the Renderer readback request or
the original native receipt.

Raw RGBA and lossless PNG are actual captured bytes. Context, zoom and diff
images remain separate review aids. Review packets include source commit,
PR HEAD, workflow run/attempt, artifact identity when available, and exact
input digests. Source checkout, PR HEAD and a later main merge are distinct.
The downloaded artifact's digest belongs in an external provenance record;
it cannot be inserted into itself without a circular definition.

The packet digest provides integrity against an independently retained
expected digest. It does not authenticate fabricated, internally consistent
evidence or confer review authority. A reference-acquisition run must remain
different from a later Acceptance run. Unapproved candidates and self-reference
remain rejected by the production Golden loader; the six-state requirement
and `APPROVED_SHA256=None` are unchanged.

## 7. Failure retention and validation

The exporter must reopen the saved native, environment, keyfile, fingerprint
and image evidence and derive its own verdict. A producer's `status=pass`,
exit code zero or successful first session is insufficient.

Required negative checks cover changed storage/root, intermediate-directory
replacement, reused nonce/capture/Runtime/application owner, premature second
launch, wrong initial key, unrelated settings changes, changed restart bytes,
missing/relabelled second states, foreign Create/Renderer ownership, stale
main buffer/frame, invalid types, mismatched fingerprints, unapproved Golden,
self-reference, failed partial sessions and external rescue. Saved-evidence
mutations must fail independently of the live producer.

Only controlled fixture pixels and bounded allowlisted metadata are exported.
SSH keys, an entire HOME, arbitrary logs and user documents are excluded.
CI retains failure artifacts and source/run/attempt distinctions. Production
oracles, expected values and deadlines are not loosened to make an acquisition
succeed.

### Verification record

Implementation and actual execution evidence are separate. Record the final
PR/source revision, local checks, real GUI run/job/artifact, both session
identities, three keyfile observations, six acquired states, fingerprint
gaps and complete cleanup results here or in the PR's dated validation record.
Until that evidence exists, this design does not claim a completed second
session, GUI capture, successful CI or independent human approval.

## 8. Remaining B5 and independent-review work

1. Independently review the six-state reference's GUI semantics, menu identity,
   numeric glyphs, masks, tolerance and complete effective environment.
2. Supply an actual reviewed reference and source pin in a separate approval
   change. Neither B4 code review nor its candidate hash performs this step.
3. In B5, execute a distinct Acceptance run and join the two product sessions,
   keyfile observations, approved pixels and cleanup into the typed N07 receipt.
4. Connect that receipt to the unchanged formal evidence validator only when
   all thirteen frozen N07 assertions have their required independent evidence.

I6 completion and I7/Quest/H acceptance remain separate gates after N07.
