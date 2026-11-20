# I6 Host acceptance 固定 case manifest

この directory は [I6 詳細設計](../../../docs/features/linux-desktop-v3/linux-real-app-v3-i6-detailed-design.md) §2、§11–15 の
標準 profile `i6-host-v1` を実行前に固定する。`cases-v1.json` の全 row、全 assertion は必須であり、
`required` / optional / 実行後の期待値選択は設けない。各 row は `(case_id, variant)` で一意とする。
manifest の bytes を SHA-256 で pin し、変更する場合は実行前にレビューする。

ここでの列挙は runner の実装・実施・合格を意味しない。証拠 validator の unit test成功と、
全 required row に実証拠が揃った I6 Host acceptance成功を区別する。I7/V12/Quest/Hは対象外。

## Schema と evidence class

- root: `schema` = 1、`profile` / `revision` / `verification_ids` は固定値。
- `cases`: 全28 familyのvariant row。rowは`case_id`、`variant`、`evidence_classes`、
  `expected_product_outcome`、`cleanup_policy`、`assertions`を持つ。
- `R`: 通常 Registry/Runtime と実SSH/C2/Waypipe/Mousepad/production compositorの証拠。
- `S`: native I4 gate に対する合成SDK境界。実Meta SDK/Surface/Android保存の証拠ではない。
- `T`: 限定fault seamまたは純契約検査。C04は実native gateへの到達が必須であり、gateをmockして通すものではない。
- `assertions` は意味的な証拠要求のID。report中の同名boolだけでは充足しない。
  後続oracleが対応する独立receipt/trace/file/pixelの内容を照合する。oracle未実装は未完了として残す。
- `not_applicable` / `contract_only` は C01–C05 の product cleanupを起こさない純契約検査専用。
  これらの成功は R/S ケースや実 resource census の代替にならない。

## 固定した cleanup policy

`product_complete` row は個別assertionに加えて、全資源census、同capture journal finalizer、
origin retirementをRuntime全停止より先に成立させる順序、最後の実 `Registry::destroy == Accepted` を要求する。
未割当境界は同じboolで成功扱いせず、manifestに次の別assertionを固定する。

| 固定した境界 | 必須assertionの意味 |
| --- | --- |
| N01、N03の二重Launch以外、N09、F11 catalog待ち | `no_local_allocation_sealed_before_stop`: 同captureのNoLocalAllocation sealを全停止前に採取。origin retirementを捏造しない |
| F08 publish/rename後fsync | Inspect-onlyでjournal I/O失敗を注入。retry後のjournal finalizerとNoLocalAllocation sealを要求 |
| F11 Session前 | `no_journal_allocation_sealed` と `no_origin_allocation_sealed_before_stop`: 生存Runtimeのallocation封鎖と未生成証拠。journal finalizerは存在しない |
| N11 v2 Recover | `legacy_journal_finalized` と `legacy_cleanup_before_runtime_stop`: 旧版のcapture/journal/回収契約。v3 originを要求しない |

N09の候補はInspectより前に配置する。F08 unlink後fsyncは通常Launch後のfinalizerで注入する。
未割当の証拠は空配列や新Runtimeの所有数0で置き換えない。

`preserve_unknown_then_rescue` は product結果を pending/unknownに固定し、journal・保存/新規起動拒否を
保持した後に試験ownerのrescueを行う。rescueや環境破棄をproduct completeに昇格しない。
F05 write end保持variantだけは結果固定後にwriter FDを解放する。通常floodはterminalを待つ前にwriterを解放する。

F11の4 variantは回収不能faultを重ねず、元requestのcancel完了とproduct completeを要求する。
F12 callback/dispose variantは途中のpendingを必須としたうえで同leaseだけを修復し、最終completeを要求する。
旧Scene delete不足variantは修復せず retained_pendingを要求する。観測結果に合わせて期待値を変更しない。

## N03 の固定した拒否status

