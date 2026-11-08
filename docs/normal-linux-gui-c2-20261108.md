# NX18 C2: 通常 Linux GUI の固定アプリからの接続準備

> この文書は保存commit `43767a4` 時点のcheckpointを記録する。後続のL1〜L4ではC2を取り込み、非同期終了event競合、通常lease、SSH actor、journal、helper喪失時の復旧を接続した。現在の実装と検証範囲は [L1〜L4](normal-linux-gui-l1-l4-20261108.md) を参照する。以下の「未配線」と旧4file manifestは当時の状態であり、現行はwire sourceを含む5file・protocol v2となる。

C1 の接続設定保存、B6 の固定 GTK 検査、C2a の live pidfd 子孫所有を調べ、最初の通常 GUI capability を **新しい GTK desktop demo 一種**へ絞る。接続先の profile / SSH 認証と、何を起動できるかの capability は別に扱う。任意 Linux アプリ、自由な shell / executable / argv / catalog 保存を解放する変更ではない。

B6 の `gtk3-interaction-fixture.c` とその既存 pins / source / 実機証拠は変更しない。従来計画の固定 foot 接続より先に、既に SHM / SDK の成立した GTK を使う小さな通常 GUI 経路を整える。foot と PTY の接続は引き続き別の受入れを必要とする。

## 今回の checkpoint

- `gtk3-desktop-demo.c` は独立した通常 GTK3 アプリ。Entry、別 toplevel の Dialog、Close を持ち、文字を入力しただけでは自動終了しない。検査用キー注入、自己テストの正解条件、60秒後の自己終了は設けない。Entry の本文は stdout / stderr へ書かない。画面には PID / nonce / capability などの実装情報を表示しない。
- Wayland display を必須とする。固定 run ID から app ID を作り、GTK 初期化前の prgname と map 後の app ID 設定を使う。起動 / 終了時の stdout は固定 metadata に限る。実際の GTK 表示や入力は別に観測する。
- `eidolon_linux_desktop.py` は CLI を持たない内部 adapter。通常 SSH owner が固定 bootstrap から使う前提であり、任意の path / argv / PID を渡す外部入口は設けない。
- profile session / revision / binding / nonce から private scope を作り、さらに bundle と固定 app ID を含む capability revision を計算する。scope / revision / app ID が揃わない起動選択、追加 argv、foot や任意アプリへの付替えを拒否する。
- 最初の catalog は `linux-desktop` namespace の `gtk3-desktop` 一項目、mode=gui、instance limit=1。global Board の9窓制限を緩めない。接続済み profile だけでこの catalog を生成・公開せず、実 Waypipe の version 照合後の `Instance.inspect()` を通す。
- C2a `ProcessOwner` を専用 single-thread helper 内で作り、起動前の固定 version process と一つの Waypipe/GTK instance を所有する。子孫の setsid を含む pidfd 回収、終了と pipe EOF を分ける。pending は同 owner を保持し、新しい起動を拒否する。

この段階では lease / heartbeat / SSH protocol / StreamLocal / durable journal / runtime / 通常 Host / profile UI / launcher はまだ接続していない。helper class の `complete` は **子孫と pipe の回収**だけを意味し、remote directory/socket/receipt、native/SDK、通常 UI の復帰を完了扱いしない。終了しない通常アプリは、次の SSH owner の有限 lease / stop によって管理する。

## 独立した bundle

既存の Linux base bundle に対して、次の operator を明示実行する。実際の配備は端末操作を担当する owner が行い、この checkpoint の検証では既存 bundle / sshd / profile / 鍵へ書き込んでいない。

```sh
scripts/build-linux-desktop-bundle --bundle-directory "$LINUX_PRIVATE_BUNDLE"
```

新たに追加するのは `desktop-bundle.json`、通常 demo の C / binary、Python adapter / pidfd owner、`desktop-build/` だけである。base の helper / Waypipe / SHM / B6 GTK / 既存 manifest は変更しない。既存名が一つでもあれば上書きや自動 rollback をせず拒否する。中断した場合は新規物の所有と hash を確認してから配備をやり直し、旧 bundle / pins の回収証拠には流用しない。

manifest は schema / backend / revision / base hash / GTK version / 四つの固定 file hash の完全一致を検査する。個々の file は同 UID / single link / nofollow / private mode / 上限サイズを要求する。adapter / pidfd source / desktop C は呼出元が capture した source hash にも一致させる。共有 GTK library 全体の固定性を compile version だけで保証しない。通常の公開診断へ private profile / binding / pins をコピーしない。

## 検証

host の固定 fixture で次を確認した。

