# NX18 B6b: 固定Linux GTKとtransport/runtimeの接続

[B6計画とhelper](remote-linux-gtk-b6-plan-20261108.md)の次として、固定GTKをB4/B5と同じ認証済み
SSH handle/StreamLocal routerへ接続する。通常Linux GUI/catalog/任意shell/接続先選択UIは有効化しない。
`ProbeReady`を維持し、debug featureの固定commandだけがGTKを起動する。

## private configと不変な束縛

`RuntimeConfig.debug_linux_gtk_pins`は最大4096bytesのJSON **文字列**。
nested JSONを文字列のままparserへ渡し、重複keyを失わない。`debug_probe_profile_v2`が必須。
通常snapshot/diagnosticsにはこのpin本文をserializeせず、RustのDebugも値を伏せる。

```json
{
  "schema": 1,
  "base": {
    "schema": 1,
    "bundle_sha256": "<既存base manifestの64hex>",
    "waypipe_sha256": "<固定Waypipe 0.11.3の64hex>",
    "fixture_sha256": "<既存SHM fixtureの64hex>"
  },
  "gtk_bundle_sha256": "<GTK拡張manifestの64hex>",
  "source_sha256": "<このAPKに組み込んだ固定Cの64hex>",
  "binary_sha256": "<Linux GTK binaryの64hex>",
  "gtk_version": "3.24.52"
}
```

この例はschema説明用で、そのまま実行できる設定ではない。
base schema1は従来のSHM pinsと同じ。GTK source digestはnativeに埋め込んだ
`scripts/fixtures/gtk3-interaction-fixture.c`と完全一致を要求する。
GTK3.24.x build versionを捕捉するが、shared library全体の不変性を保証する値には使わない。
helperと固定Cは両方APK source digestの必須入力とする。

controller mailboxはcase/pinsを所有し、actorは接続開始時に確定したsession/profile binding/revision/UIDと組み合わせる。
別SSH handleへ付け替えず、Termux v1 credential/profile/helperへのfallbackを行わない。
exec直前にjournalを永続化し、その後bootstrapでbase/helper/GTK manifestと固定source/binary/versionを再照合する。

## 固定commandとAppMode

```json
{"schema":1,"type":"linux_gtk_probe","request_id":"101","session":"1","nonce":"0123456789abcdef","case":"input"}
```

caseは`input`/`dialog`/`resize`の三つだけ。nonceはhelperと同じ16hex、IDはdecimal文字列。
host/path/argv/任意JSONをcommandへ追加できない。SDK側の外部nonce32hexとの対応はB6cが捕捉する。

- `LinuxGtkInput` / `LinuxGtkDialog` / `LinuxGtkResize`という専用AppModeを使う。
- callback clockはtarget-driven。Pattern100のhash条件やvirtual callbackへ通さない。
- 同時app/probe/helper/forwardがあると拒否し、一つのruntimeでは一つの固定caseだけを開始する。
- 通常`StartApp`/`AppProbe`とTermuxの`Controller.start_app`から、このAppModeを起動できない。
- `debug-fixtures` featureが無いlibraryは、debug assertionsが有効でも新config/commandを拒否する。
- このcheckpointはAndroidのB5 lease/UI/input導線をまだ追加しない。固定GTKは操作されなければ有限timeoutで終了する。

## reportの検査と意味

`gtk_report`は固定Cの本文なしcounterと、helperがlive時に捕捉したPID/starttime/group/UIDだけを運ぶ。
Waypipe group leaderとGTK PIDは分け、GTK process.groupがchild_started.groupに一致することを検査する。
最初のPID/starttimeを捕捉したら、同一接続の後続reportを別processへ付け替えない。

nativeでもcase/run_id/main・dialog app_id、型、sequence、最大256行、counter逆行、geometry上限、
host selftest/physical_input_verified=false、ready→state/finalの順序を検査する。
final後の追加report、不明field、重複key、helperの総件数との不一致を拒否する。
stderr本文は保存せずbyte数だけ。GTK channel全体は1MiB、1行16KiB、helperの元counter出力は512KiBに制限する。

