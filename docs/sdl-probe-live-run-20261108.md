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
`--variant baseline`（既定）は追加のgraphics envを持たず、`--variant egl-wayland`は
`EGL_PLATFORM=wayland`だけを追加する固定対照である。選択値とこの固定graphics envを
host request/result・remote events/reportへ記録し、返却された条件との不一致は失敗にする。
親processの`EGL_PLATFORM`や自由envを継承しない。

Build152の最初の自然終了runでは、OpenGL rendererの失敗復元中にWayland windowのNULL破棄でSIGSEGVとなった。
Termux MesaはX11をbuild時のnative platformにする一方、native displayのWayland自動判定もあるため、
これだけでplatform選択が原因とは断定しない。同じbinary/manifestで次の一項対照を行える。

```bash
scripts/quest-run-sdl-probe --serial "$EIDOLON_QUEST_SERIAL" \
  --run-id "$SDL_RUN_ID" --binary-sha256 "$SDL_BINARY_SHA256" \
  --manifest-sha256 "$SDL_MANIFEST_SHA256" --variant egl-wayland
```

この対照はC・配備runner・manifestを変更しないので、既知hashの同じrun IDを使用できる。
実行ごとに新しいprobe IDでresult/reportを排他的に作り、元の失敗結果を上書きしない。
今後これらの配備物を変更する場合は、新run IDへ配備・compileしてhostの既知hashを取り直す。
大きなSDL/Mesa rebuildや通常設定の永続変更は、この対照に含めない。

Build153で`egl-wayland`を一回実行したが、同じSIGSEGV（client exit -11）を再現した。
protocol errorは0、content SHM commitは0、追加のmapped windowはなく、元のfootは継続した。
runnerと子groupの回収・pipe drainは確認した。環境値一項では解消しなかったため、
次は明示的なGL library/window/contextの固定stageごとに最初の失敗を取得する。
成功条件を緩めたり、同じ条件を反復して結果を選んだりしない。

`--stage`の既定は従来の`render`である。初回GL/EGLエラーの
[固定stage診断](sdl-egl-stage-probe-20261108.md)も選択できる。
このstage追加でC/配備runnerを更新したrevisionでは、新runへの再配備・compile・新hashが必要になる。
stage診断の完了は`diagnosed`と記録し、SHM描画の成功には数えない。

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
