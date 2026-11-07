# NX17b1: 背面のpresentとリング時計を止める接続

[グループの純粋モデル](nx17a-window-group-model-20261108.md)の背面policyを実行するため、
rendererに `set_hidden_targets` を追加した。runtime group ownerとSDK overlayは次段で接続する。
この時点では通常UIから窓をまとめられず、Questで背面停止を確認した結果でもない。

maskは最大9件の完全なsession/window/scene/generationを使い、重複・上限超過・停止済みownerを拒否する。
logical windowの登録、最新画像、GPU context、SDK Surfaceの所有を残したまま、指定targetの新presentだけを止める。
rendererのlogical target一覧から背面を削除してwindowを退役させる方法は使わない。
別窓のpresentは続け、解除時は `needs_redraw` で最新imageとsemanticを再描画する。
古いfull targetのmaskは新しいSurface世代へ適用しない。

既存theme playerのvisibilityも、global visibilityとこのmaskから決める。
背面のplayer時計は停止し、解除時に非表示の時間を進めない。CPU/RSS/I/Oの入力、ring速度の式、
採用済みの線幅や直線端は変更しない。
`TargetReport.presentation_paused` はrender workerが採用したmask、`player_clock_ms` は
同tickで読み取った各windowの実player時計を示す。後者は読取専用で、metricやphaseを進めない。
最後に成功したdrawのsemantic値を示す `io_draw` は従来どおりである。

host fixture二件が成功した。二つの独立targetで、背面のframe/content frame/player時計が止まり、
前面は進むこと、背面の新image/semanticは保持されて再表示後に描かれること、
Surface/context/latest imageが背面停止だけで消えないこと、旧maskと新Surface世代の分離、
通常stop後のowner/画像/context回収、maskの上限/重複/停止済み拒否を確認した。
render/theme両packageの全target clippy（warningsをerror扱い）も成功した。
host platformによる検査であり、Android GPUの実資源やHMD視認の受入れは別に行う。

次は、検証済み起動情報と現在Boardに束縛したgroup owner、workspace保存poseから独立するpresentation overlay、
SDKの実geometry ACK、Host/native両側の窓単位入力gateを接続する。
その後に固定SDK caseで前面切替・反転・close/new・背面時計停止と再表示時の最新画像を確認する。
