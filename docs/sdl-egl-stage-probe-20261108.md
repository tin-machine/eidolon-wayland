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

## Build154の実機結果と次の切り分け

新runへ配備・Bionic compileした固定probeを、現行APK/leaseを照合した別processで実行した。
`gl-load`と`gles-load`の両方がload段階で`completed=false`、固定分類
`egl_display_unavailable`、client終了code 1となった。API結果とcleanup結果の両方を取得し、
所有process/groupは回収した。各process内のFDは3→7で、その後processが終了している。
この短い観測から長期のFD増加を断定しない。従来のRenderer rollback SIGSEGVを避けて、
最初のEGL display取得失敗を分離できたが、描画は未合格である。

Termuxの`share/glvnd/egl_vendor.d/50_mesa.json`と指定`libEGL_mesa.so.0`は存在し、
実libEGLの既定探索先もこのdirectoryに一致した。
[libglvnd 1.7.0のvendor読込み](https://gitlab.freedesktop.org/glvnd/libglvnd/-/blob/v1.7.0/src/EGL/libeglvendor.c)は
JSONの存在だけでなく、vendorのdlopen、`__egl_Main`の呼出し、必要callbackの検査まで行う。
同UIDの短命な別processで固定Mesa vendorのdlopenを試すと、未解決symbolの固定分類で失敗した。
error本文は保存せず、processの終了・回収を確認している。
追加の固定計装で、参照元`libgallium-26.2.6.so`に対して
`_ZNSt6__ndk113__hash_memoryEPKvm`が欠けていることを特定した。
これは`std::__ndk1::__hash_memory(void const*, unsigned long)`で、
[LLVM 21のavailability定義](https://github.com/llvm/llvm-project/blob/llvmorg-21.1.8/libcxx/include/__configuration/availability.h)と
[実装](https://github.com/llvm/llvm-project/blob/llvmorg-21.1.8/libcxx/src/functional.cpp)に共有runtimeの追加APIとして存在する。

導入済みTermux `libc++`は27c、候補は30だった。版を固定したsimulateが更新1件・追加0件・削除0件になることを確認し、
`libc++=30`だけを更新した。新しい実runtimeで当該symbolのexportを確認し、
新しい短命processでMesa vendorのdlopenと`__egl_Main`の取得が成功した。
[Termuxの固定recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/packages/libc%2B%2B/build.sh)も
NDKr30の`libc++_shared.so`を配布する構成で、この結果と整合する。
これはloaderの修復確認であり、SDL/EGL window・SHM・SDK描画は次の別検証である。

[libglvndのeglGetDisplay](https://gitlab.freedesktop.org/glvnd/libglvnd/-/blob/v1.7.0/src/EGL/libegl.c)は
`EGL_PLATFORM=wayland`を既に明示Wayland platformへ変換するため、
同じ条件でeglGetPlatformDisplayへ置き換えるだけの比較は後回しにする。
固定vendor JSON指定も、正しい探索先とvendorの実dlopen失敗が分かった現段階では第一選択にしない。
SDK/Waylandの描画を変更したり、大きなSDL/Mesa rebuildへ進む前にloader失敗を分ける。

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
