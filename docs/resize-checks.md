# 論理resizeと物理サイズの検証

2026-11-05、I04実装中。HMD視認と物理入力の受入れはPhase 5の最後に行う。

## 要求・ack・画像を分ける

`native/model/src/resize.rs`は希望logical size、送信serial、ack serial、
configureで提示したsize、commitされたserial/sizeを分ける。ユーザー要求は幅320〜2048、高さ200〜2048。
一つのconfigureを待つ間の追加要求は最後のサイズへまとめる。
既に送ったserialは不透明なu32のticketとして管理し、数値の大小で新旧を判定しない。
focusのActivated更新が同じサイズを含む別configureを発行した場合も追跡する。

ackだけでは画像を切り替えない。既知のack serialと、要求送信後に更新されたroot contentを対応付ける。
要求値と異なる正当な実寸法は`adjusted`で報告し、完全一致の`applied`と区別する。
新contentのない空commitでは旧owned snapshotを保持し、同じViewportを描画と入力に使う。
5秒未応答はwindowごとの`RESIZE_TIMEOUT`とし、SSHやアプリ全体を失敗させない。
遅れて届く正しい画像、新しいサイズ要求、unmapも区別する。
未応答clientの履歴は64 ticketまでとし、無制限に増やさない。

modelのserial wrap/古ack/空commit/timeout/範囲・履歴上限と、実Wayland clientの
11 checkpoint（要求coalesce、ack、空commit、clientによるサイズ調整、完全一致画像、timeout、遅い画像、unmap）をhostで検査する。
target readinessを失った状態でも新画像のprotocol処理が進むことと、
physical commandがconfigureを送らず古いsize ackを拒否することも独立に検査する。
このhostのtarget lossは実Android Surface破棄の証明とは分ける。
fixtureのtimeoutはownerへ渡す仮想clockで進め、実機で5秒待った証拠とは区別する。
独立した実画像二回の更新前後で旧owned snapshotの不変性を確認する。

## メートルとproducer pixel

physical sizeは0.25〜3mの有限値で検査し、logical resizeと別commandで管理する。
SDKの`Scale` componentでパネルを拡大し、producerは実EGLで観測したpixel寸法を報告する。
現段階のproducerは1024×768を保ち、新logical画像をletterbox付きで表示する。
メートルだけの拡大ではWayland configureを送らない。

希望サイズとSDK適用済みサイズは別に報告し、全target tupleとsize command IDでackを束縛する。
SDK 0.15.0の`appliedScale`は内部テスト用APIで、Build75がKotlin compileに失敗した。
publicなJVM getterがあるだけでは製品用Kotlin APIの保証にならない。
可視性抑制やreflectionを使わず、Build76では公開`Scene.lineSegmentIntersect`から
既知UVの二点を実測する方式へ変更した。
Entityの希望Poseとphysical寸法からworld rayを作り、実hitのEntity・UV・world pointが
一致した後だけ適用ackを返す。単に希望componentを読み直して適用済みとはしない。
これは固定geometryの計測であり、controllerの生入力は採取しない。

UIには800×600、1024×768、1280×720の画面サイズと、独立した表示拡大を追加した。
変更中・未応答を表示し、縦方向にscrollして操作できるようにした。

## 再現

```bash
scripts/check-host --suite all
EIDOLON_ARTIFACT_ROOT=/absolute/private/work/builds scripts/build-android 82
adb -s "$QUEST_SERIAL" install -r /absolute/private/work/builds/RUN/eidolon-debug.apk
scripts/quest-install-launcher --serial "$QUEST_SERIAL"
scripts/quest-install-shm-fixture --serial "$QUEST_SERIAL"
scripts/quest-check --serial "$QUEST_SERIAL" --case resize-wire --instrumentation
scripts/quest-check --serial "$QUEST_SERIAL" --case resize-native --instrumentation
scripts/quest-check --serial "$QUEST_SERIAL" --case resize
```

`resize`は通常Host→SSH→Waypipe→footで800×500と1280×720を要求し、
実renderのcontent frameが新window画像に追いつくのを待つ。
さらに1.44×1.08mへ拡大してconfigure/producer pixelが変わらないことを確認し、
拡大後のSDK rayから合成クリックと固定keyを送って配送・停止・通常UI復帰を検査する。
物理キー入口や文字の可読性の証明にはしない。

raw receipt、build log、失敗と再開メモは、その都度読む`codex-work-root`の
`eidolon-phase5-implementation/`へ保存する。

## 実footのcell丸めから設計を修正