| variant | 固定status | 現行sourceの根拠と実行時点 |
| --- | --- | --- |
| unknown_app_id | InvalidArgument | [typed AppId](../../../native/model/src/linux_desktop_v3/mod.rs) と [Command::parse](../../../native/model/src/commands.rs) が未知値を拒否 |
| scope_changed | NotReady | [reserve_linux_desktop_v3_launch](../../../native/transport/src/session.rs) の captured selections に含まれない有効64hex scope |
| revision_changed | NotReady | 同上。現在scopeと有効な別64hex revision |
| old_catalog | NotReady | 同上。終了済み旧captureのselectionを、新catalogのある現行sessionへ提出 |
| double_launch | Busy | [RuntimeのLaunch分岐](../../../native/core/src/runtime.rs) の v3_pending/observer が生存する間に第二Launchを提出 |

非同期 commandはRegistry queueのAcceptedと実完了statusを別に記録する。
`start_count_at_most_one` は各variantのStart送信数≤1、`rejected_launch_no_spawn`は拒否したrequestに対応するspawn=0を意味する。

## Variant と必須観測

以下の表と設計書は assertion の意味を固定する。共通cleanup assertionの意味は上節のとおり。
`first_case_failure_preserved` は、存在する最初のcase failureをcleanup/expected product failureで上書きしないことを指す。

