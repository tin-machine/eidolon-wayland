# 04 — Kotlin SDK adapterと入力lease

[入口](README.md) / [wire](01-contracts.md) / [owner](03-runtime.md)。Kotlinでsolverや保存済みbaseを作らない。SDK操作は既存UI/SDK thread、native command提出は既存Hostのserial経路を通す。

## 1. 新しいファイルの責務

| ファイル | 追加する内容 |
|---|---|
| `WorkspaceLayoutProtocol.kt` | exact snapshot/command DTO。mutable JSONObjectを長期保持せずparse時に値へ変換 |
| `WorkspaceLayoutPresentation.kt` | native reportから Unowned/Pending/Owned を選ぶ。groupと同じデザインだが別owner |
| `WorkspaceLayoutSdkController.kt` | operation/roundごとのquiesce、apply、実probe、listener準備、ACK request相関 |
| `PanelInputLease.kt` | immutable runtime/full target/input epoch/geometry revision/SDK identity |
| `WorkspaceLayoutUi.kt` | 3窓selector、距離/gap、明示Apply/Undo、operation結果表示 |

既存変更は `SpatialPanelAdapter.kt`, `NativeSessionHost.kt`, `InputAdapter.kt`, `ControlPanel.kt`。新fileを置いただけで通常UIへ公開しない。08の統合完了まで入口は無効。

## 2. DTOと所有

```kotlin
// 設計上の追加型。IDはLongへの厳密parseとdecimal wire表現を両方検査する。
data class PanelInputLease(
    val runtime: Long,
    val target: InputWindowKey,
    val inputEpoch: Long,
    val geometryRevision: String?,
    val listenerSerial: Long,
    val entityId: Long,
    val sceneObjectHandle: Long,
)
```

listenerSerialはKotlin SDK ownerのchecked monotonic counter。Serializableな長期保存モデルに入れない。captureしたtarget JSONを後からupdateTargetで別窓へ書き換える方式にしない。Surface replacementでは新InputAdapter/leaseへ交換し、旧instanceをclosedにする。

既存InputTargetへnullable geometryRevisionを加え、sendPointerはticketのfieldをそのまま送る。legacy field省略はnativeの未管理targetだけ受理される。managed entryが見つからないことと「このtargetは未管理」という確定snapshotを区別する。runtime/scene不一致、parse失敗、Ownedだったreportの一時欠落では入力を閉じる。

## 3. SpatialPanelAdapterの適用優先順

```text
retire/destroy/disposal
  > CW operation / CW一時overlay
  > group overlay
  > 通常workspace pose / move / size
```

coreはCWとgroup同時所有を拒否する。SDKで両方Ownedを見たらpriorityで黙って片方を採用せず、CW_INTERNAL_INVARIANTを報告し入力を閉じる。

通常snapshotに `workspace_layout` がない旧runtimeを新adapterが受ける場合は、初期handshakeのschema互換判断でlegacy modeを確定する。新runtimeで報告欄が途中消失した場合はlegacyへ降格しない。

### 3.1 Quiescing

1. runtime/full targetと現在Bindingのhandle・sceneを確認。
2. menuを閉じる。WindowMoveControllerをcancel、SDK grabをcancel、Grabbable=false。
3. controlsの配置/resize/group操作をdisable。Stopを含む全体制御UIは残す。
4. current.inputをsuppressed/closedにしSceneObject listenerを解除。未完のHost input intentionを失効。
5. collisionはprobe用に保持可能。ただしlistener/native gateのどちらも閉じる。
6. native pressed状態と既知SDK input sourceの押下状態がneutralになるまで待つ。
7. `workspace_layout_quiesced`を一度提出。ACCEPTEDまでpending requestを保持。

ACKを送れなければsuccessにしない。現在のBindingがretiringならquiescedを代用せずretirement経路へ。

### 3.2 Applying

全members quiescedのnative reportを見てから各targetのoverlay poseを適用する。`setAbsoluteTransform`を利用。現在のbody sizeは変えないので新Scaleを生成しない。pose適用のためSurfaceをresize/attachし直さない。

writeしたcomponentのreadbackは必要条件だが十分条件ではない。既存 `physicalGeometryMatches` の考え方を使い、次の実ray検査を行う。

- UV=(.05,.05),(.5,.05),(.95,.05),(.05,.5),(.5,.5),(.95,.5),(.05,.95),(.5,.95),(.95,.95) の9点。
- expected pointはcaptureされた本文pose/sizeから計算。
- 各点の法線前後を0.05mずつ伸ばしたsegmentをSDKへ渡す。期待するreceiver/Entity/scene object handle、world hit point、canonical UVを照合。
- bodyのmetric sizeは0.005m以内、UVは0.002以内。SDKが返すgeometryとTransformのposeがPanelPoseCodec.nearを満たすこと。
- 現在構成に存在するcontrols全てのgeometryMatchesを確認。存在しない部品を無条件に4個と決めず、captureした構成で期待個数を固定。
- render targetのframes>0、content_frame>=minimum_frame、presentation_paused=falseを同full targetで確認。

合格後 `workspace_layout_applied` を一度提出。同一stampのpollで毎回新ACKをenqueueしない。stamp=(runtime,operation,revision,target)。該当requestがRejectedになったら同stampで無限再送せずfailureを通知。新roundでは新stampを使う。

