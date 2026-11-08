# NX11: 固定Freedoom GUIを通常helperへ接続する

固定demoとTermux pidfd能力probeの実機成立を前提に、専用workerを通常helperへ接続する。
本checkpointのQuest表示・物理入力・SDK全退役は未確認で、hostのprocess/構造化契約の検証と分ける。

## 明示opt-inと拒否境界

launcher Definitionに任意envを追加せず、次の固定execution profileだけを扱う。
これは接続先を保存するLinuxConnectionProfileとは別の型である。

```toml
[[apps]]
id = "freedoom"
label = "Freedoom"
mode = "gui"
icon = "builtin:game"
executable = "/data/data/com.termux/files/usr/bin/chocolate-doom"
args = []
cwd = "."
execution_profile = { kind = "freedoom_phase1_software", assets_run_id = "af24550bb2416de36657b515895b0a6d", manifest_sha256 = "0fcab0db176ef675c9eb3857547458ed424b6d8c6af3fd160f05369aec2ee727" }
```

既存catalogへ追加する項目であり、他のterminal項目を置き換える全設定ではない。
root operatorが配備済みasset/engine/runtimeを照合してから、既存Catalog.saveのrevision検査を使い明示追加する。
このcheckpoint自身は利用者のcatalogを変更しない。未作成の場合に標準terminal/editorを失わない扱いも登録側で行う。

executableは固定Chocolate Doom、argsは空、cwdは`.`、interpreterは無しに限定する。
実際のargv/env/stateはworkerが生成する。未profile GUI、別SDL/GTKアプリ、未知profile、
terminal modeへのprofile付与、自由env/argv/interpreterは拒否する。
GUIの拒否は既存footの停止と分ける。source、asset、engine、GL runtimeが変わった場合も、
GUIだけを拒否し、既存footの追加とStop/cleanupを維持する。

## 起動receipt・cookie・slot

通常の`launchRegistered`/`launch_terminal`経路を使い、helper内でterminalとGUIを分岐する。
GUIをfootclientやPTY内のterminal-appへ偽装しない。terminalの既存経路はそのままである。

- `terminal_opened`に`mode=gui`を加える。旧helperのmode無しreceiptはterminalとして読む。
- GUI cookieは`io.github.tinmachine.eidolon.gui.g-<nonce>-<launch-id>`とし、foot/workload cookieと分ける。
- nativeのslot相関はrequest、receiptのmode/accepted、同nonceのcookie、既存window除外、Mappedを照合する。
  app_idだけで完了へしない。GUI cookieをPTY workload登録へ流用しない。
- foot/GUI混在でも起動ordinalを一つにまとめ、途中拒否で既存footの数やrequestを壊さない。
- worker終了は`gui_exited_v1`、所有数は`gui_state_v1`として本文なしで記録する。
  最後の16件の元returncode・pidfd回収receiptは、既存のdurable session終了receiptにも残す。

最初のGUIには下層API `Launcher::start_app_target` / `start_application_v1`を用意した。
隠れFootserver anchorを一つ起動し、foot窓を作らずGUIを開始できる。
最後のGUIが終了してもanchorはsessionのStopまで残り、次のGUI/footを受け付ける。
Androidの既定UIは従来どおり最初にfootを開く。最初からGUIを選ぶUI導線は後続であり、下層APIの確認と混同しない。

Stop/EOFではGUI workerへcontrol cancelを送り、そのpidfd所有者の回収完了後にanchorを停止する。
GUIを使ったsessionのremote回収期限は12秒、従来の通常経路は8秒とする。
所有不明のpending workerを強制killして孫の所有を失った状態を完了にしない。

## 3file bundleの再現手順

対応するsourceのAPKをbuildし、既存sessionを正常停止した上で同じsourceを配備する。

```sh
scripts/quest-install-launcher --serial "$EIDOLON_QUEST_SERIAL" \
  --artifact-root "$EIDOLON_TASK_ROOT/gui-helper-install"
```

CLIは従来のままで、固定`eidolon-session.py`、`eidolon-gui.py`、`eidolon_gui_owner.py`だけをstdin bundleへ入れる。
主helperのSHAがworkerのSHAを固定し、workerがownerのSHAを固定する。
全file/manifest/payloadのsize/SHA、source前後identity、名前・所有・symlinkと上限を検査する。
run-asのHOMEを使わず、実Python prefixからTermux HOMEを導出する。
保存先は従来の`.local/lib/eidolon/`、directory0700/file0700である。

owner→worker→主helperの順にstaging fileをfsync/replaceし、主helperを最後に交換する。
途中失敗は旧主helperを残し、依存先のhash不一致でGUIを拒否する。
一時fileを回収し、installerのraw stderr/本文を保存せず、固定reportとbyte数だけ残す。
他のlibrary、設定、asset、認証fileを変更しない。

## 検証の範囲

hostの実Popen fixtureで、初回GUI→終了後foot、foot/GUI混在とordinal、last GUI後のanchor維持、
control EOF/StopのGUI→anchor順序、自然RC7保持、setsid孫の回収、runtime変更中のGUIだけ停止を確認する。
catalogのinline TOML roundtrip、他terminalの保持、未検証GUIと自由envの拒否、
hash鎖・symlink・改変・bundle途中失敗の拒否も検査する。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p 'test_termux_gui_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p test_launcher_bundle.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -p test_catalog.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -p test_application_launch.py -v
```

RustにはDefinition/profile、GUI/foot cookie分離、receipt schema/元returncode/cleanup拒否のunit caseを追加した。
このcheckpoint作成時は容量制約でRust compileを保留し、rustfmtによる構文確認まで実施した。
root統合時のmodel/transport compileと、同jobのSDK full-target/Board/control/MODE退役・baseline foot維持、
合成キー・window close・Stop反復を次に確認する。HMD/物理キーの確認は最後に残す。
