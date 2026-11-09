# L7a A0: PR #4後の実装計画と次セッションへの引き継ぎ

更新日: 2026-11-09。状態: **PR #5までmainへマージ済み。PR2のGUI設定oracleと明示的A0成功経路を追加**。
PR2の観測方式・偽陽性拒否・期限と回収の維持・後続工程の境界は
[GUI設定保持・保護設定の実装記録](linux-real-app-a0-gui-settings-20261109.md)を参照する。
A0の成功は当該headの実Mousepad/WaypipeとCIで判断し、実装の存在だけから認定しない。
以下のPR #4直後の状態表および末尾のPR1向けプロンプトは過去の基準である。
PR1の変更根拠、caseごとのoracle、回収契約、検証入口は
[再起動・先行instance独立の実装記録](linux-real-app-a0-restart-20261109.md)を参照する。
実測成功は今回のPR headのCI結果で確認し、以下のPR #4時点の結果から推定しない。

この文書は、Quest 3上の広い作業空間で複数のLinuxアプリを扱うという目標に向け、
実用アプリ一種のL7aを次の会話・開発セッションから継続するための計画である。
最初に最新mainと関連PRを確認し、本書の基準commit以降の変更を調べる。
過去の検証結果を、その後の変更や別のLinux/package構成の成功へ読み替えない。

## 1. 読む順序と確認済みの現在地

1. 本書で次PRの範囲、完了条件、未実装事項を把握する。
2. [A/A0検査基盤の実装記録](linux-real-app-a0-foundation-20261109.md)で現在の実装と上流根拠を読む。
3. [L7a詳細設計](linux-real-app-l7a-design-20261109.md)でA0および後続B〜Fの契約を確認する。
4. [通常Linux GUIの全体計画](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-real-app)との位置付けを確認する。

### 基準revisionとCIの証拠

