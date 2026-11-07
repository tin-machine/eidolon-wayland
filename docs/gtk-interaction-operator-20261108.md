# GTK固定fixtureをAndroid合成入力へ接続する

Build173候補の[共通入力進捗判定](remote-linux-gtk-sdk-b6c-20261108.md)では、focus成立後も
DOWN/UPのcounterが期待値まで公開されるのを待つ。新checkの `input_progress` は必須fieldで、
固定counter/epoch/focus IDと一致boolのみ。resizeは従来のowned replacementを使うためnullとなる。
operatorも同じsourceへ更新し、旧APK/schemaの証拠を新しい合格として再解釈しない。

このoperatorは既存のfoot/Waypipe leaseを維持したまま、[固定GTK fixture](gtk-interaction-fixture-20261108.md) の input/dialog/resize を一回ずつ確認する。通常GUI catalogやhelper modeは変更しない。専用C/build sourceと実機で作成したbinary/manifestのhost pinを使う。

## 実行順序

1. 新debug receiver/caseを含むAPKをoperatorがbuild/installし、そのsourceとAPK診断のdigestが一致することを確認する。通常footを1枚起動し、workload root登録を有効にしておく。
2. prepare installer/buildのprivate artifactから配備run-id、manifest SHA、binary SHAを取得する。現remote binaryから実行直前に期待値を作らない。
3. 次の固定CLIを実行する。ADB対象、case、既知配備ID、整合性pin、保存先以外のargv/env/PID/target/text/座標は指定できない。

```sh
scripts/quest-run-gtk-interaction \
  --serial "$EIDOLON_QUEST_SERIAL" --case input \
  --run-id "$GTK_FIXTURE_INSTALL_RUN_ID" \
  --manifest-sha256 "$GTK_FIXTURE_MANIFEST_SHA256" \
  --binary-sha256 "$GTK_FIXTURE_BINARY_SHA256" \
  --artifact-root "$EIDOLON_TASK_ROOT/gtk-interaction"
```

inputとresizeは独立に実行できる。dialogは baseline foot + fixture main + dialog の3枚が同時に描画・入力可能である必要がある。2枚上限のAPKでは欠けたdialog targetを6秒で `DIALOG_TARGET_UNAVAILABLE`/unsupportedとし、元footを閉じて回避しない。複数window対応の後に検証する。

## 同じjobで対応付ける証拠

- APK source/process/handle/session、現行helper leaseのUID/PID/starttime/登録rootと所有UDS、Wayland用2envを既存capture契約で照合する。manifest/C/build source、binary、build receipt、gtk3 package3.24.52、同梱widget-factory pinを検査する。新run/probe IDを使い、旧sourceやnonceを流用しない。
- Cのstdoutは固定schemaのみ受け、実spawn PID/UID、実行nonce、case、専用app_idと一致させる。入力本文やrawログは保存しない。GTKのcallback counter/flagsとgeometry、Androidの固定case受領、SDKのMapped window/target key/first frame/configureを同じhost loopで観測する。
- Entryは論理座標 `(width/2,84)`、固定buttonは `(width/2,height-68)` のみ。hostはCが報告した矩形とSDK logical sizeの一致、およびその固定点が矩形内にあることを確認してからAndroidへ開始を送る。未知の配置を推測してクリックしない。inputのclose前には操作開始前より進んだSDK content frameも要求する。
- Android intentはDUMP権限付きdebug receiverへ `case` と32hex `nonce` のみ。`gtk-input/gtk-dialog/gtk-resize/gtk-dialog-dismiss/gtk-close/gtk-cancel` 以外を受け付けない。MainActivityは変更せず、Hostにはdebug capture/conditional-dispatchと通常入力の共通直列化境界、SDK Adapterには回収receiptを追加する。
- Androidはruntime/scene/session/nonce/remote childのPID/start/groupを固定し、専用app_idが一意なwindowだけを選ぶ。pointer/key/resizeはHostの不変InputTargetとinput intent trialを使用する。通常pointer/key/resetおよび遅延focusの最終照合からnative enqueueまでを同じmonitorで直列化し、snapshotが古い場合も通常操作の後から検証入力がfocusを奪わない。captureは以前の遅延focus ticketを失効させ、自分のpointer downだけ明示的にtrialのcounterを進める。通常操作・focus/epoch/owner喪失は終端まで失効を保持し、現在epoch/世代への盲追従は行わない。resize自身による交換は後述の一回tokenで限定する。keyはfocus tokenも固定する。
- 最後の入力後にtrialをsealし、それ以降pointer/key/resizeを送れなくする。seal後は事前指定した自分のfull targetがSDK retirementへ入ったresetのみ自己cleanupとして数え、入力を再開しない。別targetの退役や通常reset/キーは失効させる。closeは捕捉済みmain targetだけを対象とする。非同期focusTerminal、自動retry、元footへの自動focus復帰は行わない。異なるfocus/epoch/ownerや余分な入力を検出したら失敗し、自分のpointer sourceだけを取消する。
- dialogは専用app_idの別toplevelとし、SHM commit、Wayland `set_parent`、追加SDK target/first frame、Cの親設定/open/response/destroyを確認する。popupとは区別する。AndroidはDIALOG_READYで待ち、hostがCとSDKのdialog描画を採取してから固定dismissを許可する。

