---
id: linux-desktop-v3-c1-profile
title: Linux Desktop v3 C1 persistent profile handoff record
type: historical
feature: linux-desktop-v3
component: remote-profile
phase: C1
created: "2026-11-10"
last_reviewed: "2026-11-13"
baseline_commit: 151639486d41f6117a7e44c2d977af19ee430ee6
depends_on: [linux-desktop-v3-b1-contract]
related_sources:
  - scripts/linux/eidolon_linux_persistent_profile.py
---
# L7a C1: remote永続profile・所有handle・排他とC2への引継ぎ

2026-11-10（日本時間）。C1の対象は、B1の純粋契約を消費する**remote永続profileの実I/O境界**である。
通常helper、bus、app spawn、settings policy、lease/drain、実Close/Stop/RecoverはC2の別branch・別PRへ残す。
C1のPR作成を今回の完了点とし、この文書のC2用プロンプトを保存すること自体をC2着手の依頼とは扱わない。

## 1. 基準・変更範囲

開始時のmainは [`a586f042583ac0869dc6f15e06cacae96db9bd7b`](https://github.com/tin-machine/eidolon-wayland/commit/a586f042583ac0869dc6f15e06cacae96db9bd7b)。
[B1 PR #7](https://github.com/tin-machine/eidolon-wayland/pull/7) は未マージで、確認した現在headは
`80321efa9a508f2870fe8da3b5c869f2f15633a9`、treeは `0e0d95bde000919cf4161dc6744a8d5611ea2a94` だった。
そのheadから独立worktreeの `feat/linux-desktop-v3-c1` を作成し、B1の `feat/linux-desktop-v3-contract` をbaseとするdraft PRにする。
これらのSHAは開始時の記録であり、後続作業のcheckout先やC1の検証成功を固定する指定ではない。

main・B1の既存CI成功はC1の結果に加算しない。C1の実装head、CI checkout、実行結果とskipの内訳はPR本文へ記録する。
この文書だけでfilesystem試験、実回収、通常v3、Questの受入れが成功したとは判断しない。

次の文書と実装を照合した。

- [B1契約](linux-real-app-b1-contract-20261110.md)、[Android identityとowner schema](linux-real-app-b1-android-identity-20261110.md)、[表示契約](linux-real-app-b1-presentation-20261110.md)、[B1引継ぎ](linux-real-app-b1-handoff-20261110.md)。
- [L7a詳細設計](linux-real-app-l7a-design-20261109.md)、[A/A0基盤](linux-real-app-a0-foundation-20261109.md)、[A0再起動](linux-real-app-a0-restart-20261109.md)、[A0 GUI設定](linux-real-app-a0-gui-settings-20261109.md)。
- [B1のPython契約](../scripts/linux_desktop_v3_contract.py)、[共通golden](../scripts/tests/fixtures/linux-desktop-v3-contract.json)、[既存ProcessOwner](../scripts/linux/eidolon_linux_process_owner.py)。

C1の新moduleは [eidolon_linux_persistent_profile.py](../scripts/linux/eidolon_linux_persistent_profile.py)。
B1共通schema・hash式・Python/Rust module・golden、固定v2の5 source、builder、共通transport、journal/SSH bootstrapの版選択、Android保存層は変更対象にしない。

`available` / `production_acceptance_passed` / `quest_verified` はfalseのまま、通常v3 acceptanceは `APP_V3_NOT_IMPLEMENTED` のままとする。
C1のhandleを取得できても、appの起動許可、source closureの検証完了、復元データguardの成功、通常v3の提供開始を意味しない。

## 2. 固定path・policy

認証済みcontextのUIDと、実行中の非root processのUIDを照合し、[`pwd.getpwuid()`](https://docs.python.org/3/library/pwd.html#pwd.getpwuid) のpasswd entryから実HOMEを得る。
環境変数 `HOME` / `XDG_DATA_HOME`、呼出側の自由path、session nonce、revision付きprofile bindingから永続rootを選ばない。

```text
<verified-real-home>/.local/share/eidolon/apps/
  <storage-installation-id>/<profile-storage-id>/mousepad-editor-v1/
    owner.json
    profile.lock
    home/
    config/
    data/
    state/
    Documents/
```

data keyはB1の `StorageInstallationId` と `ProfileStorageId` の組である。
同じID組でrevision・binding・nonceが変わっても永続rootは同じである。一方、取得権限は毎回現在のcontextとC2 barrierに束縛する。
ID組を変えた場合は別namespaceを扱い、旧データを削除・コピー・探索・自動採用しない。

製品の `Documents/` は上記root直下に置く。A0合成profileの `home/Documents` や観測markerを製品profileとして採用しない。
settings keyfileの固定path/policyはB1と[A/A0のmanifest validator](../scripts/linux_real_app_manifest.py)に従い、C1で別のsettings schemaを作らない。

| 対象 | 所有・権限と扱い |
| --- | --- |
| HOMEまでのancestor | 親からno-followで開き、型・所有・書換え権限を確認。system ancestorを製品directoryとしてchmodしない |
| 実HOME、既存 `.local` / `share` | 信頼するanchorとして検査し、group/otherから書換え可能な状態を拒否。既存の安全な通常modeを勝手に0700へ変更しない |
| 製品namespaceとprofileの固定directory | 認証済みUID、0700を要求 |
| `owner.json` / `profile.lock` | 認証済みUID、0600、regular file、単一linkを要求 |
| process umask | 077を維持。C2も変更しない |
| 設定・保存文書・復元候補 | アプリの永続データ。C1の正常解放では再帰探索・削除しない |

[`umask(2)`](https://man7.org/linux/man-pages/man2/umask.2.html) はprocess全体に影響するため、C1は既存 `ProcessOwner` と同様に専用helperのlifetimeを前提にする。
任意のGUI processや共有thread poolへ埋め込み、処理末尾で弱いumaskへ戻す利用はこの契約に含めない。

### 一時領域との分離

runtime、cache、tmp、Waypipe socket、bus socket/configは、将来C2が所有するsession handleの領域である。
L7aの設計先は既存session root配下の `s-<nonce>/` だが、**C1は独自のsession pathや一時rootを作らない**。
sessionのno-follow検査・所有・回収を永続profileの成功で代用しない。

`handle.paths` は取得中の固定永続pathを提供する。`handle.environment()` が返すのは次の4変数だけである。

| 変数 | 固定先 |
| --- | --- |
| `HOME` | `<profile>/home` |
| `XDG_CONFIG_HOME` | `<profile>/config` |
| `XDG_DATA_HOME` | `<profile>/data` |
| `XDG_STATE_HOME` | `<profile>/state` |

初期cwdには `<profile>/Documents` を使う。
この4変数は完全なapp environmentではない。C2はinheritしない固定envへ、独立に検証したruntime/cache/tmp、Wayland、private bus、settings module等を加える。
helper自身のHOMEをアプリ用HOMEへ差し替えない。

## 3. owner recordと実I/O

`owner.json` はB1 `StorageOwner.parse()` と `matches_context()` で検査する。独自JSON parserや緩いschemaを追加しない。
owner schema 1の全fieldは次の6つだけである。

| field | 型・固定値 |
| --- | --- |
| `schema` | strict integer `1` |
| `uid` | positive u32 |
| `installation_id` | storage専用lower hex 32文字 |
| `profile_storage_id` | lower hex 32文字 |
| `app_id` | `mousepad-editor-v1` |
| `state_policy` | `persistent-keyfile-locked-v1` |

secret、credential identity、revision、binding、nonce、process ID、inodeをownerへ追加しない。
B1 decoderの16KiB上限、未知・欠落・重複field拒否、strict integer、UTF-8等の条件をそのまま使用する。
schemaが正しくても、認証済みUID・capture済みID組・filesystem所有が一致しなければ拒否する。

### 初期作成

初期化は新規profile directoryの排他的作成から始める。固定directoryとlockを作成し、ownerを最後に公開する。
必要なfile/directoryをsyncし、readback・identityを確認した後にだけhandleを返す。
[`fsync(2)`](https://man7.org/linux/man-pages/man2/fsync.2.html) のとおり、fileのfsyncだけでは親directory entryの永続化を保証しないため、directoryにもfsyncする。

既存profileの欠損したowner、lock、固定directoryを初期作成の残りとして補完しない。
同時作成で `EEXIST` になったことだけでは「正しく初期化済み」と判断しない。
途中失敗で残ったtreeは失敗の状態として保存し、次のacquireで部分初期化を拒否する。自動削除・再生成・rollbackで成功へ変換しない。
最終sync等の失敗で見かけ上すべてのfileが揃っている場合も、過去の失敗をtreeの形だけで解消したとは判断しない。
C2に残ったdurable pendingと回収barrierを確認する必要がある。

`expected_identity` があるreopenでは、すべての親directoryとprofile rootを `create=False` で開く。
既知のprofileや親が消失していれば、その時点で拒否する。新しいtreeを作ってから期待identityとの不一致を報告する動作にはしない。

### reopenと名前空間の再照合

全path成分を親FDから `openat` 相当で開く。
[`open(2)`](https://man7.org/linux/man-pages/man2/open.2.html) の `O_NOFOLLOW` は最終成分だけを保護するため、leafだけに付けて親symlinkを許す方式にしない。
Pythonでは [`os.open(..., dir_fd=...)` とnofollow stat](https://docs.python.org/3/library/os.html#files-and-directories) を使い、開いたFDと親entryのdevice・inodeを比較する。

root、lock、owner、親anchor、5つの固定data directoryのFD/identityを保持し、取得後の操作と解放時にも再照合する。
symlink、hardlinkされたrecord/lock、別UID、型・mode不正、owner不一致、別profile、内容破損、既知identityの差替えを拒否する。
既存ownerをcanonical化するための上書きや、権限を修正してから成功にする処理は行わない。

live handle内のowner/lockの不変stampにはdevice・inode・UID・mode・link数・size・mtime・ctimeを含める。
readによって変わり得るatimeは含めない。rename後に元のinodeを同じ名前へ戻してもctimeが変わっていれば、同じhandleの検査は引き続き拒否する。
「元に戻したように見える」ことを自動再採用の証拠にせず、状態を保持して明示的な調査またはC2の回収判断へ渡す。

## 4. 排他と所有handleのlifetime

[`flock(2)`](https://man7.org/linux/man-pages/man2/flock.2.html) はpathnameではなくopen file descriptionに付くadvisory lockである。
`profile.lock` をunlinkして再作成すると、古いholderと新しいholderが別inodeをlockできるため、C1はlock fileを削除・再生成しない。

C1は次のlockをhandleの全lifetimeで保持する。

1. `/` から続くroot所有かつgroup/otherから書換え不能な親chainの直下で、最初に認証済みUID所有になるdirectoryのflock。このdirectoryを安定anchorとする。
2. passwd由来の実HOMEがそのanchorの内側にある場合は、実HOMEにもdirectory flockを取得する。anchorと実HOMEが同じ場合は重複取得しない。
3. 現在のprofileにある同じ `profile.lock` のflock。

通常の `/home/user` では実HOME自身が安定anchorになる。
passwd HOMEが `/home/user/nested/home` のようにUID所有directoryの内側にあれば、安定anchorは `/home/user` である。
同UIDは実HOMEをrenameしてコピーを置けても、root所有の親entryに置かれたanchor自身をrenameできない。
実HOMEだけのlockではこのnested HOME差替えが別inodeの取得へつながるため、C1は安定anchorも保持する。
専用test accountでnested passwd HOMEを検査する場合も、実際のtest account HOMEが安定anchorになる。

このlockは、同UIDの別processがlock pathname、製品directory、nested HOMEを差し替えた場合にも、準拠する別C1 acquireが同時に成功しないための保守的な排他である。
異なるstorage IDやnested HOMEでも同じ安定anchorを共有するC1利用は直列になる。
L7aの1 lease/1 mainに合わせた制約であり、将来の同時複数profile対応は安定anchorとjournal所有を含む別設計で行う。
正常解放ではprofile lockと必要な実HOME lockを先に閉じ、安定anchorのFDを最後に閉じる。

directory flockとdirectory fsyncが成立するlocal filesystemを前提とする。
NFS/CIFS等では [flockの伝播・emulation・open modeの条件が異なる](https://man7.org/linux/man-pages/man2/flock.2.html) ため、未検証のfilesystemを同等に対応済みとしない。
lock失敗時にprocess-local mutexへfallbackしない。

flockが取れた事実は旧helper不在や旧子孫回収の証拠ではない。
helper死亡でFDが閉じても、C2のunknown journalは次のLaunchを止めなければならない。
同UIDの任意の敵対コードがadvisory lockを無視して書き換えることを防ぐ隔離機構や、remote hostの実行証明を提供するAPIではない。

### identityの保持とcold reopenの限界

`handle.identity` は取得時にpinしたfilesystem identityをC2へ提供する。
`expected_identity` を指定したreopenでは、既知のroot/lock等と現在のidentityを照合する。
この経路では親/rootの新規作成を行わず、既知のpathの欠落を初回作成へ読み替えない。
C2がその所有についてjournalに証拠を残す場合は、captureしたcontextと一緒にこのidentityを相関させる。
そのremote journal/wire schemaはB2/C2の統合対象であり、C1独自の共通schemaとして公開しない。

B1の6-field ownerにはincarnationがない。
過去のidentityを持たないcold processが、完全コピーされた同じowner bytesのtreeを元のtreeと区別できるとは主張しない。
C1が検出するのは取得したanchor/identityとの不一致と、明示された期待identityとの不一致である。
未知journalや証跡喪失を「cold reopenだから安全」と読み替えないことがC2のbarrier条件になる。

## 5. C2向けAPI

| API | 入力・意味 |
| --- | --- |
| `PersistentProfileRepository.acquire(context, *, process_owner, barrier, expected_identity=None)` | B1 `Context`、現在の専用helperに属する既存 `ProcessOwner`、必須の `JournalBarrier` を受け取り、所有handleを返す |
| `JournalBarrier(context, owner)` | B1 `Context` と6-field `StorageOwner`、現在helperのPIDへ束縛するabstract境界。通常用の具体adapterはC1に含めない |
| `barrier.before_acquire()` | filesystemの新規作成/採用より前のjournal/reclaim予約検査 |
| `barrier.after_acquire(identity)` | flock・filesystem照合とlive handleへの所有移譲後、同じ予約と取得identityを再照合 |
| `barrier.before_release(evidence)` | C1が計算した実回収証拠を受け、C2のjournal/receipt境界を検査 |
| `handle.track_process(child)` | `ProcessOwner.spawn()` が返した全childを登録し、stdout/stderrの複製FDを保持する |
| `handle.paths` / `handle.environment()` | 現在のhandleに属する固定永続pathと4つの環境変数を返す |
| `handle.identity` | pinしたfilesystem identity。既知の所有の再照合に使用 |
| `handle.release()` | 同じ実owner、全子孫、全root、pidfd、pipe EOF、barrierとfilesystemを検査してから解放 |

local APIの値型は次のとおり。wire/remote journalの新schemaとして流用しない。

- `FileIdentity(device, inode)`。
- `ProfileIdentity(home, root, lock, owner, directories)`。前4つは `FileIdentity`、`directories` は固定5directoryの名前とidentityのtuple。
- `PersistentPaths(root, home, config, data, state, documents)`。`documents` の実path名は `Documents`。
- `ReleaseEvidence(scope, identity, helper_pid, helper_start, roots, pipes)`。`release()` が現在ownerを実検査して計算し、barrierに渡す値。callerが作ったevidenceをrelease入力として受け取らない。

公開failureは `ProfileError` の固定reasonで、`APP_STATE_INVALID` / `APP_STATE_BUSY` / `APP_STATE_RECOVERY_REQUIRED` を使う。
live handleへ所有を移譲した後の失敗には `error.handle` が同じcapabilityを保持する。`acquire()` 自体が例外で終わった場合も、この属性を必ず確認する。
`handle.closed` は正常close完了、`handle.created` は今回の新規作成かどうかを示す。
どちらもapp受入れやremote journalのcleanを意味しない。

barrierの各検査は、拒否時には例外を出し、受理時の返値は `None` のみとする。
boolの `True`、任意の「成功」object、caller提供のclean flagを証拠として受け取らない。
これは呼出側で証拠を検証するためのinterfaceであって、`None` という値自体がjournalの安全性を証明するものではない。

barrierはcontextと同じstorage owner、現在helperに束縛されたone-shot reservationである。
C1のhandleは別途同じlive `ProcessOwner` を保持・検査する。constructorの `owner` に `ProcessOwner` を渡すAPIではない。
C2は取得前に現在のjournalを照合し、durable pendingと所有予約を確立し、その予約をhandle lifetime全体と全error経路で保持する。
`before_acquire` と `after_acquire` の間に別contextへ切り替えたり、旧ownerのbarrierを新しいacquireへ再利用したりしない。
C1にはjournal未接続を常時成功で埋める具体adapterはない。test専用のadapterを通常helperへimportしない。

`after_acquire` を呼ぶ前に、取得したFD/lockをhandleへ移譲し、active registryへ登録する。
以後のhook failure、再検査failureでは、`acquire()` が通常returnしていなくても同じhandleとlockを保持して例外へ渡す。
C2は例外だけを見て「取得前の失敗」と判断せず、`error.handle` を引き継ぎ、同じownerの回収とreleaseを進める。
所有移譲より前の失敗では子プロセスを生成していないため、C1がその取得途中のFDを閉じる。どちらの場合もC2のdurable pendingを成功扱いで消さない。

### C2が満たすbarrier前提

- 認証済み同じ接続、UID、B1 context、source/pins、storage ID組に対する現在の所有を検査する。
- 旧ownerのjournal、回収待ち、helper喪失、unknown receiptを無視しない。flock取得だけでcleanを認定しない。
- 取得予約とdurable pendingを、app起動前の実所有へつなぐ。filesystem取得後のI/O失敗でも予約を捨てない。
- current helperのlive回収と、別processによるRecoverで検証できるreceipt/boot証拠を区別する。
- journal/receiptのfsync・readback・相関に失敗したら、unlockを成功として返さず、同じlive handleのpendingを維持する。

`before_release` はreleaseを許可できるdurable証拠を確定する境界であり、journalをcleanへ変更したり予約を解放したりする最終段階ではない。
その後のC1再検査・FD closeにも失敗の可能性がある。`handle.release()` の成功とC2自身のsession/socket/receipt等の必要条件を確認してから、
C2が予約とpendingの最終状態を確定する。hookの返値だけで次のacquireを許可しない。

remote barrier/receiptの具体schemaはB1に含まれていない。
必要なwire field、journal保存先、bootstrap/Recoverの版選択、source pinningはB2統合担当と一本化する。
C1のlocal APIを理由に、B1のschema/hash式/goldenやv2 journalを独自変更しない。

## 6. 全子孫・pipe EOF・lock解放条件

C1はappを起動しない。C2が既存 `ProcessOwner.spawn()` を呼んだら、その直後の安全な境界で必ず `handle.track_process(child)` を呼ぶ。
全rootを登録する必要がある。未登録のrootを「C1の対象外」として解放条件から外さない。
track時にstdout/stderrを複製するので、C2が元のpipeをdrain/closeした後もC1はkernel EOFを検査できる。
pipe本文を永続owner recordや診断へ保存しない。

[`ProcessOwner`](../scripts/linux/eidolon_linux_process_owner.py) は非root、dedicated single-thread、pidfd、subreaper、全子孫scanを持つ既存の固定sourceである。
C1はそのsourceを変更しない。同moduleの `finish()` がcompleteでもpipe EOFは別条件であり、回収reportのboolだけで解放しない。

`release()` は次をすべて要求する。

1. acquire時の同じlive `ProcessOwner` がfinish済みで、complete・pendingの条件が成立している。
2. ownerのopen pidfdが0で、現在の実descendant scanも空である。
3. ownerが生成した全rootの終了statusが確定し、全rootがtrackされている。
4. 保持した全stdout/stderr pipeでkernel EOFを確認できる。単なるPopen終了、process group消失、pipeのcaller側closeでは代用しない。
5. C1で計算したlocal evidenceについて、同じbarrierの `before_release(evidence)` がC2のjournal/receipt条件を検査する。
6. barrier処理後にもfilesystem identityと実回収条件を再照合し、同じ所有だけを解放する。

barrierの返値をcallerの `all_children_exited=True` や `pipes_eof=True` へ置き換えるAPIは提供しない。
pipe読み出しがまだEOFへ到達しない、scanが失敗する、rootが未登録、journal書込みが失敗する場合は、同じhandleを保持して再試行する。
解放待ちの途中で新しいownerや別processへlock FDの意味を付け替えない。

handleはactive registryで保持し、通常のGCや例外脱出で暗黙にunlockしない。
無条件closeを行うdestructor/context managerは提供しない。
ただしprocess自体の終了ではkernelがFDを閉じるため、registryはクラッシュ耐性の代わりにならない。次の起動を止める条件はC2のdurable journalに残す。

実close開始前の検査失敗では同じhandleでreleaseを再試行できる。
一方、close処理中のI/O failureは一部FDが既に閉じられた可能性があるterminal/uncertain状態であり、通常の再試行とは区別する。
[`close(2)`](https://man7.org/linux/man-pages/man2/close.2.html) が説明するように、Linuxでは失敗後の同じFD番号へのclose再試行が別資源を閉じ得るため、
C1は曖昧なFD番号を再利用して解放を完了したことにしない。C2はdurable pendingを保持し、正常cleanup成功を返さない。

解放はlock/FDとlocal所有を閉じる操作であり、永続treeを削除するcleanupではない。
設定、Documents、autosave候補、破損して調査が必要なbytesをそのまま保持する。
実appの起動前にはC2が同じlock下で既存 `guard_recovery_state()` を適用する。C1のopen成功は復元候補なしの証拠ではない。

## 7. failure時の状態

| failure地点 | 状態と必要な対応 |
| --- | --- |
| UID/passwd/anchor/barrierの取得前検査 | handleを公開せず拒否。既存profileを修正・削除しない |
| 安定anchor・実HOME・profile lock競合 | 競合として拒否。unlink、再作成、process-local lockへのfallbackをしない |
| 期待identity付きreopenで親/rootが欠落 | `create=False` のまま拒否。置換treeや欠落directoryを作成しない |
| 新規作成途中・write/fsync/readback失敗 | 成功を返さず、作成済み状態を保存。部分初期化は次回拒否し、形が揃っていてもC2のpending/barrier処理は必要 |
| 既存owner破損・別UID/ID・mode/型不正 | 既存bytesを保持して拒否。初回未設定や空profileに読み替えない |
| 取得中のdirectory/record/lock差替え | 継続利用を拒否。既知の元FD/identityを未知pathへ置き換えない。元inodeを戻しても不変stampが異なれば自動再採用しない |
| 所有移譲後のafter_acquire・再検査failure | acquireの例外へ同じhandleを渡し、registryとlockを保持。C2が同じownerの回収/releaseを継続 |
| live handle取得後のapp/barrier/I/O失敗 | C2が同じownerを回収し、同じhandleを保持。途中でlockを解放しない |
| descendants終了、pipe EOF未確認 | 解放pending。pipe EOFを確認するまで保持 |
| pipe EOF、owner回収未確認 | 解放pending。実ownerの全回収を確認するまで保持 |
| journal/receipt barrier失敗 | local回収済みでもpendingを維持。記録成功を捏造せず再試行 |
| FDの実close開始後のI/O failure | terminal/uncertain。既に閉じた可能性があるFDへcloseを再送せず、C2のdurable pendingを保持 |
| helper processの死亡 | kernel lockは外れ得る。C2のunknown journalが次のLaunchを止める |
| 全条件を満たした正常release | FD/lock/local所有を解放。永続設定・Documents・復元候補を保持 |

最初の起動/検査failureと、その後のcleanup完了は別の事実として残す。
後からlockを解放できたことを理由に、初回の失敗を検証成功へ書き換えない。

## 8. 推移的import/fileとB2への受渡し

製品source closureとして必要になるfileは次である。

| file | 役割・依存 |
| --- | --- |
| `scripts/linux/eidolon_linux_persistent_profile.py` | C1のrepository、所有handle、abstract barrier |
| `scripts/linux_desktop_v3_contract.py` | B1 `Context` / storage ID / `StorageOwner` / canonical JSON / strict validator |
| `scripts/linux_real_app_manifest.py` | B1 contractからの推移的import。固定manifest validator |
| `scripts/linux/eidolon_linux_process_owner.py` | 既存のlive子孫所有。固定v2 sourceと同じbytesを再利用 |

上記以外の依存はPython standard libraryである。
C1は通常の明示importを使い、自由な `sys.path`、環境由来のpath、repo checkoutへの暗黙fallbackで製品bundleを成立させない。
実際のbundle配置、isolated loader、全推移的importのhash照合とsource allowlistはB2が統合する。
sourceが未同梱・未pinなのに、単体testでimportできたことを通常v3利用許可にしない。

C2で追加するhelper、private bus template、settings source/binary/probe等も最終closureへ加える必要がある。
C1のcommitを含めただけでは完成したC2の最終bundle hashにはならない。
hashの自己参照を作らず、完成したsource集合の確定と実pinsはB2統合担当が行う。

A0合成profile、observer、harness、test fixtureは製品profileの依存ではない。
共通契約の不足が見つかった場合は、必要なfield・照合理由・旧vectorへの影響を記録し、B2へ一本化する。

## 9. 検証境界

実I/Oは専用test環境だけで行い、通常利用のpasswd HOME内profileやユーザー文書を試験対象にしない。
B1の同一[fixture](../scripts/tests/fixtures/linux-desktop-v3-contract.json)とvalidatorを使う。I/O用UID等の実環境値を束縛する場合も、共通fixtureを変更・複製して別契約にしない。

| 検証対象 | 必要な証拠 |
| --- | --- |
| 初期作成・再open | 実filesystemのmode/owner/bytesと同一dev/inodeの再取得 |
| 同時作成・lock競合 | 別OS processでの競合。一つのprocess内mutexだけで判定しない |
| 差替え | root、親、固定directory、owner、lock、nested passwd HOMEの差替え拒否。旧holder生存中の別process取得と安定anchorの保持も確認 |
| 部分初期化・途中失敗 | write/fsync/readback等の失敗後に自動修復せず、残った状態を拒否 |
| schema/所有 | B1 raw拒否vector、別UID、別ID、未知/余剰field、link、mode/型不正 |
| 保存データ不変 | settings・Documents・復元候補のbytesを取得前後/正常解放後に照合 |
| 解放条件 | 実processの全回収、実pipe EOF、未track root、未回収/不一致barrierでlockが保持されること |
| 既存互換 | 固定v2の5 sourceとgolden、v2 hash/schema/bootstrap/journal/Recoverを維持 |
| 既存A0 | 実process異常経路、復元保護、全worker回収、共有lifetime、125秒先行保持後のfresh応答 |

mockで返したcleanup successを実回収の成功件数へ加算しない。
Python/Rust、host/Android CI、専用A0 CIはC1の新PR headで確認し、skipは合格に数えない。
実行不能の理由と範囲、過去headとの差、CIで補えた範囲はPR本文で明記する。
Quest実機・物理入力・通常v3 acceptanceはこのPRの成功から補完しない。

実装したC1検査入口は [test_linux_persistent_profile.py](../scripts/tests/test_linux_persistent_profile.py)、
専用の別process fixtureは [linux_persistent_profile_worker.py](../scripts/tests/fixtures/linux_persistent_profile_worker.py) である。
fixtureはテストaccountの実HOME直下へ作った専用0700 directoryだけをpasswd HOMEとして与え、実UID・filesystem・flock・pipeは差し替えない。
通常の永続profileや既存文書へ接続しない。rootのみの環境では実所有classをskipし、その件数を成功へ加算しない。

```bash
python3 -m unittest discover -s scripts/tests -p 'test_linux_persistent_profile.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_linux_desktop_v3_contract.py' -v
python3 scripts/check-design
cargo test --locked -p eidolon-model linux_desktop_v3
scripts/check-host --suite all
```

## 10. C2開始条件と次セッション用プロンプト

C2は、レビューを反映して確定したC1 commitと、B2のwire/bootstrap/journal/receipt境界を確認してから着手する。
C1作成時点のbranch HEADや本書の開始時SHAを無条件に固定しない。
B2が未完ならその未実装境界を確認し、成功placeholderで通常helperを接続しない。

以下をC2依頼時に使用する。確定commitは、その時点の実際のレビュー結果から記入する。

```text
tin-machine/eidolon-waylandの開発を継続してください。
目標はQuest 3の広い作業空間で複数のLinuxアプリを扱うことです。
今回は「C2: 通常v3 helper」を、C1とは別branch・別PRで実装してください。マージは行わないでください。

最初に最新main、B1/C1/B2/E1の関連PRとCI、現在のAGENTS.md等を確認してください。
C1はレビュー反映後の確定commitを確認し、古い引継ぎSHAへ戻さないでください。
B2の現在のwire、SSH bootstrap、journal、cleanup receipt、版別Recoverの境界を先に読み、
C1確定commitとの組合せで実装できることを確認してください。
mainへ必要な変更がマージ済みなら最新mainから独立worktreeと新branchを作ってください。
未マージなら依存関係を明示し、適切な現在の依存branchをbaseとするdraft PRにしてください。
依存PRの無断マージや、未レビューの別実装への置換は行わないでください。

主な引継ぎ:
- docs/linux-real-app-c1-handoff-20261110.md
- docs/linux-real-app-b1-contract-20261110.md
- docs/linux-real-app-b1-android-identity-20261110.md
- docs/linux-real-app-b1-presentation-20261110.md
- docs/linux-real-app-l7a-design-20261109.md
- docs/linux-real-app-a0-foundation-20261109.md
- docs/linux-real-app-a0-restart-20261109.md
- docs/linux-real-app-a0-gui-settings-20261109.md
- scripts/tests/fixtures/linux-desktop-v3-contract.json
- B2の現在の実装記録とsource closure一覧

A/A0/B1/C1を再実装せず、C1 repository/所有handleを通常helperへ接続してください。
JournalBarrierの具体adapterは、実際の現在journal・回収・receipt境界を検査する形で実装してください。
常に成功するbarrier、flock成功だけの旧owner不在判定、保存PIDからのsignal、
同UIDという理由だけの所有採用は行わないでください。
context/UID/storage IDs/source/pins/boot/receiptを既存契約に従って相関させてください。
必要な共通wire/schema差分と最終source pinningはB2統合担当へ一本化してください。

C2の対象は、固定private bus、検証済みsettings module、既存の復元データguard、
固定app spawn、leaseとbounded drain、Close/Stop/EOF/異常終了/Recoverです。
全spawn rootをC1 handleへ登録し、全子孫・pidfd・pipe EOF・journal barrierが成立するまで
同じlive ownerとprofile lockを保持してください。回収/receipt I/O失敗でもpendingを保持してください。
永続profileの設定・Documents・復元候補は削除・自動移行しないでください。
C1が返す4変数だけを完全なapp環境として使わず、C2の専用session handleで一時領域を所有してください。

固定v2の5 sourceとgolden、v2 hash/schema/bootstrap/journal/Recover、既存A0 gate、
実process異常経路、復元保護、全worker回収、共有lifetime、125秒保持後のfresh応答を維持してください。
Android保存はE1、native origin/main admission/watchdog/SDK表示追跡はD1/D2の境界を維持してください。
available / production_acceptance_passed / quest_verifiedは、実受入れが完成していない間falseを維持し、
通常v3 acceptanceの未実装をplaceholderで成功に変えないでください。

実filesystem・別process・実pipeで所有と回収を検査してください。
新PR headの関連Python/Rust、既存host/Android CI、専用A0 CIを確認し、skipを合格へ数えないでください。
実行不能の理由と未確認範囲を明記してください。
変更根拠・一次情報・failure時の状態・最終推移的import/file集合をリポジトリへ記録し、
PR本文を日本語でまとめてください。C2のPR作成を完了点とし、マージは行わないでください。
```
