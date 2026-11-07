# NX11: SDL software rendererからSHMへ届く経路の小規模検証

調査基準: `2b6a380`。日本語変換NX09はユーザー指定で保留し、NX10と独立してNX11を準備した。
**ホストの隔離Wayland環境ではSHM描画が成立した。QuestではBionic buildとdummy描画まで確認し、Eidolon上のSDL描画・Doomは未確認。**

## 追加したprobe

- [Cクライアント](../scripts/fixtures/sdl-wayland-probe.c): SDL2、640×400、software renderer、色を更新する小窓。
  毎frameでrendererから1 pixelを読み戻し、そのframeの予定色と比較する。
  音声・全画面・相対マウス・IMEを使わず、通常キーの押下/解放件数と終了を記録する。
  指定frame数、Escape、window close、signalで終了する。キー内容・文字列は記録しない。
- [観測runner](../scripts/termux/eidolon-sdl-probe.py): 自分が起動したprocess groupだけを期限付きで所有し、
  stdout 16KiB / Wayland trace 8MiBを上限としてdrainする。生traceは保存しない。
  `xdg_toplevel`のsurfaceへattachされたbufferを追跡するため、カーソル用SHMだけでは成功しない。
  非SHM/未知buffer・不完全trace・protocol error・回収未完了はSHM gate不合格になる。
  contentのSHM formatも記録し、現行compositorが対応するARGB8888/XRGB8888（0/1）以外は不合格とする。
- [ホスト反復検証](../scripts/check-sdl-probe-host): 作業rootへ展開したWestonをheadless/pixmanで起動し、
  Mesa CPU描画を指定してwarmup→10回→framebuffer acceleration無効の対照を実行する。
  現在のdesktopへ接続せず、短命socketだけを0700の一時fixtureへ置く。結果・ログは指定artifact rootへ保存する。
- [判定器テスト](../scripts/termux/tests/test_sdl_probe.py): カーソルSHM、DMA-BUF、未知buffer、ID再利用、
  trace分割/欠落、偽の成功JSON、合成入力、子孫process、timeout・出力floodを検査する。

`graphics_client=completed`と`shm_gate=pass`を分ける。dummy driverや合成キーは実Wayland入力の証拠ではない。
`input_gate=observed`も単に通常のdown/upを観測した状態であり、物理操作の受入れ・focus/reset試験を代替しない。
`quest_sdk`はこのホスト検証では常に`not_run`。

## 2026-11-07の結果

ホストはSDL2 `2.32.10+dfsg-6`、Weston/libweston `14.0.2-5`、C compiler `15.2.0`。
SDL2 runtimeは元から導入済み。headersとWestonのdebは合計約2.6MBを作業rootへ展開しただけで、システムへ導入していない。
最終runは`20261107T221311-603603`。format判定追加後に10回を再実行した。

| 条件 | 結果 | 範囲 |
| --- | --- | --- |
| dummy + synthetic down/up | 描画完走、合成入力各1件、FD 3→3 | クライアントのhost内部検査のみ |
| headless Weston/pixman + `LIBGL_ALWAYS_SOFTWARE=1` + SDL software renderer | 10回すべてSHM gate成功、各12 frame/12 pixel検査/content SHM commit 13回、全回XRGB8888 | ホストのWayland経路 |
| 同条件でframebuffer accelerationを無効化 | renderer作成失敗、SHM commitなし | 無効化を一律の解決策にしない |
| 所有process/group | 各回reap・回収成功 | 起動したprobeのみ |
| WestonのFD | warmup後13、10回すべて13、最後13 | この短い反復で増加なし |
| 判定器のunit/process tests | 8件成功 | 実機成功とは別 |
| Quest ADB | device一覧が空 | APK・Termux packageの変更なし |

最初の6 frameの観測ではcontent SHM commit 7回、release 3回、frame callback 3回、
非SHM・未知buffer・protocol errorは各0。SDLのrenderer名は`software`。
SDL_Quit直後の子内FDは3→15だったが、process終了で所有groupは回収した。
この数値だけでSDK/ドライバの長期漏れや固定費を判断しない。

