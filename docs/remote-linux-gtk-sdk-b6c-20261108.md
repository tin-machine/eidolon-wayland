# NX18 B6c: 固定Linux GTKのSDK表示と合成入力

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
