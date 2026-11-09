# B2: v3 SSH・journal・Recoverの接続境界

2026-11-10（日本時間）。B2は[B1契約](linux-real-app-b1-contract-20261110.md)を利用して版選択と共通transportを実装する。
C2の完成sourceとcommitが未提供のため、**最終source統合・実bundle・通常v3の実行受入れは未完了**である。
この文書のAPIが存在することを、remote helperの実process回収やQuest動作の成功とは扱わない。

## 単一の所有範囲

| 範囲 | 所有担当と入口 |
| --- | --- |
| 版付きPins/Binding/Protocol、wire/receipt parser、共通fixture | B2。[v3](../native/transport/src/linux_desktop/v3/mod.rs)、[wire](../native/transport/src/linux_desktop/v3/protocol.rs)、[receipt](../native/transport/src/linux_desktop/v3/receipt.rs) |
| source・manifest・immutable配置の検査 | B2。[source inventory](../scripts/linux-desktop-v3-sources.json)、[builder validator](../scripts/linux_desktop_v3_bundle.py)、[compiled source hashes](../native/transport/src/linux_desktop/v3/sources.rs) |
| SSH引数、channel・timer、journal、回収先の版選択 | B2。[owner](../native/transport/src/linux_desktop/v3/owner.rs)、[bootstrap](../native/transport/src/linux_desktop/v3/bootstrap.py)、[journal](../native/transport/src/linux_desktop/v3/journal.rs)、[dispatch](../native/transport/src/linux_desktop/dispatch.rs) |
| 永続profile、private bus、settings、復元guard、実lease/drain/Close/Stop/Recoverとreceipt生成 | C1/C2。B2はこれらの成功を仮実装しない |
| storage生成・保存 | E1。B2はcapture済みB1 `StorageRecord`の照合だけを行う |
| native admission、main採用、watchdog、SDK ACK/frame、通常UI/actorからの起動 | D/E2。B2 APIを呼ぶ側の接続は後続 |

## Typed bootstrap

`owner::command(&Binding, &B1::Bootstrap)` は、B1の`matches`と14/15 vector decoderを通した値だけを使う。
serve/recoverのoperation、context、pins、storage IDsをshellへ自由入力として渡す口はない。
vector末尾へ任意path/commandを足せず、構文が正しくてもcaptureと異なるhash/IDを拒否する。

