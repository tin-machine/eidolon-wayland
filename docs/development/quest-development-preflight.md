# Quest 3 開発開始前チェック — スリープ抑止と Wi-Fi ADB

**用途:** Codexが実機開発を始める前に、対象・現在の設定・所有者を確認し、不足する開発用設定を適用する運用runbook。

**資料確認日:** 2026-11-21。参照したmainは`64374c65f21dd24a5de3d06af4365d9a5abbf063`。

**証拠の範囲:** 公式資料と過去の実機記録に加え、4.5に今回の短時間実測を記載した。本手順全体の現行Quest実行成功やHMD受入を意味しない。managed feature/resolverには未登録。

入口は[AGENTS.md](../../AGENTS.md)。接続・forward・観測の詳細は[開発自動化・デバッグ](development-automation-and-debugging.md)、製品のsleep/復帰検証は[Phase 6](../features/initial-phases/phase-6-rust-lifecycle-testing.md)を参照する。

## 1. 各タスクの開始時に、実機を使うか判断する

1. **文書・hostテスト・実機なしCIだけ:** `not_applicable`とし、端末設定は変更しない。
2. **Questで導入・起動・観測・実機テストを行う開発:** 最初の実機開発ループに入る前に本手順を実行する。長いbuild後にも、最初の端末操作の直前に再確認する。
3. **Questを使う予定だが、ADB・接続経路・承認・対象が不足:** 実機preflightは`blocked_device`、実機検証は`not_run`と記録する。独立した編集・build・hostテストは継続できる。

すでに依頼・承認されている開発用Questが一意に分かり、排他を確保できれば、以下の不足設定はCodexが適用して開発を進める。各設定のたびに確認を取り直す必要はない。対象不明や他の利用者との競合を、設定変更で解決しない。

実機を使う場合は、最初に試験profileを選ぶ。

| Profile | 目的 | スリープ関連の扱い |
| --- | --- | --- |
| `development` | build/install/観測を反復し、未装着中もADBを維持 | 有効な既存設定を再利用し、不足時だけ近接センサーoverrideを設定 |
| `lifecycle` | 実際の脱着・sleep・復帰・省電力挙動を検証 | 開発用overrideの影響を除き、試験で要求する通常設定を確認してから開始 |

本書はCodexが実行する手順であり、自動起動するdaemonや新しいCLIの実装ではない。既存の[`QuestDevice.validate()`](../../scripts/quest_device.py)も接続・機種・APIの確認であり、スリープ設定は変更しない。

## 2. 対象と開発セッションの所有者を固定する

- Wi-Fi ADBを確立したホストで調べる。Ubuntu Desktop経由なら、まず既存のSSH経路でそのホストへ入り、そこで承認済みのQuestを選ぶ。`adb devices -l`の最初の行を無条件に選ばず、以降は必ず`adb -s`または`metavr -d`で対象を指定する。[ADB][adb]
- **同じ物理QuestのUSB serialとWi-Fiの`IP:port`は、別の端末として扱わない。** 固定の管理IDと現在のtransportを対応付け、既存の端末管理・排他を引き継ぐ。
- 同じQuestを他のCodex/job/Developer Hubが操作していないか、共有hostの所有記録を確認する。並行利用なら端末操作を直列化し、一つの開発セッションが設定を管理する。所有者が不明な設定を解除しない。
- 現在の依頼・継続開発の引継ぎ・過去の設定記録を確認する。短いテストの終了と、端末を使う開発セッション全体の終了を区別する。

