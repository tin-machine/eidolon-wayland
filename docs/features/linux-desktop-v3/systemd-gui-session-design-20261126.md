# Ubuntu の GUI セッションを systemd で管理する詳細設計

2026-11-26 JST に作成した設計案。対象は Quest の通常ランチャー、Ubuntu Desktop の実 GUI アプリ、GUI 全体の無応答時設定。基準は main `9bec03932121f46bae41db6f2b8b10168f71728a`。設計前の観測と再現手順は[実験記録](systemd-gui-session-host-evidence-20261126.md)、設計後の到達点は[実装と検証](systemd-gui-session-implementation-evidence-20261126.md)を参照。本書は手動で参照する設計で、既存の [v3 統合計画](linux-real-app-v3-integration-plan-20261111.md)・wire revision・受入結果は従来どおりとする。

## 1. 利用者と決めた動作

| 入力・出来事 | 期待する結果 |
|---|---|
| アプリを明示終了 | そのアプリの停止を直ちに要求する。通常終了を待つ短い処理時間はあり得る。無応答猶予の満了は待たない。 |
| 窓を閉じる | 当該窓に終了要求を渡す。Chrome は他の通常窓があれば継続し、最後の窓が閉じたら対象セッションを回収する。ダイアログで終了を取り消した場合は窓が残る。 |
| SSH の GUI 転送は存続、Quest の生存通知だけが停止 | **全 GUI アプリ共通、既定60分**。最後にホストが受理した通知から設定時間だけ維持する。期限内の復帰は同じセッションを継続、期限超過はホストが自律的に終了。 |
| SSH の GUI 転送が実際に切断 | 猶予中でも回収に進んでよい。次回は新規起動。消えた窓・未保存編集を再構成する機能は作らない。保存済みの文書・プロファイルは保持する。 |
| フォーカスを外す、棚やシステムパネルを開く | それだけでは終了しない。ユーザー操作の頻度をタイムアウトに使わない。 |
| 同じ Ubuntu 接続先で Chrome を再選択 | Quest 専用の同じブラウザーに**新しい通常窓**を一つ開く。デスクトップで使っている Chrome の窓・プロファイルは別。 |
| Chrome のキャッシュ | Ubuntu のセッション専用 tmpfs。永続プロファイルはストレージ。個別の容量上限値は実使用後に決める。 |
| 旧方式からの切替 | **起動中の旧セッションを移植しない**。対象を旧方式で終了・回収して切り替える。確認できない旧セッションだけを個別に扱う。 |

以降の設定範囲、更新間隔、コマンド名、保存形式、段階導入は実装のための**設計案**。プロトコルに既に存在する API の名前と、新設する名前を分けて記す。

## 2. 現行コードの入口と差分

| 作業 | 先に読む現行実装 | 新方式で変更する境界 |
|---|---|---|
| 設定と配線 | [ControlPanel.kt](../../../android/app/src/main/java/io/github/tinmachine/eidolon/ControlPanel.kt)、[ConnectionSettings.kt](../../../android/app/src/main/java/io/github/tinmachine/eidolon/ConnectionSettings.kt)、[LinuxRuntimeConfiguration.kt](../../../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxRuntimeConfiguration.kt)、[RuntimeConfig](../../../native/model/src/commands.rs)、[Android registry](../../../native/android/src/registry.rs) | 共通設定を主・追加 Ubuntu・Termux の実行経路へ渡す。現行の v3 pins/envelope に未知キーを足さない。 |
| 起動と窓 | [NativeSessionHost.kt](../../../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt) の `launchShelfApp`、[CompanionLinuxSession.kt](../../../android/app/src/main/java/io/github/tinmachine/eidolon/CompanionLinuxSession.kt)、[LauncherCapacity.kt](../../../android/app/src/main/java/io/github/tinmachine/eidolon/LauncherCapacity.kt)、[窓の履歴](../../../native/core/src/linux_desktop_v3.rs) | 初回起動と既存 Chrome の追加窓を分岐。主/追加 runtime に同一の判定を使う。v3 の固定 main surface 判定を一般の Chrome 窓数に転用しない。 |
| SSH と Waypipe | [host_key.rs](../../../native/transport/src/host_key.rs)、[forward.rs](../../../native/transport/src/forward.rs) の `Router::arm`、[v3 の SSH owner](../../../native/transport/src/linux_desktop/v3/owner.rs)、[Waypipe](../../../native/core/src/waypipe.rs) | 同じ認証・転送・local compositor を使い、Ubuntu の起動/停止だけを新しい actor に分ける。1 router は同時に1 route。別アプリは独立した runtime/route。 |
| ホスト起動とカタログ | [v3 serve](../../../scripts/linux/eidolon_linux_desktop_session_v3.py)、[Desktop Entry の選択](../../../scripts/linux/eidolon_desktop_entries.py)、[C1 の保存領域](linux-real-app-c1-handoff-20261110.md) | 新 backend 用の起動では Ubuntu に Eidolon 専用の常駐・単発ヘルパーをインストールしない。標準コマンド、Waypipe、アプリ本体を使用。旧 C2 の配備を新カタログの前提にもしない。 |
| 旧終了記録 | [v3 journal](../../../native/transport/src/linux_desktop/v3/journal.rs)、[remote recovery](../../../native/transport/src/linux_desktop/v3/recovery.rs)、[local retirement](../../../native/core/src/v3_retirement.rs) | 旧記録は旧 reader だけが読む。systemd の unit 消失から v3 の `pipes_eof` や完了 receipt を合成しない。 |
| Termux | [TermuxShelfSession.kt](../../../android/app/src/main/java/io/github/tinmachine/eidolon/TermuxShelfSession.kt)、[forward_app.rs](../../../native/transport/src/forward_app.rs)、[launcher.rs](../../../native/transport/src/launcher.rs)、[supervise](../../../scripts/termux/eidolon-session.py) | 共通の猶予を既存の Termux 所有者へ渡す。Ubuntu 用の systemd を Termux に導入しない。 |

