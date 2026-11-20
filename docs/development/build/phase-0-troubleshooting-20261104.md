# Phase 0 — 2026-11-04の実装・試行錯誤・実機記録

対象baseは`a53541f744a67fb491e32ee658e4ee733d44c985`。この記録と同じcommitの`android/`・`scripts/`が実装である。時刻は特記なければJST。手順の正本は[再現手順](phase-0-reproduction.md)、将来の受入条件は[Phase 0計画](phase-0-development-environment.md)。失敗した実行の記録も消さずに残す。

## 結果と検証範囲

| 確認対象 | 結果 |
| --- | --- |
| JDK 25 / compileSdk 36の最小APK | assembleDebug・lintDebug成功。Quest API 34で実行 |
| XRパネルと入力 | Build 1を目視、Verifyを2回操作。Build 2への更新後も目視・操作確認 |
| 背景 | ユーザーが現実のカメラ映像を背景に確認。通常passthroughをSDKから有効化 |
| 給電器＋Wi-Fi | PCのUSBを外した状態でAPK導入・更新・起動・logcat・Android screencapが成功 |
| PC→Termux SSH | loopback限定、公開鍵認証、host key照合、ADB forwardで成功 |
| host接続の復旧 | ADB disconnect/connect後、forwardを再作成してSSH成功 |
| Termux service/log | 専用runit serviceとlog serviceの稼働、保存されたlisten/公開鍵認証ログを確認 |
| 未実施 | Rust JNI・Surface描画・Waypipe/Smithay、APK内SSH、USB経由APK導入、計画的な長時間試験、Quest reboot後復旧、CI |

今回の3目標は「APK build」「XR実機起動」「Termux/SSHと給電＋Wi-Fi開発」である。従来の広いPhase 0計画全体を完了したという意味ではない。

## 試行順と切り分け

