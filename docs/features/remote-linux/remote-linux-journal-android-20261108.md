# NX18 Linux journalのAndroidでの排他的公開

Build 165の固定Linux SHM検査はSSHの`ProbeReady`まで進み、その後に
`LINUX_JOURNAL_FAILED`で停止した。通常runtimeの復帰とlocal owner回収は成立したが、
SHM受信の合格ではない。`journal_pending=true`は失敗時の保守的な状態であり、
ファイルが存在する証拠とは区別する。別途のread-only確認では専用0700 directoryがあり、
`pending.json`はなかった。

## 原因の範囲と変更

従来は0600 temporary fileをwrite/fsyncした後、`hard_link`で既存pendingを置換せず公開した。
固定private fixtureによる同UIDの`run-as`検査で、hard linkがPermission deniedとなり、
元ファイルはlink数1のままだった。ただし`run-as`と実APKのSELinux domainは同一とは限らず、
この結果だけでは実APKの失敗syscall/errnoを確定しない。

AOSPにはuntrusted appによるfile hard linkを禁止する規則があり、端末の観測と整合する。
[AOSP app_neverallows.te](https://android.googlesource.com/platform/system/sepolicy/+/8ac37f025f7a9e9efef4fb81182dc8e8c07d956b/private/app_neverallows.te)
を根拠として、公開処理を`SYS_renameat2`の`RENAME_NOREPLACE`へ置き換えた。
このflagは既存の宛先を上書きせず、filesystemの対応も必要である。
[Linux man-pages: rename(2)](https://man7.org/linux/man-pages/man2/rename.2.html)

- libcの`renameat2`動的symbolには依存せず、同一private directory内でkernel syscallを一回呼ぶ。
- 事前の不存在確認後に別ownerのregular fileやsymlinkが現れても上書きしない。
  `EEXIST`は`LINUX_CLEANUP_PENDING`として拒否する。
- `ENOSYS`、`EINVAL`、`EOPNOTSUPP`、`EPERM`、`EACCES`等は固定の
  `LINUX_JOURNAL_PUBLISH_*` tokenで失敗させる。通常rename、hard link、記録削除へのfallbackはない。
  その他のcreate/write/file fsync/directory fsync失敗も固定stage tokenで区別する。
- file fsync→排他的公開→directory fsync→nofollow読戻し、0600/same UID/link数1、
  bytes/device/inode一致によるclearの契約は維持する。profile/pins/path/本文をエラーに含めない。
- 公開後のfsync失敗などでpendingが残った場合も、別profileへ付替えたり自動削除したりしない。
  同nonce/profile/pinsに対する既存の明示recoveryを使う。

## 検証と次の実機gate

限定host検証17件とtransportのall-targets clippy（`-D warnings`）が成功した。
新規4件では公開成功時の完全な本文・UID/inode/link数維持、競合での一勝者、
事前確認後に挿入されたregular fileとdangling symlinkの非置換、未対応・拒否errnoの固定分類を検査する。
既存の別profile/旧Termux journal非置換、同owner回収、SSH取消・forward回収も回帰確認した。

```sh
CARGO_INCREMENTAL=0 cargo test --locked --offline -p eidolon-transport --features debug-fixtures linux_probe:: -- --test-threads=1
CARGO_INCREMENTAL=0 cargo clippy --locked --offline -p eidolon-transport --features debug-fixtures --all-targets -- -D warnings
```

共有host targetではowner lock内で自checkoutのworkspace entrypointのmtimeを更新し、
テスト名と件数確認まで連続して行った。初回は隔離checkoutの生成依存`smithay`が見つからず開始前に失敗し、
既存生成ソースへの参照を設けて再実行した。依存cacheの複製はしていない。

host成功はQuestでのsyscall許可を保証しない。次のAPKでは同じ確認済みprofile/pins/endpointを維持し、
実APK UIDの固定probeで新tokenの有無、100frameの実SHM画素hash、remote/local回収、通常Termux復帰を確認する。
SDK表示、通常Linux GUIの有効化、HMD受入れは別gateのままである。
