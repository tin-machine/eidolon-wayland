# L7a B2: v3 wireと版別dispatchの受渡し

2026-11-10（日本時間）。B2がPython/Rustの**単一のwire契約**を所有する。
この文書は[B1確定契約](linux-real-app-b1-contract-20261110.md)に未定義だったenvelope/payloadを追加する。
B1のschema、canonical/hash式、既存golden、storage identity、presentation契約は変更しない。

## 完成範囲と実行許可

| 入口 | 責務 |
| --- | --- |
| [Python wire](../scripts/linux_desktop_v3_wire.py) | 純粋なcommand/event/receipt codec、状態遷移、有限frame decoder |
| [Rust v3](../native/transport/src/linux_desktop/v3/mod.rs) | B1型を再利用するPins/Binding、captureしたmanifest/sourceとcatalogの照合 |
| [Rust protocol](../native/transport/src/linux_desktop/v3/protocol.rs) | 一つのin-flight request、event順序、同期ACKと非同期terminalの因果範囲 |
| [版dispatch](../native/transport/src/linux_desktop/dispatch.rs) | 保存時に明示選択したV2/V3へdispatch。remote JSONは版を選べない |
| [receipt codec](../native/transport/src/linux_desktop/v3/receipt.rs) | capture済みcontext/pins/source/bootと回収証拠を照合 |
| [共通wire fixture](../scripts/tests/fixtures/linux-desktop-v3-wire.json) | Python/Rust双方が読む固定16正常record、383拒否、19状態trace |

`ExpectedEvidence`は**構文とcapture照合の型**であり、sourceの信頼、package実在、起動許可の証明ではない。
`capture()`が構成するtrueは、検証済みhelperから期待する値を表す。
製品側は先にコンパイル済みsource集合・immutable bundle/manifestを照合し、C2がそのsourceで実検査を行う。
C2はdemo、package/dependency、settings backend/probeを実検査した後にだけ対応するtrueをemitする。
版番号やremote boolだけを根拠にcatalog readyへ進めない。

現B2ではC1/C2完成sourceが未統合であり、`sources::require_complete()`は実行を拒否する。
合成fixtureから製品pinsを出力しない。`available` / `production_acceptance_passed` / `quest_verified`はfalse、
通常v3 acceptanceは`APP_V3_NOT_IMPLEMENTED`のまま。D/E2のnative/SDK/UI接続も含めない。

## 1. JSONとframe

[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)のJSONを、B1で固定した限定domainへ制限する。
duplicate keyはB1のraw parserで拒否してから値を扱う。Serde/Pythonの辞書へ先に読んで重複を消さない。
object以外の位置array、enum object、未知・欠落・余剰field、float/boolの整数代用も拒否する。
新codecが独自のcanonical/hash実装を持つことはない。

各recordはB1同様16,384 bytes以内。transportはLFで区切り、CR、空行、EOF時の途中recordを拒否する。
一度のchunkは65,536 bytes以内、一回のdecode結果は最大16frameとし、失敗したdecoderを再利用しない。
byte断片は保持するがUTF-8をlossy decodeしない。完全なframeが揃った後にB1 JSON parserへ渡す。
SSH ownerは出力、channel EOF/close、exit status、実process/pipe/remote cleanupを別々に追跡する。

## 2. envelopeとcommand

event envelopeは次の7 fieldを**すべて**持つ。

| field | 規則 |
| --- | --- |
| `schema` | integer 1 |
| `protocol_revision` | `eidolon-linux-desktop-v3` |
| `context` | B1 Contextの10 field全体。capture済み値へ完全一致 |
| `scope` | B1の`context.scope()`へ一致 |
| `sequence` | positive signed-i64のdecimal string。1から連続 |
| `request_sequence` | `"0"`またはpositive signed-i64のdecimal string |
| `event` | `type`と`payload`だけのobject |

contextのnested fieldはenvelopeへflattenしない。
command envelopeはevent envelopeから`request_sequence`を除き、`event`を`command`へ変えた6 field。
storageの2 ID、profile binding、UID、nonce等はcontextに必ず含まれ、同scopeだけの比較へ省略しない。