現行 Ubuntu v3 の wire は [LEASE_MS = 15000](../../../scripts/linux_desktop_v3_wire.py)。Rust 側には別に5秒の heartbeat 間隔・応答期限がある。Termux の `supervise` は既定60秒。これらの値をまとめて3600秒に置換すると、起動失敗や制御要求の応答まで1時間待つ不具合になる。今回延ばす対象は**稼働中の GUI の無応答猶予だけ**である。

## 3. 共通設定と生存通知

### 保存と入力

- 新設案 `GuiSessionPolicy`。Android のアプリ専用 SharedPreferences に整数 `gui_unresponsive_grace_minutes` を1個保存する。接続先・資格情報から独立したアプリ共通設定。設定画面の接続先選択より手前に「GUI アプリの無応答時の待機時間（分）」を置く。プリセット 5 / 15 / 60 と、整数 1〜1440 の直接入力を用意する案とする。
- 未保存・旧バージョンは60。ユーザー入力で空、0、負数、小数、範囲外なら保存せず、直前値とエラーを表示。壊れた保存値は既定60で読み込み、診断には理由だけを残す。
- 次の**新規 GUI セッションの開始時**に値を固定する案とする。「次回起動するアプリから適用」と表示。既存 Chrome に窓を追加しても、その Chrome が使用中の値を維持する。これにより、待機中に設定を短くした瞬間に未保存の窓が消えない。
- Rust に渡す新フィールドを `gui_unresponsive_grace_seconds: u32` とする案。変換は保存済みの分に対して1回だけ行う。受理範囲は60〜86400の60の倍数。旧 JSON のフィールド欠落時は3600。不正な明示値は起動前に拒否。runtime がアプリより長く存続する経路では、新規 launch ごとの command に snapshot を渡す。初期化時の `RuntimeConfig` だけに閉じ込めない。

### どの信号で更新するか

1. `MainActivity` の owner/scene 世代を確認した上で、活動中のアプリ本体から10秒ごとに生存 tick を1回発行する案とする。物理キー、描画フレーム数、視線を tick として扱わない。メニューや窓単位のフォーカス移動では抑止しない。Activity が pause/スリープしたら新しい tick の生成を休み、実際の resume で直ちに1回発行する。既存の入力リセット・SDK 所有者交代は維持する。
2. Rust は現在のセッションと世代に属する新しい tick だけを Ubuntu/Termux に配る。I/O スレッドや SSH keepalive が自動で更新し続けないこと。pause 前の滞留 tick は捨て、復帰後にまとめ送りしない。送信中1件＋最新1件まで。古い owner の resume/heartbeat は無視する。
3. ホストで受理した時刻が基準。SSH 制御コマンド一件のタイムアウト・入力フォーカス消失は転送断の証拠にしない。応答不明なら新しい tick で照会し、アプリも timer も作り直さない。データ転送自体の EOF/SSH session 終了、明示停止、期限発火は終了側を優先する。終了済みの世代を遅い ACK で復活させない。
4. Termux では heartbeat の処理は既存 supervisor に残し、起動時の認証済み要求に policy を渡す。新形式例は `set_gui_lease {nonce, seconds}` を prepare 後・GUI start 前に1回 ACK、新旧は配備済み helper digest/capability で選ぶ。稼働中は同じ nonce の正しい heartbeat の受理で期限を更新する。制御 FD の EOF では直ちに既存 stop 経路へ進む。複数ウィンドウで1プロセスを共有する場合の期限単位はその所有セッション。携帯ホスト自身の suspend 時間も算入するため、GUI 猶予の経過計測には Linux の `CLOCK_BOOTTIME` を使い、復帰時は滞留通知より先に失効判定する。IME・catalog-only・テスト用 workload の期限は個別の現行契約を使う。

Ubuntu の標準 timer はホストが稼働中に計時する。この設計の「1時間」は Quest のスリープまたは無応答に対する時間。Ubuntu マシン自体のサスペンドから起こす要件はない。Ubuntu 再起動後は転送断として扱う。

### 値を伝える実装例（新設案）

