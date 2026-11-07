# NX13: Termuxの自プロセス・専用子だけを使うI/O probe

## 目的と範囲

[`scripts/termux/eidolon-io-probe.py`](../scripts/termux/eidolon-io-probe.py) は、
Termuxのcollectorと同じUIDで `/proc/self/io` と専用子の `/proc/<pid>/io` を読めるか、
子の終了・waitでカウンターが親へ加算されるかを確認する固定probeである。
Workloadの製品契約、GUI、IME、内側リングへは接続しない。
成功してもNX13全体の完了や、一般process treeの正確な集計を意味しない。

Python標準ライブラリのみを使う。引数は一切受け付けず、PID、path、commandを指定できない。
既存processを走査せず、自分がforkした専用session/groupとwriter/reader子だけを対象にする。
一時領域に新規0700 directoryと固定名の0600 fileを作り、writerが1MiBを書いてfsync、
readerが同じ1MiBを読む。cache drop、root、ptrace設定緩和、外部ファイルの変更は行わない。

## 実行

スクリプトをTermuxの検証用場所へコピーしてから、**Termux自身のshellまたは既存のTermux接続経路**で実行する。
ADB shellのUIDで実行した結果をTermuxの取得可否として採用しない。
コピーや接続情報は既存の手順を使い、このprobeに追加の接続資格情報を持たせない。

```sh
umask 077
mkdir -p "$HOME/.local/state/eidolon-io-probe"
# カレントdirectoryにはコピー済みのeidolon-io-probe.pyを置く。
python ./eidolon-io-probe.py > "$HOME/.local/state/eidolon-io-probe/result.json"
```

stdoutは1件のJSONで、固定の観測対象名、status/reason token、UID、counter、検証フラグだけを返す。
PID/starttime、process名、作業path、ファイル本文、コマンド列、例外本文は出力しない。
実行UIDは既知のTermux UIDと照合する。実機実行・導入は別checkpointで行う。

| 終了コード | 意味 |
| --- | --- |
| 0 | 全観測箇所が取得できた `observed`。物理diskの保証・NX13採用成功ではない |
| 1 | 一部取得不可の `partial`、または操作/回収に失敗した `failed`。JSONを保存して理由を読む |
| 2 | 引数が渡されたため `NO_ARGUMENTS_ALLOWED` で拒否。作業用子は起動しない |

通常は短時間で終わり、外側は12秒で待機を打ち切る。
SIGINT/SIGTERMも専用groupの回収へ進む。専用group leaderをreapする前だけにsignalを送り、
reap後は存在確認だけを行う。自分が生成してwait前の子PIDなので、既存shellやPID再利用先へsignalしない。
fileとdirectoryを回収し、group消滅まで確認できた場合だけ `cleanup=completed` とする。
SIGKILLや端末電源断は通常のcleanupを実行できないため、この保証の対象外である。

## 読む順序とidentity

1. 親自身の初期値を読む。
2. writer/readerそれぞれで、子の自己申告と**親からの独立したproc読取り**を分ける。
   親が初期identityを固定してから操作開始を指示し、子を生存させたまま終了時counterを読む。
3. writerを終了させ、`waitid(WNOWAIT)` で終了を観測する。親のwait前とzombie子の取得可否を記録する。
4. writerをwaitした直後の親counter、続いてreaderを即waitした後の親counterを読む。

proc directoryのFDを固定し、io読取り前後でPID、starttime、real/effective/saved/filesystem UID、
親/group/sessionを照合する。UIDは実行UIDの四値一致を要求する。
取得拒否、消失、identity変化、counter欠落/不正値/逆行はゼロへ置換しない。
`ACCESS_DENIED`、`PROCESS_GONE`、`UID_CHANGED`、`IDENTITY_CHANGED`、
`IO_FIELDS_MISSING`、`COUNTER_REGRESSED` 等を返す。
観測対象は `observations.writer_parent_live` 等の固定keyで分かり、
`unavailable_targets` に取得不可箇所を列挙する。zombieだけ読めない場合も区別できる。

## カウンターの解釈

- `rchar/wchar` はread/write系のbyte数であり、pipe、TTY、probe自身のproc読取りも含む。
- `read_bytes/write_bytes` はstorageへの帰属counterであり、前者はcache hitで増えない場合がある。
  後者はdirty化時点の計上を含む。fsync完了や自己申告の1MiBだけで物理diskの実処理量を保証しない。
- `cancelled_write_bytes` は別欄で保存し、write_bytesから引いて「実書込量」とは呼ばない。
- 子の自己計測後にもpipe出力やproc読取りがあるため、自己申告と親のsampleは同一時点・同一値ではない。

これらの意味は[Linux kernelのproc説明](https://docs.kernel.org/filesystems/proc.html#proc-pid-io-display-the-io-accounting-fields)に従う。
wait済みの子も親のioへ含まれ、取得にはptraceのread権限判定が関与する。
[Linux man-pages: proc_pid_io(5)](https://man7.org/linux/man-pages/man5/proc_pid_io.5.html)

`writer_delta` と `parent_wait_delta` を別々に返す。
`naive_sum_double_count_risk.character_io` はwchar、`storage_io` はwrite_bytesに正の重なりを観測した場合だけ
`observed` とする。値がゼロ/取得不可なら `unknown`。unknownを「二重計上なし」と解釈しない。
この固定probeでは親自身はfileを書かないが、一般collectorの重なり解消アルゴリズムはまだ実装しない。
`physical_disk_guaranteed` と `nx13_complete` は常にfalseである。

## host検証と残件

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/termux/tests -p test_io_probe.py -v
```

10件でparser、UID/PID再利用、読取り中のidentity変化、欠測/逆行、固定fileのmodeとsymlink拒否、CLIの任意引数拒否、
実子processのwrite/fsync/read/wait、無関係processの生存、SIGTERM時の専用group/file回収を確認した。
初回hostではlive子とselfを取得でき、wcharのwait後加算を観測した。
一時領域でread_bytes/write_bytesは0、zombie子のioはACCESS_DENIEDだった。
そのためstorage側の重複はunknown、結果はpartialとして保持した。

## Quest上のTermux実UIDでの結果

2026-11-08、配布sourceのSHA-256を照合してTermux自身のPythonで同じprobeを実行した。
自プロセスと生存中の子は取得でき、取得不可は`writer_parent_zombie`だけだった。
結果は`partial / OBSERVATION_UNAVAILABLE`、専用group/fileの回収は`completed`として保存した。

固定writerの`write_bytes`差分と、writerをwaitした親の`write_bytes`差分は、どちらも1,048,576 bytesだった。
`wchar`でも正の重なりを確認したため、`character_io`と`storage_io`の二重計上リスクは両方`observed`となった。
同じ子の終了前counterを保存し、wait後にそのcounterと親のcounterを単純加算する方式は採用できない。
`read_bytes`差分は0であり、キャッシュされた読み込みを物理disk読出しとして扱わない。

これは当該UID・固定親子での取得可能性とwait境界の証拠で、一般process treeや内側リングの完成ではない。
source hash、実行UID、全固定counterと取得不可箇所は、作業rootの
`eidolon-ime-implementation-20261107/nx13-device/`へ保存した。

次はこの結果に基づき、wait済み子の別加算を避け、sampling中のtree変化をpartialにする集計方式を決める。
thread終了、孫process、tmux共有session、頻繁なPID交代、一般process treeの二重計上除去、
sampling境界のpartial/stale、collector→wire→ringの配送は残件である。
