# Window stateとowned snapshotの検証（W03）

windowのmap/unmap/remap、configure、surface tree、8変換とscale、immutable snapshotを実装し、
Linux hostとQuest Build45のAPK UIDで検証した。通常の製品UIへの復帰、SDK panelへの画像転送、
HMD上の表示・入力は後続の受入に残す。W03のstatusはin_progress。

## 実装

`native/model/src/window.rs`はWayland/Android/GLに依存せず、
LogicalSize/BufferSizePx/LogicalRect、BufferTransform、SurfaceLayer、WindowSnapshotを定義する。
snapshotにはsession/window/frame/surface/content IDと、予算管理された`Arc<OwnedRgba>`だけを渡す。
共有mappingやWayland resourceをrendererへ持ち出さず、windowごとに最新snapshotを保持する。
consumerが古いsnapshotを保持している間も同じallocationを重複計上しない。

protocol owner内の`snapshot.rs`がbufferなし初期commit→configure/ack→mapを管理する。
null attachでunmapしたらrole属性と未ACK configureを破棄し、次の初期commit/ackを要求する。
Window IDはremapで保持し、role破棄後の新windowや新surfaceには別IDを割り当てる。
server configureのACKだけでは画像・logical sizeを更新せず、対応commitまで旧snapshotを保持する。

subsurfaceの同期cacheと重なり順を保ち、非同期commitでは対象windowだけを更新する。
xdg window geometryは実treeのboundsでclampする。popupは親のwindow geometryを基準に配置し、
nested popupも親canvasでclipする。unmap/role破棄でpopupを終了し、owned cacheを回収する。
positionerは計算前に既知enum・最大2048の寸法・座標絶対値32768以下を検査する。
bufferのdamageは全体copyの処理後にclearし、各frameで蓄積しない。

Waylandのbuffer transformはclientが適用済みの変換なので、描画で逆変換する。
左上原点のbuffer UV、clip、logical offsetを一つのmodelで扱う。
frame callbackはmapped/visible/target利用可能/render予算ありの場合だけ最大60Hzで進める。
release、callback、実際のXR presentは別の指標として扱う。
producerの非zero attach offsetと3D配置・resizeの関係はI04の座標統合時に確認する。

## 実clientの検査

fixtureは実際のWayland requestとmemfdを使う。
型付きcheckpoint channelはserver configure・可視性変更・診断の同期だけを制御する。
各read/同期にはdeadline/cancelがあり、エラー時もsocketを閉じてfixture threadをjoinする。
異常clientとは接続/fixture threadの終了を指し、同じAPK process/Displayで正常clientを再実行する。

| scenario | 検証内容 |
| --- | --- |
| lifecycle | 初期map、ACKだけではresizeしない、commitで更新、unmap/remap、連続configure、破棄後のID非再利用、巨大resize拒否 |
| transforms | ABCDEFの非対称画像を8変換×scale1/2で実送信し、独立した固定表と照合 |
| frame_gates | visible/target/render予算を個別に遮断。releaseは進み、callbackは抑制・復帰 |
| tree | nested subsurface、同期途中の非公開、below/above、desync、window geometry、nested popupの2×2 clipとroot端の4×4 clip、popup_done 3回 |
| bad_serial | 未発行configure serialを拒否 |
| stale_configure | 未ACKのconfigureを発行してunmapし、旧serialのACKを拒否 |
| premature_buffer | 初期configure前のbuffer commitを拒否 |
| unconfigured_remap | 新しい初期commit/ackを経ない再attachを拒否 |
| bad_scale | buffer寸法がscaleの整数倍でないcommitを拒否 |
| positioner_overflow | 巨大anchor座標を計算前に拒否 |

異常6caseは、同Displayへ新clientを接続して8変換×2 scaleを再実行する。
旧snapshotの画像・旧treeを保持しながら更新し、不変性と最新cacheの回収も確認する。
protocol試験のclockは明示的な仮想clock（`callback_clock=virtual`）。HMDの実測fpsではない。

```bash
scripts/check-host --suite all
scripts/build-android 45
# build scriptが示したAPKと、識別済みQuestを指定する。
adb -s "$EIDOLON_QUEST_SERIAL" install -r "$EIDOLON_APK"
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case wayland-state --instrumentation --timeout 300
```

## Build45の結果

APK SHA256: `980f7d84bb78e1f9fed147adfd7d8c8a3778afc7d7b6c82036844166a5b9a689`。
source digest: `0c49b5473f2aa18b735ef536a0d0838532fe3a4c73d9619e4282216a3399efcf`。
基準commit `651eeae`からのdirty buildであり、このdigestで実装内容を同定する。

`20261105T092341-ede857b2`で全10caseがpass。
lifecycle 62ms/12checkpoint、transform 81ms/16checkpoint、frame gates 58ms/13checkpoint、
tree 68ms/8checkpoint。異常caseと回復は85〜93msだった。
全rowでFD数70、fixture FD/mapping、client/object/surface/window、pool/pixel予算は終了時0。
停止6ms、受付0ms。fresh runtimeの生成・停止とregistry回収も確認した。

同じsourceでshm 9case (`092347-414f75dc`)、独立truncate (`092353-8f028e17`)、
native依存10cycle (`092400-9600f14a`)、forward (`092405-20495778`)、
remote回収7故障 (`092413-b68f00a1`)が全pass。実lease失効は60334ms。
host Rust64件・Python27件・launcher12件、実APK Python31件、debug/release assemble/lint/packageも成功。
自動結果は`scope=native-instrumentation`、`normal_ui_returned=false`を保持する。

## 試行錯誤

1. frame callback処理でsurface Vecのimmutable borrowとownerのmutable borrowが衝突した。
   resourceのcloneを一時収集してから処理する形に修正した。
2. 新clientのpositionerにDispatchが未登録でcompile失敗。要求を送る前提となるnoop delegateを追加した。
3. roleの自動unmap検出はSmithay renderer stateを使う場合に依存していた。
   独自owned画像cacheではrole属性を明示的にresetし、未ACK serialも破棄した。
   未発行serialと未ACK旧serialの両方を実wireで拒否することを確認した。
4. 検証器のcadence errorでcleanupを飛ばし得るearly returnを見つけ、loopを抜けてcancel/joinへ進むよう修正した。
   失敗時の最後のstage・途中proof・回収状態も結果へ残す。
5. Build44で全検査が通った後のreviewで、最後のsnapshot参照の解放時に、Vecの実allocationを
   解放してからpixel予算を返す順序へ修正した。Build45でmatching sourceの実機検査を取り直した。
6. snapshotを改変したPC検査で、ID再利用、画像不変性なし、変換/clip/回復の欠落、異UID、
   資源漏れ、描画なし結果をUI復帰として扱うケースを拒否した。

rawログ・APK・端末固有情報は設定された作業rootの`eidolon-phase5-implementation/`へ保存する。
