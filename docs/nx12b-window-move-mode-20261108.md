# NX12b: 丸い移動ハンドルと窓横の入力MODE

2026-11-08。基準は`bb57a25`、SDK API修正`a434884`と停止待ち修正`09784d`を保持する。
[NX12aのfocus/close](nx12a-window-controls-20261108.md)に移動と状態表示を追加する実装区切り。
Build157で確定した二ボタンのSDK総合PASSは、その版と証拠のまま保持する。
以下の新しい三ボタン・移動・MODEの実機受入れは別に行い、hostテストの成功でHMD/物理操作を合格にしない。

## Build161のSDK固定case結果

probe `286c59434c219161f920207b13988f7f`で三ボタンと移動・MODEの固定caseがPASS。
debug build/lint/package/JUnit134件も成功した。APK SHA-256は
`9c7c785d447e4b63c7c16c576b08a8eedc3e2f58ce7f9af744e4754d365a28e7`、
source digestは`efd2369c063a8f43433d37c714b12d39152ea01f73cbf450e962dd72d995f84e`。

実SDKのray/listenerを通し、source pose欠落拒否、窓全体のpreviewとMOVING表示、
release前のnative pose不変を確認した。保持中にrayをハンドルから外してもcaptureを保持し、
同ControllerのUPを`sdk_off_handle_up`で受けて、native ACK・モデルpose・SDK geometry適用・
raw focus復帰まで到達した。MODEのtextureは作り直さず更新された。
取消によるclose防止、古いrevision/generation拒否、捕捉窓だけのclose、旧control非hit、
source/旧runtime退役、controlsとMODEのownerゼロ、通常設定不変も確認した。
この結果は合成SDK入力であり、HMD視認・物理コントローラー操作・文字配送の受入れは残る。

## 操作

各窓の本文外に、クリーム色の四方向矢印、ミント色のfocus、ピンク色のcloseを並べる。
四方向矢印をtrigger（A/Xも同じprimary操作）で押し続け、コントローラーを動かすと窓全体が動く。
奥行きと向きもコントローラーの姿勢に追従する。押し始めの相対位置を保持し、ハンドルへ窓が飛びつく操作にはしない。
離すと有効な配置を保存し、SDK geometryの適用まで確認してから、その窓へ入力を戻す。
移動が成立するにはSDK sourceに有効なTransformとactiveなControllerが必要。取得できないsourceをidentity poseで代用しない。

移動中は対象窓とcaptured sourceからのアプリ入力を止める。開始時に既存resetを通して押下中のLinuxキーを解除する。
他窓の描画・通信を維持し、別の入力先選択・テーマ変更・配置編集など新しい入力意図が来たらpreviewを取り消す。
sleep、追跡/Activity可視性の喪失、close、Scene/Surface世代交換、停止でも取消し、遅れて元窓へfocusし直さない。
メイン操作画面の窓一覧・微調整・復帰操作は維持する。

「動きを抑える」ではボタン押下の縮小を止める。利用者の手に追従する移動自体に補間・慣性・bounceは足さない。
サイズ変更、正面復帰の新ボタン、move menu、thumbstick専用の距離調整は別の区切りに残す。

## 配置の所有と確定

既存SDK Grabbableは既定squeeze入力とControllerに依存し、現在の全体配置編集に使っている。
今回はその編集フラグを窓別操作に転用せず、丸ボタンのcaptureで次の処理を行う。

1. DOWNでruntime、session/window/scene/generation、theme revision、input intent、source/downTimeを固定する。
2. sourceの初期絶対poseを`C0`、本文を`W0`として、保持中の本文poseを`Ct × C0⁻¹ × W0`で更新する。
   ハンドルの子だけでなく本文entityを動かすため、本文・リング・操作部・MODE・hit geometryが一体で追従する。
3. preview中は既存applyPose/applyPhysicalが古いnative poseを上書きしない。previewを毎frame保存へ送らない。
4. 有効なreleaseだけ一回`set_pose_control`へ進む。完全TargetKeyとcontrols_revision、有効poseをnativeでも再検査する。
5. request_idのnative ACK、同requestのモデルpose、実SDK本文ray照合後の`pose_applied`を別々に確認する。
6. 全て揃った成功だけcaptured targetへの既存reset ACK→fresh epochのfocus処理へ戻す。IME終了/Android解除の待機条件を緩めない。

