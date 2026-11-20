---
id: linux-desktop-v3-i2-initial-prompt-history
title: I2 initial implementation prompt
type: historical
feature: linux-desktop-v3
created: "2026-11-20"
last_reviewed: "2026-11-20"
---
# I2 初期実装プロンプトの記録

I2 設計作成時の引き継ぎ資料。現在の実装作業はルートの AGENTS.md、現在の PR 状態および I2 の詳細設計を参照する。 [I2 詳細設計](linux-real-app-v3-i2-detailed-design.md)

---

# 付録A：小さいLLMへ渡すI2実装用の初期プロンプト

以下は I2 の着手時に担当者へ渡した文面の記録。PR の進め方は現在の統合計画と利用者の指示を参照する。[現在の実装順序](linux-real-app-v3-integration-plan-20261111.md#4-実装順序と小さな完了単位)

```markdown
# Eidolon Wayland — I2: v3 SSH Controller実装

Eidolon WaylandのI2を実装してください。

リポジトリ:
https://github.com/tin-machine/eidolon-wayland

最終目標は、Quest 3のMR空間で別LinuxホストのGUIアプリを複数の独立したウィンドウとして使用することです。

今回は、Linux Desktop v3の通常SSH controller、Launch mailbox、remote recovery、journal finalizerを実装します。

最新mainから専用branchを作成し、実装・テスト・push・PR作成まで進めてください。
PRは原則一本の大きな機能PRにまとめてください。
マージは禁止です。

## 1. 正本と依存関係

PR-01:
https://github.com/tin-machine/eidolon-wayland/pull/13

主な参照:
- docs/features/linux-desktop-v3/linux-real-app-v3-i2-detailed-design.md
- docs/features/linux-desktop-v3/linux-real-app-v3-pr01-contract-handoff-20261111.md
- docs/features/linux-desktop-v3/linux-real-app-v3-integration-plan-20261111.md

このプロンプトに続けて渡す「I2詳細設計全文」を実装仕様としてください。

詳細設計書がまだリポジトリに存在しなければ、提供された全文を
docs/features/linux-desktop-v3/linux-real-app-v3-i2-detailed-design.md
へ追加してください。設計書が存在すると仮定して読み飛ばさないでください。

設計時の基準:
- main: f894d796a06bcf0fafb1567718cd180ecf29009b
- PR-01 head: ba0a0b9fecc2e8660d3de9bd4bbf611a4bd243bd

これらのSHAへ戻す指示ではありません。
最初に最新main、PR #13の状態とCI、関連PR、AGENTS.md等を確認してください。

実装の正本はPR-01マージ後のmainです。
PR-01が未マージなら依存条件が未成立であることを報告し、
無断マージやPR-01の勝手な取り込みを行わないでください。
その場合でも、最新headとの差分調査と実装準備は進めてください。

## 2. 実装範囲

中心:
- native/transport/src/linux_desktop/v3/runtime.rs
- native/transport/src/linux_desktop/v3/owner.rs
- native/transport/src/linux_desktop/v3/journal.rs
- native/transport/src/linux_desktop/v3/recovery.rs

追加:
- native/transport/src/linux_desktop/v3/reclamation.rs
- native/transport/src/linux_desktop/v3/io_fence.rs
- native/core/src/linux_desktop_v3.rs

接続:
- native/transport/src/session.rs
- native/transport/src/connect_profile.rs
- native/transport/src/linux_desktop/dispatch.rs
- native/model/src/linux_desktop_v3/report.rs
- native/model/src/linux_desktop_v3/presentation.rs
- native/core/src/runtime.rs
- native/core/src/diagnostics.rs

既存B1/B2/C1/C2/E1/I1を再実装しないでください。

## 3. 最初に修正する共有契約

最新mainで修正済みなら、その実装を検証して再利用してください。

1. 正常実行中のReadyではjournal_pending=trueを要求する。
2. relaunch_allowedからruntime全体のshutdown/nativeDestroy条件を外す。
3. RemoteCleanupへremote_cleanedとboot_changedを追加し、既存B2条件へ揃える。
4. Inspect/Catalog/Recoverで架空のNativeOriginを作らない。
5. transport snapshotとcoreの完全なV3Reportを分離する。
6. controller側の失敗をB1 FailureReasonへ無理に追加せず、別のtyped faultを使う。

B1 wire、canonical/hash、golden、C2 receipt、既存journal schemaを変更しないでください。

## 4. 実装するAPI

- v3::runtime::serve()
- Owner::next_launch()
- Owner::command_idle()
- Owner::renew_catalog()
- Owner::begin_stop()
- Controller::inspect_linux_desktop_v3()
- Controller::reserve_linux_desktop_v3_launch()
- Controller::start_linux_desktop_v3()
- Controller::abort_linux_desktop_v3_launch()
- Controller::recover_linux_desktop_v3()
- Controller::take_v3_reclamation()
- Controller::publish_v3_finalized()
- recovery::recover_remote()
- reclamation::finalize_reclaimed()
- IoGenerationFence / PreviousIoClosed
- NativeOrigin::capture()

具体的なsignature、所有権、状態遷移、失敗時動作は詳細設計に従ってください。

## 5. SSH actorの必須条件

認証済みSSH handle、UID、profile、storage、pins、manifest、compiled sourcesを同じ接続世代へcaptureしてください。

Inspect、Catalog、Launch、Stopを同じSSH actorで処理してください。

Launch mailboxは容量1とし、それとは別に一回限りのLaunch gateを持ってください。
local資源準備前にLaunchPermitを予約してください。

Owner::next()を外側のtokio::select!でmailboxと競合させないでください。
mailboxはOwner内部の安全な入力待ち境界に参加させてください。

heartbeat送信中にLaunchが来ても送信futureを中断しないでください。
ACK待ち中のStopは元のdeadlineを維持してラッチしてください。

StartRequestedを永続化してからStartを最大一回だけ試行してください。
enqueue timeoutやACK喪失でも自動再送は禁止です。

## 6. deadlineとlease

詳細設計に従い、送信enqueue時間を含む操作全体の期限を使ってください。

- Prepared 10秒
- enqueue最大2秒
- Inspect 10秒
- Start 12秒
- Heartbeat 5秒
- Stop 8秒
- channel close 3秒
- forward 5秒
- forward取消3秒
- router drain 3秒

heartbeat時計はACK受信時刻ではなく送信開始時刻を基準にしてください。
遅いStart ACK後の期限超過heartbeatは直ちに送信してください。

forward前後のrenew_catalog barrierと、journal更新後のlease余裕再検査を実装してください。

通常GUIの総稼働時間制限を追加しないでください。
Stopの絶対budgetはOwner内部で管理してください。

## 7. Journalと回収

remote process completion、pipes EOF、receipt、remote cleanup、
SSH channel complete、forward retirement、router drain、
local Waypipe、listener、window、SDK binding、Entityを区別してください。

recover_remoteはremote証拠取得とJournal::finishまでを担当し、
local/SDK証拠なしにclearしないでください。

DeferredReclamationはJournalを所有しますが、
SSH handle、Owner、Router、Leaseを持たせないでください。

SSH actorはlocal/SDK回収を待たずに終了可能にしてください。
coreが退役を進め、FS専用の所有jobでfinalize_reclaimedを実行してください。

origin退役の条件にnativeDestroyを含めないでください。
E1保存用のruntime全体barrierは別条件です。

Completeの永続化が成功した場合だけdeferredを発行してください。
finishのfsync失敗後にin-memory stageだけを見て成功させないでください。

clearはLinked / UnlinkedNeedsSync / Clearedで管理してください。
unlink後fsync失敗は、同じticketとlockを保持し、syncだけ再試行してください。
別fileが現れたら削除しないでください。

保存済みCompleteは、新規認証・capture・旧I/O終了証拠を照合し、
再sync後にremote証拠として再利用してください。
その場合queried=falseとし、local/SDK証拠は新たに確認してください。

## 8. I/O fenceとcrash

全製品v3接続世代でstableなio.lockを取得し、
全SSH/forward所有物とTokio runtimeの終了後に解放してください。

lock fileをunlinkしないでください。
CLOEXEC、private mode、owner、type、nlink、inode、directory identityを検査してください。

PreviousIoClosedは、fence取得前に存在した旧pending keyへ束縛してください。
現在世代の新しいjournalへの流用は禁止です。

fenceをremote processやlocal Waypipe/SDKの退役証拠にしないでください。

cold crash後のlocal資源について、実証拠を供給するI3 providerがなければ
remote Completeでもjournalを保持してください。

## 9. I3/I4/I5との境界

I2のtransport Launch/Stopは実fixtureで完成させてください。

通常製品のLaunchは、I3の実local adapterが未接続なら
Launch予約・local割当前にNotReadyで拒否してください。
v2 Session経路やfake originへfallbackしないでください。

I4のSDK ACK/binding/frameやEntity disposalを合成しないでください。
V3Reportはcore reducer一つで組み立ててください。

I5の設定保存には実runtime全回収とnativeDestroyが必要です。
relaunch_allowedを保存barrierの代用にしないでください。

## 10. 実装順序

内部commitは次の順を基本としてください。

1. 型・PR-01契約修正・adapter
2. Owner mailbox・deadline・lease
3. v3 runtime
4. SSH actor・Controller・pending版管理・fence
5. Recover・deferred finalizer・clear再試行
6. 実結合smoke・回帰・引き継ぎ文書

## 11. 必須テスト

詳細設計のT01〜T36とfence検査を実装・実行してください。

特に:
- 正常Inspect→Launch→Stop
- heartbeat/Launch競合
- ACK待ちStop
- Start ACK喪失
- 遅いStart ACK
- forward拒否・不確定ACK
- partial EOF
- Stop中SSH切断
- 実C2 lease timeout
- 重複Launch
- stale Selection
- 旧v2 pending
- v2/v3両pending
- 各journal stageのcrash
- remote Complete/local未回収
- SDK未回収でjournal保持
- Complete再開
- finish/clearのfsync失敗
- cleanup timeout
- shutdown循環待ち防止
- v2/Termux回帰

既存golden、SSH fixture、C2実所有fixture、Waypipe/compositor fixtureを再利用してください。

合成SSH peerの成功を実C2成功と扱わないでください。
実C2 smokeでは通常bootstrapとimmutable bundleを使い、
ForceCommandによる合成helper置換をしないでください。

実Inspect→Catalog→Stopと、
実Waypipeを通したLaunch→Started→Stopを限定smokeとして追加してください。

I6の全製品acceptanceやQuest受入れを今回の成功へ含めないでください。

## 12. CI負荷削減とPR

中間作業はローカルcommitとローカル検証を優先してください。

初回pushとPR作成は、機能完成と必要なローカル検証に近い段階で行ってください。
原則一本の機能PRにまとめてください。

重い実C2/Waypipe smokeは、既存Mousepad fixture jobのbuild成果を再利用してください。
テストskip、CI条件緩和、必須検査削除は禁止です。

最終的に:
- 最新mainとの差分を確認
- ローカル検証
- commit
- push
- PR作成
- CI確認
まで行ってください。

PR本文には、問題、変更後の動作、共有契約修正、テスト結果、
実head SHA、I3/I4/I5に残る範囲を記載してください。

失敗・未実行・skipを成功へまとめないでください。
マージは禁止です。
```
