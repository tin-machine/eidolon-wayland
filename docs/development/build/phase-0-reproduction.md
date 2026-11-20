# Phase 0 — 実機で確認したビルド・Wi-Fi・SSHの再現手順

2026-11-04、Ubuntuから作ったEidolon Build 1／2をQuest 3で表示し、Verifyボタン、通常passthrough背景、給電器接続中のWi-Fi ADB更新、PCからTermuxへのSSHを確認した。[試行錯誤と証拠](phase-0-troubleshooting-20261104.md)も残す。従来の[環境計画](phase-0-development-environment.md)にあるRust JNI、Wayland、長時間運転、再起動後の復旧は後続作業である。

## 採用した構成

| 対象 | 実測・固定した版 |
| --- | --- |
| PC | Ubuntu 26.04 x86_64、JDK 25.0.4.1 |
| Gradle / AGP | wrapper 9.4.1 / 8.11.1 |
| Kotlin / Compose compiler | 2.4.21 / 2.4.21、Java/Kotlin出力17 |
| Android SDK | compileSdk 36、minSdk / targetSdk 34、Build Tools 35.0.0 |
| Meta Spatial SDK | core / toolkit / vr / composeを0.15.0へ固定 |
| Quest 3 | Android 14 / API 34 / arm64-v8a、OS build `UP1A.231005.007.A1`、incremental `52641960031600520` |
| Termux | 公式GitHub v0.118.3、arm64-v8a debug APK |
| Termuxパッケージ | OpenSSH 10.6p1-3、termux-services 0.13-1（実行時の取得版） |

**JDK 25はPC用、compileSdk 36はビルド用。Questの実行API 34とは別である。** Rust/NDKによる独自nativeコードはまだない。既存NDK 27.3.13750724は今回の成功条件に含めない。

Meta Gradle plugin 0.15.0は新Kotlinと衝突したため適用していない。組込みcomponentとKotlinでsceneを生成し、Editor exportと独自component生成を使わない実装である。SDK本体を外したわけではない。独自componentや`.metaspatial`を導入する際にはpluginの互換性を再検証する。

## 1. PCでビルドする

既存SDKを確認して使う。今回のhostは`/opt/android-sdk`。別hostでは実際の導入先へ変更する。SDK licenseは通常のsdkmanager手順で確認・承認する。

```bash
export JAVA_HOME=/usr/lib/jvm/java-25-openjdk-amd64
export ANDROID_HOME=/opt/android-sdk
export PATH="$ANDROID_HOME/platform-tools:$PATH"
java -version
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --list_installed
# このhostのSDKはroot所有。自分が所有するSDKではsudoを外す。
sudo "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" \
  --sdk_root="$ANDROID_HOME" 'platforms;android-36' 'build-tools;35.0.0'
scripts/build-android 2
```

スクリプトは`assembleDebug`と`lintDebug`を実行し、JVM、Git revision・dirty状態、versionCode、ビルドログ、APK、SHA-256を保存する。Quest向けtarget API 34を維持するためlintの`ExpiredTargetSdkVersion`だけを除外し、その他のlintエラーは失敗扱いとする。APKはdebug署名であり、同じhostのdebug keystoreを使って更新する。

成果物の既定保存先は、`~/.config/homecluster-agents/codex-work-root`に指定したrootの`eidolon-phase0/builds/<run>/`。未設定なら`~/.local/state/homecluster-agents/codex-work/`をrootに使う。`EIDOLON_ARTIFACT_ROOT`で絶対pathを指定できる。APKと鍵はGitに入れない。

## 2. USB承認からWi-Fiへ移す

QuestのDeveloper Modeを有効にし、データ通信可能なケーブルでPCへ直接つなぎ、HMD内でUSB debuggingを承認する。`lsusb`で見えるだけではADB対応・承認済みとは判断しない。

