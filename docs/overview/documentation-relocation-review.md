---
id: documentation-relocation-review-20261120
title: Documentation relocation and implementation review
type: evidence
feature: documentation-system
created: "2026-11-20"
last_reviewed: "2026-11-20"
baseline_commit: "2e144a90dcf860f2f6b275ea9297860c95e671c7"
---
# 文書移動と設計・実装の照合

対象は main の 2e144a9 で docs/ 直下にあった Markdown 166 件。作業ツリーにあったランチャー簡素化と Quest の記録も移動先に反映した。登録済みの文書 ID を引き継ぎ、従来の文書一覧の URL から新しい一覧へ案内する。

各文書の内容と見出しで移動先を決め、未実装・必須条件・矛盾の候補を抽出した。下記の実装判断には、候補に対応するソース、検証器、タスク台帳、日付付きの実機記録を使った。

## 分割した文書

- v3 統合計画から、APK ごとの回収・入力・操作の経過と残課題を分けた。回収の契約と受入条件は統合計画を参照する。[回収と操作の記録](../features/linux-desktop-v3/quest-v3-recovery-and-usability-20261120.md)
- I2 の設計本文から、当時の担当者への実装プロンプトを分けた。[I2 初期プロンプト](../features/linux-desktop-v3/i2-initial-implementation-prompt.md)
- 進捗一覧と受入れ範囲から、B01/B02 以降の詳細な検証履歴を分けた。当時の操作、ビルド、日付、判定を継承した。[進捗と受入れ範囲](../evidence/progress.md)・[詳細履歴](../evidence/initial-phases-implementation-history.md)

## 実装を早めるために反映した変更

| 条件 | 変更後の適用範囲 |
| --- | --- |
| 固定二項目のカタログと Mousepad 用の配備検証 | 固定方式の Inspect は Mousepad と設定 probe の成立を必要とする。新ランチャーは読み取り専用の一覧と選択済み項目の起動に分ける。Mousepad の有無や回収待ちによらず一覧を取得できる。[固定契約と拡張案](../features/linux-desktop-v3/linux-real-app-b1-contract-20261110.md)・[Inspect / Start](../features/linux-desktop-v3/linux-real-app-c2-helper-20261110.md) |
| Mousepad の専用 GSettings と依存全体のハッシュ | 単一 main・復元処理の制御に由来する。一般アプリに適用するとアプリごとの設定ロックと再配備が必要になる。専用セッションと保存領域を新方式の共通部分にできる。[設定契約](../features/linux-desktop-v3/linux-real-app-l7a-design-20261109.md)・[バンドル](../features/linux-desktop-v3/linux-real-app-b2-bundle-20261110.md) |
| 旧プロセス内の観測に依存する回収 | ANR 後に、旧 SDK リソースの解放証拠を取得しにくい。元のセッションの終了と解放を開き直した後に照合する経路を用意する余地がある。新プロセスのゼロ値と PID だけで回収を認めると別セッションを壊し得る。[回収順序](../features/linux-desktop-v3/linux-real-app-v3-integration-plan-20261111.md)・[回収コード](../../native/transport/src/linux_desktop/v3/recovery.rs) |
| profile、config、保存 record と有効化 marker の複数ファイル整合 | 保存・クラッシュ後の再開に中間状態が増える。次の保存形式では有効化 record を一度だけ原子的に発行する。旧形式の読み込みと途中失敗はローカルテストで確認する。[Android 保存契約](../features/linux-desktop-v3/linux-real-app-v3-i5-detailed-design.md)・[保存処理](../../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxStorageRepository.kt) |
| 全窓の配置確定と Undo を含む 3 窓 Arc | 既存窓を一括で並べ直す操作に使う条件。新しく開いたダイアログの視認性は、1枚の初期姿勢だけで改善できる。[Curved Workspace](../curved-workspace/README.md)・[ダイアログの残課題](../features/linux-desktop-v3/quest-v3-recovery-and-usability-20261120.md) |
| I6 の固定件数と N07 の独立 Golden の承認 | 全体の正式な受入れを判定するための条件。修正ごとの調査、単独の回帰試験、実機の探索は必要なケースから実行できる。[I6 詳細設計](../features/linux-desktop-v3/linux-real-app-v3-i6-detailed-design.md)・[N07 比較コード](../../scripts/linux_real_app_v3_n07_pixel.py) |

