---
id: docs-knowledge-architecture-20261113
title: Eidolon Documentation Knowledge Architecture
type: architecture-design
design_status: proposed
feature: documentation-system
created: 2026-11-13
last_reviewed: 2026-11-13
baseline_commit: fc6e50cb2f1c20b02eac5ee51a987b18eecf1112
---

# Eidolon Wayland — LLM-first Documentation Knowledge Architecture

Status: **Proposed / design only** (2026-11-13 JST)  
Baseline: main <code>fc6e50cb2f1c20b02eac5ee51a987b18eecf1112</code>  
Target: CodexなどのLLMが、作業に必要な現行契約・依存設計・検証証拠を誤認なく発見できること。

> **本書は設計の保存であり、ディレクトリ再配置、AGENTS.md、manifest、解決CLI、CI変更を実装したものではない。** 以下の新しいパスやコマンドは将来の契約案であり、現行mainに存在すると解釈しないこと。

## 1. 背景・観測根拠

Eidolon Waylandの最終目標は、Meta Quest 3のMR作業空間に別Linuxホスト上のGUIアプリを、独立した複数ウィンドウとして表示・操作することである。開発は、大きいLLMが設計を固め、小さいLLM/Codexが機能単位で実装する形を想定する。

基準mainの <code>docs/</code> は**166ファイル**で、うち**143ファイルが直下**にある。既存のサブツリーは <code>design/</code> (10)、<code>curved-workspace/</code> (12)、<code>decisions/</code> (1)。設計、現在の仕様、古い調査記録、実験、ビルドログ、受入れの証拠が同じ階層に置かれている。

具体例:

| 対象 | 現在の所在 | 特徴 |
| --- | --- | --- |
| 基本設計 | <code>docs/architecture.md</code> | 初期時点の提案・設計と後続の実装状況が共存 |
| 初期Phase契約 | <code>docs/design/README.md</code>、<code>contracts.md</code>、<code>tasks.json</code> | タスクの参照先・アンカー・検証の既存契約 |
| Linux Desktop v3 | <code>docs/linux-real-app-*.md</code> | A0/B1/B2/C1/C2/E1/I2/I4等の連続する詳細設計と実装記録 |
| Curved Workspace | <code>docs/curved-workspace/README.md</code> 以下 | 契約・geometry・runtime・fixtures・テスト・作業票の優れた分割例 |
| Quest/host実験 | <code>docs/egl-*-probe-*.md</code>、<code>docs/build-*-integration-*.md</code> 等 | 特定ビルド、日時、環境に紐づく不変の事実記録 |
| CI運用 | <code>docs/ci-change-scope-20261113.md</code> | パス依存の実行スコープとrequired checks |