```bash
adb devices -l
quest_usb_serial='実際のUSB serial'
adb -s "$quest_usb_serial" shell getprop ro.product.model
adb -s "$quest_usb_serial" shell getprop ro.build.version.sdk
adb -s "$quest_usb_serial" shell getprop ro.product.cpu.abi
adb -s "$quest_usb_serial" shell ip -4 addr show wlan0
quest_ip='上で確認したQuestのIP'
ip route get "$quest_ip"
quest_physical_id=$(adb -s "$quest_usb_serial" shell getprop ro.serialno)
adb -s "$quest_usb_serial" tcpip 5555
quest_serial="$quest_ip:5555"
adb connect "$quest_serial"
test "$quest_physical_id" = "$(adb -s "$quest_serial" shell getprop ro.serialno)"
adb -s "$quest_serial" get-state
```

同一端末の照合と`device`を確認してからPCのUSBを外し、Questを給電器へ接続する。以降は毎回`-s "$quest_serial"`を指定する。これは開発LAN向けのlegacy TCP ADBであり、AndroidのTLS pairing方式とは異なる。ADB portを外部公開しない。

```bash
adb devices -l
adb -s "$quest_serial" shell dumpsys battery
```

今回、USB承認・OS取得の後、**最初のAPK導入からWi-Fiを使った**。USBでのAPK installを実施した記録にはしない。PCは有線LANのままで到達できた。実際のrouteと物理端末IDは個別の作業記録へ残す。

## 3. APKを導入し、HMDで確認する

```bash
quest_apk='/上のビルドが出力した絶対path/eidolon-debug.apk'
scripts/quest-smoke --serial "$quest_serial" --apk "$quest_apk" \
  --require-focus --timeout 90
```

この操作はEidolonだけを停止・更新・起動する。実行中にHMDを装着し、Eidolonのパネルが見えること、Build番号、Verifyボタン、現実の背景を確認する。ボタンは単なる回数カウンターで、2回押しても問題ない。回数はメモリ内のみで、再起動後のデータ保持試験には使わない。

`quest-smoke`はAPKのpackage/versionCode、Questのmodel/APIを検査し、起動前から対象UIDのlogcatを連続保存する。起動後のPIDに対応する`scene_ready`、`panel_created`、指定時には`XR_SESSION_STATE_FOCUSED`を判定し、失敗時も`result.json`を残す。`scene-ready`は機械判定であり、HMD視認は別欄の`hmd_visual_confirmation`へ確認者が記録する。

保存先は作業rootの`eidolon-phase0/smoke/<run>/`。`--artifact-root`で変更できる。`app.log`、操作一覧、install/launch結果、package情報、battery、Android `screencap.png`を保存する。screencapはXR・passthroughの完全な画像を保証しない。HMDでの確認を置き換えず、部屋等が写り得る画像・生ログはそのまま公開しない。自動採取は短時間で終了するため、後からクリックする場合は別途対象PIDのlogcatを連続採取する。

Build 1からBuild 2への試験では、`scripts/build-android 1` → smoke → `scripts/build-android 2` → smokeの順で実行し、表示番号の変化とボタン操作を人が確認した。

## 4. Termuxを導入してSSHを構成する

