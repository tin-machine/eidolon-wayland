# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

**Status: design / early research — not yet implemented.**

Meta Quest 3向けの実験的な空間Waylandデスクトップ。QuestのAndroidアプリにWayland compositorを組み込み、**同じQuestのTermuxへlocalhost SSHで接続**し、**Waypipe**で転送した既存Waylandアプリを**Meta Spatial SDKの独立したXRパネル**として表示することを目指します。

最初の目標は、Termux上のfootをQuest空間に1枚のパネルとして表示し、Bluetoothキーボードとコントローラーで操作することです。Linux PC、WiVRn、root化を必須としない設計ですが、Android側のWaypipe/Wayland server移植とQuest実機動作は現時点で**未検証**です。

**実装方針:** KotlinはMeta Spatial SDK / Android adapter、RustはJNI・描画・SSH・Wayland compositor・ウィンドウ管理モデルを担当します。Waypipeは既存実装を再利用し、自作C++は必要性が確認された小さなshimに限定します。これは調査に基づく方針で、実装済みという意味ではありません。

**次の到達点:** UbuntuでEidolon固有の最小APKをビルドし、QuestのXRパネルにRustから返したbuild情報を表示するところまでをPhase 0の最初の実装単位にします。その後、Surface描画・SSH・Smithay/Waypipeを独立して検証します。APK自身のUIDでのWaypipe helper起動はPhase 0〜1から先行して試せます。

2026-11-01の[追加レビュー](docs/implementation-plan.md#追加レビュー2026-11-01)では、Meta公式サンプルのEGL描画例、Build Toolsの要件、compositor layer数、XR session focus、Android helperの実行制約を反映しました。**Eidolonのビルド・実機動作は引き続き未検証**です。

## Docs

- [アーキテクチャ設計](docs/architecture.md) — Kotlin/Rustの責務、SSH/Waypipe、描画、入力、セキュリティ
- [実装計画](docs/implementation-plan.md) — 段階的な実装、通信図、技術検証ゲート、受け入れ条件、ADR
- [Phase 0 — 開発環境](docs/phase-0-development-environment.md) — Ubuntuビルド、Rust cdylib、Quest USB、Termux/Codexの調査
- [Phase 1 — Rust描画](docs/phase-1-rust-rendering.md) — JNI、NDK、SDK Surface、EGL/GLES
- [Phase 2 — Rust SSH](docs/phase-2-rust-ssh.md) — russh、認証、StreamLocal forwarding
- [Phase 3 — Compositor / Waypipe](docs/phase-3-rust-compositor-waypipe.md) — SmithayのAndroid移植、既存Waypipe再利用
- [Phase 4 — ウィンドウ描画](docs/phase-4-rust-window-rendering.md) — GlesRenderer、SHM import、Android EGL adapter
- [Phase 5 — ウィンドウ管理](docs/phase-5-rust-window-manager.md) — Rustの状態モデル、Kotlin adapter、focus・配置・入力
- [Phase 6 — 寿命と検証](docs/phase-6-rust-lifecycle-testing.md) — スレッド・所有権、再接続、性能・耐障害性

開発を開始する際は、まず[実装計画のP0スパイク](docs/implementation-plan.md#最初に潰すべき不確実性スパイク)から進めてください。