通常の保持期限は60秒、releaseから配置確認までは5秒。期限ちょうども期限切れとし、失敗した要求を自動で再送しない。
位置は既存契約のx/z±10m、y=0〜4m、有効な正規化quaternionに限定する。
取消・不正pose・enqueue失敗・ACK不一致ではpreviewを現在の有効なnativeモデルへ戻し、入力は勝手に再開しない。
既に受理された有効な確定poseはモデルを正とし、終了待ちで起きた別の入力意図を上書きするfocusは送らない。

release入口でも新しいnative snapshotからReady/可視/XR/scene/完全target適用/draw/theme revisionを再確認する。
別intentやscene失効の後に届いたUPで、古いpreviewを書き直さない。
source/time履歴を窓別controllerにつき最大8件保持し、完了・取消後の古い/equal DOWNによる再開始も拒否する。
同時移動は一件。登録sourceが上限に達した場合は追い出して古い保護を失う代わりに、新規開始を拒否する。

## 窓横のMODE

操作部のさらに外側へ、小さな非hitラベルを置く。主な表示は次のとおり。

| 表示 | 観測する状態 |
| --- | --- |
| 移動中 / 配置を確認中 | captured preview / native・geometry確認待ち |
| キー入力先 | この窓の有効targetとfocus tokenが配送先に一致 |
| 入力先を選択 | この窓が現在のキーボード入力先ではない |
| IME入力の検証中 / IME終了待ち | 実装済みIME ownerまたは終了/復帰gate。日本語変換成立の意味ではない |
| 設定欄へ入力 | 操作画面/検証欄が入力を所有 |
| キーボード未検出 / 配置編集 / 準備中 / 入力停止 | 既存配送gateと対象窓の状態 |

ラベルは理由文字列を入力判定の正本にせず、既存gateと窓別captureから作る。
本文、IMEの候補/確定文字、ファイル名、アプリ内のテキストはラベル画像やログへコピーしない。
focus、入力モード、キーボードの検出、文字配送/変換の受入れはそれぞれ分ける。

テーマの既存controls anchor/offset/diameter/gapから、move/focus/closeとラベルの位置を同時に求める。
四隅・v1省略・装飾OFF・従来v2 importの互換を維持し、自由scriptや任意actionを追加しない。
色・アイコン素材・ラベルの任意文言や独立した位置指定は、この区切りでは追加しない。
リングの採用線幅・直線端・CPU/RSS/I/O回転契約は変えない。

## MODE描画資源の予算と回収

初案では固定`mesh://` URIがSDKにcacheされると想定していたが、独立reviewで誤りと判明した。
SDK 0.15.0のMeshManagerはcreatorを都度呼び、entity別にmeshを所有する。固定URI数をTexture数の上限には使わない。

実装は窓ごとにSceneObject/SceneMesh/SceneMaterial/SceneTextureを一つずつ直接所有し、SceneObjectSystemへ登録する。
mode変更では同じTextureのpixelだけ更新する。Mesh component、creator、グローバル共有SceneMesh、遅いcreator callbackを使わない。
ラベルの実quadはNoCollision。透明な角やボタン間を広いhit rectangleで塞がない。

| 資源 | 一窓 / slot | 二枚 | 将来九枚の要求量 |
| --- | --- | --- | --- |
| 三ボタンSDK entity / hit listener | 9 / 3 | 18 / 6 | 81 / 27 |
| MODE entity / object / mesh / material / texture | 各1 | 各2 | 各9 |
| MODE GPU RGBA要求、512×64・1mip | 128KiB | 256KiB | 1.125MiB |
| MODE CPU Bitmap＋direct buffer pool | 256KiB/slot | 512KiB | 最大2.25MiB |
| 追加Android display / Surface | 0 | 0 | 0 |

これは要求するpixel量であり、GPU driver・mesh・material等の実メモリ総量ではない。
nativeアプリ画像のPixelBudgetとは分けて計上し、9枚での負荷・回収・見やすさを合格にする前に実測する。
SDK表示窓数の上限はこの変更で増やさない。

閉じる時は登録解除→SceneObject.destroy→entity/mesh/material/textureの明示destroyを順に行う。
失敗したownerをregistryに残し、GCが走ったと仮定してゼロにはしない。registry/pixel poolは最大9件で上限を保つ。
CPU pixel slotはprocess内で再利用する固定pool。runtime停止後の`pixel_slots_in_use=0`と、保持中の`pixel_slots_allocated`を分ける。
GPU/API objectのownerはゼロを確認し、backendの実GPU回収やHMD可視性はAPI呼出し成功数だけで合格にしない。

## 診断と検証

