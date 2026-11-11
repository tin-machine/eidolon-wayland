# 06 — Cylinderの隔離PoCと条件付き製品統合

[入口](README.md)。Flat完成の前提条件ではない。最初にSDK能力を測り、不成立ならFlat製品を維持する。独自OpenXR runtimeや新rendererをCylinderのために追加しない。

## 1. 公式APIで確認した範囲

2026-11-11に0.15.0版の公式referenceを再確認した。

- [CylinderShapeOptions](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_toolkit_cylindershapeoptions/)は `radius:Float,width:Float,height:Float`。widthは弧長、extent=width/radius。SDKの最大360°と180°超のhorizontal scroll制約があるため、製品はさらに狭く制限する。
- [PanelConfigOptions](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_panelconfigoptions/)の `panelShapeType` / `radiusForCylinderOrSphere` を使用する。
- [PanelSceneObject](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_panelsceneobject/)は `reshape(shapeConfig:PanelShapeConfig)`、`resize(newWidthInPx:Int,newHeightInPx:Int,onResizeReady:PanelResizeReadinessHandler?=null)` を公開。reshapeはshape再作成、resizeはSurface再作成を行うと説明される。
- 同referenceの `resolveInput()` はunderlying PanelDisplayのpending input同期であり、**Eidolonの独自SceneObject listener queue全体のdrain保証とは確認できていない**。名前だけで旧event問題を解決したと扱わない。
- [HitInfo](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_hitinfo/)はentity/sceneObjectHandle/point/normal/textureCoordinateを持つ。queryとcallbackでの実値は分けて観測する。
- [公式AnimationsSample](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/822f4d1ec967d4975ae8db386502b60cda5218c0/AnimationsSample/app/src/main/java/com/meta/spatial/samples/animationssample/AnimationsSampleActivity.kt)はQuad/Cylinder変形を行う。ただしexperimentalなanimationと、Wayland入力の成立は別。

SDK binaryのcompile確認はPoCタスクで実施する。本文のSDK method名を異なるSDK版へ推測適用しない。

## 2. 能力gateと不成立時の動作

| Gate | 必要な実証拠 | 不成立時 |
|---|---|---|
| CAPI | pin済みAARでshape生成とAPI compile | PoC停止。SDK版を勝手に変更しない |
| CIMAGE | SDK Surfaceの固定patternが正しく表示 | 通常経路に入れない |
| CUV | 5×5 grid、端、面外、yawでUV/metric一致 | Cylinder非interactive。Flat維持 |
| CLIFE | Surface/Entity/listenerの作成・交換・回収 | 通常Cylinderを無効 |
| CSTALE | old listener/eventが新geometryへ流れない | 04の代替を別試験。成功を推定しない |
| CINPUT | foot文字・pointer・drag・popup・resize | Wayland Cylinderを無効 |
| CTHEME | 装飾canvasと本文collisionが一致 | plain Cylinder検証だけ。通常theme統合不可 |
| CPERF | 同条件Flatとのframe/resource/thermal記録 | 性能劣化の理由を明示し製品採否を再レビュー |

capabilityはremoteや保存JSONの `supported:true` では有効化しない。SDK/Quest OS/build/sourceを固定した受入れ記録をコードレビューし、該当機能のbuild-time gateを変更する。source更新で影響gateを再試験する。

## 3. PoCの新規ファイルとscope

`android/app/src/debug/java/io/github/tinmachine/eidolon/CurvedPanelSdkCase.kt` を入口に、`CylinderProbeController.kt`, `CylinderProbeEvidence.kt` をdebug sourceSetへ追加する。固定caseのみ。通常Workspace、Preferences、SSH、Waypipeへ接続しない。

SDK Surface producerは既存の固定pattern描画を再利用し、初回は1024×768、body1.2×0.9m、R=1.5m、yaw=0。画素を持つSurfaceは矩形のまま。異なるroot UIDやADB screenshotだけでXR表示をpassにしない。

ケースは `create_flat / create_cylinder / pose / radius / pixel_resize / delayed_input / recreate / cleanup` の固定enum。任意shader・任意path・remote commandをdebug引数で受け取らない。

## 4. 実測の順序

1. Flat pattern。中央/四隅の色とgrid、pixel size、UV原点を記録。
2. 新しくCylinderを作成。Flatとは異なるcase ID/実ownerを使う。
3. R=1.0,1.5,2.0,3.0,4.0m、yaw0/±20/±40°を、静止状態で順番に実行。
4. 一枚の同Surfaceへのreshapeは別case。before/afterのSurface参照identity、実producer binding、Entity/SceneObject、callback回数を記録。
5. pixel resizeは別case。onResizeReady、旧Surface退役、新producer attach、旧入力拒否を観測。
6. pointer down→端を越えるdrag→up、hover、scroll、headの位置変更を検査。
7. Activity再生成、sleep/resume、scene交換、Stopで全資源回収を確認。

25点gridはu/v=.02,.25,.5,.75,.98。正確な端0/1、-epsilon、1+epsilonは別否定case。pointはSDK queryから得たworld hitでgeometry oracleと比較し、callback値をworldと決めつけない。

数式oracleとSDK点の間に一定の剛体差があれば、pivot変換を明示的なadapter関数に固定する。各点ごとに別補正を当てて誤差を隠さない。UVの上下反転は一箇所のUvOriginで解決する。

## 5. 製品のshape契約

