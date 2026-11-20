# NX18 B6c: 固定Linux GTKのSDK表示と合成入力

Build173のinput `73e4afc891ff8327ae9c69ed405f28f2` はPASSした。固定文字/削除/button、SHM/SDK描画、
全target退役、remote journal/子group回収、通常SDK復帰とlease解放を同jobで確認した。
dialog `ad6dc53590ea7a77988b35056dc834c6` は新dialogへ入力する前の `PROBE_INPUT_TARGET_LOST` で停止した。
SDK bindingは2件、counterは期待0key/2button、epochも不変で、continuityは `WAIT_TARGET_READY/failed=false` だった。
既存trialに追加するdialogにも、Host側の最初の入力準備を待つ必要がある。

後続修正は、初回に観測したdialogのfull target/geometry/epoch/counter/focusを固定し、最大3秒/512pollで待つ。
同じnative Mapped windowと適用panel、同じtrialの所有を毎回確認し、target/geometry/入力/focus変更と
恒久的なcontinuity失敗は取消とする。新targetのSDK/geometry/frame条件がそろってから一度だけclickへ進む。
待機を理由に別targetへ追従したり、通常raw/Product gateやnative最終配送条件を変えたりしない。
純粋48件（新dialog待機6件を含む）と実SDK19sourceの限定型検査を行い、APKの実機結果は別途記録する。

Build172では初回adapter cache待ちを17poll・438ms観測してcaptureに成功した。
`wait_adapter_observed=true`、producer frame待ちはfalseで、初回準備待ちの修正は実機で成立した。
次の `WAIT_FOCUS` では `PROBE_OTHER_INPUT` で停止した。失敗時の実counter値が当時は記録されていないため、
具体的なDOWN/UPの到着順やcounter値までは断定しない。GTK input/dialogの総合合格は未確認のままとする。

修正後はLinuxとTermuxの両固定caseで `GtkInputProgress` を共用する。
focus/command ACK、native key・button counter、GTK callbackは別々に公開されるため、
各操作の固定下限〜期待値の途中だけを最大6秒待つ。期待値と両pressed=0が揃うまで次の入力を送らない。
余分なcounter、逆行、無関係なheld input、時刻逆行、期限ちょうど以降の完了は拒否する。
待機中の観測値を新baselineへ変更しない。dialog Escapeの2イベントとpressed解除も確認し、
既存のseal後の自己退役だけに許されたepoch変更以外は許可しない。
`input_progress` は最後に判断したsnapshotの期待/観測counter・epoch・focus window IDとtoken一致bool、
固定reasonだけを保存する。本文・key列・focus token自体は記録しない。

別の静的レビューで、B6だけが各key/DOWN/UPの配送直前に最新producer frameの描画完了を要求していた。
通常のHost/native入力は、適用済みfull targetと初回draw、epoch、可視性、focus tokenを確認するが、
毎回のrepaint完了を要求しない。これは172の失敗原因と断定せず、通常経路との差として修正する。
初回のframe同期を維持したうえで、同期済みtargetのlogical/pixel/physical size・pose・themeを固定する。
同geometryのcontent更新だけなら継続し、owner/runtime/session/scene/epoch/full target、SDK所有、
実geometry適用、native interactive gateは各配送の最終直列化境界で再確認する。
dialogは別targetとして初回同期を要求し、2targetを超える取得やgeometry/世代変更への自動追従は拒否する。
`input_target_continuity` は固定reason、target件数、repaint判定件数のみで、pose値を記録しない。

この変更はpure Kotlin 42件（新規10件を含む）、GTK operator 34件、実SDK/APIを使う18 sourceの限定型検査で成功した。
これらは次の同source APKでのLinux/Termux input/dialog実機検証と分ける。
Termuxの受領schemaには必須 `input_progress`（resizeはnull）を追加し、operatorも同時に更新する。
旧proofは旧APK/schemaのまま保持し、新schemaの合格へ読み替えない。

