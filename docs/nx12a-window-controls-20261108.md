# NX12a: 各窓のfocus・closeボタンとテーマ配置

2026-11-08。基準revisionは`a688281`。これは[NX12計画](next-phase-termux-startup-linux-gui-plan-20261107.md)の最初の実装区切りで、NX12全体の完了ではない。
Android/Questでのビルド・描画・ray routingの受入れは統合後に行う。以下のテスト成功はHMDでの操作成功を意味しない。

## 利用する入口

通常の方法でターミナルを二枚開く。各窓の本文外、既定では右上に大きな丸いボタンが二つ出る。

- ミント色の照準アイコン: その窓をキーボードの入力先にする。本文クリックと別に見つけられる入口。
- ピンク色の×: その窓へ`xdg_toplevel.close`を送る。アプリの終了確認や応答を待ち、Termux/SSH全体をkillしない。

直径は実空間で8cm。濃い輪郭・厚み・パステル色で、kawaii / sweet / popの既定を小さなSDK meshから作る。
押下時は色面を少し縮め、hit sphereは動かさない。「動きを抑える」ではこの縮小を止める。
リングの線幅・直線の端・回転・CPU/RSS/I/Oへの対応は変更しない。

既存のメイン操作画面の入力状態・窓一覧・復帰操作は引き続き使える。
各窓の文字によるMODE表示やhover時の日本語ラベルはこの区切りには入らないため、入力状態は既存の固定上部表示で確認する。
配置編集が有効な間、Activityが見えない間、XRがFOCUSEDでない間は、窓別ボタンの入力を無効にする。

## テーマで指定する配置

format_version=2の`theme.toml`に次の任意tableを追加できる。既存のparts等はそのまま残す。

```toml
[controls]
anchor = "top-right"
offset_m = [0.0, 0.0]
diameter_m = 0.08
gap_m = 0.025
```

| 項目 | 契約 |
| --- | --- |
| anchor | `top-left` / `top-right` / `bottom-left` / `bottom-right`。本文の四隅 |
| offset_m | 選んだ隅から外向きのx/y距離。各0〜0.2m |
| diameter_m | ボタンの物理直径。0.06〜0.12m |
| gap_m | 本文との上下の隙間、およびボタン同士の隙間。0.02〜0.08m |

閉じるボタンを外側、focusをその内側へ並べる。両方とも本文の上下外側へ出し、最小窓サイズでも本文や互いのhit sphereに重ならない。
未知のkey、非有限値、範囲外、任意script/action/window IDは拒否する。色・アイコン・順序・辺中央のanchorは次のschema拡張候補とする。
従来v1にはtableを追加せず、そのまま既定配置を補う。v2のtable省略・装飾OFFでも既定を補う。
v1に新tableを混ぜたfileは拒否し、現在の正常テーマを維持する。

controlsはrendererが成功して採用したtheme revisionと一緒に公開する。ロード中・取消された候補の配置を先取りしない。
採用済みrevisionや本文物理サイズが変わると、旧gesture/listener/entityを回収して描画とhitを同時に作り直す。
SDKの`setAbsoluteTransform` / `setAbsoluteScale`で親のscale階層設定を考慮し、本文サイズによるボタンの縮小・二重scaleを避ける。
本文poseの子なので移動・回転に追従するが、装飾リングcanvasや回転animationの子にはしない。

## 入力と古い操作の扱い

button DOWN時にruntime handle、session/window/scene/generation、採用済みtheme revision、input intentを固定する。
開始時に既存のreset経路を通してLinux側の押下を解除する。操作部のeventをターミナルのpointer/keyには転送しない。
同じsource/downTime/所有targetで、移動量が直径の30%以下のUPだけ一回実行する。
hover離脱、cancel、drag、停止、旧UPやheld中の旧/equal DOWN、layout交換はクリックに変換しない。
同時sourceは一つのボタンにつき8件まで。

UI thread、host worker、native commandの各境界で所有対象を照合する。窓の全体選択状態をclose先に使わない。
ターゲット失効、別runtime/scene、世代交換、テーマ変更、別の入力操作が先行した場合は破棄する。
新native入口`focus_window_control`と`close_window_target`も完全なTargetKeyとcontrols_revisionを要求する。
後者は既存のclose処理へ接続し、適用済みSDK窓と成功draw、Activity/XRの可視条件を再検査する。

