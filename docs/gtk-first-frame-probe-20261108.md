# NX15: 固定GTKの初描画だけを観測する

`scripts/quest-run-gtk-probe`は、導入済み`gtk3-widget-factory`を認証済みの現行leaseへ一度接続する開発用operator。
通常GUIカタログは未解禁のままにし、terminalへの偽装や任意command/path/envは追加しない。
SDL/ゲームの成否とは別に、Wayland+Cairoの最初の描画を検証する。

```bash
scripts/quest-run-gtk-probe --serial "$EIDOLON_QUEST_SERIAL"
```

対象は今回確認したTermux UID10158、`gtk3=3.24.52`、prefixの固定`bin/gtk3-widget-factory`。
binaryは同UID、regular file、mode0700、174336 bytes、SHA-256
`3438ab1bdd34eeb2132bef00ba28a075318750c6f2750d0f2f7a55213fff200b`に限定する。
起動前後の検査と、実spawn直後の`/proc/<child>/exe`のfile identityを照合する。
別版・別端末UIDへの自動追従はせず、不一致はblocked/failedとして記録する。

## 同じjobで確認するもの

1. 現在APKのsource digest/process/schema、runningなfoot一枚、認証済みWaypipe leaseと同UIDの登録rootを照合する。
   [既存capture](sdl-probe-live-capture-20261108.md)と同じ境界を使い、現行active journalと無関係なcleanup記録を区別する。
2. root/owner/childのPID/starttime、socket、現行leaseの二つの環境値を再captureし、観測中もlive bindingを確認する。
3. 固定GTKを実Popenの新しいgroupで起動し、5秒観測してそのgroupだけへTERMを送る。
   猶予後も残る場合だけKILLし、leaderをreapする前にgroupの回収を確認する。
4. 同じjobのSDK metadataでGTK追加mapped windowと最初のcontent/frame、停止後の退役、元footの継続を確認する。

GTKの固定環境は`GDK_BACKEND=wayland`、`GDK_GL=disable`、`GDK_RENDERING=image`。
専用HOME/config/cacheとmemory GSettingsを使い、利用者の既存設定を読み書きする経路を分ける。
固定profileのほかに引き継ぐアプリ環境値は、照合済みの`WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR`だけ。
設定・最終reportはTermux home由来の`.local/state/eidolon/nx15/<probe-id>/`へ新規作成し、0700/file0600を使う。
既存runを上書きせず、製品のowned session directoryへ配布物を追加しない。helper自体の更新・package導入は不要。

stdoutは16KiB、stderrは8MiBの上限でdrainする。`WAYLAND_DEBUG=client`はメモリ内の
既存WaylandTraceへ流し、xdg_toplevel本体のSHM commit/format・非SHM/未知buffer・protocol errorだけを集計する。
本文・raw trace・全環境・logcat・部屋の画像は保存しない。

静止GUIなのでSHM本体commitは1回以上、SDKは追加window一つと最初のcontent/frameを求め、
内容が2回前進する条件を無条件には課さない。初期の空targetや既存target keyだけでは成功にしない。
停止後にそのwindowが退役し、元footが観測中ずっと存続した場合だけSDKを`observed`にする。

## 終了の意味と範囲

これは自然終了programではない。固定観測後の`stop_reason=OBSERVATION_COMPLETE`と
`termination=signal`、実際の`child_returncode`/`termination_signal`、TERM/KILL/reap/group回収を別々に残す。
早期の自然終了、lease変更、host取消/EOF、出力超過は初描画の合格にしない。
観測したfoot/shell/Waypipe PIDをsignal対象へ流用しない。
hostのADBだけが強制終了した場合はremote cleanupを確認済みへ書き換えない。

この初描画をGUI一般対応や製品Stopの受入れへ数えない。
dialog、入力、resize、HMD、物理キーボードは`not_run`のままにし、初描画成立後の別固定caseで調べる。

## 候補の一次source

Termux [gtk3 recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/x11-packages/gtk3/build.sh)は
Wayland backendを有効にする。upstreamの[demos既定](https://github.com/GNOME/gtk/blob/3.24.52/meson_options.txt)と
[widget factory install](https://github.com/GNOME/gtk/blob/3.24.52/demos/widget-factory/meson.build)からこのbinaryを含む構成で、
実機でも同版と上記hashを確認した。
[program](https://github.com/GNOME/gtk/blob/3.24.52/demos/widget-factory/widget-factory.c)は
`G_APPLICATION_NON_UNIQUE`を使う。固定GDK_GL指定は[gdk.c](https://github.com/GNOME/gtk/blob/3.24.52/gdk/gdk.c)で
debugbuild条件と独立に解釈され、[gdkwindow.c](https://github.com/GNOME/gtk/blob/3.24.52/gdk/gdkwindow.c)でGL context作成を拒否する。

PC検査:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.tests.test_gtk_probe scripts.tests.test_sdl_probe_run -v
```

process fixtureで静止画1commit、TERM/強制KILL、子孫回収と別group保護、元終了code、取消・lease変更、
出力上限、hash/mode/package/symlink検査、SDK初frame/退役/元foot継続を確認する。
