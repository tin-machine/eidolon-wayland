# Android IME比較probeの操作

2026-11-07。対象は[日本語入力計画](android-ime-wayland-plan-20261107.md)のIME-A1/A2。
通常のSpatial SDK session内で標準`EditText` A/Bと、端末相当の独自editor Cを比較するdebug専用fixtureである。
Linuxへの文字配送は接続しない。通常runtimeや端末sessionを停止せず、probe中の物理キーはLinuxへのraw配送から排他する。

## 開始・状態取得・終了

対象Questに今回のdebug APKが導入済みであることを確認する。CLIはAPKの導入、IME選択、
物理キーボード設定の変更を行わない。`--serial`は承認済みの対象ADB serialを明示し、
`QuestDevice.validate()`でQuest 3/API 34以上を照合する。

```bash
# 承認済みの対象を指定する。USB serialまたは照合済みWi-Fi ADB endpoint。
QUEST_ADB_SERIAL='対象のADB serial'
IME_PROBE_NONCE=$(python3 -c 'import secrets; print(secrets.token_hex(16))')

scripts/quest-ime-probe start --serial "$QUEST_ADB_SERIAL" --nonce "$IME_PROBE_NONCE"
scripts/quest-ime-probe status --serial "$QUEST_ADB_SERIAL" --nonce "$IME_PROBE_NONCE"
scripts/quest-ime-probe exercise --serial "$QUEST_ADB_SERIAL" --nonce "$IME_PROBE_NONCE"
scripts/quest-ime-probe stop --serial "$QUEST_ADB_SERIAL" --nonce "$IME_PROBE_NONCE"
```

`start`のnonceは省略すると生成され、標準出力の`nonce`へ記録される。
`status`・`exercise`・`stop`では必ず開始時の32桁小文字hexを指定する。
既に観測したnonceでの再開始は拒否する。新しい比較には新しいnonceを使う。
`--timeout`はmetadata待機時間で、既定25秒、指定範囲1～60秒。

`stop`は同じnonceの`ime-editor-probe-stop`を使う。
SDKのpointer配送が止まりパネルの終了ボタンを押せない場合にも利用できる。
アプリ側は10分のtimeoutとscene終了でもeditor接続・パネルの回収を要求する。
別nonceの停止要求は対象probeを終了しない。

## 固定操作の自動観測

`exercise`はattach済みprobeへ同nonceの`ime-editor-probe-exercise`を送り、1回だけ実行する。
A→B→Cの順に、各欄へ`focus`→`show`→`hide`→`restart`→`clear_focus`を要求し、
各操作の750ms後に全editor・panel・Activityのfocus/display/IMM状態を採取する。
本文は注入せず、非attachのsynthetic fixtureで使う`InputConnection`直接呼出しも行わない。
window flagsやIME選択・設定は変更しない。通常操作ボタンは実行中に割り込ませず、終了ボタンは利用できる。

最終段では全A/B/Cにhide・clearFocusとnative resetを要求し、750ms後の状態を
`exercise.cleanup_observation`へ保存する。25秒のtimeout、probe停止、scene終了でも解除を要求する。
この操作が終了しても比較パネルは残るため、パネルを回収するには続けて`stop`を使う。
同じnonceでの再実行は拒否し、再試行には新しいprobeを開始する。

`exercise.steps`は固定15段のmetadataで、`framework_connection_callbacks`は
attach済みA/B/Cの`onCreateInputConnection`相当callback件数をexercise開始時から数えたもの。
これはframeworkが接続を作成した証拠であり、実IMEがかな漢字変換できた証拠ではない。
`showSoftInput`の戻り値、Viewのfocus、IMMの`isActive(view)`/`isAcceptingText`、
IME insetsの可視性も個別に読む。どれか一つから候補表示やpointer復帰を推定しない。
全callback件数が0でも固定操作の完了は記録できる。その場合は接続を観測できていない。

## SDK通常配送経路への固定tap

```bash
scripts/quest-ime-probe sdk-tap --serial "$QUEST_SERIAL" --nonce "$PROBE_NONCE"
scripts/quest-ime-probe status --serial "$QUEST_SERIAL" --nonce "$PROBE_NONCE"
scripts/quest-ime-probe stop --serial "$QUEST_SERIAL" --nonce "$PROBE_NONCE"
```

`sdk-tap`は開始済みprobeと同nonceで1回だけ実行する。`exercise`との同時実行は拒否する。
固定caseは`ime-editor-probe-sdk-tap`で、受け取るのはcaseとnonceだけ。
`exercise`のView focus要求とは別に、A→B→Cそれぞれで次の3操作を行う。

1. 全入力欄へhide/clearFocusを要求し、750ms後にfocus/IMM状態を観測する。
2. editor boundsから求めた中心が可視範囲にあり、PanelDisplayのdisplay/tokenと一致することを確認して、
   公開`PanelDisplay.dispatchEvent`へDOWN/UPを提出する。750ms後にtouch callback、Window focus、IC件数を観測する。
3. 対象editorのView/Window focusを両方実測できた場合だけ、後続UI turnで`showSoftInput`を要求する。
   focusを得られなければ`show_skipped_no_focus`と記録する。750ms後に再観測する。

固定9段の前後metadataを`sdk_tap.steps`へ保存する。文字注入、IME選択、window flags変更は行わない。
最終段、25秒のtimeout、同nonceの停止、scene終了では全入力欄へhide/clearFocusを要求する。
パネル自体は`stop`まで残る。SDK例外や無効boundsは操作列を失敗で終え、cleanupへ進む。

