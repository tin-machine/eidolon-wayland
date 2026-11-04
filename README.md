# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

**Status: design / early research — not yet implemented.**

Meta Quest 3向けの実験的な空間Waylandデスクトップ。QuestのAndroidアプリにWayland compositorを組み込み、**同じQuestのTermuxへlocalhost SSHで接続**し、**Waypipe**で転送した既存Waylandアプリを**Meta Spatial SDKの独立したXRパネル**として表示することを目指します。

最初の目標は、Termux上のfootをQuest空間に1枚のパネルとして表示し、Bluetoothキーボードとコントローラーで操作することです。Linux PC、WiVRn、root化を必須としない設計ですが、Android側のWaypipe/Wayland server移植とQuest実機動作は現時点で**未検証**です。

**現実の部屋を背景にするMR表示**には、Meta Spatial SDKの通常のpassthroughを利用する方針です。OSがカメラ映像とXRパネルを合成し、Eidolonがカメラ画素を取得する必要はありません。[Phase 1の補足調査](docs/phase-1-passthrough-background.md)に公式資料・実装例と検証計画をまとめました。Eidolonでの背景表示も未実装・実機未検証です。

**実装方針:** KotlinはMeta Spatial SDK / Android adapter、RustはJNI・描画・SSH・Wayland compositor・ウィンドウ管理モデルを担当します。Waypipeは既存実装を再利用し、自作C++は必要性が確認された小さなshimに限定します。これは調査に基づく方針で、実装済みという意味ではありません。

**次の到達点:** UbuntuでEidolon固有の最小APKをビルドし、QuestのXRパネルにRustから返したbuild情報を表示するところまでをPhase 0の最初の実装単位にします。その後、Surface描画・SSH・Smithay/Waypipeを独立して検証します。APK自身のUIDでのWaypipe helper起動はPhase 0〜1から先行して試せます。

**開発時の接続方針（2026-11-03更新）:** 最初はUSBケーブルで接続し、デバッグ承認・APK導入・起動・診断を確立します。その後、USB-Cを充電器からの給電に使い、**ネットワーク経由でAPKを更新し、起動・ログ・画像取得まで行えるか**を[Phase 0](docs/phase-0-development-environment.md)で検証します。[Meta公式のADB手順](https://developers.meta.com/vr/documentation/native/android/ts-adb/)はUSB設定後のWi-Fi接続を案内していますが、Eidolonの実機での更新・連続運転・スリープ／再起動後の復旧は未検証です。USBでの開発開始と無線運用の採用を別々に判定し、詳細は[無線ADBの検証計画](docs/development-automation-and-debugging.md#無線adb)に記録します。

2026-11-01の[追加レビュー](docs/implementation-plan.md#追加レビュー2026-11-01)では、Meta公式サンプルのEGL描画例、Build Toolsの要件、compositor layer数、XR session focus、Android helperの実行制約を反映しました。**Eidolonのビルド・実機動作は引き続き未検証**です。

**2026-11-04のローカル環境レビュー:** 既存SDK／NDK、JDK 25、有線LANの複数経路を確認し、[実測結果](docs/phase-0-development-environment.md#30-既存環境の確認結果2026-11-04)と不足分を整理しました。新しい版を優先し、**JDK 25・compileSdk 36・Spatial SDK 0.15.0**を検証候補にします。Questの実行要件となるminSdk／targetSdkはまず34とし、[版の選定と採用ゲート](docs/phase-0-development-environment.md#2-ビルドツールの基準)で区別します。同日、Quest 3のUSBデバッグ承認とAndroid 14／API 34／arm64-v8aを実測しました。APK動作と無線到達性は未確認です。SSH手順、SHMの安全な読取り、描画待ちの影響、表示PoCと操作MVPの判定も修正しました。

## Docs

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

開発を開始する際は、まず[実装計画のP0スパイク](docs/implementation-plan.md#最初に潰すべき不確実性スパイク)から進めてください。

デバッグ方法・実機テスト・自動化を検討する際は、[開発自動化・デバッグの専用文書](docs/development-automation-and-debugging.md)を参照してください。2026-11-03の調査として、build→install→launch→状態・ログ・画像の取得、Android入力とXR入力の違い、無線ADB、Simulatorの適用範囲を整理しました。公式機能の存在とEidolonへの提案・実機未検証事項を分けており、自動化コードや実機成功記録を追加したものではありません。