Build171の初回inputは `PROBE_INPUT_UNAVAILABLE` で停止した。別nonceのB4 SHM/SDK回帰はともに成功したが、
GTKは初回SDK draw一回、操作stepなしの段階であり、GUI合成入力は未合格として残す。
caseは現在のadapterを確認する一方、Hostの入力gateは最大1秒周期のadapter snapshotと最新producer frameの描画を要求する。
この準備順の差と整合するため、初回だけ有限に待つ修正を進める。記録だけで実際に拒否したpredicateは断定しない。
待機中も最初のfull target・epoch・入力intent・counterを固定し、外部操作や世代変更を新captureへ取り込まない。

失敗後の通常SDK復帰とlease解放を確認し、同profile/pins/nonceで明示recoverを行った。
結果はjournal無しの `not_required` で、queried/receipt_verified/remote_cleanup_confirmedはfalseのまま保持した。
別途、同UID・同nonceのhost receiptはStop/cleaned/pipes_closed、GTK forced stopであり、active stateは残っていなかった。
通常停止の回収証拠とGTK操作成功を分け、初回失敗を成功へ読み替えない。

[B6b](remote-linux-gtk-transport-b6b-20261108.md)の認証済みSSH/GTK reportを、
[B5](remote-linux-sdk-shm-b5-20261108.md)の一時SDK runtime leaseへ接続する。
このcheckpointの入口は `input` と `dialog` の二つ。通常Linux GUI/catalog、任意コマンド、
接続先切替UI、IME、物理入力を有効化するものではない。

## 保存と実行の範囲

既存の正規v2 profile stagingに加え、同じアプリprivate `remote-probe` directoryへ
`linux-gtk-pins.pending.json` を事前配置する。ADB intentにhost/path/argv/JSONを渡さない。
`DebugLinuxGtkPinStore` が明示import時に4KiB以内・正確なfield型・duplicate key拒否・
profile binding/revision一致・同UIDの0700 directory/0600 regular single-link fileを確認し、
排他的temporary fileへwrite/fsync/readback後、`linux-gtk-pins.json` へatomic保存する。
既存SHM pins/Termux profile/journalは上書きしない。

```json
{
  "schema": 1,
  "profile_binding": "<保存v2 profileの64hex binding>",
  "profile_revision": "<decimal string>",
  "bundle_sha256": "<base manifestの64hex>",
  "waypipe_sha256": "<固定Waypipe binaryの64hex>",
  "fixture_sha256": "<base SHM fixtureの64hex>",
  "gtk_bundle_sha256": "<GTK拡張manifestの64hex>",
  "source_sha256": "<APKと一致する固定Cの64hex>",
  "binary_sha256": "<Linux GTK binaryの64hex>",
  "gtk_version": "3.24.52"
}
```

例はschema説明用。builderが生成した固定bundleとsource digestが一致する値をoperatorが確認する。
sourceの正本照合はnative `GtkPins`、remoteのmanifest/executable照合は固定bootstrap/helperが行う。
Android保存器だけの合格でremote binary実行の成功にはしない。

```sh
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case linux-gtk-input --es nonce "$EIDOLON_PROBE_NONCE"

# dialogは別のnonce。回収完了後に開始する。
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case linux-gtk-dialog --es nonce "$EIDOLON_PROBE_NONCE"

# 同nonceの明示停止・回収再試行
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case linux-gtk-stop --es nonce "$EIDOLON_PROBE_NONCE"
```

回収記録が残る場合は、同じprofile/pins/case/nonceで `linux-gtk-input-recover` または
`linux-gtk-dialog-recover` を明示実行する。起動検査と同じADB形でcaseだけを変更する。
回収入口はGUIを生成せず、GTK専用journalとreceiptを照合してdisconnect・local owner回収・通常SDK復帰を行う。
同case/nonceの再試行は最大3回、保持captureは64件で、上限時に古い記録を追い出して再許可しない。
`not_required` はjournal無しの終端であり、`remote_cleanup_confirmed=false`、GUI/SDK検証はfalseのまま。
不一致やpendingを新profileへ付け替えて成功と見なさない。

