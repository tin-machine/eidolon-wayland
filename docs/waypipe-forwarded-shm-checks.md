# SSH・Waypipeを通すSHMとfootの検証（W05）

2026-11-05、Quest 3のAPK→localhost SSH→Termux Waypipe server→APK Waypipe client→
製品compositorで、固定画像100frameと実footのbufferを受信した。
最初の検査は`scope=native-instrumentation`、`normal_ui_returned=false`。
Build62では`20261105T130318-aa8fb21f`でSDKとの共存・通常UI復帰も確認し、W05をdoneとした。
途中で見つけた検査の完了待ち競合は修正し、PCの厳格な100frame判定を維持している。
SDKパネルへの実foot描画は[D02](foot-render-checks.md)で扱い、HMDの可読性・物理入力は最終確認に残す。

## 所有者と起動順

1. 通常のcredential経路でSSHを認証し、永続journalからSessionIdを採番する。
2. core ownerがそのSessionIdのDisplayと`w`を作り、APK側Waypipe clientを起動する。
3. APK側`p`への実接続と`w`へのWaypipe接続を確認する。
4. remote ownerが専用directoryをprepareし、Waypipe/producer/footの版・featuresを照合する。
5. Routerをarmし、SSH StreamLocal forwardの成功ACK後にだけremote appを起動する。

coreだけがWayland objectを操作する。GL/UIへ渡す画像はowned snapshotであり、共有SHM pointerは渡さない。
local Waypipeの専用I/O workerが子のnonblocking pipeを上限付きでdrainし、coreのsnapshot/Stopを待たせない。
停止はremote receipt→forward/bridge→local helper→Display/pool→directoryの順で進め、
回収を確認するまでStoppedを返さない。remote未到達、取消、回収成功は別の診断値である。

同じ認証済み接続・Displayでpatternを終了してfootを起動し、WindowIdを再利用しない。
fixtureのframe callbackには明示的な仮想clockを使う。これは実HMDのfpsの測定ではない。
通常の製品Sessionは描画target側からcallbackを進める設計とする。

## 再現手順

tools導入とproducerの詳細は[Termux準備](termux-wayland-tools.md)を参照する。
既存のpairing、SSH鍵、profileを使う。端末初期化やsshdの再起動は不要。

```bash
scripts/quest-prepare-wayland-tools --serial "$EIDOLON_QUEST_SERIAL" --install
scripts/build-android 49
# EIDOLON_APKは上のscriptが保存したAPKの絶対pathにする。
scripts/quest-install-launcher --serial "$EIDOLON_QUEST_SERIAL"
scripts/quest-install-shm-fixture --serial "$EIDOLON_QUEST_SERIAL"
adb -s "$EIDOLON_QUEST_SERIAL" install -r "$EIDOLON_APK"
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" \
  --case forwarded-shm --instrumentation --timeout 300

scripts/check-host --suite all
EIDOLON_TEST_APK="$EIDOLON_APK" python3 -m unittest discover -s scripts/tests -v
```

APKの版・native source digest、APK helperとremote producerのSHA256、launcher本体、
両端の実UIDとWaypipe 0.11.3/featuresを照合する。古いAPK/sourceでの実行は拒否する。
producerのstdoutは固定JSONだけを解析し、footの本文、入力、画素/hashは診断へ保存しない。
hash検証済み件数、非一様な画像が来た事実、寸法・世代・資源数だけを記録する。

## Build49の結果

run `20261105T105352-410c6b8d`で次を確認した。

| 対象 | 観測 |
| --- | --- |
| 固定producer | copy/hash/release/callback各100、configure/ACK各1、正常終了receipt |
| foot 1.28.0 | 非一様な画像9copy、1023×762、configure/ACK各1、SHM予算拒否0 |
| ID | 同一認証Session/DisplayでpatternのWindow1→footのWindow2、遅延した別Session要求はSTALE |
| Stop | JNI受付0ms、全回収206ms |
| 停止後 | client/object/surface/window、pool/pixel予算、既知memfd FD/maps、local group/worker、SSH bridge、pending journalがすべて0 |
| 復帰検査 | 新native runtimeの生成・停止・destroy、registry0。通常UIの合格ではない |

