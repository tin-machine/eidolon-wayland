# L7a A0 PR1: 同じ合成profileでの再起動と先行instanceからの独立

2026-11-09。状態: **PR1の実装。A0全体・v3通常起動・Quest受入れは未完了**。
実測の成功判定は、この変更のheadに対する専用foundation CIと既存host/Android CIの結果で行う。
過去のPR #4の成功を今回の成功へ読み替えない。headごとの結果とrunリンクはPR本文へ記録する。

## 基準と今回の範囲

開始時に最新main [`47e015805862cd73d75e36e648df760c44573955`](https://github.com/tin-machine/eidolon-wayland/commit/47e015805862cd73d75e36e648df760c44573955)
と関連PR #1〜#4を確認した。PR #4はmerge `6fb0048`、検証済みhead `35c1b8c` でマージ済み。
merge以降は引き継ぎ計画等4文書の変更だけで、PR1の実装や重複するopen PRはなかった。
mainのtree `b12f251148d6213f7ff2e9eb57acb52dfe2dea6a` を確認して新しいbranchで作業した。
このrevisionに追跡されたAGENTS.md等の追加作業指示はない。

[引き継ぎ計画のPR1](linux-real-app-a0-next-steps-20261109.md#2-次のpr1-同じ合成profileでの再起動と先行instanceとの独立)
に対応する。通常Close、明示Stop、実Mousepadだけの異常終了を別々に実行し、各caseで終了前後と再起動前後を照合する。
先行Mousepadは検査自身が作る別の合成HOME・専用bus・displayに置く。
利用者の実desktopや既存profileを借りず、既存profileを指定するCLIや自由argvの実行口を追加しない。

GUIからの設定変更・GUI上の保護設定操作不可の2項目は残す。
production storage ID、v3 bundle/pins/helper、Android通常UI、native origin/表示watchdog、Quest受入れは後続工程である。
通常のlauncherから実用Mousepadを使えるようにしたPRではない。

## profileを保持し、runを作り直す

[`SyntheticProfile`](../scripts/linux_real_app_wayland.py) が検査内で新規作成したprofileだけを再利用する。
通常Close/Stop/異常終了の各caseは**互いに別の合成profile**を使い、そのcase内の初回と再起動が同一profileを共有する。
Stop後に復元候補が実際に残った場合でも、それを削除して次の異常終了caseへ進めないためである。

| 保持するもの | 各runで新規作成するもの |
| --- | --- |
| HOME/config/data/state、HOME内Documents、設定・保存済み文書 | runtime、cache/tmp、Waypipe socket、専用D-Bus |
| 合成profile identity、同じinodeのprofile.lock | compositor、Control、live ProcessOwner、artifact/receipt |

profileのrootと固定directory、lockのowner/type/mode/link/identity、およびprocess-localな作成元を再検査する。
既存directoryを合成profileとして採用しない。lockをunlinkして別inodeへ作り直さず、同じlockを再取得する。
flock取得から起動前の復元データguard、全子孫・pidfd・pipe終了まで、同じlive runが所有する。
回収済みownerの再利用やControlのmain/observed_mapped履歴の初期化は行わない。

[`driver.observe_ready()`](../scripts/fixtures/linux-real-app-wayland/driver.py) は、起動前のclient0、
新runのclient1、最初のparentless main、厳密なapp_id `mousepad`、SHM frameを照合する。
receiptのrun/owner/runtime identityは新規、profile identityはcase内で同一である。
別compositorでWindowIdが同じ `1` になっても正常で、数値の相違を新instanceの証明にしない。

profile snapshotはlock下でno-follow・件数/深さ/bytes上限付きで読み、文書・設定・treeのdigestのみを返す。
本文、ファイル名、title、pixelsを公開receiptに含めない。snapshotの失敗も成功や空profileへ読み替えない。

## 独立した終了と再起動のoracle

[`_pair()` と `_recovery_case()`](../scripts/linux_real_app_harness.py) が各caseを実行する。
各初回runで、実Waylandのキー入力から未改変MousepadのOpen/編集/Save/Save Asを行い、保存bytesを独立照合する。
再起動runではファイルを作り直さず、同じprofileの保存bytesと起動を確認する。

| case | 終了の観測と必要な証拠 |
| --- | --- |
| `close_restart` | 実mainのClose、app-bearing serverのexit0、全回収。同profileから新しいmain/SHM frameが来て、保存文書と通常Closeでflushされた通常設定が保持される |
| `stop_restart` | 対象workerのowner scopeを強制回収。子孫・pidfd・pipe終了後に同profileをguardし、安全なら新runが起動する |
| `crash_restart` | 現在の自分の子孫から実Mousepad executableのinodeを照合し、取得・再検査したlive pidfdへSIGKILL。app pidfdの終了とserver非0終了、残りの回収、同profile再検査を独立実行する |
| seeded `session` | 非空sessionをapp spawn前に `APP_STATE_RECOVERY_REQUIRED` で拒否。文書・設定・候補bytesを保持する |
| seeded `autosave` | autosave候補を同じ固定reasonでspawn前拒否。候補・文書・設定のtree digestが不変 |
| seeded `ambiguous` | 判別不能なsession表記もclearとして扱わず、spawn前拒否して保持する |

Stop/異常終了後の実profileに復元状態があれば、`outcome=recovery_required` を記録する。
これは再起動成功ではなく、起動前の保護拒否である。`restarted` と区別し、欠落caseや別エラーを同じ結果にしない。
`close_restart` は実際の安全な再起動成功が必須で、常に拒否するだけの実装はgateを通らない。
seeded caseも実run入口を通すが、合成復元データをMousepadへ渡して起動してはならない。

通常Close後の設定oracleは、preferences群に加え、Mousepad自身が保存する通常のwindow geometryを含む。
上流 [`mousepad_window_save_geometry()`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c#L1356-L1408)
がwidth/height等を保存し、その定義は [`state/window` schema](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/org.xfce.mousepad.gschema.xml#L610) にある。
空の設定digestの一致を成功とせず、実際に保存された通常設定が1項目以上あることを要求する。
session/historyのように通常動作で変わる状態と区別して照合する。
GUIで行番号等を変更して再表示を確かめるPR2のoracleを代替せず、Stop/異常終了で未flushの設定保持も保証しない。

復元候補を先に拒否する根拠は、上流の
[`autosave_disable` と削除経路](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-history.c#L827-L941)
である。helper自身がファイルを消さないだけでは保護成立としない。

## 対象Stopとcoordinator中断の回収範囲

[`_Coordinator` / `_worker`](../scripts/linux_real_app_harness.py) は、先行側と対象側を別OS workerで管理する。
各workerの `ProcessOwner` は自分の全子孫を対象とし、対象Stopが先行側を含むownerへ届かない構造にする。
先行側は対象の通常Close/Stop後にも同じmain、window構成、設定digestを保持する。
単なる生存PIDやcompositor入力counterでは応答済みとせず、実Ctrl+Oで新しいtransient dialog/SHM frameを観測し、
Escapeで閉じて同じmainへ戻るround tripを各回で行う。

初回head `3c5bb97` の [実Mousepad CI](https://github.com/tin-machine/eidolon-wayland/actions/runs/42600564211)
では、Close後の同profile再起動・文書/設定保持は成功した一方、先行側の通常設定が初期0件→後続4件となり、
`APP_PRECURSOR_SETTINGS_CHANGED` で停止した。この失敗を独立性の成功へ読み替えない。
上流の [`configure_event()`](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c#L1416-L1458)
は最後のconfigureから1秒のtimerでgeometryを保存する。
[Close経路](https://github.com/xfce-mirror/mousepad/blob/65fbfe0459259f820fcc8b9c4dd454228304842a/mousepad/mousepad-window.c#L2935-L2939)
は即時保存するため、通常Closeの検査だけでは起動中のこの時間差を検出できなかった。
[GLibのtimer仕様](https://docs.gtk.org/glib/func.timeout_add_seconds.html)も初回の厳密な1秒実行を保証しない。
このため、先行側の実dialog応答後に4項目以上の通常設定が実在し、profile digestが1.1秒以上安定したことを
8秒の期限内で観測してからbaselineを確定し、その後に対象workerを起動する。
後続の観測でも同じ安定条件を使う。時間経過だけで成功にせず、raw設定・通常設定・文書の不変比較を維持する。

coordinatorは最初のworker spawn前からtermination guardとlive ownerを持つ。
全体中断では全workerに終了を要求し、workerが自分の回収を完了するまで待つ。
生存workerが `CleanupPending` 中に、coordinatorの子孫全体のfinishでworkerだけをSIGKILLする経路を作らない。
全worker終了後にcoordinator自身のownerで、予期せず親を失った子孫も含めた回収を完了する。
成功判定では全workerの実exit0、全pidfd終了、全出力pipe EOFまで必要である。

SIGINT/SIGTERM/SIGHUPはラッチだけを行い、Popen→子登録→pidfd/pipe登録をhandlerからの例外で中断しない。
これは [Python公式のsignal handler例外の注意](https://docs.python.org/3/library/signal.html#note-on-signal-handlers-and-exceptions)
に従う。終了要求の再送、receiptのopen/write/fsync失敗、回収scan/drainのI/O失敗でも、同じlive handleを保持して再試行する。
workerのpending JSONは状態通知であり、親への所有移管でも、workerが終了してよい許可でもない。
全子孫が消えていてもpipe EOF未確認ならpendingを維持し、profile lockとguardを解放しない。
後で回収できても、最初の検証失敗や記録失敗を成功へ変更しない。

## receiptとCI gate

[`linux_real_app_evidence.py`](../scripts/linux_real_app_evidence.py) は、summary boolだけでなく、
caseの正確な集合、終了方法、初回と再起動のprofile一致、run/owner/runtimeの相違、実行binary hash、
文書・通常設定保持、復元拒否のspawn前性、各runとcoordinatorの回収を照合する。
先行側の同一identity・設定・window構成と、各回の新しいdialog応答/入力進行も必要である。
欠落case、Closeの証拠をStop/crashへ流用、古いsnapshotの再掲、別profile、未回収や偽のfull-A0は拒否する。

[`validate_application_foundation()`](../scripts/linux_real_app_check.py) と
[`check-linux-real-app-foundation`](../scripts/ci/check-linux-real-app-foundation) は同じPRで更新した。
実装済みoracleがすべて通っても、feasibilityは次の状態を維持する。

```json
{
  "status": "incomplete",
  "reason": "APP_A0_INCOMPLETE",
  "a0_passed": false,
  "available": false,
  "production_acceptance_passed": false,
  "quest_verified": false,
  "pending": ["gui_setting_persistence", "protected_settings_gui_readonly", "v3_acceptance", "quest"]
}
```

`--stage feasibility` のexitは2。専用CIは実装済み範囲に対してだけ `foundation_verified=true` を出す。
missing dependency、runtime失敗、未回収、検査未実行を「期待したincomplete」と同一視しない。
実プロセス検査は `EIDOLON_REQUIRE_LIFECYCLE_TESTS=1` の非root jobで必須にする。
既存host/Android workflowも継続する。

## 検証範囲と残作業

新しい拒否/回帰検査は次の入口にある。

- [profile・lock・snapshot・guard-before-spawn・pipe EOF](../scripts/tests/test_linux_real_app_profile.py)
- [固定worker protocolと回収所有・記録失敗](../scripts/tests/test_linux_real_app_harness.py)
- [実worker/coordinator・double-fork/setsid・SIGTERM・lock保持](../scripts/tests/test_linux_real_app_harness_lifecycle.py)
- [実runの既存異常経路](../scripts/tests/test_linux_real_app_lifecycle.py)
- [receiptの偽陽性・証拠相関・v2固定hash](../scripts/tests/test_linux_real_app_check.py)

ローカル作業環境はroot実行で、非rootへの切替はrunuser/setprivとも権限制限により不可。
Mousepad/Waypipe/Rust toolchainもないため、実プロセスowner・実アプリ・Rust/Androidはローカルで未実施とし、
専用CI/既存CIの**今回のhead**に対する実行で確認する。ローカルのskipは成功数へ数えない。
専用workflowは同じpinned Mousepad 0.6.3/GTK3・実Waypipeを使い、settings module、異常経路、今回の全caseを必須実行する。
集約receiptに加え、各runの本文を含まない `wayland.json` をartifactへ保存し、失敗caseも追跡できるようにする。
合成profileやDocumentsの本文、アプリ出力はuploadしない。
対象利用者Linux一般やQuestでの成功へ拡張しない。

固定v2の5 sourceと [e401918のgolden hash](../scripts/tests/fixtures/linux-desktop-v2-e401918-sources.json) は変更しない。
次は引き継ぎ計画のPR2（GUI通常設定保持・保護設定操作不可）。A0全項目成立後にB〜Fへ進む。