command objectは常に`{"type":...,"payload":...}`の2 field。

| type | payload | 許可状態 |
| --- | --- | --- |
| `inspect` | 空object | Prepared |
| `start` | `selection`だけ。B1のscope/revision/app_id | Catalog |
| `heartbeat` | 空object | Prepared / Catalog / Started |
| `stop` | 空object | Prepared / Catalog / Started |

同時に一つのrequestだけを発行する。Startは自動再送しない。
v2 Selectionをv3へ変換せず、版が異なるSelectionはdispatchで拒否する。
同一leaseは一度のStartだけで、停止後の再起動には新しいcapture/lease/Inspectを必要とする。

## 3. event payload

| type | payloadの完全なfield集合 |
| --- | --- |
| `prepared` | `lease_ms=15000`, `boot_id`, `remote_socket` |
| `catalog` | `descriptor`, `evidence` |
| `started` | `selection`, `mode="gui"`, `process_identity` |
| `heartbeat` | `phase`。`prepared` / `catalog` / `started`の一つ |
| `stopped` | `reason`, `process_complete`, `pipes_eof`, `remote_cleaned`, `receipt_written` |
| `pending` | `reason`, `public_reason`, `process_complete`, `pipes_eof` |

`prepared.boot_id`はzero UUIDではないlowercase UUID。
socketは固定`<home>/.eidolon-desktop-v3/s-<capture nonce>/p`、103 bytes以内の絶対pathで、
空component、`.`、`..`、制御文字、Termux private prefixを拒否する。自由socketを要求する口は作らない。

