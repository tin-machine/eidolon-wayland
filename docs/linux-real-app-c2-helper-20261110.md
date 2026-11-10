# L7a C2: 通常v3 helper・remote回収barrier

2026-11-10（日本時間）。基準mainは `b43896446d6682ba1b78c05d4096a21d9cbc998b`。
C1の永続profile所有をB2のv3 wire/Recoverへ接続するC2実装を記録する。
通常v3 acceptance、native window admission、Android通常UI、Quest実機受入れは本書の範囲外であり、
`available` / `production_acceptance_passed` / `quest_verified` は引き続きfalseである。

## 所有境界

C2はremote側の一つのhelper processを所有者とする。`ProcessOwner`、C1 `ProfileHandle`、
`~/.eidolon-desktop-v3/s-<nonce>` のephemeral sessionを同じ寿命で保持する。
全spawn rootは `ProcessOwner.spawn()` の直後に `ProfileHandle.track_process()` へ登録する。
解放順序は **descendant回収 → stdout/stderr EOF → C1 `release()` → ephemeral session削除 → final receipt publish** である。
途中のI/O失敗は成功へ変換せず、B2 native journalをpendingのままにする。

C1のprofile rootは永続領域であり、C2のcleanupはそこを再帰削除しない。C2が削除できるのは
`~/.eidolon-desktop-v3/s-<nonce>` 以下のruntime/cache/tmp、固定socket/config/barrierだけである。
削除はno-follow、UID/mode/type確認、depth/entry/bytes上限付きで行う。

## C1 JournalBarrierの具体化

`RemoteSessionBarrier` はC1のabstract `JournalBarrier`を実装するが、B2 native journalの代替ではない。
remote session内の `barrier.json` をexclusive createし、次を固定する。

- B1 context scope / nonce
- B1 `StorageOwner` のcanonical hash
- origin boot ID
- `reserved` → `acquired` → `release_authorized` の単調なphase
- C1が返したprofile inode identity
- C1が自分で計算したrelease evidence

正常Serveは既存の `s-*` を採用・修復しない。stale sessionがあれば
`APP_STATE_RECOVERY_REQUIRED` で止め、flock取得だけを旧owner不在の証拠にしない。
同UIDはセキュリティ境界ではなく、このbarrierはEidolonの協調所有・クラッシュ整合性のためのものとする。

## Inspect / Start

Inspectはimmutable B2 bundleの `validate_launch_package()` を再実行し、C1 lock下で既存
`guard_recovery_state()` を適用する。復元候補が一つでもあればMousepadをspawnしない。
settingsはbundle内の固定 `linux-settings-probe --check` をsynthetic configで起動し、
`EidolonKeyfileV1Backend` と保護キーを実行時に確認する。

Startはpackageとrecovery guardを再検査し、実profile環境でもsettings probeを再実行した後、
もう一度recovery guardを通す。private D-Busは固定 `dbus-daemon --nofork --nopidfile --print-address=1` と
bundle内の固定templateから起動する。`--print-address` のconnectable addressは固定socket pathへ一致させ、
D-Bus仕様で正当な `,guid=<16-byte hex>` suffixを許可する。他path・複数addressは拒否する。
Mousepadのargvは `/usr/bin/mousepad --disable-server` のみで、
ファイル名、URI、追加argvをwireから受け取らない。Waypipeは既存base bundleの固定binaryを使い、
remote forwarding socketとprivate runtime displayを接続する。

C1が返すHOME/XDG_CONFIG_HOME/XDG_DATA_HOME/XDG_STATE_HOMEへ、C2が
XDG_CACHE_HOME/TMPDIR/XDG_RUNTIME_DIR、GDK/GIO/GSettings、private busを追加する。
SSH agent、呼出元PATH、desktop/session busなどのambient environmentは子へ引き継がない。

D-Busの設定構文とprivate busの寿命は upstream `dbus-daemon` とaddress仕様に従う:
https://dbus.freedesktop.org/doc/dbus-daemon.1.html
https://dbus.freedesktop.org/doc/dbus-specification.html#addresses
MousepadのNON_UNIQUE起動とGSettings設計根拠はL7a設計の固定0.6.3 source参照を維持する:
`docs/linux-real-app-l7a-design-20261109.md`。

## lease / drain / terminal

B2 wireのleaseは15秒。heartbeat/commandを受けるたび更新し、期限切れ、stdin EOF、Stop、
app rootの終了をそれぞれB1 `StopReason`へ写像する。helperに総runtime上限は置かない。
各commandはB2のstrict decoderを通し、eventも送信前にB2 parserへ再入力する。
protocol event/Recover receiptのstdout送信はnonblocking化し、2秒以内に進まないbackpressureを失敗として
所有・回収へ進める。local reader停止でremote helperが無期限にlease処理を止めない。

