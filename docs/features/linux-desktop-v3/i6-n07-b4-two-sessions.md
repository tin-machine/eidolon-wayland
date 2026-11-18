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
root. The keyfile probe's separately opened root FD is compared with the
retained C1 root immediately after open and before and after every snapshot.
Each returned proof's root device/inode and storage digest must match that
same authority before the supervisor can acknowledge the barrier.

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

B4 alone enables an optional `last_diagnostic` inventory from the same
keyfile read and parse as each canonical snapshot. It records sorted
section/key names and SHA-256 of each parsed UTF-8 value, together with the
canonical proof SHA and observed file SHA/size. It exports no values or
keyfile bytes and performs no second read. The inventory is separate from
the three canonical proofs and cannot change `validate_file_sequence()`.
It is bounded to 64 sections, 256 keys, 256 safe ASCII characters per name
and 8192 total name bytes; an inventory outside those limits is explicitly
unavailable while the original file predicate remains unchanged. A failed
snapshot clears the diagnostic instead of exposing a stale observation.

Saved proofs also receive strict type/bounds checks: booleans cannot stand
in for inode/UID/size values, decimal strings cannot stand in for integers,
hashes must be lowercase SHA-256, and file size remains bounded to 64 KiB.
The existing no-follow, owner, mode, type, single-link, duplicate-key and
concurrent-change guards are preserved.

Directory link counts follow the product C1 rule: a directory must remain
linked (`st_nlink > 0`). The earlier `>= 2` check in the observer assumed a
filesystem-specific directory-link convention; a valid overlay/union
directory may report one link. This correction does not change the exact
`nlink == 1` requirement for regular keyfiles, `owner.json` or `profile.lock`,
or any UID, mode, no-follow, retained-identity or setting-value predicate.
If an ancestor check fails, the partial receipt retains only its fixed-chain
component ordinal/name and bounded actual stat fields under the rejected
`eidolon-i6-n07-b4-storage-rejected-v1` revision. This partial metadata cannot
satisfy the successful trusted-storage schema.

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
| `n07-b4-rejected-review/` | Separate rejected-collection diagnostics, exact six-state images and independently rechecked source evidence; never a candidate |

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

Started-session product retirement has a separate saved predicate. It checks
the original source, transport, SDK targets, Stop/Close finalizer, native
retirement and independent live-control census without rewriting the native
exercise error. This predicate cannot admit the second Runtime: the first
session still needs full session validation and a positive supervisor ACK.
After a rejected second observation, the Host sends `second_retired` only
when product cleanup completed; the original acquisition failure takes
precedence over any later barrier refusal. The supervisor stops its observer
before census and records cleanup independently of fingerprint finalization.
An observation error and a later observer-stop error are retained separately.

When all six images were acquired but the collection is rejected, the
exporter may create a distinct
`eidolon-i6-n07-b4-rejected-renderer-diagnostics-v1` packet. It rechecks the
original source/owner/capture, first-session cleanup gate, shared storage,
document, readback identities, settling, fingerprints and image bytes. The
saved validator reconstructs this diagnostic from its exact input receipts,
and semantic mutations are rejected after recomputing envelope hashes. Its
`diagnostic.json` and `review.html` show the actual failure and cleanup result;
there is no `candidate.json`. `candidate_created=false`,
`full_collection_valid=false`, the failing CI exit, unapproved Golden and
empty formal assertions remain unchanged. This preserves reviewable pixels
without weakening candidate-v2 or production acceptance.

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
The dated records below distinguish actual session/capture observations from
a successful full collection and independent human approval.

#### Initial CI acquisition — 2026-11-18

- PR HEAD: `6342ac1c2253d632633239af5fd7eae487c2693f`;
  CI checkout/source: `2035f8803e9cc34b5d4d4824bc6588a9d06f3451`.