`catalog.descriptor`は[B1の固定二項目](linux-real-app-b1-contract-20261110.md#3-固定catalog--selection--reason)を変更せずnestedにする。
順番、label、mode、icon、件数、scope/revisionの全体を一致させる。descriptorへ`available`を足さない。

evidenceは次の7 fieldに固定する。

| field | 照合対象 |
| --- | --- |
| `pins` | capture済みB1 schema 2 Pins全体 |
| `app_manifest` | immutable B1 Manifest全体。hashがpinsへ一致 |
| `source_hashes` | capture済み・コンパイル済みのexact source map。未知/欠落/改変を拒否 |
| `waypipe_version` | 固定`0.11.3`。hash照合を省略する条件ではない |
| `demo_verified` | C2による既存固定demoの検査成功true |
| `package_verified` | C2によるpackage/executable/schema/dependency検査成功true |
| `settings_backend_verified` | C2による固定module/probe/登録情報の検査成功true |

source mapのcodecは相対pathとlowerhex hashの構文だけを検査する。
実際に許すfile集合は[B2の単一inventory](../scripts/linux-desktop-v3-sources.json)と[compiled sources](../native/transport/src/linux_desktop/v3/sources.rs)が所有する。
検査結果は既存[inspector](../scripts/linux_real_app_inspect.py)と[manifest validator](../scripts/linux_real_app_manifest.py)へ委譲し、
Mousepadの版番号を単独の対応根拠にしない。

`started.selection`は送信したStartと完全一致する。`process_identity`は`pid`と`start_time_ticks`だけで、
どちらもpositive decimal string、pidはsigned-i32以内。
これはWaypipe等を含む**lease起動rootの観測**であり、エディタPID/native origin/窓/表示完了を意味しない。
保存PIDへの後日signal、origin mint、main採用はcodecの責務に含めない。

`stopped.reason`はB1 StopReason、4つのcleanup boolはすべてtrueが必須。
部分回収はStoppedへ丸めずPendingで返す。
`pending.reason`はB1 FailureReason、`public_reason`はNONE以外のB1 Reason、2つのboolは厳密bool。

## 4. ACKとterminalの順序

通常応答は現在のin-flight requestのsequenceへ厳密一致する。
Pending、および`lease_expired` / `input_eof` / `process_exited`のStoppedは非同期terminalであり、
helperが最後に処理したrequest番号を返す。その番号は**受理済みACK以上、送信済みrequest以下**である。
heartbeatやStopをenqueueした直後に、先にemit済みのterminalが届いても回収状態へ進める。
未送信requestのACKや、受理済みACKより前のterminalは拒否する。

Start ACKを失った場合でも、発行済みStartを処理した`process_exited`と全cleanup証拠が届けばStoppedへ進む。
Start未発行の`process_exited`は拒否する。
terminal後の遅着Started/Heartbeat、二重terminal、重複eventでStartedへ戻らない。
protocol違反後はFailedとしてdecoderを再選択せず、journalに不確実性を残す。

## 5. Recover receipt: B2とC2の境界

B2はcapture済み版/pins/source/context/journal/SSH引数を選択・照合する。
C2はremote owner/process/残骸を実際に検査・回収し、receiptを生成する。
同じprotocol/journal/bootstrapをC2側で別実装しない。

`dispatch::recover`は型付き`RecoveryRequest::V2/V3`と保存journalの版を照合し、該当する実装へ一度だけ進む。
異版journal、両版journalの同時存在、壊れた/読めないjournalはSSH I/O前に拒否する。
旧v2の回収処理はそのまま残し、v2のpendingをv3側でcleanにしない。

receiptはLFを一つ持つ単独record。次の15 field以外を拒否する。

```text
schema=1, protocol_revision, context, pins, build_id, source_hashes,
expected_boot_id, origin_boot_id, boot_id, status, process_evidence,
process_complete, pipes_eof, remote_cleaned, receipt_written
```

`context` / `pins`はそれぞれB1 objectの全体。
`build_id`はB1 Pinsの4-field式、`source_hashes`はcapture済み集合へ一致する。
`origin_boot_id`は必須nullable、`boot_id`は現在bootのnonzero UUID。
expectedが`-`以外ならoriginはexpectedと一致し、Prepared ACK喪失時の`-`でもC2が保存証跡からoriginを取り戻す。

| process_evidence | 許容証拠 |
| --- | --- |
| `receipt` | origin必須、process_complete/pipes_eof/receipt_writtenがtrue |
| `boot_changed` | origin必須、現在bootと異なり、process_complete=true、pipes_eof=false |
| `unknown` | 4 boolがすべてfalse。originはnullでもよいがcleanへ変換しない |

statusは`complete`とexit status 0、または`pending`とexit status 1の組だけ。
completeにはprocess_complete/remote_cleaned/receipt_written=trueが必要。
boot変更の証拠をpipe EOFの観測として偽装せず、journalのcleanup条件が別々に扱う。
旧helper不在、同boot unknown、source不一致はpendingを保持する。

immutable helper/manifestの検証と**現在のapp起動条件**を分離する。
Recover codecは現在packageの版/存在やapp実行を要求せず、package更新・削除後も正しい旧captureを照合できる。
実行中にapp、`--version`、settings probeを起動する処理はRecoverへ入れない。

## 6. 固定fixtureと引渡し

B1 fixtureのSHA256を新fixtureが参照し、B1をbyte-for-byte維持する。
新wire fixtureは通常testsから期待値を生成しない。Python/Rustの独立codecが同じraw/期待canonical/hashを読む。
拒否vectorはunknown/duplicate/extra/missing、位置array、enum object、不正型、誤版、ID交換、stale selection、
source/package/manifest差替え、偽cleanup/誤boot/誤exit statusを含む。
状態traceはterminalとheartbeat/Stop/Startの交差、ACK喪失、二重Start、遅着応答を含む。

C2の受渡しAPIはPython `Binding(Context,Pins)`、`ExpectedEvidence`、`parse_command`、`parse_event`、
`catalog_payload`、`parse_receipt`。helperはこのcodecを共有し、実I/O側のowner/lock/process証拠を別途要求する。
rootのB2統合で完成C1/C2 commitを固定し、全推移的import、bus template、settings source/binary/probe/登録情報、
固定demoをinventory/`include_bytes!`/manifestへ同時に揃え、結合CIを通して初めて製品pinsを確定する。
