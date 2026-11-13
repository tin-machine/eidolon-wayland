---
id: spatial-anchor-sa0-design
title: Spatial Anchor SA0 feasibility and evidence design
type: architecture
design_status: proposed
feature: spatial-anchor
created: "2026-11-11"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: []
related_sources:
  - native/core/src/workspace.rs
  - android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt
---
# Eidolon Wayland — Spatial Anchor × Linux 設計・SA0検証計画

Status: **Design / SA0 proposed**  
Date: **2026-11-11**  
Repository baseline: **main 08673e21d610ef9285039bc47d69811b39668ff6**  
Target: **Meta Quest 3 / Meta Horizon OS / Meta Spatial SDK 0.15.0**

> 目的は、Linuxアプリそのものの寿命と、現実空間上の「ここにこの窓がある」という配置の寿命を分離することである。
> Linuxアプリ、SSH、Wayland surface、SDK Entity、Activity、Questの再起動を越えて、空間デスクトップの配置を復元できる構造を目指す。

この文書は設計と段階的な検証計画を固定するためのものであり、Spatial Anchorを製品経路へ有効化する実装ではない。
最初の実装工程 **SA0** は通常workspaceを変更せず、Anchor APIの実機成立性だけを隔離して確認する。

---

## 1. 最終的に実現したい体験

Quest 3のpassthrough MR空間で、次を自然に扱えることを目標とする。

- 現実の壁にGrafanaを配置する。
- 机の上にfootを配置する。
- 右側にeditorや一般Linux GUIアプリを配置する。
- ウィンドウを手で移動・拡縮し、その配置を保存する。
- Questを外し、翌日再び装着しても同じ現実位置へ復元する。
- Linuxアプリ終了、SSH切断、Wayland window消滅後も空間配置は残る。
- 再接続・再起動したLinuxアプリを以前の配置へbindできる。
- 複数のLinuxホストから来たウィンドウを、Quest側の単一MR workspaceで統一して扱う。
- Linuxアプリ自身にはXR固有APIやSpatial Anchor対応を要求しない。

重要な設計原則は次のとおり。

1. **Linux application lifetime と Spatial Placement lifetime を分離する。**
2. **RustをWorkspace/Placementの正本にする。**
3. **Meta SDKのruntime objectはKotlinが所有し、永続identityだけをRustへ返す。**
4. **Anchorが未解決・tracking不明・geometry未確認の間は入力を許可しない。**
5. **既存のnative/SDK/remote回収保証をSpatial Anchorのために弱めない。**

---

## 2. 現行実装の調査結果

### 2.1 調査基準

本設計のコード基準はmain 08673e21d610ef9285039bc47d69811b39668ff6。

主に確認した箇所:

- README.md
- docs/architecture.md
- docs/progress.md
- native/model/src/workspace.rs
- native/core/src/workspace.rs
- native/core/src/window_slots.rs
- native/model/src/surface.rs
- native/model/src/ids.rs
- android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt
- android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt
- android/app/build.gradle.kts
- android/app/src/main/AndroidManifest.xml

rootのAGENTS.mdはこのbaselineでは存在しない。

AndroidはMeta Spatial SDK 0.15.0へpinされている。

### 2.2 現在のCI状態

この文書作成時点のbaseline mainについて、Anchorとは無関係な既存CI状態も記録する。

- workflow 「検証」 run 42854445713
  - android: success
  - host: failure（host suite step）
  - clipboard/IME後段: skipped
  - report: success
- workflow 「L7a foundation」 run 42854445132
  - GIO policy and recovery guard: success
  - Pinned Mousepad and real Waypipe foundation: cancelled

これらをSpatial Anchorの成功・失敗とは扱わない。
Spatial Anchorはbaselineでは未実装・未CI・未Quest実機検証である。

### 2.3 runtime identity

現在のruntime identityには次がある。

~~~text
RuntimeHandle
SessionId
WindowId
SurfaceId
SceneEpoch
SurfaceGeneration
FrameSeq
ContentSeq
...
~~~

SDK/native間の所有対象はTargetKeyで束縛される。

~~~rust
TargetKey {
    session: SessionId,
    window: WindowId,
    scene: SceneEpoch,
    generation: SurfaceGeneration,
}
~~~

TargetKeyはruntime objectのstale callback、旧Surface generation、旧Sceneを拒否するためのidentityであり、永続配置identityではない。

### 2.4 現在のWorkspace保存

native/model/src/workspace.rsは明示的に、

> 保存DTOへsession/window/Surfaceの寿命付きIDを入れない。

という方針を持つ。

現在の永続配置は概ね次の構造である。

~~~rust
SavedPlacement {
    app_id: String,
    slot: usize,
    pose: PanelPose,
    physical_size_m: PhysicalSize,
    logical_size: Option<LogicalSize>,
}
~~~

テストでは保存JSONへsession/window/generationを含めないことも検査済み。

これはSpatial Anchor導入時も維持すべき重要な不変条件である。

### 2.5 同一app_id複数窓の現在の扱い

現在は同一app_idのMapped windowが複数存在する場合、永続配置を誤って別windowへ復元しないため、remember/restoreを意図的に抑止する。

概念的には、

~~~text
matching_windows != 1
    -> saved placementを推測しない
~~~

という安全側の設計である。

Spatial Anchor対応では、この制約を無理に緩めず、後述するPlacementIdとruntime WindowIdの明示bindへ移行する。

### 2.6 SDK Entity / Surface / pose

SpatialPanelAdapterは現在、

- Rust runtimeからcreate/destroy planを受ける。
- SDK EntityをKotlin側で生成する。
- SceneEpochとSurfaceGenerationを含むTargetKeyで所有を照合する。
- SDK SurfaceをRust renderへattachする。
- window move中のSDK absolute transformを取得する。
- Rustへpose変更をrequestする。
- Rustのdesired poseをEntityへ反映する。
- lineSegmentIntersectを使い、実geometry・UV・world hit位置を確認する。
- native detachとSDK disposalを別々の証拠として扱う。

特に、希望したTransform componentを書いただけで「適用済み」とせず、公開ray APIによるgeometry確認後にACKしている点はAnchor導入後も維持する。

---

## 3. 現在のidentityと将来の永続identity

### 3.1 現在の関係

~~~mermaid
flowchart LR
    S[SessionId] --> W[WindowId]
    W --> T[TargetKey]
    E[SceneEpoch] --> T
    G[SurfaceGeneration] --> T
    T --> K[SDK Entity]
    K --> SF[SDK Surface]
    W --> WP[Runtime WindowPlacement]
    WP --> SP[SavedPlacement]
    SP --> AID[app_id + slot]
