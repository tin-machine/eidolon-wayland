# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

**Status: XR/SSH基盤とWayland protocol・SHM・window modelをQuest APK内で検証済み（2026-11-05）。Waypipe/SDKパネル統合と入力は開発中。**

Meta Quest 3向けの実験的な空間Waylandデスクトップ。QuestのAndroidアプリにWayland compositorを組み込み、**同じQuestのTermuxへlocalhost SSHで接続**し、**Waypipe**で転送した既存Waylandアプリを**Meta Spatial SDKの独立したXRパネル**として表示することを目指します。

現在の開発目標はPhase 5まで進め、Termux上のfoot等を独立した2枚のパネルとして表示し、物理キーボード・コントローラー入力、focus、resize、配置保存、popup・限定clipboardを使えるようにすることです。Linux PC、WiVRn、root化を必須としない設計です。Wayland serverのnative部分はAPK UIDで検証済みで、WaypipeとSDKパネルへの接続は後続タスクです。

**現実の部屋を背景にするMR表示**には、Meta Spatial SDKの通常のpassthroughを利用します。OSがカメラ映像とXRパネルを合成し、Eidolonがカメラ画素を取得する必要はありません。今回、最小Composeパネルと背景の同時表示をユーザーがHMD内で確認しました。SDK SurfaceへのRust描画も自動検査を進めています。[背景方式の補足](docs/phase-1-passthrough-background.md)と[最新の受入範囲](docs/progress.md)を参照してください。

**実装方針:** KotlinはMeta Spatial SDK / Android adapter、RustはJNI・描画・SSH・Wayland compositor・ウィンドウ管理モデルを担当します。Waypipeは既存実装を再利用し、自作C++は必要性が確認された小さなshimに限定します。これは調査に基づく方針で、実装済みという意味ではありません。

**現在できること:** `scripts/build-android 45`でRust runtime/helperを含むAPKをビルド・lint・静的検査できます。QuestではEGLパネル、専用鍵によるSSH、StreamLocal echo・障害回収、Smithay/native依存、SHM、window寿命・変換・popupをAPK UIDで検証しています。`scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case native-deps --instrumentation`等はActivityを開かずAPK UIDで検査し、UI復帰と区別して結果を保存します。JDK 25・compileSdk 36・target API 34・Spatial SDK 0.15.0を採用しています。HMD・実入力は最終段階の確認に残しています。[再現手順](docs/phase-0-reproduction.md)、[製品実装の進捗・失敗記録](docs/progress.md)、[native依存の固定build](docs/native-dependencies-build.md)、[window stateの実機検証](docs/wayland-window-state-checks.md)を参照してください。

**残フェーズの実装設計:** [Sol向け引継ぎ](docs/design/README.md)に31タスク、44検証、ファイル・API・状態遷移・失敗時の判断を整理しました。B01/B03/C01/H01等は受入済みです。T04/W01〜W03はnative検証が成立し、通常UI/HMDの確認を残しています。Waypipe経由の表示と製品入力は後続タスクです。[進捗と試行記録](docs/progress.md)を参照してください。

**本格実装前の小さな検証:** 独立debug APKでAPK UIDのhelper実行、Rust→SDK Surfaceの4色表示と再生成を実機確認しました。[検証・スリープ設定・ADB撮影と入力の記録](docs/native-preflight-20261104.md)に、手順、失敗、確認範囲を残しています。

**次の到達点:** APK向けWaypipeを固定sourceからbuildし、検証済みSSH・Wayland serverへつないで、既知画像をSDKパネルに表示します。その後、footと入力・複数windowへ進み、HMD確認を最終段階にまとめます。

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

開発再開時は[詳細設計](docs/design/README.md)・[進捗](docs/progress.md)・[tasks.json](docs/design/tasks.json)を読み、未受入taskの自動/手動の残件を区別して依存順に進めてください。従来のP0スパイクは今回の具体的なtaskへ分解しました。

デバッグ方法・実機テスト・自動化の拡張は[専用文書](docs/development-automation-and-debugging.md)を参照してください。2026-11-03の調査案と、今回実装した最小smokeの範囲を区別しています。
