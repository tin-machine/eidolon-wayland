# Quest 3 早期スモーク — Codex の準備・観測・引き継ぎ

2026-11-19 JST。**operator 用 runbook / 引き継ぎ文。本書作成時点では実機未実施。結果は別記録とする。**
人向けの操作は [4項目チェックリスト](quest-early-smoke-20261119.md) にある。
文書の作成時点で端末接続、APK 配備、実機操作、人の承認を行ったことにはしない。

## 1. 作業の進め方

**準備が済むまで装着を依頼しない。** 接続先・package・署名・通常設定・診断を先に確認し、
人には最後の数クリックと物理操作だけをまとめて案内する。利用者の時間切れは作業失敗と同一視しない。
残る操作を `not_run` とし、Codex が診断・回収・結果整理を継続する。

最初に AGENTS.md → docs/README.md → [統合計画 §11.1〜13](../../linux-real-app-v3-integration-plan-20261111.md) を読む。
I2/I4 を調べる場合は resolver context を別々に取得し、`proposed` / drift 警告を読む。
このスモークには resolver の task ID がない。I5 / この runbook は manual discovery とする。

Git の未コミット変更、別作業 branch、backup、既存の端末設定・owner は保護する。
旧 worktree の親 metadata が失われていれば、残った files を上書きせず、新しい checkout を使う。
source 修正が必要になったら、実行時の最初の失敗を保存し、修正と再試行を別記録にする。

## 2. 装着前の準備

### 2.1 Source と端末

1. 最新 main、関連 PR、CI、検証する SHA を確認する。#52 の merge は
   `10216ef4bc4544d206a55173d6d1d68e699bbfc6`。以後の差分を未検証のまま取り込まない。
2. 実行環境で利用可能な ADB / UI 制御を確認する。接続先 serial を明示して、boot・給電・APK の版・署名を読む。
   現在の接続を過去の Wi-Fi ADB 記録から推測しない。機器不在なら Linux / build / 手順準備まで進める。
3. 同じ端末の別作業 owner、v2/v3 pending、通常接続設定の有無を本文を出さずに確認する。
   初期化、uninstall、設定消去、pending / lock 削除を通常準備に含めない。
4. native source commit/digest、APK version/hash と固定 source を照合する。
   ソース一致は実機成功を意味しない。CI checkout、PR HEAD、main merge を区別する。

既存入口は [scripts/build-android](../../../scripts/build-android)。以下の変数は、その実行で確認した値を入れる。
空欄・過去の値・説明用 placeholder のまま実行しない。以下は Linux/Bash 上のコマンド形であり、実行結果ではない。

```bash
scripts/build-android "$quest_smoke_version_code"
```

versionCode は導入済み版を確認して選ぶ正整数。build は assemble / lint / JVM / native・同梱検査を行い、
実出力 `APK: ...` と `environment.txt` / `apk.sha256` を保存する。
`EIDOLON_ARTIFACT_ROOT` を使う場合は専用 private 作業領域を指定する。
対象 APK の署名・package・版を照合できた後だけ、通常の更新を行う。

```bash
adb -s "$quest_smoke_serial" install -r "$quest_smoke_apk"
```

署名不一致をデータ消去で回避しない。認証操作など人が必要な場合は、理由と必要な一操作をまとめて伝える。
インストール後も実 APK / native build 情報を再読する。
既存 [quest-check](../../../scripts/quest-check) の source binding 処理は参考にできるが、
旧 foot のケースを v3 Mousepad の検証として起動しない。

### 2.2 Linux host と固定 bundle

対象は実測した対応 Mousepad / GTK3 と、専用 user または合意した隔離環境にする。
普段の desktop の package / 設定を差し替えない。既存 I6 container fixture の account 作成・cleanup 関数を
普段のホストへ流用しない。[A0 の package 前提](../../linux-real-app-a0-foundation-20261109.md) を確認する。