~~~

現在のSavedPlacementはSessionId/WindowId/SceneEpoch/SurfaceGenerationを保存しない。

### 3.2 Spatial Anchor導入後の関係

将来は永続層をruntime層より上へ追加する。

~~~mermaid
flowchart TD
    WS[WorkspaceId]
    WS --> P[PlacementId]
    P --> AB[AnchorBindingId]
    AB --> AU[Anchor UUID]

    P --> M[WindowMatcher]
    M -. runtime bind .-> W[SessionId + WindowId]

    W --> TK[TargetKey]
    TK --> EN[SDK Entity]
    EN --> SU[SDK Surface]

    AU -. resolve each scene .-> AH[Anchor runtime handle]
    AH --> EN

    style WS stroke-width:3px
    style P stroke-width:3px
    style AB stroke-width:3px
    style AU stroke-width:3px
~~~

**永続化するもの**

- WorkspaceId
- PlacementId
- AnchorBindingId
- Anchor UUID
- Anchor-relative pose
- physical size
- logical size
- window matcher
- visibility/launch policy

**永続化しないもの**

- Anchor runtime handle
- SDK Entity id
- SDK Surface
- RuntimeHandle
- SessionId
- WindowId
- SceneEpoch
- SurfaceGeneration
- input listener/token
- OpenXR XrSpace runtime handle

---

## 4. Meta Spatial Anchorの成立性

### 4.1 確認できた公式仕様

2026-11時点で確認したMeta公式情報では、Spatial Anchorは現実空間へ固定されたworld-lockedな6DoF参照フレームである。

Meta Spatial SDK 0.15.0のAnchor APIは少なくとも次を公開している。

~~~kotlin
data class Anchor(
    var handle: Long,
    var uuid: UUID
)
~~~

- handle: underlying OpenXR spaceのnative runtime handle
- uuid: Anchorの永続識別子

またAnchor.FetchOptionsはUUIDやcomponent typeでdiscoverするための指定を持ち、SpaceComponentTypeにはLocatable、Storable、Sharable等がある。

0.15.0 Anchor API自身がFetchOptionsをscene.discoverSpacesで使用すること、componentをscene.setSpaceComponentStatusで有効化できることを説明している。

OpenXR側の現在のMeta persistence APIは、persistしたspatial entityをsession間だけでなく**device restart / power off後にもretrieval可能**と説明している。
関連content自体は保存されないため、Anchor UUIDとEidolon側のWorkspace/Placementの対応はアプリ自身が保存する必要がある。

### 4.2 Anchor UUIDとruntime handle

設計上、次の区別を厳守する。

~~~text
Anchor UUID
    durable identity
    Rust storeへ保存可

Anchor handle / XrSpace
    current XR sessionのruntime object
    Kotlinのみ保持
    永続化禁止
~~~

これは既存EidolonのTargetKey設計と同じ考え方である。

### 4.3 3mガイドライン

Metaは、anchor対象contentをAnchorからおおむね3m以内へ置くことを推奨している。
Anchorから離れるほど誤差が見かけ上増幅する。

Metaのbest practicesでは、

- 近接した複数contentはparent hierarchyで同じAnchorを共有できる。
- 大きなroomでは中央付近のAnchorを活用できる。
- 独立objectは新しいAnchorを作る。
- Anchorはmoveできない。
- 不要Anchorはerase/destroyする。
- 作成可能数に固定maximumはないと記載されているが、不要Anchorは性能上削除を推奨する。

したがって「1 Workspace = 1 Anchorを常に強制」は本番設計として採用しない。

### 4.4 Anchorをmoveしない

Anchorそのものを恒久移動する操作として扱わない。

恒久再配置は、

~~~text
new anchor create
    -> persist new anchor
    -> Workspace durable commit
    -> binding revision切替
    -> old anchor erase
~~~

とする。

old anchorを先にeraseしてはいけない。

### 4.5 権限

OpenXR Spatial Anchor APIはAndroid manifestのUSE_ANCHOR_APIを前提とする。

baselineのandroid/app/src/main/AndroidManifest.xmlにはUSE_ANCHOR_APIはまだ存在しない。

またMetaはScene/spatial dataへのアクセスについてruntime spatial data permissionを別途説明している。
Horizon OSの権限モデルは変化し得るため、SA0ではmanifest宣言だけでなく実Quest上のpermission stateと実際のerror codeを記録する。

### 4.6 persistence/discoveryの代表的failure

現在のMeta OpenXR API referenceには、persistence/discoveryで少なくとも次のfailure要因が記載されている。

- insufficient resources
- storage at capacity
- insufficient view
- permission insufficient
- rate limited
- environment too dark
- environment too bright
- session/runtime failure

「Anchor UUIDがある = 必ず即時localizeできる」と仮定しない。

---

## 5. 未検証事項

公式仕様で保証される範囲と、Eidolon/Quest実機で確認すべき範囲を分ける。

| 項目 | 現時点の扱い |
| --- | --- |
| sessionを越えたlocal persistence | 公式仕様上成立 |
| device restart / power off後のpersistence | 現在のMeta persistence仕様上成立 |
| UUIDを指定した再発見 | API上成立 |
| Activity recreation後の再resolve | 設計上可能、Eidolon実機未検証 |
| Eidolon process kill後の再resolve | Quest実機未検証 |
| Quest sleep/resume | Quest実機未検証 |
| 部屋の家具配置変更 | localization品質をQuest実機検証 |
| 照明条件変化 | Quest実機検証 |
| tracking lost -> relocalize | Quest実機検証 |
| app data clear | 保証と扱わず実測 |
| uninstall/reinstall | 保証と扱わず実測 |
| signature変更後reinstall | 保証と扱わず実測 |
| 別Questへのlocal Anchor移行 | 対象外。local UUIDの移行を前提にしない |
| anchor大量作成時性能 | 固定maximumなしの記載と性能保証を混同しない |
| SDK 0.15.0の正確なKotlin create/persist呼出し形 | SA0でAAR/compile/runtimeにより確定 |

特にSpatial SDK 0.15.0のcreate/persist操作の正確なKotlin signatureは、古いSpatial SDK referenceやOpenXRの関数名から推測して固定しない。
**SA0実装時に0.15.0 AARと公式referenceを正本としてcompile検証する。**

---

## 6. Rust / Kotlin / JNIの責務分離

### 6.1 推奨責務

#### Rust

Rustを空間desktop modelの正本とする。

- Workspace
- Placement
- AnchorBinding
- placement revision
- WindowMatcher
- WindowIdとのruntime binding
- anchor resolve policy
- visibility policy
- launch policy
- safe fallback policy
- rebind transaction
- durable persistence
- stale result判定
- input admissibilityの最終条件

