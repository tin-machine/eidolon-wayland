# NX13: 登録terminal内の固定I/O fixture

2026-11-08。通常のfootが1枚開いた状態から、既存のtemporary definition APIで**専用の2枚目**を起動する。既存shellへのキー・文字注入、任意PID/pathの指定、既存配置の移動は行わない。Quest実機での受入れはこの変更の取り込み後に行う。

## 所有範囲と固定動作

`LaunchTerminal` の即時返却はenqueue受理だけである。固定caseはHostから返されたrequest IDに一致するlaunch receiptを使い、そこから新terminal ID、登録scope、window、renderのscene/generationを照合する。初期窓や別の同時起動の窓を対象にしない。

専用Pythonは `terminal-app` の登録後にexecされるため、同じPID/starttimeを観測rootとして保つ。引数は受け付けず、既存helperから渡されたsession/terminalの環境を確認する。専用子は独立session/process groupを作り、次の固定処理だけを行う。

1. 子を生存させたまま6秒待ち、baselineを確保する。
2. 自分で作った0700 directory内の0600 fileへ固定1MiBを書き、fsyncする。子を6秒生存させる。
3. 子を終了させ、WNOWAITでleaderを4秒保持する。この間にzombie/集合変化のpartialを観測する。
4. 自分のgroupを回収し、親rootだけで8秒待つ。partial→baseline→available zeroを観測した後、自然終了する。

通常約24秒、親・子に40秒のdeadlineを置く。取消/失敗時は未回収のdirect child leaderを保持したまま、作成したgroupだけを停止・waitする。PID 0/1/自分自身はsignal対象として拒否する。子のsession初期化失敗も親cleanupへ誤って落とさない。既存foot/tmuxのgroupへkillpgしない。回収が2秒で確認できない場合は`cleanup_pending`で失敗として返す。

stdoutは固定phase・経過時間・UID・終了/cleanup statusだけで、書込内容や実入力本文を保存しない。data file/directoryは正常・取消ともに回収する。childが親より長く残った場合も、control pipeのEOFまたは子deadlineで終了する。SIGKILL、kernel内の停止不能状態等を「確認済みcleanup」とは扱わない。

## 導入と開始

新debug APKへ統合した後、同じcheckoutのsourceをdebug Termuxへ導入する。

```bash
scripts/quest-install-io-workload --serial "$EIDOLON_QUEST_SERIAL"
```

配備先はTermux HOME配下の固定 `.local/state/eidolon/nx13-workload/fixture.py`。directory 0700、source 0600、same UID、symlink/hardlinkを拒否し、導入前後のSHA256を照合する。起動時もAPKに固定したSHA256とsource bytesを照合してからexecする。source更新時はAPKのpinも更新する。既存launcher helperや利用者のカタログ定義は変更しない。

ADBから受け取るのは固定caseと新しい32桁hex nonceだけで、path・argv・JSONを渡す入口はない。receiverはdebug版だけで、DUMP権限を要求する。Activityの起動を伴わない。

```bash
# CASE_NONCEには今回専用の新しい32桁hexを設定する。
adb -s "$EIDOLON_QUEST_SERIAL" shell am broadcast \
  -n io.github.tinmachine.eidolon/.IoWorkloadCommandReceiver \
  -a io.github.tinmachine.eidolon.IO_WORKLOAD_COMMAND \
  --es case io-workload --es nonce "$CASE_NONCE"
```

必要条件は、通常footが1枚、観測ON、builtinのcyan-orbit theme、同じsessionのランチャーが利用可能であること。case自身はテーマ・配置を変更しない。並行runは拒否し、最近64 nonceの再送を拒否する。同nonceの`io-workload-stop`で取消できる。

```bash
adb -s "$EIDOLON_QUEST_SERIAL" shell am broadcast \
  -n io.github.tinmachine.eidolon/.IoWorkloadCommandReceiver \
  -a io.github.tinmachine.eidolon.IO_WORKLOAD_COMMAND \
  --es case io-workload-stop --es nonce "$CASE_NONCE"
```

結果は既存private diagnosticsの`check`へ保存する。保存時はworkroot配下0700 directory/0600 fileを使い、公開記録へ個人IPや通常terminalの本文を転載しない。

## 証拠の読み方

caseは最大60秒、cleanup待ち最大10秒で終える。`epoch/sequence/terminal/window`で選んだ新しいsampleだけを最大128件記録する。同じsequenceをdraw待ちで再読しても積分へ再加算しない。

- `metric_positive / metric_partial / metric_zero_after_partial`: raw I/Oと同windowのnative Displayが一致し、正のwrite→集合/zombie partial→ゼロを観測したこと。
- `positive / partial / zero_after_partial`: さらに、そのtargetで最後に成功したdrawのworkload identity・I/O値・内側回転値を照合できたこと。
- `stopped_null`: 一時的な観測OFFでnative Displayがstopped/nullになったこと。`draw_stopped_null`は成功draw側もnullになったこと。
- `owned_window_removed / observation_restored`: 自分の窓だけを閉じ、元の観測ONへ戻したこと。復帰commandは一度だけ送る。runtime/session/scene喪失時は別対象へ付け替えず、未確認を残す。
- `observed_write_integral`: 有効sampleのwrite rate×intervalの観測合計。1MiBとの厳密一致を合格条件にしない。dirty/writebackや不完全区間の影響を保存した状態と数値で評価する。

`verified_scope=metrics_only`は描画までの成功を意味しない。`metrics_and_draw`でもHMD視認・回転の見え方・物理disk完了量は未確認である。`io_draw`は成功drawの値であり、同じ値の連続sampleは同じdrawを再利用し得る。各telemetry sequenceへの個別描画ACKではない。非表示中の新semanticを成功値として出さず、描画失敗時にも更新しない。

`fixture_cleanup_verified=false`は意図的に残す。window消失だけでは子groupの完全回収を証明しない。実機では固定fixtureの終端status、または新登録rootから一時的に確認した子identityの消失を別途確認する。`cleanup_unconfirmed`や復帰未確認は成功へ読み替えない。

## host検証

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -p test_io_workload.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p test_io_workload_artifact.py -v
cargo test --offline -p eidolon-render --lib io_draw_reports
```

自然終了・各phase取消・実SIGTERM・子setsid失敗・不正owner signal拒否の5件、private導入/hash変更/symlink/hardlink/APK bootstrap一致の3件、成功drawだけの診断更新の1件が成功した。debug KotlinはAndroid 36 jarとAPI stubでコンパイルを確認した。実Host/SDKを含むGradle・APK配備・Quest実行は統合担当で行い、この記録を実機合格の代用にしない。
