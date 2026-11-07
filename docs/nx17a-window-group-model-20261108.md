# NX17a: 同種ウィンドーの配置モデルと次の接続

同種アプリを重ね、展開時に元の自由配置へ戻すための純粋モデルを追加した。
この区切りはnative modelとhost検査だけで、通常UI・SDK表示・入力配送・ring時計にはまだ接続していない。
`present=false` / `clock_running=false` は次段が実行するpolicyであり、実機で背面描画が止まった証拠ではない。
実装順と利用者要求は[次フェーズ計画のグループ化節](next-phase-termux-startup-linux-gui-plan-20261107.md#同じグループを重ねる展開する)に従う。

## 所有と保存配置

`Group` はruntime/session/scene/アプリ種別を捕捉し、最大9件のBoundなBoard entryとfull targetを持つ。
同じwindow、slot、別runtime/session/scene、未bound、無効pose/physical size、固定・別capture中のmemberは拒否する。
空きslotを作るために既存窓を閉じない。新規窓の追加には、呼出元が先に予約しBoundへ進めたentryが必要である。
最後の窓を除いたEmptyは終端とし、以前のfrontやscopeを使う再入場は拒否する。

`Application::Foot/Catalog` は分類結果を渡す型であり、型名だけで信頼を証明しない。
次段のruntimeは、現在のhelper/session/nonce、acceptedなTerminalId/request receipt、
`window_launch::correlate` の一意なmapped window、起動catalogのアプリ種別を照合する。
title、PIDの一致、任意app_idのprefixだけでは分類しない。確定できない一般GUIは別窓のままとする。
Board entry/full targetの現在性は、操作・SDK ACKごとに再検査する。モデル内の過去entryだけを実行承認に使わない。

モデルは展開元のpose/physical size/slot順を保持する。サンプル取得やアニメーション途中のposeを
workspaceへ保存しない。collapse前の手動配置、前面を切り替えた後の展開先もそのまま保持する。
closeは捕捉した一件だけを除き、残りの展開先を変更しない。モデルのremoveはBoard leaseの解放や
remote process終了を行わず、通常のNative/SDK/Remote回収と別に扱う。

## 移動と入力policy

collapse/expandは全member共通の開始時刻から標準500msで動く。
位置はsmoothstep、quaternionは最短経路の球面補間を使い、反転時はその時点の表示poseを新しい始点にする。
欠測frameがあってもframe数で時間を延ばさず、単調時計から補間する。clockが逆行した要求は拒否する。
重ね先は操作した窓の近くとし、他の窓は同じ向きで少しずつずらす。無効な範囲を軸別clampしない。
このoffsetの見やすさや快適さは、最後にHMDで確認する。

移動中・終点のSDK確認待ちは、対象memberのアプリ入力を禁止する。
500ms経過だけでは完了にせず、新しい操作revision、同full target、最終poseの実geometry、
最新content frameが全memberでそろってからstableへ進む。
reduced motionは補間時間だけをゼロにし、geometry/frame確認を省略しない。

stable collapsedでは前面の一件だけが入力可能で、新presentとring clockも前面だけを動かす。
背面の既存quad/画像は束の端として残せるが、その内容・ボタン・リングはhitを奪わない。
前面切替中も一度入力を止め、新frontの内容とgeometryを確認する。
移動前後の座標と古い画像を取り違えない。負荷リングのCPU/RSS/I/Oの意味や線幅は変更しない。

cancelも新しいrevisionを発行し、最後の確定配置へ戻るgeometry ACKを待つ。
古いアニメーションのACKが復帰確認を代用することはできない。
通常の確認期限は終点から5秒、取消後の復帰も5秒とし、復帰できなければBlockedを維持する。
同条件の自動再試行を繰り返さず、入力を安全側に停止する。
Surface/scene交換後は旧full targetへACKを当てはめず、次段のownerが新しいcaptureから復元する。

## 次segmentの接続

1. **native ownerとpresentation overlay**: verified launch→種別の台帳、現在のBoard/target検査、group command/revisionを追加する。
   workspaceの保存poseとgroup表示poseを分ける。毎frameの `SetPose` で保存配置を書き換えない。
   SDKからのgroup geometry ACKも、通常の `pose_applied` と区別する。
2. **SDK/入力**: 本文・装飾・MODE・四ボタンを一体で動かし、実hit geometryの追随を確認する。
   Hostとnativeの両方で背面と移動中のアプリ入力を止める。既存Move/Menu/IMEのguardは緩めない。
   無関係の窓は入力を続けられるよう、group member単位のgateと操作ownerを分ける。
   反転/前面切替/closeは捕捉groupの明示操作とし、global selected windowを引き直さない。
3. **renderとcallback clock**: 現在のrendererはlogical target列でpresentとtheme player visibilityを絞るが、
   同じ列をslot/panel割当にも使っている。背面を描画対象から外してもSDK bindingやBoard予約を退役させない分離が必要である。
   helper/protocol/通信を止めずに背面present/ring時計を止め、前面復帰では最新contentを描いてから入力を許可する。
4. **窓周辺UI**: 四つめのmenuを入口に、まとめる/展開/前面切替とgroup名・枚数を表示する。
   三項目のpageを使う等で同時一menu・有限資源を維持する。既存サイズmenuの直接操作とSDK fixtureを無断で置き換えない。
   束の移動・回転差分を展開元すべてへ適用する処理、再起動後の安定キーによる保存、展開先の衝突補正は別に接続する。
5. **実機固定case**: 二窓から始め、500ms/反転/close/new/drag排他/reduced motion、
   前面だけの入力、背面present/clockカウンター停止、展開後の最新画像、SDK/native/Board回収を個別に確認する。
   四ボタン九枚の資源予算と元設定の不変を回帰確認し、物理/HMDの受入れは最後に行う。

## この区切りの検証

純粋fixtureは元配置とslot占有、共通clock/終点ACK、反転/旧ACK、quaternion、reduced motion、
前面切替、close、新規Bound窓/10件目拒否、固定/drag排他、取消復帰/期限切れ、別target/旧frame、
無効値/部分更新防止、Emptyの終端、欠測frame/clock逆行を確認する。
独立read-onlyレビューでEmpty後の再入場によるfront不在を指摘され、Emptyを終端とする回帰を追加した。
実runtimeのclassification、overlay、SDK/Android、hidden draw/clock、物理入力/HMDはこの段階では未検証である。

モデル17件とeidolon-modelの全target clippy（warningsをerror扱い）が成功した。Gradle/APK/Quest操作は行っていない。
