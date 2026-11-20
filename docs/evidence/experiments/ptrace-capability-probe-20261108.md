# 固定子プロセスのptrace能力を分けて確認する

Mesaの非描画EGL診断は、実loaded libraryと既知ファイルの一致を確認しても
`eglInitialize=false` / `EGL_NOT_INITIALIZED` / `dri2_screen_failed`だった。
内部NULL分岐の観測へ進む前に、この小さなprobeだけでTermuxからの能力を一回確認する。
EGL、Wayland、SDK、通常helperや既存footは使わない。

```sh
scripts/quest-probe-ptrace-capability \
  --serial "$EIDOLON_QUEST_SERIAL" \
  --artifact-root "$EIDOLON_TASK_ROOT/ptrace-capability"
```

CLIはADB対象とprivate artifact保存先だけを受け取る。PID、exec、argv、env、library、
breakpointのaddressやslotは指定できない。配布C/binary/manifestの変更・再buildは不要。
hostの固定3sourceのSHAを実行前後に照合し、remote bootstrapは49KiB以下に制限する。
`run-as`のHOMEは信用せず、既存installerと同じ実Python prefixからTermux homeを導出する。
UID10158、AArch64以外はこの実機用能力の合格にしない。

## 固定の流れと判定

1. 新session/groupの固定Python子が、nonce付き固定receiptを出し、`PTRACE_TRACEME`の後で自分をSIGSTOPする。
2. 同じ親threadが、自分のPopen handleの子だけへ`PTRACE_O_EXITKILL`を設定する。
3. 公開`PTRACE_GETREGSET`で`NT_PRSTATUS`と`NT_ARM_HW_BREAK`を取得し、返却幅272/264byte、
   slot数3以上、既存debug stateが未使用であることを確認する。全registerやaddressは出力しない。
4. `PTRACE_CONT`後に固定子がreceiptを出して**終了23**する。23は元の終了statusを保持したか確認するsentinelである。
   operator自体は全条件を満たした場合だけ終了0/`supported`を返す。
5. terminal statusは`WNOWAIT`で保持し、live groupが無くなってから`Popen.wait`で回収する。
   `WSTOPPED`だけの待機が終了直後にECHILDとなる場合も、terminal peekを確認して終了codeを失わない。

権限拒否、未実装、短いregset、slot不足、既存debug state、想定外のstop/exitは`unsupported`。
固定reason/errnoと元の終了codeを残す。子のTRACEME拒否時は固定終了77であり、成功へ読み替えない。
能力確認はGETREGSETまでで、hardware breakpointの設定やhit、EGL内部の分岐、描画の成功を証明しない。
後続の設定権限も未確認のままである。

remoteの期限は6秒。取消、stdin EOF、期限超過では実際に起動したgroupだけへTERMを送り、
ptraceのsignal-delivery-stopではTERMをforwardする。猶予後のKILL、reap、group回収、pipe drainを
別々に残す。元のfatal signalもforwardして元codeを維持する。他プロセスへのattachやsignalはしない。
SELinux、root権限、kernelやdebuggable設定を変更しない。

## 保存範囲と検証

hostは新規0700 directoryの0600 `result.json`へ固定metadataを保存する。
remoteも`~/.local/state/eidolon/ptrace-capability/<new-id>`を新規0700で作り、reportは0600・上書き不可とする。
本文、全env、全maps、ASLR address、全register、stderr rawは保存しない。
記録は固定receipt、能力の幅/slot数、bool、元終了code、回収結果と出力byte数だけである。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_ptrace_capability_probe.py -v
```

Linux host試験では実際のown-child TRACEME/STOP/CONT、native GPR取得、終了23、拒否77、
元signal、期限/取消/EOF、無関係な子の生存を確認する。AArch64のhardware幅/slot判定はpure fixtureで分け、
hostのmock metadataをQuestの能力確認とは扱わない。実機の一回実行はoperatorが行う。

一次資料: [Linux v6.6 arm64 UAPI](https://github.com/torvalds/linux/blob/v6.6/arch/arm64/include/uapi/asm/ptrace.h)、
[arm64 regset実装](https://github.com/torvalds/linux/blob/v6.6/arch/arm64/kernel/ptrace.c)、
[ptrace UAPI constants](https://github.com/torvalds/linux/blob/v6.6/include/uapi/linux/ptrace.h)、
[ptrace manual](https://man7.org/linux/man-pages/man2/ptrace.2.html)、
[waitid/WNOWAIT](https://man7.org/linux/man-pages/man2/waitid.2.html)。

## Questで確認した能力

nonce `6f5010a51fb9119ce7b4199a58b3edd6`、UID10158、AArch64でsupported。
TRACEME/STOP/EXITKILL/GETREGSET/CONT、意図した自然終了23を確認した。
GPR272bytes/HW264bytes、hardware slot6/debug arch8、未使用状態。
TERM/KILLやtimeoutを使わず、子group/pipe/reapの回収は全て成立した。
register値、ASLR address、任意mapは結果へ保存していない。
これはGET能力の結果で、hardware slotのSET/hitとEGL内部3点の観測は後続未検証。
