# Eidolon Wayland — アーキテクチャ設計

Status: **Proposed / 未実装**  
Last reviewed: 2026-11-01  
Target: **Meta Quest 3 / Meta Horizon OS (Android)**

> Linuxに肉体を残し、その姿だけを異界へ投影する。

## 目的とスコープ

Quest 3単体で、Termux上で実行するWayland GUIアプリを、Meta Spatial SDKで作成した没入型XRアプリの**独立した空間ウィンドウ**として表示・操作する。ユーザーが選択した構成は、**案B: Spatial SDKアプリ内部にWayland compositorを組み込み、Spatial SDKアプリからSSHでTermuxへ接続し、Waypipeを介してWaylandアプリを表示する**方式である。Quest上でのLinuxデスクトップ表示に関する先行例には、VNCの画面をSpatial SDKパネルへ描画する[quest-termux-lab](https://github.com/MesmerPrism/quest-termux-lab)がある。ただし本構想はデスクトップ全体の転送ではなく、Waylandウィンドウ単位の表示を目指す。

- 通常の利用時にはLinux PC、WiVRn、インターネット接続、root化を要しない設計を目指す。
- Quest内での通信は原則 <code>127.0.0.1:8022</code> のSSHと、アプリプロセス内の通信、ローカルWaylandソケットに閉じる。
- Termux側ではfoot等の**既存Waylandアプリ**を利用する。Spatialアプリは端末エミュレーション、ANSI解析、Neovim描画を再実装しない。Termux向けの依存パッケージや移植状況は[termux-packages](https://github.com/termux/termux-packages)で確認する。
- 最終表示はHorizon OSのXR runtimeに任せる。Horizon OSのホーム置換や他アプリのウィンドウ奪取は非目標。
- 初期は単一の2Dパネル、後に複数ウィンドウ、背景演出、パネル移動・拡縮へ進む。
- リモートLinux接続は将来の拡張。まずは**Quest内のTermuxへのlocalhost SSH**に集中する。

## 全体像

~~~mermaid
flowchart TD
  subgraph Quest["Meta Quest 3 / Horizon OS"]
    subgraph APK["Eidolon Android APK / Spatial SDK"]
      UI["XRパネルと入力・フォーカス管理"]
      SURFACE["Android Surface / EGL・OpenGL ES"]
      COMP["組み込みWayland compositor / Smithay候補"]
      WPLOCAL["Waypipe display-side proxy（Android向け実装要検証）"]
      SSH["SSH client（ホスト鍵検証・公開鍵認証）"]
      UI <--> COMP
      COMP --> SURFACE
      SURFACE --> UI
      COMP <--> WPLOCAL
      WPLOCAL <--> SSH
    end
    subgraph TERMUX["Termux / 別Android UID"]
      SSHD["sshd : 127.0.0.1:8022"]
      WPREMOTE["waypipe remote-side proxy"]
      APPS["foot / GTK・Qt Wayland apps"]
      SSHD <--> WPREMOTE
      WPREMOTE <-->|"Wayland Unix socket"| APPS
    end
    SSH <-->|"SSH over 127.0.0.1"| SSHD
  end
~~~

図は**提案**であり、Android側WaypipeとSmithayの統合がQuestで検証済みという意味ではない。Waypipeにおける「local」は**画面を表示する側**（APK）であり、「remote」は**アプリを動かす側**（Termux）。物理的には同じQuest内に存在する。転送の役割とWayland socketとの関係は[Waypipeのマニュアル](https://man.archlinux.org/man/waypipe.1.en)を参照。

## 通信の責務境界

| 区間 | 想定方式 | 送るもの | 留意点 |
| --- | --- | --- | --- |
| APK内 compositor ⇄ Waypipe local | Wayland Unix socket、同一アプリUID | Wayland requests/events、<code>wl_buffer</code>等 | Androidアプリのprivate領域にランタイムソケットを作る |
| APK内 Waypipe local ⇄ Termux側 Waypipe remote | SSH経由の双方向バイトストリーム (<code>127.0.0.1</code>) | Waypipe固有の転送データ（プロトコル、SHM内容など） | SSHだけではWayland fdを転送できない |
| Termux側 Waypipe remote ⇄ Waylandアプリ | Termux UID内のUnix socket | <code>wl_surface</code>/<code>xdg_toplevel</code>、<code>wl_shm</code> buffer fd等 | Waypipe remoteがWayland serverの代理として振る舞う |
| compositor ⇄ XR UI（APK内） | Rust↔JNI/FFI または同一プロセス内メッセージキュー | Window ID、frame、入力、geometry | **別UIDのIPCは不要**。処理間・スレッド間の同期は必要 |
| compositorの画像 → Spatialパネル | Android Surface、ANativeWindow、EGL/GLES | 描画済みウィンドウフレーム | 文字の鮮明さ、resize、Surface再生成に注意 |
| Bluetoothキーボード・Questコントローラー → compositor | Android key/pointer、XR ray hit → Wayland seat | keycode、修飾キー、pointer、focus | IME・記号配列・key repeatは段階的に検証 |

**重要:** TermuxのWaylandアプリとcompositorを別UIDで直接接続するのではなく、WaypipeとSSHを終端する。これによりTermuxのprivate Unix socketへの直接アクセスを避けられる。一方で、Android側に**Waypipe相当の実行機能が必要**となり、ここが最初の成立性検証ポイント。

SSHはプロセス起動・認証・ストリームの経路であり、端末描画サーバーではない。GUIの表示には[Waypipe](https://man.archlinux.org/man/waypipe.1.en)経由でWaylandプロトコルと共有メモリーバッファを中継する必要がある。SSHのセッションを単純に端末として描画する方式とは異なる。

## Wayland compositorの具体的責務

初期の候補はRust + [Smithay](https://docs.rs/smithay/latest/smithay/wayland/)（Wayland serverのプロトコル実装支援）。ウィンドウ管理や描画モデルの参考には、Smithayで組み込みcompositorを実装している[WayVRのソース](https://github.com/wayvr-org/wayvr/tree/main/wayvr/src/backend/wayvr)を利用する。ただしAndroid/aarch64へのビルド可否と依存関係を**先に検証**する。実装不可能ならlibwayland-server＋C/C++を比較し、WaylandフロントエンドとAndroid描画層を分離する。

- Wayland serverの起動・client接続とevent loop。
- <code>wl_compositor</code>、<code>wl_shm</code>、<code>xdg_wm_base</code>、<code>xdg_surface</code>、<code>xdg_toplevel</code>、<code>wl_seat</code>等の必要なprotocol。
- xdg configure/ack、attach/commit、damage、<code>wl_buffer.release</code>、frame callbackを規約どおり扱う。
- surface tree、subsurface、popup、座標変換とウィンドウライフサイクルを管理。
- keyboard focus、pointer enter/leave、keymap、キー修飾子、繰返し、clipboard等を段階的に扱う。
- <code>toplevel ID → XR panel entity → Android Surface</code> の対応関係を保持。
- ウィンドウの論理サイズ（Wayland）と物理サイズ（メートル）、パネル用ピクセル寸法を分離する。
- 最初のclientはfootを想定するが、アプリが要求するprotocol拡張を実際のログで評価する。

**既存のLinux DRM/KMS/GBM表示バックエンドをQuestへ持ち込むことを前提としない。** Androidのアプリには物理ディスプレイを直接制御する役割はない。必要なのはWayland surfaceの画像を取り出し、Spatial SDKパネルが受け取るAndroid Surfaceに描画する**Android向け出力・renderer連携**である。Spatial SDKへの組込み方は[公式サンプル](https://github.com/meta-quest/Meta-Spatial-SDK-Samples)と[Surfaceを利用するメディアパネルの説明](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-media-playback/)を起点として確認する。

## 描画バックエンド（具体像）

### Stage 1: CPUベースの最小構成

1. Termuxのアプリが <code>wl_shm</code> bufferに描画してcommit。
2. Waypipeがバッファの内容とWayland通信をSSHストリーム越しに転送。
3. compositorはWaylandのsurface状態と<code>wl_shm</code> bufferを受け取る。
4. Android側rendererはCPU bufferをGLESテクスチャにアップロード（copyあり）。
5. EGL window surfaceへ描画し、<code>eglSwapBuffers</code>。
6. Spatial SDKの<code>VideoSurfacePanelRegistration</code>が供給する<code>Surface</code>に表示される。

候補: Kotlin (Spatial SDK / View/入力) + Rust (Smithay/scene state) + JNI/C++ (EGL/NDK)。ただしRust <code>NativeActivity</code> 等の特定構成を決め打ちしない。最初は**静止パターンの描画をAndroid Surfaceに出す**実験を独立させる。

[Spatial SDKの<code>VideoSurfacePanelRegistration</code> API](https://developers.meta.com/vr/reference/spatial-sdk/v0.8.1/com_meta_spatial_toolkit_videosurfacepanelregistration/)は動画以外のカスタムSurface描画にも利用できる。ただしリンク先は**SDK v0.8.1のAPIリファレンス**なので、実装時には採用するSDKバージョンのシグネチャ・対応機能を再確認する。Surface提供・破棄時のlifecycle、実際のbacking pixel size、色空間、各種パネル数の上限や更新タイミングをQuestで計測する。SDKの<code>PixelDisplayOptions</code>指定と実際のバッファサイズを同一視しない。

### Stage 2: GPU効率化（MVP後）

- <code>linux-dmabuf</code>、EGL import、<code>AHardwareBuffer</code>、同期フェンス対応は**将来の検証課題**。
- DMA-BUFとAHardwareBufferが無条件に相互互換という前提を置かない。modifier、format、ownership、fence、GPU driver、XR layerを評価。
- 当面はWaypipe <code>--no-gpu</code> 相当のCPU/SHM経路でPoCを行う。現時点で確認した[TermuxのWaypipeビルドレシピ](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)ではdmabuf/videoサポートが無効化されている。実装時にはその時点のレシピを再確認する。
- 「同一端末だからゼロコピー」は目標ではあるが、成立は未確認。

## Spatialウィンドウ管理と入力

1. <code>xdg_toplevel</code> の生成・破棄に応じて、Spatial SDK内でパネルentityを作成・破棄。
2. アプリ固有IDとパネルを対応させる。Wayland clientからのtitle/app_id変更を反映。
3. パネルの移動は空間位置の変更として扱う。リサイズは「物理メートル変更」か「再レイアウトする論理ピクセル変更」かを明示。
4. XRレイキャストから2D panel UV/ピクセル座標へ変換し、Wayland seatへpointerイベントを返す。
5. パネルを選択したらWayland keyboard focusを切替。Android KeyEventから適切なxkb keycode/modifiersへ変換。
6. popups・subsurfacesは親との相対配置/入力整合性を維持し、まずは親パネル内合成で扱う。
7. 初期はパススルー背景＋**1枚の平面パネル**。将来、複数、円筒状/大画面、配置永続化を実装する。XRパネルの実装パターンは[Meta Spatial SDK公式サンプル](https://github.com/meta-quest/Meta-Spatial-SDK-Samples)を参照する。

## SSH・権限・ライフサイクル

- Termuxは通常SSHポート8022を使用。**127.0.0.1のみにbind**できることを確かめて設定。LANへ公開しない。
- 公開鍵認証のみ。Androidアプリ側のprivate key保護、Termuxの<code>authorized_keys</code>、ホスト鍵ピン留め/known_hosts検証を設計。<code>StrictHostKeyChecking=no</code>は使わない。
- Termux起動やsshd準備が必要な場合、最初は手動でよい。後にIntent/Termux:Tasker等による起動自動化を検討するが、SSH/Wayland通信と混同しない。
- SSH接続のアプリ側実装はlibssh等の移植、あるいはAndroidアプリで実行可能なネイティブクライアントを比較する。最新Androidの実行ファイル制限やAPKパッケージングを確認する。
- **WaypipeはAndroidアプリ側に同梱または同等のproxy機能が必須**。Termuxに入れるだけでは成立しない。[Waypipeの起動モデル](https://man.archlinux.org/man/waypipe.1.en)と[Termuxパッケージのビルド条件](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)を確認しつつ、Bionic対応・依存ライブラリ・spawn・fd転送・socket配置を実機ゲートにする。
- XR Activity再生成、Surfaceの消失、SSH切断、Termuxバックグラウンド制限、Questスリープからの復帰をstate machineとして扱う。
- 認証情報、SSH内容、キーストローク、ウィンドウ画像をログへ恒常的に出力しない。

## 代替手段（主案を変更せずに保留）

- **Option A: Termux側compositor + Binder/FDブリッジ（[Anland: Termux方式](https://github.com/lfdevs/anland-termux/blob/main/docs/developer-guide.md)）**：SSH+WaypipeでAndroid側の移植が行き詰まった場合に評価。Anlandはブリッジ部分の**参考実装**であり、そのままXRウィンドウ管理を提供するわけではない。
- **Option B: VNC/noVNCで一枚のLinuxデスクトップを表示**：XRパネル/Surface/入力の単独検証に使う。類似の表示経路は[quest-termux-lab](https://github.com/MesmerPrism/quest-termux-lab)を参照する。ただし最終目標の独立ウィンドウとは別。
- **Option C: Linux PC + [WayVR](https://github.com/wayvr-org/wayvr)/WiVRn**：UI・配置の参考として使用可能。Quest単体MVPの必須条件にはしない。
- 将来的なremote host接続は、同じWayland受け入れ面へ第二のSSH接続先を追加する形で検討可能。

## 参考資料

- [Meta Spatial SDK — custom Surface rendering](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-media-playback/)
- [Meta Spatial SDK — VideoSurfacePanelRegistration API](https://developers.meta.com/vr/reference/spatial-sdk/v0.8.1/com_meta_spatial_toolkit_videosurfacepanelregistration/)
- [Meta Spatial SDK official samples](https://github.com/meta-quest/Meta-Spatial-SDK-Samples)
- [Smithay Wayland helpers](https://docs.rs/smithay/latest/smithay/wayland/)
- [Waypipe manual](https://man.archlinux.org/man/waypipe.1.en)
- [Termux Waypipe build recipe](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)
- [Termux package build system](https://github.com/termux/termux-packages)
- [WayVR — embedded Wayland compositor reference](https://github.com/wayvr-org/wayvr/tree/main/wayvr/src/backend/wayvr)
- [Anland: Termux — Android↔Wayland bridge reference](https://github.com/lfdevs/anland-termux/blob/main/docs/developer-guide.md)
- [quest-termux-lab — VNC/Spatial SDK baseline](https://github.com/MesmerPrism/quest-termux-lab)