#### Kotlin

Meta Spatial SDKの実体を所有する。

- Anchor create
- Anchor component enable
- persist
- discover/resolve
- localization状態
- erase
- runtime Anchor handle
- getAnchorPose
- SDK EntityへのTransform適用
- Activity / Scene lifecycle
- permission handling
- SDK Future/callbackの回収
- runtime errorの正規化

#### JNI

状態の正本を持たず、command/result boundaryにする。

- Anchor operation command
- async result
- SceneEpoch
- request id
- binding revision
- pose/result notification

#### Wayland compositor

Anchorを知らなくてよい。

- Linux window lifecycle
- app_id
- resize
- surface tree
- input focus
- buffer lifetime

#### SSH / Waypipe

Anchorを知らなくてよい。

- remote application lifecycle
- drawing/input transport
- remote process ownership
- cleanup/recovery

### 6.2 RustがAnchorを直接管理する案

理論上、OpenXR spatial entity extensionをRustから直接操作することは可能である。

ただしEidolonでは既に、

~~~text
Kotlin
  Activity
  Meta Scene
  SDK Entity
  SDK Surface
  permission

Rust
  model
  compositor
  render
  transport
~~~

というownershipが成立している。

Rust側へAnchor runtime ownershipを追加すると、

- Spatial SDKが所有するXR sessionとの整合
- Activity recreation
- SceneEpoch
- Kotlin SDK callback
- OpenXR extension lifecycle
- thread constraint
- permission flow

を二重管理することになる。

したがって推奨しない。

**Anchorの意味・policyはRust、Meta Anchor APIのruntime操作はKotlin**を基本とする。

---

## 7. 永続データモデル案

### 7.1 新しいID

最低限、次を導入する。

~~~text
WorkspaceId
PlacementId
AnchorBindingId
~~~

これらはruntimeのpositive i64 IDと同じ採番方式に必ずしも揃える必要はない。
永続性・merge・backup・将来のexportを考えるなら、UUID等のcollision-resistant IDも比較対象にする。

SA1で型を固定する。

### 7.2 モデル案

~~~rust
struct Workspace {
    id: WorkspaceId,
    name: String,
    placements: Vec<PlacementRecord>,
    zones: Vec<AnchorZone>,
}

struct PlacementRecord {
    id: PlacementId,
    matcher: WindowMatcher,

    anchor_binding: Option<AnchorBindingId>,
    local_pose: PanelPose,

    physical_size_m: PhysicalSize,
    logical_size: Option<LogicalSize>,

    visibility_policy: VisibilityPolicy,
    launch_policy: LaunchPolicy,
}

struct AnchorBinding {
    id: AnchorBindingId,
    scope: AnchorScope,
    provider: AnchorProvider,

    uuid: AnchorUuid,
    revision: u64,
}

struct WindowMatcher {
    host: Option<RemoteHostId>,
    app_id: String,
    role: Option<String>,
    launch_binding: Option<LaunchBinding>,
}
~~~

これは方向性でありSA0では実装しない。

### 7.3 app_idを永続identityにしない

app_idはmatcherの属性としては有用だが、Placement identityではない。

例えば、

~~~text
Placement A
  app_id = foot
  target = desk

Placement B
  app_id = foot
  target = left wall

Placement C
  app_id = foot
  target = right wall
~~~

を成立させる。

Eidolon自身からlaunchしたwindowは、既存のlaunch request/claim情報とPlacementIdを相関させることで、Wayland window出現後に明示bindする。

### 7.4 multi-host

空間配置はQuest側が所有する。

~~~text
Workspace "Monitoring"

Placement A
  host = quest-termux
  app  = foot

Placement B
  host = home-linux
  app  = browser/grafana

Placement C
  host = work-linux
  app  = editor
~~~

Anchor UUIDやabsolute room poseをremote Linuxへ送る必要はない。

---

## 8. Anchorが見つからない場合のpolicy

### 8.1 禁止するfallback

保存済み「world座標」をそのまま使用して表示しない。

tracking/reference spaceの再構成後に以前のworld座標が同じ現実位置を意味する保証はない。

### 8.2 推奨順序

1. UUIDでAnchor resolveを開始する。
2. localizationを待つ。
3. insufficient viewならユーザーへ「周囲を見回す」案内を出す。
4. dark/bright/permission/rate limit等は原因別に表示・再試行する。
5. 解決中は対象Placementをinteractiveにしない。
6. 解決不能ならRecovery UIへ移す。
7. ユーザーが明示した場合だけ「現在位置へ一時表示」を許可する。
8. 恒久変更は「ここへ再固定」操作で新Anchorを作る。

### 8.3 Bring Here / Return

日常利用向けに次を推奨する。

~~~text
Persistent placement
    wall anchor
        |
        +-- Bring Here
              |
              +-- temporary viewer-relative / current-space transform
              |
              +-- Return
                    |
                    +-- original anchorへ戻る
~~~

Bring HereはAnchorBindingを変更しない。
ユーザーが明示的に「ここを新しい場所として保存」を選んだ場合だけrebind transactionへ進む。

---

## 9. WindowとAnchorの寿命分離

### 9.1 既存回収保証を維持する

native/core/src/window_slots.rsは現在、

- Wayland window
- native render target
- SDK binding
- remote/session cleanup

を独立した証拠として観測する。

Spatial Anchorを既存window leaseの生存条件にしない。

### 9.2 lifetime hierarchy

~~~mermaid
flowchart TD
    A["Workspace / Placement / Anchor UUID<br/>days - months"]
    B["runtime Window binding<br/>minutes - hours"]
    C["SessionId / WindowId<br/>connection lifetime"]
    D["SceneEpoch / Entity / SurfaceGeneration<br/>SDK scene lifetime"]

    A --> B --> C --> D
~~~

上位が残っていても下位は完全に回収できる。

### 9.3 代表的なイベント

#### Linux app終了

~~~text
Wayland window -> Destroyed
SDK Surface -> detach
SDK Entity -> destroy
Window lease -> release

Placement -> KEEP
Anchor UUID -> KEEP
~~~

#### SSH切断

~~~text
remote process/session -> cleanup
WindowId -> invalid
Entity/Surface -> cleanup

Placement -> KEEP
Anchor UUID -> KEEP
~~~

#### Quest sleep / Activity recreation

~~~text
SDK Surface -> release
SDK Entity -> release
Anchor runtime handle -> release
SceneEpoch -> increment

Workspace -> KEEP
Placement -> KEEP
Anchor UUID -> KEEP

