# I6 第3実装単位: 実 Host EGL・bounded readback・renderer retirement

2026-11-15（日本時間）。開始点は最新 main
`4bf08e6df9cc3a2eb3cedb8856be52d9fe1dcfca`（PR #34 merge）。
PR #34 の検証済み head は `a329da7f64ef3fff0fb9e50f9b841b350521fb3b`。
開始時に main の検証／L7a／CodeQL が成功済みであることを確認した。
別スコープの open PR #36 は Python Deep 解析であり、この変更へ取り込まない。
既存 checkout と検証 worktree を保存し、専用 clone／branch で実装する。

**この単位は renderer backend の T 証拠を成立させる。通常 v3 Launch や N02 の合格ではない。**
[I6 詳細設計](../../linux-real-app-v3-i6-detailed-design.md) §7.2、§7.3、§9.1、§13、§21 を
現 source の所有境界へ接続した。詳細設計の proposed 状態、I6 の manual discovery、
I2／I4 の別々の resolver context と check_required 警告は維持する。
第1・第2単位の履歴は [evidence validation](i6-evidence-validation.md) と
[N01 Inspect-only](i6-n01-inspect-only.md) に残す。

## 1. 実装した経路

| 境界 | 実装と成立条件 |
| --- | --- |
| feature | `eidolon-render/host-acceptance` は default-off。core から render／transport へ伝播する。Linux 以外、特に Android との組合せは compile error |
| backend | 通常 Controller の GL owner が明示的な surfaceless EGL display、GLES3 context、target ごとの RGBA8 pbuffer を生成する。Mesa／llvmpipe と GLES3 を実 query で照合し、失敗時は error を返す |
| 描画 | 同じ `Compositor::paint_decorated` の内部経路で app、背景装飾、前景装飾を描く。別 compositor や成功する Host stub へ切り替えない |
| frame | 実際に選んだ immutable `WindowSnapshot` を描画と receipt の両方に使う。content FrameSeq と target ごとの成功 present counter を分ける |
| readback | 描画後、swap 前の実 pbuffer を RGBA8 で読む。pack state を保存・復元し、左上原点へ行を正規化する。GL error、CPU が待つ GPU fence、swap の成功後だけ receipt を公開する |
| retirement | 同じ FIFO と generation admission seal を使い、GPU fence、対象 surface の実破棄、texture/image/player 参照の解放を確認してから fence を完了する |
| shutdown | GL owner 上で surfaces → compositor → context → 最後の display lease の順に解放する。別 renderer が使う display を終了しない。失敗した回収は `owner_reclaimed` に昇格させない |

入口は [render Controller](../../../native/render/src/lib.rs)、
[Host EGL backend](../../../native/render/src/host_egl.rs)、
[共通 compositor](../../../native/render/src/compositor.rs)、
[readback 型と bounded ledger](../../../native/render/src/host_readback.rs)、
[Runtime guarded 境界](../../../native/core/src/runtime.rs) である。

通常の最大 dimension 2,048、1 buffer 16 MiB、SHM client 64 MiB／total 128 MiB、
既存 PixelBudget 128 MiB を変更しない。pbuffer も個別の validated dimension と
renderer 内の合計 128 MiB 上限で制限する。GL fence wait は render thread 内で最大 100 ms とし、
timeout／wait failure を成功へ変換しない。pump は request／poll を続けられる。

## 2. 要求と実フレームの相関

renderer の `HostReadbackRequest` が受け取るのは full TargetKey、minimum FrameSeq、
非ゼロ request sequence と ROI のみである。caller は owner ID、実 frame、present、
CreateSeq、backend 成功状態を入力できない。ticket は非 Deserialize の private Weak owner を持ち、
別 Controller に同じ数値の key を渡しても使用できない。

- 一要求は最大 8 ROI、RGBA8 合計 1 MiB。空 ROI、重複 ID、範囲外、overflow を拒否する。
- 保持する要求／未回収結果は最大 8 件、予約 byte の合計も 1 MiB。通常 FIFO の 32 件上限を共有する。
- minimum 未達は Pending のまま。要求の処理だけで FrameSeq を増やさない。
- poll の成功結果は一度だけ取り出せる。ticket の drop は取消と予約解放を行い、遅い GPU 結果を捨てる。
- 新世代の attach admission、retire、stop、context loss は未取得結果も失効させる。
  timeout や新 owner の空配列で旧 ticket を成功にしない。
