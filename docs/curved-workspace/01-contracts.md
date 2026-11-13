---
id: curved-workspace-contracts
title: Curved Workspace identity and wire contracts
type: contract
design_status: proposed
feature: curved-workspace
created: "2026-11-11"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: [curved-workspace-plan]
related_sources:
  - native/model/src/commands.rs
  - native/core/src/workspace.rs
---
# 01 — 型・wire・公開境界

[入口](README.md)。本書のCW専用APIは追加設計。既存APIはREADMEのsource参照を正本とする。

## 1. moduleと責務

`native/model/src/workspace_layout/`を新設し、`mod.rs`（公開型）、`geometry.rs`（計算）、`operation.rs`（純粋遷移）、`wire.rs`（CW payload validator）へ分ける。上位計画の単一workspace_layout.rs候補を、この小さいmodule群で具体化する。core側も `native/core/src/workspace_layout/{mod,capture,owner}.rs` とし、既存runtime.rsへ大きなsolverを直接書かない。

新IDは `LayoutOperationId = CommandSeq`（開始要求のrequest_id）、`GeometryRevision`（runtime単調positive i64をdecimal stringにserializeするnewtype）。operation ID、geometry revision、入力epochは異なる用途で、同一値を仮定しない。geometry revisionは適用、rollback、新target初期化ごとにchecked_addし、overflowは永久blockedにする。

```rust
// 型契約の要約。下記は実装済みコードではない。
struct RoleTarget { role: Role, target: TargetKey }
enum Role { Center, Right, Left, Up, Down, UpLeft, UpRight, DownLeft, DownRight }
enum Mode { Flat, Fan, Arc, Custom }
struct ReferenceFrame { eye_m: [f64; 3], yaw_rad: f64 }
struct LayoutParams { distance_m: f64, gap_deg: f64 }
struct Envelope2d { left: f64, right: f64, bottom: f64, top: f64 }
struct CapturedPanel {
    target: TargetKey, role: Role, slot: usize,
    pose: PanelPose, pose_command: Option<CommandSeq>,
    physical: PhysicalSize, logical: LogicalSize,
    target_px: [u32; 2], content_origin_px: [i32; 2],
    envelope: Envelope2d, controls_revision: u64,
    minimum_frame: FrameSeq,
}
struct Proposal { reference: ReferenceFrame, poses: Vec<(TargetKey, PanelPose)> }
```

同じ値型をRust内で再利用する。Kotlinはwire DTOとSDK変換だけを持ち、solver・永続配置の正本を複製しない。

## 2. 初版の固定範囲

- 同runtime、同session、同sceneの通常Mapped非診断windowが**ちょうど3枚**。全3枚が選択済み。
- roleはcenter/left/rightが各一つ。UIで中央を明示選択し、左右候補を表示して確定する。window titleをidentityにしない。
- global visible panel上限9、slot/lease/Surface予算は現行のまま。3以外は `CW_COUNT_UNSUPPORTED`。後続一般化は別作業。
- 同一windowのfull targetは一つ、applied Create ACK・SDK binding・present frame・pose ACK・physical size ACKが揃う。unmapped/retiring/paused/diagnosticは禁止。
- 既存group、grab、size menu、IME active、他layout、接続切替・cleanup中は禁止。pointer/keyboardの押下中も開始を拒否する。開始後の新押下はgateで拒否し、最後にneutralを再確認。
- physical/logical size、theme/canvas、slotは変更しない。通常content更新は許す。
- referenceは `center` 固定。中央の適用済みposeがyaw-onlyでなければ `CW_REFERENCE_TILTED`。quaternionのq/-qは同一回転。

## 3. 定数（設計値）

| 名前 | 値・意味 |
|---|---|
| DISTANCE_MIN / MAX | 1.0 / 2.0 m。初期UIは1.5m |
| GAP_MIN / MAX | 2.0 / 8.0 deg。初期UIは3.0deg |
| MAX_AZIMUTH | 各Envelopeの全点がreference正面±85°以内 |
| MAX_VERTICAL_SPAN | 一般化時120°。快適性の保証値ではない |
| QUIESCE_MS | 2000 |
| APPLY_MS | 5000 |
| REARM_MS | 2000 |
| RESTORE_MS | 5000 |
| MAX_COMPLETIONS | 8、古いものから削除。現在operationを削除しない |
| MAX_UNDO | 1、runtime内のみ |
| COMMAND_BYTES | 現行16KiBを引き上げない |
| GEOMETRY_F64_EPS | 1e-9、計算中の比較用 |
| POSE_MODEL_TOL | 既存PanelPose::nearの1e-4m / quaternion成分1e-4を維持 |
| SDK_RAY_POS_TOL | 0.005m、PoCで採否する初期検査閾値 |
| SDK_RAY_UV_TOL | 0.002、本文UV。測定誤差をclampして隠さない |

