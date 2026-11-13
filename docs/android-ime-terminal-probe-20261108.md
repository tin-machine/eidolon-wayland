---
id: android-ime-terminal-probe
title: Android IME terminal debug probe runbook
type: runbook
feature: android-ime
created: "2026-11-08"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: [android-ime-design, android-ime-implementation-record]
related_sources:
  - scripts/quest-ime-terminal
---
# 端末IMEとCtrl+Spaceのdebug検証

2026-11-08。対象は[NX09のAndroid IME計画](android-ime-wayland-plan-20261107.md)。
専用debug caseを明示的に開始し、実footを対象にAndroid入力接続のON/OFFと直接入力への復帰を観測する。
通常画面へ戻っただけでは有効にならず、case開始後のCtrl+Spaceと専用パネルの切替ボタンが同じ操作を使う。
releaseにはcaseとreceiverを含めない。

Ctrl+SpaceはEidolonの「IMEへ渡す」「Linuxへ直接キーを渡す」を切り替える。
利用するIMEと言語はQuest側で選択する。かな漢字変換・候補・物理キー・HMDの受入れは、この制御試験とは別に確認する。

## 再現手順

以下はBuild151で確認した構成。APKとCLIは同じsourceの組を使い、承認済みQuestのADB serialを
`EIDOLON_QUEST_SERIAL`へ指定しておく。CLIはQuest 3/API 34以上を照合する。
保存先省略時は、実行時の`codex-work-root`設定配下の`eidolon-ime-terminal/`へ記録する。

```bash
IME_NONCE=$(python3 -c 'import secrets; print(secrets.token_hex(16))')

scripts/quest-ime-terminal start --serial "$EIDOLON_QUEST_SERIAL" --nonce "$IME_NONCE"
scripts/quest-ime-terminal focus --serial "$EIDOLON_QUEST_SERIAL" --nonce "$IME_NONCE"
scripts/quest-ime-terminal toggle --serial "$EIDOLON_QUEST_SERIAL" --nonce "$IME_NONCE"
scripts/quest-ime-terminal status --serial "$EIDOLON_QUEST_SERIAL" --nonce "$IME_NONCE"
# statusでBOUNDを確認してからOFFを要求する。
scripts/quest-ime-terminal toggle --serial "$EIDOLON_QUEST_SERIAL" --nonce "$IME_NONCE"
scripts/quest-ime-terminal status --serial "$EIDOLON_QUEST_SERIAL" --nonce "$IME_NONCE"
# Android解除とraw_return=COMPLETEを確認したら終了する。
scripts/quest-ime-terminal stop --serial "$EIDOLON_QUEST_SERIAL" --nonce "$IME_NONCE"
```

`start`のnonceは省略時に生成される。継続操作は同じ32桁小文字hexを使い、次の試行には新しいnonceを使う。
既存の通常起動・保存済み認証・Termux helperを通して端末を準備する。任意shell文字列や入力本文をCLIへ渡す入口はない。
`--artifact-root`で絶対pathの保存先、`--timeout`で1〜60秒の待機期限を指定できる。

初回に外部focus喪失が起き、`start`が`TARGET_UNAVAILABLE`を観測して終了する場合がある。
アプリが可視・XR focusedであることを確認し、明示的な`focus`で入力先を選ぶ。
外部離脱後に元窓を自動で選び直すことはしない。対象窓が不明な複数窓構成では、通常UIで入力先を選んでから実行する。
`focus`はcaseの対象窓へ一度だけ要求し、受付counter増加と新しいfocusの観測がそろうまで待つ。
既に表示されていた`DIRECT_READY`だけで完了しない。

`toggle`のCLI完了は要求の受付を表す。ONは`BOUND`まで、OFFはAndroid解除と直接入力先の復帰まで`status`で確認する。
`stop`はcaseの終了ACKであり、入力・候補・HMDをまとめて合格にする判定ではない。
異常終了時も同nonceの`stop`を要求でき、caseには10分の期限とscene終了時の回収処理がある。

## 状態と失敗の読み方

