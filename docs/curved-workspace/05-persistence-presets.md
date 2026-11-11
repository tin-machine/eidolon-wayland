# 05 — Preset・保存・手動調整・Hybrid

[入口](README.md)。初版は03のlegacy保存だけ。本書はPhase 2/2.5の決定で、初版PRへ前倒し実装しない。

## 1. 二つのfileを同時commitしない

保存済みposeとLayoutSpecを別fileへ書くと、片方だけ成功した際に不整合になる。Phase 2では `noBackupFilesDir/workspace/layout-v2.json` 一つを新しい正本とする。現在の `workspace/layout.json` はmigration前のまま保持し、自動更新しない。

旧fileが存在し新fileがない場合はlegacy reader。新fileが存在する場合はv2 readerだけを使い、壊れていてもlegacyへ自動fallbackしない。symlink/permission不正/parse失敗を「新fileなし」として扱わない。

初回のmigrationは全通常operation・一時overlayがなく、geometryが安定した状態でユーザーが明示実行する。migration自体はapp起動・SDK移動を行わない。

## 2. 新保存schema（exact）

```json
{
  "schema":2,
  "preferences":{
    "schema":1,"background":"passthrough","keyboard_layout":"us",
    "workload_observation":true,"placements":[]
  },
  "layout":{
    "schema":1,"mode":"arc","distance_m":1.5,"gap_deg":3.0,
    "vertical_gap_deg":3.0,"auto_fill":false,"activation":"explicit"
  },
  "placements":[
    {
      "app_id":"example-editor","slot":0,"role":"center",
      "local_pose":{"position_m":[0.0,0.0,1.5],"rotation_xyzw":[0.0,0.0,0.0,1.0]},
      "physical_size_m":{"width_m":1.2,"height_m":0.9},
      "logical_size":{"width":1024,"height":768},
      "manual_override":false,"shape":{"type":"flat"}
    }
  ]
}
```

例のapp_idはfixture用。実アプリとの対応をこの名前から仮定しない。

`preferences`は既存Preferencesを再利用し、まだCW管理へ移行していないlegacy placementのみを含む。外側placementsはCWへ明示昇格したもの。一つの `(app_id,slot)` を両配列へ重複させない。昇格時は一つのcandidate document内でlegacyから除去して外側へ追加する。二つのpose正本を同じ窓へ持たせない。

外側local_poseはWorkspaceFrameからの相対pose。world pose用のPanelPose::valid（Y>=0）をそのまま使わない。新 `LocalPanelPose` は全位置[-10,10]、単位quaternionを許し、実worldへ解決した後にPanelPose::validを別途要求する。下段の負Y offsetを不正扱いしない。

`shape`はPhase 2ではflatのみ。Phase 5で `{"type":"cylinder","radius_m":1.5}` を追加し、capabilityが不成立の保存形状は非interactiveとして表示・明示変換を求める。silent Flat fallbackやradius clampで保存内容を変更しない。

全体64KiB以内、両placements合計16件以内。app_id長と制御文字、slot<9、logical/physical制限は現行と一致。role重複は同一activation集合内で拒否。保存record自体はアプリ別の候補を持てるため、異なるapp候補のcenter role重複は許すが同時activationで二つを勝手にcenterにしない。

schema/unknown/duplicate/missing field、位置arrayによるstruct代替、不正enum object、nonfinite、UTF-8不正を拒否。decode→encodeでruntime/session/window/scene/generation/SDK handle/Undo/入力本文を含まないことを検査する。

## 3. migration手順

1. legacyをread-onlyに読み、全bytesとdigestを保持。破損ならmigration拒否。
2. 明示保存対象のcurrent windowと一意app_idを検証。曖昧なappは外側placementsへ移行せずlegacy扱いを維持する。
3. 操作時のWorkspaceFrameから `local_pose = inverse(frame) * stable_base_pose` を算出。
4. legacy placementsを保持したcandidateを作り、昇格キーだけ移動。合計件数とschema検査。
5. private temporaryへ全bytesを書き、file fsync、exclusive publish、親directory fsync、readback。
6. fileが既にあれば初回migrationを拒否。既存v2を上書きする通常Saveと区別する。
7. active storeをv2へ切替。途中失敗なら成功と表示せず、file存在/readbackと元digestから明示再開する。

新fileの存在が正本選択のmarkerを兼ねるため別active-marker fileは作らない。rename後fsync失敗でも新fileが見えることはある。validate/readbackできてもdurability成功は未確認なのでerrorを保持し、明示Saveでfsyncを完了する。新fileを消してmigrationをやり直す自動復旧は禁止。

existing Storeのroot/owner/no-follow/size/atomic publication規則を共通関数へ抽出し、legacy writerとv2 writerで二重実装しない。未知path・ファイル名をUI/remote入力から選べない。通常saveはcurrent document revisionのCASをowner内で検査し、全I/Oを単一laneへ送る。

旧APKへdowngradeすると旧layout.jsonしか見えない。v2配置まで互換復元できるとは主張しない。downgrade用exportは本機能外であり、旧fileの無断書換えは行わない。

## 4. 復元と基準frame