SDK 0.15の通常pointer配送は`PanelInputListener`からこの公開APIを通る。
固定AARの実装はVirtualDisplayへdisplay IDを付けた入力注入を試し、利用できない場合はView配送へ戻る。
内部の注入成否を呼出元へ返さないため、`down_submitted`/`up_submitted`は公開API呼出の完了だけを示す。
`panel_touch_callbacks`はViewへ実際に届いたイベント数であり、物理コントローラの入力証拠ではない。
IC増分、Window focus、IME表示、候補操作、HMD視認をそれぞれ別に読む。
[`PanelDisplayBase` API](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_runtime_paneldisplaybase/)
と[AndroidのIME接続条件](https://developer.android.com/develop/ui/views/touch-and-input/keyboard-input/visibility)を参照。

SDK内部ではconstructor contextのIMMが`isAcceptingText=true`の間、MotionEvent配送を止める。
現在のmetadataにあるActivity IMMとeditor contextのIMMは別々に観測するもので、
SDK内部IMMとの同一性や、hideだけでのpointer復帰を推定しない。

## パネルの比較操作

1. Aで固定語`nihongo`を入力し、Spaceによる候補選択と「日本語」の確定を試す。
2. Bへ移動し、さらに別パネルのCでも同じ操作を行う。
3. 変換中・確定後・鍵盤非表示後それぞれで、無害ボタン、別入力欄、別パネル、hover/scrollを操作する。
4. `非表示`のみ、`focus解除`、`reset`、`restart`を区別して試し、pointer操作が復帰する条件を記録する。
5. 終了後に通常UIへの復帰を確認する。必要なら同nonceのCLI停止を使う。

操作ボタンは最後にfocusした入力欄を対象にする。
`reset`は局所編集を取り消し、`restart`はAndroid入力接続の再開始を要求する。
鍵盤の非表示やnative入力resetだけで、Androidの入力接続が終了したとは判断しない。
Cの直前の確定文字はパネル内だけに表示し、診断へ保存しない。

## 結果の読み方

| 証拠 | 意味 |
| --- | --- |
| CLI `status=observed`、`metadata_received=true` | 一致するnonceの検査済みmetadataを取得した |
| `action_status=panel_fixture_ready` | 2枚のパネルとA/B/Cのattach、固定synthetic fixtureの成功を確認した |
| `action_status=metadata_observed` | 状態取得だけを行った。probe自体が`fail`でも取得は成功し得る |
| `action_status=exercise_sequence_observed` | 固定15段と終了時の解除要求・観測を完了した。実IME接続や日本語変換の成功は含まない |
| `action_status=sdk_tap_sequence_observed` | SDK配送APIを使う固定9段と終了時の解除要求・観測を完了した。touch到達・IME接続が0でも操作観測は完了し得る |
| `action_status=stop_acknowledged` | 同nonceの終了とeditor解除・パネル破棄要求を確認した |
| `action_status=already_stopped` | 既に同nonceの終了記録があり、新たな停止操作は送っていない |
| `synthetic.result=pass` | 非attachの別Viewに固定文字を直接`InputConnection`で渡し、確定一回・preedit終了・古い接続の拒否を検査した |
| `acceptance.*=not_verified` | 実IME、日本語候補、物理USキーボード、HMD視認、pointer復帰の受入れは未判定 |

CLIの終了コード0を日本語入力の合格に置き換えない。
開始時のsynthetic失敗、停止時も`running`のまま、停止証拠の欠落、cleanup失敗、
誤nonceのみの応答、過大・不正metadataは成功にしない。
`panels_destroy_requested`と`views_detached_observed`は別の証拠であり、
破棄要求だけでSDK資源の回収完了やHMD内の消失を断定しない。

## 保存する情報

保存先の既定は、実行時に読み直す`~/.config/homecluster-agents/codex-work-root`の下の
`eidolon-ime-probe/<日時>-<操作>/`。`--artifact-root /absolute/path`で変更できる。
directoryは0700、fileは0600で作成する。

- `probe.json`: callback種類・件数・長さ、View/Window/display、IMM状態、独立synthetic結果などの許可したmetadata。
- `result.json`: CLIの操作結果、nonce、失敗分類、実入力受入れが未判定であること。
- `commands.log`: 対象表示を置換した固定ADBコマンド列。コマンド出力は含めない。

通常診断の`latest.json`は上限付きでメモリ内へ読み、許可した`check`だけを保存する。
通常診断全体、IME service dump、logcat、入力本文、候補一覧、ADB起動出力はartifactへ保存しない。
未知のprobe field、未知のcallback名、重複JSON key、偽のPASSや受入れ昇格は保存前に拒否する。
probe metadataは128KiB、元snapshot読出しは1MiBを上限にする。操作列は15段と9段で上限を固定する。

## PC上の検査と実機結果

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  scripts/tests/test_ime_probe_result.py scripts/tests/test_quest_device.py
```

2026-11-07のSDK tap追加後、probeのPC fixtureは28件成功。
固定tapの実機観測は未実施であり、PC上の結果を実IME・物理入力・HMD受入れへ昇格しない。
実機結果ではAPK/source、OS/IME/subtype、実USキーボード、各操作、候補視認、
pointer復帰、終了後の状態を、自動metadataと分けて追記する。
