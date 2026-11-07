# テーマ・独立アニメーション・作業負荷の実装

2026-11-06。基底は`12bad86`。Phase 5の残る手動受入れを保持し、この追加機能をPhase 6の全耐久・release完了より先に実装する。
ユーザー操作が必要な確認は最後にまとめ、実装・host検証・可能なQuest検証を先に進める。

2026-11-07の追加要望: [次フェーズ計画](../next-phase-termux-startup-linux-gui-plan-20261107.md)の第5節に、
リング線幅3倍、外側CPU・中央RSS・内側Disk I/Oによる独立した速度制御を追記した。
NX01/06で線幅・外側CPU・中央RSSを実装した。内側Disk I/OはNX13へ残す。
速度・欠測・v1互換の契約は[追加実装記録](../extensions-nx01-nx08-20261107.md)を正とする。
共有foot本体はユーザー指定により計測対象から除外できるため、シェル/子processまたはtmux sessionを対象とする。
同計画の第6節には、テーマで配置と外観を指定する移動・閉じる・menu等の窓別操作を追記した。
初期デザインは大きめの丸いkawaii/sweet/popなボタン。右上は初期テーマの配置であり、共通実装へ固定しない。
第7節には最大九枚の初期配置と、GUI検証後のグループ化・500msの移動アニメーションを追加した。
リングの回転と窓全体の移動は別の時計で管理し、背面窓の入力・描画・資源上限も検証する。
第9節と[ランチャー計画](../application-launcher-plan-20261107.md)では、左下のアイコン列の配置・外観も次期テーマの対象とする。
実行file・引数は別のランチャー設定が所有し、テーマ変更で起動内容を変えない。
本書の現行実装・受入れ記録と、次期要望の成立を混同しない。

## 到達点と順序

| 段階 | 成果物 | 現在の状態 / 未完了条件 |
| --- | --- | --- |
| TH00 | GL任意角度・alpha、SDK透過・余白のray、canvas/content契約 | 共通座標変換とhost実GPU readbackを実装・検証。Build119でSDK余白ray/入力UV/回収を検証。SDK光学的透過は未確認 |
| TH01 | TOML/CSS compiler、SVG cache、独立時計、focus transition、busy pulse、CPU meter、reload | runtime command・ZIP import・設定UI・GPU採用前の世代再検査を接続。hostの取消/不正reload/復元を検証。Quest製品受入れは継続中 |
| TH02 | 静止foot背後の三重リング、二枚、resize/再生成/入力 | SDKの非入力表示面＋content専用入力meshを接続。旧Surface回収後の世代交換を実装。Build119で余白ray/入力UVを検証。Build123で装飾あり/なしの通常SDK二枚・入力・resize・Activity再生成・復元が成功。HMD透過・文字品質は未確認 |
| TH03 | TerminalId、PTY登録、collector、CPU/RSS | Build104でQuestの二footを別PTYへ対応付け、73秒・resize/表示切替・終了をnative scopeで検証。busy/欠測の見え方はHMD未確認 |
| TH04 | tmuxのsession/pane対応、共有scope、再接続 | 登録socket限定adapterと共通EMAを実装。host/Questの専用PTY二client・共有二pane・CPU負荷検証成功。APK window内のattach切替/HMDは残る |
| TH05 | import/設定/無効化、性能比較、製品受入れ | ZIP選択、builtin/import済み/無効化、動きを抑える設定と原子的保存を実装。Build111のnative四条件比較とBuild112の設定復元は成功。Build122の通常SDK四条件比較も成功。HMD/物理操作の受入れは未完了 |

TH00のSDK経路確認と独立して、TH01の純粋処理、TH03/04のcollectorを進めてよい。
いずれもデモやhost検査だけで製品受入れ済みにしない。既定ONはTH05の受入れ後とする。

## レビューで確認した制約