外部nonceは32hex。helper nonceは先頭16hex、固定Cのrun_idはその16hexを2回連結する。
main app_idとdialog app_idもこのrun_idから固定する。case/nonce replayは既存debug SDK入口と同じ
有限履歴・同時一件の制限を使う。

## 固定leaseだけに与える入力権限

Hostは作成前に固定modeをcaptureし、GTK commandをenqueueする同じ直列化境界で
owner/runtime/session/helper nonce/case/sceneを確定する。このscopeからtrialを作れるのは一回だけ。
通常 `inputTarget`、keyboard focus cache、rawキー、IME、設定/窓操作の `blocksProduct` は維持する。

GTK専用trialは既存 `InputIntentSerial` の比較からenqueueまでの同じmonitorを通す。
入力直前に最新nativeのReady・可視FOCUSED・scene・app/session/nonce/mode・Mapped app_id、
full target・適用panel・最新content frameの描画・同SDK ownerを確認する。
文字キーは同epoch/focus tokenに束縛し、pointerの押下だけをowned intentとして前進させる。
外部intent、scene/runtime変更、対象喪失、拒否で旧trialを失効し、別windowへの付け替えや自動再送をしない。
終了開始時はscopeを先に失効させ、既存B5のstop/destroyと通常runtime復帰へ進む。

## input/dialogの受入れ

- input: helperが報告するentry/button矩形とnative logical sizeを照合し、矩形中心をclick。
  focus/tokenを確認して固定A/Backspace各down/upを送り、nativeの配送4件とGTKの変更2件・
  token/delete各flagを照合する。button click後にGTKのcompleteと全request ACKを待ち、mainをcloseする。
- dialog: mainのbuttonをclickし、別app_id/full targetの実SDK表示を待つ。dialogをfocusし、
  固定Escape down/upで閉じる。GTKのopened/closed/response各1とparent flagを照合してからmainをcloseする。
  parentはこの段階では固定GTKのreportであり、native xdg parent fieldの独立検証とは区別する。
- 全caseでnative SHM copies/callbacks/releases、各full targetの成功draw/content frameとpose適用を確認する。
  mainの複数時点のdraw前進、dialogの独立SDK targetを要求する。画素hash100やvirtual clockの結果を流用しない。
- 最終成功はGTK helperの自然終了・EOF/exit・remote group/pipe/forward/channel回収・専用journal clear、
  native panel/Board/SDK ownerの退役receipt、disconnect・scope解除・local resourceゼロを必要とする。
- B5と同じ通常設定digest、旧runtime destroy、新通常runtime Ready/Idle、可視FOCUSEDの同sceneでの
  fresh診断SDK draw、最終owner lease解放まで待つ。中間の `normal_runtime_restored` では終了しない。

全体90秒、既存Host停止10秒、通常SDK復帰15秒の有限deadline。取消や失敗でも回収へ進む。
回収不明時はowner/handleを保持し `cleanup_unconfirmed`、同nonceのstopで再試行できる。
remote journalが残った場合も同じprofile/pins/case/nonceを保持し、通常画面復帰をremote回収成功に読み替えない。

## checkpointと残り

固定pins・opaque scope・回収の再試行上限と分類、共通入力/resize ownerを合わせたpure 34件がPASS。
既存SDK/APIへの限定型検査は、通常GTK・SDK adapter・Host・新caseの14 sourceを同時に対象としてPASS。
APKでのSSH/GTK実行、SDK描画、合成入力、退役と通常復帰はrootの次版実機検証まで未確認。
HMDの見え方と物理操作は最後の別gateとして残す。

resizeは既存GTKのowned replacement APIへ接続する後続segmentとする。
request_id/旧full target、configure/ACK/commit serial、サイズ、旧SDK回収、新描画・geometryを確認した
一回の交換だけでepoch/targetを更新する。同じ処理を別実装したり、最新epochへ盲目的に追従したりしない。

## Build171の初回入力準備と有限待機

Build171の実Linux GTK inputは最初のSDK draw/poseを観測した後、入力trialを取得できず終了した。
この実行ではGTK入力の成功は未確認。失敗後のStopはremote receiptのcleaned/pipes closedで回収を確認し、
同じprofile/pins/nonceの明示recoverはjournal無しの `not_required` だった。
成功経路専用の `remote_cleanup_confirmed=false` と、Stop後の回収状態を区別する。