現行 <code>scripts/check-design</code> は <code>docs/design/tasks.json</code> を固定パスで読み、タスク依存DAG、specアンカー、チェックの提供順などを検証する。<code>scripts/ci/ci_scope.py</code> は <code>docs/design/**</code> をhost対象にし、その他の通常Markdownをビルド対象から外す一方、<code>docs/**</code> のJSON等は全ビルド扱いとなる。**移行にはこれらの既存利用者との互換性が必要**である。

### 1.1 解くべき問題

- **Discoverability**: feature/task/source pathから必要文書へ決定的に到達する。
- **Authority**: 正本、提案、歴史的記録、廃止済み資料を混同しない。
- **Dependency**: 先に読むべき契約・実装境界・チェックを欠落させない。
- **Freshness**: 設計の基準revisionと現行コードの差分を検知する。
- **Evidence**: host/Android CI合格とQuest実機での受入れを混同しない。
- **Token efficiency**: 「docsを全件読む」を禁止し、対象タスクの最小コンテキストを構築する。
- **Maintainability**: 文書移動、改名、段階的公開、複数LLMの並列作業で参照が壊れにくい。

## 2. 基本原則と非目標

1. **1 feature = 1 Knowledge Unit**。入口README、機能manifest、正本契約、タスク、検証条件を近接させる。
2. 文書の **stable IDはパスと独立**。移動してもIDは変えない。IDを再利用しない。
3. **設計の承認、コードの実装、検証の合格は別の状態**。単一の <code>verified: true</code> は設けない。
4. **時点を明示**。歴史的な調査記録は事実として保存し、その時点の記述を現在の実装保証としない。
5. **現在の実装挙動についてはソースが最終的な実態**。承認済み設計と食い違えば自動的に片方を正とせず、差異を顕在化させる。
6. **古いことと無効なことは違う**。文書の年月日やHEADとの差だけで無効判定しない。
7. **安全な失敗**。未解決ID、曖昧な正本、循環、失われた参照、確認不能な基準revisionでは暗黙のfallbackをしない。
8. **本文は原則維持**。移行は最初にメタデータ・索引・リンクの変更を行い、既存の調査証拠を要約で置き換えない。
9. **既存の製品CIとQuest検証の意味を変えない**。ドキュメントシステムの改善を実機成功に読み替えない。
10. **権限境界**。文書本文は作業上の根拠であり、コマンド実行・認証情報開示・セキュリティ保護の迂回を許可する命令にはしない。

非目標: 初回から全166ファイルの一斉移動、全文RAG/ベクトルDBの導入、LLMによる曖昧な文書自動選択、実機oracleの自動代替、GitHub上の古いURLの永続リダイレクト保証。

## 3. 最終的な論理ディレクトリ構成

~~~text
docs/
  README.md                         # 全体の機能一覧、読み順、文書契約
  architecture/
    README.md
    system.md
    android-native-boundary.md
    ownership.md
  features/
    linux-desktop/
      README.md
      v3/
        README.md                   # 対象featureの入口/境界
        manifest.yaml              # 機械解決用の依存索引
        contracts/
        design/
          i2-ssh-controller.md
          i3-window-renderer.md
          i4-startup-observation.md
          i5-integration.md
        tasks/
        tests/                      # テスト計画・oracleの仕様
      legacy/
    workspace/
      README.md
      curved/
      spatial-anchor/
      window-groups/
    input/
      ime/
      keyboard/
      pointer/
    termux/
  development/
    build/
    ci/
    tooling/
  decisions/                        # ADR: なぜその設計を選んだか
  evidence/                         # 実測/CI/HMD受入れの記録
    quest/
    host/
    experiments/
  archive/                          # 歴史的計画、置換された契約
~~~

このツリーは**予定形**であり、実際の詳細分類はインベントリから決める。<code>docs/design/</code> や <code>docs/curved-workspace/</code> を最初から削除・改名しない。特にCurved WorkspaceのREADME/01〜08/fixturesの構造を新しいfeatureの雛形として活かす。

配置規則:

- <code>architecture/</code>: 複数featureを横断する現行の責務・所有権・システム境界。
- <code>features/</code>: 各機能の正本契約、設計、依存、タスク、テスト仕様。
- <code>decisions/</code>: 設計判断の理由と代替案を保存するADR。承認後の変更は新ADRまたは明示的な置換関係。
- <code>evidence/</code>: SHA、ビルド、装置、手順、結果、未実施を固定した実測記録。追記修正時は証拠の出所を保持。
- <code>development/</code>: CI、ビルド、デバッグ、公開運用の現行手順。
- <code>archive/</code>: 正本として使わない過去文書。削除の代用ではなく履歴追跡の入口。

機能固有の受入れ仕様は <code>features/*/tests/</code> に置き、実際に得られた実行結果は <code>evidence/</code> に置く。文書種別は**物理ディレクトリだけで決めない**。機械的な判定はメタデータを使う。

## 4. Markdown Front Matter v1

Markdown先頭のYAML Front Matterを採用する。v1ではallowlist方式の型/enum検証を行い、未知キーは検証エラーとする。既存文書への付与は段階的に進め、未移行文書は現行の場所に維持し、manifest側ではlegacy参照として明示する。全ファイル同時にfront matterを必須化しない。

~~~yaml
---
id: linux-desktop-v3-i4
title: Startup Observation / Spatial SDK Evidence
type: detailed-design
design_status: proposed
feature: linux-desktop-v3
component: startup-observation
phase: I4
created: 2026-11-12
last_reviewed: 2026-11-13
baseline_commit: f894d796a06bcf0fafb1567718cd180ecf29009b
depends_on:
  - linux-desktop-v3-b1-contract
  - linux-desktop-v3-i3
