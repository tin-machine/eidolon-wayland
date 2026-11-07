# NX11: 固定Freedoom demo128の自然終了operator

`scripts/quest-run-freedoom-probe`は、[配備済みの固定8file](freedoom-owned-install-20261108.md)と
Chocolate Doom 3.1.1だけを使う開発用の一回検証である。通常GUI catalogは変更しない。
host fixtureの成功をQuestでのゲーム成功へ数えず、実機resultの`status`と各gateを別に記録する。

## 実行条件

現行sourceと一致するdebug APK、runningの認証済みWaypipe lease、登録済みのTermux terminal rootが必要である。
元footが一枚の状態から開始する。古いnonce、socket名の推測、観測PIDへのsignal、任意exec/argv/env指定は行わない。
hostがAPK process/source/sessionを照合し、Termux側もUID10158、leaseのPID/starttime/marker/owned UDSを再照合する。
引き継ぐ環境値は`WAYLAND_DISPLAY`と`XDG_RUNTIME_DIR`だけである。

```sh
scripts/quest-run-freedoom-probe --serial "$EIDOLON_QUEST_SERIAL" \
  --asset-run-id af24550bb2416de36657b515895b0a6d \
  --manifest-sha256 0fcab0db176ef675c9eb3857547458ed424b6d8c6af3fd160f05369aec2ee727 \
  --engine-sha256 abcb1d304f6c9f859d46c156990c9db214b0a3e67707170a7655844dc0c5c253 \
  --artifact-root "$EIDOLON_TASK_ROOT/freedoom-demo128"
```

上のrun-idとmanifestは当日の固定配備に対応する。別のhost準備runはその既知manifest SHAを使う。
engine SHAはCLIの自由選択ではなく、固定版の既知期待値との一致条件である。
assetを再配備せず、各回新しいprobe-idと0700 stateを作り、過去の失敗・成功を上書きしない。

## 固定条件と記録

- engineのregular file・UID・0700・nlink1・801632bytes・AArch64 ELF・既知SHA、dpkgの3.1.1 installedを照合する。
  実spawnした子のUID/PID/starttime/process groupと`/proc/<own-child>/exe`のfile identityも一致させる。
- asset manifestとreceipt、COPYING/CREDITSを含む8fileの600/owner/size/SHAを開始前と終了後に検査する。
  実行中もfile identityとengineを再検査する。途中変更・不明なreceipt field・symlink・既存stateを拒否する。
- 専用stateへconfig templateをコピーし、既存設定やimmutable assetをゲームの保存先にしない。
  `-playdemo`だけを使い、`-timedemo`、ネットワーク対戦、音、mouse grabは使わない。
- 固定profileは`SDL_VIDEODRIVER=wayland`、`LIBGL_ALWAYS_SOFTWARE=1`、`EGL_PLATFORM=wayland`、
  `GALLIUM_DRIVER=softpipe`、`LIBGL_KOPPER_DISABLE=1`。専用configがsoftware rendererを要求する。
  この値は描画方針であり、engineがrenderer名を報告したという意味ではない。
- [SDL2.32.10のclassname選択](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/wayland/SDL_waylandvideo.c)と
  [xdg/libdecor app_id設定](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/wayland/SDL_waylandwindow.c)に従い、
  固定`SDL_VIDEO_WAYLAND_WMCLASS`へprobe-idを設定する。
  Wayland trace内のapp_idとSDK Mapped windowのapp_idを両方照合する。未知app_idの本文は保存しない。
- 25秒の期限、control cancel/EOF、lease喪失時は、実spawn handleが所有するgroupだけTERM、期限後KILL、wait/reapする。
  自然exit0、signal exit、TERM/KILL送信、timeout、pipe drain、group回収を個別に保持する。
  wrapperへのTERM/HUP/INTも協調取消へ変換する。元foot/shellをsignal対象にしない。

raw Wayland/SDL stdout・stderrは保存しない。有限drainし、app_id一致回数・SHM本体commit・非SHM・protocol error・
frame callbackなどの固定集計だけ残す。EGL4依存library/vendorとSDL2実libraryの既知pinも前後照合する。
SDL2は`libSDL2.so`が所有UID10158の`libSDL2-2.0.so`を指すこと、実fileが0700/1522200bytes、
SHA-256 `5e70d30076c0259aac79d226465c1b0e265b5bbd0cf1665f82764c031b98cc0a`であることを要求する。
hostとremote sourceの全SHA、APK source digest、binding、数値SDK metadataはprivate artifactへ記録する。
remote sourceはstdlibで圧縮し、既存の49KiB command上限を維持する。

## 合格の範囲

自然exit0、TERM/KILL/取消なし、所有childのreap/group/pipes回収、asset/runtime/lease不変を要求する。
app_id一致のSHM本体commitが3回以上、未知/非SHM/protocol errorが0であることに加え、同jobで以下を確認する。

1. 元footを全sampleで維持し、固定app_idの追加窓が一枚だけ表示される。
2. 追加窓のSDK frameとcontentがそれぞれ2回以上進む。
3. 追加窓の全full-target世代が退役し、panel/SDK binding/Entity/listener/装飾/ボタン/MODE/メニュー/Boardを解放する。
4. 元footのfull target・Entity・所有counter・Board状態が開始時と一致する。

Mapped一覧から消えただけでは合格にしない。remoteの元終了コードやエラーはSDK gate失敗で置換しない。
固定demoの記録入力、合成入力、物理キーボード、HMD視認、製品Stop/切断/反復を区別する。
今回のoperatorはdemoの描画・自然終了だけで、後者は未確認のままである。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p 'test_freedoom*.py' -v
```

localhost検証には、実Popenした固定fixtureの自然終了・元signal/RC保持・期限/KILL・EOF/取消・無関係な子の生存、
receipt/asset/engine改変拒否、app_idのobject再利用、圧縮bundle import/予算、早期拒否とclosed stdin、
元footの一時消失・不完全なSDK回収の拒否を含む。
