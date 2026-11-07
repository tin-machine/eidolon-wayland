# GTK3入力・dialog・resizeの固定fixture

`gtk3-widget-factory` の実機初描画が成立したため、次の3項目を小さなGTK3アプリで個別に確認する。widget-factoryの内部状態や画面の見た目を推測して判定しない。現時点ではC/build sourceの配備・build準備とhost callback検証のみで、Questでの入力/dialog/resizeは未検証。

| case | 固定操作とアプリ側の観測 |
| --- | --- |
| input | Entryへ `a` down/up、Backspace down/up、固定buttonクリック。対応key各1、Entry changed2、空への復帰、button clicked1を要求 |
| dialog | 固定buttonクリックでmodal/transient dialogを1個開き、Close/Escape/xdg closeの固定経路で閉じる。親設定、open/destroy/response各1を確認 |
| resize | compositorから論理sizeを800×500へ変更し、640×360へ戻す。GTK configureイベントと実widget sizeを確認 |

各caseは別process。自由な文字列・argv・ファイルを受け取らない。Cの引数は `--case input|dialog|resize --run-id <32hex>` のみ。run-idから専用Wayland app_id `io.github.tinmachine.eidolon.gtkfixture.r<run-id>` と `.dialog` を作る。ウィンドー作成後、map前に [GDK Waylandのapplication ID API](https://gitlab.gnome.org/GNOME/gtk/-/blob/3.24.52/gdk/wayland/gdkwaylandwindow.h) で設定する。

Cのrun-idは実行ごとのprobe IDとし、sourceを置く配備run-idとは分けて記録する。同じbinaryを別caseで使う場合も新しい実行IDを割り当て、古いwindowとの誤対応を防ぐ。

stdoutは固定JSON schemaのcounter、bool、case/run/app_id/PID/UID、sequence、logical size、Entry/buttonの矩形だけ。Entry本文・key文字列・raw Wayland trace・画像は出さない。[Editable::changed](https://docs.gtk.org/gtk3/signal.Editable.changed.html)、[key-press-event](https://docs.gtk.org/gtk3/signal.Widget.key-press-event.html) 等のGTK callbackを記録する。これは物理入力の証明ではなく、`physical_input_verified=false` を常に出す。

## 配備とbuild

```sh
scripts/quest-install-gtk-fixture \
  --serial "$EIDOLON_QUEST_SERIAL" \
  --artifact-root "$EIDOLON_TASK_ROOT/gtk-fixture-install"
```

このCLIはUID10158のTermuxへ、C/build script/manifestだけを新runに配備する。compile・GUI実行・APK操作・通常GUI catalogの解禁は行わない。新runは `$TERMUX_HOME/.local/state/eidolon/nx15-fixture/<run-id>` の0700、配備fileは0600。既存session directoryやSDL runは使わず、空runでも上書きしない。所有、symlink、size、SHA、manifestを照合し、ADB bootstrapは49KiB以下とする。

host artifactの `manifest.json` と一致した `installed.manifest_sha256`、`installed.directory` をoperatorが取り出し、次の固定scriptをrun-asで実行する。

```text
python3 <installed.directory>/build-gtk3-fixture.py --manifest-sha256 <hostで保持した期待SHA>
```

`build-gtk3-fixture.py` はTermuxの実Python prefix、UID、runの場所と所有、source/自身/manifestのSHAを再検査する。GTK3のpkg-config版3.24.52を要求し、固定clang引数で `build/gtk3-fixture` を一回compileする。production buildにはhost用macroを付けない。新build directoryも上書きせず、ELF64 little-endian/AArch64（machine183）、binary SHA/sizeを `build/build.json` へ0600で記録する。compiler出力は上限付きで読み捨て、byte数・元終了code・timeout/reapを残す。新しいbinary SHAはhost側で保存して次のoperatorの期待値とし、実行直前に現binaryから期待値を自動生成しない。

runtime operatorは今後、既存GTK probeと同じAPK/source/lease/登録root gateに加え、manifest・binaryのhost pin、UID/PID/starttime、専用app_idと追加windowの一意な対応を確認する。GDK_BACKEND=wayland/GDK_GL=disable/GDK_RENDERING=imageを固定し、本文やraw traceは保存しない。通常GUI catalog/helper modeは変更しない。

## 終了と次段

Cの `complete=true` はcallback条件の達成だけを表す。windowを閉じた時、条件達成済みならcompleted/exit0、未達ならwindow_closed/exit1。60秒でtimeout/exit124。operatorがTERMした場合はsignal終了として別に記録し、自然終了・GUI合格と混同しない。所有したprocess groupだけを回収し、元foot、dialog退役、入力のpressed解除、SDK側window/target frameを別に確認する。

Androidの固定入力入口は次段で追加する。既存MainActivity/NativeSessionHostへの変更を避け、新debug receiver/caseでcase・nonce・run-idだけを受け、現行runtime内の一意なfixture app_id/target keyを選ぶ案。自由window ID/PID/textを受け付けず、古いepoch/focus token/target世代は拒否する。入力、dialog、resizeを別caseに分け、root operatorが合成操作とアプリcounter・SDK描画を同じjobで対応付ける。

## host検証の範囲

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_gtk_interaction_fixture.py -v
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_sdl_probe_install.py -v
```

hostはGTK3 dev、pkg-config、C compiler、Xvfbを使用する。`EIDOLON_GTK_FIXTURE_SELFTEST` を付けた別binaryで固定key counterと実GTK Entry/button/dialog callback、X11 configureを検査し、`host_selftest=true` を出す。production binaryはX11を拒否する。これらはQuest Wayland、Android入力経路、物理キーボード、HMD視認の合格ではない。


## Build167: app_id設定時期の修正

最初の実機input試験は35秒で停止し、Cのready/SHM2 commitはあっても専用main app_idとして認識されず、Android caseは開始しなかった。SDK/Boardには追加windowのbound/create/frameがあり、未知窓の排除とは断定できない。元のTERM終了と回収結果を保存した。

[GTK3.24.52のWayland実装](https://github.com/GNOME/gtk/blob/3.24.52/gdk/wayland/gdkwindow-wayland.c#L2138) では `gdk_wayland_window_set_application_id` はxdg_toplevel生成前に呼ぶと何も保持せずreturnする。GtkWidgetのrealizeだけでは足りず、show/mapでxdg_toplevelを作ったときにprogram name由来の初期IDを送る。このためCは `gtk_init_check` 前にmainの固定IDを `g_set_prgname` で設定し、main/dialogのsetterを `gtk_widget_show_all` 後へ移した。mappedでないwidgetは成功としない。

ダイアログは初期mainIDから専用dialogIDへ遷移できるが、最初のSHM buffer commit時には専用dialogIDであること、別toplevel・set_parentがあることを要求する。未知IDを許すgate変更はしていない。縦boxの最後のbuttonは余白を受けて高さ88以上に広がる設計であり、固定pointが報告矩形内にあるかを従来どおり照合する。

PCのreal Wayland検証は既設KWinのvirtual backendを、私有runtime/HOME/config/cache/dataと、service directoryを含まない私有D-Bus設定で起動する。full trace/画像を保存せず、既知IDの遷移・最初のSHM時のrole・別toplevel/parent・SHM commit数だけを集計する。host selftestのinput callback自体は合成であり、この検証をQuest入力/HMDの合格にはしない。KWin/dbus-run-sessionが無い環境ではこの1検査をskipする。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_gtk_interaction_fixture.py -v
```

Cが変わったため、実機ではinstallerで新run-idを作り、source/manifest/binary SHAを新たに固定する。既存runへCやbinaryを上書きしない。Android sourceや通常GUI catalogは変更不要である。

検証環境でも、通常の私有session busはsystemにあるportal serviceを自動起動できる。最初のhost試行でidentity検査は通ってもruntime/docのFUSE跡により一時directoryの削除が失敗したため、fixtureではservice activationを持たないbus configとportal無効envを使う。GTKのwire検査成功とhost testの後片付け成功を別々に確認する。