| 観測 | 意味・次の操作 |
| --- | --- |
| `DIRECT_READY` | 対象端末と専用パネルを準備した。Ctrl+Spaceまたは`toggle`でIME接続を要求できる |
| `BOUND`、editorのView/Window focus・IC activeがtrue | Android editorとnativeの対象を結び付けた。かな漢字変換の成功はまだ別項目 |
| `CLOSING` / `WAITING_CLEANUP` / `WAITING_FOCUS` | IMEの解除または直接入力先への復帰を確認中 |
| `android_released=true`、`raw_return=COMPLETE`、`native_raw_blocked=false` | native cleanupとAndroid解除後、元の対象への新しいfocusを確認した |
| `TARGET_UNAVAILABLE` / `last_external_loss` | 外部離脱等で入力先を失った。状況を確認して明示`focus`を使う |
| `raw_return=CANCELLED` | 対象・scene・入力意図等が変わり、古い対象への復帰を中止した。現在の入力先を確認する |
| `raw_return=FAILED` / `raw_return_failure` | ACK、Android解除、focus復帰を期限内に確認できなかった。gateを閉じたまま記録し、通常のruntime停止・再起動で回復する |

`connection_failure`と`raw_return_failure`は別に読む。通常OFFの`connection_failure=closed`と、
直接入力への復帰失敗である`raw_return_failure=return_timeout`を混同しない。
`editor_imm_active`、`editor_imm_accepting_text`も取得し、Viewのfocusが外れただけでAndroid解除済みとは扱わない。

IME開始時に捕まえたruntime・SDK対象・入力世代を維持し、終了後はnative reset/disableのACK、
Android解除、元対象への新しいfocusを確認してから直接入力を開く。古いcallbackを別窓へ付け替えず、失敗した確定文字を再送しない。

開始のみActivityを通る。継続中の`focus`/`toggle`/`stop`は、`android.permission.DUMP`で保護した
debug専用receiverへ固定actionとnonceだけを送る。NoDisplay Activityへの継続遷移が検証対象の`onPause`を誘発することを避ける。
`status`は保存済みmetadataの読取りだけで、Activityを起動しない。

## Build151の実機結果

記録は作業rootの`eidolon-ime-implementation-20261107/terminal151/20261108T005753/`。
`cycle.json`、`on.json`、`off.json`と各CLI操作の結果を保存した。

- 開始時の`TARGET_UNAVAILABLE`から、明示`focus`で`DIRECT_READY`へ移った。
- ONで`BOUND`、View/Window focus・IC active・IMM active/acceptingがtrue、永久close契約fixtureも成功した。
- OFFで`COMPLETE`、`raw_return=COMPLETE`、`android_released=true`、IMM active/acceptingがfalseとなり、editorを除去した。
- 最後に`STOPPED` / `result=completed`の終了ACKを確認した。

このrunは文字を入力せず、`enqueued/applied=0`で接続・解除・focus復帰の寿命を検査した。
かな漢字変換・候補、通常shellへの実IME文字配送、物理Ctrl+Space、HMDでの視認は次の受入れ項目とする。
詳細な版・hash・失敗と修正は[実装記録](android-ime-implementation-20261107.md)を参照する。

Build150ではnative cleanup後もIMM acceptingが残り、直接入力への復帰が期限切れになった。
Build151でeditorを永久closeし、新ICの再生成を拒否、非text親へfocusを移す処理を追加した。
nativeとAndroid双方の解除条件を維持したまま、同じ制御経路の完了を実測した。

## 実行条件と記録

このrunは開発用の装着模擬と境界一時停止の条件で行った。ユーザー指示により装着模擬を維持しており、
検証終了ごとの自動解除は行わない。[開発時の設定と復元手順](development-automation-and-debugging.md#無人運用とライフサイクル試験)を参照する。
この条件の結果を通常の非装着sleepやpassthroughの受入れへ流用しない。

CLIは固定metadataのfield/type/tokenを検査してから保存する。本文・未確定文字・候補・生入力列は保存せず、
実IME/physical/HMDのacceptanceは`not_verified`を維持する。端末識別子・個別ログ・APK等はprivate作業rootへ保存する。
