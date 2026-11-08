# NX17c: SDKのgroup表示poseとgeometry確認

このcheckpointはnativeが出したgroup表示poseを、既存SDK窓へ適用する。
本文・背景ring・操作部・MODEは同じ親の配置に追随する。追加のSurface、Android display、
Entity、mesh、material、textureは作らず、既存の4ボタンと最大9窓の資源枠を維持する。
通常group menuと固定実機caseは後続であり、この段階で製品のgroup操作は提供しない。

SDKの処理は次の順序とする。

1. captured runtime/full targetが現在のgroup memberと一致した場合だけ表示poseを使う。中間poseをworkspaceへ保存しない。
2. groupがSDK poseを所有する間は、通常のbase pose/size適用・grab・move・size menuの所有を止める。menu回収失敗時は元ownerを残し、geometry ACKを送らない。
3. scene/Surface交換や分類済み新窓の準備中は、対応するSDK本文・操作部を隠す。旧targetのposeを新targetへ読み替えず、現在のcaptureが届いてからgroup位置へ出す。
4. `settling`中は本文と操作部のColliderを生かす。アプリ入力はHost/nativeで止め、実SDK rayでbodyのEntity/UV/world点、操作球の内外とMODE objectを確認する。
5. 同group ID/revision/full targetと成功content frameを持つ専用ACKを送る。enqueue受付、native command処理、group全体完了は別々に扱う。
6. 全memberのACK後に安定した背面の本文Colliderと操作部を止める。本文の最後の画像は束の端として残し、nativeのpresent/テーマclock maskで更新を止める。

ACK受付mapは最大9件。旧stamp、Pendingへの移行、binding廃棄、runtime停止で旧要求を外し、
遅いACKを別group/revisionへ付け替えない。同条件の不合格を自動的に成功へ読み替えず、nativeの有限復帰/停止gateを維持する。
`component_pose`はSDK componentの値であり、実geometryの証明とは区別する。
`body_geometry`と`control_geometry`はrayで確認した結果として記録する。

## 回転と重なり

通常のbase/move/sizeに使う既存の長いrayは変更しない。
collapsed memberは、anchor quaternionでlocal offsetを回転し、panel normal方向に25mmずつ離す。
全memberのcollapsed回転はanchorと同じで、元の回転はexpand先として保持する。
body用の短rayは各panel poseのlocal Z ±5mm、操作球用は各球のlocal normal近傍±4mmであり、world Z固定ではない。
yaw90なら層間隔もrayもworld X方向へ回る。厚さ1mmのbody hitboxと別層を同じ長いsegmentで貫かない。

完全に同じplaneに別の窓が重なる場合、公開のglobal rayだけでは対象を一意に測れない。
その場合の不一致は成功扱いせず、recenter/手動配置の別gateとして残す。
scene準備中の非表示でも実Surface作成と最初のdrawが成立すること、重なった背面の短rayが実機で成立することは、固定caseで確認する。

## 検証と統合条件

- 入力許可・実org.json wire・SDK表示capture選択の純粋fixture: 15件PASS。
- 実Android/Meta SDKのclasspathと明示したNativeBridge sourceによる関連13 sourceの限定型検査: PASS。
- owner/stamp/cleanup/表示所有の独立read-only reviewで追加の確定blockerなし。
- Gradle/Questの実表示、500msの動き、reverse/close/new/reduced motion、hidden clock、frontだけの入力、全資源回収は未受入れ。

統合時はNX12 recenter側のsize/recenter双方を取り消す`cancelForGroup`と、
`WindowMode.from`末尾のrecentering引数を維持する。
新しいGUI receiptをfootへ誤分類しないnativeのterminal mode条件も維持する。
