# 固定Mesaの3 NULL分岐を非描画の子プロセスで観測する

surfaceless+softpipeでも`EGL_NOT_INITIALIZED` / `dri2_screen_failed`となり、
実loaded Mesa/Gallium/libc++と既知ファイルの同一性も確認できた。
環境変数の再試行を増やさず、固定の`driCreateNewScreen3`内の3か所を一回観測する。
これは内部戻り値の診断であり、GL/SDL描画やGUIの合格ではない。

前段の[ptrace能力確認](ptrace-capability-probe-20261108.md)は実機でsupportedだった。
Termux UID10158/AArch64、GPR272byte、hardware264byte、6slot/debug_arch8、未使用state、
TRACEME/STOP/EXITKILL/CONT/自然終了23とgroup/reap/pipe回収を確認した。
SETREGSETやhardware hitは前段の合格に含まれず、本probeで初めて試す。

```sh
scripts/quest-probe-egl-null-stages \
  --serial "$EIDOLON_QUEST_SERIAL" \
  --artifact-root "$EIDOLON_TASK_ROOT/egl-null-stages"
```

新CLIはserial/artifact-rootだけを受け取る。自由PID、offset/address、exec argv、env、
library pathは受け付けない。APK、C/binary、配布manifestの更新・package導入は不要。
新probe IDで既存EGLのprivate保存先に新規0700 directory/0600 reportを作り、過去の失敗を上書きしない。
固定7sourceのSHAを実行前後に確認し、ADBと子processそれぞれの圧縮bootstrapを49KiB以下に制限する。

## 固定条件と観測範囲

`eidolon-egl-null-stages-v2-mode-classification`は既存のsurfaceless probeと同じ4library pin、vendor JSON、
`LIBGL_ALWAYS_SOFTWARE=1` / `GALLIUM_DRIVER=softpipe` / `EGL_LOG_LEVEL=debug`を使う。
Wayland/SDK、既存footや認証leaseへ操作しない。自分でPopenした固定Python子のmain threadだけを対象とする。

固定Mesa実ELFはGalliumを直接DT_NEEDEDとして持つため、既存のquery/displayでvendorがロードされれば
initialize前にも依存としてmappedになる想定である。ただし実際のmapping確認を必須とし、
無ければ`MAP_UNVERIFIED`/BP未設定のまま元EGLを続ける。追加preloadへのfallbackはしない。
`loading_profile=existing-egl-query-display-no-preload`を保存する。
`prior_initialize_failure_matches`は既知のcore/display成功→initialize false/12289→元終了1との一致boolで、
結果が変わった場合も元API結果を残してfalseを記録する。原因特定や描画成功とは区別する。

1. 子のTRACEME/最初のSIGSTOPでEXITKILLを設定する。initialize直前に固定receiptをflushし、2度目のSIGSTOPを行う。
2. Gallium SHA `2684bf73cd4cec80f40c19ffd4e787af4680b9c03912650e1bdc7320670934f1`、
   owner/mode/identity、ELF64 little-endian/EM_AARCH64、実行PT_LOADと既知3命令wordを検査する。
3. 停止中の自分の子のmapsはメモリ内で固定Galliumだけを選び、既知prefix、device/inode、
   実行mappingとfile offsetを照合する。実instructionも既知3wordだけをPEEKTEXTで読み、fileの命令と一致させる。
   全条件が通るまでbreakpointを設定しない。
4. 公開arm64 hardware regsetの先頭3slotだけをSETする。iovecは56byteであり、
   16slot全体を渡して余分なperf eventを初期化しない。GETは264byte全体で、設定と残りslotの不変を確認する。
5. 最初のhitでSWRAST type2/FD-1をboolで確認する。各hitの戻り値はNULL/非NULLだけに縮約する。
   hit後のslotは無効化し、NULLが出た場合や3段階完了時には3slotとも無効化する。最初のcallだけの観測である。

結果の固定stageは次の3個。

| stage | 意味 |
| --- | --- |
| `screen_allocation` | screen構造体の確保が非NULLか |
| `pipe_screen` | software pipe_screenが非NULLか |
| `dri_configs` | DRI config生成の戻り値が非NULLか |