Phase 4の初版は **生成時にshapeを決め、動的な曲率変更をしない**。変更する際は旧targetを通常退役し、新SurfaceGenerationで新shapeを作成する。window/remote processは保持。旧SDK資源が回収できなければ再生成を拒否する。

radiusは[1.0,4.0]m、本文弧長/高さは現行physical size制限、装飾canvasを含むextentは最大120°。implicit SDK clampへ依存せず事前拒否。pixel sizeとlogical sizeの上限は変更しない。

新model `PanelGeometry` は `Flat` または `Cylinder{radius_m}`。physical.width_mはCylinder時に**本文の弧長**。flatは直線幅。logical resizeはshapeとは独立だが、canvas/body比率が変われば同generationでgeometryを流用せず再検証する。

親Entityの非一様Scaleは禁止。body/canvasの実width/height/radiusをSDK shapeへ指定し、親Scaleは(1,1,1)。通常Flatの既存Scale経路は残して分岐する。

## 6. plain Waylandへの接続

SHM→owned RGBA→GLES→Android Surfaceをそのまま使う。compositorへXR曲率を知らせず、pointerはUV→logical→surface-localで配送する。

plain CylinderのcollisionがSDK Panel自身で正しいことをCUVで確認できればそのUVを使う。独自hit meshを使う場合も同じcanonical body UVを返す。描画canvasとbody collisionのどちらかはNoCollisionにして二重hitを作らない。

popup/subsurfaceは現在の2D parent canvas合成。parent外の拡張や新windowへの分割をしない。logical resizeでconfigure/ack/commitとhit treeの整合を確認する。radius変更のみでWayland configureやfont sizeを勝手に変更しない。

## 7. theme付きCylinderの正確な配置

既存ThemeHitMeshは平面なのでそのまま使えない。**本文中心をwindow poseの基準**とし、visual canvasとbody collisionを同じ円筒へ置く。

定義:

```text
body pixels = Bw,Bh
canvas pixels = Cw,Ch
body top-left offset in canvas = Ox,Oy
body physical arc/height = W,H
sx=W/Bw; sy=H/Bh
canvas arc Sc=Cw*sx; canvas height Hc=Ch*sy
body-center offset in unfolded canvas:
  dx=(Ox+Bw/2-Cw/2)*sx
  dy=(Ch/2-Oy-Bh/2)*sy
phi0=dx/R
F0=[R*sin(phi0),dy,R*(cos(phi0)-1)]
```

canvasの中央をbody原点と同じ場所に置くのは、非対称offsetで誤る。canvas local→body localの剛体変換は:

```text
Qbc = yaw(-phi0)
tbc = -rotate_y(F0,-phi0)
T_world_canvas = T_world_body * (tbc,Qbc)
```

この変換でcanvas上のbody中心がbody原点、接線方向がbody local Xへ一致する。body collisionはW,H,Rで生成し、T_world_bodyを使う。canvas visualはSc,Hc,RとT_world_canvasを使いNoCollision。

geometry fixtureの非対称例dx=-0.15m,dy=0.1m,R=1.5mでは、Qbc=[0,0.049979169270678324,0,0.9987502603949663]、tbc=[0.1497501249702422,-0.1,-0.007493752082961429]。body中心のpoint/normalだけでなく左右端も一致することを独立テストする。

## 8. curved hit meshとcontrols

`CurvedBodyHitMesh.kt` を追加する場合、既存ThemeHitMeshのmaterial/collision ownershipを参考にする。verticesは角度方向N分割、上下2列。`N=max(8,ceil(extent/radians(1)))`、最大128。extent<=120°で上限内。indices=6N、vertices=2(N+1)。位置は02のF、normal=[-sin(phi),0,-cos(phi)]、UVはbodyの0..1。windingは既存frontface conventionとSDK実rayで確認。

このmeshはray hit用で、本文pixelをRustで曲面描画する新rendererではない。triangle近似とSDK visualの差が許容UV/metric誤差を超えたらCTHEME不合格。分割を無制限に増やして通さない。

controlsは本文の展開面上のanchor位置s,yからF(s/R,y)へ配置し、接線yaw=s/Rを与える。button自体は小さいFlatのままでよい。装飾ringやbadgeはvisual canvasに含めるか明示された子Entityとして扱い、無関係な平面座標を足さない。

## 9. dynamic reshapeは後続

CLIFE/CSTALEがreshapeを含めて通った後にのみ、03のgeometry transactionへshape変更を追加できる。geometry revisionを進め、new shape・body collision・controlsを同じroundで検証。Surfaceが変わった場合は既存generation lifecycleへ移行し、その操作を同targetのpose-only成功として扱わない。

animation中にWayland入力を許可する機能はこの版の非目標。単に最終shapeへ切替える間もgateを維持する。動的shapeを実装しなくてもstatic Cylinderの価値評価を完了できる。

## 10. PoC evidence schema

```text
schema=1, case_id, run_id, source_commit, apk_sha256,
sdk_version, device_model, os_build,
operation, target_identity(redacted), shape, radius_m,
input_path(query|callback), samples,
surface_before, surface_after, callbacks,
first_failure, cleanup_failure,
status(pass|fail|not_run), evidence_files
```

surface_before/afterはcase内のopaque連番で十分。アドレス・室内絶対pose・本文・入力文字列は公開artifactへ出さない。SDK完了boolだけでpassを決めず、samples誤差、入力echo、資源終了の別条件をvalidatorで検査する。
