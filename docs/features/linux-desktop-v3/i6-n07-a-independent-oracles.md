# I6 N07-A — independent keyfile and reviewed-pixel contract (preparation)

> Status: N07-A **merged** in [PR #46](https://github.com/tin-machine/eidolon-wayland/pull/46) (merge 9aaac53cbb7cde03c549f7cb230cf3268bed4c91); **NOT N07 acceptance**. The merged N07-A contains no live N07 GUI run, second C2 lease, reviewed pixel golden or typed N07 receipt. All 13 frozen N07 assertions remain **unverified** until N07-B product evidence exists. Neither a green unit test nor file-only / pixel-only predicates may turn N07 into complete.

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

## D. N07-B 実装順序と Quest 実機検証の分離（2026-11-17 追記）

本節は上記 C の**実施順序と判定ゲート**を明文化する。新しい N07 variant／assertion を定義せず、[I6 詳細設計](../../linux-real-app-v3-i6-detailed-design.md) §10.2／10.3／11／20 と [統合計画](../../linux-real-app-v3-integration-plan-20261111.md) §11〜13 の責任分界を変更しない。

**結論：N07-B の Host 実装は Quest 3 実機確認を待たずに進める。** I6 の Host R+S と I7/V12 の実 Meta SDK／Android、H の装着・物理操作は独立した証拠である。早期の Quest 試験的スモークは並行してもよいが、Host assertion の根拠に数えず、N07-B のブロッカーにも完了条件にもしない。

| 順序・レビュー境界 | 作業・検証するもの | 進行条件／未成立の扱い |
| --- | --- | --- |
| 0. N07-A（完了） | C1 永続 keyfile の read-only FD suboracle、fingerprint／ROI pixel comparator、負例 CI | **比較器の単体成功だけでは N07 0/13。** |
| 1. N07-B1：Host GUI 最小経路 | 通常 Registry→Runtime→SSH Controller→C2→Waypipe→Mousepad→native compositor→Host SDK→renderer に乗せ、実 View メニューと popup／pointer 配送を採取する。 | 実装済み診断。Registry ACK だけで pointer assertion を合格にしない。 |
| 2. N07-B2：単一セッション4状態 | 実 pointer で Line Numbers 候補位置を操作し、OFF/ON gutter／View checkmark の4 ROI を取得する。 | PR #49 マージ済み。SDK／Renderer／popup を照合した診断であり、第二セッション・意味確認・Golden は含まない。 |
| 3. N07-B3：独立レビュー準備 | 実 receipt から画像・補助 context・fingerprint・ROI/mask/tolerance 候補・出所資料を生成し、改変を検証する。 | [B3 詳細](i6-n07-b3-pixel-review.md)。実際の承認は別工程。4状態を6状態へ複製しない。完全 fingerprint と独立6状態 reference が揃った後のみ人手レビュー／SHA lock を行う。 |
| 4. N07-B4：同 StorageRecord の2セッション | 1st：key absent＋GUI OFF→実 pointer ON→独立 keyfile true→通常 Close と**全 product 回収**。2nd：同 installation/profile storage/binding と C1 root、新 nonce/capture/Runtime/owner で通常 Inspect／Launch、固定8行 file、最初から GUI ON＋key true、通常 Stop／全回収。 | 未実装。B4 の収集機構は未取得2状態の独立 reference 取得にも利用できるが、reference run と acceptance run は分離する。Golden 未承認なら pixel assertion は未成立。 |
| 5. N07-B5：統合 oracle と CI | 保存済み native＋supervisor keyfile＋reviewed pixel＋2回分の product cleanup を専用 oracle で revalidate。typed receipt と正式 linux_real_app_v3_evidence.py を接続する。 | 未実装。**N07 13/13** が現 head の実証拠で成立した場合のみ **N07/same_storage_restart** を complete。I6 全体は host_v3_acceptance_passed=false のまま。 |
| 6. I7/V12 → H | Host 実装と区別して実 APK／Android 通常保存／Meta SDK Surface・callback・Scene、Quest 操作、最後に装着して視認性・物理キーボード／コントローラーを検証。 | I7/V12 と H の固有証拠が必要。Host の S を Quest SDK evidence に昇格させない。正式な I7 受け入れの前提条件は統合計画に従う。 |

### D.1 View メニューの popup は dialog と同一視しない

Mousepad 固定 upstream の [menus.ui](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/gtk/menus.ui) には _View と Line N_umbers（action は app.preferences.view.show-line-numbers）がある。**メニュー実体を xdg_toplevel と決め打ちしない。** Wayland の [xdg-shell](https://wayland.app/protocols/xdg-shell) では、menu は xdg_popup 等の役割を持ち得る。実 GTK/Waypipe/compositor の protocol trace を先に取り、実際の popup role と生成・破棄を識別する。popup が xdg_popup か、親 surface 内の描画かなどは**実観測**に基づく。

xdg_popup が現れた場合、N05/N06 transient xdg_toplevel admission を機械的に再利用しない。同 capture／NativeOrigin に対応する parent chain、surface/target generation、configure/ack serial（および grab に必要な入力 serial）、layer/content frame と pointer routing を照合する。popup を独立した通常アプリ window として誤採用したり、別 capture の popup を容認しない。popup serial／layer を現行 API が提供しなければ、**通常製品の観測 seam を追加してテストする**。fixture で架空の popup 証拠を追加しない。

入力は同 capture の current main/popup target、input epoch、focus token の正当性を検証し、**Registry gate を通る** pointer press／release／実配送を計測する。keyboard shortcut の捏造、D-Bus、keyfile 直接変更、GTK3_MODULES 注入による代替成功は禁止。クリック座標と ROI は固定環境・review 済み fixture／独立確認したレイアウトに拘束し、**その acceptance run の observed image を基準値に自動更新しない**。

参考：Wayland [xdg_popup と親 surface／grab](https://wayland-book.com/xdg-shell-in-depth/popups.html) と [Mousepad 固定 menus.ui](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/gtk/menus.ui)。

### D.2 PR 粒度、再利用、ブロッカー

実際の分割は **B1＝pointer/popup、B2＝単一セッション4状態、B3＝独立レビュー準備、B4＝実2セッション、B5＝正式統合 oracle** とする。旧案の「B2 で2セッション」は現在の分担に適用しない。golden fixture の実承認は独立したレビュー境界であり、B3 の候補生成だけでは成立しない。**各実装 PR では詳細設計だけで終わらず、動作するコードとテストを追加する。**

独立 oracle の意味を緩めないため、欠損は失敗・未実施として残し、N01〜N06 の既存 oracle を変更して N07 を通す方法を採らない。N08 以降は keyfile suboracle や popup 観測 seam を再利用できるが、receipt や storage/owner/capture を共有して成功に数えない。Issue [#45](https://github.com/tin-machine/eidolon-wayland/issues/45) の自然 Close／異常終了の分類変更は N07-B に含めない。

**完了判定を二段階に区別**する：N07 自体の Host 13/13 と、I6 全体の 28 family／87 required variants／533 assertions。N07 13/13 が成立しても I6 全体は自動的に complete ではない。Quest/H も独立した未検証フラグを維持する。