以下は **接続対象 Linux の実非 root user** と、その source checkout で使う入口である。
実 passwd HOME・UID・対応 descriptor・既存 base bundle が必要であり、`HOME` の環境変数変更では代用できない。

```bash
scripts/inspect-linux-real-app \
  --reviewed-package "$quest_smoke_reviewed_package" \
  --artifact-root "$quest_smoke_inspection_root"

scripts/build-linux-desktop-v3-bundle --source-status

scripts/build-linux-desktop-v3-bundle \
  --reviewed-package "$quest_smoke_reviewed_package"

scripts/build-linux-desktop-v3-bundle \
  --print-config --build-id "$quest_smoke_build_id"
```

前の検査に失敗したら後続へ進めない。inspection root は新しい private directory とし、
既存 receipt を上書きしない。reviewed-package はその package の正当な descriptor を確認し、
inspect 成功に合わせて内容を作り変えない。表示上の `Mousepad 0.6.3` だけで対応を認定しない。
build ID は成功した builder の実出力を使う。base bundle がなければ既存の build 手順に戻り、
source / pins が合う状態を作る。[builder](../../../scripts/linux_desktop_v3_build.py) は
既存 base bundle と現在の source bytes を照合する。

export した config は通常の「v3 / Mousepadの設定」へ保存する。
IPv4 / SSH user / host key / bundle の一致を確認し、Android の通常 storage 保存を通す。
任意 storage ID、設定 file 直接編集、debug broadcast による profile 差し替えで開始しない。
private key や config 本文を会話・PR・公開 artifact に出さない。

### 2.3 文書と操作の準備

- app 起動前に、検証用の新しい directory へ固定8行 ASCII 文書を作る。
  元 bytes / size / SHA / 作成時点を記録する。S1 の Ctrl+Home → `quest3x` → Backspace に対応し、
  期待する保存 bytes は元 bytes の先頭に ASCII `quest3` を加えたものとして、操作前に固定する。
- Open で選択できる実在する短い path と、まだ存在しない Save As 先を確認する。
  文書・settings を GUI 実行中に Codex が書き換えて結果を作らない。
- 物理 keyboard と controller が使えるか確認する。合成キーを入れられることを物理入力の成功としない。
- UI 制御が利用可能なら、実在する通常 UI の設定 / Connect / Inspect まで進める。
  Launch / 固定文書の通常 Open も準備できた場合は、その到達点を明示し、二重起動を避ける。
  入力練習用の未保存 tab を追加しない。物理入力の確認は本人の操作を待つ。
- UI 制御が使えないときは、調査を繰り返して人を待たせず、残ったボタン操作を一度に渡す。

## 3. 利用できる観測と、その限界

通常 debug APK の [DiagnosticsStore](../../../android/app/src/main/java/io/github/tinmachine/eidolon/DiagnosticsStore.kt)
は `no_backup/diagnostics/latest.json` を atomic に更新する。実在する bounded read の形は次のとおり。
診断を読める debug build、`run-as`、`timeout` が使える環境であることを先に確認する。

```bash
(
  umask 077
  set -C
  timeout 5s adb -s "$quest_smoke_serial" exec-out \
    run-as io.github.tinmachine.eidolon \
    head -c 1048577 no_backup/diagnostics/latest.json > "$quest_smoke_snapshot"
)
```

`quest_smoke_snapshot` は、その段階用に新しく選んだ private 作業領域内の file path とする。
既存 file の上書きを拒否して stdout を保存し、terminal / 会話へ全文表示しない。
終了値・size を確認し、上限 **1 MiB** 超過を拒否する。追加の1 byteは過大入力の検出用。
JSON の重複 field / 型を検査し、`schema`、`process_id`、`build_info`、正の decimal `sequence` を読む。
PID が現に生存する対象 app の PID であることを照合し、同じ process で sequence の前進を確認する。
process が変われば別 lifecycle として扱う。終了後に残った古い file を現在の正常状態へ読み替えない。