persistent recordにはHMDの絶対world poseを保存しない。current Sceneで明示「保存済みworkspaceを開く」を選ぶと、現在viewerからreference frameを捕捉してlocal_poseをworldへ解決する。Anchorによる同じ机・壁への固定は別計画。

起動時に自動SSH接続・自動Launchしない。アプリが現れるまでは配置候補だけを保持。current mapped集合を利用者が確認してからactivationする。window出現だけでapp_id候補を推測適用しない。

同app_id複数なら、そのappに対する永続restore/updateを抑止する。接続内のWindowIdによるrole保持は可能。legacy slotを永続identityへ昇格させない。後のPlacementId/launch相関を必要とする多窓復元は、上位Spatial Anchor計画と一本化する。

## 5. viewer/recenter取得の追加protocol

Phase 2のreport schemaは2へ上げ、initial schema1の型へoptional fieldを無制限追加しない。追加phase `capturing_reference` と `temporary`欄を導入する。Kotlin/Rust readerを同じ変更で更新する。

`workspace_preset_apply` は `schema, type, request_id, expected_revision, mode, members, distance_m, gap_deg, reference` を持つ。referenceは `center` または `viewer_once`。Customは保存済み個別local_poseを解決する。new modeを初版のradial_threeと同じ意味へ丸めない。

viewer_onceのとき、coreがrequest/sceneと2秒deadlineを捕捉して `capturing_reference` を公開。Kotlinは該当stampを見て現在SDK viewer poseを一度採取し `workspace_reference_captured{schema,type,request_id,operation,scene,pose}` を返す。nativeは同request/scene/deadlineを照合し、そのposeからyaw-only基準を作る。AndroidとRustの異なるclock起点を直接比較しない。

古いcaptureを現在のrequestへ再署名しない。結果欠落・傾斜除去の不定値・scene交換は変更なしで失敗。viewer capture完了後に02のsolver/admissionを行い、03のquiesceへ進む。通常tracking updateでreference frameを更新し続けない。

recenterは全対象の共通剛体変換であり、旧frame→新frameの差だけを適用する。一枚ずつ別時点のviewer poseを使わない。

## 6. 手動調整と新窓

runtimeに `RoleBinding{target,role,manual_override,base_revision}` を保持。

- 明示Applyは確認したmemberだけを変更。manual_overrideのある窓を含める場合はUIで「手動位置を置換」を明示し、同意したcaptureをnative requestへ束縛する。
- 手動move/rotate/physical/logical resizeでその窓をmanual_override=trueにし、Undoを失効。
- window消失時はroleの穴を維持。他windowを詰めない。
- auto_fill既定false。trueでも新windowだけを空roleへ置く。既存manual placementとSDK controlsを動かさない。
- 新window候補を置けなければCW_NO_FIT。9窓上限を変更せず、他窓をclose・hiddenにして空間を作らない。
- mode変更でgroup所有を変更しない。groupがある場合は明示expand/解除を先に求める。

## 7. 中央へ呼び寄せる / Return

Phase 2.5では **一時的に中央と選択窓のposeを交換する**方式を採用。中央へ重ねる複製やmirrorを作らない。WindowId/TargetKey/slotは不変。

追加command `workspace_bring_here` はrequest_id、expected_revision、target、center_targetのみ。対象2窓の現geometryを捕捉し、scene内の全窓へ対するoverlapを検証。単純なpose交換でサイズ差による重なりが起きる場合は拒否し、暗黙resizeしない。

temporary stateに元base、表示overlay、target集合、revisionを保持。03のquiesce/ACK/rearmを通すがcommitは**baseではなくtemporary overlayの確定**。保存documentへ書かない。

`workspace_return` はexpected_temporary_operation/revisionへ一致する場合だけ元baseへ戻す。入れ子Bring Here、temporary中のmanual move/resize、group、恒久Save/別presetはBusy。returnまたはStopを先に行う。この制約で一時位置がrememberへ混入する経路を閉じる。

temporary中にtargetが消えれば生存窓だけbaseへ戻し、失われたtargetを別windowへ付け替えない。scene交換ではtemporaryを失効し、新sceneはbaseから作成する。旧資源の回収は独立に確認。

完了後focusは初版と同じく空。将来の「中央へ呼ぶ＋明示focus」操作を追加する場合も、新しいepoch/tokenを取得する単独操作として行う。

## 8. Undoと保存済み配置への復帰

Undoは直前のbase編集を戻す。Returnはtemporaryを戻す。Restore Savedはdiskから候補を読み、current sceneで全geometryを再検証する新operation。この三つを同じボタン/IDへ混在させない。

Restore Savedの失敗でcurrent baseを破壊しない。候補ロード→全検証→通常operation→完了の順。保存readbackでI/O errorがあれば旧snapshotへsilent fallbackしない。

## 9. 追加UXの扱い

Visual angle lockは距離に比例したphysical size候補を別途計算し、論理解像度は維持する。最初のpresetでは無効。Reading modeはテーマのreduced_motion設定を利用し、CW独自のanimation schedulerを作らない。関係グラフの線はcurrent presentation poseを参照するだけでlayout authorityを持たない。
