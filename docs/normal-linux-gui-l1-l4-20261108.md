# NX18 L1〜L4: 通常Linuxのlease・SSH actor・journal・復旧

2026-11-08。C2 checkpoint `43767a4` をmainへ取り込むPRで、通常GTK一種のtransport/helper経路を接続する。
対象は [通常GUI計画](nx11-nx18-normal-gui-followup-plan-20261108.md) のL1〜L4のコードとhost検証。
実Linux/Questへの配備、Host/SDK/UIの採用、描画・入力の受入れはこの変更では実施していない。

## 変更と入口

| 項目 | 実装 |
|---|---|
| L1 | C2の固定GTK demo・adapter・builderを取り込み、非同期終了eventと要求ACKの相関を修正。通常用wire sourceをbundleとAPK入力digestへ追加 |
| L2 | 固定bootstrapから `serve` / `recover` を実行。inspect/start/heartbeat/stop、更新可能な15秒lease、専用owner/lease/receipt、同ownerでの回収 |
| L3 | `linux_desktop::run` が認証済みhandleとそのStreamLocal routerへ接続。bounded actor、要求期限、起動の自動再送禁止、停止・取消・自然終了 |
| L4 | 通常専用のdurable journal、boot identity、独立した回収証拠、`linux_desktop::recover` による明示照会と復旧 |

`linux_desktop` はdebug featureに依存しないtransport API。
呼出側は同一SSH接続で検証したprofile/session/remote UIDから `Binding::capture` を作り、
その接続のhandle/router、private files directory、local Waypipe socketを `run` に渡す。
この型の生成だけではSSH認証や実行許可の証明にならない。Host/Controllerからの選択と呼出しはL5/L6で接続する。

通常固定appは `gtk3-desktop` 一項目。catalog・source・bundle・Waypipe 0.11.3の照合後にだけstartする。
任意argv、remote foot、Qt、既存desktopへの接続、同時複数instanceは追加しない。

## C2の非同期終了event競合

C2保存版ではcommand生成時に進めたrequest sequenceと全eventを同値比較していた。
Started ACKが要求2、Questがheartbeat 3を生成した直後、helperのprocess exitが要求2で届くと、
正常な終了でもscopeエラーになった。

現行protocolは次を別々に保持する。

- event sequenceは同じbindingで厳密に連続し、replay・逆順を拒否する。
- 通常ACKは、一つだけのin-flight要求の種類と最新request sequenceに完全一致させる。
- 非同期 `process_exited` / `lease_expired` / `input_eof` / `pending` は、最後にACKを確認した要求から生成済み要求までの因果範囲を許す。
- 明示StopのACKは実際のStop要求を必要とする。非同期terminalが未応答要求を退役させても、その要求の成功ACKを得たとは扱わない。
- terminal受理後のACK、二重terminal、別session/profile revision/scope/nonce/UIDは拒否する。

StartとStoppedが同一SSH data chunkに入る場合も、Started ACKを観測した事実はreportへ残る。
commandを生成・送信した後のtimeout/切断を「未実行」と判断せず、Startを再送しない。

## 寿命・出力・停止

| 制御 | 通常経路の値・条件 |
|---|---|
| Prepared待ち | 10秒 |
| 通常要求ACK | 5秒、single in-flight |
| heartbeat | 5秒間隔 |
| remote lease | 15秒。正常なheartbeatとsetup要求で更新 |
| Stop ACK / 終端待ち | 8秒 |
| terminal後のchannel終了待ち | 3秒 |
| runtimeの例外時停止処理 | 最大14秒、別途EOF/close/forward取消を試行 |
| アプリ総稼働時間 | 短い固定上限を設けない |
| control frame | 16KiB上限、有限の入出力buffer |

子stdout/stderrは別pipeから小さい単位で継続drainし、本文を通常diagnosticsへ保存しない。
通常serveはadapterの累積64KiB制限を適用せず、少量のログが積算しただけでアプリを終了させない。
受信control frameをアプリログとしてdiscardする経路は設けない。
これは長時間の性能受入れを済ませた意味ではなく、実測はL7bで行う。

自然終了、Stop、control EOF、lease切れは同じlive pidfd ownerで子孫を回収する。
回収pendingの場合、新しい起動は拒否し、元helperがlive pidfdを保持して再試行する。
helper自体が失われた後に、保存した数値PIDを使って別processへsignalしない。

## 起動順とdurable journal

通常Quest側は `files/linux-desktop-state/pending.json` と専用lockを使う。
Termux/B4/B6のjournalを照合・削除・昇格しない。

1. helper exec直前に、profile binding/revision、origin session、UID、nonce、全pins/source hashを予約してfsyncする。
2. remote helperは `.eidolon-desktop/s-<nonce>/owner.json` に同scopeとboot IDを保存する。
3. QuestはPreparedのboot IDをjournalへfsyncしてからinspectを要求する。アプリ起動前に両側の所有記録がある。
4. StreamLocal登録要求とStart要求は、それぞれ送信前にstageを保存する。再開時にstageを見てStartを再実行しない。
5. 終了時はprocess、pipe EOF、remote cleanup、receipt、channel、forward取消、router drainを別々に確認する。
6. 全証拠が揃ったときだけCompleteをfsyncしてjournalを削除し、directoryもfsyncする。

