# NX13: storage帰属量の集計と内側リング

2026-11-08。Termux helperからnative themeまでの初期実装。APKとhelperの同版配備、Questの実UIDでの継続観測、視認性の受入れは別途確認する。この実装だけでNX13全体完了とはしない。

## 観測値と採用範囲

`/proc/<tgid>/io` の `read_bytes` と `write_bytes` を用い、両方向の合計bytes/sで内側リングを回す。read側はstorage層へ帰属する量、write側はページをdirtyにした時点の計上である。物理diskの完了量・待ち時間・使用率ではない。`rchar`/`wchar` はTTYやcache hitを含むため代用せず、`cancelled_write_bytes` の単純減算もしない。[Linux kernelのproc資料](https://docs.kernel.org/filesystems/proc.html#proc-pid-io-display-the-io-accounting-fields)

既存と同じ登録shell tree、または対応を確認できたtmux sessionのpane root unionを対象にする。foot本体・tmux server・無関係な同UID processを便宜上追加しない。複数の登録窓が同じtmux scopeを表示する場合、1 tickに1回集計し同じ値を配る。全scopeのTGID unionを一度だけ読み、`task/<tid>/io` と二重加算しない。

[固定I/O probe](termux-io-probe-20261108.md)では、実UIDで子の1MiB書込みを観測した後、waitした親にも同量が現れた。終了した子の履歴を別加算すると二重計上になる。初期実装では生存TGIDの点時刻合計を差分比較し、retired processのcounterを持ち越さない。

## 不確かな区間は欠測にする

CPU/RSS用の既存scanをS0として保ち、I/O読取り後にS1を再scanする。rootsと `(pid,starttime,ppid)` 集合をS0/S1とbaseline間で照合する。proc directory FDを固定し、読取り前後のPID・starttime・PPID・全UID・TGIDも確認する。tmuxのserver/client/session/pane対応を前後で照合する。

- zombie、集合変更、reparent、PID再利用、scan打切り、counter逆行はI/Oだけをpartialにする。
- `/proc/io` の権限拒否や取得不能はunavailable。ゼロへ置き換えない。
- 不完全区間はbaselineを破棄する。次の完全sampleをbaseline、その次からrateにし、欠測区間を後から追いかけない。
- 初回はbaseline、停止はstopped、長欠測や単調時計逆行はstale。完全な二点間で両counter差分ゼロの場合だけavailableの0を出す。
- CPU/RSSとI/Oの有効性は独立。CPU baselineでもI/Oが有効なら動き、I/OがpartialでもCPU/RSSは継続する。切断・登録喪失では両方を止める。

前後のsnapshotが一致してもkernel全体のatomic snapshotではない。短命な子がsample間で生成・終了・waitまで済めば親の増分として一度計上し得るが、scope外への一時移動を完全捕捉する保証はない。頻繁なfork/exitでは欠測が増える。Quest kernel固有のthread終了・autoreap・subreaperの動作は今後の実機確認に残す。

既存observer worker内で採取する。S0/S1はそれぞれ50ms・512同UID process・4096 entriesの既存上限、I/O unionの追加読取りは25msで打ち切る。各proc fileも有限bytesまで読む。tmuxは固定query・既存timeoutで前後確認するため、これは全tickが125ms以内に終わる保証ではない。実機で遅延・partial頻度を測り、lease/control処理を妨げないことを確認する。

## wireと描画

`workload_v1` / schema 1の各rowへoptional `io` objectを追加した。

| field | 意味 |
| --- | --- |
| state | available / baseline / partial / unavailable / stale / stopped |
| read_bytes_per_second, write_bytes_per_second | available時だけ有限・非負のrate。片方向だけの値は採用しない |
| interval_seconds | I/O独自の二点間隔、0より大きく3秒以下 |
| sampled_monotonic_ms | collectorのI/O採取時刻。親frameより未来/3秒超過を拒否 |
| reason | 固定tokenだけ。path・例外本文・command・実入力は含めない |

受信側は登録terminal/session/nonce/UID/starttimeとscopeの既存照合を維持する。CPU coverageがpartialでもI/O availableなら登録照合を省略しない。同じworkload IDを持つ重複行はI/O objectも一致する必要がある。I/O値は受信後も独立した3秒TTLを持つ。

新consumerは旧helperの `io` 欠落をunavailableとして扱う。旧consumerは未知fieldを拒否するため、逆方向の互換性は保証しない。既存helper SHA gateに従って**APKとhelperを同版で配備**する。14,000bytesのworkload上限と16,384bytesのtransport上限は変更しない。I/O追加が前者を超える場合はI/Oをnullへ落とし、まずCPU/RSS frameを残す。

内側のtheme metricは `workload.io.bytes_per_second`。初期値は1MiB/s以上で30度/秒、それ未満は比例、ゼロで250msの減速後に停止する。欠測は角度を保持して淡色化する。再開・非表示・reduced-motionで過去時間を再生しない。CPUのbusy判定でI/Oを止めない。外側CPU・中央RSSの式、ユーザー採用済み線幅と直線端のSVGは変更していない。

## 検証と再現

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -p test_io_aggregate.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -p test_workload.py -v
cargo test --offline -p eidolon-model -p eidolon-theme --lib
cargo test --offline -p eidolon-core -p eidolon-render -p eidolon-transport --lib workload
```

実装時のhost検証は専用Python 18件・既存Workload 13件、Rust model 47件・theme 23件・core workload 4件・render workload 1件・transport workload 1件が成功した。model/themeのclippy（警告をerror扱い）も成功。Questへはこの時点で未配備。

固定counterのwait二重計上反例、集合変更、PID/UID/TGID、zombie、前後tmux変更、共有scopeの一回更新、counter逆行、cache hit、短いread、budget、clock、CPU/RSSとの独立性、登録照合、wire最大16行、I/O独立回転・ゼロ・欠測・reduced-motionを検査する。hostのself TGID読取りと別TID拒否は、Questでの成功の代用にしない。

次の実機確認は同版配備後、登録した単一shellで固定write/read/wait、次にthread終了、二窓で同じtmux session、観測OFF/ON・切断復帰の順とする。kernel版、I/O state/reason、固定counter差分、scan時間を記録し、実入力本文・個人IPは公開記録へ載せない。

## Build153の配備とidle観測

debug build・lint・APK検査・JUnit 104件成功後、旧runtimeの正常停止とcleanup journalが空であることを確認し、
同版helperとBuild153を配備した。helper SHA256は
`ef09a9b3f72bd1954e94144a97bd044b3f3376a27598dbc89bb22955d5e5fab2`。
hostのTermux関連95件、scripts関連152件（実APK指定7件skip）、design・format検査も成功した。

通常経路で開いた登録shellの7時点で、native側のI/Oが`available / 0 bytes/s`、CPU/RSSがcompleteとして継続した。
これは実測ゼロと配送の確認範囲であり、非zero、wait後、thread終了、共有tmux、描画semanticの実機確認は含まない。
固定I/O専用端末の検証を追加してそれらを個別に評価する。HMDでの内側リングは未受入れ。
