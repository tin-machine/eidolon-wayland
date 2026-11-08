# L7a: 別Linuxの実用アプリ一種の詳細設計

状態: **詳細設計レビュー反映済み・A/A0検査基盤を実装中・A0全体は未完了・対象Linux/Questでの実用アプリ受入れ前**。2026-11-09作成・改訂。
基準revisionはPR #2/#3マージ後の `3bcd6cfb44dd21e8c9979a106f93434b52fa396c`。
改訂ではmain `1a92b5c6864f38f7901419d1d93de3d004c9051d` の実装とMousepad 0.6.3の上流sourceを照合した。
本文の実装根拠はこのrevision/tagへ固定する。新しい対策の動作確認済みという意味ではない。
本書のv3 API/schemaは実装契約案である。追加した検査CLIとsettings moduleの範囲は
[A/A0実装記録](linux-real-app-a0-foundation-20261109.md)を参照し、通常GUIへの組込み済みと解釈しない。

[L7aの要求](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-real-app)を、
[通常GTKデモのL5/L6](normal-linux-gui-l5-l6-20261108.md)から実装できる単位へ分解する。
最初の実装PRでread-only事前検査、settings module/probe、復元データguard、実Waylandの検査基盤を追加する。
対象hostへのpackage導入・配備とB〜Fは、A0全項目の成立後に進める。

## 採用案と成立条件

第一候補を **MousepadのGTK3版一種**とする。参照sourceは上流tag `mousepad-0.6.3`。
最新版や対象hostの導入版を意味しない。実装開始時に対象hostのpackage版、patch、依存ライブラリを固定する。
別版なら同じ名称だけで受理せず、以下の独立起動・設定・dialog検査をその版で実施して対応定義を更新する。
検査を満たさない場合はMousepadをavailableにせず、任意エディタへ自動代替しない。

| 項目 | L7aの決定 |
| --- | --- |
| 識別 | 固定catalog ID `mousepad-editor-v1`。表示名 `Mousepad`、組込みアイコン |
| 同時実行 | Linux側の通常GUI leaseは一つ。既存GTKデモかMousepadを明示選択し、一回だけLaunch |
| 起動 | 固定 `/usr/bin/mousepad --disable-server`。ファイル/URI/自由argvをLaunchへ追加しない |
| 描画 | `GDK_BACKEND=wayland`、現在のSHM/CPU経路。Xwayland、GPU転送は含めない |
| 独立性 | 専用session busとNON_UNIQUE起動。既存desktop instanceへの転送を認めない |
| 設定 | Eidolon専用の永続HOME/XDGと、固定3キーを変更不可にする専用GSettings backend。その他はkeyfileへ永続化 |
| ファイル | remoteの専用 `Documents/` を初期cwdとする。Open/Saveでremoteファイルを扱う |
| 追加画面 | 同じlease由来のmain一つと、その実際のtransient子孫。全体の9窓制限を維持 |
| 対象外 | 自由catalog/任意shell、複数main、新規ウィンドー操作、portal一般対応、ブラウザ/印刷、音声、遠隔IMEの合格認定 |

これはアプリの動作環境を分離する設計であり、remoteユーザー権限のfilesystem sandboxではない。
ファイル選択で同UIDが読める他のremoteパスへ進むことはあり得る。Questのローカルファイルを直接開く機能ではない。

### 上流の確認と設計への反映

