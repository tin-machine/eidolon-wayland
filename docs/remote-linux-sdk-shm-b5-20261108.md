# NX18 B5: 固定Linux SHMのSDK表示検査

[B4](remote-linux-apk-shm-b4-20261108.md)のvirtual clock検査とは別に、固定100フレームをQuestの通常SDK Surfaceへ接続するdebug caseを追加する。通常Linux GUI、任意アプリ、ランチャーの接続先切替は対象にしない。SSH capabilityは検査中・検査後とも `ProbeReady` のまま。

本checkpointは実装とhost検証の段階。実APK、実Linux bundle、SDK draw、HMDの合格を先取りしない。

## Hostが所有する一時runtime

B4の外部handleを後付けで交換せず、`NativeSessionHost` の単一workerが次の順序を管理する。

1. `debugBeginSdkProbe` がopaque ownerと通常handleをcaptureし、共通 `ProbeRuntimeRecovery` をclaimする。通常入力intentを失効し、既存IME/キー/SDK/nativeを通常の `stopRuntime` で回収する。
2. registryが空であることを確認してから、debug sourceSetの固定準備処理を実行する。正規保存済みv2 profileとpinsを読み、`files_dir` はB4と同じ短いprivate `lx`、通常profilesは空にする。`preferences_dir` は渡さず、無永続の既定設定を使う。
3. workerが `nativeCreate` し、直後にhandleをleaseへ記録してからReadyを待つ。通常のscene/XR/visibilityを送り、既存Host tickと `SpatialPanelAdapter` を継続する。
4. Hostがconnect requestを採番し、runtime/request/profileに束縛した `ProbeCredentialCapture` を準備してからconnectする。`credential_required` は同workerでprivate scopeを検査し、既存の鍵を一度だけ `provide_for` へ渡す。SDK lease中のイベントをTermux用credential分岐へfallbackしない。
5. `debugSdkProbeCommand` は同owner/runtimeに対する固定SDK pattern開始とdisconnectだけを受ける。通常GUIや任意commandの実行口にはしない。

lease中は他の `withStoppedRuntime` / 排他caseを共通owner gateで拒否する。通常connect、terminal、launcher、設定保存、テーマimport、配置変更などは `DebugSdkProductExecutor` で拒否する。検査前にqueueした操作も世代を照合し、復帰後の新runtimeへ遅れて再生しない。完了したtheme importの遅延callbackは単に捨てず、自分のscratch fileだけを回収する。

rawキー、pointer、IME、窓操作の入力targetも公開しない。画面の入力状態は固定Linux検査中であることを示す。旧focus/token/押下を復帰先へ再生せず、新しい入力epochで通常操作を再開する。

## 32×32 patternと装飾

固定producerは32×32のpatternを送る。通常テーマの最低1280canvasをそのまま適用すると、本文との物理比率から背景が巨大化し得る。今回のprivate runtimeは無永続preferencesの既定Theme Offを使い、caseでもtheme未採用・panel decorationなし・canvasなしを確認する。

通常のtheme/layout/profileやTermux journalは変更しない。準備時に通常profile、workspace layout、theme state、旧Termux pending journalのdigestをworker上でcaptureし、正常復帰前に同じworkerで照合する。設定本文、鍵、pin、画素は診断へ含めない。

## 固定caseと受入れ

`DebugCommandActivity` のDUMP境界で `linux-shm-sdk` / `linux-shm-sdk-stop` を追加する。intentはcaseと32桁hex nonceだけ。B4と同じ事前profile/pin配置を使い、helper nonceは外部nonceの先頭16桁。任意host/path/argv/JSONをintentに含めない。

```sh
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case linux-shm-sdk --es nonce "$EIDOLON_PROBE_NONCE"

# 停止、または回収未確認ownerの再回収。同じnonceを使う。
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case linux-shm-sdk-stop --es nonce "$EIDOLON_PROBE_NONCE"
```

新native command `linux_shm_sdk_probe` は `Pattern, virtual_clock=false` を使用し、通常Board/panel/render経路へ接続する。B4の `linux_shm_probe` は引き続きvirtual clock専用。新commandは実 `debug-fixtures` featureが無いcoreで拒否し、case本体はdebug sourceSetだけに置く。

