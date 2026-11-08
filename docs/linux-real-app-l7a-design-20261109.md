# L7a: 別Linuxの実用アプリ一種の詳細設計

状態: **設計完了・実装未着手・対象Linux/Questでの実用アプリ受入れ前**。2026-11-09作成。
基準revisionはPR #2/#3マージ後の `3bcd6cfb44dd21e8c9979a106f93434b52fa396c`。
本書の新設API、schema、コマンドは実装契約案であり、現在利用可能という意味ではない。

[L7aの要求](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-real-app)を、
[通常GTKデモのL5/L6](normal-linux-gui-l5-l6-20261108.md)から実装できる単位へ分解する。
今回は設計とpushまでを行い、実用アプリの実装・対象hostへのpackage導入・配備は次の作業に残す。

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
| 設定 | Eidolon専用の永続HOME/XDGとkeyfile設定。ユーザー本来のHOME/dconfを編集しない |
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
既定backend利用版には `GSETTINGS_BACKEND=keyfile` を渡す。memory backendは採用しない。

[0.6.3のdialog実装](https://raw.githubusercontent.com/xfce-mirror/mousepad/mousepad-0.6.3/mousepad/mousepad-dialogs.c)には
GTKのfile chooser dialogがある。初版はこの非portal経路を検査対象にする。
ここから「任意版・任意dialogが必ず同じ経路になる」とは推定しない。

[dbus-run-sessionの仕様](https://manpages.debian.org/bookworm/dbus-daemon/dbus-run-session.1.en.html)では、
指定PROGRAMと専用busの寿命が結び付く。ただしbusとappは別processである。
wrapperのPIDや正常exitだけをアプリ表示・全子孫回収の証拠にしない。

## 現行実装との差分

| 現在の契約 | L7aで必要な変更 |
| --- | --- |
| `eidolon-linux-desktop-v2`、pins schema 1、`gtk3-desktop`一項目 | v2を厳密に維持し、明示選択するv3/pins schema 2を追加 |
| `Selection.valid()` とKotlin catalogが固定一項目 | v3専用の固定二項目。未知ID、label、mode、項目重複を拒否 |
| nonce由来のmain/dialog app_id | demoは維持。実用アプリはnative側lease originとStartedを使う別admission |
| nonce配下の一時HOME、memory設定、busなし | session一時領域と永続profile領域を分離、専用bus、keyfile設定 |
| app stdout/stderrの累積64KiB超で失敗 | 制御メッセージ上限とapp出力の継続drain・速度上限を分離 |
| 自作demoのcounter/marker | 実用アプリに存在すると仮定せず、実ファイルと実設定のoracleを用いる |

単に `APP_ID` の許容値を増やしたり、nonce照合を外したりしてはならない。
変更対象の具体的な入口は末尾の作業分解に示す。

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
`desktop_bundle_sha256` はv3 adapter/wire/owner/bus設定templateとmanifest参照を含むbundleを固定する。
`app_manifest_sha256` は次の固定schema 1 objectのcanonical JSON hashとする。
canonical化は既存Pythonのsorted keys、compact separators、UTF-8方式に揃え、Rustのgolden vectorでも照合する。

| app manifestのfield | 型・検証 |
| --- | --- |
| `schema`, `app_id` | integer 1、固定 `mousepad-editor-v1` |
| `package`, `package_version`, `architecture` | packageは `mousepad`。残りは検証した実値。各ASCII文字列1〜128 bytes |
| `executable`, `executable_sha256` | 固定 `/usr/bin/mousepad`、実体のhash。root所有の検証済みregular executableを要求 |
| `dependency_fingerprint` | GTK/GLib/GtkSourceView/GIO module/DBusとschemaの検証記録のSHA256 |
| `settings_kind` | `glib-keyfile` または `mousepad-keyfile` の既知enum。package/sourceと永続化試験で決める |
| `settings_relative_path` | 前者は `config/glib-2.0/settings/keyfile`。後者は当該packageで確認した固定相対pathを対応定義に列挙 |
| `window_app_ids` | 当該GTK版で観測したmainの固定ID集合。初期候補 `org.xfce.mousepad`。空/未知をmainとして採用しない |
| `launch_policy`, `state_policy` | 固定 `mousepad-private-bus-v1`、`persistent-keyfile-v1` |

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

## 固定起動とD-Bus

app用envはinheritせず構築する。既存helper自体のHOMEは変更せず、子へ渡すHOMEだけを上記 `home/` にする。
環境の設計値は次のとおり。

| 変数 | 値 |
| --- | --- |
| `PATH`, locale | `/usr/bin:/bin`、検査済み `C.UTF-8` |
| `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` | 永続rootの対応directory |
| `XDG_RUNTIME_DIR`, `TMPDIR`, `XDG_CACHE_HOME` | 当該nonceのsession領域とその子 |
| `GDK_BACKEND`, `GDK_GL`, `GDK_RENDERING` | `wayland`、`disable`、`image`。実SHM commitを別途検査 |
| `GSETTINGS_BACKEND`, `GIO_USE_VFS` | `keyfile`、`local` |
| `GTK_USE_PORTAL`, `NO_AT_BRIDGE` | `0`、`1`。これだけをportal非使用の証明にしない |
| `GTK_IM_MODULE` | 初版は既存demo同様 `gtk-im-context-simple`。remoteかな漢字変換は別受入れ |

`DISPLAY`、既存DBUS関連変数、`LD_PRELOAD`、`GTK_MODULES`、外部IME設定は引き継がない。
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
5. 同じrequest/nonce/app_idのStarted受信と、起動前から存在しない一意なMapped mainを揃えて初めて表示・入力を許可する。
   Started前のMappedは保留し、別slotへ流さない。mainの固定app_id照合も維持する。
6. Dialogは同じoriginの実xdg parent chainが採用済みmainへ到達するものだけを許可する。
   同じapp_id/空app_idのdialogはあり得るため、demo専用 `.dialog` suffixは要求しない。titleからparentを推測しない。
7. popup/subsurfaceは既存の同client surface tree制約を維持する。dialogも独立したfull targetとSDK所有を持つ。

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
Launchからmain採用までは20秒を上限とし、期限切れは同じownerのStop/回収へ進む。自動再Launchしない。
これはアプリの利用時間制限ではない。正常稼働中は明示Close/Stopまたは障害まで継続する。
連続利用・Questスリープ・実ネットワーク遅延の総合受入れはL7bで別に行う。

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

v3の固定reasonには `APP_NOT_INSTALLED`、`APP_PACKAGE_UNSUPPORTED`、`APP_DEPENDENCY_CHANGED`、
`APP_BACKEND_UNSUPPORTED`、`APP_SETTINGS_UNVERIFIED`、`APP_ORIGIN_UNKNOWN`、`APP_WINDOW_UNSUPPORTED`、
`APP_STATE_BUSY`、`APP_STATE_INVALID`、`APP_LAUNCH_TIMEOUT`、`APP_OUTPUT_RATE_LIMIT` を追加する。
これらは新設予定。各decoderの既知enumとUI messageを同時に追加し、未知reasonはprotocol失敗にする。
失敗表示だけでcleanup完了へ遷移せず、現在ownerの回収状態を併記する。

app stdout/stderrは常時drainし、本文を通常ログへ保存しない。制御protocolの1行16KiB/未知field拒否は別に維持する。
実用アプリの累積bytesは診断counterとし、累積64KiBを終了条件にしない。
初期上限は未処理制御queue 64件、app出力read chunk 16KiB、保持本文0、各stream 1MiB/秒超が5秒継続で失敗・回収。
実装時にfake clockと実pipeで検査し、通常の継続出力がlease更新・Stopを妨げないことを確認する。

## 実装単位と完了条件

依存は `A → B/C → D → E → F`。BとCはcontract確定後に分けて実装できる。
同じprotocol/modelファイルの編集は一人で統合し、途中の緩いdecoderをmainへ出さない。

| 単位 | 主な既存入口/新設file案 | 完了条件 |
| --- | --- | --- |
| A: 対応版とhost事前検査 | 新設 `scripts/inspect-linux-real-app`、`scripts/tests/test_linux_real_app_inspect.py` | read-only package/path/hash/schemaと対応定義の照合。更新・未導入・backend不一致を固定reasonで拒否。appのhelp/version起動もしない |
| B: 固定manifestとv3 | `scripts/build-linux-desktop-bundle`、`scripts/linux/eidolon_linux_desktop.py`、`native/transport/src/linux_desktop/{mod,protocol}.rs` | v2 decoder不変、schema 2 pins/manifestの相互golden vector、自由argv不可、buildとprint-pinsはapp非起動 |
| C: 永続state・専用bus | 新設 `scripts/linux/eidolon_linux_real_app.py`、既存 `eidolon_linux_desktop_session.py` / process owner | 永続root/lock/env/固定bus、全子孫回収。正常終了後の設定保持と既存HOME不変 |
| D: originとadmission | `native/core/src/compositor/{protocol,snapshot}.rs`、`native/model/src/linux_desktop.rs`、core `runtime.rs/session.rs/window_slots.rs` | acceptからorigin伝播、Started両順序、main一意、parent chain、９窓/世代/全回収の回帰 |
| E: 通常UIと観測 | Android `LinuxDesktopPins.kt/LinuxDesktopCatalog.kt/NativeSessionHost.kt/ApplicationLauncher.kt`、profile保存・debug観測 | 固定二項目、profile storage IDのCAS保存、待機/回収/非対応の表示、旧設定・診断panel入口の維持 |
| F: host end-to-end/Quest受入れ準備 | 新設 `scripts/check-linux-real-app` とfixture、既存 `scripts/normal_linux_evidence.py` を版分岐 | actual Mousepadを本物のWaypipe/Wayland compositorで操作。本文なしreceiptと合成ファイルの独立oracle |

新sourceをinclude_bytesでpinする場合はbuilder/source_hashes/manifest/testsを同一変更へ含める。
既存B4/B6、Termux、GTKデモのfixtureとv2 pendingを削除して検査を通してはならない。

### host検査の具体的なoracle

新設 `check-linux-real-app` はoperator管理下の0700合成rootだけを作り、通常利用の永続profileを流用しない。
実Mousepad本体を改造してcounterを埋め込まない。固定キー/ポインター操作をWayland入力経路から送る。

| 検査 | 独立した合格条件 |
| --- | --- |
| 既存desktop instanceとの独立 | 別bus/displayの実Mousepadを先に起動。本試験で新client/main/最初のframeが来て、先行instanceの窓/設定が変わらない |
| Startedと窓の順序 | ACK先行/窓先行の両方、旧nonce/旧request/別originを拒否。正常exitだけではpassしない |
| Open→編集→Save As | 専用Documentsへ合成UTF-8 fileを事前配置。GUI Open dialogを経て開き、既知ASCIIを追記、Save As dialogで別名保存。hostが実bytesを照合 |
| 上書き/未保存Close | 確認dialogの実parent/full targetと取消を検査。保存/破棄は合成fileだけで実行し、app終了原因を区別 |
| 設定保持 | GUIで行番号表示を変更→通常Close→新nonce・同storage IDで再起動。同じ設定の実値とGUI状態を照合。別storage IDには漏れない |
| state境界 | session削除後も設定/文書が残り、cache/socket/busだけが消える。既存HOMEの事前sentinelが不変 |
| 連続出力 | 累積64KiB超の通常出力でも継続。速度制限・queue超過・pipe EOF遅延時はheartbeatとStopを保ったまま固定失敗・回収 |
| 所有/失敗 | 二重Launch、lock競合、link差替え、permission、app/dbus起動失敗、未知main/dialog、９窓上限、古い世代の入力を拒否 |
| 回収と互換 | Close/Stop/lease/切断ごとに全子孫・pidfd・pipe・StreamLocal・SHM・SDKを照合。v2 pendingがv3開始を阻止し、旧Recoverでだけ解消する |

実GUI testのmissing package/displayをpass扱いにしない。CIの必須jobで検証済みpackage版を導入し、
版不一致は固定fail。環境都合のskipは明示し、そのrunでL7aを合格にしない。
Xvfbによる代替表示だけではQuestへ送るWayland経路の合格に数えない。
fixture起動timeoutはstderr長・process状態・probe段階を本文なしで残し、無制限再試行しない。

### 再現手順とQuestの最終確認

以下は**実装後の順番**。現時点では `inspect-linux-real-app` / `check-linux-real-app` は存在しない。

1. 対象Linuxをread-only事前検査し、OS/arch/package版/hash・GTK/backend・schema・dbus toolのreceiptを保存する。
2. host fixtureで上記全項目を成功させ、そのreceiptに対応するmanifest/pinsをbuilderから出力する。
3. 旧v2を全回収した状態でv3 bundleを追加し、Questの同じLinux profileにpinsとstorage IDを明示保存する。
4. 通常Connect→Inspect→Mousepadを一度起動し、source digest/APK/pins/profile revision/lease/full targetを固定して観測する。
5. 自動観測で起動・dialog・frame・回収を進めた後、操作者がHMDで英数入力、Open/Save、resize、focus往復、Closeを確認する。
6. 同じprofileで再起動し設定/文書が残ることを確認する。通常Stopと切断は別runで行う。

既存の検査として `python3 scripts/check-design`、`scripts/check-host --suite all` とAndroid CIを維持する。
新検査のCLIは `python3 scripts/inspect-linux-real-app --artifact-root <private-dir>` と
`python3 scripts/check-linux-real-app --artifact-root <private-dir>` を新設し、自由command/argv引数を持たせない。
前者はappを起動せずread-only、後者は合成profileだけで明示実行する。package導入/upgradeを内部で自動実行しない。

公開記録はsource/CI ID、hash、段階、固定reason、照合bool、未確認項目に限定する。
file内容・絶対保存path・window title・入力本文・画像・鍵/endpointは公開しない。
HMD/物理入力/remote IME/長時間/L7bの未実施をhost成功で埋めない。

## 設計時点の未確定事項

- 対象LinuxとそのMousepad package版は未固定。参照0.6.3との差、main app_id、keyfileの実path、追加dialogのparentをA/Fで確定する。
- 当該packageが独自keyfile backendを使う場合、相対pathを固定対応定義へ追加する。未確認pathをmanifestから無制限に許可しない。
- 専用busの最小configとWaypipeの終了連鎖は実host試験で確定する。想定外serviceが必要ならcatalogを有効化せず設計を見直す。
- 現行のnative origin追加は未実装。既存demoのnonce guardを緩める近道は採らない。
- 設定保持は通常終了で受け入れる。通信断後の未保存文書復元、session継続、portal、複数mainは別の拡張である。

これらは実装時の検査gateであり、現在対応済みという記録ではない。