| 境界 | 渡す値と受理条件 |
|---|---|
| 設定 UI → store | `saveMinutes(Int) → Saved / InvalidInput / WriteFailed`。保存成功後だけ UI の有効値を更新する。アプリ終了や接続設定の保存 barrier は起動しない。 |
| Kotlin → Rust | 新規起動時に `{request_id, runtime, expected_session, app_id, catalog_revision, gui_unresponsive_grace_seconds}` を既存のコマンド envelope に合わせて型付きで渡す。これは論理フィールド名。JNI 境界の既存の上限・ID 表現・strict decode を維持する。主 runtime で設定した値が、追加 runtime 作成時の config コピーで消えないことを試験する。 |
| Activity → 各 actor | `LivenessTick(sceneEpoch, ownerGeneration, sequence)`。sequence はその世代内で単調増加。最新の有効な tick を各 actor が1回ずつ消費する。同一 tick の再配信で期限を延長しない。 |
| actor → 診断/UI | `backend, phase, appliedGraceSeconds, reason, liveRootCount, localRetired, remoteRetired`。設定画面の次回適用値と実行中セッションの適用値を個別にテストできること。 |

Termux 改修時は [launcher.rs](../../../native/transport/src/launcher.rs) の `beat_every` と `deadline`、[supervise](../../../scripts/termux/eidolon-session.py) の厳密な control frame の許可表・停止理由の双方を更新する。prepare/start は従来の短い制御期限、GUI 開始後は有効な tick の停止だけで旧60秒 deadline から切断しない。nonce 不一致、秒数の型違い、重複設定、start 後の再設定を拒否する。prepare だけ行って GUI を起動しない接続を1時間保持しない。新 helper 対応の digest/command と旧 helper での従来の起動・回収を別 fixture にする。

## 4. Ubuntu の所有単位と起動

### 識別子と保存先

| 寿命 | 内容 |
|---|---|
| 接続設定 | 認証済みホスト鍵・ユーザー、installation、storage ID を既存の検査で結びつける。表示名や IP だけでプロファイルを選ばない。 |
| 永続プロファイル | 新 backend 専用のアプリ root。例えば、Ubuntu の検証済みユーザーホームの `.local/state/eidolon-gui/<installation>/<storage-id>/<app-key>/`。0700。直下に兄弟ディレクトリ `home/`、`config/`、`data/`、`state/`、`Documents/` と、起動時の排他用 lock を置く。Chrome の `--user-data-dir` は `config/google-chrome`。`HOME`・`XDG_CONFIG_HOME`・`XDG_DATA_HOME`・`XDG_STATE_HOME`・初期 cwd を各ディレクトリに明示する。 |
| 1起動 | Quest で生成した128ビット乱数を小文字 hex 32字にした `g`。作成する unit は `eidolon-gui-<g>.service`、期限は別 stem の `eidolon-exp-<g>.timer/.service`。世代を再利用しない。ホスト boot ID・実行時の systemd InvocationID・Quest の runtime handle/session/scene を別フィールドとして管理。 |
| 1起動のファイル | 検証済み `XDG_RUNTIME_DIR=/run/user/<uid>` の下の `ei-<g>/` に `p`（SSH 転送）、`r/w`（Wayland）、`r/b`（専用 D-Bus）、`tmp/` を置く。Chrome の `cache/` もここ。`XDG_RUNTIME_DIR` は子の `r/`、`TMPDIR` は `tmp/`、`XDG_CACHE_HOME` は `cache/`。すべて systemd の `RuntimeDirectory` で作り、停止時に削除する。 |