supersedes: []
related_sources:
  - native/core/src/runtime.rs
  - native/model/src/linux_desktop_v3/**
  - android/app/src/main/java/io/github/tinmachine/eidolon/**
---
~~~

上は**形式例**である。<code>proposed</code> はここでの仮値であり、現行I4の承認・実装・検証を認定するものではない。依存IDは移行時にインベントリと照合すること。

| field | v1契約 |
| --- | --- |
| id | repository全域で一意の小文字kebab-case。移動で変更しない |
| title | 人間向けの一行タイトル |
| type | architecture / contract / detailed-design / task / test-plan / evidence / decision / runbook / historical / index |
| design_status | proposed / accepted / superseded / retired（設計・契約系で必須、他種別は適切な状態値を別途schemaで定義） |
| feature | stable feature ID。横断文書には明示的なsystem scopeを使う |
| component / phase | 任意の細分化。IDとの意味的な矛盾を検査する |
| created / last_reviewed | ISO 8601日付。日付は新しさの証明ではない |
| baseline_commit | 設計時に確認した40文字SHA（Gitの実体存在を検査） |
| depends_on | 文書IDによる前提。循環不可 |
| supersedes | 置換されたID。置換先の存在とチェーン整合性が必要 |
| related_sources | repository相対のglob/path。存在と安全性を検査し、変更影響解析に使用 |

<code>accepted</code> は設計に対する人間のレビュー/承認を示すメタデータであり、CIが内容を真だと証明するものではない。移行時に自動で全設計をacceptedにしてはいけない。

### 4.1 独立した状態モデル

- **Design status**: proposed → accepted → superseded / retired。状態変化はPRの差分でレビュー可能にする。
- **Implementation status**: not_started / implementing / implemented / blocked。**タスクの正本**で管理し、同じ値をfront matterへ二重書きしない。
- **Verification status**: not_run / pass / fail / blocked / unknown。**証拠の正本**でhost、Android、Quest、manualを別々に記録する。
- **Evidence identity**: 対象commit、APK/build、実行host/device、手順、日時、観測、結果を区別。別commitの成功を新HEADの成功として継承しない。
- **Legacy**: メタデータ未移行は <code>unclassified</code>。自動でacceptedへ昇格させず、候補検索には出しても正本扱いしない。

例: I4が設計上accepted、実装implementing、host=pass、Android=not_run、Quest=not_runであることは同時に成立し得る。単一の <code>verified</code> は禁止。

## 5. Feature manifest v1

featureの入口 <code>manifest.yaml</code> は**リンク索引・依存解決の正本**とし、個々の文書のID/状態/基準SHAの正本はFront Matterに置く。重複した状態を二か所で管理しない。

~~~yaml
schema: 1
feature: linux-desktop-v3
entrypoint: README.md
contracts:
  - linux-desktop-v3-b1-contract
tasks:
  I2:
    document: linux-desktop-v3-i2
    requires:
      - linux-desktop-v3-b1-contract
  I4:
    document: linux-desktop-v3-i4
    requires:
      - linux-desktop-v3-b1-contract
      - linux-desktop-v3-i3
source_paths:
  - native/transport/src/linux_desktop/v3/**
  - native/model/src/linux_desktop_v3/**
  - native/core/src/**
~~~

上のパス・ID・タスク対応は**移行後の想定例**。未作成のI3/I5文書を現存扱いしない。パイロットでは欠落文書を黙って補完せず、explicitな <code>planned</code> / <code>external</code> 参照表現をschemaへ定義するか、当該エントリの有効化を遅らせる。

- manifest内の <code>document</code> / <code>requires</code> / <code>contracts</code> はパスでなく安定IDを使う。
- manifestはfeature/taskの読み順と入口を定義。doc ID → pathの全域索引はFront Matter走査から生成する。生成物は手編集しない。
- 依存の展開はstable ID単位で重複排除、トポロジカル順＋同順位は辞書順。欠落・循環はエラー。
- <code>source_paths</code> は関連コードの検索対象であり、排他的な所有権の宣言ではない。共有sourceは複数manifestに一致してよい。
- ファイル単位の <code>related_sources</code> を使って詳細な差分確認を行う。feature単位の広いglobだけで安全判断しない。
- 複数のaccepted contractが同一判断を競合する場合、更新日時・辞書順による暗黙の勝者を作らず**authority conflict**として停止する。必要なら明示的なsupersedesやscope境界を追加する。
- 継続的に参照する初期Phase契約にはcompatibility aliasを定義できるが、aliasは単一IDへ解決させ、循環禁止とする。

## 6. Authority / Source / Evidenceの優先規則

LLMに「最新らしい文章を採用する」自由裁量を与えない。解決結果の区別は次のとおり。

| 種別 | 使い方 | 禁止 |
| --- | --- | --- |
| accepted contract | 対象scopeの実装要求・不変条件 | sourceに不一致があるのに無言で完了と判断 |
| accepted detailed-design | 契約を具体化する実装方針 | 明示した上位契約を勝手に上書き |
| proposed detailed-design | 設計候補/実装前レビュー | 既存正本を自動置換 |
| task | 実装範囲・チェック・done_when | 手動/HMD項目を自動テスト通過で置換 |
| evidence | 特定revision/build/deviceの観測事実 | 別revisionの成功へ一般化 |
| historical/archived | 判断の経緯・再現背景 | 正本として自動採用 |
| current source | 実際の現在のプログラム動作 | 設計変更承認の代用 |

正本に矛盾があれば「contract violation / spec drift / underdetermined」を出力し、LLMが差分を調査する。実装側に合わせてcontractを自動変更しない。適切な修正提案は別のレビュー対象とする。

## 7. Codex統合と決定的context resolver

### 7.1 入口

将来、ルート <code>AGENTS.md</code> を短く保つ。Codexへfeature/taskを指定したら、まずmanifestからcontextを解決させる。文書の全文をAGENTS.mdにコピーしない。

~~~text
1. 最新のmain/作業branch、AGENTS.md、変更対象を確認する。
2. feature/taskを確定しdocs resolverでcontextを取得する。
3. required contract → dependency design → task → test planの順に読む。
4. baselineと現在のrelated_sourcesのdriftを確認する。
5. 未解決の権威衝突・欠落・破壊的なdriftを黙殺しない。
6. 許されたsourceを実装し、指定チェックを実行する。
7. 自動検証とQuest/HMD受入れの未実施を別に記録する。
~~~

注意: 深い階層の <code>AGENTS.md</code> が、別ディレクトリのsource編集へ自動適用されると仮定しない。ルート指示からCLIを明示して呼ぶ。

### 7.2 提案CLI

将来の入口を <code>scripts/docs.py</code> とする。Python CLIは高速・read-onlyを基本とし、初版でネットワークやLLM APIを必須にしない。

~~~sh
python3 scripts/docs.py validate
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4 --format json
python3 scripts/docs.py affected --path native/core/src/runtime.rs
python3 scripts/docs.py drift --feature linux-desktop-v3 --task I4
~~~

コマンド契約:

| command | 戻り値/失敗 |
| --- | --- |
| validate | 重複ID、schema不正、欠落パス/ID、循環、アンカー/リンク、未許可glob、authority conflictを検出 |
| context | manifestの依存閉包を安定順に列挙。文書のpath、type/status、baseline、reading order、明示的な警告を出す |
| affected | 与えたsource pathに関連する全feature/doc IDを列挙。共有領域では複数候補を落とさない |
| drift | 基準commitと対象HEAD間の関連source差分を分類。semantic correctnessは判定しない |

想定JSON出力:

~~~json
{
  "schema": 1,
  "feature": "linux-desktop-v3",
  "task": "I4",
  "documents": [
    {"id": "linux-desktop-v3-b1-contract", "path": "docs/features/linux-desktop/v3/contracts/b1-contract.md", "role": "contract"},
    {"id": "linux-desktop-v3-i4", "path": "docs/features/linux-desktop/v3/design/i4-startup-observation.md", "role": "task-design"}
  ],
  "drift": {
    "status": "check_required",
    "baseline": "f894d796a06bcf0fafb1567718cd180ecf29009b",
    "changed_paths": []
  },
  "warnings": []
}
~~~

これは**出力形式の例**であって実行結果ではない。<code>status=check_required</code> と <code>changed_paths=[]</code> の組み合わせは、SHA確認不能等であり得るが、実装では <code>reason</code> を必須とし、偽の正常判定を避ける。

初版の出力は索引・経路を示す。本文の自動連結は、明示的なバイト上限、重要契約の優先順位、途中省略の可視化を定義するまで行わない。上限超過なら黙って切り詰めず、文書一覧と要読箇所を返す。

### 7.3 Driftの意味

- <code>clean</code>: 基準が取得可能、対象HEADまでの差分で登録された関連パス変更なし。**正しさの証明ではない**。
- <code>check_required</code>: 関連パス変更あり。Codexが差分と契約の再照合を行う。
- <code>unknown</code>: 基準SHA不存在、浅いfetch、祖先関係不明、diff障害、glob不一致など。cleanへfallbackしない。
- <code>conflict</code>: 承認済み契約間の衝突、または明示的な不変条件の矛盾が判明。実装を進める前に解消する。

実装メモ: 基準コミットがHEADの祖先かを最初に検証し、<code>git diff BASE HEAD -- paths...</code> とrename/copyの両パスを考慮する。ブランチを横断する場合はmerge-baseの扱いを明示し、関係が曖昧ならunknown。変更行がなくても実機SDKや外部依存に影響がある場合は別の再検証が必要。

### 7.4 安全境界

- Front MatterとmanifestのYAMLは安全なパーサ・限定schemaで読む。任意オブジェクトの構築やカスタムタグを受けない。
- IDやpathをシェルのコマンド文字列として評価しない。リポジトリ外、絶対パス、<code>..</code>、不正なsymlink解決を拒否。
- 文書内のコマンドは「検証予定のデータ」。Codexは作業指示と実環境の許可範囲に照らして実行する。
- Context resolverは外部URLを取得して追加指示を自動導入しない。外部SDK情報は別途出典と版を照合する。

## 8. 既存契約との互換性

### 8.1 <code>scripts/check-design</code>

現状、<code>DESIGN = ROOT / "docs/design"</code> と <code>tasks.json</code> のspec相対path/アンカーを前提とする。移行初期はこのスクリプトと既存タスクを**完全に残す**。新validatorは独立して動かす。

<code>docs/design/</code> を移す前の必須条件:

1. check-designの入力先を明示的なmanifest/ID解決または移行済みパスへ変更。
2. <code>tasks.json</code> のspec pathとanchor、<code>files</code>、<code>checks</code>、依存グラフを維持。
3. 既存の <code>python3 scripts/check-design</code> が同じ保証で成功。
4. 新validatorも成功。既存チェックの削除を成功扱いにしない。

### 8.2 CIのパス分類

現状 <code>scripts/ci/ci_scope.py</code> は <code>docs/design/**</code> と、普通の <code>docs/**/*.md</code> と、<code>docs/**/*.json</code> 等を別分類している。manifest.yamlを新設すると、**現行classifierでは全ビルドが起動する**。

