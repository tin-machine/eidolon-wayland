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

## 採用言語と責務（2026-11-01のRust実現性調査を反映）

**Kotlin＋Rustを新規実装の中心とする。** 自作C++を最初から必須にせず、既存CライブラリやWaypipeは再利用し、Rust bindingで不足する具体的な箇所に限って薄いshimを許容する。RustでNDK/EGL/GLESを再実装するのではなく、RustからAndroidのAPI・GPUドライバーを呼ぶ構成である。

| レイヤー | 第一候補 | 境界・詳細資料 |
| --- | --- | --- |
| Android / Meta Spatial SDK adapter | Kotlin | Activity、SDK Entity、Surface、権限・IME UI。モデルの正本は持たない |
| JNI / NDK連携 | Rust `jni`、`ndk` | Kotlin→Rust cdylib。[Phase 0](phase-0-development-environment.md)・[Phase 1](phase-1-rust-rendering.md) |
| Wayland compositor | Rust / Smithay | protocol、surface tree、seat、buffer lifetime。[Phase 3](phase-3-rust-compositor-waypipe.md) |
| XRウィンドウ管理モデル | Rust | WindowId、focus、pose、size、保存・復旧。KotlinはSDK操作を実行。[Phase 5](phase-5-rust-window-manager.md) |
| 2Dウィンドウ描画 | Rust / EGL + GLES、Smithay GlesRenderer候補 | SDKが渡したSurfaceへ出力。XR空間そのものの最終rendererはSDK/runtime。[Phase 1](phase-1-rust-rendering.md)・[Phase 4](phase-4-rust-window-rendering.md) |
| SSH transport / supervisor | Rust / russh + Tokio | host key検証、公開鍵認証、exec、StreamLocal forwarding。[Phase 2](phase-2-rust-ssh.md)・[Phase 6](phase-6-rust-lifecycle-testing.md) |
| Waypipe | 上流実装を再利用 | Termuxは調査時点でC版をbuild。Eidolonで再実装しない |
| ビルド・補助 | Gradle Kotlin DSL / Cargo / Shell | Android NDKとnative依存は引き続き必要 |

