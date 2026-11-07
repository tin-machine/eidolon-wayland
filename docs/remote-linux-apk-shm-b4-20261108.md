# NX18 B4: APKからLinux固定SHM検査への接続

[B3aの固定Linux helper](remote-linux-helper-b3a-20261108.md)と[B3bのtransport owner](remote-linux-transport-b3b-20261108.md)を、debug APKの独立runtimeと既存Pattern AppSessionへ接続する。通常Termux設定や既存GUI起動のbackendは切り替えない。Linux接続のcapabilityは検査後も `ProbeReady` で、catalog、foot、任意GUIは引き続き拒否する。

このcheckpointは配線とhost検証まで。APK UIDでのSSH→Linux helper→実SHM受信、SDK/GPU表示、物理HMDの受入れはそれぞれ別gateであり、本記録だけでは合格にしない。

## 固定入力と実装境界

- `RuntimeConfig.debug_linux_shm_pins` はB2の `debug_probe_profile_v2` と同時指定するprivate入力。型・4096bytes上限・正確なhash形式をruntime生成前に検査する。
- `LinuxShmProbe` / `LinuxShmRecover` commandはschema、request ID、session、16桁hex nonceだけを受ける。host、path、argv、JSON本文をADB intentから受けない。
- coreの `debug-fixtures` featureをtransportへ伝播する。通常releaseは新configとcommandを拒否する。`debug_assertions` の有無で境界を推測しない。
- Androidのcase、pin store、pin型はdebug sourceSetのみ。新しいJUnitもtestDebugへ置き、release testからdebug型を参照しない。
- nativeの同じControllerがcaptureしたv2 binding/revision、session、認証後UID、同じSSH handle/Routerを使う。別接続の値を再利用しない。Linux検査中も通常app APIの `Ready` 条件は維持する。
- private owner用pathは `noBackupFilesDir/lx` に固定する。Unix socketの103bytes制限のため短い名前を使う。既存Termux files directory、preferences directory、journalを渡さない。
- compiled helper本文は `scripts/native_build.py` のsource digest対象へ追加した。配備するWaypipe/fixture実行ファイルは別bundle manifestとpinsで同定する。

## 準備

operatorが接続先とpinを確認した後に、B2の正規debug profile保存APIへ渡す固定staging fileを配置する。通常v1 profileの自動移行や書換えはしない。

1. Linux側はaccount HOME配下の固定private bundleを使う。`$HOME/.local/lib/eidolon-linux/` は0700、`bundle.json` とhelperは0600、固定Waypipe/SHM fixtureはsame UID・非group/world writableの実行可能なregular fileにする。bundleの詳細はB3aを参照する。root所有 `/usr/bin/python3` とユーザー所有bundleを区別する。既存HOME環境やsystem sshd設定を書き換える手順ではない。
2. APK側はB2と同じ `noBackupFilesDir/remote-probe/` のprofile stagingを使う。profile IDは `linux-probe`、backendは `linux_ssh`。既存インストールidentityを維持し、SSH host keyとusername/endpointを含むv2 binding/revisionを確認する。
3. 同directoryへ `linux-shm-pins.pending.json` を0600で配置する。directoryは0700/same UID、fileはnofollow/regular/link数1。最大4096bytesのstrict JSONで、必要fieldは次の6個だけ。

   | field | 型・意味 |
   | --- | --- |
   | `schema` | 整数1 |
   | `profile_binding` | 確認済みv2 profileのbinding、lowercase SHA-256文字列 |
   | `profile_revision` | 同profileの非ゼロu64を十進文字列で保持 |
   | `bundle_sha256` | 配備したbundle.jsonそのもののSHA-256 |
   | `waypipe_sha256` | 配備したWaypipe 0.11.3のSHA-256 |
   | `fixture_sha256` | 配備した固定SHM fixtureのSHA-256 |

4. case開始時にprofileとpinをstrict validateし、pinをexclusive tmp→write/fsync→読戻し→rename→directory fsync→読戻しで `linux-shm-pins.json` へ保存する。重複key、型のcoercion、未知field、UTF-8不正、別binding/revision、stale tmpを拒否する。pinを診断ログや `toString()` に出さない。

stagingはoperatorが確認した値を事前配置する経路であり、検査中にremote自己申告hashを信頼してpinを更新しない。fixture bundleを更新した場合も明示した新pinsが必要になる。

## 起動・判定・回収

既存DUMP権限の `LinuxSshProbeReceiver` に `linux-shm-probe`、`linux-shm-recover`、`linux-shm-probe-stop` を追加した。intent extrasはcaseと32桁hex nonceだけ。受信側は一件だけ実行し、各operation/nonceの直近64件を再実行しない。

