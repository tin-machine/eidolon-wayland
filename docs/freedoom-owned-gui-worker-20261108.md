# NX11: 固定Freedoomの通常GUI worker準備

固定demo128は実機で自然exit0、SHM74commit、SDK更新・全資源退役、元footとasset/runtime維持を確認した。
また専用Termux workerのAndroid/AArch64 Bionic pidfd、subreaper、PPID fallback、kernel ECHILD証拠の
固定能力probeも成立した。過去のunsupportedとその修正根拠は[所有者の記録](termux-gui-process-owner-20261108.md)に残す。

`scripts/termux/eidolon-gui.py`は、その境界で固定Freedoomだけを扱うpolicy/worker/親managerである。
本checkpointだけでは通常helperやランチャーから呼び出さず、GUI catalogの解禁・Quest上の対話成功には数えない。

## 実行capability

固定profileは`kind="freedoom_phase1_software"`、32hexの`assets_run_id`、既知64hexの`manifest_sha256`だけである。
接続先を保存するLinuxConnectionProfileとは別の実行条件であり、自由env、exec、argv、interpreterは含まない。
engineはChocolate Doom3.1.1の既知SHA/801632bytes/同UID/0700/nlink1だけを許す。
GUI未検証の他SDL/GTKアプリを、このprofileだけで起動することはできない。

専用asset directoryのmanifest/receiptとCOPYING/CREDITSを含む8fileを照合する。
WAD/短demo/license/configのpinは[host準備と同じ値](freedoom-minimum-probe-plan-20261108.md)であり、
未使用のlaunch-planをexec命令へ変換しない。engineに加えSDL2とEGL/Mesa/Gallium/libc++の5runtime、alias、vendor JSONもpinする。
実spawnしたgameのUID/PID/starttime、実executable identityを確認する。

Waylandは現行helperを実parentとして持つworkerだけに渡す。parent PID/starttime/groupと現在leaseのowner、
生存Waypipe child、0700 marker、所有UDSを確認する。socket名を過去runから復元しない。
`XDG_RUNTIME_DIR=<owned lease>`、`WAYLAND_DISPLAY=w`を固定し、GUI固有app_idは
`io.github.tinmachine.eidolon.gui.g-<nonce>-<launch-id>`とする。footのterminal/workload cookieとは別namespaceである。

新しい0700 stateへ600 configをコピーし、`-warp 1 1 -skill 1`の対話回を固定する。
nosound、nomouse/nograbmouse、window640×400、software renderer、Wayland/softpipe/Kopper無効を指定する。
既存ユーザー設定とimmutable assetへゲームの終了時保存を向けない。

## 親managerとanchor

既存helper内の`GuiChildren`は、専用workerの実Popen handle/control pipeを保持する。
主helperがpinしたworker bytesを子で実行し、子はさらにowner libraryのpinを検査する。
新workerの中だけでsingle-thread/child無し/default SIGCHLDのpidfd所有者を作り、既存Footserverへsubreaper設定を適用しない。
raw game出力は有限drainで破棄し、固定counter/returncode/cleanupだけを親へ渡す。

1. 親は所有Wayland socketが用意されるまで最大5秒待つ。初回GUIでも隠れFootserver anchorだけで開始できる。
2. 自分のgameのidentity確認後にGUIの起動receiptを返す。最初のreceiptと追加request、単調増加launch-idを区別する。
3. 自然exit、TERM/HUP/INT、control cancel/EOF、lease/asset喪失、元returncode、pidfd TERM/KILL/reapを個別に保持する。
4. 子孫空のkernel証拠、全pidfd close、pipe EOF、worker waitが揃った場合だけ親recordを退役させる。
   pending workerを強制killせず、観測したgame/foot PIDをsignal先へ転用しない。
5. 最後のGUI終了後もanchorを保持し、次のGUI/footを受け付ける。session StopではGUI workerを先に回収してからanchorを停止する。

hostでは実Popenの自然RC7保持、cancel/EOF、別session孫のadoption/reap、bodyを含む不正wireの拒否を確認した。
初回GUIだけ・最後GUI後もanchor生存、追加起動、launch-id再利用拒否も確認する。
この段階はprocess/policyの証拠であり、SDKのMapped/全target退役や物理キー/HMDの証拠は別に採る。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_termux_gui_worker.py -v
```

次に主helper/worker/ownerの3file hash鎖を持つ配備bundle、型付きDefinition/receipt mode、GUI cookieのslot相関を
通常経路へ接続する。既存のterminal/footclientはそのまま使い、未profile GUIの拒否を維持する。
