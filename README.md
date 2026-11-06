# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

**Status（2026-11-06）: 二枚のfoot、入力・配置・背景・Rustテーマを実装し、通常SDKの回帰検査まで成功。HMDで文字・部屋の表示とUSキーボードの主要操作を確認済み。残りの物理確認を保持し、停止・復旧・初期設定と長時間測定を先行開発中。**

Meta Quest 3向けの実験的な空間Waylandデスクトップ。QuestのAndroidアプリにWayland compositorを組み込み、**同じQuestのTermuxへlocalhost SSHで接続**し、**Waypipe**で転送した既存Waylandアプリを**Meta Spatial SDKの独立したXRパネル**として表示することを目指します。

Termux上のfootを独立した二枚のパネルへ表示し、キー・ray入力、focus、resize、配置保存、popup・session内clipboardを扱います。Linux PC、WiVRn、root化を通常利用の必須条件にしない設計です。受入範囲は[進捗](docs/progress.md)と[物理確認の記録](docs/phase5-manual-acceptance.md)を分けて管理しています。

**起動方法:** Questのライブラリ→「提供元不明 / Unknown Sources」→**Eidolon**。Build124以前は**Eidolon Phase 0**です。再起動後は先にTermuxのSSHサービスを起動します。[利用・初回ペアリング・再起動後の手順](docs/usage.md)を参照してください。

**現実の部屋を背景にするMR表示**には、Meta Spatial SDKの通常のpassthroughを利用します。OSがカメラ映像とXRパネルを合成し、Eidolonがカメラ画素を取得する必要はありません。今回、最小Composeパネルと背景の同時表示をユーザーがHMD内で確認しました。SDK SurfaceへのRust描画も自動検査を進めています。[背景方式の補足](docs/phase-1-passthrough-background.md)と[最新の受入範囲](docs/progress.md)を参照してください。

**実装方針:** KotlinはMeta Spatial SDK / Android adapter、RustはJNI・描画・SSH・Wayland compositor・ウィンドウ管理モデルを担当します。Waypipeは既存実装を再利用し、自作C++は必要性が確認された小さなshimに限定します。これは調査に基づく方針で、実装済みという意味ではありません。

**ビルドと検査:** `scripts/build-android VERSION_CODE`でdebug、`scripts/build-release VERSION_CODE`でunsigned releaseを生成します。versionCodeには端末の導入版以上の正整数を指定してください。JDK 25・compileSdk 36・target API 34・Spatial SDK 0.15.0を採用しています。`scripts/check-host --suite all`がホスト検査、`scripts/quest-check`が固定caseのQuest検査です。native instrumentation・通常SDK・HMD物理確認は別の結果として保存します。詳細は[利用手順](docs/usage.md)、[進捗・失敗記録](docs/progress.md)を参照してください。

**実装設計:** [詳細設計](docs/design/README.md)と[tasks.json](docs/design/tasks.json)にAPI・状態遷移・検証条件を記録しています。Phase 5の通常SDK回帰とテーマの自動検査を終え、残る手動項目を保持したままPhase 6の独立実装を進めています。

**本格実装前の小さな検証:** 独立debug APKでAPK UIDのhelper実行、Rust→SDK Surfaceの4色表示と再生成を実機確認しました。[検証・スリープ設定・ADB撮影と入力の記録](docs/native-preflight-20261104.md)に、手順、失敗、確認範囲を残しています。

**次の到達点:** [Phase 6](docs/design/phase-6.md)の停止・障害復旧・初回設定・長時間動作の検査を進めます。テーマの光学的な見え方と残りの物理操作は最後に確認します。

**開発時の接続（2026-11-04実測）:** USBで初回デバッグ承認と端末照合を済ませ、USB-Cを給電器へ回した状態でWi-Fi経由のAPK導入・更新・起動・ログ・Android画像取得とTermux SSHに成功しました。ADBの切断・再接続後もSSHを復旧できました。USB経由のAPK導入、長時間の残量維持、sleep／Quest再起動後の自動復旧は未検証です。

2026-11-01の[追加レビュー](docs/implementation-plan.md#追加レビュー2026-11-01)では、Meta公式サンプルのEGL描画例、Build Toolsの要件、compositor layer数、XR session focus、Android helperの実行制約を反映しました。当時は設計調査のみで、実装・実機結果は上記2026-11-04の記録へ追加しています。

**ローカル環境レビュー:** 既存SDK／NDK、JDK 25、有線LANの複数経路を[実測](docs/phase-0-development-environment.md#30-既存環境の確認結果2026-11-04)し、Quest API 34と新しいビルド用APIを区別しました。SSH手順、SHMの安全な読取り、描画待ちの影響、表示PoCと操作MVPの判定も修正しています。

## Docs

- [利用・起動・初回ペアリング](docs/usage.md) — ADBなしの公開鍵設定、Termux起動、release、長時間測定

- [キーボードの検証記録](docs/keyboard-checks.md) — US/JISの実wire・通常Hostの固定入力・押下解除・失敗の切り分けと未確認範囲

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