既存の[2026-11-07の記録](development-automation-and-debugging.md#無人運用とライフサイクル試験)には、`prox_close`を継続し、検証ごとの自動`automation_disable`を行わない運用指示がある。この継続運用を引き継ぐ場合は維持する。新しい時限設定で既存の維持期間を短縮したり、テスト終了のたびに解除したりしない。

その記録は当時の実測で、現在の状態確認の代用にはならない。[2026-11-04のpreflight記録](build/native-preflight-20261104.md#スリープと給電中の起動維持)にある`screen_off_timeout=86400000`、`sleep_timeout=-1`、`stayon`の`0→15`も、今回の現在値・復元値として流用しない。

## 3. 変更前に読取専用で確認・保存する

記録はrepository外の非公開作業rootへ置く。既存規約に従い、`~/.config/homecluster-agents/codex-work-root`を毎回読み、未設定なら`~/.local/state/homecluster-agents/codex-work`を使う。セッションごとのdirectoryは0700、ファイルは0600にする。実serial/IP、端末ログ、PINや認証情報をGit/PRへ載せない。

以下は**Bashの手順例**。`quest_serial`と`quest_run_dir`は確認済みの対象と今回の非公開directoryに置き換える。Linux例の`timeout`がない環境では、呼び出し側で同等の期限を設ける。無期限の再接続待ちを作らない。

**変更前baselineは初回変更前に一度だけ採取し、上書きしない。** 以下のsubshellは読取失敗や既存ファイルへの上書きをエラーにする。失敗した取得を元値として使わず、その値を必要とする設定変更は保留する。近接状態が取得不能な場合のADB経路は手順4.3に従う。再接続・長いbuild後の観測は手順5の別directoryへ保存し、`*-before.txt`は維持する。

```bash
quest_serial='REPLACE_WITH_CONFIRMED_ADB_TARGET'
quest_run_dir='/ABSOLUTE/PRIVATE/WORK_ROOT/quest-preflight/SESSION_ID'
(
set -eC
umask 077
mkdir -p "$quest_run_dir"

adb version
timeout 15s adb devices -l
test "$(timeout 15s adb -s "$quest_serial" get-state)" = device
timeout 15s adb -s "$quest_serial" shell getprop ro.product.model
timeout 15s adb -s "$quest_serial" shell getprop ro.build.version.sdk
timeout 15s adb -s "$quest_serial" shell getprop ro.build.fingerprint
timeout 15s adb -s "$quest_serial" shell dumpsys power > "$quest_run_dir/power-before.txt"
timeout 15s adb -s "$quest_serial" shell dumpsys battery > "$quest_run_dir/battery-before.txt"
timeout 15s adb -s "$quest_serial" shell settings get global stay_on_while_plugged_in \
  > "$quest_run_dir/stayon-before.txt"
)
```

`get-state=device`と対象機種を確認する。既存の実機checkerはQuest 3/API 34以上を要求している。`unauthorized`、`offline`、対象の取り違え、読取失敗は正常値として処理しない。

`metavr`が導入済みなら、その版・対応オプション・近接センサー状態も確認する。

```bash
metavr --version
metavr device proximity --help
(
set -eC
umask 077
timeout 15s metavr -d "$quest_serial" device proximity --status \
  > "$quest_run_dir/proximity-before.txt"
)
```

`--status`の出力形式・JSONキー・期限の表示を推測しない。採用版のhelpと実出力を読み、期限や所有者が出力されなければ開発セッション記録で補う。取得不能は`unknown`であり「センサー有効」「overrideなし」とは判定しない。[CLI reference][cli]

確認の境界:

- `Awake`は現在起きている証拠で、近接センサーoverrideや長時間の接続維持の証拠ではない。
- `mStayOn=true`はAndroid側の給電中設定の観測で、Questの装着検知無効化を意味しない。
- `Broadcast completed: result=0`だけを機能の有効化成功としない。後述の状態・接続再確認を行う。
- `dumpsys power`の項目はOS差がある。見つからない項目をfalseへ変換せず、生出力と未確認箇所を残す。

## 4. 通常開発では、不足する設定だけ適用する

### 4.1 まず既存の設定を再利用する

現在のセンサー状態、セッション記録、残り時間を照合する。今回の作業をカバーするoverrideが既に有効なら変更しない。既存の給電設定も要件を満たすなら維持する。

接続があるだけでは不足設定があるとは限らない。一方、既存の期限が切れた・rebootした・通常の装着検知へ戻った場合は再設定が必要になる。古い記録だけを根拠に再利用しない。

### 4.2 新しいセッションは、対応版CLIの時限設定を優先する

通常状態から新たにoverrideを入れるときは、導入済みのMeta VR CLIが対応していれば時限指定を使う。公式CLIはLinuxにも対応するが、追加導入を全開発の前提にはしない。[導入手順][cli-install]

```bash
# 2時間。7200000ミリ秒 = 2時間
timeout 15s metavr -d "$quest_serial" device proximity \
  --disable --duration-ms 7200000
timeout 15s metavr -d "$quest_serial" device proximity --status \
  > "$quest_run_dir/proximity-after.txt"
```

実行開始時刻・期限・所有者・使用した版とコマンドを記録する。公式の`--duration-ms`は期限後の再有効化を指定する。期限を省略した場合の持続時間は仮定しない。既存の長いoverrideや継続指示を、この2時間設定で上書きしない。[CLI reference][cli]

2時間は例であり、次に確認できる時刻までのbuild・観測・復旧余裕を含めて期間を選ぶ。長い処理の途中で期限を迎える見込みなら、処理開始前に自分の設定を延長するか、期限前に再確認できる作業単位へ分ける。期限切れでADBが到達不能になってからの更新には依存しない。

CLIが未導入なら次のADB経路へ進める。`npx metavr@latest ...`やCLIの導入・更新も公式の選択肢だが、作業中に無断でglobal toolchainやAI設定を変更する必須手順にはしない。[導入手順][cli-install]

### 4.3 ADBだけの場合は、装着中の状態を模擬する

```bash
timeout 15s adb -s "$quest_serial" shell am broadcast \
  -a com.oculus.vrpowermanager.prox_close
```

これはMeta公式の未装着時の開発手順。通常センサーへ戻す操作は`automation_disable`で、`prox_far`は非装着の模擬にとどまる。名前から意味を逆に解釈しない。公式ページではセットアップ設定はreboot後に保持されないと説明するが、このADB操作にCLI同等の時間制限があるとは扱わない。[Meta XR Operator][operator]

センサー状態を取得できなくても、承認済みの対象を排他的に所有し、前の所有者や継続方針との衝突がない場合は、この公式操作で開発状態を確保できる。ただし変更前状態は`unknown`のまま記録し、終了時に「自分が通常状態から変更した」と推測して解除しない。継続・復元の扱いを引継ぎ記録に残す。

権限不足・端末不一致・所有競合を別コマンドで迂回しない。CLIの未導入/非対応と、端末側の権限エラーを区別する。

変更コマンドがtimeoutした場合も、端末側で変更済みの可能性がある。結果を`unknown`として再読取し、失敗表示だけを理由に別経路で上書きしたり、変更なしと報告したりしない。

### 4.4 給電中の`stayon`は補助として必要時だけ

外部給電中で、Android側の起動維持が不足している場合に使う。`dumpsys battery`と`dumpsys power`で給電を確認し、変更前値を保存してから実行する。バッテリー駆動中の必須設定にはしない。

```bash
timeout 15s adb -s "$quest_serial" shell svc power stayon true
timeout 15s adb -s "$quest_serial" shell settings get global stay_on_while_plugged_in \
  > "$quest_run_dir/stayon-after.txt"
```

`stayon true`は**対応する外部電源につながっている間**の設定。`usb`だけでは、OSがAC等として認識する充電器を含められない場合がある。`true`が書くbitmaskはOS実装によるため、`7`や`15`を固定で期待しない。これは設定値を保存する操作で、ADB切断やrebootによる自動復元を期待しない。[AOSP power command][power-command] / [Settings.Global][settings-global]

Quest固有の装着検知とは別である。既存記録にも、給電中・`mStayOn=true`でAsleepになり、`prox_close`後にAwakeとなった短時間観測がある。過去の観測は現在の端末/OSでの成功保証ではない。

### 4.5 適用結果を確認してから開発へ進む

```bash
timeout 15s adb -s "$quest_serial" get-state
timeout 15s adb -s "$quest_serial" shell dumpsys power > "$quest_run_dir/power-after.txt"
timeout 15s adb -s "$quest_serial" shell dumpsys battery > "$quest_run_dir/battery-after.txt"
```

短い観測区間（例: 30〜60秒、1回の待機は60秒以内）で接続と起動状態を再確認し、CLI経路ならセンサー状態も照合する。観測時間と未確認の長時間維持を分けて記録する。未装着を確認できなければ、未装着での維持を検証済みとはしない。

対象・所有者・適用/再利用した設定が整合し、必要なADB接続と起動状態が確認できたら`ready`として実機開発を始める。未確認や失敗を消して成功扱いにせず、実機操作の阻害理由を`blocked_device`へ残す。

### Wi-Fi ADB は応答するが画面が消えているとき

`dumpsys power` の `mWakefulness`、`mLastSleepReason`、`mIsPowered`、`mStayOn` と、`dumpsys battery` の AC/USB/Wireless 給電を照合する。必要に応じて `settings get system screen_off_timeout`、`settings get secure sleep_timeout` を読み取る。タイムアウト延長や省電力設定の一括変更は不要。直前の sleep 理由だけで原因を特定しない。ADB 接続を維持したまま `Asleep` であれば、4.1〜4.3 の対象・所有条件を確かめて装着状態を再設定し、4.5 の間隔で電源と接続を再読取する。

2026-11-21 の Quest 3（API 34）では、Wi-Fi ADB が応答し、AC 給電、`stay_on_while_plugged_in=15`、`mStayOn=true`、画面タイムアウト 86400000 ms、sleep timeout −1 で `Asleep` だった。Meta VR CLI がない Ubuntu Desktop から既存の開発用 `prox_close` を送り、約1秒後と45秒後に Wi-Fi ADB と `Awake` を確認した。給電設定の変更はなかった。装着状態と長時間維持は未確認。

インストールには ADB 到達性、UI 操作・スクリーンショットにはさらに Eidolon の前景復帰と現在の XR focus を確認する。[Activity と XR の観測手順](development-automation-and-debugging.md)を使う。画面が点灯しても XR focus が戻らない場合は OS ダイアログ、ロック、装着・追跡状態を個別に確認する。

## 5. 作業中の期限・再接続・通常ライフサイクル試験

**長いbuildの終了後、CLIの期限切れ前後、reboot後、再接続後、給電変更後、担当者変更後**は、次の実機操作の前に手順3〜4の関連項目を再確認する。初回のbaseline取得例は再実行せず、現在の観測は次のように別directoryへ保存する。物理端末が同じでも、reconnectを理由に復元元を更新しない。

```bash
(
set -eC
umask 077
quest_observation_dir="$(mktemp -d "$quest_run_dir/recheck.XXXXXXXX")"
printf 'Observation directory: %s\n' "$quest_observation_dir"
timeout 15s adb -s "$quest_serial" get-state
timeout 15s adb -s "$quest_serial" shell dumpsys power > "$quest_observation_dir/power-current.txt"
timeout 15s adb -s "$quest_serial" shell dumpsys battery > "$quest_observation_dir/battery-current.txt"
timeout 15s adb -s "$quest_serial" shell settings get global stay_on_while_plugged_in \
  > "$quest_observation_dir/stayon-current.txt"
)
```

導入済みCLIのセンサー状態や機種・OS等も必要に応じて再確認し、結果はこの観測directoryへ置く。自分のセッションの時限設定が不足する場合だけ延長/再適用し、新しい期限を記録する。無期限のbackground再設定ループを追加しない。

接続断時は、既知の対象endpointへ期限付きで再接続する。

```bash
# quest_endpointは、この物理Questについて確認済みのIP:port
quest_endpoint='REPLACE_WITH_CONFIRMED_IP_AND_PORT'
timeout 15s adb connect "$quest_endpoint"
timeout 15s adb -s "$quest_endpoint" get-state
```

接続後は同じ物理Questであることを再照合してから`quest_serial`を更新する。再試行は回数/総時間を決める（例: 2回、合計60秒以内）。到達不能なら、実機の電源ボタン/装着による復帰や、既存のUSB経路が必要と報告する。到達不能なADB経路へwakeコマンドを送れば必ず起こせるとは扱わない。[ADB][adb]

今回のpreflightのために共有ADB serverの`kill-server`、既存forwardの一括削除、未知端末への接続、無条件の`tcpip`再設定を行わない。必要なforwardの再作成は[既存の接続手順](development-automation-and-debugging.md#usb以外の接続と実機ci)に従い、自分のものだけを扱う。

`lifecycle` profileでは、通常の脱着/sleep試験を始める前に、近接override、`stayon`、本体Auto Sleep、MQST、Developer Hubの自動再無効化、castingの影響を確認する。自分の変更を復元し、試験で要求する通常設定へ移したことを記録する。他の所有者の継続設定が残る場合は担当者と調整し、解除できない条件で通常sleep試験の合格を報告しない。試験後の開発設定への復帰も、その開発セッションの所有範囲だけに限る。

## 6. 終了時は、自分が変更した設定だけ復元または引き継ぐ

個別のテスト終了ではなく、**開発セッションを終える時点**で判断する。

- 同じセッションで開発を継続する、または既存の継続指示を引き継ぐなら、設定を維持し、所有者・期限・次回確認・復元担当を記録する。
- 通常のセンサー状態から自分がoverrideを入れ、開発を終える場合は、使用した経路で通常制御へ戻す。
- 開始前から有効だった設定、変更前状態が不明な設定、他のセッションの設定を、自分のcleanupで解除しない。未確定の復元は明示して引き継ぐ。

**復元順は、給電中設定等の復元・読取確認を先にし、近接overrideの解除を最後にする。** 先に通常の装着検知へ戻すと、残った設定を戻す前に眠ってWi-Fi ADBを失う可能性がある。

`stayon`を自分が変更した場合は、今回保存した元値へ戻す。復元前に現在値を読み、自分の変更後値と一致することを確認する。他の変更があれば上書きせず所有者を確認する。`false`は0への上書きなので、元値が0だった場合にしか同じ復元にならない。

```bash
# 手順3で保存した今回の元値を読み、通信エラー/空文字でないことを確認する
(
set -e
quest_stayon_before="$(tr -d '\r\n' < "$quest_run_dir/stayon-before.txt")"
quest_stayon_after="$(tr -d '\r\n' < "$quest_run_dir/stayon-after.txt")"
[[ "$quest_stayon_after" =~ ^[0-9]+$ ]]
quest_stayon_current="$(timeout 15s adb -s "$quest_serial" shell settings get global stay_on_while_plugged_in)"
if [[ "${quest_stayon_current//$'\r'/}" != "$quest_stayon_after" ]]; then
  printf '%s\n' '変更後の値と一致しないため、現在の設定を維持します。' >&2
  exit 1
fi
case "$quest_stayon_before" in
  null)
    timeout 15s adb -s "$quest_serial" shell settings delete global stay_on_while_plugged_in
    ;;
  ''|*[!0-9]*)
    printf '%s\n' '元値が不明です。推測で復元せず restoration_pending として記録してください。' >&2
    exit 1
    ;;
  *)
    timeout 15s adb -s "$quest_serial" shell settings put global stay_on_while_plugged_in "$quest_stayon_before"
    ;;
esac
quest_stayon_restored="$(timeout 15s adb -s "$quest_serial" shell settings get global stay_on_while_plugged_in)"
quest_stayon_restored="${quest_stayon_restored//$'\r'/}"
test "$quest_stayon_restored" = "$quest_stayon_before"
)
```

`null`は設定が存在しなかった場合の復元。元値の読取コマンド自体が成功していたことが前提である。復元結果の読取りと電源状態も記録する。必要な復元・読取確認に成功した場合に、最後に近接overrideを解除する。復元失敗時は後続の解除を進めず、所有する設定と残件を記録する。

```bash
# CLI経路で、自分が通常状態から変更した場合
timeout 15s metavr -d "$quest_serial" device proximity --enable
timeout 15s metavr -d "$quest_serial" device proximity --status

# ADB経路で、自分が通常状態から変更した場合（上のCLI経路と選択する）
timeout 15s adb -s "$quest_serial" shell am broadcast \
  -a com.oculus.vrpowermanager.automation_disable
```

中断・timeout・通信断で復元できなければ`restoration_pending`とし、残った変更、対象、期限、再接続後の復元手順を報告する。CLIの期限経過は復元予定の根拠にはなるが、未観測の復元を確認済みにしない。

## 7. 設定ごとの適用範囲と、通常preflightで使わない操作

| 設定/方法 | 適用範囲・戻し方 | この手順での扱い |
| --- | --- | --- |
| 本体`Settings > Power > Auto Sleep` | 未装着後の待ち時間。元の時間へ戻す。OSによるUI差は実機確認 | 短い離席の補助。時間延長だけで無期限の接続維持とはしない。[lifecycle][meta-lifecycle] |
| MVDH（旧MQDH）`Device Manager > Device Actions > Proximity Sensor` | Windows/macOSのGUI。切断後通常10分、または設定に示された時間で再有効化 | 使用中ならCLIとの競合を確認。接続時自動無効化の設定も記録。USBを外してもWi-FiでHub接続が続く場合がある。[Hub][hub] / [自動切替][hub-auto] |
| `svc power stayon true` | 外部給電中。global設定を保存するため元値へ明示復元 | 必要時だけ追加。近接センサー対策とは別。[AOSP][power-command] |
| MQST `disable_autosleep` | 開発用テストサービス。Store PIN・単一テストユーザーを前提とする案内。**reboot後も残る** | 通常preflightの自動設定対象外。専用E2E計画で現状取得・変更・復元を管理する。[MQST][mqst] |
| `FLAG_KEEP_SCREEN_ON` | アプリのwindowが表示中の画面維持。backgroundでは通常の消灯が可能 | 装着検知や開発環境全体の維持の代用にしない。[Android][screen-on] |
| `wifi_sleep_policy=2` | Android API 30以降は非推奨でplatformが使用しない設定 | Quest独自対応の根拠なしに対策へ加えない。[Settings.Global][settings-global] |

Boundary/Guardianやdialogの無効化、`configure-testing setup/restore`の一括変更、端末初期化、root化、鍵やPINの変更は本手順に含めない。`configure-testing restore`が現在の利用者の元設定を復元するとも仮定しない。スリープ対策のために製品側のActivity/XR lifecycleやwake lockを変更しない。

## 8. 非公開のセッション記録と報告

以下は記録項目のテンプレートであり、新しい自動生成schemaではない。`unknown`、未実行、未確認を埋め合わせない。

```text
session_id / owner / host:
source_sha / checked_at:
profile: development | lifecycle
physical_device_id / current_transport: 非公開記録のみ
tool_versions / model / OS_fingerprint:
ownership: exclusive | inherited | blocked
preflight: ready | blocked_device | not_applicable
device_validation: performed | not_run
before: proximity / stayon（nullを区別）/ powered / wakefulness
decision: reuse | apply_missing | ensure_unknown_baseline | blocked
changed: method / values / command_result / own_changes_only
lease: started_at / expires_at_or_unknown / next_recheck / continuation_policy
after: observed_connection / observed_power / proximity_status_or_unknown
observation: duration / worn_state_or_unknown / remaining_unknowns
cleanup: unchanged | restored_verified | handed_off | restoration_pending
handoff: next_owner / remaining_changes / expiry / recovery_steps
```

開発開始時には、対象を秘匿した短い結果、変更した項目、期限、阻害があればその理由を報告する。終了時には復元または引継ぎを報告する。`ready`は実機開発を開始できる状態の確認であり、製品テスト、描画、操作感、長時間無人運用やQuest/HMD受入の合格ではない。

## 一次資料

- [Meta VR CLI reference][cli] / [Install Meta VR CLI][cli-install]
- [Meta XR Operator: Test with the headset off your face][operator]
- [Manage your device with MVDH][hub] / [MQDH 4.7の自動近接センサー設定][hub-auto]
- [Meta application lifecycle][meta-lifecycle] / [Quest Scriptable Testing][mqst]
- [Android Debug Bridge][adb] / [AOSP PowerCommand][power-command]
- [Android Settings.Global][settings-global] / [Keep the screen on][screen-on]

[cli]: https://github.com/meta-quest/agentic-tools/blob/main/docs/metavr-cli.md
[cli-install]: https://developers.meta.com/vr/essentials/metavr-install/
[operator]: https://developers.meta.com/vr/documentation/unity/meta-xr-operator/quest/#test-with-the-headset-off-your-face
[hub]: https://developers.meta.com/vr/documentation/unity/ts-mqdh-basic-usage/
[hub-auto]: https://developers.meta.com/vr/blog/quest-developer-updates-mqdh-47-preview-pdp-changes/
[meta-lifecycle]: https://developers.meta.com/vr/documentation/unity/unity-lifecycle/
[mqst]: https://developers.meta.com/vr/documentation/spatial-sdk/ts-scriptable-testing/
[adb]: https://developer.android.com/tools/adb
[power-command]: https://android.googlesource.com/platform/frameworks/base/+/HEAD/cmds/svc/src/com/android/commands/svc/PowerCommand.java
[settings-global]: https://developer.android.com/reference/android/provider/Settings.Global
[screen-on]: https://developer.android.com/develop/background-work/background-tasks/awake/screen-on