- raw pixels は caller が比較する一時 buffer とし、Debug／Serialize に本文を含めない。
  公開 artifact は比較値と SHA-256 を保存する。

receipt は renderer が採番した owner、context epoch、request、full TargetKey、要求 minimum、
実 FrameSeq、present sequence、snapshot identity、logical/buffer geometry、content rect、
layer の surface/content sequence、ROI revision/digest、pixel digest、実 backend fingerprint を保持する。
idle request や装飾だけの更新は同じ content FrameSeq の新しい present になる。

Runtime の `request_host_readback`／`poll_host_readback` は通常 `guarded` を通り、
実 panel ledger が発行した Create command sequence と現在の scene／full target を照合する。
v3 では生存する retirement owner の origin／target ownership と未 seal を必要とする。
戻り値に加える RuntimeHandle／CreateSeq は native が取得した値であり、fixture JSON から受け取らない。
この read-only API は SDK binding、B1 Ready、input gate、retirement proof を変更しない。

## 3. 回収と失敗の境界

`retire_target` は exact tuple を seal してから通常 FIFO に入る。
古い target の fence を別 renderer が受理せず、古い callback は retired tuple を再 attach できない。
新世代が同じ logical window の画像を使っている場合、旧 target の回収では共有 texture cache を消さない。
`retire_window` は最後の submitted image、全該当 target、texture、player cache を解放してから完了する。

`retirement_complete` は同じ renderer の fence、または同じ GL owner の検証済み全終了だけを使う。
実 GL／EGL 回収失敗は sticky に保持し、後続の「対象なし」や Stop で成功へ上書きしない。
unwind 時も surface が device より先に drop されるよう寿命順を固定する。
context loss 時は古い content evidence と readback ticket を失効させてから再構築する。

## 4. N01 と既存構成

target がない場合、theme staging だけで Host EGL Device を初期化しない。
CPU theme の採用と初回 target の GPU 初期化を分け、Inspect-only が不要な context／pbuffer を持たない。
EGL が利用できない別 process でも theme staging とゼロ target の観測が成立し、
その後の本当の attach は失敗する、という負例で fallback がないことを確かめる。

既存の `cfg(test)`／`platform::TestControl` に依存する renderer unit tests は bookkeeping backend を使う。
その backend の readback は常に失敗し、実 backend の成功 receipt を生成しない。
実 EGL integration tests は通常 library を link する別 test binary とし、専用 job で必ず実行する。
default Host tests と通常 Android 構成は既存経路を維持する。

PR #34 の Stop／ACK → remote cleanup → I/O retirement → finalizer の因果順序、
正当な ACK-loss と同 capture Recover、欠落 artifact の case 不合格伝播、cancelled の部分合格除外は変更しない。
N01 の Start=0／NoLocalAllocation と実 Registry destroy は同じ専用 N01 job でも検証する。

## 5. 実 SHM fixture と専用 CI

[fixture README](../../../scripts/fixtures/linux-real-app-v3-renderer/README.md) と
[独立 validator](../../../scripts/linux_real_app_v3_renderer.py) を参照する。
既存の実 wl_shm client が private socket pair 上で xdg toplevel を作り、
production ProtocolState が付けた window／surface／FrameSeq を Controller に渡す。
描画時の期待値は renderer の `reference_pixel` に依存せず、固定入力から Rust と Python で別々に計算する。

1280 × 1280 target 中の 32 × 32 app と装飾を実 readback し、
initial、idle、装飾変更、実 SHM 更新、新 generation の順に比較する。
実 Wayland frame callback の送出を制御して input の更新時点を固定するため、
product の successful frame や content sequence を setter で投入しない。
Scene／generation は renderer 単位の明示 T 入力であり、通常 Runtime Create の実施を装わない。

専用 Host renderer job は既存 L7a の conservative scope と Ubuntu disposable container を使う。
専用 nonroot account、network none、host mount なしで実行し、
既存 desktop、profile、Documents、settings、共有 ADB を使わない。
EGL 不在、未実行、欠落 receipt、失敗、timeout、外部救済は非成功である。

