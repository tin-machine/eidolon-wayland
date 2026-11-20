# NX11: Termux GUI専用workerの子孫所有

Freedoom固定demoは実機で自然終了0、SHM74commit、SDK更新・全資源退役、元foot維持、
owned group/reap/pipe回収を確認した。通常GUIへ接続する前に、別sessionへ移った子孫の回収を補う。
既存Termux helperにはWaypipe/Footserverのchildがあるため、そこでsubreaperを新規取得しない。
固定GUI専用workerを起動し、その内部だけでsingle-thread・既存child無し・default SIGCHLDの所有者を作る。

`scripts/termux/eidolon_gui_owner.py`はLinux C2a ownerのlive subreaper/pidfd契約を基にした。
CLI、外部PID attach、保存PIDからsignalを再構築するAPIは無い。
PID/starttime/全UIDをcapture前後で照合し、捕捉済みのpidfd以外へsignalしない。
TERM→KILL、rootとadoptしたzombieのreap、連続二回の子孫空scan、全pidfd closeを個別に確認する。
未確認/期限切れではpendingのlive ownerを残し、新しいownerへ置き換えない。

Questでは`/proc/<pid>/task/<tid>/children`が提供されないため、その場合だけ同UIDの有限`/proc`列挙から
PPIDの系統を作る。専用subreaper自身から到達するprocessだけを採用し、snapshot後のPID/starttime/UID/親も再照合する。
whole-machineの同UID process全体を停止対象にしない。上限はproc entry4096、同UID process1024、1scan250msである。
読み取り不能・上限超過・途中のreparentは完全な空snapshotへ変換しない。
他ownerのD-Bus/systemd等が生成したprocessは、この子孫の保証へ含めない。

## 固定capability probe

```sh
scripts/quest-probe-termux-gui-owner --serial "$EIDOLON_QUEST_SERIAL" \
  --artifact-root "$EIDOLON_TASK_ROOT/termux-gui-owner-capability"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_termux_gui_owner.py -v
```

このoperatorは新規Termux UID10158 processだけを使い、固定Python childのdouble-fork/setsid/adoptionと
pidfd回収を一回確認する。子は4秒で自然終了する上限も持つ。既存のfoot/SSH shell/Wayland/Quest SDKへ触れない。
command予算49KiB、source前後SHAと固定schemaだけを記録する。外部PID/path/argv/envのCLIは無い。
raw出力、process一覧、環境値は保存しない。`supported`は子孫所有能力の確認であり、ゲーム/GUI起動合格ではない。

host fixtureはQuestと同じPPID fallbackを必ず通し、通常group・setsid・PTY・二重fork・非leader threadからのfork、
pendingから同ownerへの明示回収、古いcallback/別owner/既存child拒否、無関係な同UID siblingの生存を確認する。
TermuxのSELinux/kernelでの実capabilityはoperatorの結果で別に判断する。

## Android CPythonのpidfd wrapper欠落

最初の実機probeは`PLATFORM_UNSUPPORTED`で、child spawn前に拒否した。
実環境は`sys.platform='android'`、AArch64/Python3.14.8であり、`os.pidfd_open`と
`signal.pidfd_send_signal`が提供されていなかった。Linux CPythonのwrapper有無とkernel能力は別である。
同UIDの固定own childだけを使った別検証では、kernelのpidfd open/signalが成功し、TERM終了とreapを確認した。

この根拠に限定し、Android/AArch64/LP64だけにBionic `syscall` adapterを追加した。
NDK27.3.13750724の`sysroot/usr/include/asm-generic/unistd.h`で`pidfd_open=434`、
`pidfd_send_signal=424`を照合し、`unistd.h`の`long syscall(long, ...)`に合わせる。
番号は`long`、pid/fd/signalは`int`、flagsは`unsigned int`、NULL siginfoはpointerで渡し、errnoを保持する。
Linuxでは既存Python wrapperを使う。他architectureへの番号の外挿や、PIDだけへのsignal fallbackは行わない。
adapterが開いたlive fdだけをinstance内で追跡し、close時に集合から除く。番号再利用後の別fdを送信先にしない。
元のunsupported結果は保持し、この修正後のcapabilityを別runで判断する。

Android対応後の次runもspawn前で停止し、他PIDの`/proc/<pid>` directory statがEACCESになることを確認した。
以後はUIDを確認する前の他PID directory stat拒否をskipする。一方、空snapshotには必ずkernelのchild証拠を加える。
`waitid(P_ALL, WEXITED|WNOHANG|WNOWAIT)`がECHILDの時だけ空を認め、Noneのlive/stopped child、
waitable zombie、権限不明を空へ変換しない。この条件は専用ownerの初期取得と終了時の二回空scanの両方に適用する。
kernelが知らせたPIDをsignal先にする変更は無い。隠れたown childはpendingで保持する。
fixtureでは他PID stat拒否のskipと、意図的にprocから隠した自分のlive child/zombieが完了を妨げることを確認する。

通常GUIの実装では、worker hashを既存helper本体へ固定し、親helperはworkerの実handleを保持する。
pending workerを強制killして孫の所有を失った状態を「回収済み」にしない。
最初のGUIから使える隠れFootserver anchor、最後のGUI終了後のanchor維持、session Stop時のworker→anchor回収を
後続のhelper/launch-cookie/slot契約と合わせる。既存terminalのfootclient経路とworkload識別は維持する。