`SDL_FRAMEBUFFER_ACCELERATION=0`の対照では`Couldn't find matching render driver`となった。
[SDL2のWayland実装](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/wayland/SDL_waylandvideo.c)には
独立したwindow framebuffer実装がなく、今回の成功を「EGLが不要だった」と解釈しない。
EGLの初期化API自体はinstrumentしていない。実測で確認したのはSDL rendererの成立と、最終的なtoplevel SHM bufferである。

Weston起動の最初の試行では、`--backend`へmoduleの絶対pathを渡して`unknown backend`になった。
`--backend=headless`と、[公式のmodule map](https://cgit.freedesktop.org/wayland/weston/tree/libweston/compositor.c)で
展開先のmoduleを指定すると起動した。これをhost scriptへ反映した。

## 2026-11-08: Quest上の準備とBionic検証

Termuxの同UIDでSDL2 `2.32.10`、opengl `4.5`、Mesa `26.2.6`、libglvnd `1.7.0-1`、
libdecor `0.2.5`を導入した。追加49件、既存packageの更新・削除は0件、追加容量の見積りは135MBだった。
最初は古いapt一覧のvulkan-loader-generic `1.4.367`が404となった。
一覧を更新し、追加・更新・削除を再simulateした後、取得可能な`1.4.368`で導入に成功した。
全追加packageの版とrepository上のarchive SHA-256を保存した。既存foot/Waypipeは更新していない。

[固定source配備ツール](sdl-probe-install-20261108.md)でC/runner/manifestを新runへ配備した。
初回はAndroidの標準的な`files`親directoryのmode `0771`を拒否して失敗したため、
同一app UID/GID・world writeなしの場合だけこの親のgroup writeを許容した。
usr/bin/home以下の検査と配備先0700/file0600は維持し、端末の親modeを変更していない。

Termux clang `21.1.8`で同C sourceをbuildし、AArch64 ELF（13,736 bytes）を確認した。
binary SHA-256は`446b3428f45c2f94acf7292677cc33b9590e5e1f3ae6466e45c7152212b26046`。
dummy driverで3 frames/3 pixel checks、SDL compile/runtimeとも`2.32.10`、自然終了・reap・group回収に成功した。
これはBionic/SDLの最低限の動作確認で、SHM・Eidolon texture・実入力は`not_run`のまま。

runner自身へのTERM/HUPで別sessionの子・孫が残る問題もPCで再現し修正した。
取消は独立metadataへ残し、先行clientの失敗・終了codeを保持する。先行した成功も取消後は合格へ昇格させない。
取消・元失敗保持・非所有processの保護を含む10件が成功した。

記録は作業rootの`eidolon-ime-implementation-20261107/`内の`nx11-device/`、`sdl-install/`、`nx11-runner-evidence/`。
C/runnerの各SHA-256、配備manifest、compiler argv、package追加前後、404と条件変更を含む。

## 2026-11-08: Quest Build152での初回Wayland実行

認証済みの現行footのlease・同UIDの登録root・現在APKのsourceを照合し、
固定operatorで`LIBGL_ALWAYS_SOFTWARE=1`、software renderer、120 frames/15秒の自然終了を一度試した。
SDL_CreateWindowは成功したが、続くSDL_CreateRenderer中にSIGSEGV（client returncode `-11`）となり、
クライアントの完了reportは出なかった。

| 観測 | 結果 |
| --- | --- |
| Wayland registry bind | compositor/xdg-shell/SHM/seat等は成立 |
| toplevelのcontent SHM commit | 0、描画不合格 |
| SDKの追加mapped window | 0、`not_verified` |
| 元のfoot window | 維持 |
| 所有runner/SDL process group | reap・回収を確認 |
| HMD視認・物理入力 | `not_run` |

保存したSDL2 ELFをNDKのobjdumpで調べると、最上位frameは
[Wayland_DestroyWindow](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/wayland/SDL_waylandwindow.c)で
NULLのwindow driverdataからegl_surfaceを読む命令（fault address `0x80`）と一致した。
[SW_CreateRenderer](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/render/software/SDL_render_sw.c)は
SDL_GetWindowSurfaceを使い、[window framebuffer生成](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/SDL_video.c)では
software指定でも内部の加速rendererを試せる。今回の別frameはOpenGL driverの
[失敗復元SDL_RecreateWindow](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/render/opengl/SDL_render_gl.c)へ一致する。
最初の再作成が旧windowを破棄してから失敗し、復元時にもう一度破棄する経路が有力である。
最初のGL library/EGL初期化失敗の理由はまだ取得していない。

次はC/binary/配備runner/manifestを変えず、
[固定operator](sdl-probe-live-run-20261108.md)の`--variant egl-wayland`で
`EGL_PLATFORM=wayland`だけを追加する一項対照を行う。
[Termux Mesa recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/packages/mesa/build.sh)は
native platformをX11にしているが、[Mesa 26.2.6の選択処理](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/egl/main/egldisplay.c)は
環境値・native displayの自動判定・build時defaultの順に選ぶ。
従って「未設定なら必ずX11になる」とは断定しない。対照の成功も別途SHM/SDK観測で判定し、
元のSIGSEGV・終了code・回収結果を上書きしない。同じ失敗ならwindow作成前の固定GL load stageを小probeで調べ、
SDL/Mesaの大きなrebuildはその結果を待つ。

## Termuxへ持ち込む前の確認

Termux公式tree `fbdb3c03610c697322ac608e77cea2b553f49ef3`（recursive tree、truncated=false）で確認した。

- [SDL2 recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/x11-packages/sdl2/build.sh):
  2.32.10、Wayland/OpenGL/OpenGLESを有効化、`opengl`を推奨。
- [opengl recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/packages/opengl/build.sh):
  `libglvnd, mesa`へ依存。
- [Mesa recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/packages/mesa/build.sh):
  26.2.6、EGL有効、platforms `x11,wayland`、Gallium `llvmpipe,softpipe,virgl,zink`。
- [Chocolate Doom recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/x11-packages/chocolate-doom/build.sh):
  3.1.1、依存は`sdl2 | sdl2-compat, sdl2-mixer, sdl2-net`。

これらはsource定義の証拠であり、Questの導入済み版・apt候補・Bionic上の動作証拠ではない。
SDL再移植を始める前に、公式SDL2＋Mesa CPU→SHMの組合せを実機で試す。

## 再現手順

SDL2 headers/libraryとpkg-configを利用できる環境では、同じC sourceを次のようにbuildできる。
上記のTermux検証でも同じC sourceとpkg-configのflagsを使用した。

```bash
cc -std=c11 -Wall -Wextra -Werror -O2 scripts/fixtures/sdl-wayland-probe.c \
  $(pkg-config --cflags --libs sdl2) -o /absolute/artifact/directory/eidolon-sdl-probe

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -p test_sdl_probe.py -v

python3 scripts/check-sdl-probe-host \
  --binary /absolute/artifact/directory/eidolon-sdl-probe \
  --weston-prefix /absolute/extracted/weston/root \
  --artifact-root /absolute/artifact/directory/repeat --cycles 10
```

今回のheadersのみ展開したhostでは、SDL2 includeとarchitecture-specific includeを`-I`で渡し、
既存runtimeの`-l:libSDL2-2.0.so.0`へlinkした。deb取得・SHA256・compiler・source/binary SHA256は
作業root配下`eidolon-nx09-nx18-20261107/nx11-probe/provenance.json`に記録した。
ホストで展開したrootは`usr/bin/weston`、`libweston-14.so.0`、`libexec_weston.so.0`、
`headless-backend.so`、`kiosk-shell.so`を持つ構成である。

実機で次に行う順序:

1. 上記の配備・Bionic buildの既知hashを保持し、現行APK/leaseでfoot一枚を準備する。
2. `--variant egl-wayland`の固定一項対照で自然終了を一回実行し、同じjobで二枚目のSDL窓・SHM更新・退役を観測する。
   成立しない場合は最初のGL/EGL failureを小probeで分ける。描画成立後に製品経路と入力を検証する。
3. 焦点移動・取消・10回の停止回収を追加し、成立後にChocolate Doom＋Freedoomへ進む。
   Doom成功と一般GUI対応は別に確認する。HMD/物理キーボードの受入れは最後に残す。