- Meta Spatial SDK 0.15.0の`MediaPanelRenderOptions.applyTo`は`enableTransparent=false`、`AlphaMode.OPAQUE`。
  現行VideoSurfaceを透明clearにするだけでは装飾余白が透けると保証できない。
  ReadableVideoSurface/SceneTexture経路と、必要なら公開APIのPanel登録を小probeで比較する。
  alphaとdepth順・文字品質・ray routing・Surface寿命を独立して測る。
- scene backfillの透明化で部屋が見えたBuild100の結果は、VideoSurface alphaの証明ではない。
- 透明な余白もSDK colliderが入力を遮り得る。Rustでhitを捨てただけでは背後panelに再配送されない。
  非入力背景geometry等を比較して、二枚を操作できる要件を維持する。
- Smithay 0.7.0の`GlesFrame::render_texture`はtexture matrixと頂点matrixを別に受け、内部でcurrent projectionを掛ける。
  UVを回して代用しない。任意角度の頂点変換、原点、上下方向を独立readbackで確認する。
- app画像のNearest filterは維持し、装飾passはLinearを検証する。filter状態を次のpassへ漏らさない。
- launcherはscript SHA一致を必須にする。新protocolを旧scriptに黙って送らない。
  capability unavailableで負荷表示を無効にすることと、任意の旧launcherを受け入れることは別である。
- Termuxのcommは`linker64`の場合がある。名前だけでshell/nvimを観測対象から外さない。
  PTY wrapperは既存の直接exec probeとLD_PRELOAD維持を引き継ぐ。

