# Quest 3 早期スモーク — Mousepad の実機確認

2026-11-19 JST。**確認手順。本書作成時点では実機未実施。実行結果は別の dated record に残す。**
準備・診断・後処理は Codex が担当し、人は装着時の視認と物理操作に集中する。
最初の装着操作は **約10分を目安**に一巡する。APK build、Linux 側準備、障害調査の時間は含めない。
これは作業時間の目安であり、製品の timeout / lease / cleanup deadline を変更する指示ではない。

**人が読む箇所は §2 の4項目と、中断方法だけでよい。**
Codex は先に [担当手順・次セッション用プロンプト](quest-early-smoke-codex-handoff-20261119.md) を読む。

## 1. 今回の目的と分担

一つの Mousepad で **表示 → 物理入力 → 保存 → 正常終了 → 再起動**を確かめ、
実 Meta SDK / Android / Linux のどの境界に次の作業が必要かを明らかにする。
現在の v3 は単一 GUI app lease である。主窓と、その Dialog / popup を対象にする。

| 担当 | 作業 |
| --- | --- |
| Codex・装着前 | 対象 source / APK / host の照合、対応 package / bundle、通常設定、専用の小文書、観測入口を準備。操作できる直前まで進める |
| 人・装着中 | 文字と窓を見る、物理キーボードとコントローラーを操作する、違和感を短く伝える |
| Codex・操作間 | 固定文書の保存結果、通常 Close / Stop 後の owner ごとの回収を確認。再起動してよい状態を知らせる |
| Codex・装着後 | 通常停止、限定した診断の整理、実行済み / 未実施 / 不足証拠の記録、次の修正候補の提案 |

接続先・鍵の照合や package の準備で止まった場合、装着を依頼しない。
UI 自動操作が使えない環境では、残った設定の数クリックを一度に案内する。
人へ build の待機、ログ解読、ID / SHA の転記、同じ失敗の繰り返し操作を依頼しない。

## 2. 装着して行う4項目

Codex が「準備完了」と伝え、**起動済みか、最後に押すボタンは何か**を一言で案内してから始める。
準備完了は MR 内の表示成功を意味しない。表示は装着した人が確認する。

| 順番・目安 | 人が行う操作 | 確認ポイント |
| --- | --- | --- |
| **S1 表示・入力 / 2分** | 通常ランチャーの Mousepad を起動し、用意した8行文書を通常 Open で開く。起動・Open 済みならその窓を見る。物理キーボードで Ctrl+Home → `quest3x` → Backspace → 左右矢印を一度ずつ操作する | MR 空間に主窓が見える。文字が読める。文書先頭に `quest3` が加わり、削除・カーソル移動が意図した窓へ届く |
| **S2 メニュー・Dialog・保存 / 3分** | コントローラーで View → Line Numbers を操作する。S1 の編集結果を指定された新しい名前へ Save As する | pointer の位置と反応が合う。行番号が見える。Open / Save As の Dialog が操作可能な位置にあり、入力先が切り替わる。保存できたように見える |
| **S3 正常終了・再起動 / 3分** | 保存した状態から通常 Close。Codex の回収確認後、通常 Connect → Inspect → Mousepad の順で起動し、保存文書を開く | 主窓を再び表示・操作できる。保存内容と、選んだ行番号設定が保持されている。今回の再起動は Mousepad の再 Launch を意味する |
| **S4 最終停止・感想 / 2分** | 通常の「停止・切断」を行うか Codex に停止を依頼する。最後に、文字・pointer・Dialog のうち気になったことを一言伝える | 停止操作の見え方を確認する。技術的な回収結果は Codex が別に照合する |

S1 の入力は開いた検証文書だけに行う。別の未保存練習 tab を作らず、
元の8行 fixture は Save As で上書きしない。期待する保存 bytes は、元 bytes の先頭に ASCII `quest3` を加えたもの。
異なる入力になった場合は、その事実を残す。観測結果に合わせて期待 bytes を後から書き換えない。
初期の Line Numbers が ON なら、その状態を先に記録する。設定ファイルを編集して OFF を作らない。
UI の通常操作で最終的に ON にし、その状態を S3 で観察する。
S2 の目視は file bytes の証明ではない。保存 bytes / size / SHA と元文書の不変は Codex が確認する。

**途中で「見えない」「入力できない」「時間切れ」になったら、そこで中断してよい。**
一度伝えたら装着を外せる。Codex は同じ失敗操作の反復を求めず、未実施項目を残し、
利用可能な接続で診断と通常停止を進める。非装着で XR / ADB が止まった場合も、それを記録し、
再装着が不可欠な具体的な一操作があるときだけまとめて依頼する。

