# GTK固定fixtureをAndroid合成入力へ接続する

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
- Android intentはDUMP権限付きdebug receiverへ `case` と32hex `nonce` のみ。`gtk-input/gtk-dialog/gtk-resize/gtk-dialog-dismiss/gtk-close/gtk-cancel` 以外を受け付けない。新規debugファイルとdebug Manifestだけを追加し、MainActivity/NativeSessionHostは変更しない。
- Androidはruntime/scene/session/nonce/remote childのPID/start/groupを固定し、専用app_idが一意なwindowだけを選ぶ。pointerは既存Hostの不変InputTargetを使用する。key/resize/closeはtarget世代、epoch、focus tokenを固定したJNI commandを使い、現在の別windowへ選び直さない。非同期focusTerminal、自動retry、元footへの自動focus復帰は行わない。異なるfocus/epoch/ownerや余分な入力を検出したら失敗し、自分のpointer sourceだけを取消する。
- dialogは専用app_idの別toplevelとし、SHM commit、Wayland `set_parent`、追加SDK target/first frame、Cの親設定/open/response/destroyを確認する。popupとは区別する。AndroidはDIALOG_READYで待ち、hostがCとSDKのdialog描画を採取してから固定dismissを許可する。

Androidの操作完了とCのcase条件が両方成立してから、同じnonceの固定closeを送る。Cの自然終了0、全group/reap/pipe回収、main/dialogのwindowとSDK target退役、元footの継続、pressed解除を確認して初めてobservedとする。C失敗、signal、timeout、lease変更、回収失敗は元終了codeとともに保持する。host切断時はremoteのEOF/期限で所有groupだけをTERM/KILLする。

## 検証範囲

これは `target_bound_synthetic_native` 検査。Android物理keyboard ingress/filter、IME、実キーボード、HMD視認は `not_run/false` のまま。通常GUIやwidget-factory全機能の合格へ一般化しない。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_gtk_interaction_live.py -v
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_gtk_probe.py -v
```

Android側のbinding guardは `GtkFixtureBindingTest` でowner/UID/世代/重複app_id/欠けたtargetを確認する。APK/SDK/Quest検証は実機operatorが別途行う。