`runtime` は Host の envelope、その中の `runtime.runtime` が native snapshot、
top-level `adapter` が SDK adapter の観測である。利用可能な場面だけ、次を必要十分な範囲で採取する。

| 時点 | 照合するもの |
| --- | --- |
| 設定後 / Inspect 後 | build identity、保存した profile/storage の対応、catalog / request / capture |
| 初回表示 / Dialog 中 | 実 Runtime / session / nonce / owner / full target、SDK binding / frame、操作時点 |
| 第一 Close 後 | 同じ旧 owner の app / SSH / target の退役、pending、firstFailure と cleanupFailure |
| 再 Launch 後 | 同じ profile/storage を使う根拠、新しい起動 identity、保存文書・行番号の観測 |
| 最終 Stop 後 | 同じ owner に対する通常停止、残った pending / 資源、観測できなかった範囲 |

各段階で上限を決めた少数の snapshot を取り、最初の失敗を残す。無期限 polling、全 logcat、
HOME / app-data の一括出力は行わない。観測値が無ければ `unobserved` とする。
ADB screenshot は補助であり、XR panel の視認・物理入力の証明にはしない。

### 回収を誤認しない

[NativeSessionHost.disconnect()](../../../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)
は app Stop と transport disconnect を要求する。通常 UI のこの操作で Android Runtime / Registry 全体が
必ず destroy されるわけではない。生存 Runtime の利用可能な範囲で owner ごとの退役を確認する。
別 process の空 snapshot、別 Runtime の0カウント、画面消失だけでは旧 owner 回収を証明できない。

`stopReceipt()` は process 内 API であり、通常 `latest.json` に完全な typed Stop receipt が
必ず export されるわけではない。不足する証拠を推測で補わず、必要な観測 seam の候補として残す。
新しい Runtime ID を作るための強制再起動も行わない。
必要な旧 owner の照合ができない場合、次の Launch を止めて `blocked` / `unobserved` を記録する。
外部 rescue が必要になったら通常 product cleanup と分け、元の失敗を保持する。

### まだ存在しない自動化を前提にしない

- 統合計画の `scripts/quest-check-linux-real-app` は、この基準では未実装である。
- [quest-observe-normal-linux-gui](../../../scripts/quest-observe-normal-linux-gui) は旧 `normal-linux-gtk` 専用ケース。
- `quest-check --case stop` は旧 foot の停止ケース。v3 Mousepad の成功や回収の証拠へ流用しない。
- UI からの通常操作を自動化できるかは、実行環境の能力を確認する。
  不存在の receiver / flags / SDK API、任意 shell を受ける wire をドキュメントから創設しない。
- 今回の探索で不足が判明したら、小さな観測追加や I7 operator の設計課題として提案する。
  現在の runbook を実装済み operator と呼ばない。

## 4. 中断と結果整理

人が「ここまで」と伝えたら再操作依頼を止め、観測済み範囲を固定する。
Codex は利用可能な通常入口でその試験 owner を停止し、回収結果を別記録にする。
非装着で接続 / XR が停止した場合は、採取できた範囲と残る owner / pending を記録する。
必要な場合にだけ、人へ短い再接続・承認操作をまとめて依頼する。

準備中・操作中の失敗 artifact を上書きせず、再試行は別 run ID とする。
UI reason `APP_WINDOW_UNSUPPORTED` と実回収の結果、Issue #45 の分類問題、
Issue #50 の Dialog 未観測、`I6_TRACE_OVERFLOW` は別に記録する。
一度の成功や再実行成功で既知問題の根本原因を修正済みにしない。

最後は [結果テンプレート](quest-early-smoke-20261119.md) に基づき、次を短く報告する。

1. 人が実際に確認した表示・入力・操作と、Codex の file / snapshot 観測。
2. 最初の失敗、通常回収、観測不能、未実施。新旧 source / APK と再試行の対応。
3. 次の課題を優先順に最大3件。各項目に「Codex だけで進める作業」と「必要な最小の人の再確認」。