[公式v0.118.3 release](https://github.com/termux/termux-app/releases/tag/v0.118.3)の`termux-app_v0.118.3+github-debug_arm64-v8a.apk`を作業directoryへ取得する。bootstrapスクリプトはこの**debug版の`run-as`**を利用するため、F-Droid等の別配布・別署名へそのまま適用しない。既存Termuxがある場合は配布元とデータを確認し、installの署名不一致を自動アンインストールで解決しない。

```bash
termux_apk='/作業directory/termux-app_v0.118.3+github-debug_arm64-v8a.apk'
curl -fL --retry 2 -o "$termux_apk" \
  'https://github.com/termux/termux-app/releases/download/v0.118.3/termux-app_v0.118.3%2Bgithub-debug_arm64-v8a.apk'
printf '%s  %s\n' \
  72fdb596045116bf5ba1b5bdf5b26fddb9acc0bd074ad9f2da9eb0ae85e83a4e \
  "$termux_apk" | sha256sum -c -
adb -s "$quest_serial" install -r "$termux_apk"
scripts/quest-termux --serial "$quest_serial" --bootstrap
```

`--bootstrap`はTermuxだけをdeviceidle allowlistへ追加し、WAKEUP・Termux起動後に初回展開を待つ。公式の署名付きapt repositoryからOpenSSH/termux-servicesを導入し、既存authorized_keysを保持してPC公開鍵を追記する。apt package版は実行時に変わるので`bootstrap.log`の`dpkg-query`結果も記録する。

専用`eidolon-sshd`は`127.0.0.1:8022`のみをlistenし、公開鍵認証だけを許可する。PCからは`adb forward --no-rebind tcp:18022 tcp:8022`で接続する。SSH host公開鍵を承認済みADB経由で取得して専用known_hostsへ固定し、以前と違えば停止する。SSH portをLANへ直接開ける必要はない。

PC秘密鍵の既定は`~/.ssh/quest_termux_ed25519`、known_hostsは`~/.ssh/eidolon_quest_known_hosts`。鍵がなければ無人開発用のパスフレーズなし専用鍵を生成する。既存の別鍵は`--key`で指定できる。結果に表示された`SSH:`のコマンドは対話接続にも使える。端末user名は実測するので`u0_aNNN`を固定しない。

```bash
# 通常の調査はbootstrapを繰り返さず、SSHだけ実行する。
scripts/quest-termux --serial "$quest_serial" \
  --command 'set -e; uname -m; id; sv status "$PREFIX/var/service/eidolon-sshd"'
scripts/quest-termux --serial "$quest_serial" \
  --command 'tail -n 20 "$PREFIX/var/log/sv/eidolon-sshd/current"'
```

Termux内の`~/.ssh/eidolon_sshd_config`、`$PREFIX/var/service/eidolon-sshd/`をこのスクリプトが管理する。runitのlog serviceが出力をローテーションする。非対話SSHでは`SVDIR`が設定されない場合があるため、`sv`には絶対pathを渡す。`bootstrap.log`・`ssh.log`・`result.json`は作業rootの`eidolon-phase0/termux/<run>/`に保存する。

Termuxの起動時にservice supervisorが起動する構成で、端末boot直後の無操作起動は未検証。Termux:Bootは導入していない。APK内russhからTermuxへ接続する製品機能もまだ実装していない。

## 5. 再接続・復旧

```bash
adb disconnect "$quest_serial"
adb connect "$quest_serial"
# forwardが失われても再作成し、host key照合付きで再接続する。
scripts/quest-termux --serial "$quest_serial"
```

この往復は実機で成功した。IP変更時は現在のIPを再確認する。端末再起動等でTCP ADBへ接続できない場合は、PCへUSBでつなぎ直して手順2から復旧する。HMDが未装着・sleep中ならXR sessionがIDLEのままになることがある。`am start`成功だけで表示成功とせず、装着・system dialog・アプリログを確認する。

USB運用に戻す場合は、PCへUSBケーブルを接続して対象を確認後、`adb -s "$quest_serial" usb`を実行し、`adb devices -l`でUSB transportを再確認する。Termuxのallowlist設定を戻すには`adb -s "$quest_serial" shell cmd deviceidle whitelist -com.termux`を使う。ADB server全体の停止やlogcat消去は通常手順に入れない。

給電器接続中の短時間の更新・操作は成功したが、30〜60分の計画的な連続試験、残量維持、sleep/wakeとQuest reboot後の自動復旧は未合格。`AC powered: true`と`Weak Charger: false`だけで充電中・残量維持と判断せず、status・残量・温度を時系列で確認する。