[Mousepadの公式説明](https://docs.xfce.org/apps/mousepad/start)はGSettingsによる設定を説明している。
[0.6.3のapplication実装](https://raw.githubusercontent.com/xfce-mirror/mousepad/mousepad-0.6.3/mousepad/mousepad-application.c)では
`--disable-server` が `G_APPLICATION_NON_UNIQUE` を設定する。これを固定argvに採用する。
同sourceでは通常の設定dialogもactive windowへtransientとして関連付ける。

[0.6.3の設定store](https://raw.githubusercontent.com/xfce-mirror/mousepad/mousepad-0.6.3/mousepad/mousepad-settings-store.c)は、
ビルド時指定による独自keyfile backendか既定backendを選ぶ。
したがってHOMEを変えるだけで設定分離済みとせず、packageのビルド条件も調べる。
[GLibのkeyfile backend](https://docs.gtk.org/gio/func.keyfile_settings_backend_new.html)を使う構成を必須とし、
その上に下記の専用policy backendを置く。L7aは既定GSettings backendを利用するMousepad packageを対象にする。
独自keyfile backendを直接生成するbuildは環境変数でpolicyを適用できないため、L7aでは未対応とする。memory backendは採用しない。

[0.6.3のdialog実装](https://raw.githubusercontent.com/xfce-mirror/mousepad/mousepad-0.6.3/mousepad/mousepad-dialogs.c)には
GTKのfile chooser dialogがある。初版はこの非portal経路を検査対象にする。
ここから「任意版・任意dialogが必ず同じ経路になる」とは推定しない。

[dbus-run-sessionの仕様](https://manpages.debian.org/bookworm/dbus-daemon/dbus-run-session.1.en.html)では、
指定PROGRAMと専用busの寿命が結び付く。ただしbusとappは別processである。
wrapperのPIDや正常exitだけをアプリ表示・全子孫回収の証拠にしない。

### 起動と窓構成に影響する設定の固定

Mousepad 0.6.3は[既定で `session-restore=after-a-crash`、`autosave-timer=30` を持つ](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/org.xfce.mousepad.gschema.xml#L118-L135)。
[main生成より前に復元処理へ入り](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-application.c#L1009-L1015)、
[復元確認はparentがNULLでも応答を待つ](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-dialogs.c#L626-L663)。
復元をL7aの対象外と書くだけでは、この起動経路を無効にできない。

また、[通常設定画面でopening-modeとsession-restoreを変更できる](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-prefs-dialog.c#L691-L696)。
[既存mainを再利用するのはopening-modeがtabの場合だけ](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-application.c#L1103-L1117)なので、
window/mixedへの変更後は通常Openでも二つ目のmainになる。
[CLI処理後に保存設定へ戻す](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-application.c#L1092-L1093)ため、
`--opening-mode=tab` の追加や起動前の設定書換えだけを制約の実装にしない。

L7aの `mousepad-single-main-no-restore-v1` policyは次の3キーを実行中も固定する。
行番号・フォント・配色などその他の設定は、対応版で検査した範囲で変更・永続化できる。

| GSettingsの完全なkey | GVariant型・固定値 |
| --- | --- |
| `/org/xfce/mousepad/preferences/file/session-restore` | `s`、`never` |
| `/org/xfce/mousepad/preferences/file/autosave-timer` | `u`、`0` |
| `/org/xfce/mousepad/preferences/window/opening-mode` | `s`、`tab` |

**採用する実装案は、小さな専用GIO settings moduleをv3 bundleへ同梱する方式**とする。
GLib 2.80.5の[公開GSettingsBackendClassにはread/get_writable/write/write_tree/reset等の拡張点がある](https://github.com/GNOME/glib/blob/2.80.5/gio/gsettingsbackend.h#L60-L115)。
これを使う `eidolon-keyfile-v1` backendが固定キーを処理し、その他を `g_keyfile_settings_backend_new()` で作るkeyfile backendへ委譲する。
source案は `scripts/linux/eidolon_settings_backend.c`、固定probe案は `scripts/fixtures/linux-settings-probe.c` とし、
対象GLib/GIOに対してbuilderでcompileする。公開class vfuncと通知APIを使い、GLib内部の非公開read/write関数へ依存しない。
Mousepad本体へ検査counterを埋め込まず、ホスト全体の設定は変更しない。
このmoduleは新規実装であり、対象GLib/packageでの成立はA0のgateとする。

標準keyfileにはlocks機能があるが、[参照先は既定で `/etc/glib-2.0/settings` である](https://github.com/GNOME/glib/blob/2.80.5/gio/gkeyfilesettingsbackend.c#L665-L725)。
`GSETTINGS_BACKEND=keyfile` と専用XDGだけでprofile単位のlockが成立するとは扱わず、hostの `/etc` を編集する解決は採らない。
[独自backend buildは `g_keyfile_settings_backend_new()` を直接呼ぶ](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-settings-store.c#L203-L247)ため、
このbuildへ同じpolicyを適用できると推定しない。検査不成立なら `APP_SETTINGS_UNVERIFIED` でavailableにしない。

moduleの必須契約は以下とする。

- 固定3キーの有効値を常に上表へ固定し、`get_writable=false`。writeを拒否し、resetでも値を変えない。
  固定キーを含むwrite_treeは全体を拒否し、一部の可変キーだけをcommitしない。保存済みの競合値を有効値として露出しない。
- 可変キーは同じprivate keyfileへ保存する。write/reset/syncと、同process内のGSettings間の変更通知を検査する。
  内側backendの通知が外側へ自動伝播すると仮定せず、公開APIでwrapperの変更通知を発行する。
  L7aでは同profile設定を外部editor/processと並行編集する運用は対応範囲に入れない。
- `GSETTINGS_BACKEND=eidolon-keyfile-v1` と固定bundle内の `GIO_EXTRA_MODULES` を子へ渡す。
  module source/binary・登録情報・依存をpinし、自由なmodule pathやbackend名をmanifest/Launchから受け取らない。
  名前の指定だけをload成功の証拠にせず、同じGLib/env/moduleを使う固定probeでbackendの実型・固定値・writableを確認する。
  module欠落・load失敗・別backendへのfallbackではInspect/Launchを拒否する。
  この拒否は[GLibが指定backend不成立時に別候補を探す実装](https://github.com/GNOME/glib/blob/2.80.5/gio/giomodule.c#L1006-L1031)を踏まえたもの。
  通常Inspect/Launchのprobeはprivateな合成領域で実行し、利用中の設定を書き換えない。app非起動の事前検査CLIとは分離する。
  合成probeの成功後も、実profileのpath/permission/keyfileによりmoduleを初期化できなければappを失敗終了させ、owner回収へ進む。
  別backendへのfallbackを許す初期化失敗として返さず、標準backendで継続しない。実profileの不正とprobe成功を別に検査する。
- [GSettingsの通常bindはwritableをwidgetのsensitiveへ反映する](https://github.com/GNOME/glib/blob/2.80.5/gio/gsettings.c#L2947-L2956)。
  A0では実Mousepadの該当設定が変更不可であること、通常OpenがtabのままであることをGUIからも確かめる。
  keyfile委譲先が参照するhostのdefaults/locksも事前検査し、必要な可変設定が書けない環境を成功扱いにしない。

#### 既存の復元データは起動前に保護する

復元無効化を既存profileへ無条件に適用してはならない。
上流は[autosave-timer=0でautosave_disableを呼び、認識したautosaveファイルを削除する](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-history.c#L827-L941)。
helperが永続rootを削除しなくても、アプリ自身の起動処理でデータを失う可能性がある。

profile lock取得後、appをspawnする前に、固定keyfile内の `/org/xfce/mousepad/state/application/session`（型 `as`）と
`data/Mousepad/` の復元用データを
no-follow・件数/size上限付きでread-only検査する。空でない保存session、autosave候補、判別不能な状態があれば
`APP_STATE_RECOVERY_REQUIRED` でLaunchを拒否し、設定・文書・復元用ファイルを変更しない。
通常Recoverもこのデータを削除せず、process回収と文書復元を別の結果として示す。
自動移行・復元データ削除UIはL7aに含めず、利用者が別途データを回復・退避して再検査するまで起動を止める。
新規の空profileでは固定policyを適用する。通常Close、Stop、異常終了後も、同じstorage IDで再起動可能か、
復元データ保護の固定reasonで拒否されることを独立に検査する。

## 現行実装との差分

| 現在の契約 | L7aで必要な変更 |
| --- | --- |
| `eidolon-linux-desktop-v2`、pins schema 1、`gtk3-desktop`一項目 | v2を厳密に維持し、明示選択するv3/pins schema 2を追加 |
| `Selection.valid()` とKotlin catalogが固定一項目 | v3専用の固定二項目。未知ID、label、mode、項目重複を拒否 |
| nonce由来のmain/dialog app_id | demoは維持。実用アプリはnative側lease originとStartedを使う別admission |
| nonce配下の一時HOME、memory設定、busなし | sessionと永続profileを分離、専用bus、固定policy付きkeyfile backend |
| 通常serveは既に累積64KiB制限を適用しない | 継続drainを維持し、出力速度上限・制御処理との公平性を追加 |
| 自作demoのcounter/marker | 実用アプリに存在すると仮定せず、実ファイルと実設定のoracleを用いる |

単に `APP_ID` の許容値を増やしたり、nonce照合を外したりしてはならない。
変更対象の具体的な入口は末尾の作業分解に示す。

累積出力については、現行の[通常serveが `drain(enforce=False)` を使う](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/linux/eidolon_linux_desktop_session.py#L311-L313)ことと、
[64KiB超・複数lease期間の回帰テスト](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/tests/test_linux_desktop_session.py#L451-L460)を確認した。
旧adapter単体の既定引数と通常serveの呼出条件を区別し、L7aでこの制限を初めて解除するとは記録しない。

## バージョン・pins・固定catalog

v3のprotocol revisionを `eidolon-linux-desktop-v3` とする。wireのenvelope schemaは1を維持するが、
revisionごとに別の厳密なdecoder/state machineを選び、v2 messageをv3として読まない。
起動前に選んだpinsの版がdecoderを決め、遠隔の自己申告で降格・昇格しない。

新pinsは次の**全キーだけ**を持つ。hashは64桁lower hex、JSONは4096 bytes以内。

```json
{
  "schema": 2,
  "backend": "linux_desktop",
  "protocol_revision": "eidolon-linux-desktop-v3",
  "base_bundle_sha256": "<64 lower hex>",
  "desktop_bundle_sha256": "<64 lower hex>",
  "waypipe_sha256": "<64 lower hex>",
  "shm_fixture_sha256": "<64 lower hex>",
  "desktop_binary_sha256": "<64 lower hex: existing demo>",
  "app_manifest_sha256": "<64 lower hex>"
}
```

この例のplaceholderを設定へ貼り付けない。builderの検証済み出力だけを使用する。
`desktop_bundle_sha256` はv3 adapter/wire/owner/bus設定template、settings moduleのsource/binary/固定probe/登録情報とmanifest参照を含むbundleを固定する。
`app_manifest_sha256` は次の固定schema 1 objectのcanonical JSON hashとする。
canonical化は既存Pythonのsorted keys、compact separators、UTF-8方式に揃え、Rustのgolden vectorでも照合する。

| app manifestのfield | 型・検証 |
| --- | --- |
| `schema`, `app_id` | integer 1、固定 `mousepad-editor-v1` |
| `package`, `package_version`, `architecture` | packageは `mousepad`。残りは検証した実値。各ASCII文字列1〜128 bytes |
| `executable`, `executable_sha256` | 固定 `/usr/bin/mousepad`、実体のhash。root所有の検証済みregular executableを要求 |
| `dependency_fingerprint` | GTK/GLib/GtkSourceView/GIO module/DBusとschemaの検証記録のSHA256 |
| `settings_kind`, `settings_policy` | 固定 `eidolon-keyfile-v1`、`mousepad-single-main-no-restore-v1`。packageは既定GSettings backend利用版に限定 |
| `settings_module_sha256` | A0で検証したmodule binaryのSHA256。source/固定probe/登録情報はdesktop bundleにも含める |
| `settings_relative_path` | 固定 `config/glib-2.0/settings/keyfile`。moduleの委譲先と一致させる |
| `window_app_ids` | 当該GTK版で観測したmainの固定ID集合。CIのMousepad 0.6.3 / GTK 3.24.41 / NON_UNIQUE起動では `["mousepad"]`。空/未知をmainとして採用しない |
| `launch_policy`, `state_policy` | 固定 `mousepad-private-bus-v1`、`persistent-keyfile-locked-v1` |

初期候補の `org.xfce.mousepad` はGApplication/schemaの識別子だったが、
[実Waypipeを通したCIの初回window観測](https://github.com/tin-machine/eidolon-wayland/actions/runs/42502638269)
ではWayland `app_id` は `mousepad` だった。最初のMapped frameと250ms後の両方で同じ値を確認した。
この結果に合わせ、CI用packageの厳密なwindow IDは `mousepad` 一つへ固定する。
[GTK 3.24.41の実装](https://github.com/GNOME/gtk/blob/3.24.41/gdk/wayland/gdkwindow-wayland.c#L2151-L2155)も、
`g_get_prgname()` からWaylandのapp_idを設定している。詳細な識別子経路は[A/A0実装記録](linux-real-app-a0-foundation-20261109.md#waylandの識別子は実測で固定する)を参照。
別のpackage/GTK版へ値を推測適用せず、対象構成のA0で再確認する。GApplication IDやtitleからWayland IDを補完しない。

manifestからcommand、env、cwdを自由生成しない。これらはhelperの固定policyから組み立てる。
依存fingerprintの入力形式もschema化し、package名・版・対象schema/moduleのhashをsortして固定する。
通常診断へpackage一覧やpath本文を追加しない。必要な照合boolと固定reasonだけを出す。
package更新による不一致ではInspect/Launchを拒否し、再検査・pins再保存を要求する。
検証後のpackage更新とLaunchを並行させない。起動直前にも再照合し、不一致なら起動しない。
共有library全体をbundle化した再現可能配布の保証とは区別する。

v3 catalogは既存demoとMousepadの二項目を固定順で返す。`instance_limit=1`、`arbitrary_exec=false`、`terminal=false`。
二項目のどちらも検証済みの場合だけcatalog readyとする。Mousepad未導入ならv3を成功させず、既存v2を別途選択できる。
selectionは既存どおり `scope/revision/app_id` の三fieldだけ。revisionのhash入力へbundleとapp manifestを加える。
UI表示後にInspect/profile/pins/leaseが変われば古いselectionを失効させる。

v3のSSH bootstrapはv2の11引数（operation/context/UID/5 pins）の後ろへ、順に
`app_manifest_sha256`、`installation_id`、`profile_storage_id` を加える固定14引数とする。
Recoverだけは最後へ既存のexpected boot identityを加えた15引数。二つのIDは32桁lower hexへ正規化する。
相対pathやapp argvをbootstrapへ受け取らない。source hashは引き続きAPKに同梱して照合する。
v3側でcontext/scopeのhash入力に二つのIDとprotocol revisionを加え、v2のhash式は変更しない。

## 永続設定と一時sessionの分離

新設する `profile_storage_id` はAndroidのLinux保存設定へ紐付ける32桁lower hexの乱数。
session nonce、設定revision、鍵の一時captureから導出しない。同じhost identity/userの編集・再起動では保持し、
別host key/userへの変更時には新規IDにする。自動で旧設定をコピーしない。
既存profileには全回収後の明示保存で一度だけ追加する。接続profile本体のv2 wire schemaは変更せず、別のversion付き保存recordに置く。
installation IDとの組でremote stateを区別し、両IDをv3 context/scope・journalへ束縛する。
endpointのIP変更だけでデータが消えないよう、data keyにprofile bindingのrevision付きhashを使用しない。
L7aで使用する `installation_id` も16 random bytesの32桁lower hexとしてAndroidのnoBackup領域へ一度だけ保存する。
APK更新では保持し、再インストールでは新規作成する。SSH鍵や端末serialから生成せず、二つのIDの保存はatomic/CASにする。
profileを別installationへコピーして旧remote stateを自動採用する機能は設けない。

remote実HOMEをpasswd/認証済みUIDから確認し、次の構造を使う。以下は設計上の配置である。

```text
<real-home>/.local/share/eidolon/apps/<installation-id>/<profile-storage-id>/mousepad-editor-v1/
  owner.json          # schema、UID、二つのID、state_policy。secretやsession nonceを保存しない
  profile.lock        # 生存helperがflockを保持
  home/               # app HOME
  config/             # XDG_CONFIG_HOME、keyfile・Mousepad設定
  data/               # XDG_DATA_HOME
  state/              # XDG_STATE_HOME
  Documents/          # 初期cwd、保存した文書

<existing-session-root>/s-<nonce>/
  p, w, bus, bus.conf  # 既存Waypipe socketsと専用bus
  tmp/, cache/        # TMPDIR、XDG_CACHE_HOME
```

親directoryから所有UID/型/権限を検査し、directory 0700、record 0600、umask 077。
symlink・別UID・不正owner record・他profileのlockなら失敗し、既存pathへ上書きしない。
appが作る文書を全てhelper作成fileと同一視せず、正常cleanupは永続root以下を再帰探索・削除しない。
必要な親path検査はno-follow/openat方式とidentity再照合を使う。

helperは全回収とjournal barrier確認後にlockを取り、全子孫とpipeの終了確認後にだけ解放する。
lockが取れるだけでは旧owner不在の証拠にしない。helper死亡でlockが外れてもunknown journalは次のLaunchを止める。
Close/Stop/EOF/lease失効/Recoverはいずれも永続設定・Documentsを保持する。
明示的なprofileデータ削除UIと旧版データ移行はL7aに含めない。
通常Close時の設定flushは再起動後の実値で検査し、強制終了時の未flush設定保持までは保証しない。
アプリの復元無効化による自動削除は上記の起動前gateで防ぐ。永続rootをhelperが削除しないことだけでデータ保持を証明しない。

## 固定起動とD-Bus

app用envはinheritせず構築する。既存helper自体のHOMEは変更せず、子へ渡すHOMEだけを上記 `home/` にする。
環境の設計値は次のとおり。

| 変数 | 値 |
| --- | --- |
| `PATH`, locale | `/usr/bin:/bin`、検査済み `C.UTF-8` |
| `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` | 永続rootの対応directory |
| `XDG_RUNTIME_DIR`, `TMPDIR`, `XDG_CACHE_HOME` | 当該nonceのsession領域とその子 |
| `GDK_BACKEND`, `GDK_GL`, `GDK_RENDERING` | `wayland`、`disable`、`image`。実SHM commitを別途検査 |
| `GSETTINGS_BACKEND`, `GIO_EXTRA_MODULES`, `GIO_USE_VFS` | `eidolon-keyfile-v1`、検証済みv3 build-id配下の固定 `gio/`、`local` |
| `GTK_USE_PORTAL`, `NO_AT_BRIDGE` | `0`、`1`。これだけをportal非使用の証明にしない |
| `GTK_IM_MODULE` | 初版は既存demo同様 `gtk-im-context-simple`。remoteかな漢字変換は別受入れ |

`DISPLAY`、既存DBUS関連変数、`LD_PRELOAD`、`GTK_MODULES`、外部IME設定は引き継がない。
既存の `GIO_EXTRA_MODULES` も引き継がず、上表の検証済みdirectoryだけを設定する。
Waypipeが生成する専用 `WAYLAND_DISPLAY` はその起動連鎖だけで渡す。
固定command構造は次のとおり。実装はargv配列で生成し、shellを経由しない。

```text
waypipe <既存の固定SHM転送引数> server --
  /usr/bin/dbus-run-session --dbus-daemon=/usr/bin/dbus-daemon
  --config-file=<session>/bus.conf -- /usr/bin/mousepad --disable-server
```

bus.confは検証済みtemplateから生成し、listen先は当該0700 session内のUNIX socketだけにする。
同UIDのEXTERNAL認証、必要な通常bus通信を許可し、既存desktop/system/session設定をincludeしない。
標準servicedir・portal/dconf service activationを取り込まない。keyfileなのでdconf daemonは必要ない。
templateとdbus binaryのhash/版も検査対象にする。XMLへ外部文字列を無加工で埋め込まない。

起動ownerはWaypipe、dbus-run-session、dbus-daemon、Mousepadと、その起動に由来する子孫を監督する。
Startedのprocess identityは「leaseの起動root」であり、エディタのPIDだと表示しない。
pidfd/subreaperによる現在ownerの回収を維持し、host desktopの既存busやeditorへsignalしない。
bus EOF・wrapper終了・アプリ全窓Closeと全子孫の終了はそれぞれ観測する。

Open/Save/設定dialogは同じdisplayへ来ることを実測する。portalの名前取得や別processへの転送が必要なpackageなら不適合とする。
専用portalを新たに起動する対応は別設計に分ける。ヘルプURL、印刷、外部pluginの利用はL7a合格条件に含めない。

## StartedとWayland窓の対応

現行 `WindowReport` はwindow/app_id/parent等を持つが、アプリnonceに代わるoriginは持たない。
新しい `WaylandOrigin` をnative内部に追加し、以下を必須とする。

1. Launch enqueue前に `(runtime, session, request, nonce, scope, app_id)` を予約し、当該Linux専用displayへlease tokenを束縛する。
2. compositorのaccept時に、制御側が保持するdisplay/listener ownerからoriginを `ClientState` へ付与する。
   app_id、window title、remote JSONからoriginを作らない。同じUIDであることだけで別displayのclientを採用しない。
3. 当該displayは当該local Waypipeだけへ接続先を渡し、Termux/別GUI/別runtimeと共有しない。
   listener directory・local Waypipeの生存owner・終了状態を照合する。origin不明のacceptは実用アプリへ採用しない。
4. `WindowRecord/WindowReport` へそのclient由来のopaque originを伝播し、途中でapp_id変更やremapがあっても付け替えない。
5. 同じrequest/nonce/app_idのStarted受信と、起動前から存在しない一意なMapped mainを揃えて表示pipelineへ採用する。
   Started前のMappedは保留し、別slotへ流さない。mainの固定app_id照合も維持する。
   実入力はSDK表示完了後、現在のfull targetのSDK/描画・focus gateを満たしてから許可する。
6. Dialogは同じoriginの実xdg parent chainが採用済みmainへ到達するものだけを許可する。
   同じapp_id/空app_idのdialogはあり得るため、demo専用 `.dialog` suffixは要求しない。titleからparentを推測しない。
7. popup/subsurfaceは既存の同client surface tree制約を維持する。dialogも独立したfull targetとSDK所有を持つ。

mainの採用は一つのleaseにつき一度とし、採用した `WindowId`・origin・main/dialogの役割を固定する。
同じmainの正当なunmap/remapは再検査できるが、破棄済みmainを別のrootで置き換えず、孤立dialogをmainへ昇格させない。
parentの状態は「明示的に親なし」と「親参照が未解決・破棄済み」を区別する。
現行の[WindowReport生成は見つからない親を `None` にする](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/core/src/compositor/snapshot.rs#L843-L861)ため、
[v2のmain再探索](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/model/src/linux_desktop.rs#L125-L149)を同app_idのv3へそのまま流用しない。
main破棄後は終了観測・回収へ進み、一時unmapだけを正常Closeの証明にしない。

このoriginは認証済みSSH/専用displayの配送相関であり、改変されたremote host/同UIDの敵対processに対するアプリ実行証明ではない。
peer credentialに見えるのはlocal Waypipe側であり、remote MousepadのPIDだと解釈しない。
実装レビューではlistener共有がないこととaccept時のbindingを先に検査する。originを生成できない経路はfail closedにする。

二つ目の独立main、別origin parent、循環/失効parent、10枚目の窓は表示・入力へ採用しない。
非対応top-levelがアプリ操作に必要なら固定エラーで同じleaseのStopへ進め、黙って正常稼働と表示しない。
９窓上限はdialogを含む全体上限であり、アプリごとに９枚を追加確保する意味ではない。
Close後もraw窓・全世代target・SDK disposal receiptを追跡し、非表示だけでslot/所有を解放しない。

## 状態・期限・回収と旧版互換

通常UIは保存、接続、Inspect、選択、Launchを分ける。profile保存やアプリ再起動で自動Launchしない。
新しいエディタのラベルとともに「保存先は接続先Linux」「通信切断時は未保存作業が終了する場合がある」を表示する。
`StartedAwaitingWindow` を「表示完了」と表示せず、初frameとSDK attachを待つ。
表示後のメトリクスはCPU/RSS/Disk I/Oを未取得ならunknownとし、TermuxやQuest全体の値を代入しない。

leaseは現行15秒を維持し、actorのheartbeatは3秒間隔、制御/drain待機は最大100ms単位を初期値とする。
deadlineはmonotonic clock。期限後のheartbeatでownerを復活させず、GUIの描画量で制御actorを塞がない。
Launchからmain採用までは20秒、最初のmain採用からSDK表示完了までは10秒を初期上限とする。
期限切れは同じownerのStop/回収へ進む。自動再Launchしない。これらは設計値であり実測済みの保証値ではない。
これはアプリの利用時間制限ではない。正常稼働中は明示Close/Stopまたは障害まで継続する。
連続利用・Questスリープ・実ネットワーク遅延の総合受入れはL7bで別に行う。

### SDK表示完了までの有限な起動追跡

現行の[MappedはSHM snapshot生成時に成立する](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/core/src/compositor/snapshot.rs#L682-L704)。
[通常Hostは `running` でLaunch操作を完了する一方、表示にはpanel appliedとframeを別途要求する](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt#L395-L428)。
[SDK側のSceneObject/Surface待ち](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt#L245-L282)にはbinding完了の期限がない。
したがって、main採用だけで起動watchdogを解除すると、表示されないままheartbeatが継続する経路が残る。

v3ではnative coreがLaunch受理・origin/slot予約時刻 `t0` と、mainの固定WindowId、最初の採用時刻、
現在のpresentation target、最初の失敗reasonを起動追跡recordへ保存する。
現行の[DesktopLaunchはStart enqueue時に消費される](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/core/src/runtime.rs#L565-L583)ので、
このrecordは別に保持する。Kotlinの操作deadlineやUI更新を停止判断のownerにしない。

| 段階 | 成立条件・期限 |
| --- | --- |
| main待ち | captureしたStartedと同originのmainを照合。期限は `t0 + 20秒` |
| SDK表示待ち | 最初のmain採用時刻から10秒。scene、現在のCreate panel、SDK所有、成功ACK、renderer frameを同一full targetで照合 |
| ready | mainがMapped、panelがappliedかつerrorなし、同targetのSDK bindingが存在し、rendererがpauseされずframeが1枚以上。activity/scene/XRの起動前提も有効 |
| stopping / pending | deadline超過・取消・回復不能失敗。遅着Started/Mapped/ACKでreadyへ戻らず、元の失敗と回収状態を別々に保持 |

nativeの[AdapterAppliedは既にcommand_seqとfull targetを照合し、Createには対応frameを要求する](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/core/src/runtime.rs#L1469-L1512)。
これを再利用し、dialog・診断panel・旧generationのframe/ACKやKotlinからの独立したready申告を代用しない。
v2/Termuxの `Outcome::Matched` の意味は変更せず、v3専用 `presentation_state` を追加する。
同じtickでterminal/取消/deadline超過とready候補を観測した場合は、terminal/取消/deadline超過を優先する。

generation交換・remapでは現在のtargetを選び直すが、mainのWindowIdと元のdeadlineを保持する。
既存の装飾fallbackは一度だけ元の期限内で許し、新targetで再照合する。再失敗・期限超過でStopへ進む。
旧targetは全世代退役の追跡対象に残す。SDK callback欠落はwatchdogで扱い、明示的な作成/attach失敗は該当targetの失敗ACKへ接続する。

Launch受付前にnativeの `activity_visible`、現在scene、`XrState::Focused` を確認し、未成立なら `NOT_READY` でStartをenqueueしない。
ready前にこの前提を失った場合は起動を取消し、時計を停止して待たず同じownerをStopする。
ready後はstartup watchdogを再起動せず、既存のvisibility/lease処理とL7bの検査範囲に従う。
表示不能時の停止は通常Close要求へ置き換えず、保存dialogの応答を必要としない既存の
[bounded Stop経路](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/transport/src/linux_desktop/runtime.rs#L251-L268)を使う。
起動失敗reasonを `CANCELLED` で上書きせず、回収未確認ならjournalとSDK所有を保持する。

| 終了/失敗 | 必須の結果 |
| --- | --- |
| mainの通常Close | 保存dialogがあればGUIとして処理。app/全子孫・pipe終了→remote receipt→forward/local/全target回収 |
| 明示Stop・EOF・lease失効 | 入力解除後、現在ownerだけを終了。永続設定/文書を削除しない。通常Close成功として数えない |
| app/daemon/wrapper喪失 | 原因を固定tokenで記録。残る子孫とsocketを確認し、回収未確認ならjournalを維持 |
| helper喪失・同bootのunknown | 保存PIDへ後日signalしない。Recoverが不在を証明できなければ次のLaunchを拒否 |
| package/pins/設定の変更 | 旧selectionを失効。稼働中ownerの設定を差し替えず、全回収後の保存・Inspectを要求 |
| 再起動/旧journal | versionと同じhost/boot/UIDを照合。新しいdecoderで旧recordを再解釈しない |

v2のbootstrap/adapterとRecoverを残し、v2 pendingの回収にはcapture済み旧pins・旧helperを使う。
旧bundleを上書き・削除しない。v3 bundleはversion別のimmutable directoryへ追加し、全回収後に選択する。
baseは既存の `<real-home>/.local/lib/eidolon-linux/` に保持し、v3追加分は
`<real-home>/.local/lib/eidolon-linux-desktop-v3/<build-id>/` に置く。
build-idは `protocol_revision/base_bundle_sha256/desktop_bundle_sha256/app_manifest_sha256` の
四つのキーを持つcanonical JSONのSHA256。全v3 source/templateはdesktop bundleのhashへ含める。
builderとAPKが同じ式で導出し、自由なdirectoryをSSHへ渡さない。新builderは検証したbaseからの参照だけを許可し、
既存v3 build-idへの上書きも拒否する。旧 `checked_home()` の固定path検査を緩めず、v3専用validatorを追加する。
接続先選択のrecover barrier、診断パネルだけを許す入口、全SDK退役gateは維持する。
v2 pendingがあるままv3 pins/profileへ置換しようとしたら `RECOVERY_REQUIRED`。
旧helperがない場合は回収不能を示して止め、v3でclean済みに変換しない。

### 旧sourceの固定とRecoverの検証範囲

v2互換はdecoderの意味だけでなく、APKに同梱する旧source bytesも維持する。
現行は[5種類のsourceをinclude_bytesで取り込む](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/transport/src/linux_desktop/mod.rs#L24-L32)うえ、
[そのhashを計算し](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/transport/src/linux_desktop/mod.rs#L99-L106)、
[旧journalのsourcesと照合する](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/native/transport/src/linux_desktop/journal.rs#L338-L353)。
したがってv2と同じ動作を保つだけのsource編集では、既存pendingの回収互換を保てない。

基準revisionの `scripts/linux/eidolon-linux-session.py`、`eidolon_linux_desktop.py`、
`eidolon_linux_process_owner.py`、`eidolon_linux_desktop_session.py`、`scripts/fixtures/gtk3-desktop-demo.c` を
v2用としてbyte-for-byte維持する。v3 adapter/wireは別fileへ追加し、旧fileへv3分岐を足さない。
変更不要なbase/process owner/demoは両版で再利用できる。
pins/journalの版から既知のsource set・bootstrap・decoderを選び、未知hashやremote自己申告を実行許可にしない。
v2の固定directoryとserve/recoverの11/12引数を維持し、14/15引数とbuild-id directoryはv3だけに適用する。

Recoverはcapture済みpins/source、immutable manifest、host/UID/boot/context、owner/receiptと残骸の所有を検証する。
現在のMousepad/GTK/D-Bus packageとの `executable_sha256` / `dependency_fingerprint` 照合はInspect/Launchだけに適用する。
現行は[serve/recover分岐前にBundleを検証する](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/linux/eidolon_linux_desktop_session.py#L460-L481)ため、
v3ではimmutable helperの検証と現在のapp実行条件の検証を分ける。
package更新・削除後も正しい旧helper/manifest/receiptに基づく回収を許し、Recoverでappやversionコマンドを実行しない。
旧helper不在・改変や証拠のない同boot ownerは従来どおりpendingにする。

### 成功receiptの保管上限

現行は[成功receiptを自動GCしない方針](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/docs/normal-linux-gui-l1-l4-20261108.md#L109-L111)である。
[prepareはroot全entry数を制限し](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/linux/eidolon_linux_desktop_session.py#L111-L117)、
[finishはsession directoryを回収した後もreceiptを保持する](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/linux/eidolon_linux_desktop_session.py#L172-L200)。
レビュー時、空の合成rootで現行Stateのprepare→finishを繰り返し、81回正常完了後の82回目に
`STATE_LIMIT`、残存81 entriesとなることを確認した。実GUIの反復起動試験や上限解消の証拠ではない。

L7aはこの保持規則を維持し、事前検査でentry数と上限到達の有無を記録する。
通常cleanupで成功receiptを消さず、上限拒否をアプリ未導入や回収完了と誤表示しない。
継続利用に必要な成功receiptの受領・退役契約と、80件を超える反復接続の受入れは
[L7bの必須未完了項目](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-receipt-retirement)とする。

v3の固定reasonには `APP_NOT_INSTALLED`、`APP_PACKAGE_UNSUPPORTED`、`APP_DEPENDENCY_CHANGED`、
`APP_BACKEND_UNSUPPORTED`、`APP_SETTINGS_UNVERIFIED`、`APP_ORIGIN_UNKNOWN`、`APP_WINDOW_UNSUPPORTED`、
`APP_STATE_BUSY`、`APP_STATE_INVALID`、`APP_STATE_RECOVERY_REQUIRED`、`APP_LAUNCH_TIMEOUT`、`APP_PRESENTATION_TIMEOUT`、
`APP_PRESENTATION_FAILED`、`APP_STARTUP_CANCELLED`、`APP_OUTPUT_RATE_LIMIT` を追加する。
これらは新設予定。各decoderの既知enumとUI messageを同時に追加し、未知reasonはprotocol失敗にする。
失敗表示だけでcleanup完了へ遷移せず、現在ownerの回収状態を併記する。

app stdout/stderrは常時drainし、本文を通常ログへ保存しない。制御protocolの1行16KiB/未知field拒否は別に維持する。
実用アプリの累積bytesは診断counterとし、累積64KiBを終了条件にしない。
初期上限は未処理制御queue 64件、app出力read chunk 16KiB、保持本文0、各stream 1MiB/秒超が5秒継続で失敗・回収。
実装時にfake clockと実pipeで検査し、通常の継続出力がlease更新・Stopを妨げないことを確認する。

## 実装単位と完了条件

依存は `A → A0 → B/C → D → E → F`。BとCはA0でcontract確定後に分けて実装できる。
同じprotocol/modelファイルの編集は一人で統合し、途中の緩いdecoderをmainへ出さない。

A0は対象packageの実物で成立性を先に確認する作業であり、settings moduleの試作と固定host fixtureを含む。
専用bus・合成HOME/XDG・固定Waypipe/Wayland displayで、窓の順序、app_id、parent、設定変更不可、Open/Save、再起動を確認する。
合成復元データの保護試験もここで実行し、main前のdialogを実用admissionへ無条件採用する近道は使わない。
実測結果により対応版・manifest・policyを確定してからB/Cへ進む。A0成功でv3 transport/QuestのF受入れを代用しない。
事前検査Aは引き続きread-onlyとし、A0を自動実行しない。

| 単位 | 主な既存入口/新設file案 | 完了条件 |
| --- | --- | --- |
| A: 対応版とhost事前検査 | 新設 `scripts/inspect-linux-real-app`、`scripts/tests/test_linux_real_app_inspect.py` | read-only package/path/hash/schema/既存receipt数の照合。未導入・対応外backendを固定reasonで拒否。appのhelp/version起動もしない |
| A0: 実packageの成立性 | 新設 `scripts/check-linux-real-app --stage feasibility`、settings module試作・固定probe・host fixture | 既定backend利用版で固定3キーと可変設定が両立。実際の窓順序/parent、Open/Save、終了後再起動、復元データ保護を確認。未成立ならB/Cへ進めない |
| B: 固定manifestとv3 | `scripts/build-linux-desktop-bundle`、新設 `scripts/linux/eidolon_linux_desktop_v3.py`、`native/transport/src/linux_desktop/{mod,protocol}.rs` | v2 source/decoder不変、v3専用source set、moduleを含むpins/manifestのgolden vector。buildとprint-pinsはapp非起動 |
| C: 永続state・専用bus・設定policy | 新設 `scripts/linux/eidolon_linux_real_app.py` / `eidolon_linux_desktop_session_v3.py` / settings module source・固定probe | 永続root/lock/env/bus、起動前復元データgate、固定3キー、可変設定通知・永続化、全子孫回収。旧v2 wire/ownerは変更しない |
| D: origin・admission・起動追跡 | compositor `protocol.rs/snapshot.rs`、model `linux_desktop.rs`、core `runtime.rs/session.rs/window_slots.rs/panels.rs`、transport `linux_desktop/runtime.rs` | acceptからorigin、main役割固定、Started両順序、20秒/10秒期限、同targetのSDK ACK/frame、失敗からStop/全世代回収 |
| E: 通常UIと観測 | Android `LinuxDesktopPins.kt/LinuxDesktopCatalog.kt/NativeSessionHost.kt/SpatialPanelAdapter.kt/ApplicationLauncher.kt`、profile保存・debug観測 | native presentation状態に従う表示、SDK失敗ACK、固定二項目、storage IDのCAS保存、旧設定・診断panel入口の維持 |
| F: host end-to-end/Quest受入れ | `scripts/check-linux-real-app --stage acceptance` とfixture、既存 `scripts/normal_linux_evidence.py` を版分岐 | 実v3経路のMousepadを操作。本文なしreceiptと実ファイルoracle、旧APK→新APKのRecover、Questで同target表示・入力・終了を確認 |

新sourceをinclude_bytesでpinする場合はbuilder/source_hashes/manifest/testsを同一変更へ含める。
既存B4/B6、Termux、GTKデモのfixtureとv2 pendingを削除して検査を通してはならない。

### host検査の具体的なoracle

新設 `check-linux-real-app` はoperator管理下の0700合成rootだけを作り、通常利用の永続profileを流用しない。
実Mousepad本体を改造してcounterを埋め込まない。固定キー/ポインター操作をWayland入力経路から送る。

| 検査 | 独立した合格条件 |
| --- | --- |
| 既存desktop instanceとの独立 | 別bus/displayの実Mousepadを先に起動。本試験で新client/main/最初のframeが来て、先行instanceの窓/設定が変わらない |
| Startedと窓の順序 | ACK先行/窓先行の両方、旧nonce/旧request/別originを拒否。正常exitだけではpassしない |
| mainの役割・parent | main破棄後の孤立dialog・新rootをmainへ再採用しない。未解決parentと親なしを区別し、同WindowIdのremapでも旧target入力を拒否 |
| 表示完了と期限 | Mapped後のSurface callback欠落、SceneObject未完了、invalid Surface、失敗ACKで有限時間内にStopへ進む。dialog/診断/旧世代frameは成功に使わない |
| 起動中の世代・可視性 | 世代交換・装飾fallbackでdeadlineを延ばさない。ready前のactivity/scene/XR前提喪失は取消。失敗後の遅着ACKでreadyへ戻らない |
| Open→編集→Save As | 専用Documentsへ合成UTF-8 fileを事前配置。GUI Open dialogを経て開き、既知ASCIIを追記、Save As dialogで別名保存。hostが実bytesを照合 |
| 上書き/未保存Close | 確認dialogの実parent/full targetと取消を検査。保存/破棄は合成fileだけで実行し、app終了原因を区別 |
| 設定保持 | GUIで行番号表示を変更→通常Close→新nonce・同storage IDで再起動。同じ設定の実値とGUI状態を照合。別storage IDには漏れない |
| 設定policy | 固定3キーの型/値/writable、write/reset/混在write_tree拒否、通常キーの変更通知を検査。実設定画面で変更不可、Openが同mainのtabであることを照合 |
| backendの成立 | 専用backendの実型、module/依存hashを照合。欠落・load失敗・fallback・Mousepad独自backend利用版を拒否し、設定を保護できないまま起動しない |
| 復元データ保護・再起動 | 空profileの通常Close/Stop/異常終了後は同storage IDで再起動できる。合成の非空session/autosaveを配置した場合はapp spawn前に拒否し、候補bytes・設定・文書を保持 |
| state境界 | session削除後も設定/文書が残り、cache/socket/busだけが消える。既存HOMEの事前sentinelが不変 |
| 連続出力 | 累積64KiB超の通常出力でも継続。速度制限・queue超過・pipe EOF遅延時はheartbeatとStopを保ったまま固定失敗・回収 |
| 所有/失敗 | 二重Launch、lock競合、link差替え、permission、app/dbus起動失敗、未知main/dialog、９窓上限、古い世代の入力を拒否 |
| 回収と互換 | Close/Stop/lease/切断ごとに全子孫・pidfd・pipe・StreamLocal・SHM・SDKを照合。v2 pendingがv3開始を阻止し、旧Recoverでだけ解消する |
| APK更新と旧source | 旧revision由来の固定source hashes/wire/journalをfixtureにし、新sourceから再生成しない。旧APKのv2 pendingを新APKで回収。同bootのreceipt有/無・boot変更・誤版dispatchを検査 |
| package変更後のRecover | app/依存の更新・削除で新規Launchは拒否。正しい旧helper/manifestと成功receipt又はboot変更の証拠によるRecoverは可能。同bootのunknownは維持 |
| receipt容量 | 合成rootの上限と満杯拒否を確認。成功receipt保持を検査し、未実装のGC/長期反復運用をL7a成功で認定しない |

実GUI testのmissing package/displayをpass扱いにしない。CIの必須jobで検証済みpackage版を導入し、
版不一致は固定fail。環境都合のskipは明示し、そのrunでL7aを合格にしない。
Xvfbによる代替表示だけではQuestへ送るWayland経路の合格に数えない。
fixture起動timeoutはstderr長・process状態・probe段階を本文なしで残し、無制限再試行しない。

### 再現手順とQuestの最終確認

以下は**v3実装後の順番**。`inspect-linux-real-app` / `check-linux-real-app` は最初のPRで追加するが、
feasibilityの未確認項目と未実装acceptanceを成功にはしない。

1. 対象Linuxをread-only事前検査し、OS/arch/package版/hash・GTK/backend・schema・dbus toolのreceiptを保存する。
2. A0の実package/settings module検査後、v3実装のhost acceptanceを成功させ、そのreceiptに対応するmanifest/pinsをbuilderから出力する。
3. 旧v2を全回収した状態でv3 bundleを追加し、Questの同じLinux profileにpinsとstorage IDを明示保存する。
4. 通常Connect→Inspect→Mousepadを一度起動し、source digest/APK/pins/profile revision/lease/full targetを固定して観測する。
5. 自動観測で同じmain/full targetのSDK ACK・初frame・dialog・回収を記録し、操作者がHMDで英数入力、Open/Save、resize、focus往復、Closeを確認する。
6. 同じprofileで再起動し設定/文書が残ることと固定設定を変更できないことを確認する。通常Stopと切断は別runで行う。
7. 旧APKでv2 pendingを作った状態から新APKへ更新し、旧bundleでのRecoverを確認する。host fixture成功だけでこの実機更新を合格にしない。

既存の検査として `python3 scripts/check-design`、`scripts/check-host --suite all` とAndroid CIを維持する。
新検査のCLIは `python3 scripts/inspect-linux-real-app --artifact-root <private-dir>` と
`python3 scripts/check-linux-real-app --stage feasibility|acceptance --artifact-root <private-dir>` を新設する。
`stage` は二値のenum（上記の縦棒は選択肢の表記）とし、自由command/argv引数を持たせない。
前者はappを起動せずread-only、後者は合成profileだけで明示実行する。package導入/upgradeを内部で自動実行しない。

`check-design` は既存の `docs/design/` 契約を検査するもので、[このL7a文書は直接の検査対象ではない](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/check-design#L89-L98)。
文書改訂では相対リンク・anchor・表/コードフェンス・差分を別途検査し、設計検査成功を新moduleやQuestの動作証明にしない。

公開記録はsource/CI ID、hash、段階、固定reason、照合bool、未確認項目に限定する。
file内容・絶対保存path・window title・入力本文・画像・鍵/endpointは公開しない。
HMD/物理入力/remote IME/長時間/L7bの未実施をhost成功で埋めない。

## 設計時点の未確定事項

- 対象LinuxとMousepad/GLib package版は未固定。参照0.6.3/2.80.5との差、既定backend利用、CIで観測したmain app_id `mousepad` が対象構成でも成立すること、追加dialogのparentをA/A0で確定する。
- 専用GIO settings module・固定probeはA0検査基盤として追加する。公開APIの委譲/通知、実GUIの設定変更不可、復元データ保護がA0全体で成立するまで対応済みにしない。
- Mousepad独自keyfile backendを使うbuildはL7aでは未対応。別経路への対応は別設計とし、未知pathをmanifestから許可しない。
- 専用busの最小configとWaypipeの終了連鎖は実host試験で確定する。想定外serviceが必要ならcatalogを有効化せず設計を見直す。
- native origin・固定main役割・SDK表示watchdogは未実装。20秒/10秒は初期設計値で、対象Questで検証する。
- 設定保持は通常終了で受け入れる。通信断後の未保存文書復元、session継続、portal、複数mainは別の拡張である。
- 成功receiptの自動退役はL7bの必須未完了項目。現行entry上限の確認を、反復運用の受入れとしない。

これらは実装時の検査gateであり、現在対応済みという記録ではない。
