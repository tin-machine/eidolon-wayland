# L7a A/A0: 実用アプリ検査と設定backendの実装基盤

状態: **AとA0の検査基盤を実装中。A0全体・v3通常起動・Quest受入れは未完了**。
この変更は[詳細設計](linux-real-app-l7a-design-20261109.md)の `A → A0 → B/C → D → E → F` の最初のPRに対応する。
検査で未確認の項目を残し、A0成立前に通常catalogへMousepadを追加しない。

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
