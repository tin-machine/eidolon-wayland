# NX18 C2a: Linux端末の子孫processをlive ownerで回収する

通常Linuxのfootを接続する前に、PTY shellやjobが別process groupへ移る場合の所有境界を追加した。
既存B4/B6の固定SHM/GTK helperとTermux経路は変更しない。
本checkpointは新しいPython libraryとhost上の固定process fixtureまでであり、foot/Waypipe実起動、
APKへの組込み、通常Linux接続、ランチャー、Quest表示の成功は含まない。

## groupだけでは不足する理由

LinuxのPTY shellは新session/groupを作り得る。Waypipeのgroupだけをsignalし、leaderの終了だけを
待っても、別groupのshell/jobが終了した証拠にはならない。
専用helperをsubreaperにすると、途中の親が終了した子孫は生存している最寄りのsubreaperへ引き取られる。
[Linux man-pages: PR_SET_CHILD_SUBREAPER](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)

signalは生きている所有者が開いたpidfdへ送る。PIDの番号だけを後日再利用せず、fd取得前後で
PID/starttime/全UIDを照合する。pidfdは特定のtaskを参照するため、番号が再利用された別processへ
signal先を付け替えない。[Linux man-pages: pidfd_open](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)、
[Python: pidfd_send_signal](https://docs.python.org/3/library/signal.html#signal.pidfd_send_signal)

## 専用helperの契約

`scripts/linux/eidolon_linux_process_owner.py` はCLIを持たない。固定adapterが内部で利用する。

- non-root / real UIDとeffective UID一致 / single-thread / 既存childなし / SIGCHLD defaultの
  新規helper processだけが `ProcessOwner()` を作れる。既存desktop processへsubreaper設定を適用しない。
- Linux subreaperとPythonのpidfd APIが使用できなければ拒否する。PIDやgroupへのsignal fallbackはない。
- `spawn` は固定adapterが選んだargv/env/cwdだけを受ける内部API。後続protocolが任意SSH commandや
  外部PIDをこのAPIへ渡してよい、という意味ではない。
- `/proc/<owned pid>/task/<tid>/children` を使い、leader以外のthreadが作ったchildも列挙する。
  whole-machineの同UID processを選んで停止しない。途中のexit/reparent/UID変更は成功と扱わず再観測する。
- captureしたPID/starttime/UIDとlive fdを同じowner内だけで保持する。journalからsignal権限を再構築しない。
- 最初にTERM、短いgrace後にKILLをpidfdへ送り、zombieをreapする。専用helperの子孫が空であることを
  連続2回確認し、Popen rootも終了を取得してからcompleteにする。
- timeout・scan上限・所有不一致ならpending。fdとlive ownerを保持し、同ownerの明示的な次の回収だけを許す。
  pendingになったownerで新processを起動しない。helperが失われた場合は未知ownerとしてjournalを残す。
- process内active ownerも一つに固定する。子が自然終了して一時的に空でも、pending ownerを別objectで
  置き換えない。回収完了後に新ownerを作った場合、古いobjectのcallbackは新しい子を検査・signalできない。
- forkしたprocessにコピーされたowner objectは現在PID/starttimeが違うため使えない。

上限は子孫1,024、scan全体のtask 4,096、1scan 250 ms、spawn root 16件。
`finish` の既定期限は3秒、呼出し上限5秒であり、途中のscanにも残り期限を渡す。
この初期root件数は通常の長寿命foot server/複数起動の最終予算ではない。後続adapterでroot statusの寿命と
command数を整理し、9窓予算と別に固定する。

`finish` が完了してもstdout/stderrやWaypipe socket/journal/SDKを回収済みとはしない。
adapterはpipeのEOF/close、remote socket/receipt、native/SDK資源の回収をそれぞれ確認する。
既存desktopのD-Bus/systemd等に依頼して別ownerが起動したprocessを、この子孫所有へ含めない。

## 固定host fixture

```sh
python3 -B scripts/test_linux_process_owner.py -v
```

9件で、通常group、setsid、PTY、二重fork、非leader threadのfork、scan欠測後の同owner再回収、
既存childとfork後objectの拒否、PID/starttime/UID変更、無関係な同UID siblingが存続することを確認した。
pending中のowner再生成と、新owner作成後の古いcallbackも拒否する。
子は固定Python処理だけで最大4秒後に自然終了する。任意PID/path/argv、既存shellへの入力、
実foot/GUI、device/SSH設定は扱わない。出力は件数・status・固定tokenのみである。

host実行では9件すべてPASS。`pending=true` の試行がlive pidfdを保持し、後続の明示回収で
`complete=true/open_pidfds=0/descendants_empty=true` となることも確認した。
これはhost kernel/Python上の結果であり、Linux bundle配備・APK実UIDからの端末実行の合格ではない。

## 次の接続

1. 新しい通常Linux terminal helperへ本ownerを組み込み、固定foot・shell・system binaryの所有検査、
   private session directory、Linux HOMEとlocale、Waypipe0.11.3とimmutable bundle hashを接続する。
2. v2 profile/request/session/bindingに束縛した専用journal、StreamLocal、lease/heartbeatを配線する。
   pending時はlive helperの回収を優先し、新しいhelperが保存PIDをsignalしない。
3. footの実SHM/入力/終了、通信断、process/pipes/socket回収を確認してから通常capabilityを付与する。
   カタログ・任意GUI・profile選択UIは後続に分け、Termux v1をそのまま使える状態を維持する。