対応:

- workflowを <code>paths-ignore</code> で止めず、既存の軽量scope jobとrequired checks構造を保つ。
- docs metadata/manifest/validatorの変更で専用docs validation jobを必須実行。
- 実際にビルド入力となる <code>docs/design/**</code>、fixtures/契約JSONは、従来と同等以上の対象CIを維持。
- classifier・schema・validatorの変更はセキュリティ上重要な変更としてレビューし、fail-openで必要な全ジョブを実行。
- renameの旧新パス双方を分類し、移動によってチェックをすり抜けない。
- 「docs-only」でもlint/リンク検査は実行するが、無関係なAndroid大規模ビルドは省略可能にする。
- required check名の変更・追加はGitHub Rulesetと整合させ、Skipped/Pendingの扱いを検証する。

CIの正本: [change-scoped CI](ci-change-scope-20261113.md) と <code>scripts/ci/ci_scope.py</code>。

### 8.3 リンクと履歴

- <code>README.md</code> / <code>docs/*.md</code> / <code>docs/design/tasks.json</code> / scriptsの相対参照を静的検査する。
- intra-repo Markdownの相対リンク、アンカー、画像、JSON fixturesの相対pathを移動前後で検証する。
- <code>git mv</code> を使いGitのrename追跡を利用する。ただしGitがrenameを永続的に保証するわけではない。
- 外部からのGitHub <code>/blob/main/docs/old-path</code> リンクは自動リダイレクトが保証されないため、必要な入口は短い移動案内（stub）を残す。
- SHA固定URLは歴史的証拠への参照として基本維持し、現行仕様の正本リンクと区別する。
- 同一ファイルの単なる移動で本文を大幅編集しない。レビュー可能な差分粒度を保つ。

## 9. Knowledge Graphへの拡張余地

最初からRAG/Graph DBを必須にせず、manifestとFront Matterを**型付きの有向グラフ**として扱う。

~~~mermaid
flowchart LR
  F[Feature] --> T[Task]
  T --> D[Detailed design]
  D --> C[Contract]
  D --> S[Source paths]
  T --> P[Test plan]
  P --> E[Evidence]
  D --> B[Baseline commit]
  D -.supersedes.-> O[Older design]
~~~

node: Feature、Task、Document、Contract、SourcePath、Evidence、Commit。edge: requires、implements、affects、verified_by、based_on、supersedes。ID/edge種別を固定し、将来的な検索・差分分析・設計影響範囲の逆引きに利用する。

将来の問い合わせ例:

- 「I2 SSH Controller変更で影響を受ける設計・受入れは？」
- 「Questで未実施の受入れが残るfeatureは？」
- 「このsourceが依存するaccepted contractは？」
- 「古いbaselineを持ち、関連ソースが変化した設計は？」

検索結果は必ず根拠path、ID、SHA/evidence identityを返し、LLMの推測だけで状態を作らない。

## 10. 実装フェーズとPR境界

現行docsの一括移動を最初のPRに含めない。複数の独立PRで、常に旧入口を利用できるようにする。

| 段階 | 作業 | 受入れ |
| --- | --- | --- |
| D0 | **本設計の保存** | Markdown追加のみ。既存source/CI/文書は不変更 |
| D1 | 全ファイルのread-only inventory、分類・ID台帳案 | 166ファイルを欠落なく列挙。未分類は明示 |
| D2 | metadata schema、機能manifest schema、<code>docs/README.md</code>、短いroot <code>AGENTS.md</code> | 種別/状態の意味と入口が一意、既存文書を壊さない |
| D3 | <code>scripts/docs.py</code> のvalidate/context/affected/drift、単体テスト | 出力順決定的、欠落/衝突/祖先不明は安全に失敗 |
| D4 | docs CI jobとclassifier調整 | docs-only、fixtures、設計契約、rename、workflow変更を回帰検査 |
| D5 | Linux Desktop v3のパイロット移行 | I2/I4/B1などからtaskごとに必要文書を解決可能。現行設計本文と参照を維持 |
| D6 | Curved Workspace、Spatial Anchor、IME、Phase 0〜6、実験/記録を段階移行 | links/check-design/fixtures/README/CIが保持される |
| D7 | ソース逆引き・証拠照合の運用 | PRで変更影響と未実施の検証が判別できる |

推奨PR分割: (1) D1〜D2、(2) D3、(3) D4、(4) D5、(5) D6以降。CIが高コストなため、実装PRはレビュー可能性を保ちながら大きめにまとめ、docs-only経路を検証してから大規模な移動に着手する。

### 10.1 Linux Desktop v3パイロット

1. 既存のI2/I4詳細設計とB1契約/統合計画の**実際の依存関係**を最新mainのsourceと照合する。
2. stable ID一覧と、正本/参考/歴史的記録の分類を確定する。存在しないI3/I5文書を仮の実体として登録しない。
3. manifestからI4を引くと、必要なB1/共有契約、I3に要求するインターフェース、I4設計、受入れ仕様が列挙されること。
4. sourceとのdriftと未完了のQuest受入れを別々に出力すること。
5. パイロット成功まで、旧 <code>docs/linux-real-app-*.md</code> の参照を壊さない。

## 11. テスト・受入れ契約

### 11.1 Validator（host、ネットワーク不要）

最低限の回帰試験:

1. すべてのmanaged IDが一意。複数manifestから同一IDが異なるpathへ解決されない。
2. type/design_status enum、日付、SHA、必須値、未知キーを検査する。
3. <code>depends_on</code> と <code>supersedes</code> の未知ID、自己参照、循環を拒否。
4. accepted contractのauthority衝突を検出し、勝者を推測しない。
5. absolute/parent escape/symlink escapeのpathを拒否。
6. 相対Markdownリンクとアンカー、README、既存tasks specを検証。
7. resolverが同一入力で決定的な順序・同一JSONを返す。
8. 複数featureに一致する共有sourceをすべて返す。
9. 未移行ファイルをacceptedに昇格させない。
10. コンテキスト上限で黙って文書を切り捨てない。
11. baselineの存在/祖先関係不明を <code>unknown</code> とする。
12. driftの無関係差分はclean、関連差分はcheck_requiredとなる（fixtureで検証）。
13. renamed pathの旧新双方を影響判定する。
14. Quest/manual未実施はhost passで埋めない。
15. 既存 <code>scripts/check-design</code> のチェック結果が退化しない。

### 11.2 CIと移行受入れ

- 既存文書の移動前後で、全文・コードブロック・実測条件の欠落がないことを差分確認。
- docs-onlyのPRで軽量検証は成功、不要なAndroid/hostジョブはscope通りSkipped。
- docs/design契約変更とJSON fixture変更で、従来必要だったビルドが誤って省略されない。
- README/ドキュメント内の相対リンク・画像・anchorの解決に失敗がない。
- プロジェクトの古い実測記録を新実装のQuest成功として提示しない。
- PR本文には移動件数、link修正件数、validator結果、CI影響、未移行範囲を明記する。

### 11.3 Rollback

各段階は独立commit/PRとし、破壊的変更の前に既存文書と索引を残す。失敗した場合は対象PRをrevertできるようにする。旧 <code>scripts/check-design</code> と旧参照が新resolverより先に削除される移行は禁止する。

## 12. 実装者への引き継ぎ

1. 最新main、適用されるAGENTS.md、<code>docs/architecture.md</code>、<code>docs/design/README.md</code>、<code>docs/design/tasks.json</code>、<code>docs/ci-change-scope-20261113.md</code>、現行classifier/check-designのsourceを最初に読む。
2. 本書は**将来の設計契約案**であり、承認されていない決定は現行実装の事実と混同しない。
3. D1の棚卸しでID、型、正本、legacy候補、関連sourceを確定する。最初から166ファイルへ大量のfront matterを挿入しない。
4. D2〜D4でschema/索引/validator/CIを整備し、旧タスク契約とrequired checksを保つ。
5. D5でLinux Desktop v3のI2/I4を実例としてcontext resolverの精度を検証する。
6. 実装、CI通過、Quest実機受入れの各完了判定は別に記録する。
7. docs-onlyの変更であっても参照崩壊・安全性の退行があり得る。read-only検査に加え、必要なCI scopeの意図を確認する。

### 根拠（基準main）

- [アーキテクチャ](architecture.md)
- [初期詳細設計の入口](design/README.md)
- [初期タスク契約](design/tasks.json)
- [Linux Desktop v3統合計画](linux-real-app-v3-integration-plan-20261111.md)
- [I2詳細設計](linux-real-app-v3-i2-detailed-design.md)
- [I4詳細設計](linux-real-app-v3-i4-detailed-design.md)
- [Curved Workspace詳細設計](curved-workspace/README.md)
- [Spatial Anchor / SA0設計](spatial-anchor-linux-design-20261111.md)
- [CIスコープ仕様](ci-change-scope-20261113.md)
- [設計検証スクリプト](../scripts/check-design)
- [CI分類器](../scripts/ci/ci_scope.py)
- [Codex AGENTS.mdの公式文書](https://developers.openai.com/codex/guides/agents-md/)