APK SHA256は`9bb290a02f41d0e2e0c5b969850a2b73dd5c34df44759db03ff10a09f777dd83`、
source digestは`87b53eaa309ade170798132bd63e7627471636e950fedb644efe2bb13e533b9d`。
`c428a64`からのdirty buildとして保存した。後のclean commitから再buildしたAPKと混同しない。
host実Waypipe client/serverと独立producerでも100hash・回収を検査した。

同sourceでdebug/release assemble・lint・package検査、Rust66件、Python実APK37件、
launcher16件、fmt/clippy/designが成功した。実機回帰もすべてnative scopeでpass。

| case | run |
| --- | --- |
| Waypipe起動停止 | `20261105T105416-312f1460` |
| Window state 10case | `20261105T105425-d0f65914` |
| SHM 9case | `20261105T105432-08f4d7cb` |
| 独立truncate/回復 | `20261105T105440-18ba60ee` |
| native依存10cycle | `20261105T105446-254619a1` |
| SSH forward | `20261105T105451-da3ee9e2` |
| remote回収7故障/実lease失効 | `20261105T105500-75af0c06` |

試験後のTermux auditではWaypipe/footは残らず、元からあったshellとsshd等は維持された。

## 試行錯誤と修正

- **host wire検査のdeadlock:** upstream backendはclient列挙とobject列挙で同じMutexを取る。
  `with_all_clients`のcallback内から`with_all_objects_for`を呼び、再入で停止した。
  thread状態とupstream sourceを照合してから、所有fixture PID/startだけを確認して終了した。
  client IDを先に集め、callback外でobjectを列挙するよう修正。W04回収側の同形も修正し、
  実wireの100frame・回収試験が成功した。無関係なprocessは終了していない。
- **Build47:** 固定画像100frameの転送は成功。footはconfigure/ACK後、最初のbuffer前にexit230。
  [foot 1.28.0の一次source](https://codeberg.org/dnkl/foot/src/tag/1.28.0/shm.c)では、
  scrolling用に既定512MiBの仮想SHM poolを作る。compositorの64MiB/client上限への抵触が疑われた。
  この試行のstderr本文は保存していないので、当該errorを直接観測したとは断定しない。
  [`foot.ini`の定義](https://codeberg.org/dnkl/foot/src/tag/1.28.0/doc/foot.ini.5.scd)に従い、
  固定起動引数`--override=tweak.max-shm-pool-size-mb=0`で最適化を無効にした。
  compositorの上限と利用者のfoot設定fileは維持した。以後は実bufferを受信できた。
- **Build48:** pattern100hash、foot9copyは成功。Stopで残ったWaypipeの`w` socketをcleanupが
  対象に含めず`DIRECTORY_NOT_EMPTY`となり、完了receiptが返らなかった。
  remote子は終了、APK側pool/mapsは0だったが、pending journal1件が残り、製品は次の起動を拒否した。
  同じsocket残存をhostで再現してから、作成者だけが自分の`p/w`を検証・削除するよう修正した。
  未知file/不正socketではlease/markerを残し、`CLEANUP_FAILED`を返す検査も追加した。
- **Build48の手動復旧:** 元journalを保護された作業rootへ保存し、該当nonce/UID/owner、
  子PID+startとgroupの不在、stale socketのconnection refused、既知2fileだけを照合した。
  そのsocket/markerだけを除去し、採番last_sessionを保って確認済みの1件だけを手動解消した。
  製品receiptを捏造せず、製品の自動回復成功にも数えない。
  再現scriptと前後journal・照合JSONを`recover-w05-build48.py`/`w05-recovery48-*`として保存した。
- **releaseのcode除外marker:** `eidolon-shm-client-v1`はlauncher共有wire版にも含まれるため、
  fixture codeのlink有無を判断できない。実codeの独立markerを使うようAPK checkerを修正した。

raw logsは設定された作業rootの`eidolon-phase5-implementation/`に保存する。
`w05-host*`、`w05-waypipe-wire*`、`w05-build47/48/49.log`、`w05-forwarded47/48/49.log`、
`w05-socket-cleanup-red/green.log`、`w05-launcher49-final.log`、
`checks/<run>/latest.json`が、失敗→判断→修正→再試験をたどる入口である。

次はD01のowned画像→Android EGL/Smithay rendererと、D02のSDKパネル表示を実装する。
