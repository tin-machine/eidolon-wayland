# Eidolon Wayland — 実装計画・検証項目

Status: **Planning / 実装未着手**  
Architecture: [architecture.md](architecture.md)  
Phase 0 runbook: [phase-0-development-environment.md](phase-0-development-environment.md)  
Target: **Quest 3単体 + Termux + localhost SSH + Waypipe + Spatial SDKアプリ内Wayland compositor**

## 成功の定義

最小MVP: Quest 3にインストールしたEidolon APKを起動し、**同じQuest上のTermuxで動くfoot**を、VR空間内の独立した長方形パネルとして表示する。Bluetoothキーボードでshell/Neovimに入力でき、コントローラーでクリック、画面の再描画、リサイズ、接続の再確立が行える。

- **ネットワークに依存しない**: アプリ実行と表示にはPC・Wi-Fi・クラウドは不要。localhost SSHのみ。
- **非特権**: Questのroot化、OSの置換、カーネルモジュールの導入なし。
- **既存のターミナルを再利用**: footをWayland clientとして動かし、ターミナル描画は再実装しない。
- **分離したXRパネル**: 最初は1画面、その後2枚以上の独立した<code>xdg_toplevel</code>へ。
- **PoC段階はCPUコピーを許容**: 低遅延/ゼロコピーはMVP合格要件にしない。

## 最初に潰すべき不確実性（スパイク）

| 優先度 | 検証 | 合格条件 | 不成立時 |
| --- | --- | --- | --- |
| P0 | 空のSpatial SDK APKをQuest実機でビルド・起動 | immersiveシーンを表示できる | SDK/Androidビルド環境を最小化 |
| P0 | <code>VideoSurfacePanelRegistration</code> + NDK EGL/GLES | 独自のテストパターンをXRパネルへ連続描画できる | ViewPanel/別Surface APIを比較 |
| P0 | Android/aarch64にWayland server/Smithayを組込む | <code>wl_shm</code>を扱う最小compositorがAPK内で起動 | libwayland-server C/C++を代案として検証 |
| **P0** | **Android側Waypipeのビルドとcompositorソケット接続** | **Bionic上でWaypipe display-sideが動作し、Wayland clientの接続要求を転送できる** | Android側は小さなcustom proxy、またはTermux compositor＋Binder bridgeへ変更 |
| P0 | APK内SSHクライアント → Termux <code>127.0.0.1:8022</code> | ホスト鍵検証＋公開鍵認証でコマンド実行可能 | libssh/NDK/exec方式を見直す |
| P0 | Waypipe両端 + Wayland client | 描画と入力が往復し<code>xdg_toplevel</code>が生成される | Waypipeのprotocol/依存差を解析 |
| P1 | Questのバックグラウンド制限 | XR表示中にTermuxのsshdとfootが維持される | 明示的な再接続とTermux保護手順を設計 |

> 最も危険なのは「SSHを使えばWaylandが自動的に届く」という思い込み。Waypipeの**両端**が動き、local display側に有効なWayland compositorが存在してはじめて表示できる。

## フェーズ別通信図の読み方

以下の図は**各フェーズ完了時に目指す接続**を示す設計図であり、現時点の実装済み構成を示すものではない。図中の「Quest」はすべて**同一Quest 3内**で、Phase 0に限りAPKビルド/ADB用の開発PCを使う。矢印は制御またはデータの流れを示し、プロセス境界、Unix domain socket、SSH転送、Android Surfaceを区別して記載する。