new Scene
  -> resolve UUID again
~~~

#### app data delete / uninstall

現時点ではEidolonのWorkspace metadataとsystem-side persisted Anchorの対応がどう残るかを保証しない。
SA0/後続探索試験で実測し、orphan cleanup policyを別途決める。

---

## 10. AnchorとWorkspaceの構成比較

### 案A: WindowごとにAnchor

~~~text
Window A -> Anchor A
Window B -> Anchor B
Window C -> Anchor C
~~~

**長所**

- 独立配置精度が高い。
- 3m guidelineへ自然に従える。
- 1 Anchor lossが他windowへ波及しにくい。
- 壁/机など離れた場所へ自由に置ける。

**短所**

- Anchor数が増える。
- windowの恒久moveごとにrebindが必要。
- create/persist/erase transactionが多い。

### 案B: Workspaceに1 Anchor

~~~text
Workspace Anchor
  +-- Window A relative transform
  +-- Window B relative transform
  +-- Window C relative transform
~~~

**長所**

- persistenceが単純。
- Anchor API呼出しが少ない。
- PoCに適する。

**短所**

- 3m guidelineに弱い。
- 大きな部屋・左右壁・机を同時に扱いにくい。
- 1 Anchor localization failureがWorkspace全体へ影響する。

### 案C: Hybrid / Zone Anchor

~~~text
Workspace "Coding"
  |
  +-- Zone Anchor "desk"
  |     +-- foot
  |     +-- editor
  |
  +-- Zone Anchor "left-wall"
  |     +-- docs
  |
  +-- dedicated Anchor
        +-- Grafana
~~~

**長所**

- 近接windowはrelative transformを共有できる。
- 遠い独立windowは専用Anchorにできる。
- 3m guidelineとAnchor数のバランスが良い。
- 部屋ごとのWorkspaceへ発展しやすい。

**短所**

- Zone membershipとrebind policyが必要。
- modelはA/Bより複雑。

### 推奨

本番は**案C Hybrid / Zone Anchor**。

ただしSA0では一切このmodelを実装せず、Anchor 1個だけでAPI成立性を確認する。

---

## 11. 座標系

### 11.1 基本変換

永続化するのは「tracking worldでのabsolute pose」ではなく、Anchorから見たrelative poseとする。

~~~text
T_world_window =
    T_world_anchor
  * T_anchor_window
~~~

Zoneの場合:

~~~text
T_world_window_i =
    T_world_zone_anchor
  * T_zone_window_i
~~~

### 11.2 Spatial SDK座標

Meta Spatial SDKのScene documentはmetric systemを使用し、座標系を以下として説明している。

- X+: right
- Y+: up
- Z+: forward
- left-handed coordinate system

EidolonのPanelPoseとPanelPoseCodecは現在SDK poseとの変換を担っている。
Anchor導入時は座標系変換を一箇所へ集約し、Rust modelへSDK固有runtime handleやEntity transform objectを持ち込まない。

### 11.3 サイズはAnchorと分離

~~~text
Anchor pose
    |
    +-- window center/orientation
           |
           +-- physical size [m]
                  |
                  +-- Wayland logical size [px]
                         |
                         +-- buffer size [px]
~~~

Anchor resolveでWayland logical sizeやbuffer sizeを変えない。

### 11.4 relocalization時のinput整合性

Anchorが再localizeした直後に入力を解禁しない。

~~~text
Anchor localized
    -> calculate desired world pose
    -> apply Entity transform
    -> verify actual transform
    -> verify physical geometry with ray
    -> verify current rendered target/frame
    -> enable input
~~~

既存SpatialPanelAdapterのlineSegmentIntersectによるgeometry確認を利用する。

これにより「表示位置だけ新しく、hit-testは旧位置」という中間状態をinteractiveにしない。

---

## 12. Anchor resolveとWindow restoreのsequence

~~~mermaid
sequenceDiagram
    participant Store as Rust Workspace Store
    participant Core as Rust Workspace Core
    participant JNI
    participant Anchor as Kotlin SpatialAnchorAdapter
    participant Meta as Meta Spatial SDK
    participant Panel as SpatialPanelAdapter

    Store->>Core: Workspace + Placement + Anchor UUID
    Core->>JNI: Resolve(binding, uuid, scene_epoch, revision)
    JNI->>Anchor: resolve request
    Anchor->>Meta: discover UUID

    alt anchor discovered and localized
        Meta-->>Anchor: runtime Anchor
        Anchor->>Meta: get anchor pose
        Meta-->>Anchor: T_world_anchor
        Anchor-->>JNI: AnchorResolved
        JNI-->>Core: uuid + resolution epoch + pose
        Core->>Core: T_world_window = T_world_anchor * T_anchor_window
        Core-->>Panel: desired resolved pose
        Panel->>Panel: apply Entity Transform
        Panel->>Panel: geometry/ray/frame verification
        Panel-->>Core: pose/geometry ACK
        Core->>Core: Placement becomes Interactive
    else insufficient view
        Meta-->>Anchor: localization/discovery failure
        Anchor-->>Core: Relocalizing / InsufficientView
        Core-->>Panel: hidden or non-interactive
    else unavailable
        Meta-->>Anchor: not found / permission / environment error
        Anchor-->>Core: AnchorUnavailable
        Core-->>Panel: keep non-interactive
    end
~~~

---

## 13. Anchor状態遷移

~~~mermaid
stateDiagram-v2
    [*] --> Persisted

    Persisted --> Resolving: new Scene
    Resolving --> Localized: UUID discovered + locatable
    Resolving --> Relocalizing: insufficient view / temporary tracking issue
    Relocalizing --> Localized: environment recognized
    Relocalizing --> Unavailable: timeout / permanent failure

    Localized --> TrackingLost: tracking invalid
    TrackingLost --> Localized: relocalized
    TrackingLost --> Unavailable: recovery failed

    Localized --> Rebinding: explicit permanent move
    Rebinding --> Localized: new anchor persisted + durable commit

    Persisted --> Erasing: Placement deleted
    Erasing --> [*]
~~~

---

## 14. Window状態遷移

~~~mermaid
stateDiagram-v2
    [*] --> Absent

    Absent --> Matched: Wayland window appears
    Matched --> AwaitingAnchor: Placement binding selected
    AwaitingAnchor --> Visible: anchor + geometry + frame verified
    Visible --> AwaitingAnchor: anchor tracking lost
    Visible --> Retiring: Wayland window disappears
    Retiring --> Absent: native + SDK reclaim complete

    Absent --> Matched: later reconnect / relaunch
~~~

