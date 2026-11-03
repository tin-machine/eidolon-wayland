# Phase 0 — Ubuntu開発PC・Quest 3・Termuxの開発環境

**Status:** Planning（2026-11-03計画更新、Quest実機では未検証）  
**上位計画:** [implementation-plan.md — Phase 0](implementation-plan.md#phase-0--レポジトリと開発環境)  
**全体設計:** [architecture.md](architecture.md)

**共通の操作・調査:** [開発自動化・デバッグ](development-automation-and-debugging.md)（2026-11-03追記）。Codex／ADB／Meta VR CLI、スクリーンショット、入力、自動判定、無線接続、実機CIの詳細を参照する。

本ファイルは **Phase 0の実行手順・調査事項の正本**。上位planには概要と合格条件を残す。現状のレポジトリにはまだAndroidアプリ本体がない。以下の <code>android/</code> やAPKのパスは、後に実装するまで存在しない。

**Rust中心構成の調査結果を[第9節](#9-rust中心構成のビルド調査2026-11-01追記)に追記した。** Kotlin＋Rustを主要言語とし、C++は必要性が実証されたshimに限定する。描画は[Phase 1](phase-1-rust-rendering.md)、SSHは[Phase 2](phase-2-rust-ssh.md)、compositor移植は[Phase 3](phase-3-rust-compositor-waypipe.md)へ続く。

<a id="1-ゴールとusb接続構成"></a>

## 1. ゴールと開発接続構成

**Phase 0の目標:** Ubuntu PCでMeta Spatial SDKを利用した最小APKをビルドし、まずUSBで初期承認・APK導入・XR起動・基準診断を再現する。続いてQuestのUSB-Cを給電器へ挿し替え、Wi-Fi経由のAPK更新・install、起動、ログ、capture、TermuxへのADB forward＋SSHを検証し、通常の開発経路として採用できるか判定する。Termux/Waylandとの製品内通信はPhase 2〜5の対象で、ここで構築するSSHは開発用である。

[Meta公式のQuest向けADB手順](https://developers.meta.com/vr/documentation/native/android/ts-adb/)は、USB接続中にTCP/IPモードへ切り替えて接続した後、USBを抜いてADBを利用できるとしている。**これは資料で確認した対応であり、Eidolon／手元のQuestでの実測結果ではない。** 詳細な接続・復旧手順の正本は[開発自動化・デバッグの無線ADB](development-automation-and-debugging.md#無線adb)、Phase 0での採用試験は[第5.4節](#54-usb基準からwi-fi運用への採用試験)とする。

~~~mermaid
flowchart TB
 subgraph PC["Ubuntu開発PC"]
  BUILD["git + Gradle + JDK + Android SDK/NDK"] --> APK["debug APK"]
  CODEX["Codex CLI (PC)"] --> SSH["SSH client :18022"]
  APK --> ADB["adb install / logcat"]
  SSH --> FWD["adb forward tcp:18022 tcp:8022"]
 end
 subgraph QUEST["Quest 3 / Horizon OS"]
  AD["adbd"] --> APP["Spatial SDK APK"]
  AD --> T["Termux sshd :8022"]
 end
 ADB <-->|"初期USB / 通常Wi-Fi ADB"| AD
 FWD <-->|"初期USB / 通常Wi-Fi ADB"| AD
~~~

**区別:** 上のSSHは**開発PC→Termuxへの調査用**。Eidolon APK→Termuxへのlocalhost SSH＋Waypipeという製品構成は[architecture.md](architecture.md)に記載。USB/ADBは製品実行時に不要とする。

## 2. ビルドツールの基準

2026-11-01に上流[Meta-Spatial-SDK-Samplesのコミット `f233e2327b95f9871b75bdba867d6fdd726f07cc`](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/commit/f233e2327b95f9871b75bdba867d6fdd726f07cc)を確認した。[README](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/f233e2327b95f9871b75bdba867d6fdd726f07cc/README.md)、[StarterSampleのversions](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/f233e2327b95f9871b75bdba867d6fdd726f07cc/StarterSample/gradle/libs.versions.toml)、[Gradle wrapper](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/f233e2327b95f9871b75bdba867d6fdd726f07cc/StarterSample/gradle/wrapper/gradle-wrapper.properties)の値を再現開始時の基準にする。**以下は上流ソースの構成であり、Ubuntu 26.04で動作確認済みの組み合わせではない。** 実装時には上流SHAと実際に成功したツール構成を記録・固定する。

| 対象 | 参考バージョン/選定方針 |
| --- | --- |
| 開発PC | Ubuntu 26.04 x86_64 |
| Android Studio | 上流はNarwhal 2025.1.1以降（Linux版はGoogle公式） |
| JDK | **17** |
| Gradle | **9.4.1**、必ずGradle wrapperを使う |
| Android Gradle Plugin | **8.11.1** |
| Kotlin | **2.1.0** |
| Meta Spatial SDK | **0.14.0** |
| Android API | 上流StarterSampleはcompile/min/target **34** |
| SDK Build Tools | **35.0.0**。AGP 8.11の最低・既定版。Android APIの値とは別 |
| Android NDK | build.gradle.ktsとの整合で決定。上流MediaPlayerSampleの例は27.0.12077973 |
| Rust | stable ＋ aarch64-linux-android target。採用crateのMSRVを確認し固定する（第9節） |

**Ubuntuの重要な注意点:** 上流サンプルREADMEはビルド環境をMac/Windows中心に案内する。一方Metaは[Linux版Spatial Editor CLI v16.1](https://developers.meta.com/vr/downloads/package/meta-spatial-editor-cli-for-linux/)と[LinuxヘッドレスCLI手順](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-editor-ai-scene-manipulation/)を提供している。Linux版にはGUI Editorがない。**Linux CLIを使ったStarterSampleのscene export/assembleがそのまま通るかは未確認**であり、最初の検証ゲートとする。

3つの選択肢: (A) Ubuntu + Meta Linux Editor CLI＋StarterSampleを再現、(B) scene exportに依存しない最小Spatial SDKアプリを構築、(C) Mac/Windowsの公式ビルドと比較してLinux依存の失敗箇所を切り分け。Ubuntuから開発する方針を優先し、他OSへの移行を必須とはしない。

[Meta公式Hello World](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-helloworld/)、[Spatial SDK Gradle Plugin / cliPath](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-editor/)を参照。

## 3. Ubuntu PCのセットアップ

### 3.1 OS・Java・Android SDK

~~~bash
sudo apt update
sudo apt install -y git curl unzip zip usbutils openjdk-17-jdk \
  android-sdk-platform-tools openssh-client
java -version
javac -version
adb version
~~~

1. [Android Studio Linux](https://developer.android.com/studio)を公式から導入する。
2. Android Studio初回設定でSDK、Android SDK Command-line Tools (latest)、Platform Toolsを導入する。
3. SDKの正しいインストール先を調べ、以下の例のパスを実環境に置き換える。fishを使う場合はset -gxで設定する。

~~~bash
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
command -v adb
command -v sdkmanager
sdkmanager --list_installed
sdkmanager "platform-tools" "platforms;android-34" "build-tools;35.0.0"
sdkmanager --licenses
~~~

**Build Toolsの訂正:** [Google公式AGP 8.11互換表](https://developer.android.com/build/releases/agp-8-11-0-release-notes)はSDK Build Toolsの最低・既定版を35.0.0としている。旧手順の34.0.0から修正した。`compileSdk=34`とは矛盾しない。上流StarterSampleは`buildToolsVersion`未指定のため不足版が自動取得される可能性もあるが、再現手順・CIでは必要な版を明示する。

<code>sdkmanager</code>がなければAndroid Studio SDK ManagerからCommand-line Toolsを追加。[Google公式sdkmanager](https://developer.android.com/tools/sdkmanager)。apt版adbがSDK版adbより優先されないようにし、<code>which -a adb</code>で混在を検査する。

NDK/CMakeが必要になった場合の**例示**（Eidolonの確定版ではない）:

~~~bash
sdkmanager "ndk;27.0.12077973" "cmake;3.22.1"
~~~

### 3.2 Linux版Meta Spatial Editor CLI

[Meta公式Linux CLI配布](https://developers.meta.com/vr/downloads/package/meta-spatial-editor-cli-for-linux/)から手動で取得・展開し、現物の実行ファイルを確認する。公式資料ではLinuxの実行ファイル名は <code>MetaSpatialEditorCLI</code>。第三者mirrorを利用せず、バージョンと取得元を記録する。

~~~bash
# 公式のパッケージを展開した場合の例。パスは実物に合わせる
export MSE_CLI="$HOME/opt/meta-spatial-editor/MetaSpatialEditorCLI"
test -x "$MSE_CLI" && "$MSE_CLI" --help
~~~

[Spatial SDK Gradle Plugin](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-editor/)の <code>spatial.scenes.cliPath</code> に実際のCLIパスを指定する。`MSE_CLI`をexportするだけではGradleへ渡らないため、Gradle propertyや環境変数を読む設定を用意し、個人の絶対パスをコミットしない。上流StarterSampleがこれだけで通ることを保証しない。

検証は「CLI単体の実行」「Gradleのscene export」「`:app:assembleDebug`」の順に分け、各結果を記録する。export task名は採用したpluginの `./gradlew :app:tasks --all` で確認する。エラーログからCLI不在、CLI実行失敗、export内容不整合、Androidビルド失敗を判別する。

### 3.3 Rust toolchain（Phase 1以降の先行準備）

~~~bash
# rustup導入方法は https://rustup.rs を参照
rustup target add aarch64-linux-android
rustc --version
cargo --version
# cargo-ndkでのcdylibビルドとJNI疎通は第9節へ
~~~

参考: [cargo-ndk](https://github.com/bbqsrc/cargo-ndk)、[Smithay/smallvil](https://github.com/Smithay/smithay/tree/master/smallvil)。

## 4. Quest 3のDeveloper ModeとUSBデバッグ

[Meta公式端末セットアップ（2026-10-28更新）](https://developers.meta.com/vr/documentation/android-apps/enable-developer-mode/)に準拠。

1. Meta developer accountと開発チーム登録・本人確認等の必要な準備を済ませる。
2. スマートフォンのMeta Horizonアプリから対象Questの**Developer Mode**を有効化する。
3. **初期承認とUSB基準試験にはデータ通信対応**USB-CケーブルでQuestとUbuntuを接続する。Wi-Fi経路を確立した後はPCから外し、給電器へ接続して試験する。
4. Questを装着して**Allow USB debugging**を承認（信頼するPCのみ）。
5. Ubuntuから以下を実行する。

~~~bash
lsusb
adb kill-server
adb start-server
adb devices -l
adb shell getprop ro.product.model
adb shell getprop ro.product.cpu.abi
adb shell getprop ro.build.version.sdk
adb shell getprop ro.build.version.release
~~~

上記のABI/API確認を初回のビルド・NDK設定より先に行い、Horizon OSのbuild情報も記録する。Rustのnative minimum APIはAPKの`minSdk`と揃える。[Android公式NDK資料](https://developer.android.com/ndk/guides/sdk-versions)のとおり、native APIの利用可否を決めるのは`compileSdk`ではなくminimum APIである。

期待値: <code>adb devices -l</code>に<code>device</code>。<code>unauthorized</code>はHMD内のUSB承認を確認。<code>offline</code>や不安定な接続はケーブル、ADBの二重インストール、udev権限を確認。Linux用udevルールは[Android公式 device setup](https://developer.android.com/studio/run/device#setting-up)に従う。複数端末なら <code>adb -s SERIAL ...</code> で指定。

## 5. 実際にAPKをビルド・デプロイする

### 5.1 先にMeta公式StarterSampleを再現

~~~bash
mkdir -p ~/src
git clone https://github.com/meta-quest/Meta-Spatial-SDK-Samples.git \
  ~/src/Meta-Spatial-SDK-Samples
cd ~/src/Meta-Spatial-SDK-Samples
# 調査時の上流構成を再現する。別の版を採用する場合はSHAと差分を記録する
git checkout --detach f233e2327b95f9871b75bdba867d6fdd726f07cc
cd StarterSample
# 第3.2節でCLI設定とscene exportを検証してからassembleを実行する
chmod +x ./gradlew
./gradlew --version
./gradlew :app:assembleDebug --stacktrace
ls -lh app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell pm list packages | grep com.meta.spatial.samples.startersample
~~~

Quest側の **Library → Unknown Sources** からアプリを起動し、表示を目視する。Android StudioのRunからも起動可能。ログ確認:

~~~bash
adb logcat -c
# Questでアプリを起動後
adb logcat -d -v time | grep -Ei 'Spatial|AndroidRuntime|FATAL EXCEPTION' | tail -100
~~~

[Meta公式StarterSample](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-sample-starter/)参照。Linux上で失敗した場合は <code>Meta Spatial CLI not found</code>、exportタスク、JDK/Gradle/AGPの不整合、ADB認証を分離して記録。**実機ビルド未検証のため、成功したとは記載しない。**

上のログ例は短い表示確認用である。自動試験では、先にmain／system／crash bufferを保存してから絞り込み、native crashを含む情報を残す。明示的なActivity起動、`am start -S -W`、PID／dumpsys、期限付きreadinessの詳細は[共通文書のADB操作](development-automation-and-debugging.md#adbの基本操作)へ集約する。

### 5.2 Eidolon専用の空APK（未実装）

- [ ] 公式サンプルのライセンス/構成を確認し、Eidolon固有applicationId・AppSystemActivity・registerFeatures・registerPanelsを用意する。
- [ ] [Passthrough背景の準備](phase-1-passthrough-background.md#manifestとfeature登録)として、merged manifestの`com.oculus.feature.PASSTHROUGH`宣言と背景entityを確認する。背景表示のためにCamera2の権限や部屋スキャンを必須化せず、実際の背景＋Rustパネル表示はPhase 1で試す。
- [ ] Linux CLIが障害なら、scene exportが不要な構成を検証する。Scene Editorの依存を盲目的に削除しない。上流[StarterSampleActivity](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/f233e2327b95f9871b75bdba867d6fdd726f07cc/StarterSample/app/src/main/java/com/meta/spatial/samples/startersample/StarterSampleActivity.kt)は`apk:///scenes/Composition.glxf`を読み、`Environment`ノードへアクセスするため、exportを外す際はActivity側の読込・Entity生成も整合させる。
- [ ] Gradle wrapper/JDK/SDK/Kotlin/AGP/Meta SDKを固定しCIでassembleDebugを実行する。
- [ ] Quest実機へUSBで初回installし、起動、logcat、クラッシュとXR描画の基準結果を記録する。Wi-Fiでの更新・再実行は第5.4節で別に判定する。
- [ ] compositorやrendererの統合はPhase 1/3で行い、初回実装では第9節のRust cdylibロードとJNI疎通を先行検証する。

初回実装では、上記の最小APKに第9節のJNI疎通を加え、次の成果物までを一つの小さな到達点にすることを推奨する。描画・SSH・compositorの統合は後続の独立した検証で進める。

| 成果物 | 確認する結果 |
| --- | --- |
| PC環境の診断手順またはスクリプト | JDK/SDK/NDK/Rust/ADBの実体・版、接続端末のABI/APIが記録される |
| Eidolonの最小Spatial APK | 独自applicationIdでinstall・XR起動し、静的パネルが見える |
| Rust cdylibとJNI呼出し | Rustから返した文字列・版情報がパネルに表示される |
| GradleからCargoへのビルド連携 | 一つのビルド手順で正しいABIの`.so`を生成・同梱し、debug/releaseや更新を取り違えない |
| 基本CIと検証記録 | APK assemble・native成果物の同梱を確認し、実機での成否は別に記録する |

**実装上の提案:** APK側とRust側のbuild revisionを両方パネルへ表示すると、古い`.so`を同梱した場合に気づきやすい。成功した構成は`Cargo.lock`、`rust-toolchain.toml`、Gradle wrapper、version catalog等へ固定する。ファイル構成や連携方式は実装時に決めてよい。以上は未実装の成果物案である。

実装後の**想定パス（現在存在しない）**:

~~~bash
git clone git@github.com:tin-machine/eidolon-wayland.git
cd eidolon-wayland/android
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
~~~

### 5.3 自動化できる作業と実機で確認する作業

PC側Codexには環境調査、ビルド、APK/JNI実装、認証済みADB（USB／Wi-Fi）でのinstall・起動・ログ／状態／画像取得、CI整備を任せる。[開発自動化・デバッグ](development-automation-and-debugging.md)に、公式のADB／Meta VR CLI／Codex MCP、2D UI操作、XR capture、無線ADB、実機runnerの条件を整理した。Developer Mode設定、初回USBデバッグ承認、USBケーブルの接続・給電器への挿し替え、必要なTermux初期設定、HMD装着時の空間表示・操作感の確認は利用者が行う。

最初は[小さなsmoke](development-automation-and-debugging.md#実機smokeの実装案)でbuild→install→launch→JNI readiness→ログ・画像取得をつなぐ。`am start -W`の成功とPID存在はXR表示成功ではない。Meta CLIの`metacam`をXR画像の第一候補として実機照合し、普通の`screencap`が黒い／一部しか写らない場合はcapture経路も調べる。画像が取得できない段階も、build／JNIと表示の結果を分けて記録する。

APKの生成や通常のAndroid emulatorでの起動を、QuestのXR表示確認に置き換えない。上流[Manifest](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/f233e2327b95f9871b75bdba867d6fdd726f07cc/StarterSample/app/src/main/AndroidManifest.xml)もQuest固有のnative libraryとVR起動設定を要求する。[Simulatorの適用範囲](development-automation-and-debugging.md#simulatorの使い分け)と、XR Operator／Tapedeckの未検証条件を確認し、それらの完成をPhase 0の開始条件にしない。

最小APKの実機確認時から、Horizonメニュー表示、装着解除・復帰によるXR session/focusの変化を観測する。TermuxへのSSH経路も準備できていれば、Eidolonを前面にした後も接続・Termuxプロセスが維持されるかを記録する。詳細は[Phase 1](phase-1-rust-rendering.md)と[Phase 6](phase-6-rust-lifecycle-testing.md)へ引き継ぎ、SurfaceやWaylandの耐障害性をこの段階で検証済みにしない。

[Phase 3のP3-B](phase-3-rust-compositor-waypipe.md)で扱うAPK UIDからのWaypipe helperの配置・起動も、最小APKができた時点で独立した先行検証にできる。ADB shellやTermux UIDでの実行成功と、製品APK自身からの実行成功を区別する。

### 5.4 USB基準からWi-Fi運用への採用試験

先にUSBで第5.2〜5.3節の最小APK・JNI・診断の基準を残し、[無線ADBの手順](development-automation-and-debugging.md#無線adb)に従ってネットワーク接続を確立する。PCとのUSBを外して給電器へ挿し替えた後、同じAPK revision・診断条件で再実行する。更新試験では更新前後のrevisionも記録する。

| 試験 | 確認・記録すること |
| --- | --- |
| USB基準 | 初期承認、最小APKのinstall・起動、JNI readiness、ログ・captureとHMD表示の照合 |
| 給電中のWi-Fi操作 | APK更新・install、起動、ログ・captureを確認する。Termux準備済みの場合はnetwork側serialを指定したADB forward＋SSHも試し、未準備ならその項目を未実施として残す |
| 30〜60分の連続運用 | 使用した給電器・ケーブル、実施時間、開始・終了時の残量、温度の推移、切断・再接続、取得できた診断と欠落を記録する。温度は取得元・単位を残し、未取得の値を推測で埋めない |
| 回復 | sleep→wake、Questのreboot、hostの再接続を別々に試し、ADB復帰・forward再設定・Termux SSHを確認する。HMD操作やUSBでの再設定が必要だった場合も記録する |

host再接続ではADB server／hostの再起動を含む条件と、使用した認証鍵・端末IPが維持されたかを記録する。ネットワーク接続の復帰とアプリ／Termuxの復帰は別に判定する。[Meta公式debug資料](https://github.com/meta-quest/agentic-tools/blob/3a8553d10a5a1bd1bf9beaaa950243fbbed3b9ea/skills/hz-vr-debug/SKILL.md#gotchas)はsleep後のWi-Fi ADB切断を、[公式device管理資料](https://github.com/meta-quest/agentic-tools/blob/3a8553d10a5a1bd1bf9beaaa950243fbbed3b9ea/skills/metavr-cli/references/metavr-device-management.md#metavr-device-reboot)はreboot後に再接続が必要な場合を案内している。TCP/IP待受や認証の永続性、ADBからのwakeが常に可能とは扱わず、採用Horizon OSで実測する。

**USB基準の合格とWi-Fi運用の採用判定は独立させる。** Wi-Fi試験の未実施・失敗をUSB成功で埋めず、採用・条件付き採用・保留と復旧条件を記録する。無線に課題が残ってもUSBを初期設定・診断・復旧に使い、Rust JNI、描画、SSH、compositor等の独立した検証を継続できる。ここでの30〜60分試験を[Phase 6の耐久・lifecycle検証](phase-6-rust-lifecycle-testing.md)の完了とは扱わない。

## 6. Quest内Termuxを調査する方法

<a id="61-推奨pc側の公式codex--usb-adb-forward--termux-ssh"></a>

### 6.1 **推奨：PC側の公式Codex → ADB forward（USB／Wi-Fi）→ Termux SSH**

ADBの[port forwarding](https://developer.android.com/tools/adb#forwardports)を使い、Ubuntuのlocalhost:18022をQuestのTCP:8022へつなぐ。最初はUSB経由で確認し、[Wi-Fiへの切替](development-automation-and-debugging.md#無線adb)後はnetwork側serialでforwardを作り直して再検証する。**PC側のCodex CLIがsshを実行する**のでQuestにCodex CLIをインストールする必要はない。

~~~mermaid
flowchart TB
 subgraph HOST["Ubuntu PC"]
  C["Codex CLI"] --> SSH["ssh -p 18022"]
  SSH --> FWD["adb forward tcp:18022 tcp:8022"]
 end
 FWD <-->|"USB / Wi-Fi ADB"| AD["Quest adbd"]
 AD --> SS["Termux sshd: 127.0.0.1:8022"]
 SS --> SH["Termux shell: ls / pkg / git / ps"]
~~~

**Termux側（Quest内でTermuxを起動してから）**:

~~~sh
pkg update
pkg install openssh
whoami
id
mkdir -p ~/.ssh
chmod 700 ~/.ssh
# 開発PCの公開鍵を ~/.ssh/authorized_keys に登録してchmod 600
# SSH待受をloopbackに限定
sshd -p 8022 -o ListenAddress=127.0.0.1
ss -lnt | grep 8022
~~~

SSH調査経路だけならTermux側のGitHub CLIやNode.jsは不要。`git`、`gh`、Node.js/npmは第6.2〜6.3節の作業が必要になったときに追加する。

**PC側**:

~~~bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/quest_termux_ed25519" -C eidolon-quest
# 上の公開鍵をTermuxに登録する（安全な初回セットアップ）
adb devices -l
# USB基準ではUSB serial、Wi-Fi試験では adb devices -l の IP:port に置換する
quest_serial='YOUR_ADB_SERIAL'
adb forward --list
adb -s "$quest_serial" forward tcp:18022 tcp:8022
# TERMUX_USERNAME はTermuxの whoami 結果で置換する
ssh -p 18022 -o IdentitiesOnly=yes -i "$HOME/.ssh/quest_termux_ed25519" \
  TERMUX_USERNAME@127.0.0.1 'uname -a; id; pwd'
adb -s "$quest_serial" forward --remove tcp:18022
~~~

<code>$PREFIX/etc/ssh/sshd_config</code>で公開鍵のみ（<code>PasswordAuthentication no</code>）、loopback待受と設定を確認。<code>sshd -T</code>と<code>ss -lnt</code>で実際の状態を確認し、秘密鍵はGitへコミットしない。初回SSH host keyはTermuxの <code>ssh-keygen -lf $PREFIX/etc/ssh/ssh_host_ed25519_key.pub</code> などの指紋と照合して受け入れる。<code>StrictHostKeyChecking=no</code>での迂回はしない。

**Codexへ渡す指示:** PC上のリポジトリで公式Codexを使い、「<code>adb devices</code>と<code>adb forward --list</code>を確認してから、<code>ssh -p 18022 …</code>でQuest Termux内のOS・pkg・Node・OpenSSH・Waypipeを**まず読み取り専用で調査**。削除、設定変更、認証情報閲覧は許可を求める」と依頼する。Codexのsandbox/コマンド承認でsshが制限される場合は適切な権限を調整。

**ADB shellの限界:** <code>adb shell getprop</code>・<code>adb logcat</code>・<code>adb shell pm list packages</code>は使用可能。しかしAndroid別UIDのTermuxのprivate homeへ通常の<code>adb shell</code>からアクセスはできず、release版Termuxに<code>run-as com.termux</code>が通るとも限らない。Termux内部調査はSSHで行う。

**ADB forwardはQuestのインターネット回線を提供しない。** USB／Wi-FiのADB経由でSSHが成立しても、QuestからGitHub/OpenAI APIへ通信する到達性は別に確認する。

### 6.2 Termux内部でCodex CLIを起動する（オプション）

**可能性はあるが、公式CLIのAndroid互換性は未保証。** TermuxはNode.js/npmを利用できるが、CodexはRust製のnative binaryを含む。Androidの <code>process.platform=android</code>、Bionic libc、npm optional dependency、ファイルロックの差異で動作しない報告がある。

- [公式Codex CLI](https://github.com/openai/codex)
- [Android ARM64 optional dependency問題 #37262](https://github.com/openai/codex/issues/37262)
- [Android lock()問題 #26277](https://github.com/openai/codex/issues/26277)
- [Termuxネイティブの調査報告 #11809](https://github.com/openai/codex/issues/11809)

~~~sh
pkg install nodejs-lts npm git gh openssh
node -p 'process.platform + "/" + process.arch'
node --version
npm --version
# 公式パッケージの互換性を先に検証
npm install -g @openai/codex
codex --version
~~~

公式パッケージで動作しない場合、[calibancode/codex-termux](https://github.com/calibancode/codex-termux)、[DioNanos/codex-termux](https://github.com/DioNanos/codex-termux)、[bash0816/Codex-Termux](https://github.com/bash0816/Codex-Termux)など**非公式**Android ARM64向け移植例がある。

~~~sh
# 以下は非公式フォークの導入例。利用前にソース差分・配布バイナリを審査すること
npm install -g @mmmbuto/codex-cli-termux@latest
codex --version
~~~

**重大な注意:** 非公式CodexのネイティブバイナリにChatGPT認証・GitHub private repo・SSH鍵へのアクセスを許すことになる。配布元・upstreamとの差分・ビルド成果物を監査し、限定権限の検証環境でのみ試す。sandbox無効化や承認の全面回避を「動作するため」として採用しない。**標準運用はPC側の公式Codex**。

### 6.3 Codexの認証とGitHubの認証

CodexがTermuxで起動できた場合、ブラウザーcallbackを期待するより[Codex公式のdevice code認証](https://learn.chatgpt.com/docs/auth)が適している。アカウント/ワークスペース側でdevice code loginを許可してから:

~~~sh
codex login --device-auth
# 表示されたURL/コードをPC・Questブラウザー・スマートフォンで入力する
codex login status
~~~

API key方式もあるが秘密情報・課金・保管を別途設計する。**ChatGPT/Codex認証とGitHub認証は独立**。

Termuxには[GitHub CLI <code>gh</code>の公式パッケージ](https://github.com/termux/termux-packages/blob/master/packages/gh/build.sh)が存在する。

~~~sh
pkg install git gh openssh
 gh auth login -h github.com -p https -w
# Questでブラウザー遷移できない場合は表示されたdevice codeをPC側ブラウザーで承認
 gh auth status
 gh repo clone tin-machine/eidolon-wayland
cd eidolon-wayland
git status
~~~

Tokenやauth.json、秘密鍵はシェル履歴・Git・logcatへ書き込まない。QuestからGitHubへ接続できるネットワークも必要。

### 6.4 採用判断

| 比較 | **PC公式Codex＋ADB/SSH（主案）** | Quest Termux Codex（検証案） |
| --- | --- | --- |
| 互換性 | Linuxの公式バイナリ | Android nativeと依存の問題あり |
| Termux調査 | SSHコマンド・ファイル転送 | Termux内で直接 |
| GitHub認証 | PC側に集約可 | Quest側にも必要 |
| APKビルド | PCでネイティブ実行 | QuestでのAPKビルドは別問題 |
| USBを抜いた後 | Wi-Fi ADBを接続してforwardを再設定すれば継続可能。採用OSで実測する | Quest単体の調査が可能になる余地 |
| リスク | ADB権限・SSH鍵管理 | 非公式バイナリへの資格情報委託、Android寿命制限 |

**Phase 0の合格にQuest側Codexの動作を必須にしない。** 動作試験・fork選定は任意の独立スパイクとして記録。

## 7. Phase 0完了条件と観測記録

USBでの開発基準とWi-Fi通常運用の採用判定を別欄で管理する。無線の未了項目は未了のまま残し、USB基準を満たした部分から後続フェーズへ進める。以下はすべて未実施で、資料上の対応確認を実機成功として数えない。

- [ ] UbuntuでJDK17、SDK/NDK、Gradle wrapper、Meta SDKのバージョンを確認。
- [ ] Quest 3でDeveloper Mode/USB承認、<code>adb devices -l</code>が<code>device</code>。
- [ ] Quest ABI/API/Horizon OS buildを<code>adb shell getprop</code>で取得。
- [ ] Ubuntu上で上流StarterSampleをビルド・XR起動するか、Linux CLIの具体的な障害を記録して代替の最小Spatial APKを実証。
- [ ] Eidolon独自の最小APKを追加。Ubuntuで<code>assembleDebug</code>し、USBで<code>adb install</code> → XR起動 → <code>logcat</code>の基準結果を再現できる。
- [ ] 基本CI（Gradle compile/静的検査、秘密情報除外、ライセンス確認）を用意。
- [ ] 第5.2節の初回実装として、Rust由来の版情報表示とGradleからのnative成果物生成・同梱を確認し、ビルド成功と実機成功を分けて記録。
- [ ] [最小smoke](development-automation-and-debugging.md#実機smokeの実装案)でserial・build情報・起動結果・JNI readiness・ログ・capture可否をrun単位で保存。自動判定とHMD確認の範囲を分け、timeout／失敗時にも証跡を残す。
- [ ] Termuxを準備した環境では、USBでADB forward＋SSHの開発経路を確認。未準備の場合は未実施として残し、APK更新の検証は先行できる。Quest側Codexは任意。
- [ ] USB-Cを給電器へ挿し替えた状態でWi-Fi経由のAPK更新・install、起動、ログ、captureを確認。Termux準備済みの場合はADB forward＋SSHも確認。
- [ ] 30〜60分のWi-Fi連続運用で給電条件、残量・温度、接続と診断の欠落を記録。
- [ ] sleep／wake、Quest reboot、host再接続の結果とUSBを含む復旧条件を個別に記録し、Wi-Fi運用の採用・条件付き採用・保留を判定。
- [ ] **Android Waypipe/Smithayは未完成**であることを後続フェーズへ引き継ぐ。

~~~text
Date / Ubuntu / JDK / Android Studio:
ANDROID_HOME / SDK platform / Build Tools / NDK / Gradle / AGP / Kotlin / Meta SDK:
Spatial Editor CLI Linux version/path:
Quest device / Horizon OS / API / ABI:
adb devices -l / USB cable / authorization:
Wi-Fi IP:port / host / authorization identity (no private key contents):
USB baseline result / Wi-Fi adoption result / pending conditions:
Upstream sample SHA / CLI execution / scene export:
StarterSample Gradle / install / XR result:
Eidolon APK build/install/launch/logcat (USB and Wi-Fi separately):
APK revision / Rust revision / JNI result:
Smoke run ID / device serial / readiness / timeout / result:
Capture tool/version/method / HMD comparison / untested items:
XR focus/session changes / Termux background SSH (if available):
Termux distribution / node/gh/sshd:
USB and Wi-Fi adb forward / SSH:
Wi-Fi run duration / charger and cable / battery start-end / temperature source and trend:
Sleep-wake / Quest reboot / host reconnect / forward recreation / USB recovery needed:
Quest-native Codex (optional):
Failing commands / log excerpts (secrets removed):
~~~

## 8. 参考実装・公式資料

- [Meta: Test your app on your device](https://developers.meta.com/vr/documentation/android-apps/enable-developer-mode/)
- [Meta: StarterSample](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-sample-starter/)
- [Meta: Linux Spatial Editor CLI](https://developers.meta.com/vr/downloads/package/meta-spatial-editor-cli-for-linux/)
- [Meta: Spatial SDK Gradle/CLI設定](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-editor/)
- [Android: adb forward](https://developer.android.com/tools/adb#forwardports)
- [Android: sdkmanager](https://developer.android.com/tools/sdkmanager)
- [Meta Spatial SDK official samples](https://github.com/meta-quest/Meta-Spatial-SDK-Samples)
- [OpenAI Codex](https://github.com/openai/codex)
- [OpenAI device code login](https://learn.chatgpt.com/docs/auth)
- [Termux official packages](https://github.com/termux/termux-packages)

検証結果は本書に記録し、上位の[実装計画](implementation-plan.md#phase-0--レポジトリと開発環境)のPhase 0完了状況にも反映する。

## 9. Rust中心構成のビルド調査（2026-11-01追記）

### 9.1 採用する境界

**Kotlin APK + Rust cdylib**を第一案にする。KotlinはSpatial SDK／Android adapter、RustはJNI、描画、SSH、compositor、WindowManagerを担当する。RustからJNIやNDKを呼べるため、C++のwrapperを最初から挟む必要はない。[jni公式](https://docs.rs/jni/0.22.4/jni/)と[ndkのNativeWindow API](https://docs.rs/ndk/0.9.0/ndk/native_window/struct.NativeWindow.html)を参照。

Rustを採用してもAndroid NDKのlinker／sysrootは必要。CMakeはRustのcdylibを作るだけなら必須ではないが、Waypipeやnative依存のビルドでは別のbuild systemを利用し得る。

~~~mermaid
flowchart LR
  K["Kotlin / Gradle"] --> A["APK packaging"]
  R["Rust / Cargo + cargo-ndk"] --> S["libeidolon_native.so / arm64-v8a"]
  S --> A
  A --> Q["Quest APK"]
  Q -->|"System.loadLibrary / JNI"| N["Rust core"]
~~~

### 9.2 確認した部品とバージョン

| 部品 | 調査時の版／場所 | Phase 0で確認すること |
| --- | --- | --- |
| jni | 0.22.4 | Kotlin native methodをRustで実装できる。0.21系とAPIを混ぜない |
| ndk | 0.9.0 | Surface→NativeWindow、ffi型の整合、所有権 |
| khronos-egl | 6.0.0 | Androidの`libEGL.so`へ接続 |
| glow | 上流crateを選定・固定 | GLES binding。context生成はEGL側 |
| Smithay | 0.7系を評価 | native依存と最小features。Android動作は別gate |
| russh | 0.65.0 | MSRV 1.89、暗号backendのAndroidクロスビルド |

これは動作確認済みの組み合わせではない。`rust-toolchain.toml`とCargo.lockへ**実際に成功した組み合わせ**を固定する。[russh Cargo.toml](https://github.com/Eugeny/russh/blob/main/russh/Cargo.toml)、[jni migration guide](https://docs.rs/crate/jni/0.22.4/source/docs/0.22-MIGRATION.md)を参照。

### 9.3 Rustビルドの具体例

将来`native/`にcrateを作成し、`[lib] name = "eidolon_native"`、`crate-type = ["cdylib"]`とJNI関数を追加した後の例。**この変更では実装コードを追加していない。**

~~~bash
# Ubuntu側。NDK_PATHはインストール・検証したNDKの絶対パス
export ANDROID_NDK_HOME="${ANDROID_HOME}/ndk/27.0.12077973"  # 例
rustup target add aarch64-linux-android
cargo install cargo-ndk --locked

# native/Cargo.tomlとCargo.lockが作成済みの場合
cd eidolon-wayland/native
cargo tree --target aarch64-linux-android -e features
CARGO_NDK_PLATFORM=34 cargo ndk -t arm64-v8a \
  -o ../android/app/src/main/jniLibs build --locked

# 初期診断用。名前はcrateの[lib] nameに合わせる
file ../android/app/src/main/jniLibs/arm64-v8a/libeidolon_native.so
cd ../android
./gradlew :app:assembleDebug
unzip -l app/build/outputs/apk/debug/app-debug.apk | grep libeidolon_native.so
~~~

`cargo-ndk`はjniLibs形式への出力とNDK環境設定を提供する。[公式README](https://github.com/bbqsrc/cargo-ndk)を参照。実運用ではcargo-ndk自身の版も固定する。GradleからCargo taskを呼ぶ場合、入力・出力・debug/releaseを宣言し、古い`.so`を誤って再利用しない。SDK APIレベルはアプリのminSdkと整合させる。

### 9.4 切り分け用のビルド順序

1. JNIだけのcdylibをKotlinから呼ぶ。
2. `ndk`＋EGL/GLESを追加し、[Phase 1](phase-1-rust-rendering.md)のSurface描画を試す。
3. `russh`だけを別spikeでAndroid向けにビルドし、[Phase 2](phase-2-rust-ssh.md)を試す。
4. Smithayのprotocol最小featuresとlibxkbcommon等を追加し、[Phase 3](phase-3-rust-compositor-waypipe.md)を試す。

- [ ] `System.loadLibrary("eidolon_native")`と単純なJNI呼出しがQuestで成功する。
- [ ] Rust MSRV、NDK版、native依存、JNI版の混在を記録する。
- [ ] `cargo tree`／ELFのNEEDEDからUbuntu用`.so`の誤リンクがないことを確認する。
- [ ] 既存C依存の利用と、自作C++ shimの追加を区別する。shimは具体的な不足を確認した部分に限定する。
- [ ] APKのビルド成功とXRの動作成功を別に記録する。