| 観測 | 意味 |
| --- | --- |
| `transport.linux_forward.gtk.helper_verified` | 固定counter完了・自然終了・helperの自group/state回収が整合した |
| `app.remote_gui_verified` | 同session/nonce/caseのtransportが成功終端へ到達した |
| `app.copies/configures/acks/releases/callbacks` | nativeが実際に処理したSHM/Waylandの独立counter |
| render target / SDK draw / pose ACK | B6c以降で同fulltargetに束縛して確認する実表示の証拠 |

GTKは`pattern_verified=false`、`frames=0`のまま。0はGTKの実描画枚数が0という意味ではなく、
Pattern100の検証枚数をGTKへ流用していないことを示す。
helper counterの自己申告やSSH転送量だけでは、SHM/SDK/入力成功と判定しない。

## GTK専用journalと回収

`linux-gtk-probe-state/pending.json`（schema2）に、session/profile binding/revision/UID/nonce、
base/helper pinsに加えてcase/GTK manifest/source/binary/versionを保存する。
旧`linux-probe-state`（schema1）と旧Termux journalは読まず、削除せず、流用しない。
いずれもprivate file・排他的publish・fsync・内容/device/inode同一性を確認してからclearする。

```json
{"schema":1,"type":"linux_gtk_recover","request_id":"102","session":"1","nonce":"0123456789abcdef","case":"input"}
```

明示recoverは同じcaptureのreceipt照会だけを行う。保存PIDへの後日signalや、別case/profileへのcleanupをしない。
remote receiptが回収済みでも、GUI/SHM/SDKの成功証拠は再生成しない。
journal無しは`queried=false,pending=false,receipt_verified=false`であり、「回収不要」と「回収成功」を分ける。

通常成功・取消とも、helper終了/pipe EOF/exit、StreamLocal取消・router active=0、SSH channel回収を別々に観測する。
途中失敗や期限切れでは固定stopを送り、回収が不明ならjournalを残す。GTK operationは90秒、stop後は8秒の有限deadline。
helper本体もleaseと最大時間を持つ。次callback待ちで期限を自動延長しない。

## 検証と残り

hostのpure/固定SSH testは、pin/case/PID/順序・summary不整合、cancel、channel EOF/exit、
GTK専用journal回収、通常ProbeReady/Termux経路の拒否、featureoff拒否を検査する。
固定SSH testのcounterは合成であり、GTK実行・SHM・SDKの実測を主張しない。
実helperのPID/executable/groupやprivate state回収はB6aのprocess fixtureで別に確認する。
GTK caseはqueued/preparing時点で捕捉し、exec前の取消やjournal拒否でも失敗を同じcaseへ返す。
helper開始後のreportが来るまで、失敗理由を見落として待機する状態を作らない。

チェックポイントでは次を確認した。

- Rust 32件: Linux transport 28（GTK専用9を含む）、GTK mailbox 1、model wire 2、featureoff boundary 1。
- Python 32件: Linux helper/GTK関連27、native source digest関連5。
- core/transportのall-targets clippy `-D warnings`、Rust format、`git diff --check`。

```sh
cargo test --offline -p eidolon-transport --features debug-fixtures linux_ -- --nocapture
cargo test --offline -p eidolon-transport --features debug-fixtures gtk_mailbox_ -- --nocapture
cargo test --offline -p eidolon-model linux_ -- --nocapture
cargo test --offline -p eidolon-core --no-default-features --test remote_probe_boundary -- --nocapture
cargo clippy --offline -p eidolon-core -p eidolon-transport --all-targets --features debug-fixtures -- -D warnings
```

複数worktreeでhost Cargo targetを共有する場合は、共通owner lockの下で対象treeのworkspace entrypointの
mtimeを更新し、build/testと`--list`のtest名・件数照合まで同じlockで実行する。
このcheckpointの正式記録はその条件を満たす実行に限定した。途中で別lock pathを取得していた
検証ラッパーを修正し、旧ログは理由付きで保持して再検証した。製品sourceや依存cacheは取り替えていない。

次はB6cでB5のleaseとprivate configを使い、固定GTKをSDKへ表示する。
synthetic inputは所有caseのfulltarget/scene/runtime/epoch/inputIntent/最新geometryを同時に検査して送り、
通常の入力gateを開放しない。input/dialog/resizeを別nonceで確認し、main/dialog退役と通常SDK復帰までを受入れに含める。
HMDの見え方・物理操作・日本語IMEは最後の別gateとして残す。