重要なのは、WindowがAbsentでもAnchorBindingとPlacementがPersistedであり続けることである。

---

## 15. permanent moveのcrash-safe transaction

Anchorはmoveできないため、恒久再配置はtransactionとして扱う。

推奨順序:

~~~mermaid
sequenceDiagram
    participant UI as User Move
    participant Core as Rust Workspace
    participant KA as Kotlin AnchorAdapter
    participant Meta as Meta SDK
    participant Store as Durable Store

    UI->>Core: commit permanent placement
    Core->>KA: create new anchor
    KA->>Meta: create + make storable/persist
    Meta-->>KA: new UUID persisted
    KA-->>Core: new UUID
    Core->>Store: write new binding revision + fsync
    Store-->>Core: durable
    Core->>Core: switch active binding
    Core->>KA: erase old UUID
    KA->>Meta: erase old anchor
~~~

failure時の原則:

- old valid + new orphan は回復可能。
- old erased + new未commit は配置喪失につながるため禁止。
- erase失敗は新binding成功を巻き戻さず、orphan cleanup pendingとして記録できる。
- duplicate/late callbackはbinding revisionとSceneEpochで拒否する。

---

## 16. JNI command/result案

SA0ではRustへ接続しないが、後続の境界案を固定しておく。

### Rust -> Kotlin

~~~text
AnchorCreate {
    request_id,
    binding_id,
    scene_epoch,
    binding_revision,
    world_pose
}

AnchorResolve {
    request_id,
    binding_id,
    uuid,
    scene_epoch,
    binding_revision
}

AnchorErase {
    request_id,
    binding_id,
    uuid,
    binding_revision
}

AnchorReleaseRuntime {
    request_id,
    binding_id,
    scene_epoch
}
~~~

### Kotlin -> Rust

~~~text
AnchorCreated {
    request_id,
    binding_id,
    uuid,
    scene_epoch,
    binding_revision
}

AnchorResolved {
    request_id,
    binding_id,
    uuid,
    scene_epoch,
    binding_revision,
    resolution_epoch,
    world_from_anchor
}

AnchorUnavailable {
    request_id,
    binding_id,
    uuid,
    scene_epoch,
    binding_revision,
    reason
}

AnchorTrackingChanged {
    binding_id,
    scene_epoch,
    binding_revision,
    resolution_epoch,
    state
}

AnchorErased {
    request_id,
    binding_id,
    uuid,
    binding_revision
}
~~~

Anchor runtime handleはJNI payloadへ出さないことを第一候補とする。
必要になった場合もopaque runtime tokenとしてKotlin ownershipを維持する。

---

## 17. stale callback / concurrency policy

Anchor APIは非同期であるため、既存のSurface/pose callbackと同様にstale completionを前提とする。

結果受理条件の候補:

~~~text
runtime handle matches
AND SceneEpoch matches
AND AnchorBindingId matches
AND binding revision matches
AND request_id is current
AND operation state expects this completion
~~~

Activity recreation前のFutureが後から成功しても、新Sceneへ適用しない。

erase completion、resolve completion、tracking updateの順序が逆転しても、現在revisionを越えて状態を巻き戻さない。

---

## 18. セキュリティ・privacy・安全性

### 18.1 remote Linuxへ空間情報を送らない

通常のLinux application/SSH peerに次を送る必要はない。

- Anchor UUID
- room pose
- absolute tracking pose
- room layout
- physical-space history

remote側へ必要なのは従来どおりWindow/Wayland/resize/input情報だけとする。

### 18.2 diagnostics

Anchor UUIDやphysical-space poseはユーザーの空間を示すデータとして保守的に扱う。

通常diagnosticsでは第一候補として、

- binding id
- UUIDのhashまたはredacted form
- state
- error category
- latency
- SceneEpoch
- revision

だけを保存し、absolute room poseは保存しない。

SA0の明示的debug artifactでは検証に必要なpose差分を保存してよいが、通常product diagnosticsへ自動昇格させない。

### 18.3 input safety

Anchor stateが次の場合、対象Windowをinteractiveにしない。

- Resolving
- Relocalizing
- TrackingLost
- Unavailable
- stale SceneEpoch
- geometry unverified
- rendered target unverified

誤位置のwindowがpointer/keyboard focusを奪うことを防ぐ。

### 18.4 recovery UI

自動的に別場所へ恒久再配置しない。

ユーザーへ、

- 再localizeを待つ
- 周囲を見回す
- 今だけ手元へ表示
- 元のAnchorへ戻る
- ここへ再固定
- Placementを削除

を区別して提示する。

---

## 19. 生産性向け機能

### 19.1 Named Workspace

例:

- Coding
- Monitoring
- Incident
- Reading

Workspace切替はAnchorをerase/createし直す操作ではなく、既存resolve stateとvisibility policyを利用する。

### 19.2 部屋ごとのWorkspace

Workspaceへroom identityを直接Meta依存で固定するかは後続検討とする。
最初はユーザーが明示的にworkspaceを選択し、Anchor resolve結果から利用可否を判断する方が単純で安全。

### 19.3 Restore layout without auto launch

推奨する既定モード:

~~~text
Restore spatial layout: yes
Start Linux applications automatically: no
~~~

空間上の「置き場所」は復元するが、SSH接続やアプリ起動はユーザー操作まで行わない。

### 19.4 Bring Here / Return

Anchorを変更せず、現在だけ手元へwindowを呼び出す。
毎日使うdesktopでは特に効果が大きいと考える。

### 19.5 Show All / Hide All

Anchor/Placementを維持したままSDK Entityだけ表示・非表示にする。

Hide WorkspaceとStop Applicationsを別操作にする。

### 19.6 Anchor health

低コストなUIとして、

~~~text
● stable
◐ relocalizing
○ unavailable
~~~

程度のWorkspace/Placement healthを表示できる。

通常は邪魔にならないよう、問題があるPlacementだけ表示する。

---

## 20. 既存コードへの将来変更候補

| 場所 | 変更候補 |
| --- | --- |
| native/model/src/ids.rs | WorkspaceId / PlacementId / AnchorBindingId |
| native/model/src/workspace.rs | Spatial Workspace durable schema |
| native/core/src/workspace.rs | Placement bind / resolve / restore policy |
| native/core/src/window_slots.rs | 基本変更なし。Anchor lifetimeをleaseへ結合しない |
| native Android JNI boundary | Anchor async command/result |
| SpatialPanelAdapter.kt | resolved pose適用、input gate、geometry ACK |
| 新規 SpatialAnchorAdapter.kt | Meta Anchor API隔離 |
| NativeSessionHost.kt | SceneEpoch/lifecycle/result routing |
| AndroidManifest.xml | SA0でUSE_ANCHOR_API等必要permissionを隔離追加 |
| Quest debug checks | persistence/relocalization/reboot evidence |
| diagnostics | Anchor state/error、redacted identity |