raw 診断は private な作業領域へ保存し、Git / PR には必要な公開要約だけを出す。
実機の観測結果を記録しても、正式 N07 / I6 / I7 / H、未承認 Golden の判定は変更しない。

## 5. 次の Codex セッションに渡す初期プロンプト

以下は、利用者が次のセッションで実行を依頼するための文面である。
この文書を読むだけで端末配備や新しい権限が付与されるわけではない。

```text
https://github.com/tin-machine/eidolon-wayland の開発を継続してください。
今回は Quest 3 の早期 Mousepad スモークを進めたいです。
正式 I7 / H Acceptance ではなく、現段階の表示・物理入力・保存・正常終了・再起動を確認し、
次のフェーズで必要な作業を具体化する探索です。

私は装着して確認する時間が限られています。Codex 側で準備・build・診断・記録を進め、
準備が完了してから、約10分を目安とする4項目の操作だけを一度に案内してください。
必要な端末・接続先・権限が不足する場合も、可能な準備を進め、足りない情報をまとめて聞いてください。
私が「ここまで」と伝えたら操作依頼を止め、可能な通常停止と結果整理を続けてください。

AGENTS.md と docs/README.md に従い、以下を読んでください。
- docs/features/linux-desktop-v3/quest-early-smoke-20261119.md
- docs/features/linux-desktop-v3/quest-early-smoke-codex-handoff-20261119.md
- docs/linux-real-app-v3-integration-plan-20261111.md の §11.1〜13
- 関係する I5 / C1 / C2 / I4 の所有・回収契約と現在の実装

#52 は 10216ef4bc4544d206a55173d6d1d68e699bbfc6 へ squash merge 済みです。
開始時に最新 main / PR / CI と実機状態を再確認し、実行する source SHA を固定してください。
PR HEAD a3c2638bfa672d4e884cef5d4138efe42c5097ef と過去 CI source
a23ca2de5ce23c6efe6017114e50c8d74b0e18ca は、main merge とは別 commit です。

対象 Quest と Linux host を確認したうえで、専用の検証環境の準備、APK の build、
同じ署名での通常更新、通常の v3 設定・Connect・Inspect・Launch・Close・Stop、
管理した小文書の作成・保存比較、限定した診断採取まで進めてください。
既存の作業 branch、未コミット変更、通常設定、別 owner、鍵、普段の文書は保護してください。
uninstall / app-data 初期化、pending / lock 強制削除、別作業の kill は行わないでください。
端末操作は実在する通常入口を使い、I6 fixture を実ホストへ流用しないでください。

単一 Mousepad とその Dialog / popup、ASCII 入力を対象にしてください。
UI 操作を代行できるところは準備し、物理入力・MR の視認だけは私の回答を待ってください。
私の回答や実測のない項目を成功にせず、ADB screenshot を HMD 視認の証明にしないでください。
正常 Close / Stop でも Android Runtime が残る場合があるので、Host B4 の全 Runtime 0件や
再起動ごとの別 Runtime ID を新しい条件にしないでください。
旧 owner の回収が確認できないまま次の Launch を強行しないでください。

保存文書の bytes と、初回 Close / 最終 Stop の回収結果を独立に記録してください。
失敗時は first failure と cleanup failure、外部 rescue、未実施・観測不能を区別してください。
存在しない v3 Quest operator や不足する観測は、次の実装課題として残してください。
鍵・HOME 全体・任意ログ・ユーザー文書を公開せず、必要な要約だけを報告してください。

最後に、実行 source / APK / host、確認できた範囲、未確認、回収、次の課題を最大3件に整理してください。
この早期スモークで Golden 承認、N07 0/13、host_v3_acceptance_passed=false、
正式 I6 / I7 / H の状態を変更しないでください。
コード変更が必要なら失敗証跡を残し、実機試行と区別した Draft PR にしてください。
新しい PR は自動マージしないでください。
```
