# L7a A/A0: 実用アプリ検査と設定backendの実装基盤

状態: **A/A0検査基盤はPR #4でmainへマージ済み。A0全体・v3通常起動・Quest受入れは未完了**。
この変更は[詳細設計](linux-real-app-l7a-design-20261109.md)の `A → A0 → B/C → D → E → F` の最初のPRに対応する。
検査で未確認の項目を残し、A0成立前に通常catalogへMousepadを追加しない。
PR #4後の実装順序・完了条件・次セッション用プロンプトは
[A0の次工程と引き継ぎ](linux-real-app-a0-next-steps-20261109.md)にまとめる。
続くPR1のprofile/run分離、通常Close/Stop/異常終了後の再起動、先行instance独立は
[再起動harnessの実装記録](linux-real-app-a0-restart-20261109.md)を参照する。
本書のPR #4時点の検証結果と、後続PRのheadに対する検証結果は分けて扱う。
続くPR2のGUI状態読取り、実Wayland操作、設定保持・保護2widgetのoracleとA0成功経路は
[GUI設定の実装記録](linux-real-app-a0-gui-settings-20261109.md)を参照する。
本書内のGUI pendingと常時incompleteの説明はPR #4時点の基準であり、現在のgateは完全な実測証拠を要求する。

## このPRの境界

| 実装 | 得られる証拠 | まだ証明しないこと |
| --- | --- | --- |
| `inspect-linux-real-app` | package/ELF/schema/依存hash/実build設定、既存receipt容量のread-only照合 | app起動、GIO moduleのload、GUI、available |
| `eidolon_settings_backend.c` と固定probe | 3キーの値・型・変更不可、混在transaction拒否、通常設定の通知と永続化、fallback拒否 | 実Mousepad設定画面のwidget状態、remote通常Launch |
| 復元データguard | exact session key、autosave候補、link/owner/mode/交換raceの拒否とbytes保持 | 文書の自動復元・移行、process Recoverの認証 |
| 実Wayland compositor/seat fixture | 製品 `ProtocolState` に接続する実Waypipe両端、実Mousepadのmain/dialog、キー入力、ファイル保存のbytes照合 | SDK/HMD表示、物理入力、native v3 admission/watchdog |
| `check-linux-real-app` | 各段階の成功・失敗・未確認を別のreceiptに残す | 未実装acceptanceを成功として扱うこと |

正常なAPI probeだけでA0全体を合格にしない。GUI設定の変更不可、設定変更後の再起動、既存instanceとの独立など、
未実装のoracleは `pending` として残す。`--stage acceptance` は明示的に未実装を返す。
通常UI、APKのv2 pins、bootstrap、既存helper/wireを切り替える変更はこのPRに含まれない。

## 対応packageを推測しない

