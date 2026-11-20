# I6 N07-B3 — real pixel review packets and observed environment

> **Review preparation, not Golden approval or N07 acceptance.**
> `APPROVED_SHA256=None`, N07 **0/13 verified**, and
> `host_v3_acceptance_passed=false` remain unchanged. A generated packet,
> successful byte-integrity check, code review or green CI is not an independent
> review of the GUI semantics. No reviewer, review commit or approval is supplied.

## 1. Baseline and scope

Work started from main `79c9134354f80d18e80df116da2e68f43e1a0929`.
[PR #49](https://github.com/tin-machine/eidolon-wayland/pull/49) was already
merged at that commit; its final implementation HEAD was
`a819714625798130700d97e70a4275c49a709910`.
[PR #47](https://github.com/tin-machine/eidolon-wayland/pull/47) was also merged
(`c230aae6721db09eabfb756aaa1a989bdc4953a4`). B3 is based on main and does not
copy or cherry-pick B2. I6 remains manually discoverable rather than a new
registered documentation task.

The [N07-A contract](i6-n07-a-independent-oracles.md),
[B1/B2 implementation](i6-n07-b1-pointer-popup-readback.md),
[I6 design §10](linux-real-app-v3-i6-detailed-design.md) and
[integration plan §11](linux-real-app-v3-integration-plan-20261111.md)
remain authoritative. The manifest is unchanged: **28 families / 87 variants /
533 assertions**, SHA-256
`e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33`.
N01–N06 oracles, C1/C2 ownership and retirement, v2/v3 isolation and unverified
Quest/Meta SDK/H evidence retain their existing boundaries. N06's intermittent
dialog failure remains [Issue #50](https://github.com/tin-machine/eidolon-wayland/issues/50).

| Stage | Responsibility | Current boundary |
| --- | --- | --- |
| B1 | Actual Registry pointer, owned popup, Renderer ROI | Merged diagnostic |
| B2 | Single-session candidate toggle, four real ROI observations | Merged diagnostic; GUI semantics unapproved |
| B3 | Saved-receipt audit, raw images, context, fingerprint and review packet | This implementation; no approval or second session |
| Separate approval | Independently acquired **six-state** reference and actual review | Still required; production pin unset |
| B4 | Same StorageRecord, new nonce/capture/Runtime/app owner, two real sessions and independent keyfile observations | Still required; can implement collection before approval, cannot pass pixel assertions without it |
| B5 | Revalidate both sessions and reviewed pixels; connect typed N07 receipt and all 13 assertions | Still required |

The table records the B3 implementation boundary. The subsequent
[B4 design and collection increment](i6-n07-b4-two-sessions.md) adds the
same-storage lifecycle, independent observations and explicit six-state
candidate revision. It does not rewrite B3's single-session evidence or
approve either candidate. B5 and independent Golden approval remain separate.

## 2. Existing real evidence and an important ROI limitation

The original artifact was available and downloaded, not reconstructed:

| Identity | Value |
| --- | --- |
| Workflow run | [44107303617](https://github.com/tin-machine/eidolon-wayland/actions/runs/44107303617) |
| Successful B2 job | [134123467769](https://github.com/tin-machine/eidolon-wayland/actions/runs/44107303617/job/134123467769) |
| Artifact ID | `14338254865` |
| Artifact name | `linux-desktop-v3-n07-b2-52d8d9f7cdc4d303f3af5c390bc8c1dd41255dff` |
| ZIP SHA-256 | `c72202094507e91d199051ae14b434e3bd5d363dad0e1ebc64aeec8aa070951c` |
| Checkout/source commit | `52d8d9f7cdc4d303f3af5c390bc8c1dd41255dff` |
| PR HEAD | `a819714625798130700d97e70a4275c49a709910` |

The checkout/source commit identifies the CI source, not a main merge. The
saved native receipts were revalidated and converted to raw RGBA and PNG.
The old receipt contains real Mesa/backend metadata but no GTK/Pango/Cairo,
font/fontconfig or effective-theme observations. Its packet is therefore
`diagnostic_only_missing_fingerprint`; old missing values are never guessed.

Visual inspection of those bytes found a change from an empty to a checked
box beside the cropped `Line Num…` label. The adjacent Menubar box stays
checked. The original `menu_open` crop provides additional label context.
**Both original gutter crops `(0,64,48,128)` show background/border without
numeral glyphs.** The observed 6144 changed gutter pixels therefore do not
establish line-number visibility. The checkmark's 65 changed pixels likewise
do not select an approved mask or threshold. These are unapproved engineering
observations, not the independent human review required by the contract.

For that one run the four content frames were `2,6,9,14`. The tool checks
positive decimal-string type and causal ordering; it does not require those
frame numbers, popup surface IDs or changed-pixel counts on another run.

## 3. Product-path collection and separate context images

The existing B2 runner gains an opt-in `--prepare-review` mode. Ordinary
Inspect/Selection/Registry Launch, real pointer press/release and popup grab
barrier, SDK Create binding, Renderer, Stop and full retirement are reused.
There is no direct Mousepad launch, GTK action call, C1 keyfile write or
substitute shortcut. Without this flag the B2 diagnostic path is unchanged.

`host_n07_review.rs` uses the existing current-Create
`Registry::guarded(Runtime::{request,poll}_host_readback)` path. Immediately
after each canonical state it requests one ticket containing four tiles:

| Tile ID | x | y | width | height |
| --- | ---: | ---: | ---: | ---: |
| 101 | 0 | 0 | 256 | 128 |
| 102 | 256 | 0 | 256 | 128 |
| 103 | 0 | 128 | 256 | 128 |
| 104 | 256 | 128 | 256 | 128 |

These cover only the controlled fixture's upper-left **512×256** area, to
locate actual digits and the complete menu row. Each tile is at most 32768
pixels; a request is 524288 RGBA bytes, below the unchanged 1 MiB Renderer
request bound. The four context phases are `first_off_context`,
`first_off_menu_context`, `first_on_context`, `first_on_menu_context`.
They are supplementary observations, not additional formal Pixel Oracle states.

In B3 mode, the post-toggle gutter capture also waits for the same main
surface to publish a strictly newer `content_seq`, with no popup layer and
unchanged capture/Create/renderer/geometry identity. A bounded 200 ms quiet
interval then stages the readback, within a 12-second budget and at most 32
actual samples. The saved `post_popup_settling` proof records causal main-layer
progress and request order. Neither elapsed time nor a new composed frame
alone proves an application repaint, and no part of this wait proves glyph
identity. Failure retains the latest actual capture and normal retirement
evidence. The ordinary B2 mode keeps its existing behavior.

The original native receipt retains its 1 MiB limit. Larger context pixels
go into a separate **4 MiB maximum** `n07-b3-context.json`. The native
`review_context` entry binds the exact serialized sidecar SHA, byte length,
acquired phase names and completion status. Copy the raw bytes unchanged:
Python canonical reserialization would change the Rust digest.

The offline validator joins the full capture/target generation, SDK adapter
and Create lease, Runtime, renderer owner/context, geometry and composition
layers. Same-frame layers must match exactly. A later context frame must keep
the same surfaces/offsets/clips/scales/buffer dimensions and nondecreasing
content sequences. Request/present/frame ordering must place the context
between the corresponding canonical captures. `max_swap_us` remains a
nonnegative monotonic measurement and is excluded from immutable identity.

## 4. Fingerprint: observed fields and explicit gaps

The existing production `FINGERPRINT` field set is unchanged. Supplemental
evidence explains how each value was obtained; missing observations are an
exact explicit list. A partial record cannot pass production fingerprint
validation or silently become complete.

| Contract field | Observation / definition |
| --- | --- |
| `mousepad_commit` | Installed build evidence, fixed upstream commit, executable SHA |
| `waypipe_commit` | Independently verified upstream/archive mapping below; generated build and deployed binary SHA joined |
| `gtk_version`, `pango_version`, `cairo_version` | Libraries actually mapped by the dedicated Mousepad process; version getters run on those immutable library bytes in a separate observer, without GTK initialization or application calls |
| `font_sha256` | SHA of canonical sorted `{path,size,sha256}` descriptors for the observed mapped system fonts |
| `fontconfig_sha256` | Exact bytes of the isolated `fonts.conf`, without includes |
| `mesa_vendor`, `mesa_renderer` | Actual Renderer receipts, equal across the four states |
| `renderer_revision` | `sha256:` plus canonical tracked path/mode/content inventory of `native/`, root `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml` |
| `locale` | Allowlisted actual Mousepad environment `LANG`/`LC_ALL` |
| `scale` | Actual Renderer layer scale; mixed/unproven values cannot be normalized |
| `theme` | **Not established by this collector.** Configured Adwaita is auxiliary evidence only; the effective GTK setting remains missing |

The renderer-source inventory is deliberately conservative, including
compositor/core/model/shaders/dependency inputs. It is distinct from the full
checkout commit/tree identity, and excludes a future documentation or Golden
pin-only commit. Dynamic frames, times and swap metrics are not fingerprint
fields. Mesa version, library digests, font paths, process start identity,
build/patch hashes and typography-policy digests remain supplemental metadata.

Before product launch, B3 alone prepares fixed system typography in its fresh
disposable container: `DejaVu Sans 10`, configured Adwaita and a bounded
fontconfig directory with explicit generic-family aliases. This never changes
Mousepad's Line Numbers key or any C1 profile. Actual fonts are observed rather
than inferred from `fc-match`. Docker defaults do not grant cross-UID ptrace;
the bounded observer helper runs under the dedicated app UID, exports only
allowlisted metadata through a pipe, exits before final census, and requires
no added capability. It receives no SSH credential FD, display or raw logs.

The mapped font set is conservative. It can include a fallback face that was
examined but did not draw a glyph, and periodic observation does not prove
every short-lived mapping. Exact glyph-to-font attribution remains a review
limitation. Configured theme is not promoted to an observed effective theme.
Consequently a successful collection can still produce a **partial** record
and a diagnostic-only candidate. CI preparation success does not hide this gap.

GTK documents `gtk-theme-name` as a readable in-process setting, while
[`GTK_THEME`](https://docs.gtk.org/gtk3/running.html) can override it. Neither
the [default Adwaita value](https://docs.gtk.org/gtk3/property.Settings.gtk-theme-name.html)
nor a supervisor's separate GTK instance proves the actual app's effective
theme. A future reference acquisition can add a separately designed read-only
observation of that property and its override/theme assets. The documented
GTK Inspector is one possible investigation route, but its additional app
windows, Registry admission and retirement would need explicit treatment;
this collector does not inject it or claim that inspection has happened.

### Waypipe commit mapping

Upstream tag `v0.11.3` resolves to
[`3ea049b2d33cbf1000714c28b9668f8c244f70bd`](https://gitlab.freedesktop.org/mstoeckl/waypipe/-/commit/3ea049b2d33cbf1000714c28b9668f8c244f70bd).
The downloaded archive SHA is the existing lock's
`2d4093903d444fda39c9b7db0a0b826f450a64d4302a03c9c9e3dbe480e8bb03`.
All **141** non-directory entries matched `git archive` of that commit by
relative path, Git file mode and payload SHA (symlink target bytes if present).
Compact canonical sorted `[path,mode,sha256]` inventory SHA is
`76dc5c8146c2234856e576751ed87741cb9308cb0f0e296121bdb436fa19a37a`.
The repository's applied patch/tree/binary are separately recorded; the
upstream commit is not falsely described as an unpatched runtime executable.

## 5. Review packet, provenance and tamper boundary

`scripts/linux_real_app_v3_n07_review.py` reopens and validates saved B2
receipts before rendering. `candidate.json` uses the distinct revision
`eidolon-i6-n07-review-candidate-v1`, never the production Golden schema.

| Output | Meaning |
| --- | --- |
| `raw/*.rgba`, `raw/*.png` | Exact original RGBA and lossless PNG, top-left origin; PNG adds no color profile or pixel transformation |
| `aids/*` | Nearest-neighbor zoom, absolute RGB differences, mask views; never expected-image inputs |
| `context/*` | Separate raw context tiles and stitched display aids; never canonical states |
| `evidence/*` | Fixed-name bounded inputs needed to reproduce and revalidate the packet |
| `candidate.json`, `candidate.sha256` | Exact proposal and integrity digest; no review authority |
| `review.html` | Images, metadata, pinned source link, limitations and independent-review checklist |

The packet ties original workflow run/attempt, artifact name/ID/archive SHA,
source commit, PR HEAD, internal run/capture and all Renderer identities to
each state. During CI upload, the artifact ID/archive digest do not yet exist:
`artifact_status=pending_upload` and explicit nulls preserve that fact. After
download, a separate provenance receipt can record the actual GitHub artifact
identity/digest and the original packet digest. Re-exporting with that new
provenance creates a new packet digest; it grants no approval. An artifact's
own digest cannot be inserted into itself without a circular definition.

Default masks select the full original ROI, tolerance is zero, and the
provisional negative contrast uses at least eight changed pixels with channel
delta at least one. These are visible **unreviewed starting proposals**, not
optimized masks or thresholds learned from the observed differences. A manual
proposal can refine masks/tolerance, but changes the exact packet digest and
requires renewed independent review. The original broad blank gutter is not
automatically cropped into a falsely successful Golden.

Keep the expected packet SHA **outside** the packet under review. The validator
rederives pixels, assets and metadata from saved evidence and checks that
external SHA. Hashing internally consistent fabricated evidence is not
cryptographic authentication: the artifact's source/transport identity and
external digest are also required. This tool performs no approval/promotion
operation, and its digest does not populate `APPROVED_SHA256`.

## 6. Operator commands

From a clean committed checkout, run the existing disposable product route:

```bash
bash scripts/ci/check-linux-desktop-v3-n07-b2 \
  --artifact-root /tmp/i6-n07-b3-private-empty --prepare-review
```

The wrapper retains ordinary B2 failure status, separately validates context
and fingerprint, runs saved-receipt mutations and exports `n07-b3-review/`.
`n07-b3-validation.json` reports `review_prepared` only for a generated and
revalidated packet; fingerprint completeness and missing states remain explicit.
Fresh B3 CI additionally requires the actual app process, mapped font/library
and locale observations, no observation error, and only the documented `theme`
gap. An unexpected collector failure fails that gate after saving its valid
diagnostic packet; offline extraction of honest incomplete old evidence remains
available. A pending/absent main-content settling proof cannot pass fresh B3 CI.
Per-file collection failures retain other safe observations and existing
native/environment retirement evidence. No invalid body, HOME tree, key,
arbitrary log or user document is bulk-exported. An external container rescue
cannot count as normal product cleanup. CI preserves failure diagnostics too.

For an already downloaded B2 artifact, prepare an explicit provenance JSON
with the exact fields in `_provenance()` (the observed values above are an
example), then:

```bash
python3 scripts/linux_real_app_v3_n07_review.py export \
  --native /tmp/b2/n07-b2-native.json \
  --environment /tmp/b2/n07-b2-environment.json \
  --validation /tmp/b2/n07-b2-validation.json \
  --provenance /tmp/b2-provenance.json \
  --output /tmp/b2-review-new

python3 scripts/linux_real_app_v3_n07_review.py validate \
  --bundle /tmp/b2-review-new --expected-sha256 EXTERNALLY_RETAINED_PACKET_SHA
```

For new B3 evidence add `--fingerprint` and `--context` with the corresponding
saved paths. Missing old evidence must be reported, not backfilled. To reject
self-reference when planning a separate comparison, also supply
`--observed-run-id`, `--observed-capture-id` and, when known,
`--observed-workflow-run-id`. A different label for the same capture is rejected.

## 7. Independent approval and B4/B5 handoff

An actual reviewer must verify the complete View/Line Numbers label against
[fixed Mousepad menus.ui](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/gtk/menus.ui),
its action `app.preferences.view.show-line-numbers`, the correct unchecked and
checked row, actual numeral glyphs, popup/parent coordinate composition,
font/theme/render environment, and mask/tolerance/negative-contrast coverage.
Hover, caret, selection, shadow and background-only changes must not supply
semantic proof. New context acquisition makes those questions inspectable;
it does not answer them with an automated `reviewed=true`.

| Formal state | B3 coverage |
| --- | --- |
| `first_off_gutter` | Real original crop; numeral semantics still require corrected/reference ROI review |
| `first_off_checkmark` | Real original crop; independent semantic review required |
| `first_on_gutter` | Real original crop plus new context when collected; review required |
| `first_on_checkmark` | Real original crop plus new context when collected; review required |
| `second_on_numbers_1_8` | **Not acquired** |
| `second_on_checkmark` | **Not acquired** |

Complete the missing effective-theme/font evidence and acquire both second
states in a separately identified reference run, using real new-nonce startup
and the fixed eight-line document. B4 can supply the normal two-session
collection mechanism; its later acceptance run must be different from this
reference acquisition. Copying or renaming first-session images is forbidden.

Only a separate, actual approval change may add the six-state fixture at
`scripts/fixtures/linux-desktop-v3-acceptance/n07-pixel-golden-v1.json` and set
the exact `scripts/linux_real_app_v3_n07_pixel.py:APPROVED_SHA256`. The real
reviewer, review commit, reference run, source, all six images, mask/tolerance
and complete fingerprint must be reviewable there. B3 leaves the path absent,
the pin `None` and production `load_reviewed()` fail-closed. B5 then joins
reviewed pixels with independent keyfile and two complete product sessions;
it cannot infer N07 acceptance from this packet.

## 8. Verification entry points

```bash
python3 -m unittest discover -s scripts/tests -p 'test_linux_real_app_v3_n07*.py' -v
cargo +1.95.0 test --locked --manifest-path scripts/fixtures/linux-real-app-v3-host/Cargo.toml
cargo +1.95.0 fmt --manifest-path scripts/fixtures/linux-real-app-v3-host/Cargo.toml -- --check
python3 scripts/docs.py validate
```

Tests cover exact pixel reproduction; length/SHA/ROI/fingerprint mutations;
source/capture/generation/Create/owner/context/frame substitution; layer and
geometry disagreement; strict types; missing or invented second states;
unapproved production rejection; self-reference; mask/tolerance/packet drift;
same-UID observer teardown; and retained failure/cleanup evidence. Synthetic
unit pixels remain synthetic. Actual GUI collection is the explicitly named
CI job/artifact; a test run alone does not establish GUI or Quest evidence.

## 9. First B3 collection and retained failures (2026-11-18)

The first B3 implementation was actually run, downloaded and independently
revalidated as saved bytes. This is an engineering inspection, not a human
Golden approval. The failed jobs below remain part of the execution record.
That packet was validated by its source revision `38d7f67180e2de36b4721fee3b9e8e6cb3a6e4bc`.
The corrected validator intentionally rejects its B3 context because it lacks
the newly required main-content settling proof. Use the historical revision
to reproduce the old diagnostic check; do not rewrite or promote the old data.
Original B2 receipts without the B3 context remain extractable as legacy
diagnostic packets, without making a settling or fingerprint claim.

| Identity | First B3 execution |
| --- | --- |
| PR HEAD | `38d7f67180e2de36b4721fee3b9e8e6cb3a6e4bc` |
| Checkout/source | `42ae856a7c1f67dfb10cee09715cc54e9aa87fae` |
| L7a run | [44284319234](https://github.com/tin-machine/eidolon-wayland/actions/runs/44284319234) |
| B2/B3 job | [134733031962](https://github.com/tin-machine/eidolon-wayland/actions/runs/44284319234/job/134733031962), succeeded |
| B3 artifact ID | `14418064377` |
| Artifact name | `linux-desktop-v3-n07-b3-42ae856a7c1f67dfb10cee09715cc54e9aa87fae` |
| Downloaded ZIP SHA-256 | `7f5fe48e7433608ccea96313cb834d071407a56ce4bfe9855ad03c074a9d098e` |
| Candidate SHA-256 | `2d42727629ff9cae13cc82ebcfe80d21c4fe77d44925b0476f4c6415a453d048` |

Four actual canonical states and four supplemental contexts were acquired;
normal product cleanup was verified. The complete Line Numbers label and
unchecked/checked boxes are visible in the two menu contexts. A numeral `1`
and a separate caret are visible at the left of the reopened-menu context.
However, the canonical `first_on_gutter` at frame 11 remains blank: the main
surface's `content_seq` is still 9, the same as before the toggle. Popup
removal advanced the composed frame without establishing a new parent buffer.
The later reopened-menu frame 15 has main `content_seq=17` and the digit.
These numbers describe this run only; no new exact-frame expectations follow.

The fingerprint helper returned no observed process/fonts/libraries, with
`N07_FINGERPRINT_OBSERVATION_FAILED`. Thus GTK/Pango/Cairo, mapped font SHA and
locale were missing in addition to the intentionally unobserved effective
theme. The packet correctly remained `diagnostic_only_missing_fingerprint`;
it must not be cited as a fully fixed environment. The generic error does not
establish an exact syscall/errno retrospectively. This execution motivated
per-process discovery error handling, more specific bounded diagnostics and
a stricter fresh-CI observation gate, without backfilling its old receipt.

L7a finished with 11 successful jobs and two pre-GUI dependency failures:
foundation [134733031518](https://github.com/tin-machine/eidolon-wayland/actions/runs/44284319234/job/134733031518)
exceeded the 240-second APT install deadline; N05
[134733031633](https://github.com/tin-machine/eidolon-wayland/actions/runs/44284319234/job/134733031633)
failed in Waypipe dependency acquisition with HTTP 500. N06 succeeded; this
does not close Issue #50. In validation run
[44284319245](https://github.com/tin-machine/eidolon-wayland/actions/runs/44284319245),
Host and documentation passed, while Android
[134732987711](https://github.com/tin-machine/eidolon-wayland/actions/runs/44284319245/job/134732987711)
hit the same APT deadline before Android build/tests. CodeQL run
[44284319238](https://github.com/tin-machine/eidolon-wayland/actions/runs/44284319238)
passed all five languages. Bounded failure excerpts and downloaded raw
artifacts were retained outside tracked source. No Oracle was relaxed and no
unrelated N05/N06/Android implementation was changed to conceal these results.