公式資料: [Meta panel rendering](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-2dpanel-layers/)、
[Smithay render_texture](https://docs.rs/smithay/0.7.0/smithay/backend/renderer/gles/struct.GlesFrame.html#method.render_texture)。
API確認は実機成立の証拠ではない。

## 実装したhost側の契約

`ContentViewport`はcanvas、app logical、target pixel、content原点を区別する。
1024×768 content、1280×1280 canvas、原点[128,256]なら、appの1.2×0.9mを維持したcanvasは1.5×1.5m。
theme無効時は従来のletterboxと同じ逆変換になる。余白はapp入力へ変換しない。
この変換を装飾付きrenderへ接続した。SDKの入力面はcontentだけを覆い、そのUVはcontentの0〜1に対応する。
Build117の比較に基づき、厚み0の自作meshを厚み1mmのSDK標準boxへ変更した。
color/depth書込みを無効にし、非描画の入力面をLineTest_IgnoreVisibleで判定する。
非表示windowは入力Entity自体を回収し、余分な当たり判定を残さないことを通常SDK検査で確認する。
透明表示面は`Panel(id, NoCollision)`で生成する。PanelとHittableの共有属性を生成順で上書きしない。
Build119の小probeで背後へのray到達が成立した。通常製品の二枚/lifecycle検査を別に行う。
通常UIに「装飾なし」「Cyan Orbit」「読み込んだテーマ」「テーマZIPを読み込む」「動きを抑える」を追加した。

`native/theme`の入口は`Bundle::new`→`raster::compile`→`Player::evaluate`。
loaderはimmutableな相対path/byte列を受け、Android storageやGLを直接操作しない。
reloadを含め、同じownerの`PixelBudget`と`RasterBudget`を共有する。画像参照が残る限り予算を返却しない。
`Player`は(SessionId, WindowId)ごとに所有し、Surfaceが再生成しても捨てない。
collector/workloadの対応変更時は`clear_metrics`で他作業のhold-last値を破棄する。

| 上限・書式 | 現在の実装 |
| --- | --- |
| TOML | format_version=1/2（rotationはv2）、未知field拒否、64KiB、16part、16binding |
| CSS | 256KiB、256rule、32clip、各64keyframe。`#part`と既知状態class、opacity/tint/value/transform、transition、単一animation |
| 補間 | transformは同じ関数列のみ。0→360度の巻き数を保持。標準easingのBézierはxを解いて評価する |
| animation | 1ms〜24h、finite count 1〜1,000,000またはinfinite、fill none。focus/metrics/同一定義reloadで再開始しない |
| import bundle | ZIPは8MiB+64KiB、directory込み128entry、file34個・展開後合計8MiB。相対path/重複/CRC/長さを検査し、entry名を保存先パスに使わない |
| SVG | 512KiB/素材、4096 XML node、深さ32。svg/g/path/circle/ellipse/rect/line/polyline/polygonの静的subset |
| SVG拒否 | DTD/entity、外部resource、image/text/font/style/script/animation/filter/mask、group opacity等の中間layer生成。色はprimitiveのfill/strokeとCSS tintで指定 |
| raster | 各辺2048px、装飾32MiBを旧/新themeの同時保持込みで制限。既存128MiB pixel予算にも計上 |
| alpha | resvg→共通budgetのallocationへ直接描画。premultiplied RGBA、余白RGBA=[0,0,0,0] |
| cache | 同一素材hash/寸法をcompile内で共有。GPU cacheはGL ownerのcontextに所有させる。context再生成で全textureを再生成 |
| lifecycle | visibilityの変更時だけ時計をpause/resume。無限clip終了/無効化後の最終frameを`changed`で伝える |
| reduced motion | base styleへ即時反映、transitionを止める。解除時はclipを新しく開始する |

size省略時のraster基準はbackdrop 1280×1280、frame 1024×768、titlebar-end 24×24、titlebar-bottom 1024×3。
windowごとのslotへGPUで配置し、毎frameの再rasterはしない。必要な解像度は素材のsizeで明示する。

SVG subsetを広げる場合は中間bufferの予算とresource resolverを先に検証する。
現段階のcompilerはSVGの全仕様互換を表明しない。
テーマ例は[native/theme/assets/cyan-orbit](../../native/theme/assets/cyan-orbit/theme.toml)。
素材は独自の円弧・目盛りで、外部画像の転載はない。

固定した直接依存はresvg 0.49.0（Apache-2.0 OR MIT、text/raster-images既定featureなし）、
cssparser 0.38.0（MPL-2.0）、toml 0.9.8、roxmltree 0.21.1（ともにMIT OR Apache-2.0）。
transitiveのusvg/tiny-skiaもCargo.lockへ固定した。`scripts/theme_licenses.py`はtheme/行列変換の依存閉包から、
Cargo.lockと一致する配布archiveのlicense原本とsource URL/hashをAPKへ収録する。
既存のnative dependency検査に加えて、APK内のテーマlicense改変/欠落も拒否する。
この一覧はテーマ関係の範囲であり、Phase 6の配布全体の受入れ完了ではない。
cssparser 0.38.0はParserが1 lifetimeでParserInputを使わないAPIだったため、実crate sourceへ合わせた。
古いAPI想定によるcompile失敗は作業ログに保存している。

## 描画・reloadの検証済み部分

- `Loader`はCPU worker一つ、実行中一件、待機最新一件、完成最新一件。新しい要求/無効化で世代を進める。
  古いcompileの結果を公開せず、不正bundleのcompile失敗でも呼出側の現在themeを保持する。
  `take_ready`後のGPU準備/adoption直前には`is_current`を再確認する。UIからworkerをjoinしない。
- Render ownerはwindowごとの時計を保持する。Surface不在・非表示では時計を止め、同じlogical windowの再生成/resizeで位相を維持する。
  themeのGPU準備が成功してから適用する。装飾だけの描画でFrameSeq/ContentSeqやWayland callbackを作らない。
- 動く装飾/transition、変化後の最終frame、新しいapp画像、target再生成を再描画条件にする。
  動きを抑える設定やtheme無効化後、静止画像のswapを停止する。ownerの定期wake自体は現時点で残る。
- 描画はLinearの装飾背景→Nearestのapp/popup→Linearの前景。透明外周、既存app内の黒背景、popupのclip/alphaを維持する。
  各passをfinish/waitする正しさ優先の実装。Questのnative四条件比較は末尾の測定表を参照する。
- CPU画像はruntimeの共通PixelBudgetをsessionとtheme loaderで共有するための経路を追加した。
  GPUは旧/新装飾32MiBに加え、app textureと合わせた128MiBを検査する。
- `cargo run --locked -p eidolon-render --example theme-gl-probe`でhostの実EGL/GLESを使う。
  RTX 3060/NVIDIA OpenGL ES 3.2上で、45度の方向/中心、透過余白、半透明SVG/tintを15点、
  3pass合成・Nearest境界・popup clip・前景を21点読み戻した。再描画時の再uploadなし、無効化時の復元と解放も確認。
  hostbackendのthread testと、この実GPU検証は別であり、いずれもQuest SDK/HMD確認の代替ではない。

## SDK Surfaceの部分検証

Build103にdebug専用`theme-surface` caseを追加した。`PanelSceneObject`の公開constructorを使い、
`forceSceneTexture=true`、`PREMULTIPLIED`、meshまたは明示的なpremultiplied blendのlayerを生成する。
独立EGL threadで固定色だけを描き、producer側のRGBAと資源回収を検査する。
`Scene.lineSegmentIntersect`で中央/透明余白のhitを記録し、`Hittable(NoCollision)`後に背後のpanelへ到達するかを調べる。
NoCollisionはpanel全体を非入力にするため、これだけでcontent入力を解決したとは扱わない。

```bash
python3 -B scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case theme-surface
python3 -B scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case theme-sdk --timeout 240
```

このcaseのpassはproducer/SDK設定/rayの**部分検証**。`sdk_alpha_verified=false`、`hmd_verified=false`を必須にし、
SDK合成後の透明な見え方を確認済みとしない。部屋画像・端末本文は収集しない。
`SceneTexture.readAverageColor`はRGBの読み取りなのでalphaの合格判定には使わない。
[Meta SceneTexture API](https://developers.meta.com/vr/reference/spatial-sdk/v0.13.0/com_meta_spatial_runtime_scenetexture/)、
[Meta AlphaMode API](https://developers.meta.com/vr/reference/spatial-sdk/v0.13.1/com_meta_spatial_runtime_alphamode/)と実0.15.0 AARの署名/実装を照合した。
`setSceneMesh`のStringはentity名でありcollision設定ではないことも、
[Meta SceneObject API](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_sceneobject/)で確認した。

Build103のdebug/release assemble・lint・APK境界検査、PC64件は成功。Questへ導入後、
caseは`ready`段階でtimeoutした。Activity非表示・XR IDLEのためentity作成前であり、alpha/rayの合否は未判定。
最初のKotlin compileでnullableな`panelShapeConfig`の扱いを修正し、再compileした。
試行錯誤の詳細と正確なAPK/source hashはprivate作業ログに保存している。

## 次の統合と受入れ

1. 接続済みSDK透過Surfaceとcontent入力meshを実rayで検査し、二枚の余白・popup・文字品質を確認する。
2. `theme-runtime --instrumentation`で実APKのcompile/upload・失敗時維持・無効化・保存復元を検査し、製品のSDK検査へ進む。
3. 検証済みのcollector/tmuxを通常UIのテーマと組み合わせ、APK window内のattach切替・負荷表示・欠測を検証する。
4. import/設定/切り戻し、二枚・popup・resize・保存・接続/終了の回帰、性能比較を終え、HMD確認を一度に依頼する。

hostの再現入口は`python3 -B scripts/check-host --suite theme`、`--suite render`、実GPUの上記example。
全suiteにもtheme crateを登録した。hostの「三重リング」は角度/再生状態と実SVG rasterの検証を含むが、
Questにリングを表示したという意味ではない。

## テーマ読み込み・保存・SDK入力の接続

AndroidのStorage Access FrameworkでZIPを選択し、application scopeのworkerが読み込む。
実行中一件・待機最新一件、30秒の取消期限、圧縮/展開サイズ上限を設ける。
ZIPのentryはメモリ内の限定形式`EIDTHEME\x01`へ変換する。保存先は生成した32桁tokenだけで決め、
`noBackupFilesDir/themes/<token>.bundle`をmode0600で作成する。native側でもowner/regular file/FD identity/サイズを再検査する。
新しいimport開始・無効化はCPU loaderとGPU候補の両方を失効させる。失敗時は最後に適用したthemeを保つ。

GPU uploadは旧cacheを保持したままstageし、短いlockで最新revisionか確かめてから採用する。
GL呼び出し中にUI/coreの共有lockを保持しない。GPU側の採用receiptと新要求の取消を同じlockで順序付け、
適用済みsourceだけを設定保存へ送る。設定と最後のimportを一つのversioned recordにまとめ、
専用workerがmode0600のtemporary→fsync→rename→directory fsyncで保存する。既定は無効。
無効化してもimport元を保持し、再起動後に再選択できる。破損recordは無効へ戻し、明示変更で保存を修復できる。

テーマ有効時のSDK表示は公開`PanelSceneObject`、PREMULTIPLIED/SceneTexture/NoCollisionを使う。
入力は別のcontent quadを`SceneMesh.meshWithMaterials(..., createBVH=true)`で作る。
入力meshはColorWrite=0、DepthWrite=DISABLE、DepthTest=NEVERで色/depthへ書かず、LineTestで交差判定する。
表示面を入力meshの子Entityとし、contentのm/px・中心位置を保つscale/offsetを設定する。
layout変更は旧targetのDestroy/SurfaceDetached後に新generationを作り、旧入力を拒否する。
`producer_size_px`は実GPU canvasの寸法であり、appの`logical_size`やresize configureを置換しない。
2048px上限ではcanvasを2048に制限し、入り切らない外周装飾はclipする。

APIは同梱0.15.0 AARでも確認した。[SceneMesh公式API](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_scenemesh/)、
[SceneMaterial公式API](https://developers.meta.com/vr/reference/spatial-sdk/v0.14.0/com_meta_spatial_runtime_scenematerial/)。
この構成のSDK alpha・depth順・rayの実動作はhostコンパイルだけでは確認できない。

再現入口:

```sh
cargo run --locked -p eidolon-render --example theme-gl-probe
python3 -B scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case theme-runtime --instrumentation
python3 -B scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case theme-surface
```

`theme-runtime`は専用private設定領域でbuiltin→ZIP import→不正reload→reduced motion→無効化→再生成/復元→Stopを検査する。
`theme-surface`は非入力表示面の背後到達に加え、製品と同じ入力meshの中心/余白/UV/回収を検査する。
どちらもHMDの見え方や物理入力を自動合格にしない。ZIPは直下に`theme.toml`、そこから参照するCSS/SVGを置く。

## TerminalId・CPU/RSS・tmuxの接続

Rustがsession内で再利用しないTerminalIdを払い出す。`start_terminal_v1`/`open_terminal_v1`はschemaとSessionIdを持ち、
既存`terminal_opened`のschema1にはfieldを追加しない。APK内のlauncher SHA照合を維持するので同じsourceのscriptを配布する。
footclientのapp-idは`io.github.tinmachine.eidolon.foot.t-<nonce>-<TerminalId>`。
client申告値だけを信頼せず、起動receiptと同sessionのPTY登録が揃って一意の場合に負荷を対応付ける。
同じapp-idの二windowはambiguous、登録identityの変更は拒否、別sessionのtokenは結び付けない。
layout保存用は起動receiptを確認したbase app-idへ分離し、再接続ごとに変わるnonceを保存キーにしない。

実PTY内の固定wrapperが自分のPID/starttime/TTY/boot IDを送信してからshell/fixtureへexecする。
private UNIX datagramの`SCM_CREDENTIALS`と同UID、現在のproc identity、TTYのdevice numberを照合する。
未発行ID・再使用ID・重複登録を受け入れず、未登録の待機枠は6秒で返す。登録不可でもshell起動自体は継続する。
global profileは変更せず、既存の直接exec probeとLD_PRELOADを保持する。

collectorは一つのworkerで目標1Hz。`/proc`列挙は4096entry、同UIDのstatは512件、走査50msを上限とする。
他UIDのstat、cmdline、environ、terminal本文、tmuxの名前/titleは読まない。
CPUは各PID/starttimeのutime+stime差分をCLK_TCKとcollector内の単調時計deltaで割る。
新PID・PID再利用・boot/epoch変更・counter逆行・欠測・3秒超の間隔はbaselineを取り直す。
RSSはpage sizeを掛けた近似合計で、共有pageの重複計上を含む。短命process、reparent、SSH先の仕事は網羅しない。
不完全な走査を0 CPUにせず、coverageと理由を送る。観測PIDは既存の終了/cleanup権限へ追加しない。

stdoutは非blockingでcontrolを優先する。計測の待機は最新一件、書き始めたJSON lineは最後まで維持する。
各lineは16KiB、計測は14,000byte、control queueは64KiB以内。読まれなくてもlease/停止処理を進める。
Rustもsession/nonce/UID/epoch/sequence・未知field・個数/数値を検査し、最後のsampleだけを保持する。

tmuxはTermux既定のprivate socketを明示登録し、他socketは勝手に列挙しない。追加登録は対象terminal内で次を実行する。

```bash
python3 "$HOME/.local/lib/eidolon/eidolon-session.py" register-tmux \
  "$EIDOLON_SESSION_NONCE" /absolute/private/socket
```

登録上限は4 socket。parent directoryは同UIDの0700、socketは同UIDかつworld非公開に限る。
固定formatでclient PID/TTY→session ID→pane PIDへ辿る。各照会を150ms/32KiBで打ち切り、client一覧の再照会と
server/socket/process identityの再確認でattach切替の競合を検出する。確定できないネストや競合はpartialへ落とす。
未登録socketのtmuxや遠隔SSH先をshell-treeから自動推定しない。表示scopeを`tmux_session`/`shell_tree`で区別する。
同sessionを二端末で開いた場合はpane processを重複排除し、同じworkloadのEMAを共有する。

EMAの時定数は1秒、busy on >0.15 core / off <0.08 core。値は初期評価値である。
欠測・切断・3秒超の古いsampleはbusyを解除し、同じworkloadの表示値だけを保持する。
別workload/epochへの変更はmeterのhold-lastを消し、windowのアニメーション時計は維持する。
CPU低下をjob完了と解釈せず、pressureも自動付与しない。

host全suiteはRust139件・PC57件（実APK用7件は別）・launcher32件成功。
実PTY登録、JSON送信詰まり、schema1との分離、session/nonce/UID/epoch/sequence不一致、欠測、PID再利用を含む。
host tmux 3.6では二clientが同sessionの二paneを共有し、同じ二processだけへ集計することを実際のsocketで確認した。
QuestのSSH contextでは同UIDの12プロセスのstat読み取り、footclientのapp-id指定、tmux 3.8を確認した。

Build104はdebug/release assemble・lint・APK検査、実APKを使うPC64件が成功。同版のAPK/launcher/producerを配布した。
`quest-check --case two-windows --instrumentation`で二つの実footを別TerminalId/PID/TTYへ対応付け、
73秒の実行中にCPU/RSSがfreshのまま届くことをsnapshotで確認した。七段階の独立表示・resize・三枚目への切替・close・Stopも成功。
これはImageReaderと合成入力/visibilityを使うnative scopeで、SDK layer/HMD/物理入力の合格ではない。
終了時にdirectory回収・pending cleanupゼロを確認し、以前の接続の残件もreceipt照合でverifiedとなった。

別のQuest専用PTY probeでは配備済みlauncher SHAを照合してから二PTYを登録し、tmux 3.8の同sessionへ接続した。
二paneを二processとして重複なく集計し、固定の5秒CPU処理中に両terminalへ同じ約0.95 coreを報告した。
このsampleの走査は約7.3ms、間隔約0.99秒。性能保証や全負荷での上限達成ではない。
observerのみの終了後もclient/serverが生存し、最後にprobe所有fixtureだけを回収した。
APK window内のtmux attach/detach切替、通常テーマでのbusy/meter表示、HMD確認は別の残件である。

参考: [proc_pid_stat](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)、
[tmux公式Formats](https://github.com/tmux/tmux/wiki/Formats)。

## 観測の停止とnative性能比較

通常UIの「負荷の計測を停止/再開」はworkspace設定として保存する。古い設定fileでは有効を既定とする。
`set_workload_observation`はboolだけを受け、認証済みの同じsessionへ`observe_workload_v1`を送る。
停止中はcollectorの/proc全走査とtmux照会を止め、登録socketを保持してunavailableを報告する。
同じcollector/SSH streamのepochは維持し、再開でCPU差分の基準を更新する。停止前の計測結果は新しい設定世代へ公開しない。
観測の停止・再開はterminal、shell、tmux server/paneへのsignal送信権限を持たない。

`theme-windows --instrumentation`は実foot二枚の装飾・片側Surface喪失・resize・第三window切替・入力解除・個別closeを検査する。
固定出力の終了後、観測を止めて装飾なし/静止リング/三重回転を各6秒、再開後に三重回転＋実metricsを6秒測る。
resizeではclientの新logical寸法を確定してから新targetを接続する。これはSDK adapterの旧世代拒否を省略するものではない。
測定中はImageReaderを排出し続け、画像hashの計算を止める。製品settingsは使わず、この検査のruntimeだけで切り替える。
native instrumentation caseでは、debug manifestのWAKE_LOCKでCPU休止を最大5分防ぎ、finallyで解放する。
獲得・終了直前の保持・解放を証明へ記録する。XR focusや装着検知を偽装するものではない。
通常のsleep/復帰はこのlockを持たない製品経路で別に測る。releaseに権限が残らないこともAPK検査する。
Build111では性能caseだけに適用したが、続く保存/復元caseでもADB採取が途絶えたため、Build112からnative case全体へ揃えた。
Activityを起動する通常SDKの検査や、実際のsleep/復帰へは適用しない。

各targetの`frame_timing`は成功したdraw+swapの累積件数/時間と32binのhistogram。
1ms刻みでp50/p95/p99の上限を報告し、31msを超えるbinは`over_31`、描画0回は未定義とする。
GPU待ちを含むwall timeであり、GPU timestamp queryでもHMD present latencyでもない。
区間CPUはAPK process全体で、consumer/probeの残る負荷を含む。
装飾なしは通常canvas、静止/回転は同じ拡張canvasを使うため、最初の差にはcanvas拡大分も含まれる。
HMD/通常SDKの性能予算はこの測定だけで合格にしない。

Build111でCPU wake lockを使う同じcaseが成功した。4区間はすべて約6秒、計測結果は以下。
CPUはAPK processの使用時間/実経過時間で、1.0が1 core相当。native heapはRSSではない。

| 条件（二窓） | APK CPU（core相当） | 各窓の新規draw/s | draw+swap p95上限 | native heap |
| --- | --- | --- | --- | --- |
| 装飾なし・計測停止 | 0.151 | 0 | 未定義 | 19.2 MB |
| 静止リング・計測停止 | 0.182 | 0 | 未定義 | 34.1 MB |
| 回転リング・計測停止 | 0.467 | 42.3 | 4 ms | 34.1 MB |
| 回転リング・計測有効 | 0.476 | 42.5 | 4 ms | 34.1 MB |

全区間でapp contentのsequenceは不変。区間内の装飾uploadも増えず、静止条件は再描画しなかった。
計測再開後に二窓のfreshなCPU/RSS対応が復帰し、合成adapter失敗から同じsessionの通常二窓へ戻った。
Stop193ms、全owner回収、wake lock解放を確認。単一runのnative比較であり、実SDKの表示頻度、
HMD快適性、温度の定常状態、一般的な性能上限はこの表から保証しない。

```sh
python3 -B scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case theme-windows --instrumentation --timeout 210
git archive --format=zip -o "$EIDOLON_THEME_ZIP" HEAD:native/theme/assets/cyan-orbit
adb -s "$EIDOLON_QUEST_SERIAL" push "$EIDOLON_THEME_ZIP" /sdcard/Download/eidolon-cyan-orbit.zip
```

ZIPのコピー後、アプリの「テーマZIPを読み込む」からDownloadのfileを選択する。コピーだけでは選択themeは変わらない。

装飾panelの作成失敗は、最新のtarget/command_seqに一致するAdapterFailedだけを処理する。
装飾なしへ戻し、旧targetの回収後に通常panelを作り直す。適用済みimport元と接続は維持する。
未知の全体描画障害をテーマ起因と断定しない。合成AdapterFailedのnative検査と、実SDK失敗の発生記録は区別する。

## 通常SDKの四条件比較と復元（Build122）

`theme-sdk --timeout 240`は通常SDK上で二つの実footを表示し、合成ray/keyで入力する。
最初の一枚を800×500へresizeし、二枚の設定が確定した後で装飾なし・静止・回転・回転＋計測を各6秒測る。
静止は最終画像を一回描ければ準備完了とし、追加frameの発生を待って停止最適化を妨げない。
実FOCUSED、同scene/session、期間中のapp画像不変と固定素材のupload不増加を照合する。

| 条件 | APK process CPU (core) | process RSS (MiB) | 各targetの描画回/秒 | mean (ms、二枚) | p95上限 (ms、二枚) |
| --- | --- | --- | --- | --- | --- |
| 装飾なし・観測OFF | 1.144 | 333.2 | 0.00 | 未定義 | 未定義 |
| 静止リング・観測OFF | 1.144 | 348.6 | 0.00 | 未定義 | 未定義 |
| 三重回転・観測OFF | 1.285 | 351.1 | 49.48 | 1.320 / 1.622 | 3 / 3 |
| 三重回転・観測ON | 1.319 | 347.9 | 49.48 | 1.323 / 1.600 | 2 / 3 |

CPUとRSSはAPK process全体でSDK・診断処理を含む。RSSは/proc/self/statusのVmRSSで、native heapとは別に記録する。
これはdraw+swapのwall timeであり、GPU実行時間・HMD表示頻度・表示遅延ではない。各区間約6秒の一回の比較で、
温度変化を含む長時間の余裕や装着時の快適性は未確認。計測中のアプリ本文やカメラ画像は取得しない。

測定後も片側Surface喪失・残った側への入力・復旧・第三windowとの切替・押下解除・close・実Activity再生成・
再接続とテーマ/配置復元を通し、5＋10段階が成功した。検査前後の製品設定hashは不変で、通常UIへ復帰した。
Build121は四区間の測定が成立した後、合成clickの準備待ちが足りず後段で停止した。
Build122ではNative ReadyだけでなくHostの同runtime/input epochのticket公開を待ち、
測定前に合成pointerのhoverを解除・回収した。検査の成功を物理コントローラーの確認へ読み替えない。

Build123では四条件比較を含む装飾ありの再検査と、装飾なしの通常SDK二枚も5＋10段階で成功した。
plainの静止panelは最新contentを一回presentすれば準備完了とし、連続描画を要求しない。
他windowのclose後の継続性は、残ったwindowへSDK ray/keyを送り、focus・キー到達・frame増分で確認する。