参照するMousepad sourceを upstream 0.6.3のcommit
[`65fbfe0459259f820fcc8b9c4dd454228304842a`](https://github.com/xfce-mirror/mousepad/tree/65fbfe0459259f820fcc8b9c4dd454228304842a)
へ固定する。CIではこれを改変せずに一時package `0.6.3-0eidolon.a0.1` としてbuildし、
一時runnerへ明示的に導入する。これは一般的なUbuntu/Debian packageや利用者のremote Linuxを対応済みにする記録ではない。

[`configure.ac`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/configure.ac)の
`keyfile-settings` は、enable/disableのどちらのoptionも指定時に有効側へ入る。
そのoptionを**省略**し、生成された `mousepad/Makefile` のactive compiler flagsを検査する。
`config.h` の架空のundefや、package名・版文字列だけを根拠にしない。

`--reviewed-package` は固定schemaの検証記録を指定する。固定 `/usr/bin/mousepad`、source evidence、
package所有、root所有regular file、依存の実体hash、schema型を照合する。
自由command、argv、環境変数やアプリpathを受け取る仕組みではない。
GIO/GTK/GLib/GtkSourceView、D-Bus、schema等の記録と、host keyfile backendのdefaults/locksをfingerprintへ含める。
生成物である `gschemas.compiled` はdpkg payload一覧への所属を要求せず、root所有・hash・schema・GLib packageを検査する。
merged-/usrのpackage所有判定は、検証した固定system aliasだけを扱う。

事前検査はMousepadの `--version` / `--help`、package manager、dynamic loaderを実行しない。
`inspection_passed=true` でも `available=false`、`runtime_verified=false` である。
未知のdistro buildは `APP_PACKAGE_UNSUPPORTED` とし、検査用packageと同一と推測しない。

## Waylandの識別子は実測で固定する

GApplicationのIDは上流の[固定macro `org.xfce.mousepad`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-private.h)
だが、CIの `mousepad 0.6.3-0eidolon.a0.1` / `GTK 3.24.41-4ubuntu1.3` /
`/usr/bin/mousepad --disable-server` では、Waylandのmain `app_id` は **`mousepad`** だった。
[実行記録](https://github.com/tin-machine/eidolon-wayland/actions/runs/42502638269)のreceiptでは、
初回Mapped frameと250ms後のframeで同じID、同じparentless WindowIdを確認している。
この観測を根拠に、初期候補を実際のwindow識別子へ修正する。

[GTK 3.24.41のtoplevel生成](https://github.com/GNOME/gtk/blob/3.24.41/gdk/wayland/gdkwindow-wayland.c#L2151-L2155)
は `g_get_prgname()` を `xdg_toplevel.set_app_id` へ渡す。
[GLib 2.80.0の `g_application_run()`](https://github.com/GNOME/glib/blob/2.80.0/gio/gapplication.c#L2663-L2670)
は未設定のprogram nameを `argv[0]` のbasenameから設定するため、固定argvでは `mousepad` になる。
一方、[GtkApplicationの識別子](https://github.com/GNOME/gtk/blob/3.24.41/gtk/gtkapplication-wayland.c#L51-L57)は
[別の `gtk_surface1` D-Bus property](https://github.com/GNOME/gtk/blob/3.24.41/gdk/wayland/gdkwindow-wayland.c#L5290-L5297)へ渡される。
この二つを同一視しないことを、上流実装とruntimeの両方で確認した。

inspectorの将来manifest検証と実Wayland controllerは、共有する `WINDOW_APP_ID` により
`window_app_ids=["mousepad"]` を要求する。空・未知の値や以前の候補を同時に許容しない。
これは固定CI構成の観測であり、別版packageのA0や、native originによるinstance由来の証明を代替しない。
失敗時の診断は固定phase、bounded ID/parent/lifecycle/frame、入力countだけを保存し、本文・title・pixelsを含めない。

## File chooserの非同期処理に合わせる

[GTK 3.24.41のlocation更新](https://github.com/GNOME/gtk/blob/3.24.41/gtk/gtkfilechooserwidget.c#L2570-L2613)
は入力のたびに[150msのtimer](https://github.com/GNOME/gtk/blob/3.24.41/gtk/gtkfilechooserwidget.c#L115)を設定し直し、
その後のselection変更が[accept buttonのsensitivityを更新](https://github.com/GNOME/gtk/blob/3.24.41/gtk/gtkfilechooserdialog.c#L384-L399)する。
[実行記録](https://github.com/tin-machine/eidolon-wayland/actions/runs/42503959636)では、正しいmain/parentと
150件のnative入力配送を観測した後もOpen dialogが残った。入力直後のEnterは、このaccept有効化より早く到着し得る。
[GtkEntryのactivate](https://github.com/GNOME/gtk/blob/3.24.41/gtk/gtkentry.c#L6038-L6061)も、
フォーカス中のentryのdefault buttonがinsensitiveならactivationを行わない。
controllerはpathの最終文字の後に250ms、Wayland dispatchとtopology検査を継続してからEnterを一度送る。
その後もdialogが実際にunmapすること、Open/編集/Save/Save Asの出力bytesが独立oracleと一致することを必須とする。
この待機だけでは、GUI成功やA0成立を認定しない。

## 設定policyの実装

専用GIO module名は `eidolon-keyfile-v1`、実GTypeは `EidolonKeyfileV1Backend`。
公開 `GSettingsBackendClass` vfuncと変更通知APIだけを使い、
`g_keyfile_settings_backend_new(filename, "/", NULL)` へ通常設定を委譲する。

| 完全なkey | 有効値 | writable |
| --- | --- | --- |
| `/org/xfce/mousepad/preferences/file/session-restore` | string `never` | false |
| `/org/xfce/mousepad/preferences/file/autosave-timer` | uint32 `0` | false |
| `/org/xfce/mousepad/preferences/window/opening-mode` | string `tab` | false |

競合する保存値があっても有効値・default・user readをpolicyへ固定する。保護キーへのwrite/resetと、
保護キーを含むwrite_treeは委譲前に拒否する。混在transactionの通常キーだけを書き込むことはしない。
通常write/reset/write_treeでは外側backendへ変更通知を出す。内側の通知が自動で外側subscriberへ届くとは仮定しない。
GLibのkeyfile writeはdisk失敗前にcacheを更新し得るため、通常writeの失敗時も「変更の可能性」の通知を出し、delegateの成功/失敗を返す。
根拠は[GLib 2.80.5のwrite実装](https://github.com/GNOME/glib/blob/2.80.5/gio/gkeyfilesettingsbackend.c#L375-L430)と
[公開通知API](https://github.com/GNOME/glib/blob/2.80.5/gio/gsettingsbackend.c#L345-L386)。

`HOME=<synthetic-profile>/home`、`XDG_CONFIG_HOME=<synthetic-profile>/config` の関係を固定し、
全path成分をno-followで検査する。profile初期化失敗は固定reasonとexit 78で終了し、標準backendへfallbackして継続しない。
module自体が見つからない場合はGLib側でfallbackし得るため、probeが実GTypeを照合して拒否する。
この二つの失敗経路を別々に検査する。

固定probeの `--exercise` は、既知bytesのprivate markerを持つ合成profileにだけ通常設定を書き込む。
`--check`、`--exercise`、別processの `--verify-persistence` を使い、
保護キー、通常通知、reset、delayed apply、永続化とprofile分離を確認する。
通常利用のprofileをprobeの試験対象へ流用しない。

## 復元データの保護

`guard_recovery_state()` は、呼出元が取得したprofile flockを保持したまま、spawn直前に呼ぶAPIである。
`config/glib-2.0/settings/keyfile` のexact group `org/xfce/mousepad/state/application` の `session` と、
`data/Mousepad` を有界・read-onlyに検査する。欠落または明確な空sessionだけを許可し、
非空・不正・曖昧な構文、復元候補、link、permission不正、検査中の入替えは `APP_STATE_RECOVERY_REQUIRED` とする。
Documentsや復元候補を読出して公開したり、削除・修正したりしない。

これは[Mousepadが復元無効化に伴いautosaveを削除する経路](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-history.c#L827-L941)
から、既存の未保存文書を守るための起動前gateである。
通常helperへの組込みはC工程で行う。process回収のRecover認証へ現在のapp package検査を追加するものではない。

## 終了要求と記録失敗でも所有を継続する

PR #4の `f91c253` へのレビューで、検証中のSIGTERMがcleanupを通らず親を終了させる経路と、
未回収時のreceipt書込み失敗が `CleanupPending` を置き換える経路を確認した。
どちらも通常のOpen/Save成功では検出できないため、異常経路の契約と回帰テストを追加する。

`SIGINT` / `SIGTERM` / `SIGHUP` は、最初の子を起動する前から同じrun所有のguardで捕捉する。
handlerは最初の終了要求を記憶するだけで、例外を投げない。
[Python公式のsignalと例外に関する注意](https://docs.python.org/3/library/signal.html#note-on-signal-handlers-and-exceptions)
のとおり、handlerからの例外は任意のbytecode境界へ入り得るためである。
既存 `ProcessOwner.spawn()` の `Popen()` とroot登録の間を中断させず、起動前・子/pidfd/pipe登録後・
通常のpumpという安全な境界で終了要求を処理する。終了要求を受けた検証は `APP_WAYLAND_INTERRUPTED` で失敗とする。

cleanupでは終了要求を再送されても処理を中断しない。未完了なら同じ `CleanupPending.run` と
pidfd owner・signal guard・profile flockを上位の再試行へ渡す。全子孫の回収が確認できてからlockを解放し、
元のsignal handlerへ戻す。上位処理で別のhandlerを重ねて登録・復元しない。
scan/pipe drainのI/O例外も、所有終了の証拠とせず同じrunのpendingとして扱う。

`wayland.json` のopen・書込み・fsyncが失敗しても、メモリ上のreceiptと**同一の `CleanupPending`**を
必ず上位へ渡す。`receipt_write_failed=true` だけを加え、pathを含む例外本文は保存しない。
途中まで作られたreceiptの再作成や、上位 `cleanup-pending.json` の書込み成功を、回収継続の条件にしない。
後から回収が完了しても、最初の `APP_WAYLAND_CLEANUP_PENDING` を検証成功へ変更しない。

回帰テストは、終了要求の登録前後・起動と所有登録の境界・pending中の繰り返しシグナル、
別processからのflock取得拒否と回収後の解放、session/groupを移動する実子孫の回収、
`ENOSPC` / `EDQUOT` / `EIO` による記録失敗を対象とする。
実process検査は専用の非root helperで動かし、固定v2 sourceの変更や保存PIDからの回収は行わない。

## v2互換の固定

既存pendingのRecoverで使用する5 sourceは、main `e401918fa657fa91c2256353b7992c4376d47f43` のbytesを固定fixtureへ記録し、
今回の実装から再生成しない。新機能は新しいfilenameに追加する。

- `scripts/linux/eidolon-linux-session.py`
- `scripts/linux/eidolon_linux_desktop.py`
- `scripts/linux/eidolon_linux_process_owner.py`
- `scripts/linux/eidolon_linux_desktop_session.py`
- `scripts/fixtures/gtk3-desktop-demo.c`

このsource不変確認は、将来の旧APK→新APKによるpending Recover受入れの代わりではない。
receiptの自動GCも追加しない。既存上限と、容量を確認できない場合の拒否を維持する。

## コマンドと結果

検査先は新しいprivate artifact directoryを使う。同じ出力を上書きせず、既存profileを引数で指定しない。
packageの導入・upgrade・v3 bundle配備はこれらのコマンドでは行わない。

```sh
scripts/inspect-linux-real-app \
  --reviewed-package /absolute/private/reviewed-package.json \
  --artifact-root /absolute/private/inspection

scripts/build-linux-settings-backend \
  --output-directory /absolute/private/settings-build

scripts/check-linux-real-app --stage feasibility \
  --reviewed-package /absolute/private/reviewed-package.json \
  --artifact-root /absolute/private/feasibility
```

builderはmodule/probeをcompileするだけで、appを起動しない。feasibilityは対象packageの事前照合後、
合成profileでprobeと実Mousepadを明示実行する。実Waypipeとnative依存は既存のhost builderで事前にbuildしておく。
full A0未確認では `status=incomplete` / `a0_passed=false` を返し、exit statusも非0にする。
missing package/display/build dependency、runtime失敗、cleanup未完了をskip成功にしない。

`L7a foundation` CIは、実装したsettings/guardとWaypipe・ファイル操作の範囲を検査する。
CIのfoundation成功と、receiptのA0全体成功は別である。未実装GUI oracleをfoundation CIの成功で消さない。
通常host/Android CIも継続する。

## 次のgate

実Mousepadの設定画面での変更不可・transient parent、通常設定変更後の再起動、
既存desktop instanceとの独立、Close/Stop後の再起動について、独立した実測oracleを追加する。
その結果で対応版を確定してから、B/Cのv3 manifest・永続state・専用busの通常helperへ進む。
native origin、固定main、20秒/10秒deadline、同targetのSDK ACK/frame、storage ID、v2更新RecoverとQuest操作は、
元の詳細設計のD/E/Fに残っている。