deadlineはRust monotonic clockの `now >= deadline` で失敗。scene可視性喪失で時計を停止して無期限待機しない。各stageに移った時だけdeadlineを一回設定し、ACKや再pollで延長しない。

## 4. 初版command（すべて追加）

共通fieldは `schema:1`, `type`, `request_id:CommandSeq`。runtimeは既存JNI handleから検証し、JSON内のruntime自己申告で選択しない。対象IDは既存TargetKeyの4つのdecimal string。未知/欠落/余剰/重複fieldを拒否する。

### 4.1 開始

```json
{
  "schema":1,"type":"workspace_layout","request_id":"100",
  "expected_revision":null,"controls_revision":0,
  "preset":"radial_three","reference":"center",
  "distance_m":1.5,"gap_deg":3.0,
  "members":[
    {"role":"center","target":{"session":"2","window":"10","scene":"3","generation":"4"}},
    {"role":"right","target":{"session":"2","window":"11","scene":"3","generation":"5"}},
    {"role":"left","target":{"session":"2","window":"12","scene":"3","generation":"6"}}
  ]
}
```

`expected_revision`は現在のlayout reportの最後に公開したgeometry revision（初回null）。操作中に新要求が来ればBusy。同じrequest_idの再送を新operationとして扱わず、既存native request dedupの規則に従う。payloadが同じでもStartを再実行しない。members配列のwire順はcenter/right/left固定。solverに未知roleを渡さない。

### 4.2 lifecycle command

| type | 共通以外のexact field |
|---|---|
| workspace_layout_quiesced | operation, revision, target |
| workspace_layout_applied | operation, revision, target, pose, physical_size, content_frame |
| workspace_layout_input_ready | operation, revision, target, input_epoch |
| workspace_layout_failed | operation, revision, target, reason |
| workspace_layout_undo | expected_operation, expected_revision |
| workspace_layout_cancel | operation, expected_revision |

operation / expected_operationは元の開始request_id。revisionは**ACKが属する現在round**。通常適用とrollbackは異なるrevisionを使う。ackのrequest_idはACK自体の新request_idであり、operationと混同しない。

`workspace_layout_applied.pose`は既存PanelPose、physical_sizeは既存PhysicalSize。content_frameは同targetに実presentしたFrameSeq。毎回最新の内容frameまで追い続けずcapture.minimum_frame以上を要求する。static windowへ新しいpaintを要求しない。

quiescedは単にJSONを受けたというACKではない。04のlistener破棄とcontrol閉鎖を実行したadapterだけが送る。Rust側もnative pressed=0、現在のpause状態、scope一致を確認する。

`workspace_layout_failed.reason`のSDKから許す値は `CW_SDK_APPLY_FAILED / CW_SDK_PROBE_FAILED / CW_SDK_DISPOSAL_FAILED / CW_SDK_CALLBACK_FAILED` だけ。任意の例外本文をprotocolやdiagnosticsへ載せない。

undoはexpected_operationとrevisionが現在のUndoRecordに一致する場合だけ新operationを開始。cancelは未commit operationだけに有効。commit後に到着したcancelはStaleであり、Undoへ読み替えない。

### 4.3 Command parserの変更漏れ防止

`Command` variant追加、parseのschema match、request_id match、payload validation、runtime dispatchを同じ統合単位で揃える。新開始commandを `needs_input_reset()` へ安易に足さない。**開始受理が確定してからownerが一回reset effectを要求**し、不正入力・Busy拒否で既存focusを破壊しない。ACKはresetを要求しない。

nested DTOは `deny_unknown_fields`、object-only、固定enumは文字列のみを受ける。既存Serdeへの委譲で位置arrayやenum objectを受けないか固定raw試験を置く。float fieldは有限numberのみ、bool/null/stringは禁止。IDにnumber/0/負数/先頭0/overflowは禁止。serde_json::Valueで先に重複keyを消す再serializeをしない。厳密検査はCW payloadだけに閉じ、他backendのcanonical JSON契約を再利用してfloatを無理に混ぜない。

## 5. Pointer拡張とnative最終gate

既存 `Pointer` に `geometry_revision: Option<GeometryRevision>` を追加する。省略の互換性は**当該TargetKeyが新layoutに一度も管理されていない場合のみ**。管理済みtargetはnativeのcurrent geometry revisionと完全一致を要求し、missingでもStale。Kotlin InputTargetにもnullable fieldを末尾追加し、旧debug callerの省略は未管理targetに限定する。

