# APK向けWaypipeのbuildと起動検査（W04）

固定C版Waypipeをhost/API34 arm64へbuildし、APKの`nativeLibraryDir`から実行する。
SHM転送・SSH経由の画像・SDK表示の合格は、W05以降で別に検査する。

## 固定sourceと依存

正本は`native/deps.lock.json`。Waypipe 0.11.3 C版、LZ4 1.10.0 library、Zstd 1.5.7 libraryを使う。
archive SHA256、license、patch SHA256、Meson/CMake optionsを保存する。
`scripts/build-waypipe`はsourceを毎回archiveと固定patchから再構成し、既存cacheの全file/symlinkと比較する。
licenseを原本と照合し、static archive・実行fileのhash、実ELF、version/featuresをartifactへ残す。

Waypipeは`build_c=true/build_rs=false`。dmabuf/video/vaapi/security-context/systemtapを無効にし、
lz4/zstdだけを有効にする。APK helperはAPI34/arm64 PIEで、圧縮libraryはstatic linkする。
C版のclient本体は生のWayland socketを扱う。libwayland/scannerはsecurity-context専用なので、
この構成では不要。未実装のsecurity-context protocolはcompositorで広告しない。
API34のNDKに`posix_spawnp`があり、APK用のbuildへlibandroid-spawnを追加しない。

Termux固定recipeから版とarchiveを照合したが、TermuxのELFやprefix patchをcopyしない。
Waypipeの初回Android buildは必須の`librt`が存在せず失敗した。
`0001-android-libc-realtime.patch`はAndroidだけその別libraryを外し、libcのmemfd/timerを使う。
Linux hostで従来のlibrt探索を変えない。compression libraryのsourceにはpatchを当てていない。

```bash
scripts/build-waypipe --target all
scripts/check-host --suite all
scripts/build-android 46
adb -s "$EIDOLON_QUEST_SERIAL" install -r "$EIDOLON_APK"
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case waypipe --instrumentation --timeout 120
```

実APK検査はWaypipeのhash/source/ABI/PIE/API/NDK/loader/DT_NEEDEDと6件のlicenseを検証する。
Waypipe helperを壊したAPKと、helperを欠落させたAPKも拒否する。

## 起動・停止の検査

native I/O workerが固定`--version`を実行し、`0.11.3`、features `lz4 zstd`、
unavailable `dmabuf video vaapi`を要求する。実行fileのSHA256をAPKのmetadataと照合する。
clientは設計の固定argvと`XDG_RUNTIME_DIR/WAYLAND_DISPLAY`を使い、shellを介さない。
Waypipeのcompositor接続を実Smithay Displayでacceptし、転送socketにも接続する。
pathの存在だけでは成功にしない。この短い接続/EOFは転送protocolや画像の証拠には含めない。

各probeでclientを3回起動・停止する。APK caseはこれを3probe繰り返し、
4probe目のclient実行中にruntime Stopを要求する。
`/proc/<owned-child>/status`の4種類のUIDとSELinux contextを観測する。
PID番号は新しいgroupへ流用せず、spawnしたChildのleaderを未waitで保持している間だけgroupへsignalする。
TERM→最大3秒→KILL→waitの順で回収し、wait後はgroupの消滅をread-onlyで観測する。
stdout/stderrはnonblockingでdrainし、保持は各64KiBまで、一回のpollにも読取り上限がある。
client/object/group/socket/directoryを回収し、停止要求の受付100ms未満・全体10秒未満を検査する。

## Build46の実機結果

APK SHA256: `10dd2cd35a7a4cde75c43a3aa3cf6e5cdabdeab6473584e88507701ff5d92cf1`。
source digest: `32522b2d0d5dcc31706d283f210fb7822ad3751c469cffb655677de27a076327`。
基準commit `8be8bf7`からのdirty buildとして同定する。
Waypipe ELF SHA256: `e903441976575c6f1a0446f41d615752cfad677acdbac5989f8301a0a58c53c9`、DT_NEEDEDは`libc.so`だけ。

Quest `20261105T093854-0e4e328b`で3probe×3回のclient起動・停止がpass。
各probeでcompositor接続3回・転送socket接続3回・wait3回、最大停止5ms、FD数70で一定だった。
実子processのUIDはAPK UIDと一致した。実行中の4probe目にStopし、受付0ms・全回収14msで終了した。
終了後のclient/object/group/socketは回収済み、新runtimeのReady→Stop→Destroyとregistry0も成功した。
自動検査は`scope=native-instrumentation`、`normal_ui_returned=false`として記録する。

host Rust65件・Python28件・launcher12件、追加のsource cache改変拒否1件、実APK Python34件、
debug/release assemble/lint/packageが成功した。Pythonの実APK専用拒否検査はhost単体ではskipする。
同じsourceでwindow-state10case (`20261105T093952-ef3d63b2`)、shm9 (`094000-2a6a0b4f`)、
独立truncate (`094007-178a3707`)、native依存10 (`094012-cd14e9ce`)、forward (`094017-0e2b968f`)、
remote回収7故障 (`094025-d2cc8f17`)も全pass。実lease失効は60341msだった（省略prefixは20261105T）。
W03のCI `41941347548`もhost/Android/report全success。

Termux側にはこの時点でWaypipe/foot/fontconfigが未導入だった。既存のLZ4は1.10.0、Zstdは1.5.7。
W05で必要なpackageとfontを導入し、実際のWaypipe版/featuresを照合してから転送を始める。

通常UI復帰とActivityなしの検査を区別し、HMD・物理入力は最後の受入へ残す。

## 参照した一次資料

- [Waypipeの固定Termux recipe](https://github.com/termux/termux-packages/blob/6c7082a257f4bd15dbca71bdfe1ecab9e3e5bab2/packages/waypipe/build.sh)
- [Waypipe 0.11.3原本](https://gitlab.freedesktop.org/mstoeckl/waypipe/-/tree/v0.11.3/waypipe-c)
- [LZ4 1.10.0 library](https://github.com/lz4/lz4/tree/v1.10.0/lib)
- [Zstd 1.5.7 library](https://github.com/facebook/zstd/tree/v1.5.7/lib)

rawログ・APK・端末固有情報は設定された作業rootの`eidolon-phase5-implementation/`へ保存する。