## 実装と照合した残件

| 項目 | 根拠と残作業 |
| --- | --- |
| v3 の切断と異常終了後の自動回収 | remote 回収とローカルの最終化は実装済み。旧 I/O の閉鎖証拠が必要で、ANR 後にアプリを開き直す実機ケースが残る。通常の停止で SDK 登録が残った事例も追跡する。[回収コード](../../native/transport/src/linux_desktop/v3/recovery.rs)・[実機記録](../features/linux-desktop-v3/quest-v3-recovery-and-usability-20261120.md) |
| ANR と性能 | SDK アダプターのポーリングは nativeSnapshot と JSON 解析を呼ぶ。ANR で採取したスタックを手掛かりに、取得と解析の時間を個別に測る。quest-observe と描画カウンターは既存。時刻校正を含む Host と Quest の計測値の相関や正式 SLO は後続。[SDK アダプター](../../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt)・[性能計画](../development/performance/performance-observability-plan-20261120.md) |
| Ubuntu の GTK・Qt/KDE 自動一覧と選択起動 | Rust と Kotlin の v3 カタログには GTK デモと Mousepad が固定されている。自動列挙、選択起動、アプリ追加・更新の追従が残る。[Rust カタログ](../../native/model/src/linux_desktop_v3/mod.rs)・[Android カタログ](../../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxDesktopV3Catalog.kt) |
| GUI の日本語入力 | 通常の Ctrl+Space は foot の起動 receipt を照合する。Mousepad や Qt 向けの候補表示、確定、取消、窓切替時の検証が残る。[対象判定](../../android/app/src/main/java/io/github/tinmachine/eidolon/ImeTerminalEligibility.kt)・[IME 計画](../features/android-ime/android-ime-wayland-plan-20261107.md) |
| ダイアログの円弧配置と Curved Workspace | 現行の姿勢管理と 9 窓を上限とする採用経路を確認。ダイアログ1枚の初期配置と、3 窓の一括変更・Undo はそれぞれ実装と受入れが残る。[姿勢管理](../../native/core/src/workspace.rs)・[Curved Workspace](../curved-workspace/README.md) |
| Spatial Anchor | SA0 は作成、Activity 再生成後の再発見、Quest 再起動後の再発見、消去を確認する観測計画。実機証拠と製品接続が残る。[SA0 の索引](../features/spatial-anchor/README.md) |
| I6 と Quest/HMD の受入れ | N01〜N06 の型付き receipt と B4 の収集コードは存在する。N07 の独立 Golden と B5 は残件。N08〜N11、失敗・互換・集約検査は個別の残件表を参照する。Quest の物理操作・復帰は実機で確認する。[I6 残件表](../features/linux-desktop-v3/i6-remaining-work-20261119.md)・[B4 と B5](../features/linux-desktop-v3/i6-n07-b4-two-sessions.md) |
| 初期フェーズの 17 タスク | 台帳の in_progress は B02、R01–R03、D02、I01–I07、L01–L04、C02。各タスクの実装済み部分と、手動・長時間・リリース受入れの不足は個別記録を参照する。[タスク台帳](../design/tasks.json)・[進捗](../evidence/progress.md) |

ローカルの APK では、右トリガー選択・Mousepad のメニュー操作は 0.1.6、修正後のカーソルは 0.1.7 で利用者の確認がある。クリック位置、ドラッグ、ホイール、診断パネル直上配置、別名保存内容の照合は実機記録の残件を参照する。[実機記録](../features/linux-desktop-v3/quest-v3-recovery-and-usability-20261120.md)

## 修正した文書間の食い違い