---

## 21. 段階ロードマップ

### SA0 — Anchor API feasibility probe

**今回最初に実装すべき範囲。**

通常Workspaceを1行も変更しない。
Linux window、SSH、Waypipeとも接続しない。

検証するもの:

1. Anchorを現在のQuest空間へ1個作る。
2. runtimeからUUIDを取得する。
3. Storable/persistenceを成立させる。
4. UUIDをdebug専用storageへ保存する。
5. runtime Anchor objectを破棄またはScene/Activityを再生成する。
6. UUIDからAnchorを再発見する。
7. localized poseを取得する。
8. creation時poseとの差と状態を記録する。
9. app process/Activity境界を越えて再発見する。
10. Quest reboot後に同じUUIDを再発見する。
11. 可能ならpower off/onも別caseで確認する。
12. eraseする。
13. erase後に同UUIDを再発見できないことを確認する。
14. debug resourceが全て回収されたことを確認する。

SA0はWorkspace schemaを変更しない。

### SA1 — Durable identity model

- WorkspaceId
- PlacementId
- AnchorBindingId
- Anchor UUID型
- binding revision
- state machine
- serialization
- stale callback rules

Fake backendだけでhost test可能にする。

### SA2 — Diagnostic panel restore

Linux appではなく固定diagnostic panel一枚をAnchorへbind。

- Activity recreation
- SceneEpoch change
- resolve
- geometry verify
- input gate

を確認する。

### SA3 — foot一枚

実Wayland window一枚へPlacementIdをbind。

- Linux app exit
- SSH disconnect
- reconnect
- relaunch
- same Anchor restore

を確認する。

### SA4 — permanent move / rebind

- drag
- new Anchor creation
- durable commit
- old Anchor erase
- crash points
- orphan cleanup

を実装する。

### SA5 — same app_id multiple windows

app_id推測ではなくPlacementIdとlaunch correlationでbindする。

### SA6 — Hybrid / Zone Workspace

- desk zone
- wall zone
- dedicated anchor
- named layouts
- Bring Here / Return
- Show/Hide Workspace

### SA7 — multi-host

Termux + remote Linux複数hostを同一Workspaceへbindする。

---

## 22. SA0の詳細スコープ

### 22.1 SA0の目的

SA0は「Spatial AnchorがAPI上存在する」ことを確認する試験ではない。

次の実用上重要なlifecycleをQuest 3実機で証明する。

~~~text
create
  -> persist
  -> save UUID
  -> Activity/runtime boundary
  -> discover
  -> localize
  -> Quest reboot
  -> discover
  -> localize
  -> erase
  -> prove absent
~~~

### 22.2 SA0で追加してよいもの

第一候補:

- android/app/src/main/AndroidManifest.xml
  - Anchor APIに必要なpermission
- 新規 SpatialAnchorAdapter.kt
  - debug専用でもよい
- debug/instrumentation専用Anchor controller
- noBackupFilesDirまたは専用debug storageのUUID record
- quest-checkの新しいisolated case
- JUnit/Fake unit test
- debug diagnostics fields
- SA0実機手順文書

### 22.3 SA0で変更しないもの

明示的にscope外:

- native/model/src/workspace.rs
- native/core/src/workspace.rs の製品配置model
- SavedPlacement schema
- current placement restore semantics
- WindowIdとのbinding
- window_slots lease semantics
- normal Linux launcher
- SSH
- Waypipe
- normal app auto launch
- multi-anchor
- Hybrid/Zone model
- permanent window reanchor
- product UI
- production default settings
- same app_id ambiguous policy

必要なdebug hookは既存product stateを変えない形にする。

### 22.4 SA0のstorage

製品PreferencesへUUIDを保存しない。

候補:

~~~text
noBackupFilesDir/
  spatial-anchor-sa0/
    state.json
~~~

stateの例:

~~~json
{
  "schema": 1,
  "build": "...",
  "case_id": "...",
  "anchor_uuid": "...",
  "phase": "persisted",
  "created_at_elapsed": "...",
  "expected_next": "rediscover_after_restart"
}
~~~

SA0 artifactとして必要ならUUIDを保持する。
通常diagnosticsへの露出は別途redactionする。

### 22.5 SA0の中断耐性

Quest rebootを跨ぐため、1回のinstrumentation processだけで完結させない。

caseをphase化する。

~~~text
prepare
  create/persist UUID
  save durable SA0 state
  request operator reboot boundary

resume
  read saved UUID
  discover/localize
  record pose/state

erase
  erase persisted anchor
  verify discovery no longer returns it
  delete SA0 state
~~~

reboot自体をADBから自動実行するかは実機環境に合わせる。
自動rebootを採用する場合でも、adb reconnectを「Anchor成功」と同一視しない。

### 22.6 SA0で記録する証拠

最低限:

- Eidolon build/source commit
- Spatial SDK version
- Quest model
- Horizon OS/build version
- permission state
- case id
- operation
- UUID
- SDK/OpenXR error category
- operation start/end elapsed time
- activity/process identity
- SceneEpoch相当のdebug epoch
- anchor discovered count
- locatable/storable status
- pose
- creation -> rediscovery pose delta
- reboot boundary marker
- erase result
- post-erase discovery result

HMD内の「同じ場所に見える」は最後に手動観測として別記録する。

### 22.7 SA0 failure taxonomy

最低限、次を別々に保存する。

~~~text
PERMISSION_DENIED
INSUFFICIENT_VIEW
TOO_DARK
TOO_BRIGHT
RATE_LIMITED
STORAGE_AT_CAPACITY
INSUFFICIENT_RESOURCES
NOT_FOUND
NOT_LOCALIZED
SESSION_NOT_RUNNING
SDK_FAILURE
TIMEOUT
STALE_SCENE
UNKNOWN
~~~

SDK 0.15.0が返す実errorを確認した後にmappingを確定する。
文字列一致だけを製品protocol契約にはしない。

---

## 23. SA0のsequence

