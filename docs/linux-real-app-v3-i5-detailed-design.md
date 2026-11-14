# I5: Android storage 実回収・設定保存・通常ランチャー詳細設計

## 0. 基準、位置づけ、対象外

開始時に GitHub の main と関連 PR を再取得した。基準は
`eca71addc58009b4576644da162d56670eebfaf5`（PR #16 の merge commit）。
#16 は merged、関連する未マージ PR #30 は I5 後の任意の構造解析計画である。
この文書は **今回の実装設計**であり、設計承認・製品受け入れ・Quest 成功を宣言しない。
I2/I4 resolver を別々に実行した。I5 は manual discovery とし、登録済み task を装わない。
過去の文書に記録された open/Draft 状態は、その基準日時の記録として維持する。

I5 は V09/V10、v2/Termux/diagnostic 回帰、保存と Runtime 操作の排他を担当する。
I6 の実 SSH/C2/Waypipe/Mousepad 全経路 T20/V11、I7 の Quest operator/APK/実保存 V12、
H の装着・視認・物理入力は別の受け入れである。

基礎契約: [統合計画](linux-real-app-v3-integration-plan-20261111.md) §10/16/17.2/19–21、
[B1 identity](linux-real-app-b1-android-identity-20261110.md)、[E1](linux-real-app-e1-storage-20261110.md)、
[I2](linux-real-app-v3-i2-detailed-design.md)、[I3](linux-real-app-v3-i3-detailed-design.md)、
[I4](linux-real-app-v3-i4-detailed-design.md)、[#16 retirement 記録](linux-desktop-v3-pr16-retirement-evidence-20261114.md)。

## 1. main に対する差分表

| 対象 | 基準 main の実装 | I5 で接続・追加するもの |
| --- | --- | --- |
| `LinuxDesktopV3Config` | raw 重複 key 拒否、canonical、digest、32,768-byte import | 通常設定 UI、保存 repository、Runtime assembly から再利用 |
| `LinuxStorageRepository/Store` | E1 anchor/intent/profile/record/commit、capture/CAS、明示 resume | Host 保存 owner と実 barrier。ID 生成を複製しない |
| `LinuxStorageFiles/AndroidLinuxStorageFiles` | no-follow、所有属性、fsync、rename、readback、process 共通 lock | config/marker/設定 intent の固定 private path へ再利用 |
| `LinuxProfileStore` | 旧 v2 writer が通常 UI で直接使用される | 証跡なし legacy だけに限定。E1 証跡あり・破損・Pending は別扱い |
| `NativeSessionHost` 保存 | productWorker から `stopRuntime()` の後に profile/pins 保存 | worker が capture/epoch/二段階回収/全保存を一貫所有する state machine |
| `SdkRetirementHub` | runtime/epoch/token ticket と v3 SDK ledger | 全 Runtime owner の ticket、typed StopReceipt への結合 |
| `SpatialPanelAdapter` | v3 Create は scope/fence を持つ。legacy/diagnostic は弱い別経路 | 全 binding を reserve し、診断・旧 Scene・全 generation・補助 Entity・callback を回収対象にする |
| I2/I3/I4 | origin retirement、journal finalizer、live Recover が接続済み | origin 回収完了後にだけ Runtime 全停止へ進む。Unknown は Controller を保持 |
| native Runtime config | v2/v3 排他、strict raw envelope、storage/profile 検査、16KiB 上限が存在 | Android 通常保存済み組から構築。境界テストを追加 |
| native catalog/Start | B1 は固定二項目。transport/runtime の通常経路には Mousepad 固定部分 | B1 descriptor と両 typed Selection へ統一。同時 lease 数は増やさない |
| Android launcher | v2 GTK 一項目、v2 表示 heuristic | v3 固定二項目、runtime/session/capture/revision 照合、I4 report 表示 |
| Runtime snapshot | shutdown/transport/render 等は別 object | 保存用 typed reclamation report。実 Destroy の戻り値は Host だけが加える |
| 設定有効版 | pins 中心、config/active marker 未接続 | `connection.json`、`active-backend.json`、途中保存 intent、全体 reload |

## 2. 所有者、型、admission

`NativeSessionHost.worker` を唯一の保存 owner とする。既存 `productWorker` は同 worker へ要求を提出し、
追加する admission gate は **queue 投入時の epoch** を捕捉して実行時にも照合する。
保存開始で epoch を一度進めて閉じる。新規 Connect/Launch、profile/pins/設定変更、通常 Runtime 再生成、
debug exclusive/probe 開始を閉じる。SDK callback、native release、credential 応答、poll、保存 owner の
明示 Recover は止めない。UI は worker の完了を同期 wait しない。

追加する純粋型:

| 型 | 内容 / 所有 |
| --- | --- |
| `RuntimeAdmission` | 単調 epoch、open/closed、保存 owner identity。通常 queued command は旧 epoch なら破棄 |
| `RuntimeSaveCoordinator.Operation` / `RuntimeAdmission.Owner` | exact request capture token、old runtime、epoch は immutable。Operation が phase、期限、同意図の publish callback、receipt を保持し、Owner の object identity も照合 |
| `NativeReclamation` | 同 runtime の native snapshot から strict decode する所有数・origin/recovery flags |
| `StopReceipt` | 最後の生存 native snapshot、実際の `nativeDestroy` Accepted、同 ticket の SDK 結果。公開 JSON から生成しない |
| `RuntimeOriginStop` | 元 Connect request または session、取消 command request と実 command_complete。queue Accepted / STALE を回収証拠にしない |
| `LinuxDesktopBackend` | `V2` / `V3`。曖昧な optional field の組合せで判定しない |
| `LinuxDesktopSettingsRepository.SaveRequest` | strict parse 済み設定、next profile、版、全 expected bytes digest、元 capture token |
| `LinuxDesktopSettingsRepository.Snapshot` | `UNCONFIGURED / LEGACY_V2 / READY_V2 / READY_V3 / SAVE_PENDING / BLOCKED`、capture、profile/record/config/pins、固定 reason、canResume |
| `LinuxDesktopV3Catalog` | immutable runtime/session/settings capture と strict B1 descriptor、二項目の検証済み Selection |

capture の `toString`/診断は profile、host key、設定本文を露出しない。epoch/token は authority の照合に使い、
JSON に渡された `reclaimed=true` や UI の ready を authority にしない。

## 3. 二段階回収の状態遷移

| phase | 入力・処理 | 次状態 / 失敗時 |
| --- | --- | --- |
| idle | worker で repository capture と strict parse。確認済み要求を保持 | epoch を進め admission を閉じ origin_stopping |
| origin_stopping | 対象 session の既存 Disconnect/Stop。core/SDK/I2 の origin retirement を drain | `origin_retired` なら runtime_stopping。remote pending/期限切れなら recovery_required |
| recovery_required | 元 Capture と旧 Controller を保持。設定 publish は一切しない | 利用可能な `recovery_connect_allowed` / `recovery_command_allowed` のときだけ明示 Recover |
| recovering | 必要なら旧 profile で Connect、credential capture を保持、元の有効版の typed v2/v3 Recover | 同 capture finalizer/journal clear 後に回復用接続も Disconnect。未完了なら pending を保持 |
| runtime_stopping | connection/coordinator/diagnostic も含め nativeRequestStop、SDK ticket seal | shutdown complete と全 ownership/callback drain を待つ |
| reclaimed | **最後の生存 snapshot** を捕捉し nativeDestroy を実行、Accepted を Host が記録 | 同 runtime/epoch/token の StopReceipt を生成。BUSY/STALE/失敗は保存不可 |
| publishing | `withReclaimed` callback 内で保存だけを実施 | 全 file の readback 一致なら committed。不一致/例外なら save_pending または blocked |
| committed | repository 全体一致を再確認。保持した gate を終了 | admission を開き新 Runtime を作る。自動 Connect/Launch はしない |

表の `reclaimed` / `publishing` は同期処理中の段階で、外部から操作できる enum phase ではない。
実装の enum は `IDLE / ORIGIN_STOPPING / RECOVERY_REQUIRED / RECOVERING / RUNTIME_STOPPING / SAVE_PENDING / COMMITTED`。
Connect の command_complete Accepted は session 割当完了を意味しない。Host はその後も元 Connect request を保持し、
session 未割当でも CancelConnect を送る。取消の実完了を待ち、STALE なら観測した同 session の Disconnect へ移る。
この実完了も origin proof の代わりにはせず、次段階への移行は census の `origin_retired` に限る。

通常の保存に旧 `stopRuntime()` 全体をそのまま使わない。第一段階の Disconnect は transport Controller を
終了させず、元の RunCapture/PreviousIoClosed を使う既存 live Recover を残す。新 Runtime への run authority
の自動移送は行わない。Controller を既に停止した不明 owner、cold restart で旧 SDK/local proof が失われた
journal、Scene 終了までに delete/後続 pass が得られなかった lease は pending のままである。

保存待機は scheduled tick の短い一歩で進める。SDK は独立した MainLooper poll と captured Scene の
lateExecute/delete listener で進行する。worker は SDK completion を同期 await せず、UI は worker を待たない。
timeout は失敗理由を記録する期限であり、資源を回収済みにする規則ではない。

保存開始で旧 terminal/catalog の自動継続と theme import token を無効化する。theme callback は元 runtime と
admission epoch を再照合し、失効した file を破棄する。debug probe/exclusive も enqueue 時 epoch を照合する。
通常 catalog pump と SSH summary は gate 閉鎖中に保存表示を上書きしない。SDK release / ACK / callback drain は継続する。

初回未割当は process 内の `neverAllocated` と区別する。その場合だけ接続を許さない最小 Runtime を作り、
実 Stop/Destroy を得て保存する。過去に割当済みで receipt がない `handle == 0` を初回に変換しない。
同 process の保存失敗後は、同 capture の receipt と閉じた epoch を保持して明示 resume する。
再開時に repository が返す新 token は、同じ durable intent の旧/新 bytes の範囲だけを owner が再束縛する。
任意の別設定・別 profile・別 epoch の token への付け替えは禁止する。

## 4. typed StopReceipt と全所有数

native `runtime.reclamation` に次を追加する:

- 正の `runtime`、`shutdown_complete`、`origin_retired`。
- `connections`, `workers`, `native_targets`, `panels`, `sdk_bindings`, `board_windows`, `pending_operations`。
- `remote_cleanup_pending`, `local_cleanup_pending`。
- Controller の現在の private authority から導出する `recovery_connect_allowed`, `recovery_command_allowed`。

workers は core/coordinator、transport Tokio/SSH/forward/router、render、helper/dependencies/compositor/theme、
local Waypipe、finalizer を含む。pending_operations は command/render queue、transport の作業、未完了 panel、
finalizer を含む。events は通知であり所有資源と区別する。SDK binding 数は diagnostic を引かない。
native target/Board/panel も旧 generation を含む。report flag は UI の案内であり、効果実行時の既存検査を置換しない。

Host が以下を全て満たす時だけ `ReclamationEvidence` を作る:

1. snapshot の runtime が捕捉 runtime と一致し、snapshot 自体が Accepted。正数と全 field の型・存在を検査。
2. shutdown complete、上記全数 0、remote/local cleanup pending false。
3. SDK ticket の runtime/epoch/token が一致し、sealed、issued == complete、pendingCallbacks == 0、failures == 0。
4. Host が **この snapshot の後**に呼んだ nativeDestroy が Accepted。
5. 同保存 owner が gate を持ち、新 Runtime が生成されていない。

`withReclaimed(token, action)` はこの receipt を検査し、callback の前後で owner/epoch を再照合する。
callback は同期の file 保存だけを行い、Stop/SDK wait/network を始めない。
origin proof、relaunch_allowed、空 snapshot、新 Runtime のゼロ、STALE、古い ACK、disk Complete は代用しない。

## 5. SDK 全 Runtime retirement

`SdkEvidenceBridge.Lease.createCommand` の null を非 v3 binding に使用し、全 Create を副作用前に reserve する。
v3 の current/retiring/retired は既存 native strict port を使用する。
v2/Termux/diagnostic は通常 register/release port を使用するが、SDK 側では同じ scope と deletion fence を持つ。
`nativeBindingReleased` は実 Accepted だけで記録し、STALE を成功へ変換しない。

scope は Entity/decoration/controls/menu、Surface producer、SceneObject future、listener、callback を所有する。
sealed 後の既存 producer/callback は drain 義務を残す。delete 通知の後の **同 Scene** pass まで待ち、
新 Scene/世代の frame や timeout を流用しない。失敗 record は空の collection へ置き換えない。
通常保存は `SdkRetirementHub.retireRuntime(runtime,epoch,token)` で seal し、adapter poll が対象 binding を退役させる。
Activity/Scene 全体の `SpatialPanelAdapter.close()` は呼ばない。

## 6. 保存ファイル、capture、transaction

| noBackup 相対 path | 内容 / publish 順 |
| --- | --- |
| `linux-profile/remote-linux.json`、E1 anchor/record/transaction/commit | 既存 E1 が所有。旧 writer と競合させない |
| `linux-desktop-v3/transaction.json` | I5 設定 intent。実全回収 barrier の取得後、E1 前に CAS publish |
| `linux-desktop-v3/connection.json` | `{schema:1,kind:"linux-desktop-v3-saved",profile_binding,storage_record_sha256,configuration}` |
| `linux-desktop/active-backend.json` | `{schema:1,protocol_revision,profile_binding,configuration_sha256,storage_record_sha256}`。最後に publish |

settings capture は profile、E1 capture、pins、config、marker、intent の exact bytes digest を束縛する。
prepared request は capture、next profile、版、strict canonical config/pins、各 expected token を保持する。
I5 intent はそれらを durable に保存し、E1 成功・config 前の中断でも次設定を失わない。
E1 の anchor/intent と混同しない。秘密鍵・自由な remote path/argv は含めない。

保存順:

1. read-only load → capture 照合 → strict parse → UI の明示確認。
2. §3–5 の全回収。完了前は I5 intent も E1 も publish しない。
3. I5 intent を CAS publish/readback。
4. E1 save または resumePending。既に intent の next profile/record に Matched なら再保存しない。
5. v3 保存 object を CAS publish/readback（v2 は対応 pins）。profile binding と storage digest を結合。
6. marker を最後に CAS publish/readback。
7. profile/record/intent/config/marker/pins を再読し、組全体の一致を確認。

各 rename は単一 file の atomic publish であり、複数 file の atomic transaction ではない。
途中失敗の reload が Matched でも、失敗した試行を成功へ変更しない。閉じた admission と最初の失敗理由を維持する。
明示 resume は exact old/next file だけを roll-forward し、新 ID・余分な revision を作らない。
CAS 競合・異なる bytes・anchor 破損は Blocked。旧 marker+新 config で起動しない。

v2→v3 初回登録は E1 Uninitialized の証拠が必要。同 host/user 編集は storage ID を保持、host key/user 交代は
E1 の新 profile storage ID を使う。初回登録と欠落を取り違えない。
v3→v2 の明示切替も全回収が必要。profile を変えない切替では E1 を再保存せず、v3 storage/config を保持する。
v3 登録証跡があるのに marker がない/壊れている時は v2 fallback しない。

旧 profile-only writer は E1 Uninitialized かつ v3/config/marker/intent 証跡なしの legacy 利用に限定する。
E1 登録済みの profile 編集は保存済み版の設定と一緒に settings transaction へ回す。
Blocked を Uninitialized に変換する catch/fallback を設けない。

## 7. 保存再開と cold restart の境界

同 process: origin recovery 前なら元 Controller/capture を保持して明示 Recover。全停止後なら同 receipt を保持し、
現在の同 intent の capture を検査して明示保存再開する。最初の失敗と後続 cleanup failure は別欄に残す。

file の durable intent は **保存内容の再開情報**であり、旧 local/SDK owner の authority ではない。
cold process で旧 owner proof が失われている時は、repository の roll-forward 能力を UI の実行許可へ直結しない。
新 Runtime のゼロで旧 owner の不明状態を消さない。I5 はこの不明状態を固定理由で表示し、使えない Recover を
有効なボタンとして見せない。明示 resume に新しい実 barrier を必要とする。
接続を一度も割り当てず、保存 intent 自体が全回収後にしか作られず、旧 journal がない場合も、
Host は cold owner の扱いを別判定し、disk Complete のみから receipt を構成しない。

## 8. Runtime 設定と byte 境界

通常 Runtime は settings Snapshot の一致した profile+record+configuration だけを使う。
`linux_profile_v2` と v3 raw envelope `{configuration,storage}` を同 capture から構築する。
v2 は pins のみ。両版混在、profile なし、storage のみ、異なる installation/profile は拒否する。
native の既存 strict parser と版 dispatcher を再利用し、異版へ再 parse しない。

import は `LinuxDesktopV3Config.parse(raw)` を最初に呼ぶ。JSONObject に入れてから parse しない。
import raw UTF-8 は 32,768 bytes、pins/manifest は既存境界、canonical runtime envelope と profile/paths/
その他全体を含む最終 Runtime JSON は UTF-8 16,384 bytes 以下。BuildHooks の加工 **後**にも確認する。
JSON escape、非 ASCII、最大文字列、余分な空白、重複 key を固定 fixture/テストで検査する。
JNI/command limit は引き上げない。

## 9. 固定二項目ランチャーと状態

native は B1 descriptor の二項目 `gtk3-desktop` / `mousepad-editor-v1` を返す。
Wayland app_id `mousepad` は selection ID ではない。未知 ID/label/mode、重複、欠落、旧 revision は拒否。
transport の private Selection と catalog は同 run capture から作り、Start に nonce/storage/argv/path/ready を追加しない。
単一 GUI lease 制約は維持する。

Android の表示 snapshot に runtime/settings capture/session/catalog revision を保持する。
通常 click はこれらと固定 ID を worker で再照合し、同一 action がある間は二つ目の Start を queue しない。
旧 debug/v2 signature から v3 launch は許可しない。再起動は旧 lease の journal/local/SDK 回収後にだけ許可し、
nonce は既存 native owner が新規生成する。

表示は native transport/B1/I4 report を reducer に渡し、以下を区別する:

| state | 日本語表示の意味 |
| --- | --- |
| catalog_waiting | SSH/固定アプリの確認待ち |
| awaiting_main | 対象アプリの main window 待ち |
| awaiting_sdk | 同 target の SDK/renderer 証拠待ち |
| ready | I4 の現在の ready。command Accepted ではない |
| stopping | origin/Runtime の停止進行中 |
| pending | journal/remote/local/SDK の回収未完了 |
| configuration_mismatch / save_pending | 設定の組不一致 / 明示保存再開待ち |

STATE_LIMIT は上限到達として表示し、未導入へ変換しない。first_failure と cleanup_error は別々に表示する。
available は Inspect、ready は I4、Matched は保存、acceptance は I6/I7/H の証拠からのみ判定する。

## 10. ファイル別実装分担

| file | 変更 |
| --- | --- |
| 新 `LinuxDesktopV3ConfigRepository.kt` / `LinuxDesktopSettingsRepository.kt` / `LinuxDesktopSettingsStore.kt` | strict saved config/marker/intent、capture、save/resume、版 selection、既存 E1 repository 接続 |
| `LinuxProfileStore.kt` / `LinuxDesktopPinsStore.kt` | legacy writer の登録証跡 guard |
| 新 `RuntimeAdmission.kt` / `RuntimeSaveCoordinator.kt` / `RuntimeOriginStop.kt` | epoch、二段階回収、StopReceipt、取消実完了、固定 failure、barrier |
| `NativeSessionHost.kt` | 上記 owner の通常 wiring、設定 UI/save/resume/recovery、最終 config、catalog/status |
| `SdkEvidenceBridge.kt`、既存 `SdkRetirementHub.kt` の利用 | 全 binding evidence、完了済み generation の再登録拒否、runtime/epoch/token ticket の再利用 |
| `SpatialPanelAdapter.kt`、既存 `SdkResourceScope.kt` の利用 | diagnostic/legacy も captured scope、実 delete/pass/callback drain、release Accepted |
| 新 `LinuxDesktopV3Catalog.kt` / `LinuxDesktopV3Status.kt` / Runtime config assembly | strict fixed catalog、状態/reason reducer、byte 上限 |
| `ConnectionSettings.kt` / `ApplicationLauncher.kt` / `LauncherState.kt` | 版を明示、設定一括保存、再開、capture 付き click、I4 状態 |
| `native/core/src/diagnostics.rs` / `runtime.rs` | typed 全 Runtime reclamation report |
| `native/transport/src/session.rs` / `linux_desktop/v3/runtime/*` | fixed 二項目 Selection と catalog、recovery 可否の実 private authority |
| `native/transport/src/linux_desktop/journal.rs` | v2 live Recover の元 Binding 比較だけを追加。record schema・保存方式・固定 remote source は変更しない |
| JVM/native test suites | 下記 V09/V10 と回帰。既存 CI entrypoint から実行 |
| docs entrypoint と本書 | manual I5 discovery、検証記録へのリンク。歴史的状態を一括変更しない |

責務別 commit: (1) 本設計、(2) 設定 repository、(3) native report/catalog、(4) SDK/global barrier/Host、
(5) 通常 UI/launcher、(6) 統合検証で必要な修正と結果。push/PR は原則一度にまとめ、CI gate は維持する。

## 11. 検証条件

| suite | 成功条件 / 必須拒否 |
| --- | --- |
| V09 settings | 初回/同host-user/交代、E1とconfigとmarker全 publish 境界、readback failure、同 intent resume、ID/revision不増分 |
| V09 marker | 欠落/破損/mixed pair/旧 marker拒否、v3→v2保持、legacy-onlyを明示識別 |
| V10 barrier | 各 counter 単独残存、remote/local pending、Destroy Busy/STALE/failed、SDK failure/callbackで一切publish無し |
| V10 concurrency | 旧 capture/CAS競合/並行profile編集、Runtime再生成、旧epoch callback/queueの拒否 |
| V10 SDK | diagnostic/旧Scene/全世代/late callback、delete後pass、worker待機中の独立進行、dispose failure保持 |
| Recover | 元captureだけ、live Controller保持、timeout後journal/config/初回理由保持、cold unknown拒否 |
| launcher | 二項目の厳格検証、未知/重複/label/mode/旧revision拒否、二重Start拒否、未回収再起動拒否 |
| compatibility | v3 default deny/旧target拒否、v2/Termux/diagnostic既存suite、固定v2 source/golden不変更 |
| bytes | raw import、duplicate key保持、canonical、UTF-8/escape、全Runtime/JNI上限 |

各段階で対象 unit を実行し、最終的に Host、Android JUnit/lint/APK、docs validate、check-design を実行する。
実行環境の不足は未実施として記録し、既存 suite の skip/limit 緩和で通さない。

実装後の実行範囲、ローカル環境制約、V09/V10 suite 対応と I6/I7/H への引き継ぎは
[I5 検証記録](linux-desktop-v3-i5-validation-20261114.md) を参照する。
CI は新 PR の **最終 HEAD** と checkout SHA/run/job を記録する。過去 #16 の結果は比較基準だけである。
V09/V10 の合成 fixture は判断器の検証であり、Quest 実 disposal/保存の受け入れ証拠ではない。