### 3.3 Rearming

全geometry_appliedがtrueになったnative reportを受け、現在input_epoch/revisionを固定した新lease/listenerを生成する。listenerはnative gateが開くまでは入力をenqueueしない。

known sourceのheld stateを新しいdownへ変換しない。neutral確認とlistener登録完了後に `workspace_layout_input_ready` を送る。nativeが全target readyからbaseをcommitし、managed.input_allowed=trueを公開した後にだけ配送を開始。

設定値を書いた直後に任意のsleepを挟んでreadyとする方法は禁止。SDK async登録の実completionまたは既存と同等の実owner観測を使う。pending Futureはscene/operation stampへ結合する。

### 3.4 Restoring

同じ手順を、新revisionと新input_epoch、capture.old_poseで繰り返す。成功した2枚だけ先に操作可能にしない。すでに消えたtargetは作り直さず、そのownerのdisposal証拠を処理する。復旧のACKも実rayから得る。

## 4. 旧イベントを新しい入力にしない

callbackの開始でleaseをlocal valへコピーし、それをevent queueの末端まで保持する。

```text
on SDK event(receiver, hit):
  oldLease = this.listenerLease      // immutable instance member
  reject if closed/suppressed
  reject if receiver/sceneObject/entity do not match oldLease
  reject if Host.currentLease != oldLease
  reject if runtime/scene/target/epoch/revision no longer current
  canonicalize UV; validate finite and half-open bounds
  enqueue(packet, oldLease)         // 最新ticketを取得し直さない

on Host worker before JNI:
  repeat current-lease and intention check
  send packet containing captured input_epoch and geometry_revision

in Rust before Wayland:
  repeat full target/epoch/revision/CW/group/focus checks
```

SDKが旧listenerへcallbackした場合、closedで拒否できる。一方、同じSceneObjectに新listenerを登録したとき、SDK内部が古いhitを新listenerへ渡すかは公開APIだけでは断定できない。07の遅延イベント試験を必須にする。

この挙動が不成立なら、通常機能を有効にしない。controller poseを同じSDK update時点で取り直して現在geometryでre-hitできる公開APIが確認できた場合だけその経路を追加試験する。それも使えなければ新scene object identityへの安全な退役・再生成を別実装単位で行う。未検証の「2frame待てば安全」で代用しない。

## 5. すべての入力とfocus

| 経路 | 維持・追加する検査 |
|---|---|
| ray move/down/up | immutable lease、hit receiver、half-open UV、input/geometry epoch |
| drag | down時のsource/window/surface captureを維持。別面hitのUVを流用しない |
| scroll/thumb | 新leaseでは旧held directionを再生しない。fresh down後にarm |
| keyboard | 古いfocus tokenを消し、初版完了後focus=null。新しい明示focusが必要 |
| IME | 開始時activeならBusy。操作中にbackend/transactionを有効化しない |
| FocusWindowControl | theme/control revisionとepochに加えてCW gate |
| debug/synthetic input | 製品の同gateを通す。直接compositor呼出で成功を捏造しない |
| SDK controls | stale target/control revision拒否。menu/dragをquiesceする |

物理keyのrepeatが残る場合、focusなしでは破棄。新leaseへ旧keydownを再生しない。known input sourceはneutralを観測するまでそのsourceをunarmed。新sourceも最初のneutral後にだけarmする。存在しないcontrollerを待って全layoutを無期限停止しない。source数上限は既存pointer source上限を再利用し、無制限mapを追加しない。

## 6. 通常move/resize/theme/groupとの接続

layoutを一度経験したtargetは、以後もnullable geometryRevisionを要求する。Host.inputTargetが動的に新revisionを返すだけではなく、SDK側で新listenerを作る。

通常geometry変更開始→native revision/epoch更新→旧lease閉鎖→既存pose/physical/frame ACK→新lease再arm、という共通handshakeへ寄せる。初版CWの導入で既存resizeが操作不能にならないよう、同targetの通常SetPoseと物理resizeの回帰を必須にする。

groupのgeometry ownerはそのまま維持し、group geometry完了をCW ownerのACKと偽装しない。共通なのはinput leaseの失効・再armだけ。既存のreportに必要なgeometry revisionを付す際は、group側のstable/settling判定を残す。

## 7. UI契約

初版UIは「3枚を自分へ向ける」「元に戻す」。中央と左右のwindowをslot番号等の非機密ラベルで選択し、対象previewを示す。request中はApply/Undoをdisable、Cancelと全体Stopを表示。操作中の描画は見えてよいがクリックは配送しない。

結果文言は `配置済み・保存済み / 配置済み・この接続のみ / 配置済み・保存失敗 / 元の配置へ復旧 / 復旧できず入力停止中` を区別する。ACCEPTEDを「配置済み」と表示しない。

UI timeoutはnative stateを成功・失敗へ上書きしない。UIが破棄されてもcoreのdeadlineとcleanupが進む。元のUI listenerへ戻ってきたcallbackを新Activityへ渡さない。

## 8. JVM/Fakeと実SDK

JVMはDTO、stamp、duplicate ACK、遅着、callback queue、lease更新、UI表示を検査できる。SDK geometry・Surface維持・光学品質はJVM Fakeでは合格にできない。Fake SDKは要求内容と返す順序だけを制御し、テスト結果の名称にもfakeを含める。