~~~mermaid
sequenceDiagram
    participant Test as SA0 Controller
    participant Adapter as SpatialAnchorAdapter
    participant Meta as Meta Spatial SDK
    participant Disk as SA0 Durable State
    participant OS as Quest / Horizon OS

    Test->>Adapter: create at test pose
    Adapter->>Meta: create anchor
    Meta-->>Adapter: runtime anchor + UUID
    Adapter->>Meta: enable persistence/storable
    Meta-->>Adapter: persisted
    Adapter-->>Test: UUID + pose
    Test->>Disk: fsync UUID + phase=persisted

    Test->>OS: Activity/process boundary
    OS-->>Test: new runtime
    Test->>Disk: read UUID
    Test->>Adapter: discover UUID
    Adapter->>Meta: discover
    Meta-->>Adapter: runtime anchor
    Adapter->>Meta: locate
    Meta-->>Adapter: localized pose
    Adapter-->>Test: resolved
    Test->>Disk: phase=pre_reboot_verified

    Note over OS: Quest reboot / power cycle

    Test->>Disk: read UUID after reboot
    Test->>Adapter: discover UUID
    Adapter->>Meta: discover + locate
    Meta-->>Adapter: same UUID + localized pose
    Adapter-->>Test: resolved after reboot
    Test->>Disk: phase=reboot_verified

    Test->>Adapter: erase UUID
    Adapter->>Meta: erase persisted anchor
    Meta-->>Adapter: erased
    Test->>Adapter: discover UUID again
    Adapter->>Meta: discover
    Meta-->>Adapter: absent
    Adapter-->>Test: not found
    Test->>Disk: remove SA0 state
~~~

---

## 24. 自動テスト計画

### 24.1 host / Rust後続

SA0はRust Workspaceへ触れない。
SA1以降で次をhost CIへ追加する。

- runtime IDを永続serializationしない。
- Anchor runtime handleを永続serializationしない。
- Anchor UUIDだけがAnchorBindingへ入る。
- stale SceneEpoch resultを拒否。
- stale binding revisionを拒否。
- duplicate resultを拒否。
- erase後のlate resolveを拒否。
- unresolved Anchorではinput admissibleにならない。
- TrackingLostではinput admissibleにならない。
- geometry ACK前はinput admissibleにならない。
- Window消滅後もPlacement/AnchorBindingを保持。
- Anchor unavailableでもWindow leaseを回収できる。
- same app_id複数Placement。
- host違いsame app_id。
- rebind transactionの各crash point。
- old Anchor erase失敗。
- orphan cleanup pending。
- Bring Hereがpersistent bindingを変更しない。

### 24.2 Kotlin JVM/Fake backend

SpatialAnchorAdapterのbackend boundaryを薄く作り、Fakeで次をdeterministicにする。

- create success/failure
- persistence success/failure
- discover success/not found
- delayed completion
- completion after Scene recreation
- tracking lost
- relocalized
- insufficient view
- permission denied
- rate limit
- erase failure
- duplicate callback

Meta SDK runtimeそのものの成功をJVM fake successへ読み替えない。

---

## 25. Quest実機の受入条件

### SA0必須

- [ ] Anchorを1個作成できる。
- [ ] UUIDを取得できる。
- [ ] persist成功を確認できる。
- [ ] UUIDをdurable SA0 stateへ保存できる。
- [ ] Activity recreation後に再発見できる。
- [ ] app process境界後に再発見できる。
- [ ] Quest reboot後に同UUIDを再発見できる。
- [ ] reboot後にlocalizeできる。
- [ ] pose deltaを記録できる。
- [ ] eraseできる。
- [ ] erase後に同UUIDがdiscoverされないことを確認できる。
- [ ] case終了後にSA0専用state/resourceを回収できる。
- [ ] failureを成功へ読み替えない。

### SA0追加探索

- [ ] sleep/resume
- [ ] power off/on
- [ ] 照明を暗くする
- [ ] 別方向を向いた状態から再発見
- [ ] 周囲を見回してinsufficient viewから復帰
- [ ] 家具の小変更
- [ ] 家具の大変更
- [ ] app data clear
- [ ] same-signature reinstall
- [ ] different-signature reinstall

追加探索は仕様保証と混同せず、「そのQuest/Horizon OS/buildでの実測」として記録する。

### 本実装後の受入

- [ ] SSH disconnect/reconnectでPlacement保持。
- [ ] Linux app exit/relaunchでPlacement保持。
- [ ] Surface generation replacementでPlacement保持。
- [ ] 2 windows独立restore。
- [ ] same app_id 2 windows独立restore。
- [ ] remote host違いのwindowを同じWorkspaceへ表示。
- [ ] anchor loss中はinput不可。
- [ ] relocalize後、geometry/frame検証後にinput復帰。
- [ ] permanent move後のQuest rebootで新位置。
- [ ] old Anchor cleanup。
- [ ] Bring Here -> Return。
- [ ] Hide WorkspaceとStop Applicationsの意味が分離している。

---

## 26. 最初のPRの具体的なスコープ

最初のPR名候補:

> **SA0: Meta Spatial Anchor lifecycle probe**

### 目的

Meta Spatial SDK 0.15.0 + Quest 3で、Eidolonが依存する最小Anchor lifecycleを、製品Workspaceから完全に隔離した状態で実測する。

### PRへ含める

- USE_ANCHOR_API等、実際に0.15.0/Questで必要と確認したpermission。
- SpatialAnchorAdapter.kt。
- debug/instrumentation専用入口。
- Anchor 1個のcreate。
- UUID取得。
- storable/persist。
- UUID durable save。
- Activity/process境界後のdiscover/localize。
- Quest rebootを跨ぐresume case。
- erase。
- post-erase not-found確認。
- error taxonomy。
- raw evidence artifact。
- JVM/Fake unit test。
- Quest用runbook/check入口。
- resource cleanup。
- 実測結果document。

### PRへ含めない

- SavedPlacement変更。
- Workspace schema変更。
- PlacementId。
- AnchorBindingId。
- WindowId binding。
- Linux app。
- SSH。
- Waypipe。
- launcher。
- auto restore。
- normal UI。
- permanent move。
- multi-anchor。
- Hybrid/Zone。
- product settings migration。

### SA0の合格条件

単にcreate APIがsuccessを返すことではない。

**create -> persist -> durable UUID -> Activity/process boundary -> discover/localize -> Quest reboot -> discover/localize -> erase -> absent**

までが同一UUIDの証拠付きで成立すること。

この条件が成立してからSA1でRust modelを固定する。

---

## 27. 推奨アーキテクチャ最終像