| 時刻・段階 | 観測・失敗 | 変更したことと結果 |
| --- | --- | --- |
| 19:03〜19:10 USB | ケーブル交換後はUSBに見えたが、当初`2833:5010`にはADB interfaceがなくdevicesが空 | HMD内の設定・承認後に`2833:5013`、ADB interface、`device`へ変化。udev書換え・ADB server再起動は不要。[先行測定](phase-0-development-environment.md#41-usb承認端末ログの実測2026-11-04) |
| 19:19〜19:21 toolchain | SDK platform 36が未導入 | 既存`/opt/android-sdk`へ36だけ追加。JDK 25、Gradle 9.4.1、AGP 8.11.1、Kotlin 2.4.21で開始。既存35は保持 |
| 19:21 初回build | Meta Gradle plugin 0.15.0とKotlin 2.4.21併用時、compileDebugKotlin task作成に失敗 | 下記のcompiler classpath不整合を確認。codeでsceneを作る最小APKにはpluginのcomponent生成/exportが不要なため適用を外した。SDK依存0.15.0は維持 |
| 19:24 Build 1 | 修正後のassembleDebug・lintDebug成功 | 新JDK/compile APIを下げずにビルド成立。Linux Spatial Editor CLIの実行成否までは検証していない |
| Wi-Fi・給電 | PC USBではWeak Charger true、900mA/5Vという報告値 | USBでTCP ADBを有効化し、Wi-FiとUSBのro.serialnoを照合。ユーザーが給電器へ差し替え、Wi-Fiだけで操作を継続。AC true、USB false、Weak Charger false、1.67A/12Vを取得 |
| Termux初回起動 | sleep中のam startでBackgroundServiceStartNotAllowedException。初回展開が進まない | com.termuxだけdeviceidle allowlistへ追加し、KEYCODE_WAKEUP後に起動。bootstrap完了。全体の背景制限を無効化していない |
| 19:27 SSH設定確認 | sshd -Tの値は取得できるがbootstrapが非ゼロ終了 | 新しいOpenSSHは`Port`等の大文字混在。小文字限定grepを`grep -Ei`へ変更。service supervisorもsv-enableより先に起動する手順へ整理 |
| 19:28 XR smoke | Activity/OpenXR instance/session/swapchainは作成されたが、XR_SESSION_STATE_IDLEのままscene_ready timeout | HMD装着前の起動と表示成立を区別。ユーザー装着後にFOCUSEDへ進み、パネルと2回のクリックを確認。APK crashを示す失敗ではなかった |
| Build 1ログ | logcat -dの後追いでは大量のruntimeログにより起動・クリック行が上書きされる | 起動前からUID限定logcatを連続採取し、判定時は起動PIDとversionを照合するようsmokeを修正 |
| 19:31〜19:33 Build 2 | 同じ署名のversionCode 2をWi-Fiでinstall -r | scene_ready、panel_created、XR FOCUSED、panel_click count=1を連続ログへ保存。ユーザーもBuild 2表示とボタン操作を確認 |
| 19:35 host再接続 | Wi-Fi ADBを一度disconnect | connect後にSSHスクリプトがforwardを復元。SSH成功。端末reboot試験とは分ける |
| 19:34〜19:40 SSHログ追加 | 既に動くrunsvへlog subserviceを追加すると`log/supervise/ok`が存在せず、sshdのrestartだけでは直らない | 専用serviceのrunsvへexitを送り、runsvdirに再生成させてlog supervisorを確認。他serviceは停止しない |
| 19:40 SSH再起動直後 | `sv restart`直後の接続でConnection closed。直後のstatusには一時的なdownが出た | stop完了後にenable/startし、SSH bannerを読むservice/checkを追加。PIDが出ただけで接続準備完了としない |
| 19:40 非対話SSH | `sv status eidolon-sshd`がservice directory not found | SSHの非対話shellではSVDIRが設定されない。serviceの絶対pathを指定し、set -e付きで検証。最後のuname成功だけで途中の失敗を隠さない |
| 19:41 最終SSH | bootstrap→service/logのstatus→保存ログ確認→SSHがすべて成功 | listen/Accepted publickey、aarch64を確認。runitログの時刻はUTCなのでJSTより9時間前 |

Meta plugin併用時の失敗抜粋:

```text
Could not create task ':app:compileDebugKotlin'.
class org.jetbrains.kotlin.build.report.metrics.BuildTimeMetric has interface
org.jetbrains.kotlin.build.report.metrics.BuildPerformanceMetric as super class
```

pluginの依存に含まれるcompilerと採用Kotlinとの衝突として切り分けた。pluginを外した同じ新toolchainでbuild/lintが通ることを確認したが、全組合せの互換表や原因となる最小依存集合を確定したわけではない。将来pluginが必要になった際はこの境界から検証を再開する。

Build 2の保存済みログ抜粋（端末固有IDは省略）:

```text
19:32:35.203 activity_created version=2 api=34
19:32:53.816 activity_resumed version=2
19:32:53.852 scene_ready version=2
19:32:53.979 panel_created version=2
19:32:53.999 XR_SESSION_STATE_VISIBLE -> XR_SESSION_STATE_FOCUSED
19:33:00.004 panel_click count=1 version=2
```

Build 1のcount=1/2は当時の端末ログとユーザー報告で確認したが、後追いで保存したbufferには全行が残っていなかった。この欠落を明記し、Build 2では連続採取で解決した。HMD内の背景映像についてはユーザーの視認報告を根拠とし、Android screencapだけから断定していない。

## 給電の観測と残る条件

PC USBでは96%、36.0°C、Weak Charger true。給電器への切替後の短時間測定では約95%、33°C、AC true・USB false・Weak Charger false。その後19:42頃には92%、37.0°C、battery status 4を取得した。外部電源の認識は確認できたが、残量維持や継続充電はまだ確認できていない。表示された最大電流・電圧は瞬時消費電力の測定値ではない。充電器・ケーブル条件、温度、装着/描画負荷を揃えた30〜60分の試験へ引き継ぐ。

## 生ログ・成果物の索引

作業rootは毎回`~/.config/homecluster-agents/codex-work-root`から読む。今回のtask相対directoryは`eidolon-phase0-build-xr-network-20261104/`。directory 0700、作業記録0600を基本に保存し、端末serial・IP・SSH鍵・部屋の画像はこの文書へ持ち込まない。下記はすべてtask相対pathである。

| 証拠 | path / run ID |
| --- | --- |
| Meta plugin併用時の失敗 | `builds/20261104T192147-1298134/build.log` |
| Build 1 assemble/lint・APK | `builds/20261104T192401-1299407/` |
| Build 1装着前timeout | `smoke/20261104T192802-1301721/` |
| Build 1ユーザー確認・後追いログ | `xr-build1-user-confirmation.json`、`xr-build1-after-user-confirmation.log` |
| Termux最初のbootstrap失敗・修正後 | `termux-bootstrap-20261104T192707.log`、`termux-bootstrap-20261104T192855.log` |
| 最初のSSH成功 | `ssh-first-test.txt` |
| Build 2 assemble/lint・APK | `builds/20261104T193118-1304197/` |
| Build 2 XR起動・画像・目視結果 | `smoke/20261104T193228-1304905/` |
| service log追加の失敗 | `termux/20261104T193440-1308768/` |
| ADB再接続後のSSH | `termux/20261104T193516-1309745/` |
| runsv修正後・起動直後接続失敗 | `termux/20261104T194019-1314879/` |
| 非対話svのpath不一致 | `termux/20261104T194046-1315171/` |
| 最終bootstrap/SSH/log確認成功 | `termux/20261104T194108-1315306/` |

APK SHA-256（PC固有debug署名を含むため他hostでの同一hashを要求しない）:

```text
Build 1: bc7d8ec23f0c605078fd515a9b6db0bf67aa9f09208778cfb02a7c090aaae0fb
Build 2: 08a3f3e3ff3ebbacb2bed8d192cec091ddb4bec7f6354ece98d3c262a9e6d2e0
```

手順の再現性を目指す記録であり、apt repositoryのsnapshotや全依存のlock、bit単位の再現ビルドを保証するものではない。Gradle distributionとwrapperのSHA-256は公式値と照合した。Termux APKも公式releaseのchecksumと署名を確認した。

## 判断の根拠

- [Meta samples固定commit](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/commit/822f4d1ec967d4975ae8db386502b60cda5218c0): SDK 0.15.0 / Gradle 9.4.1 / AGP 8.11.1の比較対象。
- [Meta Hello World](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-helloworld/): code中心のscene構築を検討する入口。今回のEidolonは独自の最小Compose panel実装。
- [runsv](https://smarden.org/runit/runsv.8)、[sv](https://smarden.org/runit/sv.8): log subserviceの構造、supervisor終了、check付き起動の挙動。
- Git反映は既存private repositoryのmainを対象とする。ローカルGit SSHはpublickey認証で失敗したため、接続済みGitHub APIでblob/tree/commitを作成し、旧HEAD照合付きでmainを進める。Git treeは既存repository内のオブジェクトで、新repositoryの作成ではない。ローカルとremoteのtree/commit SHAを照合して同期する。

次はRust cdylib/JNIでbuild情報を返す独立スパイク、SDK SurfaceへのRust描画、APK内SSH/Waypipeの順で進める。今回成立したXR・Wi-Fi・SSH開発基盤を利用する。