binary に実 source commit／canonical tree digest／feature identity を埋め、実行前後と外側で照合する。
PR head と GitHub が checkout した synthetic merge commit を別 field に保存する。
古い binary／artifact の source は現在の head の結果へ読み替えない。
remote C1/C2 の固定 15-source inventory、source pinning、frozen v2、product deadline／lease は変更しない。

census は実 Snapshot から資源数、対象 key、error 等の検証に必要な項目だけを抽出する。
UI 設定の小数値を含む診断 snapshot 全体を strict JSON artifact に流さず、
実際の出力 bytes を既存 parser／writer に往復させる回帰テストを専用 CI でも実行する。
frozen parser の型制約は緩和しない。

## 6. 検証記録と環境制約

開発中の専用 scratch 環境で、固定 Rust 1.95.0 と既存 `build-native-deps` を使用した。
system package／HOME を置換せず、必要な toolchain と GLES linker/runtime files を専用 directory に置いた。
実測 renderer は **Mesa llvmpipe (LLVM 19.1.7, 256 bits)、OpenGL ES 3.2 Mesa 25.0.7-2+deb13u1**。
この値はそのローカル実験の fingerprint であり、Ubuntu CI の値を推定するものではない。

| 検査 | 開発時に確認した範囲 |
| --- | --- |
| default renderer unit | 26 件成功 |
| feature renderer unit | 31 件成功。context loss、owner／世代／min-frame／budget、stub の成功禁止を含む |
| 実 EGL integration | 3 件成功。実 ROI/行方向/FrameSeq、複数 renderer の display lifetime、EGL 不在と lazy theme |
| retirement 後の遅延 submit | 1 件成功。window の admission seal で拒否し、画像参照と pixel budget が回収完了後に残らないことを確認 |
| Runtime 新規 guarded 境界 | 4 件成功。native Create 相関、同じ数値 ID の foreign owner、旧 target／scene、stop/reset |
| Registry feature | 4 件成功。create／stop／destroy の反復を含む |
| N01／strict validator／CLI | 関連 216 件成功。PR #34 の 3 修正を維持 |
| 新独立 validator／CI | 25 件成功。source、receipt 相関、pixel/ROI digest、厳密な型、欠落、範囲外、CI export の拒否を含む |
| census の strict JSON 往復 | 1 件成功。実 producer の出力 bytes と既存 parser／writer の整合を検証 |
| 実 SHM fixture | app／装飾 exact pixel、FrameSeq 1→2→3、idle/装飾時は 1 を維持、target/window retirement と owner 全停止を確認 |

これらの開発時 dirty-tree 実験は、clean source に束縛した最終 CI artifact と区別する。
最終 head、実 checkout、全テスト結果、CI run と artifact の検証結果は当該 PR に記録する。
ローカルでは `socketpair(AF_UNIX, SOCK_STREAM)` は使える一方、
named socket の `socket(AF_UNIX, SOCK_STREAM)` が EPERM になる。
既存 core feature suite は 153 PASS／6 FAIL／1 ignored（親 test から起動する既存 helper）で、
失敗は dependency probe の IPC_FAILED、Waypipe の LOCAL_SOCKET_FAILED／HELPER_EXEC_FAILED、
後続の fixture mutex poison だった。default build でも IPC_FAILED／HELPER_EXEC_FAILED を再現した。
成功や skip に変更せず、通常 Host／Android CI と専用 job の実環境で確認する。

## 7. 成功の意味と残件

固定 manifest は **28 family／87 required variant／533 assertion 参照**、
SHA-256 `e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33` のままである。
この単位の `renderer_t_passed=true` は `host_v3_acceptance_passed=false` と共存する。
N02 の `gtk3_desktop`／`mousepad_editor`、通常 Registry Launch、実 GTK3／Mousepad、合成 SDK、
B1 Ready、current input gate、N02 独立 product oracle は後続であり `not_run` を維持する。
実 SDK／Android 保存／Quest/H も未実施である。

context-loss の拒否検査は明示的な T seam である。実 Mesa context を強制喪失した結果や
実 SDK の callback drain として扱わない。
**N01 の environment_disposal 成功 receipt は別の残件で、引き続き `not_yet`。**
新しい renderer wrapper も削除を試行しただけでは環境破棄を complete にせず、
正式 I6 acceptance の判定へこの T artifact を自動加算しない。