`adapter.targets[].move`はphase/reason/source/request_id/開始・完了・取消・拒否数を持つ。
`mode_badge`はmode/label/object_ready/non_hitと更新・失敗数を持ち、object_readyは最新文字の可視確認ではない。
`adapter.window_mode_badges`で各SDK資源owner、texture生成/更新、pixel poolのallocated/in-use、解放待ちownerを観測する。
従来`adapter.bindings/listeners`の本文用カウントへ新しい資源を混ぜない。

hostではmodel全52件、core/render cargo check、pure Kotlin計16件が成功した。
pureは6DoFの相対位置・深さ・回転、no-jump、所有tuple交換、期限境界、不正pose、native ACK/model/geometryの分離、
BUSY後の再送なし、完了後の旧DOWN、source上限、三ボタンの非重複、窓別MODEの誤READY防止を確認する。

新debug固定caseは`window-move`（fixture_revision=`window-move-v1`）。`SdkCaseEnvironment`で隔離する。
通常作業を終了してIdleにし、最新APKのsource identityを照合してから実行する。

```sh
EIDOLON_MOVE_NONCE="$(openssl rand -hex 16)"
adb -s "$EIDOLON_QUEST_SERIAL" shell am start -W \
  -n io.github.tinmachine.eidolon/.MainActivity \
  --es case window-move --es nonce "$EIDOLON_MOVE_NONCE"
adb -s "$EIDOLON_QUEST_SERIAL" shell run-as io.github.tinmachine.eidolon \
  cat no_backup/diagnostics/latest.json
```

同case/nonce/revisionの最終結果で、元のfocus/close・held key解除・不正target/revision拒否・片側close・他窓継続に加え、
source Pose未取得の拒否、合成Controllerの本文6DoF previewが未保存であること、release→native ACK→モデル/実SDK geometry→入力復帰を確認する。
MODEの実ray非hit、mode変更によるTexture再生成なし、停止後の全MODE SDK owner/pixel slot in-useゼロも別に照合する。

`window-controls`は新UIに対するfocus/close検査としてfixture_revision=`window-controls-v2`へ進める。
その成功をmove検査の成功に読み替えず、旧Build157の二ボタンPASSを書き換えない。
物理コントローラーの操作感、HMDでのMODEの文字・向き・可読性、かな漢字変換、長時間・sleep・Scene交換・他窓同時入力の実測は残す。

調査・失敗記録: MODEのcache仮定を独立レビューで撤回し、直接所有へ変更した。releaseの旧owner保護と旧DOWN履歴も追加した。
host確認後にroot filesystemの空きが尽きたため、書込みを止めてsource末尾と検証ログを確認した。
再生成可能なCargo dev cacheを整理して回復し、APK/source/証拠は保存した。今後host Cargoはincrementalを止め、空き容量を確認して実行する。
構造preflightは対象repository非対応のためsourceを直接確認し、graphを実機動作の承認には使っていない。

## Build160の離し操作と後続の小修正

Build160の固定caseでは、本文全体の6DoF preview、MODE=MOVING、release前にnative配置を保存しない点まで到達した。
その直後、UP用の新しいray hitを取り直すfixtureがnullで失敗した。collision側の更新が一frame遅れた可能性はあるが、まだ原因を断定しない。
wrapperの製品設定復帰は成功した。native確定・入力復帰・最終停止をこの結果で合格にしない。

捕捉済みのdragを離す操作は、新しいhitを必須としないよう修正した。
固定caseはheldのまま`Scene.onNoIntersectionInput`でrayを外し、captureがDRAGGINGを維持したことを観測した後、
同じControllerのbuttonをreleasedへ変え、no-intersectionの通常UPを渡す。Tickによるtracked releaseも同じ経路へ進む。

| 観測 | 処理 |
| --- | --- |
| hover/stop、active Controller、primary保持中 | 同じcaptureを維持 |
| hover/stop、active Controller、primary解放済み | 捕捉したsource/downTimeのrelease。fresh owner/target/revision/pose guardは維持 |
| inactive Controller、Transform/pose不在、owner失効 | 取消 |
| 明示PointerEvent.Cancel | 保持状態に関係なく取消 |

`move.release_route`と固定caseの`move_release_route`で、SDK UP / off-handle UP / Controller状態によるUPの経路を記録する。
これはreleaseを観測した経路であり、配置成功を示す値ではない。従来どおりnative ACK→モデル→実SDK geometry→同targetへの入力復帰を別に確認する。
新しい固定caseの`off_handle_capture_retained`も、HMD/物理コントローラー受入れには読み替えない。
