# SDLの最初のGL/EGL失敗を分ける固定stage probe

Build152の通常SDL実行とBuild153の`EGL_PLATFORM=wayland`一項対照は、いずれも
SDL_CreateRenderer内のOpenGL失敗復元中にSIGSEGVとなった。
元の終了code `-11`、SHM commit 0、SDK追加mapped window 0、元foot維持、所有process回収の結果を保持している。
次はSDL/Mesaをrebuildする前に、rendererの復元を呼ばない小さいstageで最初の失敗を調べる。

## 固定の操作

[C probe](../scripts/fixtures/sdl-wayland-probe.c)の`--stage`は次の6値だけを受ける。
各operator実行が独立したSDL processになり、一つのprocessでAPIを切り替えて再試行しない。

| stage | 実行するAPIの最終段階 |
| --- | --- |
| `gl-load` / `gles-load` | SDL_Init(video)、固定GL属性、SDL_GL_LoadLibrary(NULL) |
| `gl-window` / `gles-window` | 上記に加えSDL_CreateWindow(OPENGL、hidden、640×400) |
| `gl-context` / `gles-context` | 上記に加えSDL_GL_CreateContext |

GLはprofile 0 / 2.1、GLESはES / 2.0とする。音声・入力本文・描画・swapは行わず、
SDL_CreateRenderer / SDL_RecreateWindowを直接呼ばない。成功した所有context/window/libraryを順に解放して終了する。
SDL内部のwindow作成自体が失敗する場合もあるため、crashやtimeoutを成功へ読み替えない。

API結果をcleanupより先にflushし、その後でcleanup完了を別の固定JSONとして出す。
出力は要求stage・到達stage・成功bool・固定error分類・FD個数だけ。
SDL_GetErrorの本文は保存せず、library不足、EGL symbol/display/init/config/context/surfaceの既知分類と
`unclassified_sdl_error`へ変換する。未知の本文はそのまま出さない。

runnerはこの2つのJSONを検査し、cleanupが途中で壊れても最初のAPI結果を保持する。
`diagnostic_gate=observed`は、成功または失敗のAPI結果とcleanup・終了code・所有group回収が整合した意味である。
APIの失敗も有効な診断であり、`client.api.completed=false`と元のclient終了code 1が残る。
cleanup crash、取消、timeout、不完全JSON、過剰出力は`partial`または`failed`とする。
stage時は`WAYLAND_DEBUG`を外し、stderrも本文を保存せず上限付きでdrainする。

## 新runへの配備と実行

この改修はCと配備runnerの両方を変える。過去のrender失敗runへ上書きせず、
[配備ツール](sdl-probe-install-20261108.md)で新run IDを作る必要がある。

```bash
scripts/quest-install-sdl-probe --serial "$EIDOLON_QUEST_SERIAL"
```

新しい配備Cを同runでBionic compileし、そのcompile jobで記録したbinary SHAと
配備時のmanifest SHAをhostの既知値として固定する。旧binary SHAを流用しない。
現行APK source/lease/UIDと既知pinを再照合する条件は通常の
[operator](sdl-probe-live-run-20261108.md)と同じで、任意PID/path/env/argvを追加しない。

```bash
scripts/quest-run-sdl-probe --serial "$EIDOLON_QUEST_SERIAL" \
  --run-id "$NEW_SDL_RUN_ID" --binary-sha256 "$NEW_SDL_BINARY_SHA256" \
  --manifest-sha256 "$NEW_SDL_MANIFEST_SHA256" --variant egl-wayland --stage gl-load
```

まず`gl-load`を一回実行し、同条件の`gles-load`を別processで比較する。
loadが成功したAPIだけwindow、さらにcontextへ一段ずつ進める。
条件を混ぜずに切り分けるため、最初は`--variant egl-wayland`を維持する。
各回の新probe IDでresult/reportを排他的に作り、既存結果を置換しない。

完全な診断のoperator statusは`diagnosed`（CLI終了0）。描画成功の`completed`とは別で、
`graphics_client/shm_gate/input_gate/quest_sdk`は`not_run`、SDK描画は`not_verified`のまま。
同じjobのSDK監視とlease変更時の所有runner取消を維持し、foot/shellへsignalを送らない。
原因が分かって条件を選んだ後に、既定`--stage render`でSHM・SDK更新・退役を改めて検証する。

## PCでの検査範囲

stageのC関数をfake SDL APIとともにcompileし、6 stageの停止位置、固定GL属性、初回エラー分類、
任意error本文の非保存、cleanup crash後のAPI結果とSIGSEGV保持を検査する。
operatorは診断の負結果を描画成功へ昇格させず、所有groupだけを回収する。
既存renderのtrace/取消検査、配備時のmode/hash/上書き拒否も検査する。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -p 'test_sdl*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.tests.test_sdl_probe_run scripts.tests.test_sdl_probe_install -v
```

hostの実SDLでも全Cをcompileし、dummyのGL load失敗分類と既存render 3 frames/3 pixel検査・回収を確認した。
これらはQuestのGL/EGL成功・SHM・HMD視認の証拠には数えない。