Key/FocusWindow/FocusWindowControl/IMEは既存input_epochとfocus/text-input tokenを維持し、CW input gateを追加する。Pointerだけ保護してkeyboard/IMEを通す抜け道を残さない。開始時・rollback開始時・scene/generation喪失時にinput_resetsを一回進める。geometry変更のためにSurfaceGenerationを偽造しない。

管理済みtargetへの通常move/resize/theme/groupによるgeometry変更はUndoを失効し、GeometryRevisionを進め、applied pose/sizeとlistenerの再armまでそのtargetの入力を止める。普通のSetPoseを使ってlegacy Pointerを再度許可することはない。schema1の未管理Panelは従来挙動を維持する。

## 6. snapshot / report

`diagnostics::Snapshot`に `workspace_layout: Report` を追加。新DTOのUI正本はnative snapshot。`Report`のexact fields:

```text
schema=1
revision: GeometryRevision|null
operation: OperationReport|null
managed: [{target,geometry_revision,input_allowed}]
completions: [Completion] (max8)
undo: {operation,revision}|null
error: Reason|null
```

`OperationReport`:

```text
id, revision, phase, kind(apply|undo|return),
source_request, first_failure, cleanup_failure,
input_epoch, members, persistence
```

`members`各行: `role,target,desired_pose,physical_size,minimum_frame,quiesced,geometry_applied,input_ready`。
phaseは `quiescing / applying / rearming / restoring / blocked`。idleはoperation=nullで表す。completedはcompletionsに残す。
persistenceは `not_requested / pending / saved / failed / memory_only / skipped_ambiguous` のいずれか。Completionは `operation,revision,kind,outcome,first_failure,cleanup_failure,persistence`。outcomeは `applied / restored / cancelled / blocked`。

Reportにruntime ownerの期待poseを公開することと、それを適用済みとして扱うことは別。KotlinからReport丸ごとのsuccessをnativeへ送り返す口は作らない。

## 7. 固定ReasonとStatus

| Reason群 | native Status・扱い |
|---|---|
| CW_INVALID_ARGUMENT, CW_COUNT_UNSUPPORTED, CW_REFERENCE_TILTED, CW_NO_FIT | InvalidArgument、変更なし |
| CW_BUSY, CW_GROUP_ACTIVE, CW_INPUT_ACTIVE, CW_IME_ACTIVE | Busy、変更なし |
| CW_STALE_TARGET, CW_STALE_REVISION, CW_STALE_ACK, CW_UNDO_STALE | Stale、現在の操作を過去の結果で進めない |
| CW_NOT_READY, CW_GEOMETRY_UNVERIFIED | NotReady、変更なし/入力停止 |
| CW_QUIESCE_TIMEOUT, CW_APPLY_TIMEOUT, CW_REARM_TIMEOUT, CW_SDK_APPLY_FAILED, CW_SDK_PROBE_FAILED, CW_TARGET_CHANGED, CW_GEOMETRY_CHANGED, CW_VISIBILITY_LOST | first_failureに一度だけ記録、03の復旧へ |
| CW_RESTORE_TIMEOUT, CW_RESTORE_FAILED, CW_SDK_DISPOSAL_FAILED | cleanup_failure、blocked |
| CW_COUNTER_EXHAUSTED, CW_INTERNAL_INVARIANT | blocked、runtime再起動が必要 |
| CW_SAVE_FAILED, CW_STORAGE_INVALID, CW_MIGRATION_REQUIRED | 保存・migration結果。配置成功を消さない |
| CW_CANCELLED, CW_SDK_CALLBACK_FAILED | 前者は明示取消。後者は復旧を要するSDK失敗 |

文字列は一箇所のRust enumからserializeし、Kotlinの表示mapで一致させる。未知reasonは一般成功/初期状態へ変換せずprotocol failure。

## 8. owner public API

```text
model::solve_three(&CapturedPanels, LayoutParams) -> Result<Proposal, Reason>
model::Operation::begin(Capture, Proposal, now) -> Operation
model::Operation::advance(Observation, now) -> Vec<Effect>
core::Controller::start(Ticket, World) -> Status
core::Controller::ack(Ack, World) -> Status
core::Controller::cancel(operation, revision, World) -> Status
core::Controller::undo(expected, World) -> Status
core::Controller::observe(World) -> Vec<Effect>
core::Controller::input_allowed(TargetKey) -> bool
core::Controller::blocks_geometry(TargetKey) -> bool
core::Controller::snapshot() -> Report
```

pure modelはSDK・disk・clock読取りを行わず、World/Observationも実体handleを持たない。core effect executorは既存runtime ownerが一回ずつ処理する。テスト用Fakeに成功を返させた結果をSDK実測とは扱わない。