- L7a run [44302994015](https://github.com/tin-machine/eidolon-wayland/actions/runs/44302994015),
  attempt 1; B4 job [134797319710](https://github.com/tin-machine/eidolon-wayland/actions/runs/44302994015/job/134797319710).
- Failed artifact `14425489750`, named
  `linux-desktop-v3-n07-b4-2035f8803e9cc34b5d4d4824bc6588a9d06f3451`;
  downloaded ZIP SHA-256
  `d299c5a4eb34e0bf26b5b7da0029a769194483170f78acf1f7a1b61af1e7a2c1`
  (10,739 bytes).

The independent supervisor refused the **first prelaunch** barrier with
`N07_B4_UNSAFE_ANCESTOR`. No Mousepad Start, keyfile snapshot, fingerprint,
candidate image or second session occurred. A later unconditional document
check reported the secondary `N07_B4_STORAGE_NOT_OPEN`; the fix retains the
first actual refusal and does not verify a never-created document.

Independent re-reading of the unmodified saved packet applied the existing
N01 trace and live-census predicates to the actual Inspect/Stop path. Remote
finalizer, origin retirement, Runtime shutdown and Registry destruction were
present; all final process/journal counts were zero and both locks were free.
`external_rescue=false`. This proves recovery of the **prelaunch abort**,
not cleanup of two launched Mousepad sessions. The Host fixture compiled and
its 32 Rust tests plus four targeted core tests passed in this job.

The initial packet did not retain the failing ancestor's stat fields. Review
found an invalid filesystem assumption: the new ancestor guard and existing
keyfile directory primitive required `st_nlink >= 2`, whereas product C1's
directory contract requires a positive link count. Directory link count 1 is
valid on filesystems including overlay layouts. The correction preserves all
type/UID/mode/no-follow/held-inode checks and the regular-file single-link
requirement; a rejected-only bounded stat diagnostic identifies future
ancestor failures. This is not yet evidence of a successful acquisition.

At the same HEAD, all five CodeQL language jobs succeeded. Verification run
[44302994306](https://github.com/tin-machine/eidolon-wayland/actions/runs/44302994306)
attempt 1 stopped Host and Android before compilation because APT installation
exceeded the existing 240-second deadline (exit 124). Only failed jobs were
requested again at the same SHA. On attempt 2, Host passed and Android again
stopped during APT installation at the 240-second deadline. No APT or product
deadline was changed; the retry is not another B4 acquisition.

#### Second CI acquisition — 2026-11-18

- PR HEAD: `76eef9edb87c59d69d65c4805651e3b36a568400`;
  CI checkout/source: `3f77e23497cf830657f18aab4fdd264b99c8ebd5`.
- L7a run [44305026733](https://github.com/tin-machine/eidolon-wayland/actions/runs/44305026733),
  attempt 1; B4 job [134804310406](https://github.com/tin-machine/eidolon-wayland/actions/runs/44305026733/job/134804310406).
- Failed artifact `14429100165`, named
  `linux-desktop-v3-n07-b4-3f77e23497cf830657f18aab4fdd264b99c8ebd5`;
  downloaded ZIP SHA-256
  `61df2d03b260391e6b670050f462458ba9a74e95b9ade836a280786f7e73543c`.
  The unmodified extracted packet is retained as `evidence/b4-settings-failed`.

This run passed the previously refused prelaunch storage check and actually
launched two sequential Mousepad processes. The saved first-session receipt
passes the strict session validator, including normal Close and complete
product retirement. The same live Host/Registry created the second Runtime
after the first retired barrier. The following are observations of this run,
not fixed expected identities for future acquisitions:

| Identity | First | Second |
|---|---|---|
| Acquisition run | `fff9afd2450acab79c1bc52978b38550` | same acquisition |
| Runtime | `1` | `2` |
| Nonce | `509d646453f6311e` | `2e19b7729a45d821` |
| Capture | `4d41760a42241377bc8ccfef44bf01744dbb49df42e40b91f6d9505abd30835e` | `6a7656fbbfab89ef9fe800985d907558a697e6fa9c23c5846c105314c57b1f63` |
| Application owner | `74b4468d0a8de3ba272b14db099ca44b` | `cb3de916609e9b7f90c5e175e42773ca` |
| Mousepad PID / start ticks | `87` / `41846` | `132` / `42247` |

All saved keyfile observations bind to canonical storage digest
`82f7e80cb862c7e1b41a42d684fc7dbaba6a66d65f3281c97a2e235cf2bba1b1`
and root device/inode `50` / `9490153`, UID `12002`. The supervisor's retained
record uses installation `ce2a9453e88735c783cf211f2a306333`, installation ID
`9c627d32d24e02a3471a0e8eba20904c`, profile storage ID
`52561d80fede0c9d4f368220a4e5f967` and profile binding
`f527b0165bf69aa10b8f45ce6adfc9316dd761586845d1b9ef4f14d673f62e72`.

| Observation | Line Numbers key | Keyfile bytes | Other-keys digest |
|---|---|---:|---|
| Canonical before first Launch | absent; no file | — | `44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a` |
| Canonical after first toggle | `true` | 60 | `44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a` |
| Supplemental before second Launch | `true` | 149 | `c37f79d30f7ba05e1214303e7b0dd2e36307e3386b1525ea9b1f1b671fa545e0` |
| Canonical after second Open/observation | `true` | 458 | `ee758a2cf7b418c52af8260871bcc2000c4f699960f420a625b87fcbd9ac19e9` |

The file SHA-256 changes from
`3c5a0b15841cc7105db2b78c90c400d4019b2e0bd40c5abf77320d66fdf51100`
after the toggle, to
`7c5b223024c9d8c17be0789fb781d21e2d9f5e39aca4c124a841b952202e0309`
before the second Launch, to
`76e7bbf5917fc78e4c8be45091d8c8a230cfb4c7ad970e13247e893e6429bf9a`
after second-session Open. Exact lowercase `true` is preserved, but the
required other-keys and restart-bytes invariants are not. The strict
`second_after_observation` barrier therefore refused with
`N07_UNEXPECTED_SETTING_CHANGE`. This is not an accepted persistence result.
The old packet has no same-read section/key inventory, so it does not identify
the changed key names. The geometry-save source analysis remains a hypothesis
about those changes. The new opt-in bounded diagnostic will observe names and
value hashes in a later run; nothing is reconstructed into this artifact.

The native/context files contain actual Renderer captures for all six labels:
`first_off_gutter`, `first_off_checkmark`, `first_on_gutter`,
`first_on_checkmark`, `second_on_numbers_1_8` and `second_on_checkmark`.
The second captures follow the normal admitted Open path and do not reuse
first-session pixels. They are saved evidence of acquisition, not human
verification of the labels, numerals or checkmark semantics. Both per-process
fingerprints have `missing=["theme"]` and `observation_errors=[]`.
**No candidate bundle was created**, and the collection/export gates remain
failed. The second context contains both captured phases but has
`complete=false` because the supervisor refusal is retained.

The first session's strict cleanup succeeds. The second native receipt
records SDK retirement, Registry counts zero, destroy status zero and no
cleanup error, but the old packet's final external census still records
`app_process_count=1`. Therefore full second-session external cleanup is not
verified. Driver/sshd child counts and journal entries are zero, both locks
are free, and `external_rescue=false`; these do not erase the nonzero app
count. The correction stops the fingerprint observer before the final census
and sends a failure-retired barrier after product cleanup, preserving the
original refusal. A later execution must establish its result; the old census
is not relabelled as zero.

All other jobs in this L7a run passed. At the same HEAD, CodeQL run
[44305026724](https://github.com/tin-machine/eidolon-wayland/actions/runs/44305026724)
passed all language jobs. Verification run
[44305026749](https://github.com/tin-machine/eidolon-wayland/actions/runs/44305026749)
attempts 1 and 2 both stopped Host and Android during APT installation at the
unchanged 240-second timeout, exit 124; the failure stage and reason were
checked in the logs. Those dependency-installation failures are separate from
the actual B4 settings refusal and do not establish a Host or Android build
result for that verification workflow.

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