`false/null/null`、`true/false/null`、`true/true/false`はそれぞれ最初に確認したNULLの段階を表す。
`true/true/true`でも、後段や別callでEGL初期化が失敗する可能性は残る。nullは未観測であり、falseへ変換しない。
type/FD不一致、hit無し、途中まで、未知stop、短いregset、SET拒否は固定reasonの`unsupported`となる。
生address、register値、全maps、入力本文、log原文は結果へ出さない。

kernelはdisabled slotにEL0/length bitsを保持し得るため、解除は3slotのaddress0/disabledを確認し、
残り13slotの不変も確認する。新しく作ったdisabled perf eventの寿命は子の終了までであり、
最終group/reap回収と区別する。部分SET失敗でもGETで解除を検査し、不明なら自分の子を終了させる。

## 初回EGL失敗と回収を守る

APIの`eglInitialize`直後のnumeric errorをflushし、loaded identityを確認してからterminateする順序は維持する。
`api_observation`と`internal.status`を分け、内部観測の拒否で元API errorや元終了codeを置き換えない。
解除が確認できたSET拒否なら、元EGL処理を最後まで通す。

CLI終了0/`diagnosed`は内部観測・API結果・回収が揃った意味であり、API成功ではない。
終了2/`unsupported`でも`api_observation=diagnosed`なら元の負のAPI結果を読める。
元signal、timeout、回収失敗等は`failed`とし、初回API結果があれば残す。

期限は12秒。取消/EOF/期限/固定runtime変化ではowned groupだけへTERM、停止中は解除後CONT(SIGTERM)、
猶予後だけKILL、live group消滅→元statusのreap→pipe drainを確認する。
元のfatal signal、未知SIGTRAPは同じsignalをforwardし、診断用trap以外を黙って消さない。
stderrは既知分類/回数とbyte数だけを保存する。SELinux/root/debuggableやlibrary/code fileを変更しない。

独立検証:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_egl_null_stages.py -v
```

hostでは実TRACEME/STOP/CONT/EXITKILL/期限/取消/回収を使い、AArch64のhardware stateとhit値はfixtureで分離する。
これを実機SET/hitの合格へ読み替えない。固定の実機一回実行はoperatorが行う。

## 初回実機のmode不一致とv2の追加分類

v1の実機ではSET/最初のhit/解除、固定file/mapping/実3命令の照合、元initialize false/12289、
元終了1/terminate/runtime不変/全回収が成立した。ただし`mode_expected=false`/
`DRI_MODE_UNEXPECTED`で、3stageはすべて未観測のnullだった。これをNULL戻り値の判定とは扱わない。

[dri_util.h](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/gallium/frontends/dri/dri_util.h)の
enumはDRI3/KOPPER/SWRAST/KMS_SWRASTの順であり、固定ELFのprologueとjump tableも照合した。
typeは第4引数w3→w23、FDは第2引数w1→w26で、現時点では割当の取り違えは見つからない。
[surfaceless実装](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/egl/drivers/dri2/platform_surfaceless.c)は
ForceSoftware時もDRM候補を先に試すため、最初のcallがDRM無しSWRASTであることは保証されない。
v1はtypeとFDをANDしたboolしか保存しておらず、どちらが不一致だったかは分からない。

v2は同じ最初のhitに`mode_observation`だけを追加する。保存内容は
`dri_type`（`dri3/kopper/swrast/kms_swrast/unknown`の固定enum）、`type_is_swrast`、
`fd_is_minus_one`の独立boolで、raw registerやFD整数は保存しない。
元のmode gate・3stageの扱い・EGL初回結果・回収は維持する。
source/operator/profileを更新して新runで一回だけ識別し、複数call追跡やSINGLESTEPへは広げない。

一次資料: [Mesa26.2.6 dri_util.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.2.6/src/gallium/frontends/dri/dri_util.c)、
[Linux v6.6 arm64 ptrace](https://github.com/torvalds/linux/blob/v6.6/arch/arm64/kernel/ptrace.c)、
[arm64 hardware control](https://github.com/torvalds/linux/blob/v6.6/arch/arm64/include/asm/hw_breakpoint.h)、
[公開UAPIの構造体](https://github.com/torvalds/linux/blob/v6.6/arch/arm64/include/uapi/asm/ptrace.h)、
[ptrace/PEEKTEXT/GETREGSET/SETREGSET](https://man7.org/linux/man-pages/man2/ptrace.2.html)。