実行commandは固定の `/usr/bin/python3 -I -S -c ...` である。
Pythonの[isolated modeとsite import抑止](https://docs.python.org/3/using/cmdline.html#cmdoption-I)に加えて、
bootstrapは固定account home、UID、祖先directory、no-follow file open、mode/link count/size、
読み取り前後と現在pathのinode/mtime/ctimeを照合する。FIFOは`O_NONBLOCK`で開き、regular fileでなければ拒否する。

固定base `<real-home>/.local/lib/eidolon-linux/` は既存bundle.jsonのhashだけでなく、
base helper・Waypipe・SHM fixtureの実bytesも検証する。base executableのpermission条件は既存v2と同じであり、
既存baseをv3用modeへ変更しない。

v3の場所は `<real-home>/.local/lib/eidolon-linux-desktop-v3/<build-id>/` に限る。
rootは0700、完成build directoryと`gio/`は0500、通常fileは0400、3実行fileは0500。
`desktop-bundle.json`、app manifest、全sourceと生成物、未知file/directory不在を検証してからbundle codeをimportする。
sourceは検証時のbytes snapshotからcompileし、自由な`sys.path`や2回目の未検証file readからcodeを読まない。

固定base sourceには `eidolon_linux_session` → `eidolon-linux-session.py` のaliasを与える。
C2は通常の `import eidolon_linux_session` を使える。その他の独自moduleはinventoryにあるflat siblingだけである。
A0 inspectorの固定manifest alias loaderも同じsnapshotを利用する。未pin fileのdynamic importを許可しない。

最後にB1 Pinsを構築し、B2の
`validate_bundle(home, pins, source_hashes, inventory_raw)` を呼んでimmutable証拠を再照合する。
C2 entrypointの提案APIは `eidolon_linux_desktop_session_v3.main(argv)`。
このsignatureとimport closureは、C2の確定commitを取り込む際に実装と照合する必要がある。

`command`は純粋な構築APIなので、不足依存下でも固定vectorと拒否条件を検査できる。
一方、**`Owner::open`、`recover`、公開`Journal::reserve`は `sources::require_complete()`を必須とし、
現在は`APP_V3_SOURCE_INCOMPLETE`で拒否する。** 生成された文字列を製品実行許可には扱わない。

## Journalと回収barrier

新journalは `linux-desktop-v3-state/pending.json`、schema 2。
旧 `linux-desktop-state` と別directoryであり、旧schema/bytes/lock/Recoverを再解釈しない。
recordにはprotocol revision、B1 Context全体、B1 Pins全体、build-id、compiled source map、
stage、nullable boot ID、8つのcleanup証拠を保存する。
raw decode時の重複key、余剰/欠落field、不正型、位置array、enum object、未知版、source/build-id差替えを拒否する。

予約はexclusive publish、更新はlock下のexpected bytes/inode照合＋fsync＋atomic renameを用いる。
Prepared/ForwardRequested/StartRequested/Startedの遷移順を固定し、未確認状態は`unknown`として残す。
`clear`にはprocess、pipe EOF又は検証済みboot変更、remote掃除、receipt、channel閉鎖、forward退役、router drainをすべて要求する。

新予約は旧v2 pendingがあると`RECOVERY_REQUIRED`で止まる。
[共通Recover入口](../native/transport/src/linux_desktop/dispatch.rs)の`captured_version`は
両journalをそれぞれ既存decoderで読み、選択された版との不一致又は両版pendingなら拒否する。
不正/読取不能なrecordを「pendingなし」に変換しない。V2/V3どちらかのRecoverが失敗しても、他方へ再試行しない。

D/E2のactor接続では、v2/v3以外の既存backendも含む全owner barrierとSDK退役gateを引き続き保持する必要がある。
B2だけで通常UIのv3選択や複数actor同時実行を有効化しない。

## Recoverの照合とC2への受渡し

`Recovery`は認証済みprofile、新接続session、remote UID、選択済みpins、capture済みstorage record、
アプリfiles directory、前connectionの閉鎖証拠を受ける。
journalに保存した旧session/contextからBindingを再構築し、現在の認証先profileとstorage recordの
legacy installation/profile binding/host-user binding/storage IDsを照合する。
旧sessionを新sessionへ付け替えず、新sessionはSSH routerの所有照合に使う。

Recover argvの15番目は保存boot ID、Prepared ACK喪失なら`-`。
C2は保存owner/receiptから`origin_boot_id`を返す。B2は既知originの差替えを拒否し、
`process_evidence=receipt`、`boot_changed`、`unknown`を区別する。
同boot unknown、missing helper、source不一致、partial channel/pipe証拠はpendingのまま。
保存PIDへの後日signalをB2は実装しない。

**Recoverは現在のMousepad/GTK/D-Bus packageを検査せず、appもversionコマンドも起動しない。**
immutable helper/manifest/source検査と、Inspect/Launch前の`validate_launch_package`は別APIである。
package更新・削除後でも正しい旧immutable helperと所有receipt/boot証拠で回収できる構成を維持する。
ただし実際のprocess回収とreceipt生成はC2の未統合範囲であり、このPRでは製品回収成功を認定しない。

## 検査の意味

[owner tests](../native/transport/src/linux_desktop/v3/owner/tests.rs)は実生成commandをPython ASTで読み、
B1固定vectorどおりの14/15引数と識別子差替え拒否を検査する。app/helperを起動しない。
[journal tests](../native/transport/src/linux_desktop/v3/journal/tests.rs)は実file I/Oでlock/exclusive publish、
遷移・全cleanup証拠、ID/source/版不一致、inode交換、FIFO/link拒否、v2 barrierと版dispatchを検査する。
[receipt tests](../native/transport/src/linux_desktop/v3/recovery/tests.rs)はorigin boot喪失/復元、
同boot unknown、誤版/source/exit status、重複・複数receiptを拒否する。

journalの内部value-only層をunit testで使うのは、製品source不足を埋める実helperではない。
公開reserve/open/recoverの拒否gateを変更せず、通常v3 acceptanceとQuest成功は未主張のまま維持する。
最終headの実行結果、skip/未実行範囲、CIはPR本文へ記録する。