- context / source / bundle / capability revision の差替え、重複 JSON、未知 field、不正 scope と任意 app を拒否。
- capability 検査前の起動と二重起動を拒否。
- 専用 child が作った別 session の子孫を回収し、pidfd 0 / pipe EOF まで確認。
- scan 欠測による pending の間は owner を保持し、新しい起動を拒否。同 owner の明示再回収だけで完了。
- 出力上限を超えた child も本文を保存せず停止・回収可能。
- version 検査の timeout / 出力超過で作られた setsid 子孫と pipe を同 owner に保持し、実 EOF と回収後も元の検査エラーを区別する。spawn 失敗を含め検査は一回だけで、自動再送しない。
- temp の固定 bundle へのビルド、base hash 不変、既存物の上書き拒否。

Python 11件＋builder 2件が成功した。C は `-Wall -Wextra -Werror` と GTK 3.24.52 でビルドし、固定 `--version` と Wayland display 不在時の exit 69 を確認した。これは GUI / SHM / SDK の実表示・操作の成功ではない。

初回 C compile は GTK header path を誤り失敗し、既存 GTK source と同じ公開 `gdk/gdkwayland.h` へ訂正した。追加 fixture では、短い close timeout に対して pidfd owner の既定 grace が長過ぎる API 不整合を検出し、`grace=min(250ms, timeout/2)` に修正した。peer review では、version 検査失敗時に pipe を明示 close するだけでは EOF 観測にならない点を確認し、失敗した検査 child と pipe を同 owner の終了処理へ移した。失敗ログと修正後の結果は別々に保存した。

## Transport 型の checkpoint（未配線）

`native/transport/src/linux_desktop` は通常用の独立型として追加した。debug 専用 B4/B6 の pins を受け流さず、`backend=linux_desktop` の私有 pins、Linux SSH profile の不変 binding、session / revision / remote UID / nonce へ束縛する。GTK / adapter / pidfd owner source の埋込み hash は native source digest の入力にも含める。profile の保存やこの型の生成だけでは SSH 認証・catalog・描画成功を意味しない。

Protocol は prepared → inspect ACK / 固定 catalog →一回の start ACK → heartbeat / stop を分ける。command bytes を得た時点と remote ACK を区別し、未応答の再送や二重 enqueue を拒否する。event と request の sequence は厳密に照合し、ID / revision / sequence を整数へ丸めず十進文字列で扱う。scope と catalog revision の計算は実 Python adapter との相互 fixture で照合した。

catalog は source / binary / bundle / Waypipe 0.11.3 の一致と一つの `gui` entry だけを受け付ける。任意 argv、terminal mode、旧 profile、未知 field、重複 JSON、16KiB 超、順序逆転は拒否する。Pending は終端の失敗状態として新起動を禁止する。Stopped も process / EOF / remote directory / receipt の自己申告を照合した段階であり、SSH channel / StreamLocal / journal / native / SDK の回収証拠とは別である。まだ transport の SSH actor、helper の wire loop / lease、journal へこの型を配線していない。

限定 Rust 8件と `cargo clippy -p eidolon-transport --lib -- -D warnings` が成功した。共通 host target の owner lock 内で自身の entrypoint mtime を更新し、実 test list の8件と実行8件の一致を確認した。初回は隔離 tree に generated Smithay path がなく拒否され、既存の読み取り用 generated directory を参照して解消した。次に sha2 0.11 の digest に LowerHex 実装がなく compile が失敗したため既存方式の byte 別 hex 化へ訂正し、clippy の大きい enum variant 指摘は catalog を Box にして解消した。各失敗ログを成功結果とは別に保持した。

## 続く接続

1. 不変な profile/capability を受ける SSH owner と `.eidolon-desktop` の専用 lease/receipt/journal を接続する。既存 Termux / B4 / B6 journal は照合・削除・付替えしない。失われた owner の保存 PID へ後日 signal しない。
2. 一つの認証済 SSH handle / StreamLocal router 上で固定 catalog / start / stop を扱い、全 report の session / nonce / scope / sequence / UID と期限を検証する。未知 owner は pending を残す。
3. native AppMode / launch receipt / Board / surface budget を使い、同 full target の通常 SDK 描画・英数入力・Dialog・Close と回収を実測する。profile SSH 成功、catalog の表示、helper 自己申告を GUIReady や描画成功へ昇格しない。
4. 接続選択 UI / launcher へ、現在の profile と実行可能 capability を区別して表示する。旧接続回収前に、起動ボタンで接続先を無断変更しない。Termux の通常経路と実機回帰を保つ。