| 記述 | 修正と読み方 |
| --- | --- |
| 「PR #52 は未マージ」と B4 が入った main | 11月19日の残件表は Draft 時点の記録。移動時の main には B4 のコミット 10216ef がある。正式 Golden のロックは未設定。[当時の残件表](../features/linux-desktop-v3/i6-remaining-work-20261119.md)・[B4](../features/linux-desktop-v3/i6-n07-b4-two-sessions.md) |
| N07 の「1 / 13」と「0/13 verified」 | 前者は variant 数と必須 assertion 数。正式な確認数は後者の 0 件。[I6 残件表](../features/linux-desktop-v3/i6-remaining-work-20261119.md) |
| 任意の実行ファイル・引数を指定する初期ランチャー計画と、v3 の Desktop Entry 選択 | 初期計画は Termux を対象に任意パスを扱う。Ubuntu 向けの新ランチャーでは、選んだ Desktop Entry を接続先で解決する。初期文書の将来案を現在の Ubuntu 起動契約に適用すると不一致になる。[初期ランチャー](../features/launcher/application-launcher-plan-20261107.md)・[v3 ランチャー](../features/linux-desktop-v3/linux-real-app-b1-contract-20261110.md) |
| 「大きな PR 一本」「マージは禁止」と現在の作業手順 | 旧 I2 プロンプト固有の指示。現在の統合計画は検証できる単位で進める。初期プロンプトを設計本文から分け、当時の文脈を明記した。[初期プロンプト](../features/linux-desktop-v3/i2-initial-implementation-prompt.md)・[統合計画](../features/linux-desktop-v3/linux-real-app-v3-integration-plan-20261111.md) |
| 固定3窓・中央窓基準の Arc と、開いた時点の視点に単独ダイアログを向ける配置 | 既存ワークスペースの再配置と、新規ウィンドウの初期姿勢に適用される条件。後者に 3 窓条件を適用すると親窓とダイアログの2枚で利用できなくなる。[Curved Workspace](../curved-workspace/README.md)・[実機課題](../features/linux-desktop-v3/quest-v3-recovery-and-usability-20261120.md) |

## 残件の実装順序

