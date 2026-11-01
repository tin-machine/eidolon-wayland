# Eidolon Wayland — 実装計画・検証項目

Status: **Planning / 実装未着手**  
Architecture: [architecture.md](architecture.md)  
Phase 0 runbook: [phase-0-development-environment.md](phase-0-development-environment.md)  
Target: **Quest 3単体 + Termux + localhost SSH + Waypipe + Spatial SDKアプリ内Wayland compositor**

## 成功の定義

最小MVP: Quest 3にインストールしたEidolon APKを起動し、**同じQuest上のTermuxで動くfoot**を、VR空間内の独立した長方形パネルとして表示する。Bluetoothキーボードでshell/Neovimに入力でき、コントローラーでクリック、画面の再描画、リサイズ、接続の再確立が行える。

- **ネットワークに依存しない**: アプリ実行と表示にはPC・Wi-Fi・クラウドは不要。localhost SSHのみ。開発・依存取得・Codexの利用に必要な回線とは区別する。
- **非特権**: Questのroot化、OSの置換、カーネルモジュールの導入なし。
- **既存のターミナルを再利用**: footをWayland clientとして動かし、ターミナル描画は再実装しない。
- **分離したXRパネル**: 最初は1画面、その後2枚以上の独立したxdg_toplevelへ。
- **PoC段階はCPUコピーを許容**: 低遅延/ゼロコピーはMVP合格要件にしない。

## Rust中心構成の調査と詳細ファイル（2026-11-01）

**新規実装はKotlin＋Rustを中心にする。** KotlinはMeta Spatial SDKとAndroidのadapter、RustはJNI、描画、SSH、Wayland compositor、XRウィンドウ管理モデルを担当する。Waypipeは上流を再利用し、自作C++は具体的な不足が確認された部分の薄いshimに限定する。[全体の責務分担](architecture.md)を参照。

| フェーズ | 詳細ファイル | 今回の調査・判断 |
| --- | --- | --- |
| 0 | [開発環境](phase-0-development-environment.md) | cargo-ndkによるcdylibビルド、APK同梱、JNI疎通を追記 |
| 1 | [Rust JNI・Surface描画](phase-1-rust-rendering.md) | jni / ndk / khronos-egl / glowで独立した描画spike |
| 2 | [Rust SSH](phase-2-rust-ssh.md) | russhを第一候補。StreamLocal APIとchannel bridgeの実装方法 |
| 3 | [Rust compositorとWaypipe](phase-3-rust-compositor-waypipe.md) | Android上のSmithay先行例、native依存、Waypipeの再利用 |
| 4 | [Rustウィンドウ描画](phase-4-rust-window-rendering.md) | GlesRenderer、Android EGL adapter、bufferとthreadの所有権 |
| 5 | [Rustウィンドウ管理](phase-5-rust-window-manager.md) | Rustが状態の正本、KotlinがSDK操作を適用 |
| 6 | [Rustライフサイクルと検証](phase-6-rust-lifecycle-testing.md) | owner thread、切断・Surface再生成、性能／耐障害試験 |

Phase 1〜6の詳細ファイルは今回新規作成した。概要・全体の進行順は本書、API調査・接続の詳細・個別検証は各ファイルを正本とする。**資料とソースの調査までであり、EidolonのAndroidビルドやQuest実機テストはまだ行っていない。**

