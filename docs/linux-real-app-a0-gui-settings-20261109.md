# L7a A0 PR2: GUI設定の保持と保護設定の操作不可

2026-11-09。状態: **PR2の実装記録。今回のheadに対する実Mousepad・専用CI・host/Android CIの検証結果は、末尾の検証欄とPR本文で確定する**。
実装したoracleの存在と、そのoracleが実環境で成立したことを区別する。
A0が成立した場合も、v3通常起動・Android通常UI・Quest実機受入れは未完了のままである。

## 基準と今回の範囲

開始時に最新main
[`2efca7b5347ebd14a38adeceb3a490d8d71e2262`](https://github.com/tin-machine/eidolon-wayland/commit/2efca7b5347ebd14a38adeceb3a490d8d71e2262)
を確認した。treeは `d418312dc2b8eb349eded063deef62e64f74a7ec`。
[PR #5](https://github.com/tin-machine/eidolon-wayland/pull/5)はマージ済みで、その検証済みhead `de1af7e7a460b552c303896b23c273f0384ae17e` とtreeが一致する。
後続のPRやmainの追加変更はなく、リポジトリに追跡された追加のAGENTS.md等もなかった。
この最新mainから `feat/a0-gui-settings` を作成した。

引き継ぎ時に実行中だったmainの
[専用foundation CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42632193803)と
[host/Android CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42632193815)は、開始時の再確認で両方とも完了・成功だった。
これはPR2の検証結果ではなく、変更前の基準である。

対象は[次工程の計画](linux-real-app-a0-next-steps-20261109.md#3-次のpr2-gui設定の保持と保護設定の操作不可)のPR2であり、GitHubのPR番号を意味しない。
[PR1の再起動・instance独立harness](linux-real-app-a0-restart-20261109.md)、
[A/A0検査基盤](linux-real-app-a0-foundation-20261109.md)、
[L7a設計](linux-real-app-l7a-design-20261109.md)を踏まえ、次の二つの未完了項目を実測する。

- `gui_setting_persistence`: 新しい合成profileでGUIから行番号表示を変更し、通常Close・全回収・同じprofileの新runでGUIと保存値の保持を照合する。別profileへの変更漏れも調べる。
- `protected_settings_gui_readonly`: 実Preferencesで `opening-mode` と `session-restore` の操作不可を観測し、同じ入力経路で可変設定を変更できる正例、通常Openの実tab動作、backendと復元データ保護を照合する。

v3 bundle/pins/helper、production storage ID、native origin/SDK表示watchdog、Android通常UI、Quest受入れは今回の対象外である。
固定v2の5 sourceと [golden hash](../scripts/tests/fixtures/linux-desktop-v2-e401918-sources.json)を変更しない。

## GUI観測方式を先に決める

既存のcompositor snapshotはWindowId、parent、app_id、frame、入力count等を示すが、GTK widgetのsensitivityや行番号の状態を示さない。
`NO_AT_BRIDGE=1` とactivation serviceを置かないprivate busを維持するため、AT-SPIが既に利用可能であるとは扱わない。

合成GUI runに限り、固定sourceからbuildした読み取り専用のGTK3 moduleを、子processの `GTK3_MODULES` に絶対pathで指定する。
[GTK公式のmodule読込み仕様](https://docs.gtk.org/gtk3/running.html#environment-variables)はGTK3専用moduleに `GTK3_MODULES` を用いることと、絶対pathで直接loadできることを説明している。
通常helper、desktop session、利用者のMousepadへこのmoduleを有効化しない。

実装入口は [GTK observer fixture](../scripts/fixtures/linux-real-app-gui-observer.c)、
[observer client](../scripts/linux_real_app_gui_observer.py)、
[固定builder](../scripts/linux_real_app_build.py)、
[run環境と所有](../scripts/linux_real_app_wayland.py)である。
Mousepadのsource/package本体は変更しない。Mousepadへ検査counterを埋め込んだり、observerからactionをactivateしたり、widget/property/GSettingsを書き換えたりしない。
検査操作は既存の [Wayland Control](../scripts/fixtures/linux-real-app-wayland/driver.py)を通す実key/pointer eventだけで行う。

### 読み取る対象は固定する

moduleは固定のMousepad/GTK型、GtkBuildable ID、GtkActionable action-nameを用いて対象を一意に識別する。
翻訳された表示文字列、window title、ファイル本文、pixelを対象識別に使わない。
対象の欠落、複数候補、未map、型不一致を既定値へ読み替えない。

`gtk_window_list_toplevels()`には、[GtkMenuが内部生成するGtkWindow](https://github.com/GNOME/gtk/blob/3.24.41/gtk/gtkmenu.c#L1344)も含まれる。
Mousepadは[起動時に複数のmenu modelを展開](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c#L1056-L1089)するため、
このGTK内部列挙数をWaylandのWindowId数と同じ32件へ制限しない。読み取り走査は4096 objectで打ち切り、main/Preferencesそれぞれの一意性は引き続き厳密に要求する。
display種別、main/Preferences/view/notebook/menubar/targetの重複、走査上限は個別の固定reasonで拒否し、失敗応答にもrun/profile/sequenceと厳密schemaを要求する。
head `3dda694`の`APP_GUI_OBSERVER_TOPOLOGY`は細分化前の記録であり、この記録だけから特定の拒否理由を断定しない。

| 観測対象 | 固定の根拠と観測内容 |
| --- | --- |
| main | `MousepadWindow`。対応する一つのWayland mainと照合する |
| Edit menu | 一つのGtkMenuBar、固定GMenuModelの`edit`と`edit.preferences`、leaf action `app.preferences`を照合し、実submenuのmapped状態と選択項目を読む |
| Preferences | `MousepadPrefsDialog`。GTKの実transient parentとWaylandのparentをmainへ照合する |
| 行番号checkbutton | action-name `app.preferences.view.show-line-numbers`。mapped、実効sensitive、activeを読む |
| editorの行番号 | mainのmapped `MousepadView` / GtkSourceViewが持つ `show-line-numbers`。checkbuttonの値だけで描画設定を推定しない |
| Preferences notebook | GtkBuildable ID `/prefs/main-notebook`。実表示pageと各pageの対象controlを照合する |
| opening-mode | GtkBuildable ID `/prefs/window/notebook/opening-mode-combo`。mapped、実効sensitive、固定active-idを読む |
| session-restore | GtkBuildable ID `/prefs/file/history/session-combo`。mapped、実効sensitive、固定active-idを読む |
| main notebook | 文書tab数を読む。ファイル名やtabのlabelは読み出して公開しない |

上流の根拠は、固定Mousepad commit
[`65fbfe0459259f820fcc8b9c4dd454228304842a`](https://github.com/xfce-mirror/mousepad/tree/65fbfe0459259f820fcc8b9c4dd454228304842a)の
[Preferences UI定義](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/ui/mousepad-prefs-dialog.ui)、
[Preferencesのbinding](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-prefs-dialog.c#L691-L696)、
[viewの設定binding](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-view.c#L297-L310)、
[Preferencesのtransient設定](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-application.c#L1631-L1633)である。

`gtk_widget_get_sensitive()` の単体propertyだけでは、ancestorのdisabledを見落とす。
[gtk_widget_is_sensitive()](https://docs.gtk.org/gtk3/method.Widget.is_sensitive.html)でancestorを含む実効sensitivityを読む。
comboの値は[gtk_combo_box_get_active_id()](https://docs.gtk.org/gtk3/method.ComboBox.get_active_id.html)から取得し、固定の許容値と照合する。
NULLや未知値をpolicy成立へ変換しない。

### Preferencesを開く実入力

head `5a07a43e084beb08972f9f4843fb4f050062e224`の[専用CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42638603870)では、
従来の実アプリcaseと125秒保持後の応答まで進み、追加GUI caseの`Alt+E`によるPreferences起動が`DIALOG_TIMEOUT`になった。
A0はfalseのままであり、失敗したGUI runと両workerの回収完了をreceiptで確認した。

[現在のnative popup policy](../native/core/src/compositor/popup.rs)は、grabをbutton serialかつpointer押下中に限定している。
一方、GTK 3.24.41の[popup生成](https://github.com/GNOME/gtk/blob/3.24.41/gdk/wayland/gdkwindow-wayland.c#L3025-L3036)は、
[最新のkeyboardまたはpointer serial](https://github.com/GNOME/gtk/blob/3.24.41/gdk/wayland/gdkdevice-wayland.c#L5430-L5467)を使う。
keyboard由来のメニューgrabはこのnative条件を満たさない。単にF10へ置き換えても解決したとは扱わない。

fixtureの固定`button`要求でprimary pressとreleaseを分け、実Edit menu上でpressを継続する。
固定modelと対応した実submenuのmapped状態、およびcompositorのpopup layer増加を待ち、`finally`でreleaseする。
その間にfocusを再設定して押下をresetしない。releaseを確認した後に実End keyを送り、固定Preferences項目の選択を読み取ってからEnterで開く。
GActionのactivate、GTK setter、メニューlabelの読み取りは使用しない。
実wire回帰検査では、downのみをclientが受信したcheckpointを挟んでupを送り、最終press/releaseとnative countを照合する。

この変更はfixtureの入力手順と読み取り観測に限定する。productionのkeyboard起点popupの扱いは別の検討事項として残り、A0成功から通常UIの全経路を受入れ済みとはしない。

### 観測のfreshnessと入力座標

observerとのやり取りは、そのrunのprivate runtimeに限定する。
runtimeに作る合成markerへrun/profile identityとprofile rootのdevice/inodeを記録し、module起動時に照合する。
UNIX socketの固定 `OBSERVE <sequence>` 要求をGTK main loop上で処理し、その応答だけを受理する。
socket/markerの所有・権限、同UIDのpeer、連続したpeer PID、sequence、run/profile identity、型、上限、対象識別を照合する。
前のrunや前の操作の応答を再掲してfreshな観測と扱わない。
observerが取得できない場合はGUI oracleを未完了とし、backend probeへfallbackして合格させない。

widget中心は [gtk_widget_translate_coordinates()](https://docs.gtk.org/gtk3/method.Widget.translate_coordinates.html)でGTK toplevel座標へ変換する。
そのままcompositorのUVへ変換すると、CSD shadowとxdg geometry originの差だけ入力がずれる可能性がある。
[compositor fixture](../scripts/fixtures/linux-real-app-wayland/src/main.rs)は、
[`latest_snapshot()`](../native/core/src/compositor/snapshot.rs)から `geometry_origin` を読み取り専用snapshotへ追加する。
widget中心からこのoriginを引き、同じsnapshotの `logical_size` で正規化する。

```text
u = (gtk_toplevel_x - geometry_origin.x) / logical_size.width
v = (gtk_toplevel_y - geometry_origin.y) / logical_size.height
```

geometryが欠落する場合や、変換後の点がwindow外になる場合は拒否し、端へclampして入力を続けない。
nativeの [surface hit変換](../native/model/src/input.rs)はgeometryを除いたlayer offsetからsurface local座標へ戻すため、originを一度だけ引く必要がある。
fixtureのRust回帰検査は、実Wayland clientがcommitした非0 geometry `(2,1,28,30)` を使ってこの関係を照合する。

既存の `input.delivered` はkeyboard eventのcountであり、pointer clickでは増加しない。
fixtureはnativeの `pointer_buttons`、`pointer_motions`、`pointer_pressed`、`pointer_window` を別に返す。
GUI oracleは実pointer button配送とreleaseを照合するが、そのcountだけで対象widgetの応答済みとは認定しない。
後述の通常設定の状態変化を必須にする。

## 行番号表示の保持oracle

[schemaの行番号既定値](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/org.xfce.mousepad.gschema.xml#L229-L235)はfalseである。
新規profileでその値を実観測し、設定ファイルに対象keyが先に保存されていないことも確認する。
schemaの既定値だけを新規GUIの実測値として転記しない。

| 段階 | 必要な証拠 |
| --- | --- |
| 新規profile・first run | 起動前の対象key不在。実mainとPreferences、mapped viewとcheckbuttonが既定状態で一致する |
| GUI変更 | 実Wayland pointerでline-number checkbuttonを変更。入力count進行とreleaseに加え、checkbuttonとviewの状態が反転する |
| 保存値照合 | profile lock下のno-follow keyfile読取りで、対象keyがboolとして保存されGUI状態に一致する |
| 通常Close | mainの通常Close、app-bearing server exit0、全子孫・pidfd・pipeの終了を確認する |
| 同profile・restart run | 新しいrun/owner/runtime/Controlで起動。保存key、checkbutton、mapped viewが変更後の値で一致する |
| 別profile・isolated run | 対象keyが先に存在せず、GUI既定状態が保たれる。同一profileの別名や同runの使い回しを拒否する |

GUI、view、保存keyの三つを揃える。keyfileを先に書き換える操作、GSettings API probeの操作、通常Close時のgeometry保存はこのoracleの代わりにしない。
強制Stop/crash時の未flush通常設定を保持できるという保証にも拡張しない。

## 保護設定のGUI oracle

backendの保護対象は3キーだが、Preferencesで確認する対象controlは2個である。
固定版の[UI定義](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/ui/mousepad-prefs-dialog.ui)には
`autosave-timer` のPreferences widgetがない。「3widgetがdisabled」を受入れ条件にしない。

| key | backendの固定値・型 | GUI検査 |
| --- | --- | --- |
| `preferences/window/opening-mode` | string `tab`、writable false | Window pageの実comboがmapped、実効sensitive false、操作試行後も `tab` |
| `preferences/file/session-restore` | string `never`、writable false | File pageの実comboがmapped、実効sensitive false、操作試行後も `never` |
| `preferences/file/autosave-timer` | uint32 `0`、writable false | widget不存在を前提にbackendと実動作を検査する |

[backend検査](../scripts/fixtures/linux-settings-probe.c)の3キーに対する値・型・writable・write/reset・混在transaction拒否は維持する。
GSettingsの通常bindingがwritableをsensitiveへ結び付ける根拠は
[GLib 2.80.5の実装](https://github.com/GNOME/glib/blob/2.80.5/gio/gsettings.c#L2947-L2956)であり、
今回はその結果を実Mousepadの2controlでも観測する。

### 正例と負例を同じ入力経路で組み合わせる

View、Window、Fileの各pageで通常設定のmapped・実効sensitive trueを読み、実Wayland pointerで状態を変更できることを確認する。
保護設定のあるWindow/File pageでも、それぞれの通常設定を正例にする。
pageを切り替えただけ、別windowへ届いた入力、対象の表示位置から外れたclickを負例成立の根拠にしない。

| page | 正例の固定action-name |
| --- | --- |
| View | `app.preferences.view.show-line-numbers` |
| Window | `app.preferences.window.always-show-tabs` |
| File | `app.preferences.file.make-backup` |

Window/Fileの正例はclickによる変化と元の状態への復帰を観測する。Viewの行番号は変更後の状態を保存・再起動し、永続化oracleでも照合する。
Preferencesを開く操作は固定版の[Edit menu末尾](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/resources/gtk/menus.ui#L110-L135)に基づく実key経路を用いる。
NotebookのView/Window/Fileは固定UIのpage 0/2/3として識別し、実tab labelの座標へpointerを送る。label文字列は記録しない。

その上で保護comboの実効sensitive falseを確認し、同じ座標変換・pointer経路で操作を試みる。
操作後に固定値が維持されるだけでは不十分であり、同pageの正例成功、対象のmapped状態、実効sensitivity、入力配送、freshな観測の組合せを要求する。
GTK setterやaction activationで設定値を直接変え、その結果をGUI操作と呼ぶことはしない。

### 通常Openと復元保護へ照合する

GUI検査後の通常Openは、既存の実Ctrl+Oとfile chooser入力を使う。
file chooserが同じmainのtransientであること、Open後もmainが増えないことに加え、
observerで文書notebookのtab数が増えたことを確認する。
mainが一つというtopologyだけで、文書が新tabとして開いたと推定しない。

復元データ保護は [PR1のsession/autosave/ambiguous各case](linux-real-app-a0-restart-20261109.md#独立した終了と再起動のoracle)と照合する。
非空または判別不能の復元候補は `APP_STATE_RECOVERY_REQUIRED` でspawn前に拒否し、bytes不変を要求する。
候補を削除して再起動を通さず、安全なprofileの `restarted` と保護拒否を区別する。

## 所有・期限・公開記録の境界

合成profileとrun資源の分離、別worker/別ownership scope、coordinator中断時の全worker回収はPR1から維持する。
終了要求、receipt open/write/fsync、scan/drainのI/O失敗があっても、回収完了までlive owner・signal guard・profile lockを保持する。
GUI失敗をreasonへ記録する処理が、全回収を迂回してworkerを終了させる構造を追加しない。

125秒以上の先行保持、既存Close/Stop/crash・復元拒否・独立性caseを完了し、先行workerの通常Closeと全回収を確認してから、
target workerのGUI caseへ進む。追加GUI時間を先行runへ累積させない。
各GUI runは通常runの既存予算内で実行する。

| role | Python owner | Rust compositor |
| --- | --- | --- |
| `run` | 110秒 | 120秒 |
| `precursor` | 360秒 | 370秒 |

[共有lifetime.json](../scripts/fixtures/linux-real-app-wayland/lifetime.json)、起動前に固定するPythonの起算点、
ready/最終receiptのrole・期限照合を変更しない。callbackで期限をリセットしない。
CIでは125秒保持後に新しい実GUI応答を要求し、古い応答の再掲を引き続き拒否する。

observerの生の応答やwidget座標はprivate制御にだけ用い、artifactへ保存しない。
公開receiptは固定のbool/enum、digest、run/profile/WindowId、frame、count等の限定した証拠に変換する。
本文、title、tab label、pixels、自由property dump、合成profileの内容をログやartifactへ追加しない。
observerは合成検査専用であり、production telemetryや任意GUI操作APIではない。

## receipt・集約・CI gate

実装入口は [実GUIの操作・保存値・再起動oracle](../scripts/linux_real_app_gui.py)、
[harness](../scripts/linux_real_app_harness.py)、
[GUI relational validator](../scripts/linux_real_app_gui_evidence.py)、
[既存lifecycle validator](../scripts/linux_real_app_evidence.py)、
[集約とA0 validator](../scripts/linux_real_app_check.py)、
[専用CI gate](../scripts/ci/check-linux-real-app-foundation)である。

各runのreceipt単体では設定保持やprofile間分離を証明できないため、per-runのpartialを集約の成功と混同しない。
first/restart/isolatedの同一・相違関係、binary hash、通常Closeと全回収、GUI/保存値/入力の観測をGUI validatorで関連付ける。
既存lifecycleの厳密なcase集合・証拠相関もそのまま必要とする。

すべてのA0 oracleが実測で成立した場合だけ、次の明示的な成功経路を受理する。

```json
{
  "status": "passed",
  "reason": "APP_A0_PASSED",
  "a0_passed": true,
  "available": false,
  "production_acceptance_passed": false,
  "quest_verified": false,
  "pending": ["v3_acceptance", "quest"]
}
```

GUI未実施・失敗・依存不足・未回収がある場合はA0未完了のまま記録し、skipやsummary boolの変更で合格させない。
従来の正確なGUI2項目pendingを持つincomplete receiptと、完全なGUI証拠を持つ成功receiptを区別する。
専用gateはGUIを含むA0成功を必須にし、incompleteを「今回の期待値」として成功させない。
`--stage acceptance`、production available、Quest受入れをA0成功から自動昇格させない。

### 偽陽性として拒否する例

- GUIのboolとpendingだけを変更し、実GUI証拠がない。API probeしかない。
- 初期観測前に行番号keyが保存されている。GUI状態が反転していない。保存値だけが変わった。
- checkbuttonとmapped viewの状態が食い違う。対象widgetの欠落や未知型を既定値として扱う。
- 古いobserver応答、古いframe/入力count、別runの証拠を再利用する。
- Preferencesのparentが別mainまたは不明。Window/Fileの正例がないまま、保護値の不変だけを提出する。
- sensitivityを実観測せず、入力countや「値が変わらなかった」だけを提出する。
- GUI保護対象を3widgetへ水増しする。未知キー、余剰field、不正型を混ぜる。
- restartが別profile、同じrun/owner/runtimeの使い回し、Closeの代わりにStop、いずれかの未回収を含む。
- isolationが同じprofile、または変更値が別profileにも漏れている。
- 通常Openでmainだけを確認し、tab増加を確認しない。
- A0成功に便乗して `available` / `production_acceptance_passed` / `quest_verified` をtrueへ変更する。

## headごとの検証結果

この文書を含むPR本文へ、そのheadの検査数、実測case、run/artifactリンク、skip理由を記録する。
本書は観測・判定の契約を記録するものであり、実装の存在だけを特定headの成功記録としない。
開始時に確認したmainの成功やPR #5の過去headの成功を、今回headの成功に流用しない。

ローカルにはMousepad/Waypipe/GTK開発依存/Rust toolchainがなく、非root実ProcessOwnerの検査にも制約がある。
実行できた拒否・相関テストと、依存により実行できなかった検査を区別する。
専用workflowで固定Mousepad 0.6.3/GTK3と実Waypipe、実process異常経路、125秒保持、GUI oracleを実行し、既存host/Android CIも確認する。
そのheadで未実施のKWin・物理入力・Quest等を他の成功から補完しない。

## 次工程

A0の全条件が今回headで成立した場合、[L7a設計](linux-real-app-l7a-design-20261109.md#実装単位と完了条件)のB/Cへ進める判断材料になる。
v3 manifest/bundle/pinsと通常helper、native admission/SDK表示watchdog、Android通常UI、更新Recover・Quest実機受入れは後続B〜Fで個別に検査する。
今回のA0合格だけで、Questの通常launcherから実用Mousepadを利用可能とするものではない。
