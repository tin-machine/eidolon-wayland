# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

**Meta Quest 3の広い作業空間で、複数のLinuxアプリを独立したウィンドウとして使う**ことを目指す、実験的な空間Waylandデスクトップです。
QuestのAndroidアプリにRust製のWayland compositorを組み込み、SSHとWaypipeで受け取ったアプリの画面をMeta Spatial SDKのXRパネルへ描画します。

## 現在地（2026-11-10）

**Termux上のfootを使う経路は、描画・入力・最大9窓・移動・サイズ変更・グループ操作まで実装しています。別Linuxホストには固定GTKデモの通常接続経路があり、実用アプリ第一候補のMousepadはhost上での検証を進めています。**

この概要の実装基準は、[PR #5](https://github.com/tin-machine/eidolon-wayland/pull/5)をマージしたmain [`2efca7b`](https://github.com/tin-machine/eidolon-wayland/commit/2efca7b5347ebd14a38adeceb3a490d8d71e2262)です。未マージの作業は実装済みに含めません。
下記の実機結果は各記録のBuild・commitに対応します。最新mainの全機能を同じAPKでHMD・物理入力まで再検証した、という意味ではありません。

### 実装と確認できている範囲

| 機能 | mainの実装・検証状況 | 残る確認・制約 |
| --- | --- | --- |
| **Termux / foot** | 同じQuestのTermuxへlocalhost SSHで接続し、Waypipe経由で描画。公開鍵ペアリング、host key pin、必要時のTermuxサービス起動を実装。文字の可読性とUSキーボードの主要操作はHMDで確認済み。[利用手順](docs/usage.md)・[物理確認](docs/phase5-manual-acceptance.md) | 初回設定とAPKに対応するTermux helperの導入が必要。全キー・全アプリの物理受入れは未完了 |
| **最大9窓** | 3→6→9窓、上限到達時の拒否、退役後の枠再利用をQuestの固定SDKケースで確認。[9窓の統合検証](docs/nx12d-window-size-menu-20261108.md) | 実機検証は**単一footクライアントを共有する9窓**。9種類の独立した実用アプリ、最大寸法での持続性能・快適性は未受入れ |
| **配置・サイズ** | 窓ごとの移動ハンドル、サイズメニュー、配置保存、編集終了後の入力復帰を実装。移動・サイズ操作の固定SDK検証が成功。[移動](docs/nx12b-window-move-mode-20261108.md)・[サイズ](docs/nx12d-window-size-menu-20261108.md) | 物理コントローラーでの操作とHMD上の見え方を確認する項目が残る。同じapp_idの複数窓の配置は接続内の保持と永続保存を区別する |
| **グループ・正面復帰** | 通常メニューに「まとめる」「展開」「前面切替」を統合。2窓のグループ操作と単独窓の正面復帰はQuestの固定ケースで成功。[統合・実機記録](docs/window-group-recenter-integration-20261108.md) | 通常グループメニューの物理操作、複数窓の正面復帰の実機geometryは未受入れ。グループがある間の正面復帰は拒否する |
| **キー・pointer・popup・clipboard** | US/JIS配列、focus、押下解除、ray入力、popup、session内clipboardを実装し、host・wire・SDKの各検査を用意。[キー](docs/keyboard-checks.md)・[pointer](docs/pointer-checks.md)・[popup / clipboard](docs/popup-clipboard-checks.md) | US配列の主要操作・tmux・2窓の入力先切替はユーザー確認済み。drag/scroll・実copy/pasteなどの物理受入れは残る |
| **Android IME** | 通常footの「IME入力」入口とCtrl+Space切替、Wayland `text-input-v3`との接続を実装。通常入口のON→BOUND→OFF→直接入力復帰を実機自動検証。[実装](docs/nx09-terminal-ime-entry-20261108.md)・[Build176](docs/build-176-integration-20261108.md) | 既定OFF、通常footが対象。**物理Ctrl+Space・実かな漢字変換・候補のHMD表示は未確認**。別Linuxの一般GUI向けIMEは未対応 |
| **MR背景・テーマ** | 通常passthroughと単色背景、RustのSVG/CSSテーマを実装。Cyan Orbitは観測したCPU・RSS・I/Oに応じてリングを独立回転。部屋の表示とCPU/RSSリングの見た目はユーザー確認済み。[テーマ](docs/extensions-nx01-nx08-20261107.md)・[I/O実測](docs/workload-io-fixture-20261108.md) | I/Oリングの固定実機検証は成功。共有tmux・多窓での集計やHMD受入れは残る。通常passthroughはOS側の合成を使い、アプリによるカメラ画素取得は不要 |
| **停止・復旧・観測** | 所有資源の回収、journal、lease、明示再接続、端末・processメトリクスを実装。footの停止20回とGPU計測FD対策を実機検証。[FD対策](docs/gpu-fd-investigation.md)・[観測手順](docs/usage.md) | 長時間動作、100回再接続、Menu/sleep/reboot、署名済みreleaseの実機受入れは残る |
| **Freedoom** | 固定短demoのSHM転送・SDK更新・自然終了・回収を確認。通常GUI workerとランチャー登録も実装。[固定demo](docs/freedoom-demo128-operator-20261108.md)・[通常経路](docs/freedoom-normal-launcher-20261108.md) | 通常ランチャーからのゲーム起動・操作・終了は未受入れ。登録成功をプレイ可能の確認とは扱わない |

## 別Linuxホストのアプリ対応

### 通常GUI接続：固定GTKデモまで実装

**L1〜L6はmainへ統合済み**です。Linux用の接続設定・専用client鍵・host pin・bundle pinsから、明示的なConnect → Inspect → Launch、入力、親子Dialog、Close、Stop、回収・Recoverまで接続しました。
host/Android CIは成功していますが、**この通常経路での対象Linux＋Questの実配備・実機受入れは未完了**です。
以前の診断経路で得た別LinuxのGTK描画・入力・Dialog成功は、通常経路の受入れとは別に記録しています。
根拠と手順は[L1〜L4](docs/normal-linux-gui-l1-l4-20261108.md)、[L5〜L6](docs/normal-linux-gui-l5-l6-20261108.md)、[診断経路の実機記録](docs/build-174-integration-20261108.md)を参照してください。

現在の対象は**IPv4で指定するLinux一台と、固定GTKデモ一種**です。任意コマンド、remote terminal、DNS/IPv6、複数host、detach/resumeは未対応です。
切断やStopは接続先の作業も終了する契約で、切断後の同一GUIセッション保持は実装していません。

### 実用アプリ：A0成立、v3共有契約の実装

L7aでは、最初の実用アプリ候補を**Mousepad 0.6.3 / GTK3**に固定しています。A0は、通常ランチャーへ組み込む前に実アプリの動作と隔離条件を確認する段階です。
[A/A0基盤](docs/linux-real-app-a0-foundation-20261109.md)と[再起動・先行instance独立](docs/linux-real-app-a0-restart-20261109.md)では、実Waypipeと未改変Mousepadを使うhost CIで次を確認しています。

- 読み取り専用のpackage検査、専用settings backend、起動前の復元データ保護。
- Open・編集・Save・Save As、Main/Dialog、通常Closeと全子孫の回収。
- 同じ合成profileを使うClose / Stop / 異常終了後の再検査・再起動、保存文書の保持。復元データがある場合は起動前に拒否し、データを保持する。
- 別の先行Mousepad instanceからの独立。CIでは125秒以上の保持と、その後の新しい応答も確認する。

**A0はPR #6で`APP_A0_PASSED`となりました。** 実GUIによる行番号設定の保持、保護Preferencesの操作不可、同じmainでのtab増加も確認しています。
[GUI設定の実装記録](docs/linux-real-app-a0-gui-settings-20261109.md)を参照してください。
`available` / `production_acceptance_passed` / `quest_verified`はfalseで、MousepadはQuestの通常ランチャーからまだ利用できません。

次工程B1では、[Python/Rustのv3共有契約と固定golden vector](docs/linux-real-app-b1-contract-20261110.md)を隔離して追加しました。
通常起動・製品bundle・永続profile・Android保存・native/SDKには接続していません。
[B1引き継ぎ](docs/linux-real-app-b1-handoff-20261110.md)と[L7a詳細設計](docs/linux-real-app-l7a-design-20261109.md)に沿って、
B2のbundle/protocol、C1→C2の永続profile/helper、E1のAndroid保存モデルへ進みます。

## 動作構成

| 接続先 | アプリの実行場所と経路 | 現在の対象 |
| --- | --- | --- |
| このQuestのTermux | 同じQuest内のTermux → localhost SSH（`127.0.0.1:8022`）・Waypipe → Eidolon | foot、固定GUIの検証 |
| 別Linuxホスト | Linux → 認証済みSSH・Waypipe → Quest上のEidolon | 通常経路は固定GTKデモ。Mousepadはhost検証段階 |

接続先を切り替える前に、現在の接続と所有資源の回収完了を確認します。Termux経路の通常利用はQuest内で完結し、Linux PC・WiVRn・root化を必要としません。

KotlinはMeta Spatial SDK、AndroidのUI・入力・IME・権限を担当し、RustはJNI、Wayland compositor、描画、SSH、ウィンドウ管理、テーマと寿命管理を担当します。Waypipeは既存実装を利用しています。
構成の詳細は[アーキテクチャ](docs/architecture.md)、依存版・patch・licenseは[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)と[native依存lock](native/deps.lock.json)を参照してください。

## 使い始めるには

1. QuestへAPKを導入し、Termuxに**APKと同じbuildのhelper群**と固定版Waypipe・footを準備します。
2. Eidolonの公開鍵をTermuxへ登録し、username・host key・指紋を照合して初回ペアリングを行います。
3. 必要時のサービス起動を使う場合は、Termux連携の権限と`allow-external-apps=true`を設定します。
4. Questのライブラリ →「提供元不明 / Unknown Sources」→ **Eidolon**を開き、「ターミナルを開く」から接続します。

具体的なコマンドと復旧手順は[利用・初回設定](docs/usage.md)と[Termux toolsの版契約](docs/termux-wayland-tools.md)を参照してください。
初回連携が済んでいれば、接続拒否時に固定helperがTermuxのSSHサービスを必要時起動します。認証失敗やhost key不一致では起動処理へ進みません。
Linux用の通常接続設定・Inspect・起動手順は[L5〜L6の記録](docs/normal-linux-gui-l5-l6-20261108.md)にあります。

## ビルドと検証

主な固定環境は次のとおりです。実際の依存・導入手順は[CI定義](.github/workflows/check.yml)、[開発環境](docs/phase-0-development-environment.md)、[native依存ビルド](docs/native-dependencies-build.md)を参照してください。

| 項目 | 固定値 |
| --- | --- |
| Rust | 1.95.0 / edition 2024 |
| JDK | 25 |
| Android | compileSdk 36、minSdk / targetSdk 34、arm64-v8a |
| NDK / Build Tools | 27.3.13750724 / 35.0.0 |
| Meta Spatial SDK | 0.15.0 |

リポジトリのrootで実行します。`VERSION_CODE`は端末の導入版以上の正整数に置き換えてください。

```bash
# debug APK：assemble・lint・JUnit・native/同梱検査
scripts/build-android VERSION_CODE

# unsigned release APKと同buildのTermux tools
scripts/build-release VERSION_CODE

# hostのRust/Python・依存・設計検査
scripts/check-host --suite all

# CIではhost suiteとは別に実行するWaypipe検査
scripts/check-waypipe-clipboard
scripts/check-waypipe-ime
```

releaseへの署名と、そのAPKの実機受入れは別途必要です。開発時はUSBで初回承認・端末照合を済ませ、給電しながらWi-Fi ADBで更新する運用を検証しています。
Questの固定ケースは`scripts/quest-check`、長時間採取は`scripts/quest-observe`を使います。ケース・前提条件は[開発とデバッグ](docs/development-automation-and-debugging.md)と[利用手順](docs/usage.md)を確認してください。

### CIと実機結果の読み方

| 検証層 | 確認する範囲 |
| --- | --- |
| [host / Android CI](.github/workflows/check.yml) | Rust/Python、Waypipe clipboard/IME、debug APK build・lint・JUnit・ELF/版/同梱検査 |
| [L7a foundation CI](.github/workflows/linux-real-app.yml) | 固定Mousepad・GIO policy・実Waypipe・再起動/独立性・回収。GUI設定を含むA0全条件と125秒先行保持を必須検査 |
| Questの固定ケース | native instrumentation、通常SDK、通常UI入口を区別して記録 |
| HMD・物理操作 | 文字・部屋・候補の見え方、実キーボード・コントローラー操作を装着者が確認 |

実装基準のmain `2efca7b`では、[host/Android CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42632193815)と[L7a foundation CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42632193803)が成功しています。
skip・未実施・採取完了を受入れ成功へ読み替えず、後続commitの結果も個別に確認します。

## ソースとドキュメントの入口

| 場所 | 内容 |
| --- | --- |
| [android/](android/) | Questアプリ、Spatial SDK adapter、Compose UI、入力・IME |
| [native/](native/) | Rustのmodel・core/compositor・render・transport・theme・Android JNI |
| [scripts/termux/](scripts/termux/) / [scripts/linux/](scripts/linux/) | 接続先helper、起動・process所有・回収 |
| [scripts/](scripts/) / [scripts/fixtures/](scripts/fixtures/) | ビルド、host/Quest検査、実アプリharness、固定fixture |
| [docs/design/](docs/design/README.md) / [tasks.json](docs/design/tasks.json) | API・状態遷移・依存順・受入条件 |

開発再開時は、[最新のL7a引き継ぎ計画](docs/linux-real-app-b1-contract-20261110.md)、[NX拡張の全体計画](docs/next-phase-termux-startup-linux-gui-plan-20261107.md)、[通常GUIの後続計画](docs/nx11-nx18-normal-gui-followup-plan-20261108.md)、[進捗と失敗の履歴](docs/progress.md)を確認してください。
既存の手順・進捗には各Build時点の記述が残っています。窓数・統合状況などは本READMEの基準commitと、各機能の新しい実装・検証記録を合わせて確認してください。

今後の主な受入れは、**Mousepadの通常利用、IMEの実変換、窓操作の物理確認、長時間動作と復旧、署名済みrelease**です。[物理確認の残件](docs/phase5-manual-acceptance.md)と[Phase 6の条件](docs/design/phase-6.md)を保持し、実装・自動検証・手動受入れをそれぞれ進めます。

## ライセンス

Eidolon Waylandの独自コード・ドキュメントのうち、プロジェクトがライセンスを付与できる部分は[MIT License](LICENSE)（`Copyright (c) 2026 tin-machine`）で公開しています。MITは改変・商用利用・再配布を許可しますが、著作権表示とライセンス文面の保持が必要です。

**MITは、リポジトリ内のすべての素材や依存ライブラリ、ビルド済みAPKに含まれるSDKへ一律に適用されるものではありません。** Smithay、Waypipe、Mousepadのテストfixtureなど、第三者由来のコード・素材は元のライセンスに従います。[第三者ライセンス一覧](THIRD_PARTY_NOTICES.md)を参照してください。特にMeta Spatial SDKは[Meta Platform Technologies SDK License Agreement](https://developers.meta.com/vr/licenses/oculussdk/)に従い、EidolonのMITによって再ライセンスされません。

GitHub Actionsで生成するdebug APKにも、Meta SDKやその他の依存ライブラリが含まれます。**Publicリポジトリへの変更により、保持中のActions artifactを第三者がダウンロードできる可能性があります。** ソースコードのMIT適用は、APK配布に必要な全依存ライセンス・NOTICE・SDK配布条件の監査完了を意味しません。詳細は[OSS公開前ライセンスレビュー](docs/oss-license-publication-review-20261112.md)を参照してください。

APKにはMIT本文、第三者のNOTICE、MPL該当依存のソース取得先、Gradle実runtime依存一覧を生成・同梱し、CIのAPK artifactアップロード前に検査します。[仕組みと配布前の残件](docs/apk-license-compliance-20261112.md)を参照してください。CIゲートの成功だけで全第三者ライセンスの法的適合性が保証されるわけではありません。