**確認できた根拠:** [jni](https://docs.rs/jni/0.22.4/jni/)、[ndk NativeWindow](https://docs.rs/ndk/0.9.0/ndk/native_window/struct.NativeWindow.html)、[khronos-egl](https://docs.rs/khronos-egl/6.0.0/khronos_egl/)、[russh StreamLocal API](https://docs.rs/russh/0.65.0/russh/client/struct.Handle.html#method.streamlocal_forward)が存在する。また、[Local Desktop](https://github.com/localdesktop/localdesktop.github.io)にはAndroid上のRust＋Smithayの先行実装がある。

**未確認:** 上記をMeta Spatial SDKのSurface、QuestのGPU、TermuxのWaypipeと組み合わせたEidolonの動作。調査は公式資料とソースの確認までで、Androidビルド／Quest実機テストは実施していない。既存部品の存在・先行例・Eidolonでの検証を混同しない。

## 全体像

~~~mermaid
flowchart TD
  subgraph Quest["Meta Quest 3 / Horizon OS"]
    subgraph APK["Eidolon APK / Android UID A"]
      UI["Kotlin: Spatial SDK adapter / Entity / Surface"]
      MODEL["Rust: WindowManager / focus / pose"]
      COMP["Rust: Smithay Wayland compositor"]
      RENDER["Rust: EGL / GLES renderer"]
      WPLOCAL["既存Waypipe client / 表示側"]
      SSH["Rust: russh / SSH + StreamLocal bridge"]
      UI <-->|"JNI commands / lifecycle"| MODEL
      MODEL <--> COMP
      COMP --> RENDER
      UI -->|"Surface handle"| RENDER
      RENDER -->|"Surfaceへpresent"| UI
      COMP <-->|"Wayland socket + fd"| WPLOCAL
      WPLOCAL <-->|"転送socket"| SSH
    end
    subgraph TERMUX["Termux / Android UID B"]
      SSHD["sshd 127.0.0.1:8022"]
      WPREMOTE["既存Waypipe server / アプリ側"]
      APPS["foot / GTK・Qt Wayland apps"]
      SSHD <-->|"StreamLocal socket"| WPREMOTE
      WPREMOTE <-->|"Wayland Unix socket"| APPS
    end
    SSH <-->|"SSH over TCP loopback"| SSHD
  end
~~~

図は**提案**であり検証済みではない。Waypipeの「client/local」は**画面を表示する側**（APK）、「server/remote」は**アプリを動かす側**（Termux）。Waylandでの役割はEidolonがserver、footがclient。物理的には同じQuest内である。[Waypipe付属マニュアル](https://man.archlinux.org/man/waypipe.1.en)を参照。

## 通信の責務境界

| 区間 | 想定方式 | 送るもの | 留意点 |
| --- | --- | --- | --- |
| APK内 compositor ⇄ Waypipe client | 同一UIDのWayland Unix socket | requests/events、buffer fd等 | APKのprivate runtime directory |
| APK内 Waypipe ⇄ Termux Waypipe | russhによるSSH StreamLocal forwarding | Waypipe固有の転送データ、SHM更新等 | SSH単体でWayland fdを転送しない |
| Termux Waypipe ⇄ アプリ | Termux UID内のWayland Unix socket | surface/commit、SHM fd、入力 | Waypipeがcompositorの代理になる |
| Rust core ⇄ Kotlin adapter | JNIの小さいcommand/event | WindowId、pose、入力、Surface lifecycle | 同一APK。毎frameの画素配列は渡さない |
| Rust renderer → Spatialパネル | Android Surface / ANativeWindow / EGL | ウィンドウの描画フレーム | Surface寿命、buffer解像度、present |
| 入力 → Rust compositor | Android KeyEvent / XR ray-hit → JNI → seat | key、modifier、pointer、focus | IME・US/JIS変換を別途検証 |

**重要:** Termuxのprivate socketへAPKから直接アクセスするのでなく、SSH＋WaypipeでUID境界を越える。Android側にもWaypipe実行機能が必要で、Termuxにだけ導入しても成立しない。

SSHはプロセス起動・認証・streamの経路であって、端末描画サーバーではない。WaypipeがWayland protocolとSHMを転送可能な形にする。russhのStreamLocal APIは利用可能だが、incoming channelとローカルUnixStreamをつなぐadapter、EOF／cancel／backpressureは自作する。[Phase 2の詳細](phase-2-rust-ssh.md)を参照。

## Wayland compositorの具体的責務

第一候補はRust + [Smithay](https://docs.rs/smithay/latest/smithay/wayland/)。ウィンドウ管理の参考に[WayVRの実装](https://github.com/wayvr-org/wayvr/tree/main/wayvr/src/backend/wayvr)、Android移植の参考に[Local Desktopのpatch記録](https://localdesktop.github.io/docs/developer/patches/)を利用する。Android/aarch64のbuild可否、features、libxkbcommon等は[Phase 3](phase-3-rust-compositor-waypipe.md)で先に検証する。

- Wayland server、client接続、event loop、surface treeの管理。
- wl_compositor、wl_shm、xdg_wm_base、xdg_surface、xdg_toplevel、wl_seat等。
- configure/ack、attach/commit、damage、wl_buffer.release、frame callback。
- subsurface、popup、座標変換、keyboard/pointer focus、keymap、repeat、clipboard。
- `WindowId → logical window → Kotlin SDK Entity → SurfaceGeneration`の対応。
- Wayland logical size、buffer scale、XRのメートル寸法、pixel解像度を分離。

**Linux DRM/KMS/GBMの物理モニター出力をQuestへ持ち込まない。** compositorに必要なのはアプリの画像管理とAndroid Surfaceへの出力である。SDK側の利用方法は[公式サンプル](https://github.com/meta-quest/Meta-Spatial-SDK-Samples)を参照。NativeActivity/winitを追加起動するのではなく、Spatial SDKが所有するActivity/Surfaceへ接続する。

## 描画バックエンド

### Stage 1: CPU/SHM経路

1. Termuxのアプリがwl_shm bufferをcommitする。
2. Waypipeがprotocolとbuffer内容をSSH経由で転送・再構成する。
3. RustのSmithay compositorがsurfaceとbufferを管理する。
4. Rust rendererがSHMをGLESへuploadし、SDK Surfaceに対応するEGLSurfaceへ合成する。
5. eglSwapBuffersで提出し、Spatial SDK／XR runtimeが視界に配置する。

[Phase 1](phase-1-rust-rendering.md)は`jni + ndk + khronos-egl + glow`で小さく検証し、[Phase 4](phase-4-rust-window-rendering.md)はSmithay `GlesRenderer`のSHM importと合成を再利用する候補を評価する。**JNI/C++を必須の中間層にはしない。** Rust bindingに実際の不足があればその範囲だけshimを許容する。

[VideoSurfacePanelRegistrationの旧版API説明](https://developers.meta.com/vr/reference/spatial-sdk/v0.8.1/com_meta_spatial_toolkit_videosurfacepanelregistration/)と[現行media guide](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-media-playback/)を参照。採用SDKのシグネチャ、Surface lifecycle、buffer解像度を実測し、`PixelDisplayOptions`を指定するだけで実際のproducer buffer寸法が決まるとは仮定しない。

### Stage 2: GPU効率化

linux-dmabuf、EGL import、AHardwareBuffer、fenceはMVP後の検証課題。DMA-BUFとAHardwareBufferの無条件互換や、SSH＋Waypipe経路でのzero-copyを前提にしない。まずWaypipeのno-gpu/SHM経路で成立させ、[Phase 6](phase-6-rust-lifecycle-testing.md)で計測してから最適化する。

[確認したTermuxのWaypipeレシピ](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)はC版を選びdmabuf/videoを無効化している。一方で上流には[作者によるRust移行](https://mstoeckl.com/notes/code/waypipe_to_rust.html)がある。**Waypipeは上流を再利用し、言語統一のためにEidolonで書き直さない。**

## Spatialウィンドウ管理と入力

**Rustを状態の正本とする。** WindowId、focus、pose、size、配置保存、セッション世代をRustで持ち、KotlinはSDKのEntity作成／変形／破棄と入力取得を担当する。[詳細な分担とイベント案](phase-5-rust-window-manager.md)を参照。

1. xdg_toplevelの生成・破棄に応じRustからKotlinへpanel commandを送る。
2. title/app_id、親子関係をRustで管理する。
3. メートル寸法の変更とWayland resize交渉を別操作にする。
4. XR ray-hit／UVをRustへ渡し、surfaceのlogical座標へ変換する。
5. Android KeyEventをWaylandのkeycode／modifierへ変換しseatへ配送する。
6. popup/subsurfaceはまず親パネル内合成で扱い、親外領域のclip/拡張と入力原点を決める。
7. 初期はパススルー＋平面パネル、後に複数・湾曲・配置永続化へ広げる。

## SSH・権限・ライフサイクル

- Termuxのsshdは127.0.0.1:8022に限定し、LANに公開しない。
- 公開鍵認証、host key pinning/known_hosts検証を行う。検証の迂回はしない。
- SSHはRustのrusshを第一候補にし、SSHJ/libsshは問題が確認された場合の代案とする。StreamLocal APIがあるだけでは中継完了ではない。[Phase 2](phase-2-rust-ssh.md)を参照。
- Android Keystoreと非exportable鍵をrusshへ接続する部分は別spike。Kotlin adapterが残ることを許容する。
- WaypipeはAPK UID内で起動できる同梱helperを第一候補とする。Termuxの実行ファイルをコピーするだけで動くとは仮定しない。
- Kotlin UI／Rust compositor-render owner／Tokio SSH runtimeを分離する。GlesRendererのthread制約を守る。
- Activity/Surface、SSH transport、Wayland sessionの寿命を分ける。再接続しても旧アプリ状態が必ず戻るとは保証しない。[Phase 6](phase-6-rust-lifecycle-testing.md)を参照。
- Termuxの起動とsshd準備は初期は手動でよい。Intent等による自動化は通信路と混同しない。
- 認証情報、キーストローク、画面内容を恒常的にログへ出さない。

## 代替手段（主案は維持）

- **Termux側compositor＋Binder/FDブリッジ:** [Anlandの参考実装](https://github.com/lfdevs/anland-termux/blob/main/docs/developer-guide.md)。APK側移植に構造的な問題が残る場合に比較する。XR window managerをそのまま提供するものではない。
- **VNCで一枚のdesktop:** [quest-termux-lab](https://github.com/MesmerPrism/quest-termux-lab)。パネル表示の比較には使えるが独立したWayland windowの最終目標とは別。
- **Linux PC＋WayVR/WiVRn:** [WayVR](https://github.com/wayvr-org/wayvr)。UIの参考。Quest単体MVPの必須条件にはしない。
- **libwayland-server＋薄いC ABI:** Rust/Smithayに解消できない問題がある場合の比較候補。全体をC++へ置き換える判断とは分離する。

## 参考資料とフェーズ別調査

- [Meta Spatial SDK media panels](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-media-playback/) / [公式サンプル](https://github.com/meta-quest/Meta-Spatial-SDK-Samples)
- [jni](https://docs.rs/jni/0.22.4/jni/) / [NDK NativeWindow](https://docs.rs/ndk/0.9.0/ndk/native_window/struct.NativeWindow.html) / [Android EGL](https://source.android.com/docs/core/graphics/arch-egl-opengl)
- [Smithay](https://github.com/Smithay/smithay) / [Local Desktop](https://github.com/localdesktop/localdesktop.github.io) / [WayVR](https://github.com/wayvr-org/wayvr)
- [russh](https://github.com/Eugeny/russh) / [Waypipe manual](https://man.archlinux.org/man/waypipe.1.en) / [Termux build recipe](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)
- 詳細は[実装計画のフェーズ別リンク](implementation-plan.md#rust中心構成の調査と詳細ファイル2026-11-01)から参照する。