| Family | Variant | 期待product outcome | 必須観測の意味 |
| --- | --- | --- | --- |
| N01 | inspect_only | complete | 通常 Registry→実 SSH/C2 Inspect→Stop。Start=0、NoLocalAllocation seal と同 capture finalizer。 |
| N02 | gtk3_desktop | complete | 同じ通常 actor の catalog から GTK デモ を起動。実 frame と合成 SDK を join。 |
| N02 | mousepad_editor | complete | 同じ通常 actor の catalog から Mousepad を起動。実 frame と合成 SDK を join。 |
| N03 | unknown_app_id | complete | 未登録 app ID を wire parser へ提出。Registry queue の受理と command completion を区別。 |
| N03 | scope_changed | complete | 他は現在値のまま scope を別の有効64桁 hex へ変更。Registry queue の受理と command completion を区別。 |
| N03 | revision_changed | complete | 他は現在値のまま revision を別の有効64桁 hex へ変更。Registry queue の受理と command completion を区別。 |
| N03 | old_catalog | complete | 終了済み旧 capture の catalog selection を現行 session へ提出。Registry queue の受理と command completion を区別。 |
| N03 | double_launch | complete | 最初の Launch 受理後、同 origin の v3_pending/observer が生存する間に再提出。Registry queue の受理と command completion を区別。 |
| N04 | idle_125s | complete | 125秒以上の idle を実測し、前後の fresh Ctrl+O/Esc を実 gate 経由で確認。 |
| N05 | open_edit_save_as | complete | 設計 §10 の78-byte入力と95-byte出力の固定 bytes/digest。元 file は不変。 |
| N06 | open_cancel | complete | Open dialog を取消して編集継続。 |
| N06 | save_as_confirm | complete | Save As を確定して別名保存 bytes を確認。 |
| N06 | unsaved_close_cancel | complete | 未保存 Close を取消して入力を保持。 |
| N06 | close_confirm | complete | 未保存 Close を明示的に確定して終了。元 event reason を改変しない。 |
| N07 | same_storage_restart | complete | Line Numbers の off/on を独立 golden で観測。同 StorageRecord、新 nonce の2 session。 |
| N08 | independent_precursor | complete | 独立 A0 precursor control を明記。別 bus/display で先行状態が変わらないことを確認。 |
| N08 | fresh_storage | complete | 別 installation/capture の fresh storage を順に実行。保持 capture の ID だけを変更しない。 |
| N09 | session_candidate | complete | session_candidate を専用 storage に配置。app spawn 前に拒否。 |
| N09 | autosave_candidate | complete | autosave_candidate を専用 storage に配置。app spawn 前に拒否。 |
| N10 | dialog_repetition | complete | dialog_repetition で発行された全 generation の回収を照合。 |
| N10 | target_generation_change | complete | target_generation_change で発行された全 generation の回収を照合。 |
| N11 | frozen_v2_recover | complete | 旧5 source/journal/dispatch/Recover。旧 APK upgrade の証拠ではない。 |
| F01 | started_frame_loss_once | complete | 実 Started line を protocol accept 直前に一度 drop。Start再送/期限resetなし。 |
| F02 | client_to_sshd_blackhole | complete | client→sshd の bytes と EOF を止める。relay解放と旧 I/O fence 後に回復。 |
| F03 | control_eof | complete | 実 control EOF、C2 input_eof と同 capture receipt。 |
| F04 | kill_app | complete | app の生存 owner/pidfd に限った kill。 |
| F04 | kill_dbus | complete | dbus の生存 owner/pidfd に限った kill。 |
| F04 | kill_remote_waypipe | complete | remote_waypipe の生存 owner/pidfd に限った kill。 |
| F05 | low_rate_stop | complete | 各 stream 64 KiB/s、最大45秒。Stop/root death/writer budget の最初で writerを解放。 |
| F05 | stdout_limit | complete | stdout の高 rate。最大16 MiB/5秒。fault未達を成功にしない。 |
| F05 | stderr_limit | complete | stderr の高 rate。最大16 MiB/5秒。fault未達を成功にしない。 |
| F05 | write_end_held | retained_pending | 書込み停止後も write FD を保持し EOF不足を観測。product結果固定後に解放。 |
| F06 | receipt_collision | complete | 専用 r-receipt blocker で部分 publish を作り、同 inodeだけを除去。 |
| F07 | helper_owner_lost | retained_pending | completion proof前に helper ownerを失い、same boot Unknownを保持。 |
| F08 | journal_publish | complete | Inspect-onlyのjournal publishで限定fail-once。現行product deadlineは変更しない。 |
| F08 | rename_dir_fsync | complete | Inspect-onlyのjournal rename後directory fsyncで限定fail-once。 |
| F08 | unlink_dir_fsync | complete | 通常Launch後のfinalizerでunlink後directory fsyncを限定fail-once。 |
| F09 | package_changed_after_inspect | complete | Inspect後に専用環境の package を変更。旧 immutable Recover は新package判定から独立。 |
| F10 | host_process_restart | retained_pending | Host driverをkillし、新processで旧journalを読む。旧 local/SDK authorityは復活しない。 |
| F11 | before_session | complete | Session割当前の Connect取消。回収不能faultを加えない正常取消なので completeを要求。 |
| F11 | catalog_wait | complete | Inspect/catalog待ちの取消。回収不能faultを加えない正常取消なので completeを要求。 |
| F11 | launch_pending | complete | Launch受理後、Ready前の取消。回収不能faultを加えない正常取消なので completeを要求。 |
| F11 | after_ready | complete | B1 Ready後の取消。回収不能faultを加えない正常取消なので completeを要求。 |
| F12 | callback_hold_repaired | complete | 合成 callback を保持して pendingを確認後、同 leaseのproducerをdrainして回収。 |
| F12 | dispose_failure_repaired | complete | 同 leaseのdisposeを一度失敗。空ledger/timeoutで成功せず、同leaseの再試行後のみ完了。 |
| F12 | old_scene_delete_missing | retained_pending | 旧 Scene delete証拠を欠落させ、修復なしの retained_pendingを要求。 |
| C01 | config | not_applicable | 既存 config parserの goldenと拒否変異。独立Host acceptance成功には加算しない。 |
| C01 | storage | not_applicable | 既存 storage parserの goldenと拒否変異。独立Host acceptance成功には加算しない。 |
| C01 | catalog | not_applicable | 既存 catalog parserの goldenと拒否変異。独立Host acceptance成功には加算しない。 |
| C01 | mixed_version | not_applicable | 保存版でdispatchし、parse failureで別版へfallbackしない。 |
| C01 | byte_limits | not_applicable | 既存parser上限をUTF-8 bytesで検査。最終envelopeも独立に検査。 |
| C02 | deadline_exact | not_applicable | deadline_exact のfake-clock契約。実wallclock計測に読み替えない。 |
| C02 | heartbeat_inspect_race | not_applicable | heartbeat_inspect_race のfake-clock契約。実wallclock計測に読み替えない。 |
| C02 | heartbeat_launch_race | not_applicable | heartbeat_launch_race のfake-clock契約。実wallclock計測に読み替えない。 |
| C02 | heartbeat_stop_race | not_applicable | heartbeat_stop_race のfake-clock契約。実wallclock計測に読み替えない。 |
| C02 | heartbeat_eof_race | not_applicable | heartbeat_eof_race のfake-clock契約。実wallclock計測に読み替えない。 |
| C03 | boot_changed | not_applicable | boot_changed の明示的synthetic boot provider。実rebootは対象外。 |
| C03 | boot_same | not_applicable | boot_same の明示的synthetic boot provider。実rebootは対象外。 |
| C03 | receipt_contradiction | not_applicable | receipt_contradiction の明示的synthetic boot provider。実rebootは対象外。 |
| C04 | wrong_runtime | not_applicable | wrong_runtime を実装済みのnative gateへ到達させる契約検査。gateをmockした検査では充足しない。 |
| C04 | wrong_capture | not_applicable | wrong_capture を実装済みのnative gateへ到達させる契約検査。gateをmockした検査では充足しない。 |
| C04 | stale_ack | not_applicable | stale_ack を実装済みのnative gateへ到達させる契約検査。gateをmockした検査では充足しない。 |
| C04 | stale_generation | not_applicable | stale_generation を実装済みのnative gateへ到達させる契約検査。gateをmockした検査では充足しない。 |
| C04 | stale_input_epoch | not_applicable | stale_input_epoch を実装済みのnative gateへ到達させる契約検査。gateをmockした検査では充足しない。 |
| C04 | stale_focus_token | not_applicable | stale_focus_token を実装済みのnative gateへ到達させる契約検査。gateをmockした検査では充足しない。 |
| C05 | malformed_report | not_applicable | malformed_report の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | duplicate_key | not_applicable | duplicate_key の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | unknown_key | not_applicable | unknown_key の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | nonfinite_number | not_applicable | nonfinite_number の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | invalid_utf8 | not_applicable | invalid_utf8 の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | bool_as_int | not_applicable | bool_as_int の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | trailing_bytes | not_applicable | trailing_bytes の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | report_size_limit | not_applicable | report_size_limit の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | missing_case | not_applicable | missing_case の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | missing_variant | not_applicable | missing_variant の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | missing_assertion | not_applicable | missing_assertion の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | missing_artifact | not_applicable | missing_artifact の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | artifact_digest_mismatch | not_applicable | artifact_digest_mismatch の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | artifact_path_escape | not_applicable | artifact_path_escape の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | cross_run | not_applicable | cross_run の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | cross_capture | not_applicable | cross_capture の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | cross_source | not_applicable | cross_source の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | wrong_owner | not_applicable | wrong_owner の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | wrong_target_generation | not_applicable | wrong_target_generation の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | trace_overflow | not_applicable | trace_overflow の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | cleanup_evidence_missing | not_applicable | cleanup_evidence_missing の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |
| C05 | first_failure_preserved | not_applicable | first_failure_preserved の変異に対する独立validator拒否。欠損のincompleteと矛盾のinvalidを区別。 |

28 case family、87 required variant、533 assertion参照。
同名assertionは各rowのowner/captureに束縛して個別に検証する。
package/image/render/fontのfingerprintやgoldenは実測前のため、このmanifestへ架空の値を追加しない。
