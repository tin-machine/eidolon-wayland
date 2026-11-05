# テーマ・独立アニメーション・作業負荷の実装

2026-11-06。基底は`12bad86`。Phase 5の残る手動受入れを保持し、この追加機能をPhase 6の全耐久・release完了より先に実装する。
ユーザー操作が必要な確認は最後にまとめ、実装・host検証・可能なQuest検証を先に進める。

## 到達点と順序

| 段階 | 成果物 | 現在の状態 / 未完了条件 |
| --- | --- | --- |
| TH00 | GL任意角度・alpha、SDK透過・余白のray、canvas/content契約 | 共通座標変換とhost実GPU readbackを実装・検証。SDK透過/余白rayは未完了 |
| TH01 | TOML/CSS compiler、SVG cache、独立時計、focus transition、busy pulse、CPU meter、reload | compiler・再生状態・builtin三重リング・世代付き単一workerを実装。Render ownerへ接続するAPIを追加。runtime command/importへの接続は未完了 |
| TH02 | 静止foot背後の三重リング、二枚、resize/再生成/入力 | Render ownerの二窓独立tick、resize/Surface再生成時の時計、3pass合成をhost検証。SDK透過・余白ray・入力/物理寸法の製品接続は未完了 |
| TH03 | TerminalId、PTY登録、collector、CPU/RSS | 未実装。共有foot serverの二端末を混同せず、欠測・PID再利用・boot変更を区別すること |
| TH04 | tmuxのsession/pane対応、共有scope、再接続 | 未実装。登録したsocketのみ観測し、観測PIDをcleanup対象へ追加しないこと |
| TH05 | import/設定/無効化、性能比較、製品受入れ | 未実装。ユーザーがコード変更なしでtheme交換でき、自動検査とHMD/物理操作が成立すること |

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
この変換を装飾付きrenderへ接続した。input/SDKへの接続と実際のray click-throughは未確認。
`Controller::set_theme`はnative統合用で、通常UIからのテーマ有効化はまだ提供していない。

`native/theme`の入口は`Bundle::new`→`raster::compile`→`Player::evaluate`。
loaderはimmutableな相対path/byte列を受け、Android storageやGLを直接操作しない。
reloadを含め、同じownerの`PixelBudget`と`RasterBudget`を共有する。画像参照が残る限り予算を返却しない。
`Player`は(SessionId, WindowId)ごとに所有し、Surfaceが再生成しても捨てない。
collector/workloadの対応変更時は`clear_metrics`で他作業のhold-last値を破棄する。

| 上限・書式 | 現在の実装 |
| --- | --- |
| TOML | format_version=1、未知field拒否、64KiB、16part、16binding |
| CSS | 256KiB、256rule、32clip、各64keyframe。`#part`と既知状態class、opacity/tint/value/transform、transition、単一animation |
| 補間 | transformは同じ関数列のみ。0→360度の巻き数を保持。標準easingのBézierはxを解いて評価する |
| animation | 1ms〜24h、finite count 1〜1,000,000またはinfinite、fill none。focus/metrics/同一定義reloadで再開始しない |
| import bundle | 相対path、重複拒否、34file、合計8MiB。archive展開・Android importは今後接続する |
| SVG | 512KiB/素材、4096 XML node、深さ32。svg/g/path/circle/ellipse/rect/line/polyline/polygonの静的subset |
| SVG拒否 | DTD/entity、外部resource、image/text/font/style/script/animation/filter/mask、group opacity等の中間layer生成。色はprimitiveのfill/strokeとCSS tintで指定 |
| raster | 各辺2048px、装飾32MiBを旧/新themeの同時保持込みで制限。既存128MiB pixel予算にも計上 |
| alpha | resvg→共通budgetのallocationへ直接描画。premultiplied RGBA、余白RGBA=[0,0,0,0] |
| cache | 同一素材hash/寸法をcompile内で共有。GPU cacheはGL ownerのcontextに所有させる。context再生成で全textureを再生成 |
| lifecycle | visibilityの変更時だけ時計をpause/resume。無限clip終了/無効化後の最終frameを`changed`で伝える |
| reduced motion | base styleへ即時反映、transitionを止める。解除時はclipを新しく開始する |

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
  各passをfinish/waitする正しさ優先の実装で、Quest性能は未測定。
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
```

このcaseのpassはproducer/SDK設定/rayの**部分検証**。`sdk_alpha_verified=false`、`hmd_verified=false`を必須にし、
SDK合成後の透明な見え方を確認済みとしない。部屋画像・端末本文は収集しない。
`SceneTexture.readAverageColor`はRGBの読み取りなのでalphaの合格判定には使わない。
[Meta SceneTexture API](https://developers.meta.com/vr/reference/spatial-sdk/v0.13.0/com_meta_spatial_runtime_scenetexture/)、
[Meta AlphaMode API](https://developers.meta.com/vr/reference/spatial-sdk/v0.13.1/com_meta_spatial_runtime_alphamode/)と実0.15.0 AARの署名/実装を照合した。
`setSceneMesh`のStringはentity名でありcollision設定ではないことも、
[Meta SceneObject API](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_sceneobject/)で確認した。

Build103のdebug/release assemble・lint・APK境界検査、PC64件は成功。Questは未実行。
最初のKotlin compileでnullableな`panelShapeConfig`の扱いを修正し、再compileした。
試行錯誤の詳細と正確なAPK/source hashはprivate作業ログに保存している。

## 次の統合と受入れ

1. SDK透過Surfaceと二枚の余白rayをprobeし、入力/物理寸法/Surface世代の契約を製品へ接続する。
2. `Loader`とRender ownerのAPIをruntime command/importへ接続し、GPU準備中に要求が更新される競合も検査する。
   不正themeでは旧themeを維持し、旧canvas世代の入力を拒否する。
3. TerminalId/PTy登録とcollectorを実装し、1HzのCPU/RSS・coverage・stale、tmux adapterを接続する。
4. import/設定/切り戻し、二枚・popup・resize・保存・接続/終了の回帰、性能比較を終え、HMD確認を一度に依頼する。

hostの再現入口は`python3 -B scripts/check-host --suite theme`、`--suite render`、実GPUの上記example。
全suiteにもtheme crateを登録した。hostの「三重リング」は角度/再生状態と実SVG rasterの検証を含むが、
Questにリングを表示したという意味ではない。
