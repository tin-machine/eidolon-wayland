# Phase 6 — Rust coreのライフサイクル・性能・検証

**状態:** 設計／調査反映、実機未検証（2026-11-01）  
**上位計画:** [Phase 6](implementation-plan.md#phase-6--運用性性能耐障害性) / [全体設計](architecture.md)  
**前提:** [Rust renderer](phase-4-rust-window-rendering.md) / [Rust WindowManager](phase-5-rust-window-manager.md)

## 所有者とスレッドを固定する

Rustへ寄せる目的は言語数削減だけでなく、状態と破棄順序を一元化すること。採用案は次の三領域である。

| 実行領域 | 所有するもの | 境界 |
| --- | --- | --- |
| Kotlin SDK/UI thread | Activity、SDK Entity、Surface通知、権限UI | 小さなJNI command/event |
| Rust compositor/render owner thread | Smithay state、WindowManager、EGL context、renderer、window buffer | bounded command queue |
| Rust Tokio runtime | russh、socket bridge、timeout、retry、I/O counters | 状態イベント・停止要求 |

Smithay [`GlesRenderer`](https://docs.rs/smithay/0.7.0/smithay/backend/renderer/gles/struct.GlesRenderer.html)の`!Send`／`!Sync`と、[Android EGLのthread規約](https://source.android.com/docs/core/graphics/arch-egl-opengl)を前提にする。SSHのasync taskにGL所有者を移さず、UI threadから重いrendererを同期的に呼び出さない。

```mermaid
flowchart LR
  K["Kotlin lifecycle adapter"] -->|"pause / resume / SurfaceLost"| S["Rust session supervisor"]
  S <-->|"control / status"| R["Rust compositor + render owner"]
  S <-->|"connect / cancel / retry"| N["Tokio + russh bridge"]
  N <-->|"SSH loopback"| T["Termux sshd / Waypipe / apps"]
  R --> M["redacted metrics / logcat"]
  N --> M
```

## 三つの寿命を混同しない

**Android Activity/Surface、SSH transport、Wayland client sessionは別々の寿命**を持つ。

Surfaceの再生成だけならSSHやWaylandを再起動する必要はない設計を目指す。反対にSSHが再接続できても、元のWaylandアプリが保持されるとは限らない。Waypipeのreconnectionを追加実装・検証しないMVPでは、**切断後は新しいセッションとして起動し直す**ことを明示する。「再接続＝作業内容を必ず復元」と約束しない。

`SessionGeneration`、`WindowId`、`SurfaceGeneration`を分離し、旧channelのframeやcallbackが新セッションのwindowに適用されないようにする。停止処理は冪等にする。

参考: [Local DesktopのSurface suspend/resume実装](https://github.com/localdesktop/localdesktop.github.io/blob/fdd0275d6ec7840277def8ccd3ba3ed463900ca1/src/android/backend/wayland/winit_backend.rs)、[Waypipe付属マニュアル](https://man.archlinux.org/man/waypipe.1.en)、[Anlandのライフサイクル／接続方式](https://github.com/lfdevs/anland-termux/blob/main/docs/developer-guide.md)。方式は異なるので、そのままコピーせず障害境界の参考にする。

## キュー・backpressure・復旧

- メモリ上限を持つqueueを使い、フレームが増えた場合は古い表示要求のcoalesceを行う。ただしprotocol処理やbuffer解放をdropしない。
- SSHの接続／認証／forwarding／execを別のエラーとして報告する。unknown host keyを自動retryで承認しない。
- EOF／half-close／cancel時にbridge taskを終了し、child helperの寿命とsocket cleanupを回収する。[Tokio copy_bidirectional](https://docs.rs/tokio/latest/tokio/io/fn.copy_bidirectional.html)の挙動もテストする。
- JNIのGlobalRef、NativeWindow、EGLSurface、texture、Wayland buffer、SSH channelの所有者と解放契機を一覧化する。
- RustのpanicをJNI境界越しにunwindさせない。異常なnative状態を無理に継続せず、安全に失敗を表示できる経路を作る。
- APKとTermuxのバックグラウンド制限／OOM killはRustでは回避できない。停止検出と再起動案内を設計する。

## 性能の測定順序

1. APK内だけのRust pattern描画。
2. SHMをRust rendererへ投入するCPU upload。
3. localhost SSH＋Waypipeを通したfoot一枚。
4. 複数window、resize、scroll、キー入力。
5. 調査結果に応じてGPU import、圧縮、damaged areaの最適化。

描画時間、present待ち、SHM upload量、Waypipe転送量、SSH CPU、queue depth、FD数、native memory、Surface再生成回数を比較する。フレーム更新数とHMD refresh rateを同一視しない。

DMA-BUF／AHardwareBuffer／zero-copyは機能ゲートを別に置く。SSH経路で転送が生じること、buffer形式・modifier・fenceの整合が必要なことを前提にし、Rustにしただけで低遅延やゼロコピーを保証しない。

## 試験と合格条件

- [ ] WindowManagerの純粋なRust状態遷移テストをhost CIで実行。
- [ ] JNI/Surfaceの生成・破棄・再attachを繰り返し、FD／memoryが増え続けないことを確認。
- [ ] wrong host key、sshd停止、Waypipe終了、Termux kill、Quest sleep/resumeを別々に試す。
- [ ] 再接続100回、30分／2時間運転など既存planの試験を実施し、限界を記録。
- [ ] キー配列・modifier・focus・popup・clipboardを回帰試験する。
- [ ] 依存crate、Waypipe版、native patch、ライセンス、Cargo.lock、SDK版を記録。
- [ ] ログに鍵、token、入力文字、window pixelsを残さない。

実機がないCIではビルド／hostロジックまでを検証し、QuestのXR表示を合格扱いにしない。参照したRust crateの存在、Androidの先行例、Eidolon実機試験の三段階を報告書でも区別する。