外部nonceの先頭16桁 `nonce.take(16)` をhelper nonceとして使う。結果は外部32桁、native/remoteの照合は同じ先頭16桁に束縛する。recoveryでは失敗したprobeと同じ外部nonce/profile/pinsを指定する。先頭16桁を再利用した新検査はLinux側のreceipt/nonce所有検査で拒否し、既存receiptを上書きしない。

通常runtimeを停止した単一worker上で以下を行う。credential供給を同workerへ再enqueueして待つことはしない。

1. 独立runtimeを作り、request 1で保存v2 profileへ接続する。`CredentialRequired` のruntime/request/session/profile binding/revisionを照合して鍵を供給する。認証後の固定id/token probe、EOF/exit 0と `ProbeReady` を確認する。
2. request 2で固定patternを開始する。まずローカルWaypipe client/compositorを用意し、そのsocketに同じSSH Routerを束縛する。検査はvirtual frame clockだけを使い、SDK surfaceやwindow boardの通常枠を消費しない。
3. transportの `pass` だけでは成功にしない。同session/nonce/UIDのtools確認、登録ACK、固定100frame、remote child/group/pipe回収、forward取消、SSH channel終了、Linux journal消失を要求する。
4. 同session/nonceのAppSessionで `mode=pattern`、`callback_clock=virtual`、実受信 `verified_hashes=100`、copies/callbacks/releases各100を要求する。Waypipeの初期照会と実appの二接続も観測する。helperの自己申告counterを画素検証の代用にしない。
5. request 3で切断する。ACKとDisconnected/owned_connections=0、local AppSessionのstopped/directory回収、client/object/surface/pixel/pool/memfd所有ゼロを確認する。private credential scopeが消え、Stop/destroy後registryのruntime/workerがゼロになることも必要とする。
6. 通常Termux profileと旧journalのdigest、通常runtime configが不変で、別handleの通常runtimeがReady/Idleへ戻ったときだけ最終 `pass` にする。

全体deadlineは90秒、native ownerの停止回収には別の10秒上限を設ける。取消・timeout・BUSY・誤pinを自動再送しない。native ownerの回収を確認できなければhandleを保持して `cleanup_unconfirmed` とし、通常runtimeを復帰済み扱いにしない。同nonceの明示stopだけがnative owner回収を再試行する。

remoteのjournalが残ったこととlocal native ownerが残ったことは別に扱う。localを回収できてもremote回収を証明できなければ検査はfailで、Linux journalを保持する。通常Termuxへ復帰できる場合もLinux検査の成功にはしない。次のLinux startはpendingを拒否し、明示recoveryが必要となる。

## 明示recoveryの結果

`linux-shm-recover` は通常patternや新しいGUIを起動しない。同profile binding/revision/UID/pins/nonceに属するLinux journalだけを照合する。別scopeのjournalや旧Termux journalを検索・削除しない。

- `queried=true && receipt_verified=true && channel_closed=true && pending=false` とSSH切断/native回収/通常復帰を全て満たす場合は `pass`。remoteの回収receiptを確認した結果として記録する。
- journalが元から無く、query/receipt/channelの各boolが全てfalseの場合は `not_required`。`remote_cleanup_confirmed=false` のままとし、実際のremote回収を検証したPASSとは分ける。
- boolの矛盾、receipt欠落、別scope、active child、期限切れはfail/pending。保存PIDへsignalするfallbackや自動journal削除は行わない。

## checkpoint検証と残るgate

2026-11-08のhost検証:

- model固定command wire 1件、Controller mailbox/capability 1件、実featureなしcoreでのconfig/command拒否1件PASS。
- core debug-feature all-targets check/clippy、Kotlin API36に対する限定compile PASS。
- Kotlin pure 15件PASS（うちtestDebugのpins/recovery分類4件）。source identity 4件PASS。
- core既存回帰63件PASS、既存ignore 1件。隔離worktreeにfixtureが見つからない初回1件は、target複製をせず共有cacheへのsymlinkで実行前提を整えて再検査した。検査binary内のtest名と件数まで同じCargo owner lock内で確認した。

APK build/lint/package、実APK UIDからLinux account HOMEのbundleへ接続しての100画素hash/回収、cancel/receipt recovery、通常Termux復帰は次の実機gate。SDK表示と物理HMDはさらに別gateで、今回 `gui_enabled=false` / `gpu_verified=false` / `physical_hmd=false` を維持する。