返答は例えば次の程度で足りる。これは **書式例であり、今回の観測結果ではない**。

```text
表示=OK / 入力=OK / メニュー=NG / 保存=未実施 / 再起動=未実施
気になった点: pointer の先より少し右を押す必要がある
```

## 3. Codex が装着前後に済ませること

### 装着前

1. 検証対象 commit、APK SHA / version / native build 情報、端末の既存版・署名、
   Linux の対象 user / package / bundle を対応付ける。過去 CI の環境値を実機値としてコピーしない。
2. 専用 Linux user または合意済みの隔離環境で、現行の通常 v3 設定を準備する。
   既存設定、他の作業 owner、pending を保護する。設定や pending の削除で開始可能にしない。
3. 8行の固定文書を起動前に用意し、元 bytes / size / SHA、別名保存先、短い編集内容を記録する。
   ダイアログで選ぶ場所を短く案内できる状態にしておく。
4. bounded な診断の読取可否を先に調べる。取得できない項目も明示し、存在しない operator を呼ばない。
5. 人には準備完了・最後の操作・4項目だけを案内する。準備が未完了なら、未完了点を報告して装着を待ってもらわない。

### 操作間・装着後

- 人が確認した事実と、Codex が source / snapshot / file で確認した事実を分ける。
  ADB screenshot を MR 内の視認や物理入力の証明にしない。
- 保存文書は固定 expected bytes と比較する。単に Save の ACK が返っただけで成功にしない。
- 通常 Close / Stop は、その app / SSH / target owner の終了と pending を照合する。
  通常 Android UI は Runtime 全体を必ず destroy する経路ではないため、
  Host B4 の「Runtime 全体0件」「次の Runtime ID が異なる」を持ち込まない。
- 同じ生存 Runtime の再利用はあり得る。新 Runtime / 新 process の0カウントを、旧 owner の回収証拠にしない。
  画面消失・新 Launch 成功だけでも完全回収を認定しない。
- 次の Launch は通常製品の admission が許可し、旧 owner に回収 pending がないことを確認してから行う。
  必要な照合ができなければ S3 は `blocked` / `unobserved` とし、再起動を強行しない。
- 当日の接続終了後も、Codex が結果表と次の課題を整理する。人の再装着を結果整理の前提にしない。

具体的なコマンドと観測の限界は [Codex 担当手順](quest-early-smoke-codex-handoff-20261119.md) にある。

## 4. 結果を残す書式

以下は新しい実行ごとに、非公開の作業領域へコピーして埋めるテンプレートである。
初期値はすべて `not_run`。未入力を成功扱いしない。正式 typed receipt / oracle schema ではない。

| 項目 | 初期値・記録するもの |
| --- | --- |
| 実行 | 日時・run ID・操作者。`early Quest smoke / non-acceptance` |
| 出所 | 実 source commit、dirty 状態、APK SHA / version / native source、bundle build ID、host fingerprint |
| S1 表示 / 物理入力 | `not_run` / `not_run`。人の実回答と時点 |
| S2 pointer / Dialog / 保存 bytes | `not_run` / `not_run` / `not_run`。人の回答と file 比較を分離 |
| S3 再 Launch / 文書保持 / 行番号保持 | `not_run` / `not_run` / `not_run`。同じ profile/storage を使った根拠 |
| S4 最終 Stop | `not_run`。利用した通常入口と、表示上の結果 |
| 技術的な回収 | 第一 Close 後 / 最終 Stop 後を別記録。owner / pending / 観測範囲・不足 |
| 失敗履歴 | 最初に失敗した操作・reason、cleanup の失敗、再試行の有無を別々に保持 |
| 次に必要なこと | Codex だけで進める作業と、人に必要な最小の再確認を分離 |

結果は `pass` / `fail` / `not_run` / `blocked` / `unobserved` を使う。
`unobserved` は実施後も必要な証拠が取れない状態、`blocked` は前提不足で進めない状態。
人が OK と答えた視認・操作だけを、その対応する観測結果へ反映する。
操作成功と cleanup 成功は別欄にする。外部 rescue が必要ならその事実を明示し、正常回収へ読み替えない。

公開するのは必要な相関値・件数・reason・管理 fixture の画像だけとする。
鍵、接続設定本文、HOME 全体、任意ログ、普段の文書、部屋の一括録画は証跡にしない。
raw 診断は private に保存し、公開要約は内容を確認してから作る。