stdout/stderrはselectorで継続drainする。**累積bytesには上限を置かない**。
各streamは1MiB/sのtoken bucketと5秒分のburst allowanceを持ち、それを超えた場合だけ
`APP_OUTPUT_RATE_LIMIT` として回収へ移る。これにより低速ログを出す長時間GUIが累積量だけで停止しない。
terminal eventを出す前にProcessOwnerの二回empty scan、
pidfd close、全tracked root exit、全pipe EOF、C1 barrier/release、ephemeral cleanup、receipt fsyncを要求する。

## Recover

Recoverは現在のMousepad/GTK/D-Bus packageを検査せず、app/processを起動しない。
保存PIDへsignalせず、次の3種類だけをB2 exact 15-field receiptで返す。

1. `receipt`: 同じcontext/pins/sourceに束縛されたfinal receiptが存在する。
2. `boot_changed`: remote `barrier.json` のorigin bootと現在bootが異なる。旧processの消滅は証明できるが
   pipe EOFは証明できないため `pipes_eof=false` のまま返す。固定ephemeral treeだけをbounded cleanupする。
3. `unknown`: 同boot、barrier/receipt不明、I/O不確定。cleanup flagは全てfalseでpendingを維持する。

Prepared ACK喪失でB2のexpected bootが `-` の場合も、`barrier.json` のorigin bootを使って
boot-changeを証明できる。unknownをflock成功や同UIDという理由でcompleteへ昇格しない。
B2側はさらにchannel close、forward退役、router drainを確認して初めてnative journalをclearする。

## source closureとB2 final integration

C2で実在する追加product sourceは次の4件である。

- `scripts/linux/eidolon_linux_persistent_profile.py`（C1）
- `scripts/linux/eidolon_linux_desktop_v3.py`
- `scripts/linux/eidolon_linux_desktop_session_v3.py`
- `scripts/linux/linux-desktop-v3-bus.conf.in`

C2 PRではB2 inventoryの旧予定名 `eidolon_linux_real_app.py` を実C1名へ置換し、C2 sourceを
compiled closureへ入れる。ただし `integration.c1_commit` / `integration.c2_commit` はnullのまま保持し、
`require_complete()` は引き続き `APP_V3_SOURCE_INCOMPLETE` で製品実行を拒否する。
C2レビュー後の確定commitを使う最終commit pinningはB2統合として別途行う。
この分離により、C2 PR自身のSHAを自身のsource manifestへ埋め込む自己参照を作らない。

B2 bootstrapは `validate_bundle()` が返した `VerifiedBundle` とcompiled `source_hashes` を
そのままC2 entrypointへ渡す。C2がrepo checkoutやsys.pathから二度目のsourceを探索しない。

## failure状態

| failure | 結果 |
| --- | --- |
| Prepared前、C1 handle未取得 | exit 1。remote sessionを未知状態として残し、B2 journal pending |
| Prepared前、C1 handle取得済み | 同ownerで回収/releaseを試み、成功時のみRecover用receiptを残す。protocol成功eventは出さない |
| Inspect/package/settings/recovery拒否 | processを全回収し、可能ならreceiptを残すがwireはpending。初回失敗を成功へ書換えない |
| app/bus異常終了 | 同ownerでbounded drain/回収。完全証拠が揃わなければpending |
| pipe EOF未確認 | profile lockをreleaseせずpending |
| C1 release/barrier I/O failure | pending。永続profileを削除しない |
| ephemeral cleanup/receipt fsync failure | pending。B2 journalをclearする証拠を出さない |
| 同boot Recover unknown | pending。保存PIDへsignalしない |
| boot変更 | old process deathのみ証明し、`pipes_eof=false`。B2側のboot-change規則で扱う |

## 検査

`test_linux_desktop_v3_c2.py` はB2 receiptのexact parser、禁止されたsaved-PID signal/dynamic import、
固定bus templateを検査する。非root Linuxではさらに実filesystemを使い、bounded ephemeral cleanup、
別OS processによるstale session拒否、Prepared ACK喪失相当のboot-change Recover、
実`ProcessOwner` child + pipe EOF + C1 release順序を検査する。

既存B1/B2/C1、v2 frozen source/golden、A0 125秒保持を変更しない。C2 PR headではhost/Androidと
専用A0 CIも再実行し、skipを成功件数へ含めない。Quest実機・physical input・通常v3 acceptanceは
このPRの成功から補完しない。