Build79の実機foot画像はlogical 1023×762、producerは1024×768だった。
端末のfoot 1.28.0同梱`foot.ini(5)`では、`resize-by-cells`は既定yesで、floating windowを
cellとpaddingの単位へ丸めると説明されていた。
[xdg-shellのconfigure仕様](https://raw.githubusercontent.com/wayland-mirror/wayland-protocols/main/stable/xdg-shell/xdg-shell.xml)も
通常toplevelの寸法をhintとし、cell単位の小さいサイズを認めている。
そのため、最初の実装にあった「要求と同じpixel寸法でなければtimeout」は修正した。
既知serial・root contentの対応は維持し、実寸法は別に保持する。古いserialの画像が
新しい希望値と偶然同寸法でも、最新要求のack済みとは扱わない。
modelと実client fixtureで`adjusted`→完全一致への遷移、serial wrap、空commitの回帰が成功した。

Eidolonが起動するfootには`--override=resize-by-cells=no`を指定し、pixel presetへpaddingで対応させる。
ユーザーのfoot設定やshell profileは変更しない。この指定でgeneric client側のサイズ調整を隠さず、
generic modelは上記のとおり異寸法も正しく扱う。
Build80でmatching launcherを配備し、実footのlogical sizeが1024×768となることを確認した。
二回目のImageReaderを800×600へ変更してもlogical sizeは1024×768で、producer寸法と独立していた。
Build76のnative resize-wireはrun `20261105T175035-7be9c906`で成功した。
通常SDKのrun `20261105T175041-31f2588d`はアプリ起動前にtimeoutした。
アプリPIDなし・アプリlog 0byteで、QuestのGuardianDialogActivityが前面、端末はAwake・給電中だった。
private screenshotには「ルームで位置を見つける」「近くの明かりをつけてください」と表示され、
トラッキングなしで続行する変更は行っていない。
これはresize処理中の失敗とは異なり、SDK case自体が始まっていない。

Build80ではnative resize-wire、ImageReaderのfoot描画、pointer-wire、US/JIS keyboard-wireが成功し、
debug/release・lint・package、Rust全97test、実APKのPython53test・launcher16testも成功した。
追加reviewで修正した空commitの旧画像serial保持と、clientのサイズ調整もこの版に含む。

## 実Surface再生成をまたぐ検査

`resize-native`はAPKの実SSH→Waypipe→foot→Smithay→Android EGLを使い、
ImageReader二世代で800×500から1280×720へのresizeを検査する。
サイズ変更要求と旧Surfaceの解除を連続enqueueし、再生成後に新画像がpresentされること、
旧targetの要求が`STALE`となること、再開後の固定合成key・click・scrollと全停止を確認する。
通常SDKの可視性通知を使わないnative instrumentationであり、SDKパネルの視認・物理入力とは分ける。

Build81のrun `20261105T185904-5d9b252b`では、Surface喪失時に`awaiting_ack`、
再生成後に1280×720 `applied`、旧target拒否まで成立した。
その直後の検査が、新画像frameへGPUが追従する前に即時assertし失敗した。
50msごとに出力するfixtureでは画像更新と描画の間に差が生じるため、
既存の「新画像とpresent済みframeが一致したsnapshotを待つ」処理へ揃えた。

Build82のrun `20261105T190618-dafa7c3b`では、この実Surface喪失・再生成・入力・停止の全経路が成功した。
wire fixtureもrun `20261105T190632-88a0c277`で成功した。
APK SHA256は`57b313995aef7b435c88d68f9b7bef01041ae7522e08c214ff041b4feb8f2d82`、
source digestは`4e906e5f0a56bf78b6765447652d2616872d84ef57dd3bcfa5e94dd5d364d6d5`。
Build82のdebug/release・lint・packageと実APKのPython54testも成功した。
SDKのphysical寸法計測と通常SDK resize、物理操作を含む最終受入れは残件で、I04完了とはしない。

## 通常SDKで判明した描画待ちと設定保存の問題

追跡復帰後のBuild94 `20261105T230528-a4d847a6`は端末側が成功を報告したが、PCが拒否した。
入力後のwindow frameは8、SDK側content frameは6で、新しい画像の受領から描画までを待っていなかった。
float比較の誤差ではなく、検証caseの早期記録が原因だった。
初回表示・クリック・文字入力後も新しい画像の描画を待ち、PCの描画条件は維持した。
入力後だけ画像が古い負例も追加した。この失敗記録を後から合格へ読み替えない。

併せて、resizeが製品の配置設定を保存し、次の固定寸法テストへ影響し得ることが分かった。
resize/pointer/keyboard/foot/rayをnonce別の設定へ隔離し、終了時は製品runtimeへ戻す。
停止時の設定flush後に元ファイルのdigestを採り、検証後も不変であることと、
新しい製品runtimeのFOCUSED・診断パネル3frame・remote回収完了を確認してから結果を公開する。
既存の製品設定を削除して検査を通す方式にはしない。

Build97の通常resize `20261105T233922-858fea09`は、修正後の描画待ち・SDK ray・入力・設定隔離・製品UI復帰まで成功した。
同版のpointer `20261105T233929-31f4903e`、keyboard `20261105T233936-b48ff363`、
foot `20261105T233943-122c24ab`も通常SDKで成功した。
これらの合成操作を物理入力の受入れには数えない。

Build99の通常resize `20261105T235302-e853dce0`でも、描画待ち・SDK geometry・入力・製品設定不変・通常UI復帰が成功した。