`app-key` は正規化した Desktop Entry ID の SHA-256（ファイル名用 hex）。安定した永続 ID は既存の storage 型で検証する。SSH 接続時の数値 UID は非rootを要求。実行前に runtime の filesystem が tmpfs か、所有者・mode・socket path のバイト長（既存 router 上限103）が正しいかを検査する。Chrome の `--user-data-dir` を `XDG_CONFIG_HOME` の配下にすることで、Linux の Chrome がキャッシュを別の `XDG_CACHE_HOME` に対応づけられる。[Chromium の配置規則](https://chromium.googlesource.com/chromium/src/+/main/docs/user_data_dir.md#user-cache-directory)

tmpfs の空き容量は同じユーザーの他のサービスとも共有する。今回決めているのは Chrome キャッシュの媒体。ブックマーク、Cookie、ダウンロード、文書への保存先は移さない。新 backend の初期対応で他アプリの既存ディスクキャッシュを一律 tmpfs 化しない。一般アプリに必要なら XDG_CACHE_HOME はそのアプリのディスク側 cache に割り当てる。Chrome 専用上限の決定材料は §9。

永続ディレクトリは認証した非root UID の home と検証済み storage ID を起点にする。既存の他人所有、途中の symlink、共有書込可能な親、型違いの lock では起動を拒否し、既存パスへの一括 chmod/chown や再帰削除で直さない。unit の作業ディレクトリをサーバーの実ホームにしない。tmpfs 容量問題の測定に `RuntimeMaxSec` や `MemoryMax` をキャッシュ専用上限として代用しない。

### 事前確認とカタログ

- Quest の Rust 側に独立した backend 値 `ubuntu_systemd_v1`（新設案）を持たせる。既存 v2/v3 の設定を編集して別実装を装わない。接続先の host-key pin と現在の credential capture を照合し、認証した SSH 上で `systemctl --user show`、ユーザーマネージャーへのアクセス、`systemd-run`、Waypipe の版・配備 pin、`dbus-run-session`、`flock` と選択アプリの実行可能性を検査する。私設 D-Bus を起動する前に、ログイン側のユーザーマネージャーに対して systemctl を実行する。
- 最初の縦断試験は Google Chrome stable の明示的な1項目。標準の Desktop Entry ID と確認済み実行ファイルを対応づけ、`--new-window` 等の外部入力を文字列連結しない。通常版の棚に出すのはカタログ・再選択・回収がそろってから。
- 一般アプリ段階では、固定された XDG applications ルートから認証済み SSH/SFTP で Desktop Entry を読み、Quest 側でリストを構築する。ホスト環境の XDG_DATA_HOME/DIRS に従い、未設定時はユーザー applications を優先し、次いでシステムの各 applications を探索。同一 ID の shadowing（上位の Hidden も含む）、Type/NoDisplay/TryExec、作業ディレクトリ、Exec の引用とフィールドコードの扱いを[現行の describe/launch_argv/resolve_selection](../../../scripts/linux/eidolon_desktop_entries.py)の fixture と比較する。Desktop Entry は入力データである。argv は要素の配列のまま保持し、Exec をシェルとして評価しない。
- カタログ結果は `{id, label, revision, launchable, reason}`。revision は entry bytes・解決した executable の識別・cwd の識別に束縛し、起動直前に再検査。作業上限の初期案は1 entry 256 KiB、一覧 4096 件、合計16 MiB。超過時は不完全な一覧を確定版にせず理由を表示。Terminal 必須、D-Bus の外部アクティベーションしかないもの、扱えない Exec、cgroup の外へ起動を委譲するものは理由を表示し、対応済み一覧と区別する。現在使えるアプリを失う場合は切替対象を広げず、その entry の互換試験を追加する。

### 通常起動の手順

以下は新 actor が組み立てる操作の仕様。山括弧は内部で検証・生成した値である。ユーザーが貼り付けるシェルコマンドではない。

1. **予約**。ローカル容量、旧方式の一度限りの切替確認（§8）、catalog revision を確認。新世代を確保し、対象 host/storage、モード、timeout、unit 名、phase=`reserved` を Quest の backend 専用 journal に atomic replace + 親 fsync してからリモートを変更する。
2. **ホスト側の直列化**。起動の短い操作は、検証済みの永続 root に置いた操作用 lock で直列化する。`flock --close` 等で子サービスにこの lock を継承させない。起動コマンドはこの lock を取ったまま「同じ世代の停止 timer を先に作成 → アプリサービスの start job を投入」まで行い、すぐ解放する。timer の発火側も同じ lock 取得後にその世代だけを停止する。これにより、発火がサービス登録を追い越して空振りする穴を塞ぐ。SSH 応答が失われたら同じ Start を再送せず §5 の照会へ。別のアプリとグローバルに直列化しない。
3. **期限を武装**。`systemd-run --user --collect --unit=eidolon-exp-<g> --on-active=<秒>s --timer-property=AccuracySec=1s …` で、発火時に既存世代の `systemctl --user stop <app-unit>` を行う一回限りのサービスを登録する。stop は正規化した unit 名に限定し、app を起動する依存関係を expiry に付けない。発火後の timer の残存も回収対象。通常時は新しい tick で稼働中の timer だけを更新する（§5）。
4. **サービスを作成**。`systemd-run --user --no-block --collect --unit=<app-unit>`。主要 property は `Type=exec`、`KillMode=control-group`、`UMask=0077`、`RuntimeDirectoryMode=0700`、`RuntimeDirectory=ei-<g> ei-<g>/r ei-<g>/tmp ei-<g>/cache`、`TimeoutStartSec=15s`、`TimeoutStopSec=5s`（短い内部期限の初期案）。稼働の全期間に `RuntimeMaxSec=猶予` を設定すると正常利用中も強制終了するので期限の実装に使わない。Chrome 起動の排他は操作用 lock と別の安定したプロファイル lock を `flock -n` の親プロセスでプロセス存続中保持し、競合は `PROFILE_BUSY`。別世代へ勝手に合流させない。
5. **転送を準備**。systemd が専用ディレクトリを作った後、短い `ExecStartPre` が `p` を待つ。Quest は既存の local Waypipe client/socket を用意し、同じ認証済み接続で router を arm、リモートの `p` へ Unix ソケット転送を登録する。待ち合わせは上限付き。既存ソケットを unlink して横取りしない。unit の作成成功、ソケット作成、アプリ表示を別の結果にする。
6. **プロセスを実行**。サービスのコマンド列は下記の順。Chrome の安定プロファイルにはテスト用の `--disable-sync` 等を引き継がない。SSH exec は文字列1本なので、値の型を検証した上で一つの共通エンコーダでリモート argv の各要素を引用する。systemd の環境変数展開・specifier 展開も別層としてエスケープする。シェルや systemd の構文を含むラベル、パス、Desktop Entry フィールドを用いた round-trip test を作る。
7. **表示を確定**。起動 ACK は `starting`。同じ origin・runtime の新しい通常 toplevel が Mapped、対応する SDK panel が採用された時点で棚に「起動済み」を出す。採用失敗や起動期限超過は、その世代を stop してから失敗を返す。他の窓へ自動でフォーカスを移さない。

```text
flock -n <profile-lock>
  dbus-run-session --config-file=<private-bus-config> --
    <pinned-waypipe> --no-gpu --compress none --threads 1
      --socket <runtime>/p --display <runtime>/r/w server --
        <chrome> --ozone-platform=wayland --user-data-dir=<persistent-config>/google-chrome
```

行は可読性のため折り返した1本の argv。専用 bus 設定は固定テンプレート、EXTERNAL 認証、session 専用 socket、ホストのサービス自動起動ディレクトリを含めない。例えば systemd の `SetCredential=` で XML を unit に渡し、サービス内の credentials directory から読む。版ごとの展開と mode は Host テストで確認する。実験は同等の一時 XML を使用した。[systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html) / [systemd のプロセス終了](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)

## 5. 状態機械・終了・再試行

これは新 backend 内部の状態。既存 B1 の enum や v3 の `StopReceipt` に値を追加する指示ではない。状態更新の直列化単位は `(host binding, app-key, generation)`。同一世代で停止要求を受理したら不可逆とする。

| 現状態 | イベント | 操作と次状態 |
|---|---|---|
| Idle | 初回 Launch | journal を先に記録 → Starting。 |
| Starting | Mapped と SDK 採用 | 取得済み boot/InvocationID を記録 → Running。タイマーの更新を開始。 |
| Starting | 起動失敗・取消・応答喪失 | 未起動を断定できる場合だけ予約を戻す。それ以外は Stopping または Reconcile。 |
| Running | 新しい生存 tick | 稼働世代を照合し、まだ待機中の timer を `systemctl --user try-restart`。応答を確認。 |
| Running | 生存通知停止、転送は存続 | ホストで既に武装してある期限まで維持。Quest が停止していてもタイマーは動く。診断上は Waiting。 |
| Waiting | 期限前に同じ生存源が復帰 | 現在の boot/InvocationID、app が active、timer が waiting、終了 job が未発行、と照合して一度だけ更新 → Running。データ転送を作り直さない。 |
| Starting / Running / Waiting | 正しい世代の Stop、アプリ終了、転送断、または期限発火 | 停止理由を一度記録 → Stopping。同時発生時も停止は1回。 |
| Stopping | ホスト／Quest 両側の終了を観測 | 完了を永続化 → Retired。再利用できるのは枠。世代 ID は捨てる。 |
| Stopping | SSH 不達、照会エラー、状態が一致しない | Reconcile。個別の起動だけを回収待ちとして表示する。 |
| Reconcile | 認証済み再照会 | 存続する同一所有物を停止するか、不在を確かめて Retired。新世代のリソースは操作しない。 |

**競合の処理**。期限 timer は「停止を依頼する一回限りの unit」を起動する。発火と更新が競合したら、既にキューに入った stop を勝者とする。更新は start job を作らず、更新応答だけで Running を確定しない。timer が elapsed/不在、stop job が保留、app の InvocationID が変わった場合は照会に切り替える。終了後に届いた旧 tick は破棄。stop と start の短い排他は取得順を統一し、期限 job は lock 競合で停止を捨てず、取得後に世代を照合して実行する。タイマーだけ残った場合も終了時にその世代のものを退役する。[systemd.timer の単調時計タイマー](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)

例（既定3600秒、ホストの最後の受理を時刻0とする）。転送ありで3590秒後の新 tick が受理されたら次の期限は7190秒。3600秒で先に expiry job が発火した世代に3601秒で届く tick は停止を取り消さない。5秒時点で対象の転送自体が失われた場合は3600秒まで待つ必要がない。実 timer の起床精度と停止処理の時間には余裕があるため、単体試験は論理時計、Host 試験は発火記録・生存状態・観測区間で判定する。

**SSH の何が切れたか**。複数の転送チャネルを使うので、個別チャネル1本の正常 EOF をセッション全体の切断としない。親 SSH transport の喪失、対象 route の廃止、または Waypipe server の終了を根拠にする。転送断時は通常 Waypipe が終了し、systemd が同じ cgroup の残りを回収する。片側からしか切断を検知できずホストが生存している場合には期限が後始末の上限となる。切断検知までの固定秒数は保証しない。

**明示停止と証拠**。プロセス名や保存 PID で kill しない。認証済み接続で `systemctl --user show` の機械可読プロパティ（LoadState、ActiveState、SubState、InvocationID、ControlGroup、Result）を照合して対象 unit を stop。通常終了後の既に unload 済み unit も扱う。転送の受付を閉じ、新規 attach を拒否し、転送 tasks を drain、local client・表示・入力・SDK panel を退役する。既存の証拠を確認してから runtime handle を解放する。

新 journal の最小項目は `schema, backend, hostBinding, storageIdentity, appKey, generation, bootId?, invocationId?, phase, timeoutSeconds, stopReason?, localRetired, remoteRetired`。資格情報、URL、任意 argv は保存しない。実体は private app storage の新 namespace。書込み順は予約 → remote 操作送信済み → 起動同定 → 停止開始 → 各側の観測 → 完了 → journal 退役。原子的な書込みに失敗した段階では次の副作用へ進まない。

- 回収完了は remote 側の対象 cgroup・専用 runtime・転送 socket の消失、または同じ owner に対する同等の終了証拠と、local 側の退役の両方で判定する。ホスト側では `/sys/fs/cgroup` の検証済み相対パスだけを観測する。名前に一致する無関係 unit を探索して停止しない。
- Quest が再起動したら新 backend の journal を読み、記録された**同じホスト**への認証後に照会する。これは後片付けであり途切れた GUI の復旧ではない。旧 start がまだ実行に入る可能性がある間は「unit が見えない」だけで空きにしない。同じ SSH の未完了 exec を打ち切り・drain し、ホストの操作 lock 内で再照会する。exec の終了を確認できず、新しい照会が古い start を追い越す可能性が残る場合も、不在の観測だけで完了させない。この場合に限り当該枠を保留して個別に扱う。到達不能や所有不一致は理由付きで保留。異なる boot なら旧 cgroup は消滅済みだが残存する永続プロファイルを削除しない。
- 再試行は `probe/stop/retire` を冪等に行う。未知の start・追加窓要求を自動再送しない。例として SSH の応答が遅れ、別世代で同じアプリが起動した後に古い stop が到着しても、世代固有の unit と current capture の一致がなければ新世代に作用しない。
- エラーは新 backend の reason と操作結果を表示する。例は `SYSTEMD_USER_UNAVAILABLE`、`FORWARD_SETUP_FAILED`、`PROFILE_BUSY`、`CACHE_RUNTIME_UNAVAILABLE`、`LEASE_UPDATE_UNKNOWN`、`CLEANUP_UNCONFIRMED`。UI には「Ubuntu のユーザーサービスに接続できません」「終了確認のため同じホストに再接続してください」等の行動可能な文言を出す。

## 6. Chrome の複数窓

1. 起動ボタンを `host binding + installation/storage + Chrome ID` で振り分ける。Idle の場合だけ新しい unit・runtime slot・プロフィール lock を確保する。Starting 中の再描画/同じ request ID は既存結果を返す。明示的な別のタップは準備完了後に処理する。Stopping 中は新規起動へ読み替えない。
2. Running で同じ専用プロファイルなら **既存プロセスに新しい通常窓を開かせる**。初版の実装案は Chrome 向けの `OpenBrowserWindow` コマンド。対象 runtime 内の最後に選択された有効な Chrome の通常 toplevel を照合し、[コンポジターの focus/key 経路](../../../native/core/src/compositor/input.rs)を通してその窓にのみ Ctrl+N を一組届ける。接続先のデスクトップ、他アプリのフォーカス、システム全体への擬似キー送信を使わない。別サービスから同じプロファイルで Chrome を再実行する方法は、既存窓消失との競合時に新たな browser owner を作り得る。
3. これは**未実証の方式**。実装時に物理修飾キーが押下中・IME 編集中・モーダル・focus token 失効なら送信を保留または明示失敗。対象にフォーカスを移せたときだけ、専用入力 source で Ctrl 押下 → N 押下 → N 解放 → Ctrl 解放。途中で窓が消えたら source の押下状態を reset し、別窓に残りのキーを送らない。正規のユーザー入力を合成イベントの解除に巻き込まない。
4. actor で追加要求を1件ずつ処理。送出直前の root window ID 集合、origin・generation・request ID を記録する。同じ owner から新たに Mapped になった通常 root と SDK panel を観測して初めて成功。popup、親付きダイアログ、既存窓の再 map では件数を増やさない。外部から同時に窓が増え帰属が不確定なら「確認できない」とし、再送しない。確認期限は初期案5秒。Chrome の accelerator が拒否された場合も元のブラウザーを存続させる。実際の Ctrl+N とフォーカスの Host/Quest テストを通すまで一般公開しない。
5. 窓の × は[既存の対象別 close](../../../native/core/src/compositor/snapshot.rs)へ送る。サーフェスの destroy を観測したら、その起動で初めて通常窓が表示された後で、生きている通常 root 数を再計算する。一時 unmap・単なる非表示・SDK の scene 再作成・起動直後のゼロ枚を「最後の窓終了」として扱わない。最後の通常 root が destroy され、同世代で採用待ちの root/追加窓がなくなれば、バックグラウンドに Chrome が残っていても同世代を stop。追加要求中の最後の窓終了は、同一 actor 上で追加窓の確認か期限まで待ち、root が0なら stop。明示的な「アプリ終了」は窓数を問わず優先する。
6. 追加窓は新しい**窓**を消費し、ホストアプリの起動枠は追加消費しない。アプリ枠が満杯でも既存 Chrome に窓を追加できる場合と、窓数上限や当該セッション回収中で拒否すべき場合を UI と起動直前の両方で同じ判定にする。

## 7. 実装カード

以下は小さい順に進める。カードごとに「今回追加したテストの失敗 → 実装 → 成功 → 隣接回帰」を確認する。一つのカードが通っても後続カードの受入れは未達である。表中で「新規」とした型・ファイルは作成案。

| 順 | 入力・主な変更先 | このカードの完成条件 |
|---|---|---|
| 1 共通値 | Android の新規 `GuiSessionPolicy.kt`、設定 UI、[RuntimeConfig](../../../native/model/src/commands.rs)、launch command | 保存・再起動・旧設定・入力境界・主/追加 runtime の単体試験。第2セッションから変更値適用、第1の期限は不変。 |
| 2 終了判定 | Rust の新規 `gui_session_policy` に純粋な状態遷移を実装。fake clock、typed event、期限と転送状態を入力にする | §5 を表駆動テスト。60分待たず `T−1 / T / T+1`、古い世代、重複 stop を再現。タイマーが発火した世代への更新は拒否。 |
| 3 Ubuntu コマンド | [native/transport/src](../../../native/transport/src/) 配下の新規 `linux_desktop/systemd`。unit/paths 生成、能力検査、固定 argv、timer と service の開始/照会/停止。既存 SSH 認証を注入 | 合成プロセスで arm → ready → renew → stop。タイマー先行発火、各 SSH ACK 喪失/再送、unit 名の衝突、危険な path、別ホストの stop を検査。製品 UI へは未接続。 |
| 4 1窓の縦断 | 新 actor を [transport session](../../../native/transport/src/session.rs)、[core runtime](../../../native/core/src/runtime.rs)、[NativeSessionHost](../../../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt) につなぐ。1項目の Chrome で開始 | 既存 desktop Chrome と同時起動、Waypipe 上の root・panel の採用、明示停止、最終窓終了、無更新、転送断、専用 tmpfs・profile 保存を再実証。 |
| 5 複数窓 | §6。棚を押した時点と dispatch 直前で状態を再確認。アプリ所有世代に root の集合を保持 | 窓 1→2→1→0、最後だけ回収。window ID 再利用・重複タップ・モーダル・別 host・既存の非 Quest Chrome を含む負例。 |
| 6 一般 GUI と Termux | 通常棚のカタログ・選択、Ubuntu の非 Chrome アプリ、Termux の capability/lease と tick 配線 | Chrome と別の Ubuntu GUI と Termux の foot で1つの UI 設定が実効値に反映される。管理方式が対応しない場合は「適用済み」と表示しない。 |
| 7 切替と障害 | backend 別 journal/選択、下記の一度限りの切替手順、診断と rollback | 旧記録がある状態で silent fallback/二重起動しない。失敗範囲が対象接続先に限定される。旧・新それぞれの終了処理で再照会可能。 |
| 8 受入 | Host 回帰、Android/JNI、Quest 実機、キャッシュ測定 | §9 を証拠別に記録。正式な既定切替の判断はこの結果で行う。 |

### 最初に追加するテストケース（提案名）

| ケース名 | 入力 → 観測する結果 |
|---|---|
| `lost_heartbeat_keeps_forward` | 60分で起動。fake clock を59分進めても unit は生存。tick 復帰後に当初の60分を越えても同一世代で生存し、新期限で stop は1回。 |
| `transport_eof_during_grace` | Waiting 中に親 SSH EOF → 同じ起動の Stop/Retire。新しい SSH の Launch は新しい世代になり、古い tick/stop の配送で消えない。 |
| `timer_fires_during_start` | expiry worker を timer 作成と app start job 投入の間で発火させる。start の操作 lock 解放後に stop が処理され、後から app だけが生存しない。 |
| `launch_ack_lost` | SSH exec の ACK を破棄し、再接続で unit が (a) 同じ世代で存在、(b) 消失し旧 exec も終端、(c) 所有不一致/終端不明の3ケース。a は対象停止、b は両側の退役後解放、c は個別保留。二重 start は0。 |
| `second_chrome_window` | 同じ接続で要求 X を2回配送。新しい通常窓が1枚、同じ cgroup・同じプロファイル、追加 app 枠0。別の意図的要求 Y はさらに1枚。他の接続先は独立。 |
| `close_with_other_root` | root 2枚で片方を close。SDK panel と入力対象はその窓だけ退役し、残るページの応答と profile lock は存続。popup の破棄で stop を発行しない。 |
| `shared_profile_control` | 隔離したデスクトップと試験 unit にわざと同じプロファイル → 既存側のページだけ増加。転送側の起動成功・新規窓に計上しない。 |
| `legacy_cutover` | 旧方式で clean / 当該 host に旧 pending / 別 host の pending を作り分ける。切替の可否・対象・旧データ保持が §8 と一致。 |
| `policy_reaches_all_gui_owners` | 7分を保存、各通常経路で新規起動 → 主 Ubuntu・追加 Ubuntu・Termux のホスト実効値は420秒。旧/不対応 capability は明示。active session がある状態で設定変更しても既存世代は420秒。 |

## 8. 切替と既存データ

通常運用の起動ごとに旧ホストの全履歴を走査する処理は作らない。旧方式から新方式へ切り替える**その時だけ**次を行う。

1. 対象接続先で旧 GUI を終了し、旧 backend の現行 UI/回収経路で、主・追加枠の未回収がなくなったことを確かめる。Quest の再起動を挟んでもよい。再起動は Ubuntu のプロセス・作業領域・Quest の永続 journal の消失を証明しない。
2. 旧枠が空なら対象接続先の backend 選択を原子的に保存し、新方式は新規起動から使う。旧アプリを live migrate しない。旧保存領域・Documents はそのまま保持。**新規の Quest 専用 Chrome プロファイル**を使うことを切替画面に表示する。旧プロフィールの自動コピー・削除は行わない。新方式で同じ host/storage を次回利用する場合は、その新方式の保存済みプロファイルを再使用する。
3. 旧回収不明は対象だけに「旧方式で前回の回収」を案内する。同じ host binding・同じ世代の証拠を旧 reader で照合する。証跡が足りなければ個別対応。Ubuntu 再起動、全ホストのデータ削除、旧 journal の強制クリアを切替手順に組み込まない。[旧回収契約](linux-real-app-c2-helper-20261110.md)
4. 開発段階は明示選択で新経路を試す。新経路が対応する一般 GUI と共通設定の受入れが済むまで、既存の利用者の既定値を自動変更しない。旧経路では従来の lease が有効であることを表示する。最終の通常利用経路では対応版 Termux と新 Ubuntu backend に共通設定を適用する。旧形式のデバッグ・互換プローブは独立して維持する。
5. 戻す場合も、その接続先の新方式の unit・route・local panel を先に回収して backend 選択を旧方式へ戻す。世代の異なる停止記録を流用しない。どちらの永続データも backend 切替によって消えない。

## 9. 検証と引継ぎ

| 層 | 試験と合格条件 |
|---|---|
| 単体／時刻 | 未保存=60分、1/60/1440分、破損・範囲外・分秒変換。生存 tick と入力/フォーカスの独立性。同一 SSH の期限内復帰、期限超過、停止との同時更新。タイマーの時刻を進められるテスト用 clock を使う。 |
| 旧方式・永続化 | 各副作用の直前/直後で失敗を注入。ACK 喪失・Quest 再起動・ホスト再起動・旧 pending・異なる SSH host key・遅延 stop で、対象外 unit/profile が無傷。旧 journal は旧 reader だけで完了できる。 |
| Host 実 GUI | [実験記録の手順](systemd-gui-session-host-evidence-20261126.md)で、比較用ブラウザーの同じ PID＋開始時刻とページ応答、対象の生存プロセス数0、unit/cgroup・専用 dir・socket 消失を確認。専用/共有 profile の両方を試す。恒久 profile の設定・ブックマークは使い捨てデータで「再起動後に読み戻せる」まで確認。単なるファイルの存在と区別。 |
| Android/JNI | 設定の往復、主/追加 Ubuntu/Termux への伝播、pause/resume の owner epoch、Qt/GTK 窓、IME/ポップアップ、launcher capacity、追加窓への入力、停止中の二重タップを確認。 |
| Quest 実機 | [実機 preflight](../../development/quest-development-preflight.md)後に通常棚から操作。通常 close、明示終了、2窓の片方だけ close、Termux との同時起動、SSH **存続を確認した**状態で短い猶予の前後と既定1時間での復帰・満了を観測。別試験で SSH 転送を実際に切り、回収後の新規起動を確認。スリープ試験は通常の装着検知に戻した lifecycle profile で行う。転送も落ちたランは「SSH 存続中の1時間」の合格に数えない。 |
| キャッシュ | Chrome の空ページ・普段の複数タブ・長時間利用で tmpfs ファイルの割当バイト数とピーク、同時起動数、ホスト空き容量、終了後の解放を記録する。あふれる条件は使い捨ての小さな試験領域で再現し、保存済み設定と他アプリが無傷か確認。容量満杯・tmpfs 不在時に永続ディスクへ黙って退避しない。固定 MiB の製品上限はこの測定後に決める。 |

repo root からの既存回帰入口（実装カードで対象を絞って実行する）。

```sh
cargo test -p eidolon-model
cargo test -p eidolon-transport
cargo test -p eidolon-core
python3 -m unittest discover -s scripts/tests -p 'test_eidolon_desktop_entries.py'
python3 -m unittest discover -s scripts/tests -p 'test_linux_desktop_v3_c2.py'
python3 -m unittest discover -s scripts/termux/tests -p 'test_supervisor.py'
(cd android && ./gradlew :app:testDebugUnitTest)
python3 scripts/docs.py validate
python3 scripts/check-design
```

受入記録には rev、backend/capability、設定した猶予、切った対象（tick/SSH）、観測層、終了理由、残存数、比較窓の応答を残す。実プロファイルの中身、URL、鍵、接続先、個別デバイス ID は載せない。設計作成時点の実績は Ubuntu での比較実験まで。新 backend・設定 UI・複数窓・Quest 受入はこれから実装・検証する項目である。
