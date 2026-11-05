# テーマ・独立アニメーション・作業負荷の実装

2026-11-06。基底は`12bad86`。Phase 5の残る手動受入れを保持し、この追加機能をPhase 6の全耐久・release完了より先に実装する。
ユーザー操作が必要な確認は最後にまとめ、実装・host検証・可能なQuest検証を先に進める。

## 到達点と順序

| 段階 | 成果物 | 現在の状態 / 未完了条件 |
| --- | --- | --- |
| TH00 | GL任意角度・alpha、SDK透過・余白のray、canvas/content契約 | 共通座標変換を実装。GL readbackとSDK経路選定は未完了 |
| TH01 | TOML/CSS compiler、SVG cache、独立時計、focus transition、busy pulse、CPU meter、reload | `native/theme`でcompiler・再生状態・builtin三重リングを実装。製品との接続、世代付き非同期reloadは未完了 |
| TH02 | 静止foot背後の三重リング、二枚、resize/再生成/入力 | 未実装。12秒時計回り・18秒反時計回り・8秒時計回り、透明余白越しに背後panelを操作できること |
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
この変換はまだ製品render/inputへ接続していないため、実際のray click-throughは未確認。

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
| cache | 同一素材hash/寸法をcompile内で共有。GPU cacheはcontext generationを加えて後続実装 |
| lifecycle | visibilityの変更時だけ時計をpause/resume。無限clip終了/無効化後の最終frameを`changed`で伝える |
| reduced motion | base styleへ即時反映、transitionを止める。解除時はclipを新しく開始する |

SVG subsetを広げる場合は中間bufferの予算とresource resolverを先に検証する。
現段階のcompilerはSVGの全仕様互換を表明しない。
テーマ例は[native/theme/assets/cyan-orbit](../../native/theme/assets/cyan-orbit/theme.toml)。
素材は独自の円弧・目盛りで、外部画像の転載はない。

固定した直接依存はresvg 0.49.0（Apache-2.0 OR MIT、text/raster-images既定featureなし）、
cssparser 0.38.0（MPL-2.0）、toml 0.9.8、roxmltree 0.21.1（ともにMIT OR Apache-2.0）。
transitiveのusvg/tiny-skiaもCargo.lockへ固定した。APKへの組込み時にlicense/依存一覧とrelease境界を更新する。
cssparser 0.38.0はParserが1 lifetimeでParserInputを使わないAPIだったため、実crate sourceへ合わせた。
古いAPI想定によるcompile失敗は作業ログに保存している。

## 次の統合と受入れ

1. 45度のGL描画とalphaの小fixture、SDK透過Surfaceと二枚の余白rayを実装する。
2. compiler workerを一つ、pending要求をlatest一つに限定し、ThemeLoadGenerationで古い結果を破棄する。
   GPU upload成功後だけテーマを交換し、不正themeや準備失敗なら直前のthemeを維持する。
3. Render ownerへ装飾snapshotと時計を接続する。装飾tickでFrameSeq/ContentSeqやWayland frame callbackを捏造しない。
   CPU raster・GPU upload・decor present・app content frameを別counterにする。
4. TerminalId/PTy登録とcollectorを実装し、1HzのCPU/RSS・coverage・stale、tmux adapterを接続する。
5. import/設定/切り戻し、二枚・popup・resize・保存・接続/終了の回帰、性能比較を終え、HMD確認を一度に依頼する。

hostの再現入口は`python3 -B scripts/check-host --suite theme`。全suiteにもtheme crateを登録した。
hostの「三重リング」は角度/再生状態と実SVG rasterの検証であり、Questにリングを表示したという意味ではない。