private mode、nofollow、single link、サイズ、schema、元fileのinode/内容を検査する。
初回予約は `renameat2(RENAME_NOREPLACE)` を使い、既存pendingやdangling symlinkを置換しない。
更新・復旧は専用file lockの所有中だけ行う。対応しないfilesystemで通常renameへfallbackしない。

remote receiptはprocess/EOFの証明を先にfsyncし、socket/directoryの検証・削除後にremote_cleanedを確定する。
最後のowner fileをunlinkした直後にhelperが落ちても、同scopeの成功receiptと空directoryから安全に後始末できる。
不明なfile、symlink、別ownerを含むdirectoryは保存する。

## helper喪失・host再起動からの明示復旧

`linux_desktop::recover` は同じprofile binding/revision/UID/pins/sourceに戻った認証済み接続を使う。
呼出側I/O ownerが前のconnectionとforward taskを終了・joinした証拠を渡し、新routerのactive=0を確認する。
この証拠をUIのboolや保存journalのflagから作らない。profile/source変更で古いownerを別identityへ付け替えない。

| 状態 | 判定と動作 |
|---|---|
| 同boot、元helperが回収済み | private成功receiptを照合。必要な残骸cleanupを完了してからjournalをclear |
| 同boot、helper不明/喪失、成功receiptなし | Unknown/pendingを維持。保存PIDの不在だけで成功にしない |
| remote bootが変化 | 完全一致する旧bootのprivate owner/receiptを先に検証。旧processの不存続と残骸cleanupを別証拠として確認 |
| Prepared ACK喪失 | local boot未保存を固定sentinel `-` で照会。同scope/pinsのprivate recordでorigin bootが得られた場合だけ採用 |
| boot不明かつowner/receiptもない | pendingを維持。missingをcleanに変換しない |
| profile/UID/pins/source変更 | scope mismatchで保存。新しい起動を拒否 |

boot変更時に実際のpipe EOFを観測したとは報告しない。
`process_evidence=boot_changed`、`pipes_eof=false` とし、旧kernelのprocess/pipeの消失証明と、
検証済みremote cleanup/receipt、現在のchannel終了を組み合わせて完了を判断する。

same-bootでownerが失われ、完了証跡がない状態は自動解消しない。
まず元helperがまだ回収を続けていないかを運用側で確認し、同じprofile/pins/sourceで明示recoverを行う。
成功証跡を取得できなければ、そのownerをUnknownのまま保持する。
別途承認されたhost再起動後は、上記のboot変更経路で旧ownerと残骸を検証できる。
単にjournalを削除する、全UIDのprocessをkillする、数値PIDからpidfdを再構成する操作を復旧手順に含めない。

現行remote stateにはrootの80 entries / session directory 8個の上限がある。
成功receiptはQuest側journalの削除後も保持し、自動GCしない。上限到達は新規起動拒否となる。
receipt削除には未回収journalがないことを確認できる別の受領/保管契約が必要で、今後の運用項目とする。

## bundleと配備

protocol revisionは `eidolon-linux-desktop-v2`。通常manifestはadapter、wire loop、pidfd owner、
demo C、demo binaryの5fileを束縛する。base helper/Waypipe/SHMのmanifestは別pinsのまま検証する。
`scripts/build-linux-desktop-bundle` は既存baseへ明示追加するbuilderで、既存fileは上書きしない。
旧C2 v1 bundleをv2として扱わず、実配備とpins更新は対象hostの確認後に別途行う。

`scripts/native_build.py` はadapter/wire/owner/demo Cをsource digestへ含める。
既存B4/B6 helper、実機証跡、Termux経路の動作契約は変更していない。

## 検証と残る受入れ

Rust 1.95.0でmodel+transportの隔離workspaceを作り、元Cargo.lockと同じregistry package版/checksumで検証した。
新規36 testsのうち28件をローカル実行し、actual russh TCP actorの3件を含め成功。
既存の実行可能な回帰も含め77 passed / 25 filtered。除外はOpenSSH/Unix socketを必要とする既存17件・新規8件で、
この環境のAF_UNIX bind制限とOpenSSH接続制限による。除外したtestを成功として数えない。

新規Python wire/state/lease suiteは、root環境でも実行可能な22件を成功確認。
非root専用の実process/setsid/EOF/lease/log継続5件はCI対象。root禁止を解除して実行しない。
実Linuxの非root環境では既存 `scripts/check-host --suite all` が追加testsを含めて実行する。
PRのGitHub Actionsでhost全検査とAndroid buildの結果を記録する。

初回commit `5983cf4` の [CI run 42437779626](https://github.com/tin-machine/eidolon-wayland/actions/runs/42437779626) では、
host全検査が成功した。transportは102件（通常Linuxの36件を含む）すべて成功し、
通常Python sessionも非root実process5件を含む27件すべて成功。GTK builder、clipboard/IME回帰も通過した。
Android APK・lint・ELF/版/同梱検査も成功したが、後続のPython全502件で、既存Linux probe fixtureが
作成直後の空のready fileを読み `int('')` に失敗した。この待ち合わせはfixture内のtemp write→atomic renameへ修正する。
通常helperのproduction codeはこの修正で変更しない。修正後headのCI結果はPRに記録する。

L5/L6のHost/SDK/UI配線、実機GUI・Dialog・入力、対象Ubuntuの事前確認と配備、実用アプリL7a、
長時間・障害・性能のL7bは未受入れ。認証成功、catalogやreceiptの検査だけでGUIReadyにはしない。
