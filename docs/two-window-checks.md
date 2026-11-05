# 二枚の表示と論理ウィンドウの検証

2026-11-05、I05実装中。通常SDKの二枚表示・layer実測とHMD/物理入力は未受入れ。

## 表示枠と世代

論理toplevel上限8と同時表示上限2を分離する。初めにmapした二枚を表示枠へ選び、
三枚目は一覧へ保持する。表示されないwindowのprotocolは処理するが、presentを根拠にした
frame callbackは送らない。既に表示中のwindowを別の枠へ指定した場合は二枠を交換する。

`show_window(session,scene,window,slot)`と`close_window(session,scene,window)`は
現session/sceneのmapped windowにだけ適用する。表示切替・個別closeでは入力を解除する。
希望の選択が変わっても、旧枠のdestroy ackが届くまで次のEntityは作らない。
古いSDK callbackはEntity IDで照合し、旧世代のtarget/command ackを新windowへ流用しない。
registrationはActivity単位で一つ、binding/Surface/入力listenerはEntityごとに所有する。

## ターミナルの追加

実機に導入したfoot/footclient 1.28.0の同梱manとversionを確認した。
managed footをserverとして起動し、セッション専用nonce directoryの`f` socketだけへ
`footclient --server-socket=... --no-wait`で接続する。
任意のcommand/path/textを制御channelから渡さず、interactive bashまたは検査用の固定出力だけを使う。
ユーザーのshell profile・foot設定・sshdや既存ターミナルを変更しない。

footclientの終了0は「serverへ新windowの起動を依頼した」証明であり、実際のmap完了とは異なる。
Coreは新windowのmapまで追加予約を保持し、UIも実WindowIdの増加を確認する。
応答待ちの追加クリックをまとめ、8枚に達したら追加を受け付けない。
footclientは短命の専用process groupとして所有し、WNOWAITでPIDを予約してから回収する。
5秒timeout、Stop/EOF/lease失効でも回収し、終了後だけ専用socketを削除する。

二つの端末は別PTYと描画threadを持つが、同じfoot server processとWayland接続を共有する。
serverのcrashは共通に影響する。端末processの障害分離が成立したとは主張しない。

## 自動検査の範囲

```bash
scripts/check-host --suite all
EIDOLON_ARTIFACT_ROOT=/absolute/private/work/builds scripts/build-android 84
adb -s "$QUEST_SERIAL" install -r /absolute/private/work/builds/RUN/eidolon-debug.apk
scripts/quest-install-launcher --serial "$QUEST_SERIAL"
scripts/quest-install-shm-fixture --serial "$QUEST_SERIAL"
scripts/quest-check --serial "$QUEST_SERIAL" --case two-windows --instrumentation
scripts/quest-check --serial "$QUEST_SERIAL" --case resize-native --instrumentation
```

`two-windows`はAPKの実SSH→Waypipe→foot→Smithay→GLESから二つのImageReaderへ描画する。
二枚の更新、一方のSurface喪失中の他方への入力/更新、resize後の再生成、三枚目の保持、
修飾キーを保持してからの表示切替と解除、旧target拒否、個別close、全停止/remote回収を検査する。
headless instrumentationが可視性を注入するため、通常SDKと実HMDの合格には含めない。

SDK layer数は診断UI・背景・system overlayと合わせて実機計測する必要がある。
仕様値16から引き算した推定で枚数を増やさない。
片側のEGL swapがGL ownerを詰まらせる場合は既存watchdogの`RENDER_STALLED`を残す。
Surfaceを外した検査と、swapそのものが詰まる実験は別である。

raw receiptと試行錯誤は、作業開始時に読み直す`codex-work-root`の
`eidolon-phase5-implementation/`へ保存する。下記に成立範囲を記録する。

## Build83の成立範囲と追加review

APK SHA256 `ede445ea81ab2680069cf30ea1df42ba79461b26d598b03131b9b5b3b07a6a23`、
source digest `d3fe5558a53e0b1b037010fc307203d1c0215d5ed2666361509331856f58b27c`。
run `20261105T192452-f61a3d90`で二枚→片側loss/他方入力→resize復帰→第三window保持→切替→個別close→Stopの6stageが成功した。
window 1のSurface世代は1→4→8、第三windowは世代6となり、停止は232msだった。
同版でresize-native/footの回帰、Rust98test、launcher17test、debug/release・lint・package、実APKのPython54testも成功した。
これは二枚のImageReaderを使うnative instrumentationの合格である。

その後のsource reviewで、launcherの稼働中監視期限が開始時の約70秒から更新されず、
正常な端末でも時間経過だけで`LAUNCHER_TIMEOUT`となり得る点を発見した。
Build83でこのtimeoutが発生したという記録ではない。
footの制御heartbeat送信に成功したときだけ稼働中期限を延長し、prepare/終了処理の期限は維持する。
remote側の60秒leaseは引き続きheartbeatが失われると終了・回収する。
実SSHと固定helperで稼働中期限を120msに縮め、約0.5秒の出力/終了まで維持できる回帰検査が成功した。
Build84では同じ二枚caseに73秒以上の継続検査を追加し、実機で開始時の期限を越えることを検査する。

## Build84の長時間検査

run `20261105T193137-21afe53a`で7stageが成功し、開始後73,006msでも二枚の描画・接続を維持した。
全停止は191ms。APK SHA256は`139499f35b5c2a3cc6486a6a5353259afa522dc7c5498793a435f33fca1361a1`、
source digestは`beb4cd4f17fd14bc85cc2cf03c362f36672027a8d52383ba0c367216abaf25d8`。
pointer-wire `20261105T193256-6ee5bade`、US/JIS keyboard-wire `20261105T193301-c34a5745`も成功した。
Rust98test・Clippy/fmt、launcher17test、debug/release・lint・package、実APKのPython55testが成功した。
PCの負例では、片側更新不足、三枚同時選択、修飾キー残留、短い継続時間、回収漏れを拒否した。
終了後の別auditでも固定fixture・foot・footclient・waypipe processが残っていないことを確認し、既存shell/sshdのidentityは維持されていた。

Guardianのトラッキング確認画面が前面にあるため、通常SDKの二枚描画・総layer数・HMD可読性は未受入れ。
現時点の`two-windows`コマンドはinstrumentation専用で、通常SDK用の同名自動caseは未実装。
製品UIの追加・表示枠切替・個別closeは実装済みだが、このheadless検査からSDK表示の成功へ読み替えない。

## layer数の取得を準備

Meta公式の[logcat stats定義](https://developers.meta.com/vr/documentation/spatial-sdk/ts-logcat-stats/)には`LCnt`が載っている。
[MQDHのlayer表示](https://developers.meta.com/vr/documentation/spatial-sdk/ts-mqdh-compositor-layer/)でも可視/総layer情報を確認できる。
新しい通常SDK layout検査は二枚を2.2秒維持し、APK UID・PID・その時間窓が一致するepoch logから`LCnt`だけを抽出する。
`ShrpLCnt`や`SSLCnt`、別process、時間窓外の値を除く負例も追加した。
取得できなければ`not_observed`として残し、geometry検査の成功からlayer計測済みとはしない。

今回のread-only調査では、dumpsysのOVRMetricsService/OculusWindowManagerは出力がなかった。
logcatには`LCnt=4`があったが、送信元はcom.oculus.vrshellで、Eidolonの二枚の計測値ではなかった。
通常SDKがFOCUSEDとなった状態で改めて採取する必要がある。
