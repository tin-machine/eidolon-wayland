# L7a B1: Android identityとv3 storage recordの確定

2026-11-10（日本時間）。調査基準はmain
[`a586f042583ac0869dc6f15e06cacae96db9bd7b`](https://github.com/tin-machine/eidolon-wayland/commit/a586f042583ac0869dc6f15e06cacae96db9bd7b)。
本書は[B1引き継ぎ](linux-real-app-b1-handoff-20261110.md)のstorage identityに関する決定を記録する。
B1で完成させるのは純粋な型・parse・照合・変更判定と固定vectorである。
Androidでの乱数生成、file作成、保存、migration、通常UIへの接続はE1以降で実装する。

## 採用する対応

**v3 storage専用の `installation_id` を新設する。既存2系統のcredential identityと、
`ConnectionProfileV2.installation` は変更しない。**
同じ32桁lower hexでも、legacy installation、storage installation、profile storageは別の意味を持つ型として扱う。
storage IDをSSH鍵、既存UUID、端末serial、接続profile bindingから導出しない。

現コードの[初期化](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)と
[CredentialStore](../android/app/src/main/java/io/github/tinmachine/eidolon/CredentialStore.kt)の対応は次のとおり。

| 対象 | 保持先 / alias | 意味とB1以降の扱い |
| --- | --- | --- |
| default credential identity | `noBackupFilesDir/credentials/identity.json`、`public.json` / `eidolon.identity.aes.v1` | `NativeSessionHost.identity`。既存アプリinstallationの所有照合に使う |
| remote Linux credential identity | `noBackupFilesDir/credentials-remote-linux/identity.json`、`public.json` / `eidolon.identity.remote-linux.aes.v1` | `NativeSessionHost.linuxIdentity`。通常Linuxのclient認証鍵。storage namespaceに転用しない |
| 通常LinuxのConnectionProfileV2 | `noBackupFilesDir/linux-profile/remote-linux.json` | `LinuxProfileStore(application, identity.installation)` でdefault側に束縛される。remote Linux credential identityへ付け替えない |
| v3 storage installation | E1が追加するnoBackup内のversion付きstorage record | 新設する16 random bytesの32桁lower hex。wire field名は `installation_id` |
| v3 profile storage | 同じstorage record | 新設する16 random bytesの32桁lower hex。wire field名は `profile_storage_id` |

既存UUIDが不適切だから交換するのではない。
現在の `CredentialStore` は `UUID.randomUUID()` 由来であり、UUIDの固定bitを含む。
その値を再利用して「16 random bytesのstorage IDを新規生成・保存した」とは記録しない。
分離することで、既存profile、credential、pins、journal、Recoverのidentityを維持したまま、
storage namespaceの生成・保持・交代を独立に実装できる。

Android公式の[Auto Backup仕様](https://developer.android.com/identity/data/autobackup)は
`getNoBackupFilesDir()` 配下をbackup対象外としている。
[Context API](https://developer.android.com/reference/android/content/Context#getNoBackupFilesDir())もその用途を定義する。
E1はこの領域へ保存し、APK更新時は保持し、再installでは新しいnamespaceを作る。
noBackup配置だけでCASや欠落検出が成立するとは扱わない。

## 既存ConnectionProfileV2の契約

[ConnectionProfileV2](../android/app/src/main/java/io/github/tinmachine/eidolon/ConnectionProfileV2.kt)のwire schemaとbinding式は不変とする。
現在のbindingは次の順序の文字列をNULで連結したUTF-8 bytesのSHA256である。
末尾NULは付けない。

```text
[
  "eidolon-profile-v2", installation, profile_id, revision,
  backend, host, decimal(port), username, canonical_host_key
]
```

`display_name` は直接のhash入力ではないが、
[LinuxProfileRepository](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxConnectionProfile.kt)は
表示名だけの保存でもrevisionを進めるので、bindingは変化する。
このrevision付きbindingをremote永続directoryのdata keyにはしない。
v3 context/scope/journalでは、captureしたbindingと2つのstorage IDsをそれぞれ束縛する。

host keyは既存の
[ConnectionProfile.canonicalKey](../android/app/src/main/java/io/github/tinmachine/eidolon/ConnectionProfile.kt)で
検査した `ssh-ed25519 <canonical-base64>` を使う。
元入力のコメント、前後空白、base64表記の差を別host identityとして扱わない。
usernameも既存ConnectionProfileV2のLinux用validatorで検査した値を使う。
storage側で別の緩いSSH key parserやuser正規化を追加しない。

## Android storage record schema 1

recordは次の**7 fieldだけ**を持つ。
既存ConnectionProfileV2へfieldを追加せず、version付き別recordに置く。
parserは未知・欠落・重複field、余剰field、誤版、不正型、桁数違い、大文字hexを拒否する。
`schema=true` や `schema=1.0` をinteger 1へ丸めない。

| field | 型・固定値 | 照合する意味 |
| --- | --- | --- |
| `schema` | integer `1` | 本storage recordの版。pins/wire/manifestのschemaと区別 |
| `legacy_installation` | 32桁lower hex | 現在のdefault `identity.installation`。recordの所属installation |
| `installation_id` | 32桁lower hex | storage専用installation。既存credential identityを置換しない |
| `profile_id` | 固定文字列 `remote-linux` | 通常Linux profileのみ。Termux/debug profileを採用しない |
| `profile_binding` | 64桁lower hex | 保存時にcaptureした既存ConnectionProfileV2.binding |
| `profile_storage_id` | 32桁lower hex | 当該remote host identity/userに対応する永続profile ID |
| `host_user_binding` | 64桁lower hex | 以下の固定式で束縛したcanonical host keyとusername |

load/captureでは、record自身の自己申告だけで所有を認めない。
呼出側がcaptureしたdefault installation、通常Linux profileのID/binding、host/userとの一致を要求する。
別installationからコピーしたrecord、旧bindingのrecord、別host/userのrecordを自動採用しない。
SHA256照合は公開情報の相関であり、recordに認証済みstorage所有権を新たに与える署名ではない。

### host_user_bindingの固定式

`C(x)` は、keyをsortし、compact separatorsを使い、文字列をASCII escapeしてから
UTF-8へencodeしたcanonical JSON bytesとする。末尾改行は付けない。
これはB1共通moduleのcanonical bytes規則であり、既存profile bindingのNUL連結式とは区別する。

```text
host_key_sha256 = lowercase_hex(SHA256(UTF8(existing_canonical_ssh_ed25519_host_key)))

host_user_binding = lowercase_hex(SHA256(C({
  "domain": "eidolon-linux-storage-host-user-v1",
  "host_key_sha256": host_key_sha256,
  "username": existing_validated_username
})))
```

host key文字列のhashにも改行は付けない。
domain fieldと3つのkeyを固定し、IP/port/display name/revision/session nonce/client認証鍵を含めない。
`host_user_binding` 自身を入力へ戻さず、自己参照を作らない。
recordのCAS用digestは、上記7 fieldの検証済みrecord全体を `C(record)` としたSHA256である。
digestはrecordの8番目のfieldとして埋め込まない。

## 継続・交代の純粋な変更判定

B1の変更判定は、旧record、候補record、期待する旧record digest、新しいprofile capture、
`explicit_save`、`fully_reclaimed` を受け取る。
両flagはstrict booleanとし、true以外では保存候補を受理しない。
ここでtrueを渡せることは実機の回収証明ではない。E1は既存の全回収gateからその条件を得る必要がある。

| 変更 | storage installation | profile storage | 必須の照合 |
| --- | --- | --- | --- |
| 同host key/userでAPK更新・再起動 | 保持 | 保持 | recordと現installation/profile captureが一致 |
| 同host key/userでIP/port/表示名/revision変更 | 保持 | 保持 | 新profile bindingへ更新。旧record digestの一致、明示保存、全回収 |
| host keyまたはusername変更 | 保持 | 新しいID | 新host_user_binding、新profile binding。旧profile_storage_idの流用を拒否 |
| remote Linuxのclient認証鍵のみ変更 | この理由だけで交代しない | この理由だけで交代しない | 認証鍵と接続先host keyを混同しない。既存のcredential/pending制約は維持 |
| legacy installationが異なるrecordの読込み | 採用しない | 採用しない | コピーされたrecordとして拒否し、自動移行しない |
| 欠落・破損・部分publish・readback失敗 | 自動再生成しない | 自動再生成しない | 状態不明を新規未設定へ変換しない |

継続/交代とも、既存recordからの更新では `legacy_installation` と `installation_id` を保持する。
同host/userなのにprofile_storage_idを差し替える入力、host/userを変えて同IDを流用する入力、
stale expected digest、candidateと新profile captureの不一致を拒否する。
ID変更は旧remoteデータを削除・コピー・新しい名前で採用する操作ではない。

初回追加と欠落は分ける。
旧record不在を初回追加として扱えるのは、E1の所有する保存状態で初回と確認でき、全回収後の明示保存がある場合だけである。
decode失敗、I/O失敗、以前publishしたrecordの消失を `None` に読み替えて初回生成しない。
B1はIDを生成せず、randomnessや保存の成功をfixtureから認定しない。

## 現行保存実装とE1へ渡す境界

### CredentialStoreの実際の保証

[CredentialStore.atomicWrite](../android/app/src/main/java/io/github/tinmachine/eidolon/CredentialStore.kt)は
固定名のtempへ書き、file descriptorをsyncして、`ATOMIC_MOVE`で置き換える。
`@Synchronized` はobjectごとであり、expected bytesのCAS、directory fsync、nofollow/inode照合、
別Store instanceに共通のlockはない。
秘密保存が消えてpublic fileまたはkeystore aliasが残っている場合は `KEY_LOST` を返し、
黙って別のcredentialへ交換しないという既存契約は維持する。
storageの強い保存条件を満たしたと推定してこのclassへ相乗りしない。

### LinuxProfileStoreで既にある保存境界

[LinuxProfileStore.AtomicFile](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxProfileStore.kt)は次を持つ。

- companion objectの共通lockによる、同一process内の別Store instanceを含む直列化。
- 自UIDの0700 directory、自UIDの0600 regular file、`nlink=1`の検査。
- `O_EXCL | O_NOFOLLOW`でのtemp作成、data sync、temp readback。
- directory inodeとexpected bytesの再照合、rename、directory fsync、保存先readback。
- rename後の失敗でも、古いprofileへrollbackしない扱い。

これはOS processをまたぐatomic compare-and-swapの保証ではない。
E1も保存ownerを同一processの共通直列化境界に置く。
別processからの並行保存を必要とする場合は、別の排他・所有設計が必要である。

[LinuxProfileRepository](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxConnectionProfile.kt)は
expected binding、明示確認、revision増分と、保存後の全field一致をさらに検査する。
[NativeSessionHost.saveLinuxConnection](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)は
変更可能gateとcaptured bindingを確認し、runtime停止後に保存する。
publish後readback失敗でも古いin-memory profileへ戻さず、null化して保存先を読み直す。
E1はこの境界を維持し、storage recordにも同様のCAS・sync・readbackを適用する。

profile本体とstorage recordの複数fileを更新する場合、各fileのatomic renameだけで全体のatomicityを主張しない。
途中失敗・CAS競合・readback失敗では、現在profileとstorage recordのcaptureが一致すると再確認するまでv3を利用不可とする。
古いcached storage recordと新profile、新storage recordと旧cached profileを混合して成功扱いにしない。
新旧のrecordをどうpublish/reloadするかはE1のI/O実装で完成させ、B1でfile保存を開始しない。

## C1へ渡すremote owner record

Android storage recordとremote owner recordは異なるschemaであり、相互に読み替えない。
C1向けowner schema 1は次の**6 fieldだけ**を持つ。

| field | 型・固定値 |
| --- | --- |
| `schema` | integer `1` |
| `uid` | positive u32。JSON boolean/floatを整数として受けない |
| `installation_id` | 32桁lower hex。Android storage専用installation |
| `profile_storage_id` | 32桁lower hex。capture済みprofile storage |
| `app_id` | 固定 `mousepad-editor-v1` |
| `state_policy` | 固定 `persistent-keyfile-locked-v1` |

[L7a設計](linux-real-app-l7a-design-20261109.md#永続設定と一時sessionの分離)どおり、data keyは次のID組である。

```text
<verified-real-home>/.local/share/eidolon/apps/<installation_id>/<profile_storage_id>/mousepad-editor-v1/
```

owner recordへrevision、session nonce、credential identity、secretを入れない。
data keyの構文一致だけでfilesystem所有を認定せず、C1で認証済みUID、親directoryからのno-follow検査、
directory 0700 / record 0600、既存owner一致、生存helperのlock、reopen時のidentity再照合を完成させる。
同じremote data keyは同じhost/userへ束縛したcaptureからのみ使用する。
正常cleanupで永続rootを再帰探索・削除せず、別host/userへの変更後も旧rootを保存する。

## 共通fixtureと受渡し

共通contractの実装入口は
[Python](../scripts/linux_desktop_v3_contract.py) と
[Rust](../native/model/src/linux_desktop_v3/mod.rs)、固定fixtureは
[linux-desktop-v3-contract.json](../scripts/tests/fixtures/linux-desktop-v3-contract.json)である。
E1は同じcanonical bytes/digestと変更判定vectorを使い、Kotlin独自のstorage意味を増やさない。
fixture内のID/hashは合成検査値であり、製品pins、実際のID生成、保存成功の証拠ではない。

既存の保存回帰は
[LinuxConnectionProfileTest](../android/app/src/test/java/io/github/tinmachine/eidolon/LinuxConnectionProfileTest.kt)に
stale保存、別Store競合、publish前後失敗、重複key/不正UTF-8/別installation、Termux/debug profile拒否がある。
[LinuxCredentialCaptureTest](../android/app/src/test/java/io/github/tinmachine/eidolon/LinuxCredentialCaptureTest.kt)は
runtime/session/request/profile/revision/bindingのcapture照合とone-shot供給を検査する。
これらを維持し、E1では実保存経路にstorage継続/交代、部分publish、readback、欠落/破損の拒否検査を追加する。

B1の純粋判定成功は、Androidでの保存、remoteの永続profile作成、v3 Launch/Recover、Quest表示の成功を意味しない。
`available` / `production_acceptance_passed` / `quest_verified` はfalseのまま、v3 acceptanceは未実装のままとする。
