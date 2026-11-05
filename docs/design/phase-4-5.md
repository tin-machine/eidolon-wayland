# Phase 4・5 — 描画、入力、空間window

描画PoCはD02、単一windowの操作はI04、複数window等はI05〜I07で判定する。独立した成功条件を一度にまとめて実装しない。

<a id="d01"></a>
## D01 — GL ownerへSmithay rendererを導入

R03/W03の成功が前提。`native/render/src/compositor.rs`にAndroid EGL adapterとSmithay GlesRendererを導入する。Smithay featuresは`backend_egl`/`renderer_gl`を必要分だけ増やす。

入力は[所有済みWindowSnapshot](phase-3.md#w03)。`ImportMemWl`でprotocol objectをGL threadへ渡す方式は採用しない。0.7.0の`ImportMem::import_memory(data, Fourcc, size, flipped)`と`update_memory`を使い、GL objectはGL ownerだけに保持する。

1. R02のEGL/NativeWindow管理をadapterの基礎にし、Androidで公開されたEGL/GLES名だけを使う。
2. 一つのcontextでtargetを順にbindする。context stateはGlesRendererへ所有させ、独立したraw GL描画を混在させない。D01実装では診断patternもSmithayのdraw_solidへ移した。readbackだけはframeのwith_context内でGL stateを変更せずに行う。
3. `mem_formats()`を確認し、共通のpacked RGBA8に対応するFourccを選ぶ。byte order/上下向き/alphaを色パターンとreadbackで検査する。format非対応を黙って別formatとして描かない。
4. texture cacheは`(session,surface,content_seq)`。同じ画像を何度もuploadせず、サイズ変更時は作り直す。surface destroy/context lossで解放する。
5. backgroundは不透明な黒、surface treeを順に合成し、popupは親clip内。初期は全体damageでよい。

gate: host画像変換test、Questで既知snapshot→四隅/文字代替pattern→resize/再生成。GlesRendererとAndroid EGLの適合が失敗した場合は原因とpatch範囲を記録し、別rendererへの変更をD01の設計差分としてレビューする。無検証のfallbackを完成扱いにしない。

[D01の実装・検証記録](../owned-image-render-checks.md)では、Android公開名libEGL.soへの固定patchと
外部EGLContext/NativeWindow adapterを使う。公式minimal exampleと同じFlipped180をwindow framebufferの出力に適用し、
ImageReader上の実GPUでtransform/scale/alpha/clipとSurface再生成を検査した。
fixture自体はsynthetic visibilityによるnative scopeである。
通常SDKパネルへの接続・停止後UI復帰は[D02のBuild63検証](../foot-render-checks.md)で成功し、HMD視認は最終段階に残す。

<a id="d02"></a>
## D02 — foot一枚の表示PoC

D01/W05を接続する。初期1024×768、1.2×0.9 m、正面距離1.5 m、高さ1.4 m。poseは将来のmodel値を初期値に使い、SDKの座標方向はR01結果へ合わせる。

map時にCreatePanel、Surface attach成功時に最新snapshotを即描画する。新commitを待たない。unmap時は非表示・入力解除、destroy時はdetach ack後にEntityを回収する。後から来るframeは世代照合で捨てる。

既知の文字列を出すfixture shellでfoot表示、カーソルblink、連続出力、scroll、resize前後、現実背景との同時表示を確認する。この時点の出力操作はremote fixtureでよく、物理キー成功とは区別する。

結果: protocol frame seq→snapshot seq→upload/present seq、window/session/surface世代、HMD視認、capture可否。スクリーンショットだけで読める/快適と判定しない。

<a id="i01"></a>
## I01 — SDK rayと座標のadapter

作成: `InputAdapter.kt`、`native/model/input.rs`。R01で確認した`panelSetup`→`SceneObject.addInputListener`を使う。ButtonBitsをmagic numberで書かずSDK定数から変換する。

四隅・中央のhitを採取し、UV原点、軸方向、button change/state、stopInput、listener threadを確認する。入力本文や個人の操作を恒常記録せず、fixture座標とsymbolic buttonだけを限定採取する。

coreの変換順:

```text
SDK UV → 左上原点の正規化UV → panel pixel座標
  → letterbox外を除外 → window logical座標
  → window geometry/popup offset → 対象surface logical座標
```

同じ変換parameterをrendererとhit testが使う。UV=1の境界、負値、NaN、scale 2、全buffer transform、letterbox、popup clipをhost testにする。範囲外hitを端へclampして別windowへ誤クリックしない。対象surface決定はcoreの重なり順に従う。

Build66で共通Viewport/model変換と実SDK quadの5点校正が成功した。
SDK 0.15.0のUVは左上原点で、合成入力のchanged/state/stopも確認した。
物理入力とWayland配送の未確認を分け、[試行・再現記録](../ray-coordinate-checks.md)へ残す。

<a id="i02"></a>
## I02 — 物理キーボード

Android `dispatchKeyEvent`からtype/device/scanCode/keyCode/repeat/metaを取り、焦点を持つ製品windowへだけ送る。診断Composeの操作中に二重配送しない。Androidが予約するsystem keyは奪わない。

基準はevdev keycode。scanCodeが検証済みevdev範囲なら使用し、0/未知ならAndroid keyCode→evdevの明示tableへfallbackする。Android keyCodeやUnicodeをそのままWayland keyにしない。evdev→XKBの+8はxkbcommonにstateを渡す箇所で一回だけ行い、Wayland wire keyへ二重加算しない。

US/JIS compiled keymapをW01のassetから読み、keymap/enter/modifiers/repeat_infoをSmithay seat経由で通知する。物理押下/離上を追跡し、重複downと未知upを処理する。repeatはWayland clientへrate/delayを通知し、Androidのrepeat downを二重配送しない。

fixture: A/1/Space/Enter/Backspace/Esc/Tab/矢印/F1〜F12/Ctrl/Alt/Shift/Super、US/JISの記号。QuestではBluetooth keyboardでfoot→shell/tmux/Neovimの操作を確認する。ADB input text成功は代用にしない。

日本語composition/IME、Android software keyboard→Wayland text-inputは後続。初期MVPは英数字と修飾キーを明示する。

<a id="i03"></a>
## I03 — pointer、scroll、focus loss

coreがpointer focusとkeyboard focusを別々に持つ。hoverでpointer enter/motion/leave、primary pressで対象windowへkeyboard focusを移す。BTN_LEFT/RIGHT/MIDDLEの変換はtable化し、押下数を追跡する。

press時のsurfaceをcaptureし、releaseまで同じgrab対象へ送る。pointerが外へ出てもreleaseを失わない。scrollはSDKで取得できる実入力からaxis/source/discrete/stop/frameへ変換し、SDKが値を供給しない操作を実装済みにしない。

XR focus喪失、Activity pause、window destroy、session disconnect、queue過負荷では`reset_input`を通常queue外で届ける。送信済みkey/buttonのrelease、modifier reset、repeat停止、focus leaveを一度だけ実行する。復帰後に古いdownを再生せず、次の物理操作から始める。

host: press→focus loss→late release、pointer capture中destroy、menu開閉、二重reset。Quest: コントローラーclick/drag/scrollとMenu中の修飾キー押下解除。内部command注入と物理入力は別結果にする。

<a id="i04"></a>
## I04 — 論理resizeと操作MVP

modelに`requested_logical_size`、`configured_serial`、`committed_size`、`physical_size_m`、`producer_size_px`を別々に持つ。

1. UI resize要求を320×200〜2048×2048へ検査する。
2. 次のconfigureを発行し、ack/対応buffer commitを待つ。5秒未応答は診断を表示し、旧画像を継続する。clientを「resize成功」としない。
3. 新しい画像比率でsnapshot/letterbox/hit transformを同時更新する。
4. producer解像度変更が必要ならGL ownerへ送り、世代/実測EGL寸法を更新する。meterだけの拡大ではWayland configureを送らない。

連続要求は最新へcoalesceするが、既に送ったconfigure serialの正当性は保持する。古いackと新commitの取り違え、32bit serialのwrap、resize中のSurface lossをhost testにする。

MVP判定: foot一枚で英数字/修飾キー、クリック、scroll、logical resize、passthroughが同時に使える。再接続の合格はL01/L02を追加してから。

<a id="i05"></a>
## I05 — 二枚のwindowとlayer割当

coreに`logical_windows`と`visible_windows`を分離する。表示上限2、論理上限8。新しい三枚目はwindow一覧へ保持し、ユーザーが表示対象を切り替える。使用中の入力captureはfocus/表示切替前に解除する。

SDK registrationはActivity単位で再利用し、Entity/bindingはwindow単位。slot解放→次windowへ割当時にもWindowId/SurfaceGenerationを照合する。layer数を16から単純計算せず、診断UI・背景・system overlay込みで実機計測する。

二枚で一方が描画停止/resize中でも、もう一方のprotocol・入力・Stopが進むことを検査する。複数panelのswapが一つのGL threadを詰まらせる場合は`RENDER_STALLED`として記録し、未検証の増枚数を許可しない。

<a id="i06"></a>
## I06 — pose、背景、配置保存

pose/size/backgroundの正本はmodel。Kotlinがdrag結果をeventとして戻す。希望値とSDK適用ackを分け、保存はackだけに依存せずmodelの確定値を使う。scaleと物理寸法を重複適用しない。

保存先はnoBackupFilesDir内のschema付きJSON。temp＋fsync/rename、最大64 KiB。app_idとユーザー割当slotで配置を対応付け、WindowId/session IDを次起動へ流用しない。同じapp_idが複数なら一意に復元できないため既定配置へ戻す。

backgroundは`passthrough`/`virtual`の希望設定。Kotlinがsceneへ適用し、新sceneでも再適用する。`isSystemPassthroughEnabled()`を現在値getterとして使わない。背景切替でSSH/Wayland sessionを再起動しない。仮想背景の実体は最小の単色environmentとし、部屋scanを要求しない。

破損/未知schema/範囲外poseは既定値へ戻し、原因codeを残す。MR/VR切替、Activity再生成、同app二枚、保存途中killを検査する。

<a id="i07"></a>
## I07 — popupとclipboardの拡張範囲

popupは親と同じpanelで合成し、当面親rectangleでclipする。positioner制約、親子順、popup grabのserial/focus照合、popup_done、親destroy、入力targetを一貫させる。親外canvas拡張は独立した後続変更とし、clipした部分へ入力を送らない。

clipboardは最初に**同じEidolon session内のWayland text/plain UTF-8**のみ。W02で導入したwl_data_device_manager v3の空selection/取消処理を、focus・serialを検査した実転送へ拡張する。受信1 MiB上限、Coreが扱うFD転送の5秒timeout、FDのdrain/close、所有client切断時のselection解除。Waypipeの仲介bufferに渡った後の最終アプリの消費期限は保証しない。Android system clipboard同期、画像、drag-and-dropは別機能にする。

二枚のtest clientでcopy/paste、owner消失、大量/遅延データ、キャンセルを試す。通常ログにclipboard本文を残さない。任意のGUI appの完全互換性をこの合格から主張しない。
