# I6 N07-A — independent keyfile and reviewed-pixel contract (preparation)

> Status: N07-A implementation PR; **NOT N07 acceptance**. No live N07 GUI run, second C2 lease, reviewed pixel golden or typed N07 receipt is present. All 13 frozen N07 assertions are **unverified**. Neither a green unit test nor the file-only / pixel-only predicates below may turn N07 into complete.

## Baseline and immutable contract

Base is main merge commit 0537ac2e319442b0d125be2edc4bddbbaab0d346 (N01–N06 merged). Source references: I6 detailed design §10.2/10.3/11, immutable cases-v1.json, N05/N06 independent file and product oracles, and upstream Mousepad 0.6.3 at commit 65fbfe0459259f820fcc8b9c4dd454228304842a.

The upstream menus.ui defines **_View**, **Line N_umbers** and action **app.preferences.view.show-line-numbers**; no shortcut for Line Numbers is assumed. N07 must click the actual menu via the ordinary Registry pointer input gate. Mousepad has no authority to edit the expected golden.

The fixed case is exactly N07/same_storage_restart, outcome complete, evidence classes R+S and 13 assertions in their existing order. The acceptance inventory remains 28 families / 87 variants / 533 assertions, manifest SHA-256 e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33. Keep host_v3_acceptance_passed=false, the Quest/physical-input flags false, and v2/v3 and C1/C2 contracts unchanged. Natural Close reason classification continues in issue #45, not this PR.

## A. Independent read-only keyfile supervisor

The N07-A suboracle in scripts/linux_real_app_v3_n07_settings.py reads
< trusted persistent C1 profile root >/config/glib-2.0/settings/keyfile.
This **must be the persistent C1 StorageRecord-derived root**, not a new per-nonce directory and not a descriptor path invented by a producer. The future supervisor must obtain the root path, UID and canonical storage identity from its independently retained C1 context, retain the root FD across both sessions and compare the named root identity on every snapshot.

The probe never writes or creates the settings directory or keyfile. It traverses each component with directory FDs and O_NOFOLLOW, checks owner, directory type and writable bits, and checks the final file as regular, app-UID owned, single-link, non-group/world-writable and bounded to 64 KiB. It compares named and opened identities, checks size/mtime/ctime and inode before/after reading, parses the fixed GSettings key case-sensitively with duplicate rejection, and exports only bounded metadata/digests (no raw keyfile).

Three independently measured snapshots are compared:
- before first GUI toggle: show-line-numbers *key absent*, not merely false;
- after first GUI toggle: key present with exactly lowercase true;
- second session without a settings toggle: same persistent profile root, same canonical storage identity, same saved value, owner, and exact keyfile bytes digest (the inode may change during an atomic GLib Close flush).

The digest of all *other* keys/sections must be identical in all three snapshots. Changed unrelated settings, an unexpected change to the keyfile bytes on second launch, a replaced root or intermediate directory, link aliases, malformed keyfiles and cross-storage receipts are rejected. The setting can only earn product authority if the separate N07 normal Registry pointer delivery, GUI state, renderer and C2 proofs all pass. A malicious direct edit can satisfy this **file-only** predicate and must never satisfy N07 acceptance.

## B. Independently reviewed pixel golden

scripts/linux_real_app_v3_n07_pixel.py defines a strictly bounded, source-pinned pixel comparator. The expected fixture path is fixed to scripts/fixtures/linux-desktop-v3-acceptance/n07-pixel-golden-v1.json. **No such reference fixture is committed in N07-A.** The production APPROVED_SHA256 lock is deliberately unset. load_reviewed() must fail N07_GOLDEN_UNREVIEWED, and a future N07 job must leave affected assertions not_run/fail until a distinct, human-reviewed reference and its exact SHA are committed in a separate review.

The reference is built independently of the acceptance run (for example an A0 *read-only* observer plus separately reviewed reference images). It must record origin review run, review commit and reviewer. Approval metadata inside an unpinned JSON file is insufficient; the exact fixture digest is pinned in source only after review. Runtime observations may **never** supply the expected image or override the production pin.

The fixture pins exact Mousepad/Waypipe commits, GTK/Pango/Cairo, font and fontconfig SHA-256, Mesa vendor/renderer, renderer revision, theme, locale and scale. The pixel suboracle checks exact fingerprint equality, six fixed observations and two explicit negative contrasts:

| Required ROI | Session / semantics |
| --- | --- |
| first_off_gutter | First: line-number gutter OFF |
| first_off_checkmark | First: View menu unchecked |
| first_on_gutter | First: gutter ON after real pointer click |
| first_on_checkmark | First: View menu checked |
| second_on_numbers_1_8 | Second: fixed eight-line document, 1–8 visible |
| second_on_checkmark | Second: View menu checked on new nonce |

Each ROI has fixed x/y/width/height, reviewed expected RGBA, explicit binary mask (at least eight pixels where available), and bounded per-channel absolute tolerance 0–32. Negative ROIs require an explicit minimum number of pixels whose observed RGB differs between OFF and ON by a reviewed minimum contrast, not just a matching SHA for both states. Mask, ROI or tolerance changes require a new reviewed fixture SHA. The comparator rejects mismatched coordinates, missing/current readback metadata, fingerprint drift, reused nonce/capture/Runtime/owner, same reference/observed run and pixel mismatches.

N06 HostSdkAdapter currently requests one **4×4 center** real-content ROI for its SDK admission check. It is not sufficient for gutter, checkmark, or eight line numbers. N07-B must request bounded, semantically placed, real content ROIs through the same Registry-guarded Runtime readback API; it must validate readback ticket, target key/create sequence, content frame, popup serial and layer, SDK/renderer current evidence and each resource retirement. No C2 GTK3_MODULES injection and no direct Mousepad launch are permitted. The pixel comparator alone does not attest to readback provenance.

## C. Planned live N07 product integration (not implemented in N07-A)

1. Disposable N07-specific supervisor creates one pinned installation and StorageRecord, controls independent app/driver accounts and source-pinned SSH/Waypipe/Mousepad, and retains a trusted C1 profile FD. The first keyfile snapshot happens *before* Mousepad launch. Host Runtime is started only from normal Inspect/Selection and Registry Launch.
2. The Rust driver receives main admission/B1 Ready/Host SDK readback, samples OFF gutter and View menu using current target/focus/epoch, clicks Line Numbers with actual Registry pointer packets and checks delivered count. The popup generation/serial/layer and ON gutter/checkmark are recorded without private GTK injection or direct filesystem mutation.
3. Supervisor samples the saved keyfile independently. Normal Close, remote cleanup, I/O/native/SDK/renderer retirement, finalizer, Runtime stop and Registry destroy must all finish *before* the second launch.
4. Reuse the **same** installation_id/profile_storage_id/profile binding and persistent C1 directory but use a **different** nonce, capture, Runtime and application owner. No C2 lock force-release. Use normal Inspect/Launch. Open the pre-created fixed eight-line document via admitted keyboard, no file or settings changes via the supervisor during the GUI phase. Collect second-ON gutter 1–8/checkmark, true keyfile and real readback, followed by normal Stop and complete product retirement.
5. An independent N07 acceptance oracle revalidates *saved* native, environment, keyfile, pixel and two product cleanup receipts, including cross-session causal ordering. Only after this exists may N07 typed receipts be added to linux_real_app_v3_evidence.py. Never copy an N06 receipt or trust producer status=pass.

Full live acceptance uses a new source-bound disposable Docker job with dedicated nonroot accounts, host-key pin, fixed exports, PR head versus checkout-source distinction, and explicit fixture-rescue separation. N07-A only provides an isolated unit/negative gate: it **does not** start a Docker GUI and it **does not** export N07 acceptance artifacts.

## Tests and remaining evidence

The N07-A tests cover immutable manifest, pre-existing true/false, same storage/root identity, unexpected key changes, keyfile rewrites, symlink, hardlink, duplicate key, invalid boolean, oversized input and root replacement. Pixel tests cover reviewed digest lock, mismatched fingerprint, unreviewed/zero mask, wrong and missing pixels, foreign capture, nonce reuse, self-reference and negative-contrast failure.

Not established yet: **all 13 N07 assertions**, including both GUI states, actual pointer delivery, verified menu popup, key persistence across *real* C2 sessions, product census/retirement, and typed independent acceptance. The separate N07-B implementation/PR must explicitly report them not_run until actual proof exists. N08 can reuse the independent keyfile probe for fresh-storage isolation, but must not reuse a first-session receipt.