**Waypipeの名前の注意:** 表示側（Eidolon APK）で動くのは [`waypipe client`](https://man.archlinux.org/man/waypipe.1.en)、アプリ実行側（Termux）で動くのは `waypipe server`。これは**Waypipe自身の用語**であり、Waylandプロトコルでの役割は逆（EidolonがWayland compositor/server、footがWayland client）となる。標準の `waypipe ssh` はSSHのリモートUnixソケット転送（`ssh -R`）を利用できるため、**SSHの単純なexec/stdin-stdoutだけでは置換できない**。AndroidのSSHライブラリ選定時にはStreamLocal forwardingに対応するか、代替トランスポートを設計する必要がある。[Waypipe manpageの実行例](https://man.archlinux.org/man/waypipe.1.en#EXAMPLE)を参照。

## Phase 0 — レポジトリと開発環境

**詳細runbook（このフェーズの正本）:** **[phase-0-development-environment.md](phase-0-development-environment.md)**  
上記へ環境構築コマンド・上流サンプルからのAPKビルド・USBデバッグ手順・TermuxへのADB/SSH経路・Codex CLIの互換性/認証/GitHub連携・具体的なチェックリストを移した。**Phase 0の作業はリンク先を起点に進める。**

### Phase 0の接続図 — 開発PCからQuestへのAPK投入（本番通信ではない）

~~~mermaid
flowchart LR
  DEV["開発PC: Gradle / Android SDK / JDK"] -->|"build"| APK["Spatial SDK APK"]
  APK -->|"adb install / USB-C"| QUEST["Quest 3: XR Activity"]
  QUEST -->|"adb logcat"| DEV
  DEV -.->|"任意: adb forward TCP 18022 → Quest 8022 / SSH"| T["Quest 3: Termux sshd"]
~~~

**接続境界:** この図のADB/USB/Termux SSHは開発・調査時限定。Spatial SDKアプリ自体からTermuxへ接続する製品用SSH・Waypipeは後続フェーズで扱う。Ubuntu上でMeta Spatial Editor CLIが必要になる可能性とLinux版SDKビルドの制約も詳細runbookに記載した。

**Phase 0の完了条件（概要）**
- [ ] Ubuntu上のJDK 17 / Android SDK / Gradle wrapper / Meta Spatial SDKツールチェーンを確認する。
- [ ] Quest 3のDeveloper Mode・USB debugを有効化し、ADBで接続できる。
- [ ] 上流のMeta StarterSample（または検証済みの代替最小サンプル）をUbuntuでビルド・Quest実機起動し、ログを採取する。
- [ ] Eidolon専用の最小APKを実装し、ビルド・インストール・起動・診断を再現可能にする。
- [ ] 必要なら開発PC側の公式CodexからADB forward＋SSHでTermuxを調査できるようにする（**Quest内Codexの動作は任意**）。
- [ ] CIの基本ビルド・バージョン管理・ライセンス・secret除外を整備する。

**参考実装:** [Meta StarterSample](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/tree/main/StarterSample)、[Android NDK Samples](https://github.com/android/ndk-samples)、[Meta Linux Spatial Editor CLI](https://developers.meta.com/vr/downloads/package/meta-spatial-editor-cli-for-linux/)。詳細の用途別資料とコマンドは[Phase 0 runbook](phase-0-development-environment.md)を参照する。

## Phase 1 — Spatial Surfaceの描画スパイク

### Phase 1の描画経路 — Termuxはまだ接続しない

~~~mermaid
flowchart LR
  subgraph APP["Quest 3: Eidolon APK" ]
    SDK["Spatial SDK panel registration"] -->|"surfaceConsumer"| SURF["Android Surface / ANativeWindow"]
    PAT["テストパターン描画器"] -->|"GLES描画"| EGL["EGL window surface"]
    EGL -->|"eglSwapBuffers"| SURF
  end
  SURF -->|"Spatial SDKの表示経路"| XR["Quest XR空間の1枚のパネル"]
~~~

**接続境界:** Kotlin/Spatial SDKが渡す `Surface` とNDKの `ANativeWindow`、EGL/GLESとの同一APK内連携のみ。ネットワーク、SSH、Waypipe、Waylandクライアントは不使用。Surfaceの作成・破棄タイミングは別途検証。

**参考実装・資料（該当箇所）:**
- [Meta MediaPlayerSample — MediaPlayerSampleActivity.kt](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/main/MediaPlayerSample/app/src/main/java/com/meta/spatial/samples/mediaplayersample/MediaPlayerSampleActivity.kt)：`VideoSurfacePanelRegistration` と `surfaceConsumer` の使い方。映像アプリの例なのでGLES描画は別途実装。
- [Android NDK — gles3jni](https://github.com/android/ndk-samples/tree/main/gles3jni)：Java/KotlinアプリとネイティブOpenGL ESレンダラーの接続。
- [quest-termux-lab — SpatialDesktopActivity.kt](https://github.com/MesmerPrism/quest-termux-lab/blob/main/examples/spatial-desktop-panel/app/src/main/java/io/github/mesmerprism/questtermuxlab/spatialdesktop/SpatialDesktopActivity.kt)：Quest向けSpatialパネルの配置・入力・ライフサイクルに関する近い先行例（同実装の描画はVNC方式）。


作業:
- [ ] immersive Spatial SDKアプリに1枚のパネルを作成する。
- [ ] <code>VideoSurfacePanelRegistration</code>の<code>surfaceConsumer</code>を実装し、Surface取得・更新・破棄を追跡。
- [ ] <code>ANativeWindow_fromSurface</code>、EGL、OpenGL ESでcheckerboard/カラーバーとフレームカウンタを描画。
- [ ] pixel size、物理サイズ、DPI、視距離、render modeと文字の可読性を記録。
- [ ] パネル拡縮・位置移動、Surface再生成、アプリpause/resumeへの耐性を確認。
- [ ] CPU/メモリ/フレーム時間の測定点を設ける。

成果物: 入力で配置可能なテストパターンパネル。

合格: 画面のちらつきやクラッシュがなく、再生成後も再描画可能。

## Phase 2 — SSHとTermuxの安全な接続

### Phase 2の通信経路 — localhost SSHだけを先に成立させる

~~~mermaid
flowchart LR
  subgraph QUEST["同一Quest 3" ]
    subgraph APK["Eidolon APK / Android UID A" ]
      CLIENT["SSH client + host-key verification"]
    end
    subgraph TERMUX["Termux / Android UID B" ]
      SSHD["sshd listen: 127.0.0.1:8022"] -->|"exec: id / echo"| CMD["Termux shell"]
    end
    CLIENT <-->|"TCP loopback / SSH / public-key auth"| SSHD
    CMD -->|"stdout / stderr / exit status"| SSHD
  end
~~~

**接続境界:** Androidアプリの別UID間を、Unix socket直接接続ではなく**ループバックTCPとSSH**で通過する。Phase 2のSSHはコマンド実行の疎通だけを確認し、Wayland描画はまだ行わない。Android APK側にはネットワーク権限とSSHのhost-key検証が必要。

**参考実装・資料（該当箇所）:**
- [Termux OpenSSHパッケージのビルド定義](https://github.com/termux/termux-packages/blob/master/packages/openssh/build.sh)：Termux環境のOpenSSH設定パスや依存関係を確認。
- [SSHJ Javaライブラリ](https://github.com/hierynomus/sshj) / [SSHJ examples](https://github.com/hierynomus/sshj/tree/master/examples)：Android側での認証・SSH execの実装候補。**Waypipeに必要なUnixソケット転送は別途対応可否を検証**し、採用を確定しない。
- [Android Security Samples](https://github.com/android/security-samples)：AndroidセキュリティAPIの実装パターンの参考。SSH鍵をKeystoreに統合できるかは利用ライブラリと合わせて別途評価。


Termuxの初期セットアップ例（ポートは実環境の設定を確認）:

~~~sh
pkg update
pkg install openssh waypipe
pkg install x11-repo
pkg install foot
# 公開鍵を $HOME/.ssh/authorized_keys に登録し、適切な権限へ
# まずはTermuxの別セッションで接続試験する
sshd
id -un
~~~

本番運用前にはSSHDのbind先を127.0.0.1のみに制限し、鍵認証だけにする（設定はTermuxのOpenSSHの実際のsshd_configで検証）。鍵生成と登録は初期は手動とし、自動配布やパスワード保存を実装しない。

作業:
- [ ] Termuxのsshdを<code>127.0.0.1:8022</code>に限定し、APKから接続可能か試す。
- [ ] Android側にSSH接続ライブラリ等を実装。<code>libssh</code>やSSHJ等は候補であり採用確定ではない。**Phase 3で必要となるStreamLocal forwarding（SSHによるUnix socket転送）の対応有無**も選定条件として記録する。
- [ ] ホスト鍵フィンガープリント検証（初回登録方式と鍵ローテーションも設計）。
- [ ] 鍵をAndroid Keystore等で保護できるか、使用SSH実装との互換性も含めて検証。
- [ ] APK→SSH→<code>id</code>/<code>echo</code>のstdout/stderr/exit status取得と切断検出。
- [ ] sshdの待受、ユーザー、ポート、公開鍵認証の手順をセットアップドキュメント化。

合格: 物理LANにsshdを露出せず、Quest内のAPKからTermuxのコマンドを繰り返し実行できる。

## Phase 3 — Wayland serverとWaypipeのPoC（最重要）

### Phase 3の通信経路 — Wayland protocolがAPKとTermuxを往復

~~~mermaid
flowchart LR
  subgraph QUEST["同一Quest 3" ]
    subgraph APK["Eidolon APK / UID A" ]
      COMP["Wayland compositor / Smithay候補"] <-->|"Wayland Unix socket + fd"| WC["Waypipe client: 表示側"]
      SSHC["SSH client: Unix socket forwarding"]
      WC <-->|"Waypipe転送用socket"| SSHC
    end
    subgraph TERMUX["Termux / UID B" ]
      SSHD["sshd 127.0.0.1:8022"] <-->|"転送socket"| WS["Waypipe server: アプリ側"]
      WS <-->|"Wayland socket + wl_shm fd"| FOOT["foot / test Wayland client"]
    end
    SSHC <-->|"SSH over TCP loopback / streamlocal forwarding"| SSHD
  end
~~~

**接続境界:** Wayland上は**Eidolonがserver/compositor、footがclient**。Waypipe上はAPKに`client`、Termuxに`server`。両プロキシがfd/SHM更新を必要に応じて再構成する。**SSH execが通るだけでは未達**で、`waypipe ssh`が相当するforwardingもAndroid側で実現しなければならない。この段階はXRへの実画面描画を必須にせず、Wayland protocol/SHM取得を検証する。

**参考実装・資料（該当箇所）:**
- [Smithay — smallvil](https://github.com/Smithay/smithay/tree/master/smallvil) / [GETTING_STARTED.md](https://github.com/Smithay/smithay/blob/master/GETTING_STARTED.md)：小さなWayland compositorの骨格、protocol、seat、event loopの参考。
- [WayVR — Wayland server接続・入力処理 (client.rs)](https://github.com/wayvr-org/wayvr/blob/main/wayvr/src/backend/wayvr/client.rs)：既存compositorがclient socketをacceptし、Wayland eventを処理する実装。
- [Waypipe manual（`ssh`、`client`、`server`、`--no-gpu`）](https://man.archlinux.org/man/waypipe.1.en)：接続方向とSSHのUnixソケット転送例。
- [Termux — waypipe build.sh](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)：Termux側Waypipeのビルド機能。Android APK側へ**そのまま転用可能とは限らない**。


作業:
- [ ] Androidプロセス内部にWayland serverを起動（Smithayを第一候補）。
- [ ] Wayland Unix socket、event loop、<code>wl_compositor</code>、<code>wl_shm</code>、<code>xdg_shell</code>、<code>wl_seat</code>の基礎を実装する。
- [ ] テスト用Wayland clientが<code>xdg_toplevel</code>を生成・commitできるようにする。
- [ ] Android側のWaypipeをNDK/Bionic向けにビルド/同梱できるか検証する。既存waypipeのspawn、Unix socket、外部依存、fd、APK実行制約を調査する。
- [ ] Waypipe client（APK）とWaypipe server（Termux）を、SSHの**リモートUnixソケット転送（StreamLocal forwarding / 例: <code>ssh -R</code>）**または等価な双方向トランスポートで接続する。既存の<code>waypipe ssh</code> CLI/マニュアルの起動方式を参考にし、**Android側SSHライブラリがUnixソケット転送に対応するか先に検証**する。APKへの単純なコピーで動くと仮定しない。
- [ ] TermuxのWaypipeと組み合わせ、<code>--no-gpu</code>相当のSHM経路で起動する。
- [ ] <code>WAYLAND_DEBUG=client</code>とcompositorログでプロトコルを追う。
- [ ] xdg configure/ack、frame callback、buffer release、resizeが止まらないか確認する。
- [ ] クライアントごとのsurface/toplevelを追跡し、window lifecycleイベントを出す。

参考: [Waypipe manpage](https://man.archlinux.org/man/waypipe.1.en)、[Smithay Wayland helpers](https://docs.rs/smithay/latest/smithay/wayland/)、[Termux waypipe recipe](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)。

合格: 1つのWaylandアプリがTermuxで動き、APK側compositorが確実にそのsurfaceとbufferを受け取れる。

**Gate:** Android側Waypipeが構築できない場合は、何が障害か（libwayland、liblz4、Zstd、socket、spawn、API制限等）を記録する。主設計の目的を維持しつつ、custom protocol proxy、別UID FD bridge、Termux側compositorへの構成変更を技術的に比較してから判断する。

## Phase 4 — Wayland windowをXRパネルに描画（MVP）

### Phase 4の描画経路 — `wl_shm`からXRパネルへ（MVP）

~~~mermaid
flowchart LR
  subgraph QUEST["同一Quest 3" ]
    subgraph T["Termux / UID B" ]
      FOOT["foot: wl_shm buffer commit"] --> WS["Waypipe server"]
    end
    subgraph APP["Eidolon APK / UID A" ]
      WC["Waypipe client"] --> COMP["Wayland compositor: wl_buffer取得"]
      COMP -->|"CPU buffer / stride / damage"| GL["EGL + GLES texture upload"]
      GL -->|"eglSwapBuffers"| SURF["Android Surface"]
      SURF --> SDK["Spatial SDK: Window IDごとのXRパネル"]
    end
    WS -->|"SSHの転送経路: SHM更新とWaylandイベント"| WC
  end
~~~

**接続境界:** Phase 3の双方向Wayland転送を保持し、**Termux→APK→XRパネルという描画経路**を初めて完成させる。図の実線は描画の主方向で、configure/frame callback/buffer release等のWayland制御メッセージは逆方向にも流れる。SHM→GLESにはまずCPUコピーを許容し、ゼロコピーとGPU importは後回し。

**参考実装・資料（該当箇所）:**
- [Meta MediaPlayerSample — surfaceConsumer](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/main/MediaPlayerSample/app/src/main/java/com/meta/spatial/samples/mediaplayersample/MediaPlayerSampleActivity.kt)：Spatial SDKが管理するパネル用Surfaceの取得。
- [Android NDK — gles3jni](https://github.com/android/ndk-samples/tree/main/gles3jni)：EGL/GLESによるAndroid側の描画実装の土台。
- [Smithay — smallvil](https://github.com/Smithay/smithay/tree/master/smallvil)：Waylandのsurface/commit/toplevel管理の参考。**XR Surface出力は既存コードではなく新規実装する**。
- [quest-termux-lab — FramebufferView.kt](https://github.com/MesmerPrism/quest-termux-lab/blob/main/examples/spatial-desktop-panel/app/src/main/java/io/github/mesmerprism/questtermuxlab/spatialdesktop/FramebufferView.kt)：受信した画像の更新管理・表示の別アプローチ（VNC+Android View）として比較。


作業:
- [ ] compositorで受信した<code>wl_shm</code>画像をEGL/GLES rendererへ引き渡す。
- [ ] ARGB8888/XRGB8888、stride、damage、buffer lifetime、同期を正しく処理する。
- [ ] 受信フレームをSurfaceへ描画する。最初はCPU→GPUアップロードを許容。
- [ ] 1つの<code>xdg_toplevel</code>に1つのSpatialパネルを紐付ける。
- [ ] 物理パネルサイズとWayland logical buffer sizeを分離する。
- [ ] まず静止クライアント、次にfoot（shell実行）で確認。
- [ ] クラッシュ/切断でbufferが解放されることを確認。

合格: Quest上のTermuxで動くfootがXRパネルに表示され、文字とカーソルが更新される。

## Phase 5 — XR入力と複数ウィンドウ

### Phase 5の双方向通信 — 複数surfaceとXR入力・フォーカス

~~~mermaid
flowchart LR
  subgraph Q["同一Quest 3" ]
    BT["Bluetoothキーボード / Quest controller"] -->|"Android KeyEvent / XR ray"| IN["Spatial入力・focus/router"]
    subgraph APK["Eidolon APK" ]
      IN -->|"pointer/key/resize"| COMP["Wayland compositor: seat / xdg_toplevel"]
      COMP -->|"各window buffer / lifecycle"| MAP["Window ID → XR panel mapping"]
      MAP --> A["XRパネル A"]
      MAP --> B["XRパネル B"]
      COMP <-->|"Wayland Unix socket"| WC["Waypipe client"]
    end
    subgraph TERMUX["Termux" ]
      WS["Waypipe server"] <-->|"Wayland protocol"| F["foot"]
      WS <-->|"Wayland protocol"| GUI["別のWayland GUIアプリ"]
    end
    WC <-->|"localhost SSH: 双方向Waypipe転送"| WS
  end
~~~

**接続境界:** XR→Wayland方向に**座標・クリック・キー・フォーカス**を送信し、Wayland→XR方向に**2つ以上の独立したsurfaceとライフサイクル**を受ける。どのXRパネルに焦点があるかはEidolon compositorが一元管理する。Waypipeは同一SSH接続上の双方向Waylandメッセージ転送として扱う。パネルの物理拡大とWayland `xdg_toplevel` のピクセルリサイズは別操作。

**参考実装・資料（該当箇所）:**
- [WayVR — hit_test.rs](https://github.com/wayvr-org/wayvr/blob/main/wayvr/src/backend/wayvr/hit_test.rs) / [WayVR — client.rs](https://github.com/wayvr-org/wayvr/blob/main/wayvr/src/backend/wayvr/client.rs)：XR側ポインタの対象ウィンドウ選択とWayland seatへの入力配送。
- [quest-termux-lab — InputCodec.kt](https://github.com/MesmerPrism/quest-termux-lab/blob/main/examples/spatial-desktop-panel/app/src/main/java/io/github/mesmerprism/questtermuxlab/spatialdesktop/InputCodec.kt)：Android KeyEvent→keysymとポインター入力の変換例（**VNC用でありWaylandへはそのまま使わない**）。
- [Meta Spatial SDK — UISetSample](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/tree/main/UISetSample)：空間UIや入力のサンプル。panel/rayの使い方はSDK版に合わせて確認。
- [Smithay — smallvil input.rs](https://github.com/Smithay/smithay/blob/master/smallvil/src/input.rs)：Wayland seatとkeyboard/pointer入力モデル。


作業:
- [ ] Spatial panel hit-test → Wayland pointer座標変換、button、scroll、enter/leave。
- [ ] Android KeyEvent → Wayland keymap/modifiers、keyboard focus、キー繰返し。
- [ ] Bluetoothキーボードで<code>Esc</code>/<code>Ctrl</code>/<code>Alt</code>/<code>Tab</code>/function keys、US/JIS記号を検証。
- [ ] foot内のfish/tmux/nvim操作（特にcopy-modeとmodifier）を確認。
- [ ] 複数<code>xdg_toplevel</code>それぞれにXRパネルを割り当て、生成・閉鎖・フォーカス変更を実装。
- [ ] popup、subsurface、親子座標、clipboard、IME/日本語変換の仕様と実装を段階的に追加。
- [ ] 物理サイズの拡縮、ピクセルリサイズ、配置保存、パススルーの切替を実装。

合格: 2つの独立したアプリウィンドウを空間に並べ、入力フォーカスを切り替えられる。

## Phase 6 — 運用性、性能、耐障害性

### Phase 6の運用・回復経路 — 切断時にも再接続できる設計

~~~mermaid
flowchart LR
  subgraph Q["同一Quest 3" ]
    MON["Eidolon: connection supervisor"] -->|"状態監視・再接続"| SSH["SSH + Waypipe client"]
    SSH <-->|"TCP loopback / SSH-forwarded socket"| REM["Termux: sshd + Waypipe server"]
    REM <-->|"Wayland protocol"| APP["foot / GUI apps"]
    COMP["Wayland compositor + XR renderer"] <-->|"surface / frame / input"| SSH
    MON -->|"再生成・復旧"| COMP
    COMP -->|"frame time / drops / allocations"| OBS["APK内metrics / logcat / debug overlay"]
    SSH -->|"disconnect / retry / bytes"| OBS
    REM -.->|"Termux再起動やkill時は切断検知"| MON
  end
~~~

**接続境界:** SSHとWaypipeの切断、Termuxバックグラウンド停止、APKのActivity/Surface再生成を独立した障害ドメインとして扱う。再接続時はclient/surfaceの寿命と古いXRパネルの破棄を整合させる。外部LinuxへのSSH接続は**将来の別フェーズ**であり、ここでは加えない。性能改善は数値測定後に限定する。

**参考実装・資料（該当箇所）:**
- [Anland: Termux — developer-guide.md](https://github.com/lfdevs/anland-termux/blob/main/docs/developer-guide.md)：AndroidとTermuxを橋渡しするdaemon、socket/FD、diagnostics、セッション起動の先行例（Eidolonとは通信方式が異なる）。
- [quest-termux-lab — SpatialDesktopActivity.kt](https://github.com/MesmerPrism/quest-termux-lab/blob/main/examples/spatial-desktop-panel/app/src/main/java/io/github/mesmerprism/questtermuxlab/spatialdesktop/SpatialDesktopActivity.kt)：Activity lifecycle、接続状態、フレーム計測のログ実装例。
- [Termux:Boot](https://github.com/termux/termux-boot)：Termux側プロセスの起動自動化の候補。Questのバックグラウンド制限を回避できると断定しない。
- [Android performance-samples](https://github.com/android/performance-samples)：Androidアプリの性能計測・可観測性の実装例。必要な測定方式は採用SDK/Quest実機で評価する。


作業:
- [ ] lifecycle state machine（Disconnected/Connecting/Ready/Reconnecting/Stopping）を実装。
- [ ] Termuxの停止・Androidバックグラウンド制限・Questスリープ復帰に対応。
- [ ] ホスト鍵変更、秘密鍵不整合、ssh失敗、Waypipe失敗の診断を分けて出力。
- [ ] フレーム到着頻度、描画時間、Waypipe byte数、CPU/メモリを観測。
- [ ] 30分/2時間の長時間実験、再接続100回などの回帰確認。
- [ ] 必要ならGPU importを試し、コピー回数や同期を計測して最適化。
- [ ] 外部LinuxへのSSH転送は、Quest単体MVP完了後の追加機能として扱う。

## 予定ディレクトリ（実装時に調整可）

~~~text
eidolon-wayland/
├── README.md
├── docs/
│   ├── architecture.md
│   └── implementation-plan.md
├── android/                # Kotlin / Meta Spatial SDK アプリ（案）
├── native/                 # Android NDK renderer / JNI bridge（案）
├── compositor/             # Smithay / Wayland server（案）
├── transport/              # SSH + Waypipe adapter（案）
└── scripts/                # Quest/Termux setup・診断（案）
~~~

コードの分割やプログラミング言語は実験結果に応じてCodex/実装担当が変更してよい。現時点ではこのレイアウトを事実上の制約にしない。

## テスト戦略・確認項目

| 観点 | Test | 成功の目安 |
| --- | --- | --- |
| Build | Gradle、NDK、Rust target、aarch64 linker | 再現可能なAPK生成 |
| Surface | EGL draw、pause/resume、resize | 描画継続、leakなし |
| Protocol | configure/ack、buffer lifecycle、disconnect | デッドロック・プロトコルエラーなし |
| Transport | localhost SSH再接続、Waypipe転送 | 1ウィンドウで描画可能 |
| Security | sshdのlisten address、host key、鍵管理 | LAN露出なし、不正鍵拒否 |
| Input | Ctrl/Alt/Esc/US/JIS、pointer/scroll | CLI・GUI操作可能 |
| UX | サイズ調整、フォーカス、パススルー | 文字が読めて移動可能 |
| Reliability | スリープ復帰、Termux再起動 | 正常復帰・リソース回収 |
| Performance | render latency、CPU、熱、メモリ | まず計測とベースライン確立 |

## 設計判断の記録（初期ADR）

### ADR-001: compositorをAndroid APK内に置く

**採用。** 別UIDのTermux内でcompositorを動かして表示を別アプリへ取り出すより、空間ウィンドウ管理と描画パイプラインを一体化させる。Android向けの描画バックエンド実装は必要。

### ADR-002: Termuxとの境界はlocalhost SSH + Waypipe

**採用（実現性検証を前提とする）。** 通信先を127.0.0.1に限定し、Waypipeを通じてWaylandプロトコルとSHM bufferを転送する。SSHだけでWaylandのFDが透過的に渡るわけではない。Android側Waypipeの移植は未検証。

### ADR-003: terminal emulatorをSpatial SDKで再実装しない

**採用。** footなどのWaylandアプリを直接表示する。Android側はWayland server・描画・入力・空間管理を担当。

### ADR-004: 最初はSHM + CPU copy

**採用。** GPUのbuffer import/zero-copyは初期MVPをブロックさせない。性能目標を数値で決めるのは動作測定後。

## 実装担当（Codex等）への引き継ぎ

このドキュメントは仕様のベースラインであり、**実装者の設計裁量を不必要に制限しない**。採用技術の変更が合理的なら、根拠・代替案・検証結果を文書に残して前進すること。先にすべてを完成させるのではなく、重要な不確実性から短いスパイクで解消する。

最初に推奨する作業:
1. Meta Spatial SDK公式サンプルからQuest実機で描画する最小APKを起こす。
2. Android側Waypipe/Smithayのbuild feasibilityを**別々に**実証する。
3. Termuxのlocalhost SSH接続・鍵認証を確認する。
4. Wayland clientの表示と入力の往復ができたところでMVPを定義し直す。

次の条件を満たすまで、README/ドキュメントで「Quest単体のWayland XRデスクトップが動作する」とは書かないこと。