SDK検査は次を分けて記録する。

- source: 同session/nonceの実受信100画素SHA-256、copies/callbacks/releases各100、`callback_clock=target-driven`。
- native描画: 同じfull targetの成功draw `frames` と `content_frame`。途中でtargetが変わる、counterが戻る、別windowの値が混ざる場合は拒否する。
- SDK: 同targetのbinding、実Entity、panel適用ACK、実absolute poseとworkspace applied poseの一致。複数時点でdrawが前進することを要求する。
- remote: pin/tools確認、forward登録と取消、固定pattern成功、child/group/pipe/channel回収、Linux journal消失。
- local: disconnect ACK、owned connectionゼロ、AppSession/SHM/memfd/directory回収、Board/SDK bindingの回収と旧window消失。

実frame callbackは通常の `Session::render_tick` が同window/content frameの提示を確認してから返す。transportの自己申告counterだけをSDK描画の証拠にしない。100frame後にproducerの窓は自然終了するため、caseは途中のfull targetをcaptureしてsource/描画/SDK証拠を同じownerへ結合する。

初回connecting中はscene/visibility反映をdeadline内で待つ。pattern開始後のscene変更やXR/Activityの離脱は成功扱いせず、終了回収へ進む。全体90秒、Host停止は既存10秒、通常SDK診断画面の復帰待ちは15秒。自動再送は行わない。

## 失敗と通常画面への復帰

`debugEndSdkProbe` はcredential captureを閉じ、旧runtimeのSDK/native/SSH owner回収を確認してから通常configで新handleを作る。復帰先の `nativeCreate` が成功した直後にもそのhandleをleaseへ保存する。Ready/Idleと通常設定digestを確認しても、ここではRESTORING/ownerを保持する。

UIで可視/FOCUSED/captureしたsceneに属する同一診断targetの3frame以上、実SDK bindingとの一致、旧windowなしを確認した後、`debugFinalizeSdkProbe` がHost workerで最新のactivity/XR/scene/同target/SDK登録を再確認し、初めてleaseを解放する。過去の累積3frameだけで復帰成功にしない。

停止や復帰に失敗した場合はownerとhandleを保持し、`cleanup_unconfirmed` とする。同nonceの明示stopで回収だけを再試行する。通常runtimeの再作成や別caseへの切替を成功とみなさない。`nativeCreate=0` はregistry空の証拠がある場合だけ復帰へ進む。元の通常runtime停止自体が失敗しprivate設定準備に到達していない場合は、未適用のprivate設定を復元したという主張をしない。

SSH/local owner回収とremote childのreceipt確認は別に扱う。失敗/cancelでLinux journalが残れば、それを保持してB4の明示recoveryへ渡す。通常Termux画面へ戻れても `remote_cleanup_confirmed=false` のままで、remote回収や検査の成功にはしない。

SDK ownerの制御には `SdkCaseEnvironment` の既存stop/digest/復帰確認の考え方を使うが、そのwrapper自体は再利用しない。通常configと無条件restartを前提とするため、v2 scopeと回収未確認ownerを保持する専用leaseが必要になる。

最終 `pass` には `product_settings_untouched`、`normal_runtime_restored`、`normal_sdk_restored`、`owner_lease_released` が必要。途中のsource/draw観測だけを最終合格にしない。HMDの見え方と物理入力は未検証のまま記録する。

## checkpoint検証

- pure owner/queue 7件PASS。二重claim、旧owner/handle、設定切替前の遅延操作、検査中操作、create=0、回収timeout、復帰先handleの停止失敗を含む。
- Host、新debug case、SDK入口を既存SDK 0.15/API36/Compose jar・既存classへ限定compileしてPASS。Gradle/APKの代用とはしない。
- native feature付きcore all-targets clippy `-D warnings` PASS。固定wireとfeatureなし実coreの拒否fixture各1件PASS。
- 構造preflightはrepository未対応。直接source確認を行い、private作業記録へ結果を保存する。

次はrootが統合APKを生成し、B4の実SHM経路と別nonceでB5 SDK caseを実行する。SDK/GLの成功draw、通常設定保持、回収を確認してからHMDでの表示確認へ進む。