focusは既存の追跡付きreset ACK→fresh epochでのfocus要求へ接続する。
IME接続中はその終了/Android解除が終わるまで最大10秒待ち、未解除を成功扱いしない。
途中で別のinput intentや対象が現れた場合、元窓への遅いfocusを適用しない。
reset/close要求を提出したことと、入力可能・終了完了は別の観測とする。

## 資源と診断

一つのボタンは輪郭sphere・色面sphere・アイコンmeshの3 entityで、当たり判定は輪郭sphereだけ。
色面とアイコン、ボタン間の透明領域にはhit meshを置かない。追加のAndroid ViewPanel / VirtualDisplay / Surface / pixel canvasを作らない。

| 開いた窓 | 追加SDK entity | hit sphere / listener | 追加Android display・Surface |
| --- | --- | --- | --- |
| 2（今回の上限） | 12 | 各4 | 0 |
| 9（将来の計算上限） | 54 | 各18 | 0 |

mesh URIは5種類の固定登録。9枚での実測予算や描画合格は未確認であり、この変更で窓数上限を増やさない。
SDK生成失敗ではそのcontrolsを再有効化せず、メイン操作画面を復旧入口として残す。
窓がretireへ入った時点で操作部を先に回収し、native Surface解放待ちの間も透明なhitを残さない。

privateの`no_backup/diagnostics/latest.json`で、`adapter.targets[].controls`を観測できる。
固定タグのlogとmetadataには本文・入力文字・IME候補・部屋画像を含めない。

| field | 意味 |
| --- | --- |
| revision / enabled | controlsの採用済みtheme revision / 操作gate |
| buttons[].action/entity/ready/target | 固定操作、SDK entity、listener登録済み、完全TargetKey |
| requests_submitted | UPが検証されhostへ提出された回数。native ACKやclose完了の数ではない |
| rejected | DOWN開始時にhostの対象を確保できなかった回数 |
| failures | SDK生成・listener/回収の失敗数 |
| entities / extra_android_displays / extra_surfaces | そのcontrolsのentity数と追加Android資源数 |

既存の`adapter.bindings/listeners`は従来の本文binding用の数え方を維持し、controls listenerを混ぜない。
操作後は実runtimeの`app.input.focus_window/focus_token`や窓一覧を照合する。
metadataのreadyやrequests_submittedだけでは、可視性・物理キーボード配送・close完了の合格にしない。

## 検証と次の区切り

hostで以下を確認した。

- model全46件: 正の完全TargetKey、controls配置境界、未知field拒否、closeのurgent reset、focusのepoch/revision必須。
- theme manifest 4件: v1互換、省略時既定、v2の有効/不正controls、任意script拒否。
- renderer 1件: 転送中は旧配置を維持、採用後だけcustom配置へ変更、装飾OFFで既定へ戻す。
- pure Kotlin 6件: 四隅・最小/最大本文での非重複、物理径固定、対象交換/drag/cancel、旧UP/held中旧DOWN、source上限。
- `cargo check --locked -p eidolon-core -p eidolon-render`: 成功。vendor Smithayの既存警告は変更対象外。

rootによる独立read-only reviewでtarget/revision照合・reset/IME待ち・mesh回収を確認し、旧DOWNが新しいheld gestureを置換する点を修正した。
構造preflightはandroid/native/docsで`repository_not_supported`のため、graphには依存せず直接確認した。
初回Cargo確認では隔離worktreeにgenerated Smithayがなく失敗したため、gitignore対象のgenerated子symlinkを既存生成物へ向けた。
その後の型検査でsnapshot変数の取り違えを修正して成功した。生成物やSDK抽出jarはcommitに含めない。

統合ビルド後は、二枚それぞれのfocus/close、片側のcloseで他方が残ること、押下したままreload/resize/再生成/停止、
装飾OFF、明るいpassthroughでの視認性、透明な隙間へのray通過、IME終了待ち、sleep後の押下残りなしを実機で確認する。
失敗時はmetadataとnative ACKを分けて保存し、起きた経緯を上書きしない。

次segmentでは窓別move handleのcapture・奥行き/向き調整・正面復帰と、窓横の入力MODE/解除理由を実装する。
既存Grabbableは全体の配置編集向けなので、丸ボタンだけを掴む実装で窓本体が動いたことにはしない。
サイズ・menu・hoverラベル・テーマの外観指定も残っている。NX12完了後に9枚予算とgroup animationへ進む。