~~~mermaid
flowchart TB
    subgraph Rust["Rust — source of truth"]
        WS[Workspace]
        PL[Placement]
        AB[AnchorBinding]
        WM[WindowMatcher]
        RB[Runtime Window Binding]
        WS --> PL
        PL --> AB
        PL --> WM
        WM --> RB
    end

    subgraph Kotlin["Kotlin — Meta runtime adapter"]
        AA[SpatialAnchorAdapter]
        PA[SpatialPanelAdapter]
        EN[SDK Entity]
        SF[SDK Surface]
        AA --> PA
        PA --> EN
        EN --> SF
    end

    subgraph Linux["Linux side"]
        WP[Waypipe]
        APP[Existing Wayland App]
        APP --> WP
    end

    AB -->|UUID / command| AA
    AA -->|resolved pose / state| AB
    RB --> PA
    SF -->|render target| WP

    subgraph Hosts["window suppliers"]
        T[Quest Termux]
        H1[Linux Host A]
        H2[Linux Host B]
    end

    T --> APP
    H1 --> APP
    H2 --> APP
~~~

設計上の重要点:

- Quest側WorkspaceがMR空間を所有する。
- Linux hostはwindowを供給する。
- Meta Anchor UUIDはremoteへ渡さない。
- Anchor runtime handleはKotlinを出ない。
- WindowIdはPlacementの永続identityにならない。
- SDK Entity/Surfaceが消えてもPlacement/Anchor UUIDは残る。
- Anchorが消えても既存window/resource回収は完了できる。

---

## 28. 保存schemaの導入方針

現行Preferences schema=1へ最初からSpatial Workspaceを詰め込まないことを推奨する。

初期は別storeとし、例えば、

~~~text
workspace-v2.json
~~~

または専用repositoryへ、

~~~json
{
  "schema": 1,
  "workspaces": []
}
~~~

として導入する。

理由:

- 現行SavedPlacementの安全なfallbackを壊さない。
- SA0/SA1のrollbackが容易。
- migration失敗を既存ユーザー設定破損へ直結させない。
- Anchor orphan cleanupとWorkspace durable transactionを独立させやすい。

Spatial Workspaceが安定した後でlegacy SavedPlacementから明示migrationを検討する。

SA0ではこのstore自体も作らない。

---

## 29. 設計上の不変条件

後続実装で守るべき条件をまとめる。

1. SessionId/WindowId/SceneEpoch/SurfaceGenerationを永続Workspace identityにしない。
2. Anchor runtime handleを永続化しない。
3. Anchor UUIDだけでLinux windowを推測しない。
4. app_idだけで同一app複数windowの永続Placementを推測しない。
5. Anchor resolve失敗時に旧world poseへsilent fallbackしない。
6. Anchor unresolved中にinputを有効化しない。
7. geometry/frame未確認のEntityをinteractiveにしない。
8. Anchor persistence失敗をPlacement保存成功へ読み替えない。
9. Workspace durable commit前にold Anchorをeraseしない。
10. Anchor cleanup失敗のためにnative/SDK/remote window cleanupを保留しない。
11. Anchor存在のためにSDK Entityを不必要に残さない。
12. Activity/Scene recreation前のasync resultを新Sceneへ適用しない。
13. remote LinuxへAnchor UUIDやroom poseを送らない。
14. debug実測とproduct acceptanceを区別する。
15. Meta APIの旧version signatureを0.15.0の事実として固定しない。

---

## 30. 公式資料・実装例

2026-11-11の設計時に参照した一次情報。

### Meta Spatial SDK

- Meta Spatial SDK Packages v0.15.0  
  https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/

- Anchor Class — Meta Spatial SDK v0.15.0  
  Anchor(handle, uuid)、FetchOptions、Locatable/Storable等  
  https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_anchor/

- Using the Scene class  
  Spatial SDKのmetric/coordinate system等  
  https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-scene/

- Meta Spatial SDK Samples  
  https://github.com/meta-quest/Meta-Spatial-SDK-Samples

### Spatial Anchor / OpenXR

- Spatial Anchors Overview  
  world-locked frame、local storage、UUIDの別保存  
  https://developers.meta.com/vr/documentation/native/android/openxr-lsa-overview/

- Spatial Anchors Features and Lifecycle  
  create/save/discover/erase/destroyとUUID lifecycle  
  https://developers.meta.com/vr/documentation/native/android/openxr-spatial-anchors-features-lifecycle/

- Spatial Anchors OpenXR API Reference  
  USE_ANCHOR_API、runtime XrSpace、persistence/discovery、restart/power-off persistence、extended errors  
  https://developers.meta.com/vr/documentation/native/android/openxr-spatial-anchors-api-ref/

- Spatial Anchors Best Practices — updated 2026-10-14  
  3m guideline、parenting、Anchor move不可、不要Anchor erase、UUID保存  
  https://developers.meta.com/vr/documentation/unreal/unreal-spatial-anchors-best-practices/

- Spatial Data Permission  
  Scene/spatial data accessのruntime permission  
  https://developers.meta.com/vr/documentation/native/native-spatial-data-perm/

### lifecycle参考sample

- Spatial Anchor sample overview — updated 2026-11-10  
  create/save/reload/delete、localization待ちの実装例  
  https://developers.meta.com/vr/documentation/unity/unity-sample-starter-spatial-anchor/

Unity/Unreal sampleはlifecycleの公式挙動を理解する参考であり、EidolonのKotlin API signatureの正本にはしない。

---

## 31. 判断

Spatial Anchor × Eidolonは、現在のEidolon設計を大きく壊さず導入できる見込みが高い。

特に既存の、

- durable settingsへruntime IDを保存しない。
- RustをXR window modelの正本とする。
- KotlinをSDK Entity/Surface ownerとする。
- stale SceneEpoch/SurfaceGenerationを拒否する。
- SDK Entity消滅とremote/native cleanupを別々に証明する。
- geometryを実rayで確認してから入力可能にする。

という構造はSpatial Anchorと相性が良い。

本機能は既存lifetimeへAnchorを混ぜるのではなく、さらに長寿命な層を追加するものとして扱う。

~~~text
Workspace / Placement / Anchor UUID
        days / months
              |
              v
runtime Placement <-> Window binding
        minutes / hours
              |
              v
SessionId / WindowId
        connection
              |
              v
SceneEpoch / Entity / SurfaceGeneration
        SDK scene lifetime
~~~

この階層を維持できれば、

- Questを外す。
- sleepする。
- Activityが再生成される。
- SSHが切れる。
- Linux appが終了する。
- Wayland windowが消える。
- Surfaceが再生成される。

という出来事のいずれも、

> 「机にはfootの場所がある」「壁にはGrafanaの場所がある」

という空間desktopそのもののidentityを破壊しない。

最初の一歩は製品Workspaceへ手を入れることではない。

**SA0でQuest 3上のAnchor lifecycleそのものを、create -> persist -> restart -> rediscover -> reboot -> rediscover -> eraseまで実測し、その結果を根拠としてSA1の永続モデルを固定する。**
