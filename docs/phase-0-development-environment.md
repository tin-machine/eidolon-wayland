# Phase 0 — Ubuntu開発PC・Quest 3・Termuxの開発環境

**Status:** Planning（2026-11-01、Quest実機では未検証）  
**上位計画:** [implementation-plan.md — Phase 0](implementation-plan.md#phase-0--レポジトリと開発環境)  
**全体設計:** [architecture.md](architecture.md)

本ファイルは **Phase 0の実行手順・調査事項の正本**。上位planには概要と合格条件を残す。現状のレポジトリにはまだAndroidアプリ本体がない。以下の <code>android/</code> やAPKのパスは、後に実装するまで存在しない。

**Rust中心構成の調査結果を[第9節](#9-rust中心構成のビルド調査2026-11-01追記)に追記した。** Kotlin＋Rustを主要言語とし、C++は必要性が実証されたshimに限定する。描画は[Phase 1](phase-1-rust-rendering.md)、SSHは[Phase 2](phase-2-rust-ssh.md)、compositor移植は[Phase 3](phase-3-rust-compositor-waypipe.md)へ続く。

## 1. ゴールとUSB接続構成

**Phase 0の目標:** Ubuntu PCでMeta Spatial SDKを利用した最小APKをビルドし、USB経由でQuest 3へデプロイし、XR起動とlogcat取得までを再現可能にする。Termux/Waylandとの製品内通信はPhase 2〜5の対象。ただし**開発用USB-ADB/SSH経路**はPhase 0で先行構築できる。

~~~mermaid
flowchart LR
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
 ADB <-->|"USB-C / ADB"| AD
 FWD <-->|"USB-C / ADB"| AD
~~~

**区別:** 上のSSHは**開発PC→Termuxへの調査用**。Eidolon APK→Termuxへのlocalhost SSH＋Waypipeという製品構成は[architecture.md](architecture.md)に記載。USB/ADBは製品実行時に不要とする。

## 2. ビルドツールの基準

上流[Meta-Spatial-SDK-Samples README](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/main/README.md)と[StarterSampleのversions](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/main/StarterSample/gradle/libs.versions.toml)、[Gradle wrapper](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/main/StarterSample/gradle/wrapper/gradle-wrapper.properties)で2026-11-01に確認した値を基準にする。

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
sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"
sdkmanager --licenses
~~~

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

[Spatial SDK Gradle Plugin](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-editor/)の <code>spatial.scenes.cliPath</code> に実際のCLIパスを指定する。上流StarterSampleがこれだけで通ることを保証しない。シーンのexport taskとassembleDebugを分離し、エラーログで「CLI不在」「CLI実行失敗」「export内容不整合」を判別する。

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
3. **データ通信対応**USB-CケーブルでQuestとUbuntuを接続する（充電専用ケーブル不可）。
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

期待値: <code>adb devices -l</code>に<code>device</code>。<code>unauthorized</code>はHMD内のUSB承認を確認。<code>offline</code>や不安定な接続はケーブル、ADBの二重インストール、udev権限を確認。Linux用udevルールは[Android公式 device setup](https://developer.android.com/studio/run/device#setting-up)に従う。複数端末なら <code>adb -s SERIAL ...</code> で指定。

## 5. 実際にAPKをビルド・デプロイする

### 5.1 先にMeta公式StarterSampleを再現

~~~bash
mkdir -p ~/src
git clone https://github.com/meta-quest/Meta-Spatial-SDK-Samples.git \
  ~/src/Meta-Spatial-SDK-Samples
cd ~/src/Meta-Spatial-SDK-Samples/StarterSample
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

### 5.2 Eidolon専用の空APK（未実装）

- [ ] 公式サンプルのライセンス/構成を確認し、Eidolon固有applicationId・AppSystemActivity・registerFeatures・registerPanelsを用意する。
- [ ] Linux CLIが障害なら、scene exportが不要な構成を検証する。Scene Editorの依存を盲目的に削除しない。
- [ ] Gradle wrapper/JDK/SDK/Kotlin/AGP/Meta SDKを固定しCIでassembleDebugを実行する。
- [ ] Quest実機へinstallして起動、logcat、クラッシュとXR描画を検証する。
- [ ] 必要になるまでcompositorやrendererを統合しない（Phase 1/3の責務）。Rustのcdylibロードだけは第9節で先行検証してよい。

実装後の**想定パス（現在存在しない）**:

~~~bash
git clone git@github.com:tin-machine/eidolon-wayland.git
cd eidolon-wayland/android
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
~~~

## 6. Quest内Termuxを調査する方法

### 6.1 **推奨：PC側の公式Codex → USB ADB forward → Termux SSH**

ADBの[port forwarding](https://developer.android.com/tools/adb#forwardports)を使い、Ubuntuのlocalhost:18022をQuestのTCP:8022へUSB経由でつなぐ。**PC側のCodex CLIがsshを実行する**のでQuestにCodex CLIをインストールする必要はない。

~~~mermaid
flowchart LR
 subgraph HOST["Ubuntu PC"]
  C["Codex CLI"] --> SSH["ssh -p 18022"]
  SSH --> FWD["adb forward tcp:18022 tcp:8022"]
 end
 FWD <-->|"USB ADB"| AD["Quest adbd"]
 AD --> SS["Termux sshd: 127.0.0.1:8022"]
 SS --> SH["Termux shell: ls / pkg / git / ps"]
~~~

**Termux側（Quest内でTermuxを起動してから）**:

~~~sh
pkg update
pkg install openssh git gh nodejs-lts
whoami
id
mkdir -p ~/.ssh
chmod 700 ~/.ssh
# 開発PCの公開鍵を ~/.ssh/authorized_keys に登録してchmod 600
# SSH待受をloopbackに限定
sshd -p 8022 -o ListenAddress=127.0.0.1
ss -lnt | grep 8022
~~~

**PC側**:

~~~bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/quest_termux_ed25519" -C eidolon-quest
# 上の公開鍵をTermuxに登録する（安全な初回セットアップ）
adb devices -l
adb forward --list
adb forward tcp:18022 tcp:8022
# TERMUX_USERNAME はTermuxの whoami 結果で置換する
ssh -p 18022 -o IdentitiesOnly=yes -i "$HOME/.ssh/quest_termux_ed25519" \
  TERMUX_USERNAME@127.0.0.1 'uname -a; id; pwd'
adb forward --remove tcp:18022
~~~

<code>$PREFIX/etc/ssh/sshd_config</code>で公開鍵のみ（<code>PasswordAuthentication no</code>）、loopback待受と設定を確認。<code>sshd -T</code>と<code>ss -lnt</code>で実際の状態を確認し、秘密鍵はGitへコミットしない。初回SSH host keyはTermuxの <code>ssh-keygen -lf $PREFIX/etc/ssh/ssh_host_ed25519_key.pub</code> などの指紋と照合して受け入れる。<code>StrictHostKeyChecking=no</code>での迂回はしない。

**Codexへ渡す指示:** PC上のリポジトリで公式Codexを使い、「<code>adb devices</code>と<code>adb forward --list</code>を確認してから、<code>ssh -p 18022 …</code>でQuest Termux内のOS・pkg・Node・OpenSSH・Waypipeを**まず読み取り専用で調査**。削除、設定変更、認証情報閲覧は許可を求める」と依頼する。Codexのsandbox/コマンド承認でsshが制限される場合は適切な権限を調整。

**ADB shellの限界:** <code>adb shell getprop</code>・<code>adb logcat</code>・<code>adb shell pm list packages</code>は使用可能。しかしAndroid別UIDのTermuxのprivate homeへ通常の<code>adb shell</code>からアクセスはできず、release版Termuxに<code>run-as com.termux</code>が通るとも限らない。Termux内部調査はSSHで行う。

**USBはQuestのインターネット回線ではない。** USB/ADB SSHが成立しても、QuestからGitHub/OpenAI APIへ通信するには別途ネットワーク到達性が必要。

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
| USBを抜いた後 | SSH経路は消失 | Quest単体の調査が可能になる余地 |
| リスク | ADB権限・SSH鍵管理 | 非公式バイナリへの資格情報委託、Android寿命制限 |

**Phase 0の合格にQuest側Codexの動作を必須にしない。** 動作試験・fork選定は任意の独立スパイクとして記録。

## 7. Phase 0完了条件と観測記録

- [ ] UbuntuでJDK17、SDK/NDK、Gradle wrapper、Meta SDKのバージョンを確認。
- [ ] Quest 3でDeveloper Mode/USB承認、<code>adb devices -l</code>が<code>device</code>。
- [ ] Quest ABI/API/Horizon OS buildを<code>adb shell getprop</code>で取得。
- [ ] Ubuntu上で上流StarterSampleをビルド・XR起動するか、Linux CLIの具体的な障害を記録して代替の最小Spatial APKを実証。
- [ ] Eidolon独自の最小APKを追加。<code>assembleDebug</code> → <code>adb install</code> → XR起動 → <code>logcat</code>が再現できる。
- [ ] 基本CI（Gradle compile/静的検査、秘密情報除外、ライセンス確認）を用意。
- [ ] 必要に応じUSBのADB forward＋Termux SSH経路を実験。Quest側Codexは任意。
- [ ] **Android Waypipe/Smithayは未完成**であることを後続フェーズへ引き継ぐ。

~~~text
Date / Ubuntu / JDK / Android Studio:
ANDROID_HOME / SDK / NDK / Gradle / AGP / Meta SDK:
Spatial Editor CLI Linux version/path:
Quest device / Horizon OS / API / ABI:
adb devices -l / USB cable / authorization:
StarterSample Gradle / install / XR result:
Eidolon APK build/install/launch/logcat:
Termux distribution / node/gh/sshd:
USB adb forward / SSH:
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