確認できた部品は[jni](https://docs.rs/jni/0.22.4/jni/)、[NDK NativeWindow](https://docs.rs/ndk/0.9.0/ndk/native_window/struct.NativeWindow.html)、[russhのStreamLocal API](https://docs.rs/russh/latest/russh/client/struct.Handle.html#method.streamlocal_forward)。Androidの先行例は[Local Desktop](https://github.com/localdesktop/localdesktop.github.io)。これらの存在から実装方針は具体化できるが、組み合わせの実機成功までは保証しない。

## 追加レビュー（2026-11-01）

Eidolonの`main`を`64fcf5653a1aa614ba4ba6f47432c5d9f87a8951`で再確認した時点では、READMEと設計文書のみで、APK/Rustの実装や実機検証記録はなかった。今回の更新も**公式資料・実装ソースの調査結果と検証計画の追記**であり、完了チェックを進めるものではない。Kotlin adapter＋Rust core、APK内compositor、SSH＋Waypipeという主案を維持する。

| 追加確認・提案 | 根拠と反映先 |
| --- | --- |
| SDK SurfaceへEGLを接続する公式実装がある | [Meta SurfaceUtil](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/f233e2327b95f9871b75bdba867d6fdd726f07cc/PremiumMediaSample/app/src/main/java/com/meta/spatial/samples/premiummediasample/SurfaceUtil.kt)。Kotlinによる一回のclear処理で、Rust連続描画は未検証。[Phase 1](phase-1-rust-rendering.md) |
| AGP 8.11系のSDK Build Tools最低/既定値は35.0.0 | [Google互換表](https://developer.android.com/build/releases/agp-8-11-0-release-notes)。[Phase 0](phase-0-development-environment.md)の34.0.0導入例を訂正。compileSdkとは別の値 |
| VideoSurfacePanelRegistrationはlayer専用で、枚数の予算が必要 | [Meta layer/mesh解説](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-2dpanel-layers/)。[compositor資料](https://developers.meta.com/vr/documentation/spatial-sdk/os-compositor-layers/)の16 layers記述は資料更新日2024-11-06。実際の可用枚数は未検証。[Phase 4](phase-4-rust-window-rendering.md)・[Phase 5](phase-5-rust-window-manager.md) |
| Activity lifecycleとXR session focusを区別する | [公式MediaPlayerSample](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/f233e2327b95f9871b75bdba867d6fdd726f07cc/MediaPlayerSample/app/src/main/java/com/meta/spatial/samples/mediaplayersample/MediaPlayerSampleActivity.kt)の`onSessionStateChanged`。[Phase 1](phase-1-rust-rendering.md)・[Phase 5](phase-5-rust-window-manager.md)・[Phase 6](phase-6-rust-lifecycle-testing.md) |
| APK自身のUIDでのhelper起動を早めに試す | [Android実行制約](https://developer.android.com/about/versions/10/behavior-changes-10#execute-permission)。[Phase 3](phase-3-rust-compositor-waypipe.md)のP3-BをPhase 0〜1から先行可能にする。Wawonaの同梱ソースも参考として追記 |

**次の実装単位:** Ubuntuで最小Eidolon APKをビルドし、QuestのXRパネルにRustから返したbuild情報を表示する。Gradle→Cargo→APK同梱、基本CI、実機観測記録までをPhase 0で揃える。crate分割や診断画面の具体的な構成は実装者が調整できる。

## 最初に潰すべき不確実性（スパイク）

| 優先度 | 検証 | 合格条件 | 不成立時 |
| --- | --- | --- | --- |
| P0 | 空のSpatial SDK APKをQuest実機でビルド・起動 | immersiveシーンを表示できる | SDK/Androidビルド環境を最小化 |
| P0 | Kotlin→Rust cdylibのJNI疎通 | 新規C++なしでnative methodを呼べる | jni版、ABI、APK同梱・R8設定を切り分け |
| P0 | VideoSurfacePanelRegistration + Rust EGL/GLES | テストパターンをXRパネルへ連続描画できる | EGL adapter／Surface APIを比較 |
| P0 | Android/aarch64にSmithayを組込む | wl_shmを扱う最小compositorが起動 | Local Desktopのpatchとnative依存を比較。必要ならC ABIの代案 |
| **P0** | **Android側Waypipeの同梱・起動（P3-BをPhase 0〜1から先行）** | **APK自身のUIDで起動・転送socket待受・終了回収ができる** | packaging、prefix、依存、spawnを切り分ける。compositorとの結合はPhase 3 |
| P0 | Rust russh→Termux localhost SSH | host key検証＋公開鍵認証でexecできる | 暗号backend／NDK依存、必要なら他SSH実装を比較 |
| P0 | russhのStreamLocal forwarding | Unix socket echoが双方向に通る | callback、server設定、channel bridgeを切り分け |
| P0 | Waypipe両端 + Wayland client | surfaceとbufferを受け取り、入力も返せる | protocol/版/機能差を解析 |
| P0（初期観測）/ P1（長時間） | Questのバックグラウンド制限 | 最小XR APKを前面にしてsshdと検証用プロセスの存続を確認し、foot導入後・Phase 6で再確認 | Quest固有の適用条件と停止原因を調べ、再接続・Termux保護手順を設計 |
| P1 | 複数パネルのlayer使用数と可用枚数 | 採用SDK/OSで使用layer数を観測し、表示対象数と資源割当を区別できる | 非表示windowの扱い・描画方式を比較。1〜2枚のMVPは維持 |

> SSHでexecできること、Waypipeのtransportを中継できること、Wayland clientが描画できることは別の検証項目。単独spikeを合格させてから結合する。

Phase番号は全作業を直列に実行する制約ではない。Phase 0のAPK/JNI基盤から、**Phase 1のSurface描画、Phase 2のSSH/echo、Phase 3のSmithay最小構成とhelper起動**を独立して進められる。Phase 3でtransportとprotocolを結合し、Phase 4で描画へ接続する。Surfaceと入力focusの寿命管理は各実装時点から扱い、長時間・性能試験をPhase 6に残す。

## フェーズ別通信図の読み方

以下の図は**各フェーズ完了時に目指す接続**であり、実装済み構成ではない。図中のQuestはすべて同一Quest 3内。Phase 0にはAPKビルド/ADB用の開発PCが登場する。Unix domain socket、SSH転送、同一APK内JNI、Android Surfaceを区別する。

**Waypipeの名前の注意:** 表示側APKは`waypipe client`、アプリ側Termuxは`waypipe server`。Waylandでは逆にEidolonがserver/compositor、footがclientとなる。標準の`waypipe ssh`はSSHのリモートUnixソケット転送を利用する。[付属マニュアル](https://man.archlinux.org/man/waypipe.1.en)を参照。

Rust版SSHでは、`russh::streamlocal_forward`を要求し、受信channelをAPKのWaypipe転送socketへbridgeする。**Wayland socketを生のバイトコピーで転送するのではなく、Waypipeの転送用streamをSSHへ載せる。** 詳細は[Phase 2](phase-2-rust-ssh.md)。

## Phase 0 — レポジトリと開発環境

**詳細runbook:** [phase-0-development-environment.md](phase-0-development-environment.md)。Ubuntu環境構築、APKビルド、USBデバッグ、TermuxへのADB/SSH、Codex/認証/GitHub連携に加え、今回Rust cdylibのビルドとJNI疎通を追記した。

### Phase 0の接続図 — 開発PCからQuestへのAPK投入

~~~mermaid
flowchart LR
  DEV["開発PC: Gradle / JDK / Android SDK + Cargo"] -->|"build"| APK["Kotlin APK + Rust cdylib"]
  APK -->|"adb install / USB-C"| QUEST["Quest 3: XR Activity"]
  QUEST -->|"adb logcat"| DEV
  DEV -.->|"任意: adb forward 18022 → 8022 / SSH"| T["Quest 3: Termux sshd"]
~~~

**接続境界:** このADB/USB/SSHは開発・調査時限定。APK自体からTermuxへ接続する製品用SSH/Waypipeは後続フェーズ。UbuntuのSpatial Editor CLIとscene exportの制約もrunbookに記載。

- [ ] JDK / SDK / Gradle wrapper / Meta SDKを確認する。
- [ ] Developer ModeとUSB debugを有効化し、ADBで接続する。
- [ ] 公式StarterSampleまたは検証済みの代替最小サンプルをビルド・実機起動し、ログを採取する。
- [ ] Eidolon固有の最小APKを作り、ビルド・インストール・起動・診断を再現可能にする。
- [ ] Rust cdylibを同梱し、Kotlinから単純なJNI呼出しを試す。renderer/compositorはまだ必須にしない。
- [ ] Gradle→Cargo→APK同梱を一つのビルド操作へまとめる。APK側とRust側のbuild revisionをパネル等で確認できるようにし、古い`.so`の混入を見分ける。
- [ ] 必要ならPC側の公式CodexからADB forward＋SSHでTermuxを調査する。Quest内Codexの動作は任意。
- [ ] 最小XR APKでActivity lifecycleとXR session focusを観測し、Termuxを準備した環境ではsshd/検証用プロセスの背景存続を短く試す。詳細・長時間試験はPhase 6へ引き継ぐ。
- [ ] CI、バージョン固定、ライセンス、secret除外を整備する。

参考：[Meta StarterSample](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/tree/main/StarterSample)、[Android NDK Samples](https://github.com/android/ndk-samples)、[cargo-ndk](https://github.com/bbqsrc/cargo-ndk)。

## Phase 1 — Spatial Surfaceの描画スパイク

**詳細・調査結果:** [phase-1-rust-rendering.md](phase-1-rust-rendering.md)。Rust用JNI/NDK/EGL/GLESの部品を確認した。KotlinからSDKのSurfaceをRustへ渡し、`khronos-egl + glow`で描画する方針。新規C++は必須にしない。

### Phase 1の描画経路 — Termuxはまだ接続しない

~~~mermaid
flowchart LR
  subgraph APP["Quest 3: Eidolon APK"]
    SDK["Kotlin: Spatial SDK panel"] -->|"Surface / JNI"| N["Rust: ndk NativeWindow"]
    N --> EGL["Rust: EGL window surface"]
    PAT["Rust: glow / テストパターン"] -->|"GLES描画"| EGL
    EGL -->|"eglSwapBuffers"| SURF["SDK提供のAndroid Surface"]
  end
  SURF --> XR["XR空間の1枚のパネル"]
~~~

**接続境界:** KotlinとRustは同じAPK内。Termux、SSH、Waylandは不使用。別のNativeActivity/winit event loopを起動せず、Spatial SDKが管理するSurfaceへ接続する。

参考：[Meta MediaPlayerSample](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/main/MediaPlayerSample/app/src/main/java/com/meta/spatial/samples/mediaplayersample/MediaPlayerSampleActivity.kt)、[jni](https://docs.rs/jni/latest/jni/)、[NDK NativeWindow](https://docs.rs/ndk/latest/ndk/native_window/struct.NativeWindow.html)、[khronos-egl](https://docs.rs/khronos-egl/latest/khronos_egl/)、[glow](https://github.com/grovesNL/glow)。[Android gles3jni](https://github.com/android/ndk-samples/tree/main/gles3jni)はAPI接続の参考であり、C++採用を必須にするものではない。

作業:
- [ ] immersive sceneに一枚のパネルを作成する。
- [ ] surfaceConsumerのSurfaceをJNI経由でRustへ渡し、生成・破棄を追跡する。
- [ ] NativeWindow/EGL/GLESでcheckerboard、単色、フレームカウンタを描画する。
- [ ] pixel size、物理サイズ、DPI、視距離、render modeと可読性を記録する。
- [ ] 専用render threadにcontextの所有権を置き、JNI参照とSurface世代を正しく管理する。
- [ ] 拡縮、位置移動、Surface再生成、pause/resumeへの耐性を確認する。
- [ ] producer側のbuffer寸法を指定・実測し、XR上の物理寸法変更とは別に試す。Universal Menuの`FOCUSED ↔ VISIBLE`とActivity/Surfaceのイベント差を記録する。
- [ ] CPU/メモリ/フレーム時間の測定点を設ける。

合格: Rust描画のパネルが実機で表示され、再生成後も更新できる。crateビルド成功だけでは合格にしない。

## Phase 2 — SSHとTermuxの安全な接続

**詳細・調査結果:** [phase-2-rust-ssh.md](phase-2-rust-ssh.md)。`russh + Tokio`を第一候補にする。SSH execに加えてStreamLocalの要求／incoming channel処理APIを確認した。ただしAndroidビルドとTermux接続は未検証。

### Phase 2の通信経路 — localhost SSHを先に成立させる

~~~mermaid
flowchart LR
  subgraph QUEST["同一Quest 3"]
    subgraph APK["Eidolon APK / UID A"]
      UI["Kotlin: 認証確認UI"] <-->|"JNI"| CLIENT["Rust: russh / host key検証"]
    end
    subgraph TERMUX["Termux / UID B"]
      SSHD["sshd 127.0.0.1:8022"] -->|"exec"| CMD["id / echo / socket test"]
    end
    CLIENT <-->|"TCP loopback / SSH"| SSHD
    CMD -->|"stdout / stderr / exit status"| SSHD
  end
~~~

**接続境界:** 別UIDをloopback SSHで越える。APKにはネットワーク権限が必要。Waylandはまだ接続せず、exec、次にUnix socket echoで切り分ける。

参考：[russh Handle API](https://docs.rs/russh/latest/russh/client/struct.Handle.html)、[Handler API](https://docs.rs/russh/latest/russh/client/trait.Handler.html)、[Termux OpenSSH](https://github.com/termux/termux-packages/blob/master/packages/openssh/build.sh)。[SSHJ](https://github.com/hierynomus/sshj)やlibsshはfallbackとして残す。[Android Security Samples](https://github.com/android/security-samples)はKeystore等の参考。

Termux初期セットアップ例（公開鍵登録・実効設定の検証は詳細書を参照）:

~~~sh
pkg update
pkg install openssh waypipe
pkg install x11-repo
pkg install foot
id -un
# 公開鍵を ~/.ssh/authorized_keys に登録・権限を設定した後
sshd -p 8022 -o ListenAddress=127.0.0.1 -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
~~~

既にsshdが動いている場合は二重起動せず有効な設定を確認する。初期からloopbackに限定し、鍵認証を使う。鍵生成・登録はまず手動でよい。

作業:
- [ ] russhのAndroid/aarch64ビルド、暗号backend、MSRVとnative依存を確認する。
- [ ] host key検証と公開鍵認証でTermuxへ接続する。未知の鍵と不一致の鍵を区別する。
- [ ] execのstdout/stderr/exit statusと切断を取得する。
- [ ] StreamLocal forwardingでUnix socket echoを実装し、着信channelとUnixStreamを双方向にbridgeする。
- [ ] EOF、half-close、timeout、複数channel、cancelとowned socketの掃除を試す。
- [ ] Android Keystore連携は非exportable鍵とrussh APIの互換性を別spikeで検証する。
- [ ] sshdの待受、ユーザー、公開鍵認証、forwarding許可の手順を記録する。

合格: LAN露出なしで認証・execができ、Waypipeと独立したUnixソケット中継が成立する。

## Phase 3 — Wayland serverとWaypipeのPoC（最重要）

**詳細・調査結果:** [phase-3-rust-compositor-waypipe.md](phase-3-rust-compositor-waypipe.md)。Local DesktopにRust＋SmithayのAndroid先行実装がある。一方でpatchとnative依存が残るため、最小featuresから検証する。Waypipeは再利用し、Eidolonでの再実装は主案にしない。

### Phase 3の通信経路 — Wayland protocolがAPKとTermuxを往復

~~~mermaid
flowchart LR
  subgraph QUEST["同一Quest 3"]
    subgraph APK["Eidolon APK / UID A"]
      COMP["Rust: Smithay compositor"] <-->|"Wayland socket + fd"| WC["既存Waypipe client"]
      WC <-->|"Waypipe転送socket"| SSHC["Rust: russh / channel bridge"]
    end
    subgraph TERMUX["Termux / UID B"]
      SSHD["sshd 127.0.0.1:8022"] <-->|"remote StreamLocal socket"| WS["既存Waypipe server"]
      WS <-->|"Wayland socket + SHM fd"| FOOT["foot / test client"]
    end
    SSHC <-->|"SSH / TCP loopback"| SSHD
  end
~~~

**接続境界:** Waypipeがfd/SHM内容を転送・再構成する。russhはその転送streamを中継する。APK側helperの実行制約は別問題。この段階はXR表示を必須にせず、protocolとbuffer取得を確認する。

参考：[Smithay smallvil](https://github.com/Smithay/smithay/tree/master/smallvil)、[GETTING_STARTED](https://github.com/Smithay/smithay/blob/master/GETTING_STARTED.md)、[WayVR client.rs](https://github.com/wayvr-org/wayvr/blob/main/wayvr/src/backend/wayvr/client.rs)、[Local Desktopのpatch](https://localdesktop.github.io/docs/developer/patches/)、[Waypipe manual](https://man.archlinux.org/man/waypipe.1.en)、[Termuxのレシピ](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)。

作業:
- [ ] Smithayをdefault-features=falseからAndroid向けにビルドする。不要なdesktop backendを持ち込まない。
- [ ] libxkbcommonとkeymapデータ、calloop、socket、mmap/fd等の依存を検証する。
- [ ] Wayland socket、event loop、wl_compositor、wl_shm、xdg_shell、wl_seatを実装する。
- [ ] test clientがxdg_toplevelを生成・commitできるようにする。
- [ ] 必要なら既知パターンの小さなSHM clientでfd/mmapの内容を照合し、Waypipeを挟んで再確認してからfootへ進む。試験clientの選定は実装者に任せる。
- [ ] APK側WaypipeのNDK/Bionicビルド・同梱・起動を検証する。Termuxの実行ファイルの単純コピーを前提にしない。
- [ ] P3-Bの起動試験はPhase 0〜1から先行できる。同じtarget SDK/ABI/packaging設定でAPK UIDから転送socket待受と終了回収まで確認し、`adb shell`や`--version`だけの成功とは分ける。
- [ ] Phase 2のRust StreamLocal bridgeをWaypipe転送socketへ接続する。
- [ ] 両端で互換な版・機能を固定し、no-gpu/SHM経路で起動する。
- [ ] WAYLAND_DEBUGとcompositorの診断でconfigure/ack、frame callback、buffer.release、resizeを追う。
- [ ] client/surface/toplevelのライフサイクルを追跡する。

**Waypipeの言語について:** [上流にはRust版への移行](https://mstoeckl.com/notes/code/waypipe_to_rust.html)があるが、確認したTermuxの0.11.3レシピはbuild_c=true/build_rs=falseである。MVPは既存C版の再利用を起点にし、C/Rustの版を変更する場合は両端の互換性を検証する。

合格: Termuxで動く一つのWayland clientのsurfaceとbufferをAPK内Rust compositorが受け取る。

**Gate:** 問題があればlibwayland/libxkbcommon、LZ4/Zstd、socket、spawn、Android API制約を記録する。上流実装のpatch/packaging修正を先に検討し、構造的な問題が残る場合だけ既存planの別UID FD bridgeやTermux側compositor案と比較する。

## Phase 4 — Wayland windowをXRパネルに描画（MVP）

**詳細・調査結果:** [phase-4-rust-window-rendering.md](phase-4-rust-window-rendering.md)。Rust rendererを採用し、Smithay GlesRendererのSHM importと合成を第一候補にする。Android EGL adapterとSDK Surface管理は新規実装する。

### Phase 4の描画経路 — wl_shmからXRパネルへ

~~~mermaid
flowchart LR
  subgraph QUEST["同一Quest 3"]
    subgraph T["Termux"]
      FOOT["foot: wl_shm commit"] --> WS["Waypipe server"]
    end
    subgraph APP["Eidolon APK"]
      WC["Waypipe client"] --> COMP["Rust: Smithay surface tree"]
      COMP -->|"SHM / stride / damage"| GL["Rust: GlesRenderer + Android EGL adapter"]
      GL -->|"eglSwapBuffers"| SURF["SDK Surface"]
      SURF --> SDK["XRパネル"]
    end
    WS -->|"Waypipe転送 / Rust SSH bridge"| WC
  end
~~~

**接続境界:** 描画の主方向を示す。configure/frame callback/buffer.release等は逆方向にも流れる。KotlinにはSurfaceの制御情報を渡し、画素配列を毎フレームJNIで往復させない。CPU→GPU uploadは初期に許容する。

参考：[GlesRenderer](https://docs.rs/smithay/0.7.0/smithay/backend/renderer/gles/struct.GlesRenderer.html)、[Local Desktop Android backend](https://github.com/localdesktop/localdesktop.github.io/blob/fdd0275d6ec7840277def8ccd3ba3ed463900ca1/src/android/backend/wayland/winit_backend.rs)、[Meta surfaceConsumerの例](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/main/MediaPlayerSample/app/src/main/java/com/meta/spatial/samples/mediaplayersample/MediaPlayerSampleActivity.kt)。[VNCのFramebufferView](https://github.com/MesmerPrism/quest-termux-lab/blob/main/examples/spatial-desktop-panel/app/src/main/java/io/github/mesmerprism/questtermuxlab/spatialdesktop/FramebufferView.kt)は方式比較用。

作業:
- [ ] Phase 1のテスト画像を、Phase 3のwl_shm画像へ置き換える。
- [ ] ARGB8888/XRGB8888、stride、damage、alpha、buffer lifetimeと同期を扱う。
- [ ] Rustの専用owner threadでEGL/GlesRendererを扱う。GlesRendererの!Send/!Syncを尊重する。
- [ ] toplevelにXRパネルを対応させ、popup/subsurfaceの合成範囲を決める。
- [ ] logical size、buffer scale、producer pixel size、XRのメートル寸法を分離する。
- [ ] 初期は静止画像、次にfootのshell、cursor、scrollで確認する。
- [ ] close、Surface再生成、切断で古いbufferを表示せず、リソースを回収する。
- [ ] layer使用数を観測し、SDK/OSの可用枚数を記録する。非表示windowと描画資源を分離する方針はPhase 5で検討する。

合格: Quest上のfootがRustで合成され、一枚のXRパネルに表示・更新される。zero-copyは必須にしない。

## Phase 5 — XR入力と複数ウィンドウ

**詳細・調査結果:** [phase-5-rust-window-manager.md](phase-5-rust-window-manager.md)。WindowId、focus、pose、size、配置保存はRustを正本にする。KotlinはSDK Entity操作と入力取得を担い、別のwindow managerを作らない。

### Phase 5の双方向通信 — 複数surfaceとXR入力

~~~mermaid
flowchart LR
  subgraph Q["同一Quest 3"]
    BT["Keyboard / controller"] -->|"KeyEvent / ray-hit"| K["Kotlin SDK adapter"]
    subgraph APK["Eidolon APK"]
      K <-->|"JNI: events / panel commands"| WM["Rust: WindowManager + Smithay seat"]
      K --> A["XRパネル A"]
      K --> B["XRパネル B"]
      WM <-->|"Wayland socket"| WC["Waypipe client"]
    end
    subgraph TERMUX["Termux"]
      WS["Waypipe server"] <-->|"Wayland"| F["foot"]
      WS <-->|"Wayland"| GUI["別GUIアプリ"]
    end
    WC <-->|"localhost SSH: russh bridge"| WS
  end
~~~

**接続境界:** XR→Rustに座標・キー・操作を送り、Rust→Kotlinにpanel生成・pose・size・破棄を送る。SDKの実際のdrag位置はイベントでRustへ返す。Waylandのpixel resizeと物理メートル拡大は別。

参考：[WayVR hit_test.rs](https://github.com/wayvr-org/wayvr/blob/main/wayvr/src/backend/wayvr/hit_test.rs)、[window.rs](https://github.com/wayvr-org/wayvr/blob/main/wayvr/src/backend/wayvr/window.rs)、[client.rs](https://github.com/wayvr-org/wayvr/blob/main/wayvr/src/backend/wayvr/client.rs)、[Smithay input.rs](https://github.com/Smithay/smithay/blob/master/smallvil/src/input.rs)、[Meta UISetSample](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/tree/main/UISetSample)。[quest-termux-lab InputCodec](https://github.com/MesmerPrism/quest-termux-lab/blob/main/examples/spatial-desktop-panel/app/src/main/java/io/github/mesmerprism/questtermuxlab/spatialdesktop/InputCodec.kt)はVNC用でありWaylandへ直接流用しない。

作業:
- [ ] RustのWindowManagerをAndroid/GL/JNIから分離し、fake adapterで単体テストする。
- [ ] XR hit-testからlogical surface座標への変換とbutton、scroll、enter/leaveを扱う。
- [ ] Android KeyEventとWayland keycode/keymap/modifierの差を扱う。
- [ ] Esc/Ctrl/Alt/Tab/Fキー、US/JIS記号を実機で確認する。
- [ ] foot内のfish/tmux/nvim、copy-modeとmodifierを確認する。
- [ ] 二枚以上の独立したtoplevelを作成・閉鎖し、focusを切り替える。
- [ ] popup、subsurface、parent-child、clipboard、IME/日本語を段階的に追加する。
- [ ] 物理拡縮、pixel resize交渉、配置保存、passthrough切替を実装する。
- [ ] 世代付きWindowId/SurfaceGenerationで古いcallbackとdestroy済みwindowへの入力を拒否する。
- [ ] XR session focus喪失をRustへ通知し、入力captureと押下状態を解消・再同期する。Universal Menu表示をWayland/SSH切断と混同しない。
- [ ] 論理ウィンドウ数、表示対象数、実際のSDK layer割当数を区別する。非表示時の保持・再割当やmesh利用は検証後に決める。

合格: 二つ以上の独立パネルを操作でき、Waylandの入力focusとRustの状態が一致する。

## Phase 6 — 運用性、性能、耐障害性

**詳細・調査結果:** [phase-6-rust-lifecycle-testing.md](phase-6-rust-lifecycle-testing.md)。Rust compositor/render owner、Tokio SSH runtime、Kotlin UIの三領域を分ける。Activity/Surface、SSH、Wayland clientは別の寿命として扱う。

### Phase 6の運用・回復経路

~~~mermaid
flowchart LR
  subgraph Q["同一Quest 3"]
    K["Kotlin lifecycle adapter"] --> S["Rust session supervisor"]
    S <-->|"state / cancel / retry"| N["Tokio + russh bridge"]
    N <-->|"loopback SSH"| T["Termux sshd / Waypipe / apps"]
    S <-->|"control / status"| R["Rust compositor / render owner"]
    R --> M["metrics / logcat"]
    N --> M
  end
~~~

**接続境界:** Surfaceを再生成してもSSHまで再起動しない設計を目指す。一方SSHが再接続しても既存アプリ状態が必ず復元するとは保証しない。MVPでWaypipe再接続保持を実装しなければ、新セッションとして再起動することを明示する。

参考：[Anland developer guide](https://github.com/lfdevs/anland-termux/blob/main/docs/developer-guide.md)、[Local Desktop Surface lifecycle](https://github.com/localdesktop/localdesktop.github.io/blob/fdd0275d6ec7840277def8ccd3ba3ed463900ca1/src/android/backend/wayland/winit_backend.rs)、[quest-termux-lab Activity](https://github.com/MesmerPrism/quest-termux-lab/blob/main/examples/spatial-desktop-panel/app/src/main/java/io/github/mesmerprism/questtermuxlab/spatialdesktop/SpatialDesktopActivity.kt)、[Termux:Boot](https://github.com/termux/termux-boot)、[Android performance-samples](https://github.com/android/performance-samples)。方式の異なる例は比較材料とし、そのまま統合できるとは仮定しない。

作業:
- [ ] Disconnected/Connecting/Ready/Reconnecting/Stopping等の状態遷移を実装する。
- [ ] Termux停止、バックグラウンド制限、Quest sleep、Activity/Surface再生成を分離して試す。
- [ ] Phase 0〜1で得た背景存続・XR session focusの観測結果を引き継ぎ、Universal Menu、完全な背景化、sleep/resumeを別々に再現する。
- [ ] host key不一致、認証失敗、forward失敗、Waypipe失敗を区別して診断する。
- [ ] bounded queueとbackpressureを設計し、frame coalesce時もprotocol処理を失わない。
- [ ] フレーム時間、present待ち、SHM upload量、Waypipe byte数、CPU/メモリ/FD数を測定する。
- [ ] 使用layer数と表示対象window数を記録し、多数表示時の資源割当を評価する。
- [ ] 30分/2時間の長時間実験、再接続100回等を実施する。
- [ ] native resourceの所有者と解放順序を確認し、JNI境界越しのpanicを防ぐ。
- [ ] 必要が確認された場合だけGPU importとコピー削減を評価する。
- [ ] 外部LinuxへのSSH接続はQuest単体MVP完了後の追加機能とする。

## 予定ディレクトリ（実装時に調整可）

~~~text
eidolon-wayland/
├── README.md
├── docs/
│   ├── architecture.md
│   ├── implementation-plan.md
│   ├── phase-0-development-environment.md
│   └── phase-1-*.md ... phase-6-*.md
├── android/                 # Kotlin / Spatial SDK adapter
├── native/                  # Rust workspace / Android cdylib（案）
│   ├── crates/window-model/ # Android非依存の状態モデル
│   ├── crates/compositor/   # Smithay / Wayland
│   ├── crates/renderer/     # Android EGL adapter / GLES
│   ├── crates/transport/    # russh / Waypipe lifecycle
│   └── crates/android-jni/  # Kotlinとの小さいAPI境界
├── third_party/             # 必要ならWaypipeの版・patch・license情報
└── scripts/                 # build / setup / diagnostics
~~~

すべて予定パスで、現在実装済みではない。crate分割を初日から必須にせず、単純なcdylibでspikeし、責務が確定してから分割してよい。

## テスト戦略・確認項目

| 観点 | Test | 成功の目安 |
| --- | --- | --- |
| Build | Gradle、cargo-ndk、ABI、MSRV、native依存 | 再現可能なAPK生成、誤リンクなし |
| JNI / Surface | native method、EGL draw、pause/resume | C++必須でなく疎通、描画継続、leakなし |
| Protocol | configure/ack、buffer lifetime、disconnect | deadlock・protocol errorなし |
| SSH transport | host key、exec、StreamLocal echo、Waypipe | fd転送とbyte streamの境界が正しい |
| Security | loopback bind、認証、path、secret管理 | LAN露出なし、不正鍵・不明path拒否 |
| Input | Ctrl/Alt/Esc/US/JIS、pointer/scroll、focus | CLI・GUI操作可能 |
| Model | Rustの状態遷移、世代、fake adapter | host上で再現可能な回帰試験 |
| Reliability | sleep、Termux kill、Surface再生成 | リソース回収、復旧の範囲が明示される |
| Performance | render時間、転送量、CPU、熱、native memory | 計測とbaseline、最適化の根拠 |

## 設計判断の記録（初期ADR）

### ADR-001: compositorをAndroid APK内に置く

**採用。** 空間ウィンドウ管理と描画経路を近接させる。Android向け出力adapterは必要で、Spatial SDKをWayland対応済みとみなさない。

### ADR-002: Termuxとの境界はlocalhost SSH + Waypipe

**採用（実現性検証が必要）。** UID境界はSSHで越え、protocol/SHM転送はWaypipeに任せる。SSH単体でWayland fdは転送できない。APK側Waypipeの同梱・実行は未検証。

### ADR-003: terminal emulatorを再実装しない

**採用。** foot等の既存Wayland clientを表示する。Eidolonはdisplay serverとXR adapterを担当する。

### ADR-004: 最初はSHM + CPU copy

**採用。** GPU import/zero-copyを初期MVPの前提にしない。性能目標はbaseline取得後に決める。

### ADR-005: Kotlin adapter + Rust core

**採用方針。** JNI/NDK呼出し、2D renderer、SSH、compositor、WindowManagerをRustへ寄せる。KotlinはMeta Spatial SDK／Android操作を担う。C++完全排除は目的にせず、必要性が確認されたshimは許容する。依存Cライブラリの存在とは区別する。

### ADR-006: 既存Waypipeを再利用し、SSHはrusshを第一候補にする

**採用方針。** WaypipeをRustで書き直さない。上流C/Rust版は実行環境・互換性で選び、MVPはTermuxの採用するC版を起点にする。russhに必要なStreamLocal APIはあるが、bridgeとAndroid動作は検証する。

### ADR-007: Rustがウィンドウ状態の正本を持つ

**採用方針。** KotlinとRustでfocusやposeの二重管理をしない。SDK操作はKotlin adapterへcommandとして渡し、実際の操作結果をRustへ報告する。

## 実装担当（Codex等）への引き継ぎ

この文書は仕様のベースラインであり、実装者の裁量を不必要に制限しない。合理的な構成変更は根拠と検証結果を残して行う。一度にすべてのnative依存を統合せず、次の単位で不確実性を解消する。

1. 最小Kotlin APKとRust JNIの疎通。XRパネルにRust由来のbuild情報を表示し、ビルド・同梱・起動を再現可能にする。
2. RustだけでSDK Surfaceに描画するPhase 1。
3. Rust SSHの認証・Unix socket echoを独立して試すPhase 2。
4. Smithayのprotocolと既存Waypipeの起動を別々に試して結合するPhase 3。
5. Phase 4で画像、Phase 5で複数windowと入力、Phase 6で寿命と性能を統合する。

2〜4は共通基盤ができた範囲で並行してよい。特にAPK helper起動は早期に独立して試し、XR focusとSurface寿命の観測を各段階へ入れる。新しい抽象化やcrate分割を初日から必須にせず、検証で必要になった境界から具体化する。

**READMEで実機動作を主張するのは、Quest上でTermuxのfootを表示・入力できることを実際に再現してから。** crateの存在、Androidの先行例、Eidolon自身の実機検証を報告上も分ける。
