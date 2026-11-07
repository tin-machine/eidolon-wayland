# 現行leaseでのSDL自然終了1回

`scripts/quest-run-sdl-probe`は、配備済みの固定C binary/runnerを現行Waypipe leaseで一度だけ実行する。
[読取りcapture](sdl-probe-live-capture-20261108.md)の条件に加え、既知のbinary・manifest SHAを実行前に照合する。
GUI catalogや製品helper modeを変更しない開発用operatorである。

```bash
scripts/quest-run-sdl-probe --serial "$EIDOLON_QUEST_SERIAL" \
  --run-id "$SDL_RUN_ID" --binary-sha256 "$SDL_BINARY_SHA256" \
  --manifest-sha256 "$SDL_MANIFEST_SHA256"
```

run IDは[配備ツール](sdl-probe-install-20261108.md)の結果から、binary SHAはBionic buildの既知結果から、
manifest SHAは配備時の既知結果から取得する。現在のremote fileを新しい基準へ自動採用しない。
任意PID/path/exec argv/envはCLIから受け付けない。hostログの保存先だけ`--artifact-root`で指定できる。

最初にnative source digestと現在APKのschema/bindingを照合し、同UIDのowner/child/登録root・2環境値を再captureする。
Termuxの固定`.local/state/eidolon/nx11/<run-id>`から、manifest/source/runner/binaryのregular file・owner・mode・size・SHAを検査する。
binaryはAArch64 ELFだけを受け、既存resultは再利用しない。

実行引数は固定の`--driver wayland --frames 120 --timeout 15`、`LIBGL_ALWAYS_SOFTWARE=1`とする。
remote wrapperは実際にspawnしたrunner handleを保持し、lease変更・stdin EOF・operator期限・signal時にrunnerへTERMを伝える。
runnerが自身のSDL groupを回収する猶予を設け、強制runner KILLが必要ならcleanupを確認済みにしない。
観測したshell/foot/Waypipe PIDへsignalを送らない。wrapperの結果は同run内の新規`operator-<probe-id>-report.json`にも保存する。

host側は同じjobで固定APK diagnosticsをpollし、追加window・target frame/content frame・退役と元のfoot継続をmetadata化する。
別のSDK jobを並行起動しない。新window一つ、内容frameの前進、退役、元の窓の存続を観測した場合だけ
`sdk_observation.status=observed`にする。これはHMD視認の合格ではない。

元runnerのrenderer error/exit/SHM判定は`remote_result.probe_result`へ保存し、operatorの取消・cleanupとは区別する。
元の`quest_sdk=not_run`を書き換えず、SDK観測は別項目に置く。本文・全env・生trace・logcatは保存しない。

この一回の成功を、製品アプリのStop/切断/lease expiry・反復・物理入力の受入れへ数えない。
それらはhelperの所有lifecycleへ統合する後続の検証で扱う。

PC検査:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.tests.test_sdl_probe_run -v
```
