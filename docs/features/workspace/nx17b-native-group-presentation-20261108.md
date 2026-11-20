# NX17b: nativeのgroup配置・描画・入力の接続

この段階では、単一core workerが検証済みfootのgroup ownerを呼び、
`runtime.window_groups`へ表示pose・入力可否・group完了receiptを出す。
SDKの本文移動・geometry ACK・通常メニューは次の接続であり、窓をまとめる製品操作はまだ提供しない。

`window_group`はcaptured full target、テーマrevision、操作（`collapse` / `expand` / `next_front`）を渡す。
新規collapseだけ`expected_group`と`expected_revision`を両方nullにできる。
既存群の変更は両方の一致が必須で、片方だけの指定や未知の操作は拒否する。
受付時のurgent input resetと、実際の配置完了を分ける。
通常のcommand ACKは操作受付、`window_groups.completions`はgroup配置の完了/取消を表す。

SDKからの`window_group_applied`はgroup ID、revision、同full target、終点pose、物理サイズ、content frameを渡す。
pose/サイズ/frameの値を受け取っただけでは成功にせず、nativeの現在の起動所有・Board・panel・成功drawと照合する。
SDKは別途実本文/control/MODEのgeometryを検査する必要がある。
既存workspaceのpose/サイズACKをgroup移動のACKとして流用しない。

配置アニメーション・終点確認中はmemberへのFocus/Key/Pointer/IME配送をnativeの最終処理でも拒否する。
安定した束の背面も同じgateで拒否する。private IME snapshotも対象外とする。
未知の独立GUIの入力は、群の所有失効がない限り継続できる。
群の所有中は通常のpose/physical/logicalサイズ変更をBusyとし、中間配置が保存workspaceを上書きしないようにする。
closeと既存base geometryの適用確認は、各APIの従来のcaptured target条件を維持する。

安定した背面はrendererのfull-target maskでpresent/テーマclockを止め、frame callbackもpresent済みとみなさない。
Wayland処理・新しいsnapshotの保持・SDK/native割当・Board leaseは継続する。
maskが変わるときだけrendererへ送り、rendererが受理できなければ入力を再開せずruntime failureを残す。
停止時はSDK/nativeの従来の回収を待ち、group診断も空にする。

## 未受入れの範囲

Hostの最終入力gate、SDKのpreview所有と実geometry ACK、通常操作入口、実機group caseは後続。
scene/Surface交換時の初期表示がbase gridへ一瞬戻らないこともSDK側で別に確認する。
500msの実表示、reverse/close/new/reduced motion、前面だけの入力、hidden clock停止、他窓継続、
全SDK所有物の回収、HMDでの見やすさは、このnative配線だけでは合格としない。
束全体のdrag・永続group key・衝突補正は別segmentで実装する。

## 検証

model command parser 13件、core単体の既存・新規95件、core all-targets clippy（`-D warnings`）がPASS。
coreに既存のignored 1件があることは維持し、実行済みの合格件数へ含めていない。
初回はcore 94件が通り、既存のreal-waypipe 100-frame検査1件だけ準備済みfixture binary不足で失敗した。
共通targetへ隔離treeを接続し、必要な`eidolon-shm-fixture` binaryをbuildした後、同じ検査binaryの該当1件だけを再実行してPASSを確認した。
共通host owner lock、source entrypointのmtime更新、`CARGO_INCREMENTAL=0`、各command前3GiB下限を維持した。
Android/SDK/Questのgroup表示・入力は未検証。
