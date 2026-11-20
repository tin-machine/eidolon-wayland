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

## B2a: footの候補分類と描画準備を分ける

coreに `inspect_foot` / `capture_foot` を用意した。前者は現在の認証済みfoot forwarding、
nonce・UID・child PID/start/group、受理済みTerminalLaunch、一意なmapped marker、現在のBound Board leaseを照合する。
最初のfootは実機記録どおりUnrequested枠と `request_id=null` の主窓受領を照合し、追加窓は固有requestと照合する。
現在runtimeとsessionが違うlease、曖昧なmarker、受領のないapp_idだけの自己申告は採用しない。
GTK等の別アプリをfootへ寄せない。

後者はさらに完全な現在target、applied panel、slot、描画実績とcontent frameの存在、workspaceのbound placement、
有効なpose/physical sizeを確認する。memberの取得APIもfull targetを使い、WindowIdだけで操作対象を引き直さない。
初めて表示する窓や新規追加のfootは、分類が済んでもSDK準備待ちであれば入場させない。
この二段階は、まとめた束へ追加される新窓がgroup入場前に入力を受ける競合を、次のownerで防ぐために使う。

背面は意図的にcontentが古くなり得るため、分類時に現在producer frameとの一致は要求しない。
前面切替/展開の終点では、新しいSDK geometryと最新contentの別ACKを必須にする。
このcaptureはHostのMove/Menu/IME排他や入力intentの現在性を代用せず、操作とACKの直前に再取得する。
Controller、native command、SDK overlay、Host/native入力gateへの接続はまだ後続である。

この分類/captureのhost検査9件とcore packageの全target clippyが成功した。初期主窓のnull requestについては、既存の実機メタデータとも対応を確認した。新しいgroup操作の実機検証はまだ行っていない。
