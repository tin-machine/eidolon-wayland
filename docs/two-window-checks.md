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
Build84時点の`two-windows`コマンドはinstrumentation専用だった。
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

## 通常SDKの片側Surface喪失・表示切替検査

受入条件を再監査し、I06の短い二枚表示だけでは、SDK Entity別の喪失/切替/個別closeの証拠が不足すると判断した。
Build92で通常版の`two-windows`を接続した。検査専用nonceの保存先を使い、終了時は製品設定へ戻す。
同じSSH sessionを維持して次を検査する。

1. 二枚のSDK quadでpose/physical sizeのgeometry照合とray→keyを確認し、layer測定用に2.2秒保持する。
2. 片側の実SDK Entityを再生成し、新Entityの割当てを一時保留する。他方のSDK ray→keyと同じSurface世代のframe進行を確認する。
3. 保留を解き、片側だけ新しいSurface世代へ戻ること、旧targetのpose commandがSTALEになることを確認する。
4. 三枚目を非表示の論理windowとして保持し、Shiftを押した状態で表示枠を切り替える。押下解除と新しい表示先へのSDK ray→keyを確認する。
5. 三枚目を個別closeし、他方の更新を確認する。元の二枚へ戻し、実Activity再生成・新sceneへの再適用と保存復元を検査する。

保留はdebug版だけで使用する。releaseのhookは常にfalseで、通常の割当てを保留しない。
検査の終了・例外でも保留を解除する。実SDKのEntity/Surface callbackを使い、visibilityやpose適用ackを合成しない。
入力イベントは固定の合成イベントであり、物理controller/keyboard成功とは区別する。

```bash
scripts/quest-check --serial "$QUEST_SERIAL" --case two-windows --timeout 300
```

PC側は10stageのfull target・同一session・片側だけの世代変化・他方の入力/描画・第三window・押下解除・closeを要求する。
旧世代再利用、他方のframe停止、押下残留などを拒否する負例と、既存layoutのscope検査が成功した。
Build92のdebug/lint/packageは成功。通常SDKの実行結果はGuardianの追跡復帰後に記録し、現在は未実行。
geometry検査が通ってもLCntが`not_observed`なら、layer計測の受入れは保留する。

Build92ではdebug/release・lint・package、実APKのPython60件が成功した。
Questの既存native検査も、二枚73秒 `20261105T222514-09d535f0`、resize `20261105T222634-207e0d05`、
配置保存 `20261105T222647-497d4139`、US/JIS wire `20261105T222705-1ae7e6dd`で成功した。
検査用processの残留はなく、既存Termux bash/sshdを維持した。

その後のreviewで、SDK側がpose commandの完了待ち`pending`を即座にSTALE失敗と判定し得ることを発見した。
まだSDK実行前のsource reviewであり、Quest上でその失敗が発生したという記録ではない。
完了前は次tickを待つ形へ直し、同定のためBuild93を作成した。

Build93のdebug/release・lint・package、実APKのPython60件が成功した。
APK SHA256は`77b36df3b01f9f1dc702f25e4112b89c276b06a63e9fbd0ecf069f2538e54bce`。
source digestは`31ceeffebf0a78f97310b6f12c4bbc934d8f0692a6c5df4c3b74d5fcfbd4b4dd`。
同版のnative配置保存とUS/JIS wire回帰も成功し、停止後の検査用processは残っていない。
Build93作成時点では通常SDKの片側喪失/切替/closeとlayer数は未受入れで、ここまでの成功とは分けて扱った。

## 追跡復帰後の通常SDK結果と待機条件の修正

装着による追跡復帰後、Build93のlayout `20261105T225627-ed282f6b`が通常SDKで成功した。
二枚を保持した時間窓・APK UID/PIDが一致する`LCnt`は5〜6で、layer数も観測できた。
これはアプリの総layer数であり、Linux windowの数そのものではない。

続くtwo-windows `20261105T225642-37ff3bc1`は端末が成功を報告したが、PCが拒否した。
`third_hidden`で三枚目の存在だけを待ち、まだ`AwaitingInitialCommit`の時点で記録していたためだった。
後続stageでMappedになった事実を使って、この失敗を成功へ変更しない。
SDK側で全windowのMappedを待つよう修正し、PCにも初回commit前を拒否する負例を追加した。
Build94のnonce `3b058a643ed2f2ff17a7b357e7829702`では10追加stageと5基本stageが端末・PCとも成功した。
物理controller/keyboardとHMDの受入れは別に残る。

Build97の連続suiteでは実Activity再生成まで成功し、旧Sceneのadapter例外も出なくなった。
ただしPCは`shift_held`のwindow 2で、新しいwindow frameにSDK content frameが追いついていない記録を拒否した。
このstageだけは押下数を待ち、他stageと同じgeometry/描画readyを待っていなかった。
Shift保持の確認と二枚のreadyを両方待つ修正を追加し、PC基準は維持する。

Build99の通常two-windows `20261105T235243-e7ab9a89`は全10追加stage・5基本stageがPCを含め成功した。
同じ連続suiteのlayoutも成功し、両caseの二枚時間窓でアプリ由来LCnt 5〜6を観測した。