## 5. この結果から次を決める

| 最初に確認できなかった境界 | 次の作業候補 |
| --- | --- |
| 設定 / Connect / Inspect | 対応 package、bundle/source、host key、Android 保存のどこで止まったかを限定する |
| 起動後の MR 表示 | 実 Surface / SDK Create / Renderer frame / Scene の対応を調べる |
| 物理キー / pointer | 実 focus・座標・入力配送を照合する。合成入力だけの成功で閉じない |
| Dialog / popup | role / parent / target と、MR 配置・入力先を照合する |
| 保存 / 再起動 / 設定保持 | 同じ storage の根拠、file bytes、通常保存と再読、旧 owner の終了を調べる |
| Close / Stop | product reason と実回収を分けて調べる。既知 Issue #45、#50、過去の trace overflow を同一視しない |
| 一巡できた | 観測結果を固定し、B5 / 残りの I6 と I7 の operator 設計へ戻す。最初の実機観測を以後の切り分け基準にする |

スリープ / 通信断 / 強制終了、長時間動作、APK update 互換、日本語 IME、複数アプリ同時利用は
今回の最初の一巡から外す。必要な次回だけを選び、一度に増やさない。

## 6. 基準と正式検証の境界

| 2026-11-19 の基準 | 値 |
| --- | --- |
| マージ済み実装 | [PR #52](https://github.com/tin-machine/eidolon-wayland/pull/52) |
| main の squash merge | `10216ef4bc4544d206a55173d6d1d68e699bbfc6` |
| CI を確認した PR HEAD | `a3c2638bfa672d4e884cef5d4138efe42c5097ef` |
| B4 実 GUI CI の checkout/source | `a23ca2de5ce23c6efe6017114e50c8d74b0e18ca` |
| 上記3 commit の共通 Git tree | `0f64439ebd53db7ea971692c2d44eb4ae1dc33ea` |
| Host の参照結果 | [L7a run 44423014318 / attempt 1](https://github.com/tin-machine/eidolon-wayland/actions/runs/44423014318)、B4 job `135220694663` |

この表は過去の Host 検証の対応であり、Quest でその APK を動かした記録ではない。
実行時は最新 main と source 差分を確認し、実際に build / install した SHA を結果へ残す。
README / 旧 usage の dated な foot / v2 説明だけで v3 の現状を判断しない。

本手順は **manual discovery の未登録 runbook**。新しい I6 case、正式 I7/V12 operator、
H 最終受入れ、Golden レビューを追加・完了するものではない。
実効 theme の未取得と Golden の未承認は残るが、早期スモークの実施を妨げる前提条件にはしない。

```text
I6: 28 families / 87 variants / 533 assertion参照（固定manifestを変更しない）
APPROVED_SHA256=None
reviewed_golden=false
menu_identity_verified=false
verified_n07_assertions=[]
N07: 0/13 verified
host_v3_acceptance_passed=false
```

本 runbook の作成・マージ・CI 成功、または一部の実機操作成功だけで、上記や全体の
`quest_verified` / `production_acceptance_passed` を true にしない。
個別の実機観測は当日の結果表に残し、正式 I6 / I7 / H の判定は各契約で行う。

## 7. 読む順序と実装根拠

- [統合計画 §11.1〜13](../../linux-real-app-v3-integration-plan-20261111.md): 早期 Quest スモークと正式 I7 / H の境界。
- [I5 の実装・検証と引き継ぎ](../../linux-desktop-v3-i5-validation-20261114.md): 通常設定、ランチャー、Stop / Recover、保存 barrier。
- [通常接続設定](../../../android/app/src/main/java/io/github/tinmachine/eidolon/ConnectionSettings.kt) と
  [通常ランチャー](../../../android/app/src/main/java/io/github/tinmachine/eidolon/ApplicationLauncher.kt): 現行 UI と単一 lease。
- [NativeSessionHost](../../../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)、
  [DiagnosticsStore](../../../android/app/src/main/java/io/github/tinmachine/eidolon/DiagnosticsStore.kt)、
  [SpatialPanelAdapter](../../../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt): 操作、観測、所有者ごとの回収。
- [B4 記録](i6-n07-b4-two-sessions.md)、[settings lifecycle v2](i6-n07-settings-lifecycle-v2.md)、
  [I6 残件](i6-remaining-work-20261119.md): Host の成立範囲、過去の失敗、残る正式検証。