Androidの操作完了とCのcase条件が両方成立してから、同じnonceの固定closeを送る。Cの自然終了0、全group/reap/pipe回収、main/dialogのwindowとSDK target退役、元footの継続、pressed解除を確認して初めてobservedとする。さらにmain/dialogの全full targetについてSDK disposeとbinding releaseの成功receiptを要求する。native panels/Board予約も消え、SDK binding数が元foot1件へ戻ることを確認する。hostはspawn前からfootのfull target/Entity/listener/control/mode ownerを採取し、最後も同じ所有者であること、SDK Entity/listener/装飾/control/modeの所有counterが開始前へ戻ることを照合する。Mapped/render対象が消えたのみ、disposal失敗、BoardRetiring残存、mode texture/listener残存は成功にならない。SDKの1秒周期のpublicationを待つ回収期限は自然終了後最大8秒とする。C失敗、signal、timeout、lease変更、回収失敗は元終了codeとともに保持する。host切断時はremoteのEOF/期限で所有groupだけをTERM/KILLする。

## 検証範囲

これは `target_bound_synthetic_native` 検査。Android物理keyboard ingress/filter、IME、実キーボード、HMD視認は `not_run/false` のまま。通常GUIやwidget-factory全機能の合格へ一般化しない。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_gtk_interaction_live.py -v
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_gtk_probe.py -v
```

Android側は `GtkFixtureBindingTest` でowner/UID/世代/重複app_id/欠けたtarget/回収receipt/Board残存を、`InputIntentSerialTest` で通常操作と遅いdebug dispatchの両順序・seal後入力禁止・自己退役と外部resetの区別を確認する。APK/SDK/Quest検証は実機operatorが別途行う。

8b547ddの静的レビューで通常footクリック後にGTKの古いfocus snapshotから遅いpointerをenqueueできる競合と、Mapped消滅だけの回収判定が見つかった。実機へ採用する前に共通直列化と資源gateを補った。C/source/binary/manifestは変更していないため既知の配備pinを維持する。ここで記したfixture結果はホストでの検査であり、GTK input/dialog/resizeのQuest合格は別途必要である。

## Build169の実機結果と保存順の扱い

GTK3.24.52の `gdk_wayland_window_set_application_id` はxdg toplevel生成前には値を保持しなかった。
初回実行ではCが報告するIDとwire上の既定IDが異なり、入力開始前に35秒timeoutとなった。
`g_set_prgname` をGTK初期化前へ置き、main/dialogの明示setterをshow後へ移して、
実Waylandで初回SHM前のID/parent順序を確認した。新source/manifest/binaryを新runへ配備し、旧runを上書きしていない。

- input: nonce `ff2319c0f4ced0c3eaca267f4fecf9c4` はobserved。Entryへの固定入力/削除、
  button callback、SHM/SDK draw、検査窓のSDK退役、元foot継続を確認した。
- dialog: 初回 `1d44d4ce15161a591ed0d71d01503ead` はホストが未観測targetとして拒否した。
  Android checkの保存がruntime snapshotより先行し得るため、同scopeの受領を最大2秒/32件だけ保留する。
  実SDK drawを伴う同一full targetを別snapshotで観測してから採用し、世代違い・別scope・期限切れは拒否する。
  修正後 `1317b6924c000a1b71c2e2806c78cbae` はobservedで、別dialog/parent/合成focus/Escape/退役を確認した。
- resize: `0d228a8e9200f5a5dbcf4cf2aa9248df` はGTK_INTENT_LOST。
  自分のlogical resizeでもSDK producerの交換と退役resetが発生した。epochを盲追従せず、
  command ACKと一回だけのowned replacementを照合する後続修正が必要。
  この失敗でも検査窓/remote子group/pipeの回収と元foot継続は確認した。

取消後は有限時間pipeをdrainし、remoteのreaped/group/pipes/cleanup受領を保存する。
失敗やtimeout後の遅い完了を成功へ昇格せず、footer欠落を回収成功にしない。
full targetの保存順/期限/別owner拒否と、取消後footer・部分行・出力上限を含むhost24件が成功した。
物理controller/keyboard、かな漢字変換、HMD視認は未検証として維持する。


## resize自身によるSDK producer交換

Build169で通常のlogical resizeでもproducer canvasが変わり、旧SDK bindingの退役resetがtrialを失効させた。
この実測失敗を保存したまま、固定resize caseだけに一回の交換権限を追加する。通常UIのgeometry操作はtrialを取り消す。
共有の直列化境界で通常のpose/physical/logical size操作を失効させ、scene/visibility/XR喪失も同じ境界へ置く。
旧runtimeのSDK退役は現在runtimeへresetを送らない。runtimeとfull targetが一致した退役だけを照合する。

Build170（source `f5721f3`、digest `d3b8e9f32b51a9504c096bdb12851c999782fd7908fba566416cfd6563bd0b8a`）で実機を再検証した。
resize `d376b75c9a6cc143379f76af7a11a4b8` はobservedで、session519/window2のgeneration4→6→8、
epoch11→12→13をそれぞれ要求9400001/9400002へ対応付けた。800×500と640×360の両方でconfigure/ACK/commit、
進んだSHM/SDK描画と旧targetの退役が成立し、入力配送counterは0のままだった。
Cは自然終了0、子group/reap/pipe回収、全検査targetのSDK dispose/binding releaseとBoard解放、元footの同一資源への復帰を確認した。
同APKのinput `12288fa5c5b580ed782ab4b0aeada96d`、dialog `46c06183617e9d16b0afc97cfaba108e` もobservedだった。
debugのbuild/lint/packageと単体182件、releaseのbuild/lint/packageと単体141件が成功した。
物理入力・IME・HMDはこの結果に含めない。Build169のresize失敗と回収記録は保持する。

1. `debugBeginInputResize` は捕捉runtime/session/window/scene/generation、input epoch、入力counter、
   leaseのnonce/PID/start/group、theme revision、physical sizeとposeを固定し、通常の `resize_logical` を一度enqueueする。
   nativeの `app.windows[].resize.request` はそのrequest IDと元のfull targetを保持する。
   tokenは各10秒、trial内で最大2個、同時には1個とし、BUSYや期限切れを再送しない。
2. pending中はpointer/keyのdispatchとsealを禁止する。捕捉した旧runtime/full targetの退役resetだけを一回消費し、
   外部key/pointer/focus/reset、別target退役、二度目の旧退役、scene交換は永久取消のままにする。
3. native command ACKに加え、同request/元target、要求・configure・commit・actual logical size、applied phase、
   configure/ACK/commit serial、進んだproducer frameを確認する。serialは不透明なu32であり、
   alias configureや後続repaintのため3値の数値順・相互一致を要求しない。適用世代の成立はnative ResizeStateが判定する。
4. 新世代は同runtime/session/window/sceneだけで、旧世代より大きいIDを要求する。IDは他commandと共有するため隣接番号とは限らない。
   旧native panel/render、旧SDK bindingの消滅と成功回収receipt、新SDK listener/controlとframe、content pixels、
   pose/physical geometry ACK、既存Host inputTargetの最新epochを照合する。旧SDK退役resetとSurfaceLostによるepoch差1〜2だけを許し、
   raw focusを送り直したり入力gateを解除したりしない。同canvasで交換不要の場合は同じtarget/epochだけを許す。
5. Hostが返したimmutableな受領だけからcaseのtarget/epochを更新する。800×500の採用後に640×360を一度要求し、
   2回目の採用後にsealする。operatorは旧target→新target、epoch差、単調なrequest ID、固定sizeとserial型を独立に検査し、
   全full targetの観測・最終回収、Cのconfigure/size counter、元foot継続を従来どおり要求する。

checkの `resize_replacement` は固定reason、request ID、boolによるreadiness、epoch/generationだけを公開する。
入力本文、raw key、trace、画像、自由なエラー文字列を加えない。`large` / `returned` の受領は
元target・request・実serial・固定sizeを含む。このresize checkpoint時点ではinput/dialogのcheck schemaは変更しなかった。

hostではResizeStateのrequest/alias/無効要求3回帰を含む8件、既存InputIntentSerial9件と置換token14件、
operatorの既存24件と新しい受領検査5件が成功した。実SDKを使う限定Kotlin型検査も成功した。
このcheckpoint単独ではQuest resize、Linux leaseへの転用、物理keyboard/controller、IME、HMD視認の合格とはしない。
同じsourceでAPKを再build/installし、固定resize operatorの全gateと回収を実機で再確認する。
