---
id: curved-workspace-plan
title: Curved Workspace design and staged feasibility plan
type: architecture
design_status: proposed
feature: curved-workspace
created: "2026-11-11"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: []
related_sources:
  - native/core/src/workspace.rs
---
# Eidolon Wayland — Curved Workspace / Immersive Terminal 設計・検証・段階導入計画

> **Status: Design / Proposed — 未実装・未Quest実機検証**
>
> **作成日:** 2026-11-11（JST）  
> **調査基準:** [main `470f00938fbbf70d48481966ee48bcf4d172a39d`](https://github.com/tin-machine/eidolon-wayland/commit/470f00938fbbf70d48481966ee48bcf4d172a39d)  
> **対象:** Quest 3 / Meta Spatial SDK 0.15.0 / Kotlin + Rust Wayland compositor  
> **変更の性格:** 設計・実装計画のみ。実行コード変更、feature enable、Quest実測、受入れ完了を意味しない。
>
> **目標は「画面を曲げること」ではない。** 複数LinuxウィンドウでNeovim・footのコードを読み、ログ・Grafana・文書を参照する作業を、既存の平面リモートデスクトップより長時間快適にすることである。

## 0. Executive decision と不変条件

**採用候補:** 既存のFlat Panelを3枚、ユーザーからほぼ等距離の円弧上に並べ、中央は正面、左右は内側へ向ける。曲面レンダリングより先に、明示的な「中央へ呼び寄せる／元へ返す」操作を実装・評価する。

- **方式A（Flat Fan / radial flat）を先行。** 既存`PanelPose`、SDK transform・geometry ACK、入力UVと保存を再利用する。
- **方式C（Hybrid）は、まず全Flatで成立させる。** 「中央は精読・左右は状況把握」という役割と、明示的な中央交換が主なUX価値。
- **方式B（Cylinder）は独立PoC後に採否判断。** Meta SDKがCylinder Panelを提供することと、EidolonのWayland input / 2D合成 / 装飾 / Surface寿命が成立することを分ける。
- **最初のPRにCylinder、SDK描画方式変更、remote helper変更、永続ID新設を混在させない。**
- **同じ窓を動かすだけで、WindowId / TargetKey / slot lease / remote ownerを交換しない。**
- **ユーザーが手動調整した位置を自動配置で無断上書きしない。** focusだけで窓を動かさない。
- **旧scene/Surface/geometryからの入力を新しい窓・姿勢へ配送しない。** 入力停止→実geometry確認→再開を守る。

### 0.1 検証証拠の分類

以下の記述は明確に区別する。

| 区分 | 意味 |
| --- | --- |
| コード確認 | 指定mainのコード・schema・CIログで確認できる仕様。Questで当該新機能を試した意味ではない |
| Meta/Wayland公式 | 公開APIやprotocolの記述。Eidolon固有の動作保証ではない |
| 計算値 | 明示した幾何学的仮定から計算。人間の快適性測定ではない |
| 設計案 | 未実装の型、アルゴリズム、UI、閾値、テスト案 |
| 実機PoC必須 | SDK 0.15.0のQuestで実挙動・性能・UXを確認するまで成功とはしない |

### 0.2 今回変更しない境界

SSH / authentication / host key / Waypipe / Wayland window origin / process owner / lease / journal / durable cleanup・Recoverは現行契約を維持する。レイアウト機能は**既にadmitされた窓のpresentation**を操作する。レイアウトroleからアプリ起動権限やprocess所有権を導出しない。

## 1. 基準main・関連PR・CI・作業指示

- 調査基準main: `470f00938fbbf70d48481966ee48bcf4d172a39d`（Spatial Anchor設計・SA0計画の追加）。
- 調査時にopen PRなし。#3（窓groupとrecenter）、#7〜#11（B1/B2/E1/C1/C2）はmerged。
- 基準の[host/Android CI run 43017106039](https://github.com/tin-machine/eidolon-wayland/actions/runs/43017106039): Android success、host failure。`cargo fmt --all --check`で`native/model/src/linux_desktop_v3/mod.rs`と`native/transport/src/linux_desktop/v3/sources.rs`の整形差分により停止。後段検査は成功扱いしない。
- 同headの[L7a foundation run 43017106012](https://github.com/tin-machine/eidolon-wayland/actions/runs/43017106012): GIO policy / pinned Mousepad・Waypipeの両job success。
- tree内に`AGENTS.md`は見当たらない（調査時点）。実装着手時には再確認する。
- README / docs/architecture.md / docs/progress.mdには過去checkpointの説明もある。**対象時点の実装コードと最新[通常v3統合計画](linux-real-app-v3-integration-plan-20261111.md)を優先**する。I1までの静的統合と、後続I2〜I7の通常アプリ経路・Quest受入れを混同しない。
- 3窓の初期評価には既存Termux/footまたは固定fixtureを使う。Grafana・browser等の一般Linuxアプリを通常利用できる前提を置かない。

関連文書:
[README](../README.md) /
[architecture](architecture.md) /
[progress](progress.md) /
[移動モード](nx12b-window-move-mode-20261108.md) /
[9窓](nx16c-nine-window-integration-20261108.md) /
[group/recenter](window-group-recenter-integration-20261108.md) /
[Spatial Anchor独立計画](spatial-anchor-linux-design-20261111.md)。

## 2. 現行実装の確認

### 2.1 RustのworkspaceとID

| 位置 | 現在の意味 | 本機能での扱い |
| --- | --- | --- |
| [model/workspace.rs](../native/model/src/workspace.rs) | `PanelPose { position_m: [f32;3], rotation_xyzw: [f32;4] }`。有効な位置・単位quaternionを要求 | yaw・位置の出力形式を維持 |
| 同上 | `Placement`はdesired pose / pose command / bound target / applied poseを分離 | 形だけ送信して成功とせず、同一targetの適用ACKを待つ |
| 同上 | `SavedPlacement`は`app_id, slot, pose, physical_size_m, logical_size`を保存 | 既存schemaを初期PRでは変更しない |
| 同上 | 同app_idが複数mappedなら、誤復元防止のためremember/restoreを抑制 | 同一foot複数の永続bindingを推測しない |
| [core/workspace.rs](../native/core/src/workspace.rs) | slot、WindowId、保存・復元、pose/physical/logical request | 既存の一窓変更の上に、独立したlayout operationを構築 |
| [core/window_slots.rs](../native/core/src/window_slots.rs) | WindowSlot Board / Lease / reserved・retiring等の所有 | layout roleとslotを交換しない |
| [model/surface.rs](../native/model/src/surface.rs) | `TargetKey { session, window, scene, generation }` | 旧surface・scene結果の拒否に利用 |
| [model/ids.rs](../native/model/src/ids.rs) | SessionId / WindowId / SceneEpoch / SurfaceGeneration / CommandSeq等 | runtime寿命を越えて永続化しない |
| [model/resize.rs](../native/model/src/resize.rs) | 既定physical size 1.2×0.9m。logical resizeとphysical resizeを区別 | 曲率・mサイズ・logical pixelサイズを独立に扱う |
| [model/input.rs](../native/model/src/input.rs) / [model/layout.rs](../native/model/src/layout.rs) | SDK UV→viewport logical→surface-local。clip / input region / 半開区間を検証 | Cylinderでも2D logical treeに投影し、Wayland clientを曲面非認識にする |

現行上限は[limits.rs](../native/model/src/limits.rs)の**最大9 visible panels**、logical size各辺最大2048、buffer各辺最大2048、surfaceあたり16MiB、その他の全体予算に従う。9枚という数は「最大解像度の9枚を常時更新できる」性能保証ではない。

`PanelPose::at_slot`は現在3×3優先順の平面配置を作る。これは初期の非湾曲デフォルトとして維持し、新方式は明示操作にする。

### 2.2 Android / SDK panel

対象:
[SpatialPanelAdapter.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt) /
[NativeSessionHost.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt) /
[InputAdapter.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/InputAdapter.kt) /
[PanelPoseCodec.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/PanelPoseCodec.kt) /
[WindowRecenterPose.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/WindowRecenterPose.kt) /
[ThemeHitMesh.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/ThemeHitMesh.kt)。

確認事項:

1. SDKのEntity・Surface・SceneObjectをKotlinが所有し、Rustがtargetとdesired pose、描画、入力配送を所有。
2. Panelの作成・attach・destroy、SDK rayによるgeometry確認、現在target / pose commandへのACKとnative detach・SDK disposalの独立確認が既にある。
3. 通常テーマ付きpanelには**親の平面ThemeHitMesh（本文入力用）と子の描画Panel（NoCollision）**がある。子だけCylinderにすると、見える位置と当たる位置が一致しない。
4. 現行physical resizeでは親Entityへおおむね`Scale(width/1.2, height/0.9, 1)`を与える。Cylinderへ非一様Scaleを使うと円断面が楕円になるため、Cylinderはshapeの弧長・高さ・radiusで寸法を制御する。
5. 通常UIに既存の位置変更・yaw±15°・サイズ操作がある。最初の体験比較は**新実装前の手動Fan配置**でも実行できる。
6. 通常アプリのfocus、controller ray、編集mode、押下状態はHostとInputAdapter双方の条件を守る。
7. 同一app_id複数窓の永続配置は、現在の安全な抑止を維持する。将来のPlacementIdはSpatial Anchor計画と協調して導入する。

### 2.3 Renderer / compositor

- 既存Wayland SHM→所有RGBAへのcopy→GLES texture update / SDK Surfaceへのpresentを保持。
- Canvas/viewportは本文とテーマ装飾を区別。既存の本文入力用UVは、常に装飾canvas全体のUVを意味するわけではない。
- 同内容で更新不要ならpresentを省略できる。Panelのposeやshapeが変わっても、それを理由にWayland clientへ毎frame新bufferを要求しない。
- popup/subsurfaceは現在の2D surface tree合成とclipの契約を保持。親の外へ無制限に描画できると推定しない。
- window groupは所有・退役・描画抑止と関係する。レイアウトroleとgroup ownershipを混合しない。

参照: [SHM](../native/core/src/compositor/shm.rs) /
[compositor snapshot](../native/core/src/compositor/snapshot.rs) /
[render](../native/render/src/compositor.rs) /
[group owner](../native/core/src/window_group_owner.rs)。

## 3. Meta Spatial SDK 0.15.0: Cylinder / geometry / input

### 3.1 確認できたAPI

SDK 0.15.0には`PanelConfigOptions.panelShapeType`および`radiusForCylinderOrSphere`、`PanelShapeType.CYLINDER`がある。Toolkitには`CylinderShapeOptions(radius, width, height)`があり、**widthは弧長**（角度≒width/radius）で定義される。

- [PanelConfigOptions 0.15.0](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_panelconfigoptions/)
- [PanelSceneObject 0.15.0](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_panelsceneobject/)
- [CylinderShapeOptions 0.15.0](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_toolkit_cylindershapeoptions/)
- [HitInfo 0.15.0](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_hitinfo/)
- [PointerEvent 0.15.0](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_pointerevent/)

API指定の**概念例**（この文書でビルド・実機動作確認したコードではない）:

~~~kotlin
val config = PanelConfigOptions().apply {
    panelShapeType = PanelShapeType.CYLINDER
    radiusForCylinderOrSphere = 1.5f
    // width/heightとSurface構築・layer設定は既存adapterの実APIに合わせる。
}
~~~

`PanelSceneObject.reshape(PanelShapeConfig)`はPanelShapeを再作成し、`resize(...)`はpixel Surface / display resizeに関係する。**reshapeの存在からSurface object identityの永続性を推定しない。**

[Meta公式AnimationsSample（revision固定）](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/822f4d1ec967d4975ae8db386502b60cda5218c0/AnimationsSample/app/src/main/java/com/meta/spatial/samples/animationssample/AnimationsSampleActivity.kt)は`PanelQuadCylinderAnimation`で`QUAD_TO_CYLINDER` / `CYLINDER_TO_QUAD`と`targetRadius`を使用する。これは動的形状変更の実例だが、EidolonのSurface / input / thermalの受入れにはならない。アニメーションAPIのexperimental性も考慮する。

### 3.2 ご質問の10点への結論と残る証拠

| # | 問い | 公式資料から分かること | Eidolon PoCで確定すること |
| --- | --- | --- | --- |
| 1 | Cylinder Panelがあるか | CYLINDER shapeのAPI・sampleあり | Quest 3 / 使用SDK版で実生成 |
| 2 | Kotlinで設定可能か | Kotlin APIで指定できる | 現行adapterへの実配線とcompile |
| 3 | SDK Surfaceを描画できるか | PanelとSurfaceは別概念、対応する形状を持つ | 現行EGL producer→Cylinderの実pixel |
| 4 | Flat同様に入力可能か | hit / pointer APIあり | 本文のshapeとcollision、drag・grabsの一致 |
| 5 | ray hit値の形式 | HitInfoのUV / point / normal等を公開 | ray queryとcallbackそれぞれのUV原点、bounds、space、精度 |
| 6 | 幅とradius | widthは弧長、Θ=S/R（rad） | bodyと装飾canvas、collisionの有効弧長 |
| 7 | 実行中の曲率変更 | reshape・形状アニメーション例あり | input gate中の更新、applied geometry、後着event |
| 8 | Surface再生成の要否 | reshapeはshape再作成。resizeはSurface再作成を伴う | reshape前後のSurface identity・callbackとbinding |
| 9 | Entity回転・移動との併用 | Entity transformとshapeは分離 | pivot、座標変換、Scale、親子装飾の一致 |
| 10 | GPU/compositor負荷 | 単純な固定倍率の根拠なし | 同pixel・同更新率の実機frametime / thermal |

重要: 一般`HitInfo`のworld point/normalと、Panel向け`PointerEvent`の座標表現を同一視しない。`Scene.lineSegmentIntersect`、pointer callback、move/dragを**別々**に観測する。

[Spatial SDK 0.10.0 release](https://developers.meta.com/vr/downloads/package/meta-spatial-sdk/0.10.0/)には、曲面PanelのPointerEvent UVと形状変更中の入力修正がある。0.15.0に同じ不具合があるという主張ではないが、曲面表示と入力を別gateに分ける理由になる。

### 3.3 曲率変更時の対応方針

1. Rustがtarget / layout revision / geometry revisionを捕捉し入力を止める。
2. Kotlinが新PanelShapeを適用し、旧collision/listenerを無効化。
3. SDK rayで**実際の面**のhit・UV・サイズを確認。
4. Surface identity / attach / callbackを検査。変わった場合は既存SurfaceGeneration・detach/attach・退役へ接続。
5. Rustが同runtime/full target + geometry revisionのACKを受理し、再armしたlistenerを有効化する。
6. 失敗時は入力停止を保持し、可能なら元shapeへ明示的に戻して実ACKを取る。

SDKの実callback・Surface寿命が分かるまでは、動的アニメーションを製品動作へ入れない。

## 4. 三方式の比較と採用基準

| 観点 | A: Flat Fan | B: Cylindrical Panel | C: Hybrid |
| --- | --- | --- | --- |
| 画面構成 | 独立Flatを内向き | 1 Waylandウィンドウを曲面へ | 中央Flat、左右FlatまたはCylinder |
| 最初の価値 | 既存複数窓の入射角改善 | 一枚のultrawideで端まで見やすくする | 精読/参照の役割、明示的中央入替 |
| pose再利用 | 最大 | Entity poseは再利用 | Aと同様 |
| renderer変更 | 原則不要 | 初期は矩形bufferのまま貼る | 初期Flatでは不要 |
| collision/input | 既存Flat hitを維持 | **新しい曲面hitが必要** | 形状ごとの正規化契約 |
| 現行テスト流用 | 多い | 独立SDK実機検査が必須 | Aのテスト＋入替・focus |
| 描画負荷 | 同pixelならcopy/upload差なし | copy/upload量は同じ。SDK側mesh/overdrawを測る | 使用形状・窓数・更新率に依存 |
| 保存 | 現行pose利用可能 | shape/radius追加が必要 | role/override/元配置も要る |
| 保守リスク | **低** | **高** | Flatのみなら中程度 |
| 順番 | **最初** | 独立PoC | **Flat版はBより先** |

**Arcは「配置方法」、Cylinderは「パネル形状」。同義にしない。** LayoutModeとPanelGeometryを直交した概念とする。

## 5. 推奨アーキテクチャ

~~~mermaid
flowchart TD
    UI["Android UI / 明示Layout command"] --> C["Rust core: Workspace + LayoutOperation"]
    V["Kotlin SDK: viewer pose snapshot"] --> C
    STORE["Rust durable Preferences"] <--> C
    C --> LM["Rust model: layout solver / target & revision"]
    C -->|desired pose / geometry| SDK["Kotlin SpatialPanelAdapter"]
    SDK --> P["SDK Panel: rendered shape"]
    SDK --> H["SDK collision: matched hit geometry"]
    R["Rust SHM + GLES renderer"] --> P
    H --> IA["InputAdapter: immutable binding + UV"]
    IA -->|runtime + TargetKey + input/geometry epochs| C
    C --> W["Wayland seat / surface tree"]
~~~

### 5.1 Rust / Kotlin / JNIの責務

| Rust（正本） | Kotlin（実SDK owner） | JNI |
| --- | --- | --- |
| LayoutSpec・WindowRole・基準座標系 | viewer poseを一度採取 | typed command / result |
| 計算・間隔・可視域・有効性 | Transform / shape / collisionを適用 | runtime・scene・target・operation revisionを運ぶ |
| 対象集合・実行中操作・Undo | callback / geometry probe / Surface lifecycle | 自身で状態正本を持たない |
| input admissibility・focus・Wayland座標 | 現在bindingを持つrayとUV、失効listenerの破棄 | 旧世代イベントを新世代へ再署名しない |
| 保存・復元・manual override | UI・エラーメッセージ・実測結果 | — |

### 5.2 直交する新しいモデル（案）

~~~rust
enum LayoutMode { Flat, Fan, Arc, Custom }
enum PanelGeometry { Flat, Cylinder { radius_m: f32 } }

struct LayoutSpec {
    mode: LayoutMode,
    center_distance_m: f32,
    angular_gap_deg: f32,
    frame: WorkspaceFrame,     // 基準位置とyaw
    // 役割ごとの幅・高さ・angle budget等
}

struct PlacementStyle {
    geometry: PanelGeometry,
    manual_override: bool,
}

struct LayoutOperation {
    revision: u64,
    targets: Vec<TargetKey>,   // runtime内のみ
    previous: Vec<PanelState>,
    desired: Vec<PanelState>,
    // status: validating / applying / verifying / restoring / complete / blocked
}
~~~

上記は**設計用の型案であり現存しない**。実際のFieldは既存型・serialization制限・ownershipに合わせて決める。

- `LayoutRole`はcenter/left/right等の視覚的位置。`WindowId`や`WindowSlot`とは異なる。
- `SavedPlacement`の現行`app_id + slot`は誤った同一app複数窓の識別に使わない。
- 将来`WorkspaceId/PlacementId`を[Spatial Anchor設計](spatial-anchor-linux-design-20261111.md)と共有する余地を残す。今回のPhase 1では新しい永続identityを発行しない。
- 最初は保存済み`Preferences schema=1`と別のlayout設定を検討。Phase 2で厳密migration・rollbackを設計し、現行Preferencesを暗黙に上書きしない。
- 表示位置とアプリの寿命を分離。visibilityを隠してもSSH/Waylandを止めない。元の場所へ返してもアプリを再起動しない。

### 5.3 座標正本

Meta SceneはX+ right、Y+ up、Z+ forwardのmeter系（[Meta Scene](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-scene/)）。現行Eidolonは`PanelPose.rotation_xyzw`をSDK quaternionのconstructor順へ[PanelPoseCodec](../android/app/src/main/java/io/github/tinmachine/eidolon/PanelPoseCodec.kt)で変換する。

**現行Eidolonの+Z正面・Panel前面local−Zに合わせると、左Panelの内向きyawは負、右は正。** 最終符号は実SDK ray/姿勢試験で固定する。計算式における座標の符号とSDK constructorを二重反転しない。

## 6. Flat Fan / Arcの幾何

### 6.1 3枚をユーザーに向ける

参照点`E`、基準回転`Q`、中心までの距離`d`、各方位角`θ_i`とする。縦位置`h_i`を含むcenterは、

\[
P_i = E + Q\begin{bmatrix}
d\sin\theta_i\\h_i\\d\cos\theta_i
\end{bmatrix},\qquad
q_i=Q\otimes q_y(\theta_i)
\]

\[
q_y(\theta)=\left(0,\sin\frac{\theta}{2},0,\cos\frac{\theta}{2}\right)
\]

3枚の初期値は`h_i=0`。中心はほぼ等距離になり、各面の法線は参照点を向く。

基準として中央Panelの`P_0,Q`を使う場合、

\[
E=P_0-Q\begin{bmatrix}0\\0\\d\end{bmatrix}
\]

とする。**HMDへ常時追従させない。** `viewer pose`はlayout開始時または明示recenter時に一回捕捉する。

Fanの「現在の中心位置はそのまま」方式では、中心の基準frame内`(x_i,z_i)`を使い、

\[
\psi_i=\operatorname{atan2}(x_i,z_i)
\]

でyawだけ求める。この場合、左右の距離は中央と一致しない。等距離Arcは位置も更新する。

### 6.2 見かけの幅と間隔

眼から距離`d`のFlat本文幅`w_i`について中心方向からの半角は、

\[
\alpha_i=\arctan\frac{w_i}{2d}
\]

両隣の角度中心の間隔は、

\[
\theta_{i+1}-\theta_i\ge
\alpha_i+\alpha_{i+1}+\gamma
\]

`γ`は追加gap。本文幅だけでなく、テーマcanvasや操作ボタンを含む`Envelope`をworldへ投影し、**実際の角度区間**で判定する。非対称Envelopeでは単純な左右同幅を仮定しない。

必要であれば各四隅`c`を`P_i + R_i c`へ変換し、参照点からの方位角`atan2(x,z)`の最小・最大を取る。ただし回転した面での背面交差、奥行き遮蔽、縦方向の重なり、UI overlayとの衝突は角度区間とは別に評価する。

### 6.3 3枚の数値例（計算値）

**仮定:** Flatの各本文幅1.2m、全中心距離同一、同一高さ、隣接本文gap 3°。テーマ・ボタンは含まない。

| 距離d | 1枚の水平角 | 左右中心方位角（=yaw絶対値） | 3枚の全水平角 |
| ---: | ---: | ---: | ---: |
| 1.0m | 61.9° | 64.9° | 191.8° |
| 1.5m | 43.6° | 46.6° | 136.8° |
| 2.0m | 33.4° | 36.4° | 106.2° |

したがって、1.2m幅を1.5m先に3枚置いて左右±20°にするだけでは重なりが大きい。yaw角度を**任意の固定20°ではなくサイズとgapから導出**する。1m先で全約192°は首の移動が大きくなりやすい候補だが、快適性を断言する実測ではない。

### 6.4 生産性重視の初期候補（設計仮定）

「中央で精読、左右で概要把握」として、中央水平44°、左右各26°、各gap3°とすると左右中心約±38°、全体約102°。

| 距離 | 中央本文幅 | 左右各本文幅 |
| ---: | ---: | ---: |
| 1.0m | 約0.81m | 約0.46m |
| 1.5m | 約1.21m | 約0.69m |
| 2.0m | 約1.62m | 約0.92m |

これはQuest 3の**推奨値・測定値ではなく、比較用の初期候補**。現行の既定サイズ・Panel制限、窓装飾、文字サイズ、物理可視範囲を照合してPoCで調整する。

Meta公開スペックはQuest 3の水平FOV約110°、垂直約96°、片眼2064×2208、概ね25PPDを示す（[Meta device comparison](https://developers.meta.com/vr/resources/compare-devices/)）。**全FOVが精読可能な範囲であるとは解釈しない。** 方位角は頭の旋回角でも眼球運動角でもない。Quest 3はeye trackingを持たないため、眼球移動量の直接観測は要求しない。

## 7. Cylinderの幾何・UV・可読性

### 7.1 幾何モデル

Cylinderの物理弧長`S`、高さ`H`、radius`R`、角度`Θ`:

\[
\Theta = \frac{S}{R} \quad (\mathrm{rad})
\]

\[
C=2R\sin\frac{\Theta}{2},\qquad
\Delta z=R\left(1-\cos\frac{\Theta}{2}\right)
\]

`C`は端点間の弦長、`Δz`は中央に対し端がユーザー側へ寄る量。中央をlocal原点、曲率の中心をlocal`(0,0,-R)`とする**数学モデル**:

\[
\phi = \left(u-\frac12\right)\frac{S}{R}
\]
\[
x=R\sin\phi,\quad
y=H\left(\frac12-v\right),\quad
z=R(\cos\phi-1)
\]

ここではUVの`u=0`を左、`v=0`を上と仮定。SDKの実UV原点やpivotは独立PoCで確定し、変換をadapterの一箇所へ閉じる。**数式をそのままSDK内部実装だとは主張しない。**

眼からPanel中央まで`d`の見かけの方位角:

\[
\beta(\phi)=\operatorname{atan2}\left(
R\sin\phi,\ d-R+R\cos\phi
\right)
\]

`d=R`なら水平視野角≒`S/R`。`d≠R`では弧角と見かけの視野角を同一視しない。

### 7.2 弧長1.2mでの例（計算値）

| d = R | Cylinderの弧角 | 弦長 | 端の奥行き差 |
| ---: | ---: | ---: | ---: |
| 1.0m | 68.8° | 1.129m | 17.5cm |
| 1.5m | 45.8° | 1.168m | 11.8cm |
| 2.0m | 34.4° | 1.182m | 8.9cm |

PoCでは`R=d`と、より緩い`R=2d`を比較する。radiusを変えてもlogical sizeは自動変更しない。

**湾曲すると首の移動が必ず減るわけではない。** 例: 1.2m幅・1.5m距離のFlatの水平角は約43.6°、同弧長で`R=d`のCylinderは約45.8°。価値は端での入射角・文字の横幅感が揃いやすい点にある。

### 7.3 等幅fontの評価

文字のpixel height`c_px`、本文buffer pixel height`H_px`、本文物理高さ`H_m`とすると、角度高さの近似は、

\[
h_c=H_m\frac{c_{px}}{H_{px}},\qquad
\varphi_c\simeq \frac{h_c}{d}\frac{180}{\pi}
\]

仮定`1024×768px`、`1.2×0.9m`、`d=1.5m`、セル高18pxならセル物理高さ約21.1mm、角度約0.81°。25PPDを単純に掛けた約20pixel相当は**光学実解像度・表示品質の証明ではない**。

0.7〜1.0°程度を試験用候補にし、実機で以下を比較する: `0/O`、`1/l/I`、括弧、細い記号、罫線、長い行、等幅列整列、端のフォントの太さ、30〜60分後の精読性。fontの実stroke/antialias、SDK sampling、headset装着位置を記録する。[Meta typography](https://developers.meta.com/vr/design/styles_typography/)。

### 7.4 logical sizeとshapeは独立

- Wayland clientは矩形bufferを出す。CylinderへのマッピングはQuest側の表現。
- 物理的な弧長、radius、heightの変更だけでは`xdg_toplevel.configure`・logical resizeを要求しない。
- logical resizeは従来どおりconfigure→ACK→buffer commitの契約を使う。
- 親のpopup・subsurfaceは既存2D treeへ合成し、その最終canvasを曲面へ投影。
- 既存textureのUVとCylinder形状のUVが一致する条件、content-onlyとcanvas-wideの違いをテストで固定する。
- 半径制限、SDK側の自動補正、円周を超えるshapeを入力としてそのまま受け入れず、厳密validationへ送る。

## 8. レイアウトアルゴリズム / 操作の安定性

### 8.1 モード定義

| Mode | 初期条件・動き |
| --- | --- |
| Flat | 従来の同一法線・平面グリッド。安全な戻り先 |
| Fan | 現在の中心位置を保ち、各面をviewer基準点に向ける（距離は等しくない） |
| Arc | ほぼ等距離の円弧上に中心を配置し、内向きにする |
| Custom | 手動配置を正本として保持。自動計算で上書きしない |

最初の実装は`ThreePanelRadial`など、一つの固定presetとして3枚に限定できる。汎用mode保存・9枚自動配置は後続PR。

### 8.2 1〜9枚

| 枚数 | 初期配置 |
| ---: | --- |
| 1 | 中央 |
| 2 | 中央と選択側（既定右など、明示できること） |
| 3 | 中央 + 左右 |
| 4〜6 | 中央列優先、補助段を追加 |
| 7〜9 | 最大3列×3段の役割。1列9枚にはしない |

縦方向は現行0〜4mのpose有効範囲、Panel高さ、装飾、他窓の交差を検査。収まらない場合、勝手に縮小・切断・重ね合わせせず**配置不可を返して元配置を維持する**。現行`at_slot`の優先順をrole割当の参考にしてよいが、role≠slot。

### 8.3 決定的な自動配置の処理

1. 現在runtime・session・sceneと、選択したfull TargetKeyの集合をcapture。
2. 各windowのphysical/logical/canvas/control Envelopeと適用済みposeをcapture。
3. viewer/center reference poseを**操作開始時に一度だけ**capture。
4. layout solverが全候補を計算。各面・UI controlsの角度区間、姿勢、可視範囲、衝突を検査。
5. 保存・手動override・group overlay・現在のoperationとの競合を判定。
6. どれか一枚でも無効なら**全件を適用せず**失敗。
7. OKなら対象入力gateとpressed/focusを安全に解除してからSDKへ適用。
8. 全対象の同revision geometry ACKで成功。失敗なら元配置へ復旧し、復旧のACKも必須。
9. 完了状態になった場合だけ、現行保存ルールに従って永続配置を更新。

幾何算出中に`ContentSeq`の変化まで失効条件にすると更新中のログを移動できないので、**描画contentの更新と、物理geometry/bindingの失効を区別**する。

### 8.4 手動override・ウィンドウ数変化・focus

- 手動move/rotate/resizeしたplacementは`manual_override`へ移行する。明示的な再自動配置以外で触れない。
- 窓が増えたら空roleへ追加。既存自動窓でも、原則として位置を詰め直さない。
- 窓が閉じても残りの窓は動かさず穴を維持。
- 中央の入替は`WindowId`とslotを維持し、**LayoutRole / poseのみ入替**。focusの移動は明示的に行う。
- 単なるfocus変更では位置を変更しない。クリックした窓が自動で飛ぶ仕様にしない。
- `recenter`は一回だけ基準frameを更新する明示操作。HMDへ追従させ続けない。
- 一時的Bring Hereは永続baseへ上書きせず、表示用overlayで扱う。

### 8.5 Undo・保存の境界

最初は**1段Undo**（対象、元pose、operation revision、適用状態）。後続の手動変更、window close、scene変更、SurfaceGeneration交換、他layout操作でUndoを失効させる。表示確認前のundoは、古いACKの取り消しを含む状態遷移として扱う。

保存は以下を分離する。

- 現行の`SavedPlacement`: 変更不要で保持できるpose・sizeを現在の曖昧性防止ルールで保存。
- Phase 2の`LayoutSpec`: mode、基準frame、距離、gap、manual override、可能な範囲のrole情報を**新しいversion付き保存層**へ格納。
- runtimeのみ: WindowId、session、scene、generation、現在operation、Undo、SDK Entity。
- Spatial Anchorは別計画。絶対world poseを長期的な現実空間座標として扱わない。

一時Bring Here / recenter / animation中のposeを、後続`remember`やresizeの副作用で永続baseへ混入させない。

## 9. ray・Wayland座標変換と旧イベント拒否

### 9.1 回転Flatでの理論上の交点

world ray `r(t)=O+tD`をpanel pose逆変換で`O',D'`へ変換。Flat面`z=0`について、

\[
t=-\frac{O'_z}{D'_z},\quad
u=\frac{x}{W}+\frac12,\quad
v=\frac12-\frac{y}{H}
\]

ただし`t>=0`、前面側、panel内、finiteの検査が必要。SDK UVを製品で使う場合も、公式hitとこの幾何計算を**独立test oracle**として比較する。

### 9.2 Cylinder ray intersectionの理論式

`(x,z+R)`を中心距離にした円筒面:

\[
x^2+(z+R)^2=R^2
\]

local ray`O+tD`を代入:

\[
a=D_x^2+D_z^2
\]
\[
b=2\left(O_xD_x+(O_z+R)D_z\right)
\]
\[
c=O_x^2+(O_z+R)^2-R^2
\]
\[
at^2+bt+c=0
\]

判別式と有限性をチェック。正の解を機械的に一つ採用せず、**前面・弧範囲・高さ・input boundsを満たす候補の中で最近点**を採用する。交点のモデル上のUV:

\[
\phi=\operatorname{atan2}(x,z+R),\qquad
u=\frac12+\frac{R\phi}{S},\qquad
v=\frac12-\frac{y}{H}
\]

SDK UVが正しいと確認できた後は、製品経路でray解法を二重実装せず、SDK UVを正規化して渡す。ただし数学式は回帰検証用として保持。

### 9.3 SDK UV→Wayland logical

現行EidolonはUV originを明示的に扱い、右端・下端を半開区間としてclampせず拒否する。

render targetサイズ`W_t,H_t`、logical`W_l,H_l`の場合、

\[
p_x=u W_t,\quad p_y=v H_t
\]
\[
s=\min(W_t/W_l,H_t/H_l)
\]
\[
o_x=(W_t-sW_l)/2,\quad o_y=(H_t-sH_l)/2
\]
\[
x_\mathrm{root}=(p_x-o_x)/s,\quad
y_\mathrm{root}=(p_y-o_y)/s
\]

その後、surface treeの前後関係・input region・clipを評価し、hit surfaceのoffsetを引いてsurface-localの`wl_pointer.motion`へ送る。Wayland clientはCylinderを知らない。[Wayland protocol specification](https://wayland.freedesktop.org/docs/html/apa.html)。

**content-only UVとdecorated-canvas UVを混同しない。** 現行ThemeHitMeshは本文専用hitのためcontent originを二重に引かない。Cylinderの新collisionをcanvas全体へ変更するなら、明示的なcanvas→content補正が必要。

`buffer_scale`と`buffer_transform`はbuffer/logicalの関係であり、Wayland pointer座標へ二重適用しない。

### 9.4 Input epoch / geometry revisionの追加

full targetの一致（runtime、SessionId、WindowId、SceneEpoch、SurfaceGeneration）は、**旧Surfaceから新windowへの誤配送**を防ぐ。

しかし、**同一SurfaceGenerationの古いposeで取得されたray UV**は、TargetKeyだけでは区別できない。

そのため、入力Listenerを次に束縛する案とする。

~~~text
InputBinding {
  runtime
  full TargetKey
  geometry_revision
  input_epoch
  sdk_entity_identity
  collision_identity
}
~~~

- geometry変更開始で旧bindingを無効化し、旧listenerを閉じる。
- callback取得時点の**immutable binding**をqueueまで保持する。callback到着時に現在のrevisionを付与して古いhitを新しく見せない。
- SDK内部が旧hitを新listenerへ配送する場合は、callbackデータだけで完全識別できない可能性がある。これは実機PoCの未確認項目として記録。旧scene・listenerのdisposal、pendingイベントのbarrier、実ray geometry検証を併用する。
- 新geometry ACKまで入力停止。pressed / focus / drag captureの旧状態を適切にreleaseしてから再arm。
- 現行InputAdapterにはdrag中の別receiver`HitInfo`を元receiverへ誤適用しないチェックがある。この条件を維持。
- ray move、button down/up、scroll、resize開始、popup grab、keyboard focus、IMEがいずれも**同じcurrent target**へ到達することを要求。
- 失敗・timeoutで旧eventを利用してlayoutを成功扱いにしない。

### 9.5 一括適用sequence

~~~mermaid
sequenceDiagram
    participant UI as Compose UI
    participant R as Rust Layout owner
    participant K as Kotlin SDK adapter
    participant I as Input binding
    UI->>R: Explicit Layout 3 windows
    R->>R: Capture target + all envelopes + revision
    R->>I: Close old binding, neutralize pressed
    R->>K: Apply desired poses
    K->>K: Apply transforms, verify actual ray geometry
    K-->>R: ACK per full target / geometry revision
    alt Every target verified
        R->>I: Arm new immutable input bindings
        R-->>UI: Complete and undo available
    else Failure / stale / timeout
        R->>K: Restore previous poses
        K-->>R: Restore geometry ACK
        R-->>UI: Failed; recovered or blocked
    end
~~~

全件の更新はSDKにおける物理的atomic transactionとは限らない。**Eidolonの「入力・保存・完了」を一括gateで公開する**ことを意味する。中間の見え方が残る場合も操作不可にして結果を偽装しない。

## 10. SHM / GPU / CPU / long-session性能

### 10.1 初期方針

Flat→Cylinderの比較では、**現行renderer・SHM・GLES・SDK Surface経路を維持**する。同一resolution / fps / content / theme / panel数で比較する。形状と描画backendの変更を同時に行わない。

現在の通常装飾Panelは`forceSceneTexture=true`、`layerConfig=null`。Cylinderにしたから自動的に低コストlayerになるとは仮定しない。将来のcompositor-layer比較は別実験とする。[Meta 2D Panel Layers](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-2dpanel-layers/)。

### 10.2 copy/upload量の概算

RGBA全画素転送一passの近似:

\[
B=4WHf
\]

`1024×768、30fps`の場合、1窓90MiB/s、3窓270MiB/s、9窓810MiB/s。これは**一passあたりの論理的データ量**であり、CPU copy＋GPU upload＋network送信を足し合わせた実測値ではない。静止時のpresent省略やWaypipe差分転送は別に考える。

`2048×2048×RGBA`は1枚16MiB、9枚144MiB。既存予算を超える可能性があり、「9枚×最大pixel」が成立するとは限らない。本文1024×768と装飾canvas1280×1280の負荷も別に採る。Full texture updateが多ければ、damage uploadやdirty regionをCylinderと独立した将来改善候補にする。

### 10.3 記録する指標

| 分類 | 観測項目 |
| --- | --- |
| CPU | Rust protocol/model/GL owner別時間、Kotlin callback、ray hit test |
| GPU | app frame time、SDK composition、overdraw、render queue、present |
| 更新 | ContentSeq・texture upload・present回数、Surface resize/reshape |
| memory/資源 | SHM、owned pixel、GPU texture、Android Surface、SDK Entity、FD |
| input | hit error、PointerEvent latency、stale拒否、focus・drag異常 |
| 安全性 | 未回収Entity、detach/disposal failure、input blockedの残留 |
| thermal | 温度・clock/thermal throttling・power/battery傾向 |
| UX | 読み間違い、選択ミス、タスク時間、頭位yaw、主観的疲労 |

[Meta Spatial SDK performance guidelines](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-runtime-guidelines/)はpanel種類・数・解像度・視野内占有で負荷が変化することを示す。固定のCylinder GPU倍率を根拠なしに設定しない。同条件のrelease系ビルドで比較し、debug buildによるオーバーヘッドと区別する。[Meta known issues](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-known-issues/)。

## 11. 資源所有・セキュリティ・privacy

1. **layoutはUI-only。** SSH/host key、C1/C2 persistent profile、process owner、lease、journal、Recoverの新経路を追加しない。
2. SDK Entity / SurfaceとRust targetの所有証拠を、layout成功判定から省かない。未知・未回収targetがある場合は適用を拒否。
3. scene/activity交換、window destroy、SurfaceGeneration交換、focus owner変更を、layout操作中断とする。
4. 未回収SDK resourceがある場合、再作成して見た目だけ直すのではなくblocked/pendingとして示す。
5. 新しい保存にruntime IDを残さない。永続storeの整合性と回収完了を分ける。
6. HMD world pose / room pose /将来のAnchor UUIDをremote Linuxへ渡す必要はない。公開diagnosticsへ絶対室内座標、title、本文、入力内容、credential、endpointを書かない。
7. 同じapp_idの異なる窓をtitle等で勝手に再対応付けしない。
8. スリープ・Scene交換後、古いworld座標を同じ現実位置だと無条件に再利用しない。恒久固定は[Spatial Anchor計画](spatial-anchor-linux-design-20261111.md)へ分ける。
9. 想定外に別のwindow / slot / ownerをStopするレイアウト操作を実装しない。

## 12. 段階的ロードマップとgo/no-go

| Phase | Scope | 完了条件 | 主な残リスク |
| --- | --- | --- | --- |
| **0: manual Fan baseline** | 既存UIで足元の3枚を位置・yaw調整 | 平面のまま視認・入力・配置に価値があるか記録 | HMD視認とhead poseの観測 |
| **1: Flat radial 3-window** | 明示配置、全ACK、入力gate、1段Undo | 同一targetの3枚、遅着・失敗・復旧・保存のtests | 一括SDK適用の中間状態 |
| **2: Layout presets** | Flat/Fan/Arc/Custom、1〜9枚のsolver、保存・manual override | 旧設定互換、増減時の安定配置、明示migration | 同app複数窓と永続binding |
| **2.5: Flat Hybrid UX** | Bring Here / Return、中央と左右の役割入替 | focusとposeは安定、slot leaseは不変 | 入替中の入力・undo |
| **3: isolated Cylinder PoC** | 装飾なし単Panel、Surface producer、UV grid、radius変更 | shape・pixel・collision・ray・callback寿命の実機証拠 | SDK描画とinputの食い違い |
| **4: foot × Cylinder** | footを曲面に描画、文字・pointer・popup・resize | logical size独立、旧入力拒否、UI実操作、回収 | 文字品質、drag/grab |
| **5: decorated Hybrid** | 曲面用theme canvas / collision / controls、配置と保存統合 | Flat互換、シーン再生成・長時間・全資源回収 | 保守負荷・性能・発熱 |

**Phase 3/4へ進む条件:** Flat版で、再現可能な読みづらさ・ウィンドウ幅の不足が残ること。Cylinderでの文字/入力/性能の改善が具体的に確認されなければ製品経路へ採用しない。

### 12.1 Phase 0の比較

- 現行flat（回転なし）vs 手動Fan（左右内向き）。
- 1.0 / 1.5 / 2.0mの距離候補、幅・文字サイズ固定条件と角度サイズ固定条件を分ける。
- foot+Neovim、ログ、ドキュメントの3窓で行う。
- 配置に時間がかかること、左右の選択操作、誤click、読解時間・頭の移動も記録。
- 新機能を動かした結果ではなく**既存UIを利用した事前調査**として保存。

### 12.2 最初のPRの明示スコープ

提案名: **`Flat radial 3-window layout with verified geometry and one-step undo`**。

含める:

- 3枚を明示選択し、現行サイズ・Envelopeから等距離内向きの候補poseを算出。
- すべてのwindow target/scene/bindingを捕捉してから候補を検証。1件不可なら一切変更しない。
- 各targetの入力停止、押下解除、同操作revisionのSDK適用・geometry ACK。
- 途中scene/window/generation交換、SDK失敗、timeout時の復旧・blocked状態。
- 完了後だけ保存（現行曖昧性防止ルールを維持）と1段Undo。
- UIの明示操作と結果表示、固定SDK debug case、Rust/Kotlin tests、文書。

含めない:

- Cylinder shape / radius設定、曲率animation、theme collision改造。
- 新しいremote app、SSH、Waypipe、journal、process ownerの改修。
- 9枚自動配置の一般化、プリセット保存schema追加、永続PlacementId。
- head追従、暗黙focus連動、自動中央入替、Spatial Anchor。
- 既存WindowSlotを再割当する変更。

### 12.3 変更候補ファイル

| ファイル | 予定 |
| --- | --- |
| **新規** `native/model/src/workspace_layout.rs` | 幾何solver、role、validation、layout operationの純粋型 |
| `native/model/src/lib.rs` | module登録 |
| `native/model/src/commands.rs` | 明示Layout / Undo commandと厳密な入力 |
| **新規** `native/core/src/workspace_layout.rs` | runtime target capture、操作state、ACK集約、復旧 |
| `native/core/src/workspace.rs` | 既存pose requestと保存を正しい境界で再利用 |
| `native/core/src/runtime.rs` | command dispatch、focus・input gate、revision判定 |
| `native/core/src/diagnostics.rs` | 固定reason/counter、operation result |
| `android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt` | UI操作・targetの現行確認・進捗 |
| `android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt` | 複数pose適用・実geometry ACK・listener retire |
| `android/app/src/main/java/io/github/tinmachine/eidolon/InputAdapter.kt` | immutable input/geometry binding、旧event拒否 |
| `android/app/src/main/java/io/github/tinmachine/eidolon/ControlPanel.kt` | 明示配置/Undo UI |
| 新規debug SDK case・JVM tests | SDK rayの実確認、遅延event、破棄 |
| docs / quest check | runbook、exact source・版・合否を記録 |

**実際の差分は着手時mainを読み直して決定する。** 初期PRにすべての候補file変更が必須とは限らない。

### 12.4 最初のPRに必須のtests

| 検査群 | ケース |
| --- | --- |
| geometry | 左右yawの符号、基準yaw≠0、非対称Envelope、gap、範囲外、NaN、正規化失敗、可視角度不足 |
| atomic proposal | 3窓中1件失敗で事前適用なし。途中のgeometry変更で全件cancel |
| ACK | target/command/revision不一致、ACK欠落、遅着、同じposeの異なるquaternion符号、途中窓close |
| input | gate中pointer/keyboard遮断、pressed neutral、旧listener拒否、復帰後のcurrent target |
| lifecycle | scene交換、SurfaceGeneration交代、window destroy、同session別window、sdk disposal失敗 |
| group/slot | lease・slot・WindowId不変、group overlay競合で拒否 |
| undo | 完了後だけ使用可能、後続手動moveやwindow変化で失効、復旧ACK必須 |
| save | 未完了poseを保存しない、同app_id複数を推測復元しない、既存schema不変 |
| regression | Termux / fixed GTK / 9-window / resize / popup / focus / IME / native回収 |

## 13. Cylinder PoCとQuest実測手順

### 13.1 Wayland前の隔離PoC

まずLinux clientを接続しない、装飾なしの1枚Panelで以下を検査。

1. 既知格子、四隅、中央、文字のtest textureを同じSurface producerへ渡す。
2. Flat→Cylinder、固定Rを変更する。描画が正しいかHMDとframe evidenceを別々に確認。
3. SDK`lineSegmentIntersect`、callback UVを、中心・四隅・端・面外・頭の横移動で採取。
4. SDKのpoint/normalがworldかlocalか、UV原点、弧長と半径、backface、面外の扱いを測定。
5. radius変更の前後でAndroid Surface identity、Native binding、SceneObject、Entity、SurfaceGeneration、resize callbackを個別追跡。
6. 旧listenerの遅延入力を新geometryへ受理しないことを検証。
7. destroy/Activity recreation/scene交換/Stop時の全native・SDK資源回収を確認。

Surfaceが同じだったことだけでinput安全性をpassにしない。SDK shape作成成功だけでWayland成立としない。

### 13.2 footを接続した後

- 1枚のfootを横に伸ばし、Flat・R=d・R=2dを切替える。
- 0/O、1/l/I、括弧・罫線、行末を中央/左右端で比較。
- keyboard・IME・pointer down/up・drag・scroll・popup・subsurfaceを検査。
- logical resizeは独立case。`xdg_toplevel.configure`・ACK・新buffer・render・pointerの一致を確認。
- radiusだけ変えた場合に、Wayland logical sizeやrender target寸法を暗黙に変更しない。
- theme付きに進む前にplain本文のcollision精度を合格させる。

### 13.3 実機case matrix

| Case | 合格条件 |
| --- | --- |
| CV01 | yaw 0/±20/±40でSDK UVとWayland echo位置が一致 |
| CV02 | Flat 3枚一括配置が全targetのgeometry ACK後にだけcomplete |
| CV03 | 遅着Pose ACK/Scene・Surface世代変更で入力を再開しない |
| CV04 | layout中のpointer押下・dragが旧窓に残らない |
| CV05 | Cylinder中心・端・角・面外のhitと数式oracleが許容誤差内 |
| CV06 | shape変更とSurface resizeのlifecycle/identityを区別 |
| CV07 | foot文字・popup/subsurface・Wayland pointerが曲面上で一致 |
| CV08 | logical resizeでcontent/texture/UV/focusが一致 |
| CV09 | layout undo・Bring Here / Returnで元配置とfocus復帰 |
| CV10 | 1→3→9窓、window close、scene再生成、sleepで資源リークなし |
| CV11 | 30〜60分の読解試験。快適性と頭の移動量を採取 |
| CV12 | 同解像度・更新頻度のFlat/Cylinder負荷・thermal比較 |

各caseは`pass / fail / not_run`を区別し、未実施をpassにしない。HMD目視・実controller・物理keyboardは、ADB合成入力成功と別の受入れとして記録。

### 13.4 記録証拠

- main SHA、build source digest、APK digest/version、Spatial SDK版、Quest型・Horizon OS build。
- operation ID / current runtime / target / scene / generation / input/geometry revision。
- LayoutSpec（機密でない相対値）、期待pose・実geometry ACK・誤差。
- shape/radius、render/Surface identity、SDK Entity/SceneObjectの生成・回収。
- paint / upload / present / callback counts、frametime、thermal・resource counters。
- input echoとWayland配送、stale拒否、first failureとcleanup failureを別々に記録。
- 人間の読解時間・誤click・font可読性・head pose・疲労評価。
- 本文、title、SSH credential、絶対室内座標、カメラ映像を通常artifactへ含めない。

### 13.5 検査コマンドの入口（既存と将来の区別）

~~~bash
# 現存する基準検査
python3 scripts/check-design
scripts/check-host --suite all
scripts/build-android VERSION_CODE

# Quest確認入口は既存scripts/quest-checkを基礎に、実装時に固定caseを追加
# 新しいcase名・引数はこの設計時点では未実装のため推測したCLIを成功手順にしない。
~~~

CIの合格をQuestの光学的快適さの証明へ読み替えない。Phase 3は**実機がないCIではmath / fake SDK / codec /拒否条件まで**を検査し、実SDK UV / Surface lifecycle /視認はQuest上で別結果とする。

## 14. 面白いUX拡張候補（いずれも未実装）

### 14.1 Bring Here / Return

補助窓を一時的に正面の読みやすい位置へ呼び、元のrole/poseへ戻す。元placement、入力focus、現在targetを保持し、恒久保存を変更しない。遠方のログやdocsを精読するときに、Cylinderより少ない変更で価値を検証可能。

### 14.2 Visual angle lock

距離`d`変更時、物理m幅ではなく**文字の角度サイズ**を保つ設定。希望水平角`F`に対し、

\[
W(d)=2d\tan\frac{F}{2}
\]

として物理幅だけ調整できる。Wayland logical resizeを起こすかは別の明示操作。現行のphysical上限、overlap、可読性を満たす場合に限定。

### 14.3 中央は精読・左右はsituational awareness

中央に足りるfont・情報密度を確保し、左右はログの変化・監視・資料参照にする。細部が必要になれば呼び寄せる。9枚を全て同じ文字密度で精読可能にする要件とは分ける。

### 14.4 Reading mode / Reduced motion

中央のコーディング中はテーマの装飾アニメーションを弱め、周辺で必要な異常表示だけ残す。読みやすさと発熱を分けて評価。既存ring animation/CPU・RSS・I/O表示を壊さず追加可能。

### 14.5 将来のContextual Window Graphとの共存

ウィンドウ間のrelation線を可視化する機能と共存する場合、relation線の両端は**描画上の現在window pose**に追従する。ただしrelation線の存在がlayoutを無断変更したり、remote process authorityを持つことはない。Graphはoverlay、layoutはplacement ownerとして分離する。

## 15. リスク一覧・先に答えるべき未確定事項

| Risk | 対策・決定gate |
| --- | --- |
| C1: Cylinder描画と入力面不一致 | まずplain Panel、次にThemeHitMeshと装飾canvasを同形状へ。実UV/geometry ACKを必須 |
| C2: 同じTargetKeyの古いpose event | immutable geometry revision / listener retirement / input gate。SDK内部queue挙動は実機確認 |
| C3: reshapeでSurface/SceneObject寿命が変化 | callback/identityを観測し、必要時は既存SurfaceGeneration退役へ |
| C4: Cylinderのnonuniform Scale | 形状の弧長・高さ・radiusで制御。親Entityの既存Scale流用を禁止 |
| C5: 操作中にwindowがcloseまたはgeneration交換 | 失効・復旧・blockedへ。未知targetへ結果を流用しない |
| C6: 全9枚の可視・熱予算 | 計算可能でも表示不可なら拒否。段ごとの角度予算/実機評価 |
| C7: 同app_idの複数窓の永続binding | 現行保存抑止を維持。明示PlacementIdは別計画 |
| C8: presetが手動配置を破壊 | explicit apply、manual override、Undo、未完了保存禁止 |
| C9: recenterが永続poseへ混入 | 一時overlayと保存baseを分離 |
| C10: 既存group / slot leaseとの競合 | layout roleとleaseを分離。group overlay中は既定拒否 |
| C11: SDK/Android callbackの旧scene持越し | runtime / scene / target / operation / geometry epoch全部を照合 |
| C12: 負荷を形状の効果と誤認 | pixel・更新・theme・backendを固定し比較 |
| C13: 極端に広い角度と文字可読性 | 幾何とQuest光学/人間UXを別判定。実測でgo/no-go |
| C14: host CIの既存fmt failure | 実装PR前に基準CIの原因を解消し、変更の評価と混同しない |

未確定事項を実装時に再検証する順序:

1. 0.15.0にpinされた実AAR・Kotlin compileで`PanelConfigOptions`、`reshape`、listener API signatureを確認。
2. 現行ThemeHitMeshとSDK PanelのどちらのhitをCylinderで採用するか。
3. SDK UVがcanvas全体なのかcontent geometryなのか、radius変更中の旧hitがいつ失効するか。
4. `reshape`時のSurface再生成、callback順、native producer attachの変化。
5. 既存物理サイズ・logical/buffer上限で、3枚の文字が本当に読みやすいか。
6. preset保存schemaと[Spatial Anchor計画](spatial-anchor-linux-design-20261111.md)のPlacement identityの将来の共通化。
7. Android lifecycleとSDK collisionの実負荷。長時間thermalとhead movementのトレードオフ。

## 16. 一次情報・実装参照

### Meta公式

- [Meta Spatial SDK 0.15.0 API](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/)
- [PanelConfigOptions](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_panelconfigoptions/)
- [PanelSceneObject](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_panelsceneobject/)
- [CylinderShapeOptions](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_toolkit_cylindershapeoptions/)
- [HitInfo](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_hitinfo/)
- [PointerEvent](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_pointerevent/)
- [Spatial SDK Scene](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-scene/)
- [Animation sample guide](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-sample-animations/)
- [AnimationsSample source](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/822f4d1ec967d4975ae8db386502b60cda5218c0/AnimationsSample/app/src/main/java/com/meta/spatial/samples/animationssample/AnimationsSampleActivity.kt)
- [Meta Quest device comparison](https://developers.meta.com/vr/resources/compare-devices/)
- [Typography](https://developers.meta.com/vr/design/styles_typography/)
- [2D Panel layers](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-2dpanel-layers/)
- [Spatial SDK performance guidelines](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-runtime-guidelines/)
- [Spatial SDK known issues](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-known-issues/)
- [0.10.0 release record（過去のUV関連修正）](https://developers.meta.com/vr/downloads/package/meta-spatial-sdk/0.10.0/)

### Wayland・関連OSS

- [Wayland protocol reference](https://wayland.freedesktop.org/docs/html/apa.html)
- [xdg-shell specification](https://wayland.app/protocols/xdg-shell)
- [Smithay upstream](https://github.com/Smithay/smithay)
- [Waypipe upstream](https://gitlab.freedesktop.org/mstoeckl/waypipe)

### Eidolon内部の根拠

- [workspace model](../native/model/src/workspace.rs)
- [workspace core](../native/core/src/workspace.rs)
- [window slots](../native/core/src/window_slots.rs)
- [input model](../native/model/src/input.rs)
- [viewport](../native/model/src/layout.rs)
- [renderer](../native/render/src/compositor.rs)
- [SpatialPanelAdapter](../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt)
- [NativeSessionHost](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)
- [InputAdapter](../android/app/src/main/java/io/github/tinmachine/eidolon/InputAdapter.kt)
- [existing group/recenter](window-group-recenter-integration-20261108.md)
- [Spatial Anchor（独立提案）](spatial-anchor-linux-design-20261111.md)
- [L7a v3 integration（通常アプリの別進捗）](linux-real-app-v3-integration-plan-20261111.md)

## 17. 最終判断

1. **最初に平面3枚の等距離内向き配置を検証・実装**し、現行Flatとの読解・入力・負荷を比較する。
2. **次にFlatのみのBring Here / Return・中央入替を実装**し、常時広いUIと瞬間的な精読の両立を評価する。
3. **Cylinderは独立PoCで描画、実UV、collision、resize、旧入力拒否、Surface寿命まで証明**する。
4. **十分な追加UX価値が実測できた場合だけ**、foot・テーマ付きHybridへ結合する。
5. 既存のWindowId / TargetKey / scene / generation、全回収・保存・focus・入力の安全性は、どのPhaseでも緩めない。

成果判定は「湾曲した見た目が表示された」ではなく、**Quest 3でLinuxコード・ログ・文書を長く読み、少ない操作ミスと適切な頭の動きで作業できるか**で行う。