無応答と回収から着手し、読み取り専用カタログを並行できるようにする。選択起動、ダイアログの初期配置、IME、受入れ準備へ進む。Quest は最後に一度まとめて確認する。Wi-Fi ADB で APK を更新し、XR 合成の画像を確認した上で物理操作を行う。[統合計画の実装順序](../features/linux-desktop-v3/linux-real-app-v3-integration-plan-20261111.md#残件の実装順序と最後の実機確認)

## 移動先

### docs/architecture

- [Eidolon Wayland — アーキテクチャ設計](../architecture/architecture.md)

### docs/curved-workspace

- [Eidolon Wayland — Curved Workspace / Immersive Terminal 設計・検証・段階導入計画](../curved-workspace/curved-workspace-immersive-terminal-design-20261111.md)

### docs/development

- [Eidolon Wayland — 開発自動化・デバッグの調査と実装方針](../development/development-automation-and-debugging.md)

### docs/development/build

- [複数checkoutとビルド容量の検証記録](../development/build/build-cache-worktree-troubleshooting-20261108.md)
- [Native依存の固定build（W01）](../development/build/native-dependencies-build.md)
- [Native事前検証とQuest操作の記録](../development/build/native-preflight-20261104.md)
- [Phase 0 — Ubuntu開発PC・Quest 3・Termuxの開発環境](../development/build/phase-0-development-environment.md)
- [Phase 0 — 実機で確認したビルド・Wi-Fi・SSHの再現手順](../development/build/phase-0-reproduction.md)
- [Phase 0 — 2026-11-04の実装・試行錯誤・実機記録](../development/build/phase-0-troubleshooting-20261104.md)

### docs/development/ci

- [CI APT/DNS resilience — 2026-11-12](../development/ci/ci-apt-dns-reliability-20261112.md)
- [Eidolon Wayland — change-scoped CI (2026-11-13)](../development/ci/ci-change-scope-20261113.md)
- [CIの独立fixture lock更新](../development/ci/ci-fixture-lock-refresh-20261108.md)
- [PR #16 Host CI 再開調査 — 2026-11-14](../development/ci/ci-pr16-host-review-20261114.md)
- [CodeQL Advanced setup migration — five languages (2026-11-13)](../development/ci/codeql-advanced-migration-20261113.md)
- [CodeQL Kotlin — lightweight manual-build probe (2026-11-13)](../development/ci/codeql-kotlin-manual-probe-20261113.md)

### docs/development/performance

- [Eidolon自身のPSS](../development/performance/device-process-pss-20261108.md)
- [NX14b: OSの熱状態とEidolon保存先の空き容量](../development/performance/device-thermal-storage-20261108.md)
- [Performance Observability — Quest 3 / Android / Linux Host](../development/performance/performance-observability-plan-20261120.md)
- [Quest 3 Performance Observability — 実機採取Runbook（初期案）](../development/performance/performance-observability-quest-runbook-20261120.md)
- [Quest 3の端末情報・負荷メトリクスの調査と次期計画](../development/performance/quest-device-metrics-plan-20261107.md)
- [NX13: storage帰属量の集計と内側リング](../development/performance/workload-io-aggregation-20261108.md)
- [NX13: 登録terminal内の固定I/O fixture](../development/performance/workload-io-fixture-20261108.md)

### docs/development/release

- [APK License Compliance — implementation and publication gates (2026-11-12)](../development/release/apk-license-compliance-20261112.md)
- [Eidolon Wayland — OSSライセンス・公開前レビュー（2026-11-12）](../development/release/oss-license-publication-review-20261112.md)
- [Eidolon Wayland — GitHub security hardening and `protect-main` ruleset](../development/release/oss-publication-github-security-settings-20261113.md)

### docs/development/source-structure

- [Host/JNI・SDK 境界レビューと索引の探索比較](../development/source-structure/source-structure-boundary-review-20261115.md)
- [Codex から実行する自動発見型 Deep 構造解析](../development/source-structure/source-structure-deep-analysis.md)
- [Python Deep 初回評価 — 2026-11-15](../development/source-structure/source-structure-deep-evaluation-20261115.md)
- [Python Deep：repository import候補の拡張評価（2026-11-15）](../development/source-structure/source-structure-deep-import-evaluation-20261115.md)
- [IME 終了証拠と設定保存の接続案](../development/source-structure/source-structure-ime-retirement-contract-20261115.md)
- [Eidolon Wayland — I5完了後のソース構造解析・リファクタリング評価計画](../development/source-structure/source-structure-post-i5-evaluation-plan-20261114.md)
- [回収・設定保存のレビューと Eidolon 専用構造解析の試行](../development/source-structure/source-structure-retirement-review-20261115.md)
- [構造解析の監視範囲、IME の所有境界、追加3問の評価](../development/source-structure/source-structure-watch-ownership-20261115.md)

### docs/evidence

- [製品実装の進捗](../evidence/progress.md)

### docs/evidence/experiments

- [固定Mesaの3 NULL分岐を非描画の子プロセスで観測する](../evidence/experiments/egl-null-stages-probe-20261108.md)
- [公開EGL APIによるCPU screen初期化の分離](../evidence/experiments/egl-surfaceless-probe-20261108.md)
- [GPU計測FDの切り分け](../evidence/experiments/gpu-fd-investigation.md)
- [固定子プロセスのptrace能力を分けて確認する](../evidence/experiments/ptrace-capability-probe-20261108.md)
- [SDLの最初のGL/EGL失敗を分ける固定stage probe](../evidence/experiments/sdl-egl-stage-probe-20261108.md)
- [SDL小probeのsource配備](../evidence/experiments/sdl-probe-install-20261108.md)
- [現行SDL検証用leaseの読取り専用capture](../evidence/experiments/sdl-probe-live-capture-20261108.md)
- [現行leaseでのSDL自然終了1回](../evidence/experiments/sdl-probe-live-run-20261108.md)
- [NX11: SDL software rendererからSHMへ届く経路の小規模検証](../evidence/experiments/sdl-shm-probe-20261107.md)

### docs/evidence/integration

- [Build 174: IME入口とGTK入力・サイズ変更の統合検証](../evidence/integration/build-174-integration-20261108.md)
- [Build175: 通常GUI配線と視点姿勢の実機検証](../evidence/integration/build-175-integration-20261108.md)
- [Build176: 通常IMEと正面配置の確認・作業中断点](../evidence/integration/build-176-integration-20261108.md)
- [NX01〜NX08 実装・検証記録](../evidence/integration/extensions-nx01-nx08-20261107.md)
- [NX01〜NX18: 計画全体の継続記録](../evidence/integration/extensions-nx09-nx18-20261107.md)

### docs/features/android-ime

- [日本語入力の実装・検証記録](../features/android-ime/android-ime-implementation-20261107.md)
- [Android IME比較probeの操作](../features/android-ime/android-ime-probe-20261107.md)
- [端末IMEとCtrl+Spaceのdebug検証](../features/android-ime/android-ime-terminal-probe-20261108.md)
- [日本語入力計画: Android IMEを先に検証し、Wayland text-input-v3へ接続する](../features/android-ime/android-ime-wayland-plan-20261107.md)
- [次期日本語入力: まずfoot内でかな漢字変換する](../features/android-ime/japanese-input-plan-20261107.md)
- [NX09: 通常 IME の初期 owner 経路を確認する](../features/android-ime/nx09-normal-ime-probe-20261108.md)
- [NX09: 通常ターミナルの IME 入力入口](../features/android-ime/nx09-terminal-ime-entry-20261108.md)

### docs/features/freedoom

- [NX11: 固定Freedoom demo128の自然終了operator](../features/freedoom/freedoom-demo128-operator-20261108.md)
- [NX11: Chocolate DoomとFreedoomの最小検証準備](../features/freedoom/freedoom-minimum-probe-plan-20261108.md)
- [通常Freedoomの固定GUI検証](../features/freedoom/freedoom-normal-gui-probe-20261108.md)
- [Freedoomの通常ランチャーへの明示登録](../features/freedoom/freedoom-normal-launcher-20261108.md)
- [NX11: 固定Freedoomの通常GUI worker準備](../features/freedoom/freedoom-owned-gui-worker-20261108.md)
- [Freedoom専用assetsの固定stream導入](../features/freedoom/freedoom-owned-install-20261108.md)
- [NX11: 固定Freedoom GUIを通常helperへ接続する](../features/freedoom/freedoom-typed-gui-connection-20261108.md)

### docs/features/gtk

- [NX15: 固定GTKの初描画だけを観測する](../features/gtk/gtk-first-frame-probe-20261108.md)
- [GTK host検証のXvfb起動確認](../features/gtk/gtk-host-display-readiness-20261108.md)
- [GTK3入力・dialog・resizeの固定fixture](../features/gtk/gtk-interaction-fixture-20261108.md)
- [GTK固定fixtureをAndroid合成入力へ接続する](../features/gtk/gtk-interaction-operator-20261108.md)
- [GTK resize: PC観測後だけ元サイズへ戻す](../features/gtk/gtk-resize-observation-handshake-20261108.md)

### docs/features/initial-phases

- [Phase 1補足 — 現実の映像を背景にするPassthrough](../features/initial-phases/phase-1-passthrough-background.md)
- [Phase 1 — RustによるJNI・Android Surface・EGL/GLES描画](../features/initial-phases/phase-1-rust-rendering.md)
- [Phase 2 — Rust SSHとTermux・StreamLocal forwarding](../features/initial-phases/phase-2-rust-ssh.md)
- [Phase 3 — Rust compositorのAndroid移植とWaypipe再利用](../features/initial-phases/phase-3-rust-compositor-waypipe.md)
- [Phase 4 — RustでWaylandの各ウィンドウをXR Surfaceへ描画](../features/initial-phases/phase-4-rust-window-rendering.md)
- [Phase 5 — Rust中心の空間ウィンドウ管理と入力](../features/initial-phases/phase-5-rust-window-manager.md)
- [Phase 6 — Rust coreのライフサイクル・性能・検証](../features/initial-phases/phase-6-rust-lifecycle-testing.md)
- [Phase 5の最終実機確認](../features/initial-phases/phase5-manual-acceptance.md)

### docs/features/input

- [キーボード・seatの検証記録](../features/input/keyboard-checks.md)
- [Pointer・focus・captureの検証](../features/input/pointer-checks.md)
- [popup・session内clipboardの検査](../features/input/popup-clipboard-checks.md)
- [SDK rayと共通座標変換（I01）](../features/input/ray-coordinate-checks.md)

### docs/features/launcher

- [次期アプリケーションランチャーの計画](../features/launcher/application-launcher-plan-20261107.md)

### docs/features/linux-desktop-v3

- [Linux Desktop v3 I5 — 実装・検証と引き継ぎ](../features/linux-desktop-v3/linux-desktop-v3-i5-validation-20261114.md)
- [PR #16 — I3/I4 P1 verification and outstanding real-SDK evidence (2026-11-14)](../features/linux-desktop-v3/linux-desktop-v3-pr16-p1-verification-20261114.md)
- [PR #16: Stop後の回収とI4実SDK証拠の接続（2026-11-14）](../features/linux-desktop-v3/linux-desktop-v3-pr16-retirement-evidence-20261114.md)
- [L7a A/A0: 実用アプリ検査と設定backendの実装基盤](../features/linux-desktop-v3/linux-real-app-a0-foundation-20261109.md)
- [L7a A0 PR2: GUI設定の保持と保護設定の操作不可](../features/linux-desktop-v3/linux-real-app-a0-gui-settings-20261109.md)
- [L7a A0: PR #4後の実装計画と次セッションへの引き継ぎ](../features/linux-desktop-v3/linux-real-app-a0-next-steps-20261109.md)
- [L7a A0 PR1: 同じ合成profileでの再起動と先行instanceからの独立](../features/linux-desktop-v3/linux-real-app-a0-restart-20261109.md)
- [L7a B1: Android identityとv3 storage recordの確定](../features/linux-desktop-v3/linux-real-app-b1-android-identity-20261110.md)
- [L7a B1: v3共有契約と固定golden vector](../features/linux-desktop-v3/linux-real-app-b1-contract-20261110.md)
- [L7a B1: v3共通契約の確定と並行開発への引き継ぎ](../features/linux-desktop-v3/linux-real-app-b1-handoff-20261110.md)
- [L7a B1: origin・parent・表示成立の共有契約](../features/linux-desktop-v3/linux-real-app-b1-presentation-20261110.md)
- [B2: v3 bundle、source closureとimmutable配置](../features/linux-desktop-v3/linux-real-app-b2-bundle-20261110.md)
- [L7a B2: v3 bundle・protocol・版別dispatchの統合境界](../features/linux-desktop-v3/linux-real-app-b2-integration-20261110.md)
- [B2: v3 SSH・journal・Recoverの接続境界](../features/linux-desktop-v3/linux-real-app-b2-transport-20261110.md)
- [L7a B2: v3 wireと版別dispatchの受渡し](../features/linux-desktop-v3/linux-real-app-b2-wire-20261110.md)
- [L7a C1: remote永続profile・所有handle・排他とC2への引継ぎ](../features/linux-desktop-v3/linux-real-app-c1-handoff-20261110.md)
- [L7a C2: 通常v3 helper・remote回収barrier](../features/linux-desktop-v3/linux-real-app-c2-helper-20261110.md)
- [L7a E1: Android v3 storage identity・保存モデル](../features/linux-desktop-v3/linux-real-app-e1-storage-20261110.md)
- [L7a: 別Linuxの実用アプリ一種の詳細設計](../features/linux-desktop-v3/linux-real-app-l7a-design-20261109.md)
- [Eidolon Wayland — I2: v3 SSH Controller詳細設計](../features/linux-desktop-v3/linux-real-app-v3-i2-detailed-design.md)
- [I3 詳細設計 — verified listener origin / WindowHistory / window admission](../features/linux-desktop-v3/linux-real-app-v3-i3-detailed-design.md)
- [Linux Desktop v3 I4 詳細設計 — Startup Observation / Spatial SDK Evidence](../features/linux-desktop-v3/linux-real-app-v3-i4-detailed-design.md)
- [I5: Android storage 実回収・設定保存・通常ランチャー詳細設計](../features/linux-desktop-v3/linux-real-app-v3-i5-detailed-design.md)
- [I6: 通常 v3 Host acceptance／I3-T20・V11 詳細設計](../features/linux-desktop-v3/linux-real-app-v3-i6-detailed-design.md)
- [L7a: 通常v3統合からQuest自動検証までの実装計画](../features/linux-desktop-v3/linux-real-app-v3-integration-plan-20261111.md)
- [PR-01: Linux Desktop v3 integration contract handoff](../features/linux-desktop-v3/linux-real-app-v3-pr01-contract-handoff-20261111.md)

### docs/features/remote-linux

- [NX18 C2a: Linux端末の子孫processをlive ownerで回収する](../features/remote-linux/linux-terminal-process-owner-c2a-20261108.md)
- [NX18 C2: 通常 Linux GUI の固定アプリからの接続準備](../features/remote-linux/normal-linux-gui-c2-20261108.md)
- [NX18 L1〜L4: 通常Linuxのlease・SSH actor・journal・復旧](../features/remote-linux/normal-linux-gui-l1-l4-20261108.md)
- [通常Linux GUI L5〜L6: Host / native / launcher接続](../features/remote-linux/normal-linux-gui-l5-l6-20261108.md)
- [NX18 C1: 通常Linux用の接続設定を独立保存する](../features/remote-linux/normal-linux-profile-c1-20261108.md)
- [NX11・NX18: 通常GUI起動の現状と残る実装計画](../features/remote-linux/nx11-nx18-normal-gui-followup-plan-20261108.md)
- [NX18 B2: APK UIDから保存済みLinux profileへの固定SSH probe](../features/remote-linux/remote-apk-probe-b2-20261108.md)
- [NX18 B4: APKからLinux固定SHM検査への接続](../features/remote-linux/remote-linux-apk-shm-b4-20261108.md)
- [NX18 B6: Linux固定GTKの描画・合成入力](../features/remote-linux/remote-linux-gtk-b6-plan-20261108.md)
- [NX18 B6c: 固定Linux GTKのSDK表示と合成入力](../features/remote-linux/remote-linux-gtk-sdk-b6c-20261108.md)
- [NX18 B6b: 固定Linux GTKとtransport/runtimeの接続](../features/remote-linux/remote-linux-gtk-transport-b6b-20261108.md)
- [Ubuntu等の別マシンからGUIを表示する拡張案](../features/remote-linux/remote-linux-gui-plan-20261107.md)
- [NX18 B3a: Linux専用helperと固定SHM転送](../features/remote-linux/remote-linux-helper-b3a-20261108.md)
- [NX18 Linux journalのAndroidでの排他的公開](../features/remote-linux/remote-linux-journal-android-20261108.md)
- [Linux固定SHM検査の再現用operator](../features/remote-linux/remote-linux-operator-20261108.md)
- [NX18 B5: 固定Linux SHMのSDK表示検査](../features/remote-linux/remote-linux-sdk-shm-b5-20261108.md)
- [NX18 B3b: Linux固定SHM用transport owner](../features/remote-linux/remote-linux-transport-b3b-20261108.md)
- [NX18a 接続先profile v2の契約](../features/remote-linux/remote-profile-v2-contract-20261108.md)
- [NX18 B1: 接続ごとの設定捕捉と固定SSH確認](../features/remote-linux/remote-transport-b1-20261108.md)

### docs/features/spatial-anchor

- [Eidolon Wayland — Spatial Anchor × Linux 設計・SA0検証計画](../features/spatial-anchor/spatial-anchor-linux-design-20261111.md)

### docs/features/termux

- [次フェーズ案: 入力状態・日本語入力・Termux・Linux GUI・ウィンドウ操作](../features/termux/next-phase-termux-startup-linux-gui-plan-20261107.md)
- [NX11: Termux GUI専用workerの子孫所有](../features/termux/termux-gui-process-owner-20261108.md)
- [NX13: Termuxの自プロセス・専用子だけを使うI/O probe](../features/termux/termux-io-probe-20261108.md)
- [TermuxのWaypipe・foot・固定producerの準備（W05）](../features/termux/termux-wayland-tools.md)

### docs/features/wayland

- [実footからパネルへの接続と更新通知（D02）](../features/wayland/foot-render-checks.md)
- [型付きGUIとfoot専用機能の統合境界](../features/wayland/gui-foot-ownership-boundary-20261108.md)
- [所有済み画像からAndroid EGLへの描画検証（D01）](../features/wayland/owned-image-render-checks.md)
- [WaylandとSHMの実装・検証（W02）](../features/wayland/wayland-shm-checks.md)
- [Window stateとowned snapshotの検証（W03）](../features/wayland/wayland-window-state-checks.md)
- [APK向けWaypipeのbuildと起動検査（W04）](../features/wayland/waypipe-build-checks.md)
- [SSH・Waypipeを通すSHMとfootの検証（W05）](../features/wayland/waypipe-forwarded-shm-checks.md)

### docs/features/workspace

- [NX12: 窓を正面へ戻す操作](../features/workspace/nx12-recenter-menu-20261108.md)
- [NX12: 重なる正面候補を避ける純粋な計算の準備](../features/workspace/nx12-recenter-nearby-choice-20261108.md)
- [NX12: 正面配置の SDK 実経路検証](../features/workspace/nx12-recenter-sdk-case-20261108.md)
- [NX12: 正面復帰前の視点姿勢 probe](../features/workspace/nx12-viewer-pose-probe-20261108.md)
- [NX12a: 各窓のfocus・closeボタンとテーマ配置](../features/workspace/nx12a-window-controls-20261108.md)
- [NX12b: 丸い移動ハンドルと窓横の入力MODE](../features/workspace/nx12b-window-move-mode-20261108.md)
- [NX12d: 窓ごとの表示サイズと操作メニュー](../features/workspace/nx12d-window-size-menu-20261108.md)
- [NX16: foot九窓とSHM pool件数の境界](../features/workspace/nx16-foot-pool-budget-20261108.md)
- [NX16: 九つの独立Wayland接続のhost検査](../features/workspace/nx16-independent-clients-wire-20261108.md)
- [NX16a: 九枠の予約・解放モデル](../features/workspace/nx16a-slot-reservations-20261108.md)
- [NX16c: 九枠と表示・SDKの接続](../features/workspace/nx16c-nine-window-integration-20261108.md)
- [NX16d: 九枠の固定SDKケース](../features/workspace/nx16d-window-slots-sdk-case-20261108.md)
- [NX17a: 同種ウィンドーの配置モデルと次の接続](../features/workspace/nx17a-window-group-model-20261108.md)
- [NX17b1: 背面のpresentとリング時計を止める接続](../features/workspace/nx17b-hidden-render-targets-20261108.md)
- [NX17b: nativeのgroup配置・描画・入力の接続](../features/workspace/nx17b-native-group-presentation-20261108.md)
- [NX17b: remoteの回収責務とlive判定](../features/workspace/nx17b-remote-live-scope-20261108.md)
- [NX17b: 検証済みfootを束ねる単一workerの所有契約](../features/workspace/nx17b-window-group-owner-20261108.md)
- [NX17c: Hostの窓別group入力gate](../features/workspace/nx17c-group-input-gate-20261108.md)
- [NX17c: SDKのgroup表示poseとgeometry確認](../features/workspace/nx17c-sdk-group-overlay-20261108.md)
- [NX17c: 2枚のfootを使うgroup SDK固定検査](../features/workspace/nx17c-window-groups-sdk-case-20261108.md)
- [NX17d: 通常メニューのgroup操作契約](../features/workspace/nx17d-group-menu-contract-20261108.md)
- [論理resizeと物理サイズの検証](../features/workspace/resize-checks.md)
- [二枚の表示と論理ウィンドウの検証](../features/workspace/two-window-checks.md)
- [通常グループメニューと複数窓の正面復帰](../features/workspace/window-group-recenter-integration-20261108.md)
- [配置・背景の保存検証](../features/workspace/workspace-checks.md)

### docs/guides

- [Questからの起動・接続設定](../guides/usage.md)

### docs/knowledge

- [Eidolon Wayland — LLM-first Documentation Knowledge Architecture](../knowledge/documentation-knowledge-base-design-20261113.md)

### docs/overview

- [Documentation — Eidolon Wayland](README.md)
- [Eidolon Wayland — 実装計画・検証項目](implementation-plan.md)