- [PR #4](https://github.com/tin-machine/eidolon-wayland/pull/4)はマージ済み。
- merge commit: [`6fb00482a1f3550f311a6b26700951ac9752c315`](https://github.com/tin-machine/eidolon-wayland/commit/6fb00482a1f3550f311a6b26700951ac9752c315)。
- 検証したPR head: [`35c1b8ca0caebfca22d82ca0945c782e5e1b75a2`](https://github.com/tin-machine/eidolon-wayland/commit/35c1b8ca0caebfca22d82ca0945c782e5e1b75a2)。merge commitと同じtree `dbe3f05b5909d05b655191e05c3a85d98536fb1c`。
- [専用foundation CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42561323642)は成功。
  GIO16件・inspector38件・aggregator26件・controller19件・実process lifecycle3件、合計102件をskipなしで実行。
  Rust fixture3件と、実Waypipe両端・未改変MousepadのOpen/編集/Save/Save Asも成功。
- [既存host/Android CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42561323663)も成功。
  hostはPython631件・8skipと補助9件/114件、AndroidはAPK build/lint/ELF/同梱検査とPython631件・1skip。
  hostの8skipは実APK依存7件と既存KWin依存1件。APK依存7件はAndroidで実行済み。
  今回追加した11件は両jobで成功し、実process lifecycle3件も実行済み。

以上はPR headに対するCI結果であり、mainへのpush後の別runを検証済みとする記録ではない。
[実アプリreceipt artifact](https://github.com/tin-machine/eidolon-wayland/actions/runs/42561323642/artifacts/13649810935)は保持期限があるため、
必要な結果をここにも残す。終了時はwindow0・pressed0・focus null、cleanup complete・pending false・open pidfds0・root pidfds closed・pipe EOFだった。

### 実装済みと未完了を分ける

実装済みの基盤は、read-only package検査、専用GIO settings backend、起動前の復元データguard、
実Wayland/Waypipe/Mousepadによるファイル操作と通常Close、pidfd/subreaperによる全子孫回収である。
対象はCIで固定したMousepad 0.6.3/GTK3構成。対象利用者Linux全般やQuestでの対応を認定していない。

PR #4時点の判定は `foundation_verified=true` でも、A0全体は `APP_A0_INCOMPLETE`、
`a0_passed=false` / `available=false` / `production_acceptance_passed=false` / `quest_verified=false`。
feasibility CLIはexit 2、acceptanceは明示的に未実装である。
根拠は[現在のaggregator](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/linux_real_app_check.py)と
[foundation CI gate](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/ci/check-linux-real-app-foundation)。

| 項目 | 本書作成時点 | 次の実装単位 |
| --- | --- | --- |
| `independent_instance` | 未確認 | PR1: 先行instanceとの独立 |
| `clean_close_restart` | 未確認 | PR1: 同じ合成profileでの終了/再起動 |
| `gui_setting_persistence` | 未確認 | PR2: GUI変更後の通常Close/再起動 |
| `protected_settings_gui_readonly` | 未確認 | PR2: 実Preferencesの操作不可と実動作 |
| v3通常起動・Quest受入れ | 未実装/未確認 | A0成立後のB〜F |

このPR1/PR2は実装の分割名であり、GitHubの既存PR番号ではない。表の状態はPR #4直後の基準である。
今回のPR1では `independent_instance` / `clean_close_restart` の実測harnessと対応validatorを追加し、
成功時のpendingはGUI設定2項目だけになる。各headの実行結果は実装PRへ記録する。

## 2. 次のPR1: 同じ合成profileでの再起動と先行instanceとの独立

推奨タイトル: **A0: 同一合成profileでの再起動と、既存Mousepad instanceからの独立**。

### 2.1 profileと起動ごとの資源を分離する

現在の[run_case()](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/linux_real_app_wayland.py#L266)は
空artifactを要求し、その中へprofileとlockを新規作成する。単に2回呼ぶと、同じprofileでの再起動を検査できない。
検査器が所有する合成profileだけを再利用できる内部harnessへ分ける。
通常利用profileを任意指定するCLIへ拡張せず、新しいartifactの空・private・no-follow条件も維持する。

| 再起動をまたいで保持 | 起動ごとに新規作成 |
| --- | --- |
| 合成profileのHOME/config/data/state | runtime directory・Waypipe socket・専用D-Bus |
| 合成Documentsと保存済みbytes | compositor・Control・Mousepad process |
| 通常設定の保存先とprofileのidentity | live owner・実行ごとのartifact/receipt |

ここで扱うのはA0の合成profileである。製品の `installation_id` / `profile_storage_id` 保存やv3 helperの配備を先行実装しない。
既存のprofile/lockを再利用するときは所有・型・権限・link・identityを再検査し、lock取得からapp起動前guard、全子孫回収までを連続させる。
lock fileを削除して作り直すことで、別inodeに対するロックを同じ保護と扱わない。

### 2.2 新しいownerとControlで各回を検査する

凍結対象の[ProcessOwner](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/linux/eidolon_linux_process_owner.py)は
専用・single-thread・子孫ゼロのprocessで生成し、終了したownerへ再spawnできない。
全回収後なら同じ専用Python processで新しいownerを作ることは可能であり、逐次再起動ごとの新OS workerは隔離を明確にする設計選択である。

各回に新しいcompositorとControlを使い、[main latchと観測履歴](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/fixtures/linux-real-app-wayland/driver.py#L272)を維持する。
再起動のために同じControlのmainや `observed_mapped` を消して、初回mainの条件を弱めない。
別compositorではWindowIdが再び `"1"` でも正常なので、異なるWindowId数値を新instanceの証拠にしない。
新しい実行・接続・main・最初のSHM frameを関連付けて判定する。

### 2.3 終了/再起動の独立したoracle

[既存の復元データ保護契約](linux-real-app-l7a-design-20261109.md#既存の復元データは起動前に保護する)に従い、
通常Close、明示Stop、アプリ異常終了を別caseとして扱う。
安全なprofileでの再起動成功と、復元データを持つprofileのspawn前拒否を区別する。

| case | 合格条件 |
| --- | --- |
| 通常Close後 | 全回収後、同じprofileからmain/SHM frameが得られる。保存済み文書・通常Closeでflushされた設定を保持する |
| 明示Stop後 | 対象scopeを強制回収し、子孫・pidfd・pipe終了を確認してから同じprofileを再検査。安全な状態では再起動できる |
| アプリ異常終了後 | Close/Stopの結果から推定せず、別の固定fixture caseで回収と同profileの再検査を実施する |
| 非空session/autosave/判別不能な復元状態 | `APP_STATE_RECOVERY_REQUIRED` でapp spawn前に拒否し、設定・文書・復元候補bytesを保持する |
| 検査processへの終了要求/記録I/O失敗 | 今回のPR #4修正と同じく、回収完了までlive ownershipとprofile lockを保持する |

実際の終了後profileに復元データがあれば、固定reasonによる保護拒否を記録する。
文書や復元候補を消して再起動を成功させない。安全なprofileでの再起動とseeded recovery拒否の両方の証拠を必要とし、
常に拒否するだけの実装を再起動成功と扱わない。
通常Close時の設定flushと、強制終了時の未flush設定保持は別であり、後者を保証しない。
異常終了caseが未実施なら残作業を明示し、対応するチェックを推測で完了扱いにしない。

### 2.4 先行instanceは別worker/別ownership scopeにする

利用者の実desktopを借りず、別の合成HOME・bus・displayで先行Mousepadを起動する。
その後に対象Mousepadを起動し、次を照合する。

- 対象側に新client/main/最初のSHM frameが来る。
- 先行側のwindow構成・設定が保たれ、対象側のClose/Stop後も先行側が応答する。
- 対象側の回収は対象ownerだけが行う。
- 検査の最後には先行側も含め、検査が生成した全資源を回収する。

既存ownerは自分の全子孫を回収するため、一つのownerのfinishを「対象側だけの終了」として使わない。
並行する先行/対象instanceは、別workerと別ownership scopeで管理する。
さらに、workerを起動するcoordinatorにも起動前から終了要求・記録失敗への所有継続を設ける。
通常の対象Stopでは先行側を維持し、検査全体の中断では自分が作った全workerを回収する、という二つの終了範囲を分ける。
外側processのSIGTERMでworkerだけを残す構造を新設しない。
`CleanupPending` とlive ownerはprocess-localである。workerがpendingをJSONで親へ通知した後に終了する構造にせず、
worker自身がpidfd/lockを保持して再試行する。IPCの状態通知を所有の移管と扱わない。

### 2.5 主な変更候補とPR1の境界

| 入口 | 変更候補 |
| --- | --- |
| `scripts/linux_real_app_wayland.py` | 合成profileと各runの分離、既存guard/lock/ownerの連続した所有 |
| `scripts/fixtures/linux-real-app-wayland/driver.py` | 新しいControlごとの初回main・ファイル操作・終了確認 |
| 新しい固定検査worker/harness | 先行instanceと対象のscope分離、coordinator全体の回収。自由argv実行口を追加しない |
| `scripts/linux_real_app_check.py` | 実測したcaseの集約、証拠の対応・pending集合・receipt validator |
| `scripts/ci/check-linux-real-app-foundation`、専用workflow、関連tests | 新しいチェックを必須実行し、失敗や未実施を成功にしない |

このPRではGUI設定2項目を未完了として維持する。A0全体・v3通常起動・Quest成功を宣言しない。

## 3. 次のPR2: GUI設定の保持と保護設定の操作不可

本節の計画に対応する実測oracle、集約、receipt validator、CI gate、偽陽性拒否testを
[PR2実装](linux-real-app-a0-gui-settings-20261109.md)で追加した。
最新headの実行結果は同実装のPR本文を参照する。後続のv3/Android/Quest工程は本節の完了と別である。

PR1の再起動harnessを利用する。現在の[settings_checks()](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/linux_real_app_check.py#L115)は
固定probeのAPI操作と別process読出しであり、GUIからの変更・再起動後のGUI状態を証明していない。

### 3.1 通常設定は行番号表示の1項目から

1. 新しい合成profileで既定状態を観測する。
2. 実Waylandのkey/pointer経路から、Mousepad GUIで行番号表示を変更する。
3. GUI状態の変化と保存された設定値の両方を照合する。
4. 通常Close・完全回収後、新しいrun・同じprofileで再起動する。
5. GUI状態と設定値の保持を確認し、別profileでは変更が漏れていないことも確認する。

keyfileを先に書き換えたり、既存probeだけで変更した値を読んだりして、このGUIチェックを完了にしない。

### 3.2 GUIの観測方式を先に具体化する

現在の[fixture snapshot](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/fixtures/linux-real-app-wayland/src/main.rs#L191)は
WindowId/parent/app_id/lifecycle/frame/入力countであり、widget sensitivityや行番号の描画状態を返さない。
`NO_AT_BRIDGE=1` とactivation serviceを含まないprivate busを使っているため、既存のAT-SPI観測経路があると仮定しない。

候補は合成GUI専用の限定した読み取り観測をfixtureへ追加し、receiptには固定bool/digestだけを出す方法。
[ProtocolState.latest_snapshot()](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/native/core/src/compositor/snapshot.rs#L882)は描画観測の入口候補だが、
これだけでGTK widget状態が取得できるわけではない。観測方式と誤判定条件を実装前に詰める。

クリック後に設定が変わらなかっただけでは、対象widgetに入力が届かなかったケースを除けない。
同じ入力経路で通常設定を変更できる正例を組み合わせ、操作不可の負例を判定する。
Mousepad本体への検査counter埋込み、アプリ本文/title/pixelsの公開記録、API probeだけのGUI合格は追加しない。

### 3.3 保護3キーとGUIに露出する2設定を区別する

固定版Mousepadの[Preferences binding](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-prefs-dialog.c#L691)と
[UI定義](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/ui/mousepad-prefs-dialog.ui)では、
`opening-mode` と `session-restore` のcontrolを確認でき、`autosave-timer` のPreferences controlはない。
「3個のGUI widgetがdisabled」とする受入れ条件を作らない。

- backendでは3キーの値/型/writable/write/reset/混在transaction拒否を検査する。
- 実Preferencesのtransient parentを検査し、UIに露出する2設定の操作不可を観測する。
- 通常Openが同じmainのtabであることと、復元データ保護を含む実動作を照合する。

## 4. gateは実測oracleと同じPRで更新する

現在の[validate_application_foundation()](https://github.com/tin-machine/eidolon-wayland/blob/6fb00482a1f3550f311a6b26700951ac9752c315/scripts/linux_real_app_check.py#L210)は
正確なpartial/incompleteと4項目のpendingだけを受理し、偽のfull-A0 receiptを拒否する。
pendingのboolを立てるだけでA0を完成させない。

各PRで、実測case・証拠の対応・receipt validator・foundation CI・偽陽性拒否testsを一緒に更新する。
未確認のcaseを残す段階は引き続きA0未完了とする。
A0全項目成立時には明示的なA0成功経路を作るが、v3 production acceptanceとQuestの未確認は独立に残す。
missing dependency/display/packageやcleanup未完了をskip成功へ変換しない。

## 5. 維持する契約と今回のレビュー修正

PR #4では以下の2件のP2を修正し、[11件の回帰テストを追加](https://github.com/tin-machine/eidolon-wayland/commit/35c1b8ca0caebfca22d82ca0945c782e5e1b75a2)した。
新しいworker/coordinatorにもこの契約を適用する。

1. `SIGINT` / `SIGTERM` / `SIGHUP` は最初のspawn前から要求をラッチし、handlerから例外を投げない。
   Popenから子/owner/pidfd/pipe登録までを中断させず、安全な境界で検証を停止する。
   回収中・pendingのhandoff・記録中もguardを保持し、全子孫回収後に元handlerを復元する。
2. receiptのopen/write/fsyncが失敗しても、同一の `CleanupPending` とlive runを上位へ渡す。
   scan/drainのI/O失敗でも所有を保持する。回収後も最初の検証失敗を成功に変更しない。

根拠と実装は[A/A0実装記録の終了処理](linux-real-app-a0-foundation-20261109.md#終了要求と記録失敗でも所有を継続する)を参照。
保存PIDを後から読んでsignalする回収や、lock取得だけを旧owner不在の証拠とする変更は行わない。

以下のv2 sourceは `e401918fa657fa91c2256353b7992c4376d47f43` の固定bytesを維持する。
判定は[固定hash fixture](../scripts/tests/fixtures/linux-desktop-v2-e401918-sources.json)を使い、変更後のsourceからgoldenを作り直さない。

- `scripts/linux/eidolon-linux-session.py`
- `scripts/linux/eidolon_linux_desktop.py`
- `scripts/linux/eidolon_linux_process_owner.py`
- `scripts/linux/eidolon_linux_desktop_session.py`
- `scripts/fixtures/gtk3-desktop-demo.c`

識別子も区別する。catalog IDは `mousepad-editor-v1`、固定CI構成で観測したnative Wayland app_idは `mousepad`。
GApplication/schema IDの `org.xfce.mousepad` と混同しない。
通常catalog/UI、v2 pins/bootstrap、production storage ID、native origin/SDK表示watchdogはPR1/PR2で切り替えない。

## 6. A0成立後の順序

[詳細設計の依存関係](linux-real-app-l7a-design-20261109.md#実装単位と完了条件)は `A → A0 → B/C → D → E → F`。

| 工程 | A0後に進める実装 |
| --- | --- |
| B | 固定manifest、settings moduleを含むbundle/pins、v3専用source/decoder、hashのgolden vector |
| C | 通常helperの永続profile/lock/専用bus/設定policy/復元データguard |
| D | native origin、固定main admission、Startedとの相関、SDK表示までの期限/回収 |
| E | Android通常UI、固定catalog、storage IDの保存、表示状態の接続 |
| F | v3 host end-to-end、旧APK更新Recover、対象Linux/Questでの表示・入力・終了受入れ |

B/CはA0でcontractを確定した後に分割・並行化できる。
PR1/PR2の実施だけで、Questの通常ランチャーからMousepadを利用可能とするものではない。

## 7. 次セッション用の初期プロンプト

以下を新しい会話へ貼り付ける。計画本文を参照してPR1だけを実装する依頼であり、
この引き継ぎ文書の保存自体はPR1の実装開始や完了を意味しない。

```text
tin-machine/eidolon-waylandの開発を継続してください。
目標はQuest 3の広い作業空間で複数のLinuxアプリを扱うことです。

まず最新mainと関連PRを確認し、次の引き継ぎ計画を読んでください。
https://github.com/tin-machine/eidolon-wayland/blob/main/docs/linux-real-app-a0-next-steps-20261109.md

併せて以下を読んで、計画と実装を照合してください。
- docs/linux-real-app-a0-foundation-20261109.md
- docs/linux-real-app-l7a-design-20261109.md
- リポジトリに現在存在するAGENTS.md等の作業指示

PR #4はマージ済みです（merge 6fb0048、検証済みhead 35c1b8c）。
ただしA0全体は未完了で、v3通常起動とQuest受入れも未完了です。
古いcommitへ戻さず、最新mainの変更を確認してから新しいbranchで作業してください。

今回実装してほしいのは、引き継ぎ計画の「次のPR1」です。
同じ合成profileでの通常Close/Stop後の再起動と、先行する合成Mousepad instanceからの独立を実装し、PRを作成してください。
異常終了と復元データ保護のcaseも計画に従って区別し、未実施項目を合格にしないでください。

特に以下を維持してください。
- 合成profileと、各runのruntime/artifact/owner/Controlを分離する。
- 並行instanceは別worker/別ownership scopeとし、対象Stopは先行側へ影響させない。
- coordinator全体の中断では、自分で作った全workerを最後まで回収する。
- 終了要求とreceipt I/O失敗でも、live owner・signal guard・profile lockを回収完了まで保持する。
- 固定v2の5 sourceとgolden hashを変更しない。
- 実測oracle、receipt validator、CI gate、拒否テストを同じPRで更新する。
- GUI設定2項目、v3/Android通常UI、Questの完了は今回の成果へ含めない。

実プロセスの異常経路と実Mousepad/Waypipeを検証し、既存host/Android CIも確認してください。
実行できなかった検査は理由と未確認範囲を明示してください。
変更根拠は実装箇所や一次情報へのリンクとともに記録し、PRの変更点・検証結果・残作業を日本語で報告してください。
この依頼ではPR作成までとし、マージは行わないでください。
PR1が既に完了していた場合は重複実装せず、その状態を報告してください。
```