caseは `adapter.metrics()` の現在値を読んでいた一方、Hostの入力gateは最大1秒間隔で公開する
adapter cacheと、producer最新frameまでの描画を要求していた。初回drawの直後はこのgateが未成立でもよい。
当時の証拠には拒否predicate別の情報がなく、実際にどのgateが拒否したかまでは断定しない。

修正後は初回full target/runtime/session/scene/input epoch/intent/native key・button数/focusを固定する。
InputIntentSerialのprovisional Trialを先に作るため、counterを増やさない物理キーのinvalidateも失効として検出する。
adapter cacheとproducer frameだけを最大3秒・512pollで待ち、owner/target/inputの変化や期限切れは永久取消する。
待機のために新targetや新epochへ付け替えない。Hostの同じmonitor内で最終検査した後だけ、
scopeの一回claimと有効なDebugInputTrialへの移行を行う。

`input_preparation` は固定reason、adapter/frame readiness、待機を観測したbool、poll件数、上限付き経過時間のみ。
正常なcache遅延、producer frame待ち、外部intent、counterを変えないinvalidate、exact deadline、
旧target/epoch/counter/focusの再採用拒否をpure fixturesで確認する。新しい実APKでの成功は別の受入れ条件である。

## 旧bundleからの更新順序と通常GUIの残り

1. 旧版の実行ownerとpending journalを先に確認する。pendingがあれば旧APK/helper/bundle/pinsと
   同profile identityで明示recoverし、未確認のまま新bundle/profileへ進めない。
2. 公開可能な旧Linux bundleと導入APK/sourceの識別情報を保全する。隔離sshdの秘密鍵とAndroidの
   private identityは既存のsecret runtime内に保持し、作業領域へcopy/tarしない。作業記録には既存path・
   public identityのhash・permission・起動recipeだけを残す。Androidの非secret profile/pins/journalは
   固定allowlistの各fileをschema・所有権・symlink・size検査後に個別保存する。directory全体をarchiveせず、
   通常credentials本文を記録へ持ち込まない。接続metadataは非公開とし、通常sshdは変更しない。
3. 旧manifestで固定Waypipe/SHM fixtureを照合し、このcheckoutの新helperと組み合わせた新base manifestを作る。
   全owner停止中に固定bundleを切り替え、`scripts/build-linux-gtk-bundle` で固定C/GTK binary/追加manifestを生成する。
4. 同profile binding/revisionのまま新base/GTK pinsを生成し、新APK/helper/Cのsource一致を確認して配備する。
   新profileで旧journalが見えなくなったことを「回収済み」としない。
5. 別nonceでB4/B5の回帰、GTK input/dialog、同identity回収と通常SDK復帰を確認する。
   新版実行後にpendingが残った場合は新版一式を保持して先に回収し、旧版へのrollbackを先行しない。

正確な保存先・既存隔離sshd状態・値を含む手順はprivate checkpointの `bundle-transition-171.md` に残す。

通常Linux GUI/launcherへは、次の段階が必要である。

- Linux helperの固定foot/許可したアプリ定義、Wayland能力確認、起動と回収receiptをbackend別に実装する。
  固定GTKの成功だけで任意shell/catalog/通常GUIのcapabilityを解放しない。
- v2 profile選択をHost/runtime/credential actorへ配線し、接続先変更時の入力/SDK/app/forward所有者と
  backend別journalを完全に回収する。既存Termux v1の移行は明示操作とし、保存値を自動置換しない。
- launcherの定義・file/.desktop解決を接続先profile/sessionへ束縛する。ローカルTermux pathをLinuxへ流用せず、
  profile名・接続状態・未対応backendをUIで区別する。
- 複数app/window、再接続、9枠上限、子process/workload、clipboard/IMEと終了時の押下解除を実経路で検証する。
  X11/XwaylandやGPU/EGL/SDLの成立は、今回のGTK SHMと別の条件として確認する。
