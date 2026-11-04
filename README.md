# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

**Status: Phase 0の最小XR APK・給電＋Wi-Fi開発基盤を実機確認済み（2026-11-04）。Waylandデスクトップは開発中。**

Meta Quest 3向けの実験的な空間Waylandデスクトップ。QuestのAndroidアプリにWayland compositorを組み込み、**同じQuestのTermuxへlocalhost SSHで接続**し、**Waypipe**で転送した既存Waylandアプリを**Meta Spatial SDKの独立したXRパネル**として表示することを目指します。

最初の製品目標は、Termux上のfootをQuest空間に1枚のパネルとして表示し、Bluetoothキーボードとコントローラーで操作することです。Linux PC、WiVRn、root化を必須としない設計ですが、Android側のWaypipe/Wayland server移植は現時点で**未実装・未検証**です。

**現実の部屋を背景にするMR表示**には、Meta Spatial SDKの通常のpassthroughを利用します。OSがカメラ映像とXRパネルを合成し、Eidolonがカメラ画素を取得する必要はありません。今回、最小Composeパネルと背景の同時表示をユーザーがHMD内で確認しました。Rust描画との統合は[Phase 1の補足調査](docs/phase-1-passthrough-background.md)に沿って今後検証します。

**実装方針:** KotlinはMeta Spatial SDK / Android adapter、RustはJNI・描画・SSH・Wayland compositor・ウィンドウ管理モデルを担当します。Waypipeは既存実装を再利用し、自作C++は必要性が確認された小さなshimに限定します。これは調査に基づく方針で、実装済みという意味ではありません。

**現在できること:** `scripts/build-android 2`でAPKをビルド・lintし、`scripts/quest-smoke`で指定Questへ更新・起動・ログ取得、`scripts/quest-termux`でTermuxへSSH接続できます。JDK 25・compileSdk 36・Spatial SDK 0.15.0を採用し、Quest API 34でBuild 1→2の表示・ボタン操作を確認しました。[再現手順](docs/phase-0-reproduction.md)と[失敗も含む実機記録](docs/phase-0-troubleshooting-20261104.md)を参照してください。

**残フェーズの実装設計:** [Sol向け引継ぎ](docs/design/README.md)に31タスク、43検証、ファイル・API・状態遷移・失敗時の判断を整理しました。実装はB01から開始します。

**次の到達点:** 既存パネルにRustから返したbuild情報を表示し、Gradle→Cargo→APK同梱を検証します。その後、Surface描画・APK内SSH・Smithay/Waypipeを独立して進めます。

**開発時の接続（2026-11-04実測）:** USBで初回デバッグ承認と端末照合を済ませ、USB-Cを給電器へ回した状態でWi-Fi経由のAPK導入・更新・起動・ログ・Android画像取得とTermux SSHに成功しました。ADBの切断・再接続後もSSHを復旧できました。USB経由のAPK導入、長時間の残量維持、sleep／Quest再起動後の自動復旧は未検証です。

2026-11-01の[追加レビュー](docs/implementation-plan.md#追加レビュー2026-11-01)では、Meta公式サンプルのEGL描画例、Build Toolsの要件、compositor layer数、XR session focus、Android helperの実行制約を反映しました。当時は設計調査のみで、実装・実機結果は上記2026-11-04の記録へ追加しています。

**ローカル環境レビュー:** 既存SDK／NDK、JDK 25、有線LANの複数経路を[実測](docs/phase-0-development-environment.md#30-既存環境の確認結果2026-11-04)し、Quest API 34と新しいビルド用APIを区別しました。SSH手順、SHMの安全な読取り、描画待ちの影響、表示PoCと操作MVPの判定も修正しています。

## Docs

- [残フェーズの詳細設計・Solへの引継ぎ](docs/design/README.md) — 31タスクの依存順、共通契約、検証条件、一次資料

- [Phase 0の再現手順](docs/phase-0-reproduction.md) — 現在のbuild、Wi-Fi ADB、XR smoke、Termux SSH、復旧
- [実機・トラブルシューティング記録](docs/phase-0-troubleshooting-20261104.md) — 失敗した試行、原因、修正、検証結果と生ログ索引
- [アーキテクチャ設計](docs/architecture.md) — Kotlin/Rustの責務、SSH/Waypipe、描画、入力、セキュリティ
- [実装計画](docs/implementation-plan.md) — 段階的な実装、通信図、技術検証ゲート、受け入れ条件、ADR
- [開発自動化・デバッグ](docs/development-automation-and-debugging.md) — Codex／ADB／Meta VR CLI、XR画像取得、入力自動化、実機CI、未検証条件とsmoke test案
- [Phase 0 — 開発環境](docs/phase-0-development-environment.md) — Ubuntuビルド、Rust cdylib、USB初期接続から給電＋Wi-Fi更新への移行検証、Termux/Codexの調査
- [Phase 1 — Rust描画](docs/phase-1-rust-rendering.md) — JNI、NDK、SDK Surface、EGL/GLES
- [Phase 1補足 — Passthrough背景](docs/phase-1-passthrough-background.md) — 現実の映像を背景にする方式、SDK設定、MR／VR切替と検証
- [Phase 2 — Rust SSH](docs/phase-2-rust-ssh.md) — russh、認証、StreamLocal forwarding
- [Phase 3 — Compositor / Waypipe](docs/phase-3-rust-compositor-waypipe.md) — SmithayのAndroid移植、既存Waypipe再利用
- [Phase 4 — ウィンドウ描画](docs/phase-4-rust-window-rendering.md) — GlesRenderer、SHM import、Android EGL adapter
- [Phase 5 — ウィンドウ管理](docs/phase-5-rust-window-manager.md) — Rustの状態モデル、Kotlin adapter、focus・配置・入力
- [Phase 6 — 寿命と検証](docs/phase-6-rust-lifecycle-testing.md) — スレッド・所有権、再接続、性能・耐障害性

開発再開時は[詳細設計](docs/design/README.md)と[tasks.json](docs/design/tasks.json)を読み、B01から依存順に進めてください。従来のP0スパイクは今回の具体的なtaskへ分解しました。

デバッグ方法・実機テスト・自動化の拡張は[専用文書](docs/development-automation-and-debugging.md)を参照してください。2026-11-03の調査案と、今回実装した最小smokeの範囲を区別しています。
