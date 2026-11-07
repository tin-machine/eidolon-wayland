# NX17b: 検証済みfootを束ねる単一workerの所有契約

このcheckpointは、純粋な500ms配置モデルと実runtimeの間の所有処理を追加する。
`window_group_owner::Controller` はまだ通常runtime/SDK/メニューから呼んでいない。
グループの表示・入力・実機の受入れは後続の接続で確認する。

- 分類は受理済みのfoot起動receipt、一意なmarker、現在のBoard予約、同一の認証済みremote起動所有に限る。titleや一般の`app_id` prefixだけで未知のGUIをまとめない。
- `group_id` を同runtime内で単調採番し、配置モデルの`revision`と分ける。展開後に同じ窓を再度束ねても、旧群のSDK完了通知は新しい群へ適用できない。
- 最初の操作は全memberの通常workspace pose・物理サイズの適用ACK後に始める。中間poseは保存配置へ書かない。確定したcollapsed配置と元の展開配置を別に保持する。
- 操作開始時のproducer frameを各窓の必要境界として固定する。500ms終了だけで入力を再開せず、全memberの同group/revision/full target、終点geometry、必要境界以降の成功drawを別ACKで確認する。継続的な再描画の最新frameを無限に追いかけない。
- 移動・終点確認中はmemberへのアプリ入力を止める。安定した束ではfrontだけに入力し、backのpresentとテーマclockを止める。未知の独立GUIを操作したことだけで群のアニメーションは取り消さない。
- 新しい分類済みfootは、SDK準備と群への加入が済む前から入力を止める。closeは実際の窓の退役から反映し、このownerはBoard予約を解放しない。
- scene/Surface交換は現在の起動所有とBoard leaseを照合して再捕捉し、新group_idで最後の確定配置を復元する。旧targetのACKを読み替えない。テーマ交換も新しいgeometry確認を要求する。
- 非表示時は確定配置への復帰を要求し、非表示の時間は復帰期限から除外する。geometry timeoutは一度だけ確定配置へ戻し、さらに確認できなければ入力停止を保つ。内部採番・時刻等の失敗でもownerと配送gateを保持する。

診断は最大9member、8完了receipt、固定reason、target、pose、frame、入力可否のみとする。
入力本文、IME本文、remote認証材料、プロセス起動nonceそのものは追加しない。

## 後続の接続と受入れ

native command処理・snapshot・隠すtargetのmask、Host最終配送gate、SDK本文/装飾/操作部の一体移動と実geometry確認、メニュー入口、固定実機caseが必要。
scene復帰時の初期poseの表示、close/new/reverse、reduced motion、front切替、保持キー解除、他窓の継続、hidden clock、SDK/native所有の回収を分けて確認する。
束全体のdrag、保存用の安定したgroup key、配置衝突補正はこのcheckpointに含めない。
既存の9枠、4個の操作ボタン、MODE、byte予算を増やしていない。

## 検証記録

共通host Cargo owner lock、対象treeのentrypoint mtime更新、`CARGO_INCREMENTAL=0`を使用した。
配置モデル18件、owner最終16件、coreのall-targets clippy（`-D warnings`）はPASS。
通常の実機表示や入力の成功を意味しない。owner初版15件後に空き容量が下限へ達した実行も保存し、
3GiB以上への回復を確認して最終16件とclippyを実施した。
