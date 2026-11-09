# L7a E1: Android v3 storage identity・保存モデル

2026-11-10（日本時間）。E1はKotlinのstrict codec、capture、repository、noBackup実storeを追加する。
通常UI、`NativeSessionHost`、launcher、pins選択、v3 Inspect/Launch/Recoverへの接続はE2以降に残す。
**保存の一致はlocal stateの一致だけであり、`available`・通常v3 acceptance・Quest表示の証拠ではない。**

## 1. 開始基準と変更範囲

開始時mainは`a586f042583ac0869dc6f15e06cacae96db9bd7b`。
[B1 PR #7](https://github.com/tin-machine/eidolon-wayland/pull/7)は未マージで、現在headは
`80321efa9a508f2870fe8da3b5c869f2f15633a9`だった。関連PR、CI、作業指示を再確認し、
この現在headから独立checkout・`feat/android-v3-storage-e1`を作成した。
E1 PRのbaseはB1の`feat/linux-desktop-v3-contract`、状態はdraftとする。B1の無断マージは行わない。
開始時に追跡された`AGENTS.md`はなかった。過去headのCI成功をE1の結果に算入しない。

[B1共有契約](linux-real-app-b1-contract-20261110.md)と
[Android identity決定](linux-real-app-b1-android-identity-20261110.md)を確定仕様として実装する。
[古いhandoff](linux-real-app-b1-handoff-20261110.md)の「推奨案」へ意味を戻さない。
B1 Python/Rust・共通golden、固定v2の5 source、v2 pins/profile/journal/Recover、A0 gate・共有lifetimeは変更しない。

| 実装入口 | 責務 |
| --- | --- |
| [LinuxStorageIdentity.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxStorageIdentity.kt) | 名目型、乱数ID、profile capture、host/user hash、B1変更照合 |
| [LinuxStorageIdentityCodec.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxStorageIdentityCodec.kt) | strict JSON、canonical bytes、7-field record |
| [LinuxStorageRepository.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxStorageRepository.kt) | capture/CAS、保存状態、永続intent、明示resume、失敗時reload |
| [LinuxStorageFiles.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxStorageFiles.kt) | 共通直列化、所有検査、実file publish algorithm |
| [AndroidLinuxStorageFiles.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/AndroidLinuxStorageFiles.kt) | `android.system.Os`による実descriptor adapter |
| [LinuxStorageStore.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxStorageStore.kt) | `Context.noBackupFilesDir`を使うE2向け入口 |

既存保存utilityへの変更は`LinuxProfileStore.AtomicFile`の共通lockを`LinuxStorageIo.lock`へ揃える一箇所だけ。
既存credential保存・失敗・回収契約、profileのschema/codec、binding式、canonical host key、revision規則を維持する。
保存成功を理由に`available` / `production_acceptance_passed` / `quest_verified`をtrueにせず、
[通常v3 acceptance](../scripts/linux_real_app_check.py)の`APP_V3_NOT_IMPLEMENTED`も維持する。

## 2. identityと共通record

default credential identityとremote Linux credential identityの2系統は維持する。
`ConnectionProfileV2.installation`と`LinuxProfileStore`は引き続きdefault側`identity.installation`へ束縛する。
remote Linuxのclient認証鍵や新しいstorage IDを、既存installationの代わりに使わない。

`StorageInstallationId`と`ProfileStorageId`は別の名目型で、それぞれ
[SecureRandom.nextBytes](https://developer.android.com/reference/java/security/SecureRandom#nextBytes(byte[]))で16 bytesを生成し、
32桁lower hexへ符号化する。既存UUID、SSH鍵、serial、IP/port、表示名、revision、nonceから導出しない。
同host key/userならAPK更新・再起動・接続先address/port・表示名/revision変更で両IDを保持する。
host keyまたはusername変更時はstorage installationを保持してprofile storageだけを新規生成する。
client認証鍵の変更を接続先host key変更と解釈せず、旧remoteデータの削除・コピー・別IDでの自動採用は行わない。

Android storage schema 1は次の**7 fieldのみ**。C1のremote owner recordとは別である。

| field | 値 |
| --- | --- |
| `schema` | integer `1` |
| `legacy_installation` | 現在default installation、hex32 |
| `installation_id` | storage専用installation、hex32 |
| `profile_id` | `remote-linux`固定 |
| `profile_binding` | captureした既存v2 binding、hex64 |
| `profile_storage_id` | remote host key/user用storage ID、hex32 |
| `host_user_binding` | B1のhost key/user束縛、hex64 |

`StorageCapture.from(defaultInstallation, profile)`は既存Linux validatorを再利用し、
検査済みcanonical host keyのUTF-8 SHA256とusernameをB1の固定domainとともにcanonical JSON化してhashする。
profile bindingの既存NUL連結式と混同しない。record digestは7 field全体のcanonical bytesのSHA256。
raw bytesのCAS digestは別に保持し、書式だけの変更でもcapture/CAS不一致を検出する。

codecの上限は16384 bytes、depthはroot=0で32まで。未知/欠落/重複field、誤版、不正型、float、`-0`、
signed i64範囲外、BOM、不正UTF-8、孤立surrogate、余剰tokenを拒否する。
canonicalはrecursive key sort・compact separators・`ensure_ascii=true`相当・UTF-8・末尾改行なし。
補助面文字を含むkeyはUTF-16順ではなくUnicode code point順に並べる。JCSの実装とは呼ばない。

## 3. noBackup配置とE1専用metadata

[Context.getNoBackupFilesDir](https://developer.android.com/reference/android/content/Context#getNoBackupFilesDir())は
automatic backup対象外の領域を返す。E1は絶対pathをrecordへ保存せず、次の固定相対pathを使う。
noBackupであること自体を欠落検出やtransactionの保証にはしない。

| noBackupからの相対path | 用途 |
| --- | --- |
| `linux-profile/remote-linux.json` | 既存v2 profile本体。wire schemaは不変 |
| `linux-storage-v3-anchor.json` | storage directoryとは独立配置の登録証跡・保存状態 |
| `linux-storage-v3/transaction.json` | 新旧profile/recordを含む一つの固定intent |
| `linux-storage-v3/record.json` | B1の7-field Android storage record |
| `linux-storage-v3/commit.json` | intent・profile・recordのdigestを束縛するcommit証跡 |

以下はE1専用I/O metadataであり、共通recordやC1 remote owner schemaを拡張しない。
全metadataはstrict JSON、schema 1、現在default installation、固定kind、既知field集合、16KiB上限を検査する。

| metadata | 厳密なfield集合 |
| --- | --- |
| anchor（7 field） | `schema`, `kind`, `legacy_installation`, `generation`, `last_commit_sha256`, `pending`, `pending_transaction_sha256` |
| transaction（12 field） | `schema`, `kind`, `legacy_installation`, `generation`, `previous_anchor_sha256`, `previous_commit_sha256`, `previous_profile`, `previous_profile_sha256`, `previous_record`, `previous_record_sha256`, `next_profile`, `next_record` |
| commit（7 field） | `schema`, `kind`, `legacy_installation`, `generation`, `transaction_sha256`, `profile_sha256`, `record_sha256` |

kindはそれぞれ`eidolon-linux-storage-anchor-v1` / `eidolon-linux-storage-transaction-v1` /
`eidolon-linux-storage-commit-v1`。generationはcanonical decimal string、初回完了前のanchorだけ0を許す。
初回の旧record/旧commit、既存profileもない場合の旧profileと対応digestは明示null。
anchorは予約時`pending=true, pending_transaction_sha256=null`、準備完了時はexact intent digestを持ち、
最終確定時は新generation/commit digestと`pending=false, pending_transaction_sha256=null`を持つ。

E1のreadは所属directoryを作らない。既存`LinuxProfileStore.AtomicFile.read()`はdirectory作成を伴うため、
その副作用や`linux-profile`の存在を初回判定へ転用しない。
frameworkがnoBackup rootを用意することと、E1が登録証跡を作ることも区別する。
anchorもstorage directoryも存在せず、既存profileのread/decodeが成功した場合だけ`Uninitialized`となる。

## 4. publish順序と失敗時の状態

`save`は全回収barrierを保持し、同一process共通lock内で次の順序を実行する。

1. 現default installation、capture token、全profile field、新旧host/user、期待旧record digest、明示保存を再照合する。
2. **初回も更新も、ID生成より先に予約anchorをpublishする。** 初回は続いてstorage directoryを排他作成する。
3. 必要なIDだけを生成し、既存v2 revision規則とB1の保持/交代規則を検査する。
4. 旧fileのexact bytes digestを再確認し、`transaction.json`へintentをatomic publishする。
5. intent全体のhashを別配置のanchorへpublishし、**profile/recordを書き換える前にintentを確定する**。
6. profile、recordの順にpublishし、両file・intent・anchorを再照合する。
7. commitをpublishし、最後にanchorを新generation/commit digestへ更新する。
8. 全体を再loadし、新profileと新recordのcapture一致を確認して`Matched`を返す。

各fileのatomic renameだけで複数file全体のatomicityが成立したとは扱わない。
現generationの最後のtransactionは消さず、commitとanchorの照合に使う。

| reload状態 | 条件と許される処理 |
| --- | --- |
| `Uninitialized` | 登録証跡がない。明示保存と全回収後に初回生成できる |
| `Pending` | anchorに固定されたintentと一致し、各fileがそのintentの旧または新bytesである。v3利用は不可。明示resumeのみ |
| `Matched` | finalized anchor/commit/intent/profile/recordが一致し、現profile captureにも一致。local保存整合性のみ |
| `Blocked` | 予約だけ残った、intentのhashが未確定、file喪失/破損、別installation、未知bytes、I/O/decode失敗等。自動初期化・自動修復しない |

予約前の失敗ではIDが未生成なので元の未設定/確定状態をreloadできる。
予約後にintentを確定できなければ、既に生成したIDがある可能性を捨てず`Blocked`にする。
`Pending`のresumeは同じintentの同じIDを使うroll-forwardだけで、新しい乱数生成もrevision再増分も行わない。
確定済みrecordの消失は、初回未設定にも未完成intentにも読み替えない。

CAS競合、publish途中、readback、barrier callback前後の例外は`SaveFailure(reloaded)`を返す。
古いin-memory profile/recordへrollbackせず、現在のdiskを読み直す。
最終anchorのrename後に失敗してreload結果が`Matched`でも、今回の保存試行の失敗を成功へ置き換えない。
`currentState()`も再loadするため、別Store instanceの更新後に古いcached pairを返さない。

## 5. 実file保存の保証と限界

[Android adapter](../android/app/src/main/java/io/github/tinmachine/eidolon/AndroidLinuxStorageFiles.kt)は
[android.system.Os](https://developer.android.com/reference/android/system/Os)の実descriptorを使う。
自UID、child directory 0700、file 0600、regular file、nlink=1、サイズ、inode・時刻等を検査し、special mode bitsも拒否する。
noBackup rootは自UIDの0700、または自UID/自GIDの0771を許す。
0771は[AOSP ContextImpl](https://android.googlesource.com/platform/frameworks/base/+/26109ffc0f35/core/java/android/app/ContextImpl.java)の
`getNoBackupFilesDir` / `ensurePrivateDirExists`に合わせたframework rootだけの扱いで、E1 childの条件を緩めない。

file openは`O_NOFOLLOW`、readは`O_NONBLOCK`も付け、tempは`O_CREAT|O_EXCL|O_NOFOLLOW`で排他作成する。
`lstat/fstat`とread後再検査、file fsync、temp readback、expected bytesとdirectory identityの再照合、
同directory内rename、directory fsync、保存先readbackを行う。
中断したtempは自動採用せず、失敗時に消すのは今回作った同inodeのtempだけ。
根拠は[open(2)](https://man7.org/linux/man-pages/man2/open.2.html)、
[rename(2)](https://man7.org/linux/man-pages/man2/rename.2.html)、
[fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html)。file fsyncだけでdirectory entryの永続化を代用しない。

直列化の範囲は**同一processのStore instanceと既存profile/pins writer**。
OS process間CAS・排他、別processによるdirectory namespaceの敵対的置換への完全な防御は実装していない。
Context由来のnoBackup pathとapp-private領域を前提とし、複数process化には別の所有設計が必要である。
全証跡を同時に消した状態やapp data全消去と、過去に登録されていない状態はfile metadataだけでは区別できない。
外部の単調counterや署名を持たず、全fileの整合した過去snapshotへの巻戻しに対するanti-replay保証もない。
通常のfile単独喪失・部分publish保護を、その保証へ拡張して説明しない。

## 6. E2が接続すべきAPIと全回収証拠

`LinuxStorageStore(context, currentDefaultInstallation)`を、E2の保存ownerからworker上で使用する。
`load`で得たcaptureと、既存`LinuxConnectionProfile.propose`で作るnext profileを使う。
`SaveRequest`にはcurrent default installation、capture、capture済み旧profile、next profile、
旧/new host-user binding、期待旧record digest、明示保存をすべて渡す。初回の旧値nullも省略しない。
`ResumeRequest`にはPendingCapture、旧/新profile、新旧host-user binding、旧record digest、
expected transaction digest、現default installationと新しい明示保存が必要となる。

`ReclamationBarrier.withReclaimed(captureToken, action)`の通常実装はE2の責務であり、E1に成功を返す既定実装はない。
E2は新規接続・Launch・設定変更のadmissionを閉じ、実runtimeの停止とdestroyを完了してから、
保存callbackが終わるまでそのbarrierを保持する。`ReclamationEvidence`は同capture tokenに束縛し、次を要求する。

- `stoppedRuntime > 0`、`shutdownComplete`、`nativeDestroyAccepted`。
- owned connections、pending workers、native targets、panels、SDK bindings、Board windows、pending operationsがすべて0。
- remote cleanup pendingがfalse。journal/Recoverを無視してこの値を固定しない。

SDK/nativeの数には診断用所有も含める。接続開始時の「診断panelを許すgate」を設定保存の全回収証拠へ流用しない。
testの合成evidenceはguardの拒否/通過条件を検査する入力であり、実機回収の証明ではない。
通常callerが存在しない段階で`fully_reclaimed=true`等の仮実装を接続しない。
失敗後は`SaveFailure.reloaded`を受け取り、profile/recordを組として再評価する。
`Pending`や`Blocked`をavailableへ変換せず、`Matched`でも別途v3 source/pins、接続、通常acceptanceの成立が必要である。

B2はbundle/source pinning/protocol/transport、C1/C2はremote owner/profile/helperを所有する。
E1のrecordや保存成功で、B2/Cの未完成機能を成功扱いにしない。共通契約修正が必要ならB2統合担当へ一本化する。

## 7. 検査対象と検証結果

[Kotlin identity検査](../android/app/src/test/java/io/github/tinmachine/eidolon/LinuxStorageIdentityTest.kt)は
[B1共通fixture](../scripts/tests/fixtures/linux-desktop-v3-contract.json)を直接read-only resourceとして読む。
対象は6 canonical/hash、69 raw拒否、4 capture関係拒否、11 storage変更vectorで、似たfixtureを再生成しない。
[repository検査](../android/app/src/test/java/io/github/tinmachine/eidolon/LinuxStorageRepositoryTest.kt)は
初回/保持/host交代、別installation、同時Store/CAS、全barrier項目、予約/intent/全publish境界、
readback失敗、部分publish/resume、単独file喪失/破損、ID差替えを検査する。

[filesystem検査](../android/app/src/test/java/io/github/tinmachine/eidolon/LinuxStorageFilesTest.kt)と
[host fixture](../android/app/src/test/java/io/github/tinmachine/eidolon/HostLinuxStorageFixture.kt)は、
同じ保存algorithmをtest-only JNI adapterからLinuxの実descriptor/syscallと実temporary directoryへ接続する。
Linux/JDK headers/host C compilerがなければskipで成功にせず、実行条件不成立として失敗する。
UID差替え等の一部拒否入力にはfault adapterを併用するが、mock成功だけを実保存の証拠にはしない。
**host POSIX検査とAndroid実adapterの端末instrumentationは別であり、Questの実保存・再起動・APK更新・実回収は未確認。**

以下はE1変更treeのローカル事前検査。最終headのCIはPR本文にSHAとrun/job URLを束縛して記録し、
この事前結果を別headやAndroid端末の検査へ読み替えない。

| 対象 | 実行結果・記録場所 |
| --- | --- |
| Kotlin 2.4.21 / JUnit 4.13.2、host実filesystem | 63件中62成功・1 assumption skip・失敗0。identity11、filesystem14、repository18、既存profile20。skipはrootでは成立しない実EACCES検査 |
| B1 Python/Rust | 新規実行でPython15成功・skip0、Rust8成功・ignored0（114 filtered）。共通fixtureは不変 |
| check-design / 固定契約 | 31 tasks / 45 checks成功。v2五source・golden、B1 golden、共有lifetime、既存transport/credentials/profile/NativeSessionHostがbyte-for-byte不変 |
| host/Android CI | 最終PR headの結果をPR本文へ記録。JUnit XMLの全体/E1別集計とartifactを追加し、task成功と実件数を区別する |
| 専用A0 CI | 最終PR headの実process異常・復元保護・全worker回収・125秒後fresh応答をPR本文へ記録 |
| Android instrumentation・Quest・通常v3 acceptance | 未実施/未実装。local保存やA0成功から補完しない |

ローカルはUID0だけをmapしたuser namespaceで、UID65534への切替はJava起動前に`EINVAL`となった。
非root検査を実行したとは数えず、実EACCESは非rootのAndroid CIで確認する。
最終PR本文はhead SHA・run/job URL・実行件数・skip理由とともに確定する。
