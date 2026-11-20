# NX12: 正面復帰前の視点姿勢 probe

2026-11-08。通常ウィンドーの正面復帰に先立ち、Meta Spatial SDK 0.15.0 の公開 `Scene.getViewerPose(): Pose` を読む固定 debug case を追加する。この区切りでは menu、MODE、`set_pose_control` へ接続しない。既存ウィンドーの位置・大きさ・保存配置を変更する命令も送らない。

## 分けて確認する証拠

固定 `viewer-pose` case は runtime/scene/SDK Scene handle と最初の描画 target を固定する。Ready、Activity visible、XR FOCUSED、render 非停止を確認してから、UI thread で 100 ms 間隔に 20 samples（約 2 秒）を読む。採取開始後 5 秒、開始準備を含め 10 秒の有限期限。途中の runtime/scene/描画対象の消失、counter 逆行、非有限値や不正 quaternion は失敗とする。SDK 読出し後も runtime/scene/XR を再確認する。

結果は次を区別する。

- `pose_sampling_verified`: 規定の読出しが成立し、同じ通常 render の frame が進行した。
- `candidate_samples`: 有限・正規化・既存 window bounds を満たす正面候補を計算できた件数。SDK の yaw 計算との一致も確認する。
- `motion_observed`: 最初の pose から位置または回転の変化があった。静止していても採取自体は失敗にしない。変化があっても tracking 精度の証明にはしない。
- `tracking_freshness=not_exposed`: この公開 API には pose の tracking timestamp/valid flag がない。Android の呼出時刻や render frame の進行を、tracking の鮮度に読み替えない。
- `recenter_applied=false`、`operation_adoption_verified=false`、`hmd_verified=false`: 正面復帰や HMD 受入れの成功へ昇格しない。

生の position/quaternion、室内座標の列、スクリーンショット、入力本文は結果へ含めない。保持する pose は最初と今回だけで、履歴を蓄積しない。零原点の観測、採取件数、呼出所要時間、変化の有無などの固定 metadata を保存する。

## 起動

同 source の debug APK と既存の ADB 接続を使う。接続設定や通常 foot を作り直す操作は不要。intent へ渡せるのは固定 case と 32 桁小文字 hex nonce だけ。

```bash
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case viewer-pose --es nonce "$EIDOLON_VIEWER_NONCE"

adb -s "$EIDOLON_QUEST_SERIAL" exec-out run-as io.github.tinmachine.eidolon \
  cat no_backup/diagnostics/latest.json | \
  jq --arg nonce "$EIDOLON_VIEWER_NONCE" '.check | select(.case=="viewer-pose" and .nonce==$nonce)'
```

起動用 Activity の lifecycle による通常の focus/reset は、probe 本体の SDK/入力命令と分けて扱う。probe 本体は native command、SDK mutation、追加 Entity/Surface を作らない。終了時に別窓へ自動 focus しない。`pass` は上記の採取範囲だけを意味する。release package 検査は `ViewerPoseCase` の混入を拒否する。

## 次の正面復帰

pure な `WindowRecenterPose.candidate` を先行追加した。SDK 0.15 の `removePitchAndRoll()` と同じく quaternion の w/y 成分を再正規化し、yaw の forward 方向へ固定 1.5 m、視点と同じ高さに候補を置く。上下を向いた特異点、異常 quaternion、既存の位置範囲外は拒否し、軸別 clamp で別の位置を作らない。物理/論理/producer 寸法を変更する計算はない。

実機の API 観測後、既存の窓別 menu owner、reset/IME 解除、同 fulltarget への pose 一回送信、native ACK/SDK pose ACK、元窓への fresh focus 復帰に接続する。menu の位置は既存テーマの anchor/offset/diameter/gap に従い、別窓への自動移動や毎 frame の永続 setPose は追加しない。通常入口を有効にする前に、getViewerPose の reference space と実表示の正面・tracking 未公開範囲を評価する。

## 検証状況

pure fixtures 11 件 PASS。identity/yaw/反対向き/quaternion の符号、上下特異点、非有限/非正規、位置 bounds、静止・変化、runtime/scene喪失、期限ちょうど・poll上限・counter逆行・候補だけ不可を検査する。SDK/API の存在は実 0.15.0 AAR の `javap` で確認し、SDK を使う Kotlin 7 source の型検査も PASS。APK/Quest の成功とは分ける。実機測定はこの checkpoint の次の段階。
