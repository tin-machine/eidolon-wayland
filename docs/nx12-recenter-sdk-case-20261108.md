# NX12: 正面配置の SDK 実経路検証

Build 175 の読み取り専用 `viewer-pose` は、20 samples / 20 candidate samples、同 runtime / scene、render 前進を実 Quest で確認した。動きが観測され、全サンプルが原点という結果でもなく、SDK の yaw 計算とも一致した。native command、SDK mutation、追加 SDK resource は各 0 だった。これを受け、次版で窓メニューの「正面へ」を有効にする。tracking timestamp は公開されておらず `tracking_freshness=not_exposed`、実際の正面方向・高さの HMD 視認と物理コントローラー操作は未検証のままである。

## 固定 1 窓 case

`window-recenter` / `window-recenter-v1-single` は debug 専用の固定入口である。既存 `DebugCommandActivity` と `SdkCaseEnvironment` を通り、正規の Termux 設定を使って専用 foot を 1 枚だけ開く。intent に host、path、pose、任意コマンドを渡さない。

```sh
adb -s "$EIDOLON_QUEST_SERIAL" shell am start -n \
  io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case window-recenter --es nonce "$CASE_NONCE"
```

`CASE_NONCE` は新しい 32 桁 lowercase hex とする。同 APK / source digest / UID を確認し、既存の固定 case と同じ方法で native check JSON を読む。中間の `result=pass` だけを最終合格とせず、`product_settings_untouched=true`、世代の進んだ `preferences_environment`、`product_restored` の Ready / Visible / Focused / Idle / diagnostic 3 frames と pending cleanup なしまで確認する。通常利用中の window を残したまま行う検査ではなく、既存の停止・nonce 別 private preferences・通常設定 digest 照合・通常 runtime 復帰の wrapper を使用する。

1. 初期 window の native / SDK 準備と pose ACK を待ち、fixture 所有の窓だけを固定の別 pose へ事前配置する。その request と実適用を確認してから操作 baseline を採る。
2. SDK の実 ray hit → `Scene.onInput` →通常 focus listener を通す。非文字の Shift DOWN 一回を配送し、窓メニューの reset がそれを解放することを確認する。
3. 四項目メニューと MODE、文字入力停止を確認し、「正面へ」を実 hit で一回だけ選ぶ。
4. capture した runtime / full target / theme revision に対する recenter request 一件、enqueue と native ACK、同 request の model / `applied_pose`、SDK body hit を照合する。移動前後の pose が同じなら `pass` にしない。
5. 物理サイズ・size command・logical / producer / decoration canvas の不変と、操作 reset 一回＋復帰 focus reset 一回、同じ窓の raw 入力復帰を確認する。SDK ボタン操作が Linux pointer buttons へ漏れていないことも確認する。
6. 実 close ボタンから元の full target を退役させ、native runtime と SDK control / menu / MODE の owner を回収する。その後 wrapper が元の設定 digest と通常 Ready / Visible / Focused / diagnostic frames を確認する。

総 deadline は 120 秒、明示停止後は 30 秒。ACK 拒否、取消、別 runtime / scene / generation / theme revision、余計な要求や epoch は失敗となり、viewer 再読込や自動再送はしない。失敗時も wrapper の停止・復帰を通す。case 自身の proof は段階、ID、件数、真偽だけを保存し、viewer や移動後の室内座標をログへコピーしない。瞬間的な MODE RECENTERING を poll が見逃す場合は、その観測値を false と記録し、移動成功の必須証拠に読み替えない。

既存 `window-size` は `window-size-v2-four-menu-items` へ更新し、「大きく・小さく・正面へ・戻る」の四項目の準備を確認する。既存版の三項目 proof と混同しない。

## 重なりと API の限界

この case の成功は **専用 1 窓** の成立であり、`multi_window_overlap_verified=false` を維持する。2 枚目を正面へ戻すと既存の中央窓と重なる可能性がある。global raycast は別窓を先に hit し得るため、coplanar / 遮蔽した 2 窓の実 gate と、操作開始 baseline 後の他窓不変は完了条件に残す。必要なら同じ視線方向の有限個の近傍距離から選択し、選択窓だけを動かす。未観測の重なりを成功扱いしたり ACK 条件を希望 pose の一致だけへ緩めたりしない。

SDK 0.15.0 の実 jar には object 限定 intersection / world pose getter を確認できなかった。`SceneObject.getNodeGlobalMatrix` は native 呼出しではあるが、メッシュ内部の座標を返し、`setPosition` / rotation / scale による object 自体の配置を含まない。したがってこの値と希望 Transform の合成は独立した world 配置の証拠にならず、ACK には採用しない。[Meta 公式 SceneObject API](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_sceneobject/)

## 検証範囲

pure 27 tests と実 SDK 15 source の型検査、最終 case 差分の型検査が成功した。実機操作は統合版の APK で行う。これらの検査は、Quest のこの固定 case、重なり時、物理操作、HMD での見やすさの合格を意味しない。
