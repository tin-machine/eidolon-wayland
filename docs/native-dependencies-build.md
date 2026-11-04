# Native依存の固定build（W01実装中）

W01のnative実装はmainへ統合済み。製品Rust/Gradleへの組み込み、
Smithayの最小feature検査、hostでのsocket/FD/memfd/mmap/keymap検査は成功。
QuestのAPK UIDでも10cycle成功した。通常の製品UI復帰は未確認のため、W01はin_progressを維持する。
SHM buffer/SIGBUS/一般Wayland clientは[W02の検証記録](wayland-shm-checks.md)で扱う。

`native/deps.lock.json`がsource版・archive SHA256・license・Meson option・keymap hashの正本。
libxkbcommonは事前probeと同じ1.8.1、キーマップdataは
[上流配布archive](https://www.x.org/releases/individual/data/xkeyboard-config/)の2.48を使用する。
NDK27.3.13750724/API34/arm64-v8aのstatic PIC libraryと、Linux hostの生成用libraryを別々に作る。
targetのpkg-configからhost libraryを検出させず、X11/Wayland tools/registry/docsを無効にする。

```bash
# Linux、C compiler、Python/pip、Ninja、Bison、Perl、patch、固定Android SDK/NDKが必要
scripts/build-native-deps --abi arm64-v8a --api 34
# Android SDKを使わず、生成とhost意味検査だけを行う場合
scripts/build-native-deps --target host
scripts/check-native-deps
scripts/check-host --suite compositor
```

Meson1.7.2はrepository専用cacheへ導入する。source archiveはhash検証後に展開し、
再利用時も全file/symlinkをarchiveと照合する。追加fileや変更があれば、内容を保持して失敗する。
`native/build/<input-hash>/result.json`へ入力hash・library hash・生成keymap hashを残す。
生成directoryはGit対象外。source/tool/optionを変更した場合に古いMeson設定を再利用しない。

USはevdev/pc105/us、JISはevdev/jp106/jpから全includeを展開する。
展開済みtextを、include pathも環境由来の名前も使わない新しいcontextで再parseする。
`a`、Shiftで`A`、Shift+2がUSでは`@`、JISでは`"`となることを検査する。
これはhost上の意味検査で、Androidの実キー入力やIME対応の合格ではない。

| asset | bytes | SHA256 |
| --- | ---: | --- |
| us.xkb | 71,196 | `2993effb1c4286d9fa1b1ae3c5831adf3e7f3a239156a9ded2b8caa3fe4e1838` |
| jis.xkb | 71,708 | `b1abb52fac1a7a2e9dd7b71eaef7b18c15ab389ccae0755f25511ae140cc8501` |

通常buildはtracked assetを書き換えず、再生成したbytesとの完全一致を要求する。
版選定時の明示的な`--write-keymaps`だけがasset/licenseを生成する。
既存lockのkeymap hashと違う出力は、そのflagでもassetへ上書きしない。

試行錯誤:

1. 最初は展開sourceをそのままinclude rootへ渡し、`rules/evdev`が無く失敗した。
   上流Mesonでrulesと互換symbolsを生成・installしてから展開する手順へ修正した。
2. 1.8.1とdata2.48で両layoutの生成・再parse・意味検査が通り、host/Android両archiveを構築した。
3. build先が変わるとdefault lookup pathや`__FILE__`が変わるため、lookup先を固定し、
   source/build pathのprefixを正規化した。異なるinput directoryでも両archiveのbytesが一致した。
   compiler/host OSが異なる全環境での再現性までは検証していない。

## 製品への接続と現在の検証

coreのbuild.rsは現在のlock/builder/generatorとstatic archiveのhashを再計算し、
古いbuild markerを拒否する。`scripts/check-host`と`scripts/build-native`が依存buildを先に実行する。
Smithayは0.7.0のdesktop/wayland_frontendだけ。`wayland-sys`は無featureの型依存として存在するが、
system/client/server/dlopen featureを拒否し、APKのELF依存もNDK whitelistで検査する。
`drm-fourcc`のformat enumはbackendの導入とは別で、DRM/GBM/udev等のbackendは有効にしない。

製品coreの固定primitive検査は、同UIDの専用Unix socketからwl_display.syncのdone/delete_idを往復し、
SCM_RIGHTSでmemfdを渡してCLOEXECとdev/inode/sizeを照合する。4096byteの共有patternをmapping間で読み、
map解除・既知8FD・所有socket/directoryの回収を検査する。US/JISもinclude path無しでparseする。
専用workerをcoreが所有し、停止完了の前にjoinする。ADBから任意path/FD/Wayland messageは渡さない。

Build33はhost Rust41件・Python22件（別途APK依存4件skip）・launcher4件、
debug/release assemble/lint/ELF/同梱検査、実APK付きPython26件で成功した。
keymap assetを壊したAPKも拒否した。APKのDT_NEEDEDは公開NDK libraryだけで、libxkbcommonはstatic link。
基準a3b58b8のdirty build、APK SHA256は`87975fff8f0a5532d8ec7dfa16d9bf6d471e03442e06f6bff1820bf62a1278ec`。

Rust統合testの初回は既存Runtimeのmethod名を取り違えcompile失敗したため、実APIへ修正した。
依存追加でgeneric-array0.14.9から0.14.7へ解決が変わった点も確認した。
Smithay→sha2 0.10→crypto-common0.1.7が`=0.14.7`を要求するため、元の版への固定は解決不能だった。
解決内容をCargo.lockへ保存し、既存transport testも再実行した。

debugの`quest-check --case native-deps`は10cycleの上記primitiveと停止後の製品runtime復帰を検査する。
Activityを開かない検査も同じprimitiveを実行し、新runtimeの生成・停止・registry0を確認する。

```bash
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case native-deps --instrumentation --timeout 120
```

T04統合後のBuild39はhost Rust54件・launcher12件、実APK Python27件、debug/release build/lint/packageに成功。
Quest native-depsは`20261105T071657-e46f6b0b`で全10cycle成功し、各28〜44ms、FD数は全回70、
各回の既知8FDは回収され、残存mappingは0。終了後のruntime停止は13msだった。
同じsourceでforward `071648-73752891`、7故障 `071702-564a0fa4`も成功した（省略prefixは20261105T）。
lease実測は60,334ms。失敗した前buildのjournal残件1件も、次の認証接続がreceiptを検証して解消した。
記録を削除して検査を通したものではない。

統合検査では次を修正した。

- rebaseの競合解消でKotlin条件式を崩してcompile失敗したため、各caseを正しく分離した。
- fixtureが新しい永続journal directoryまで消えたと仮定していた。保存先を保持し、probe固有資源だけを回収する契約へ合わせた。
- JNIのenqueue受付直後に旧Failedを見た再接続検査が失敗。対象request_idのcommand_completeを待ってから状態を判定するよう修正した。
- instrumentation終了が失敗でも、runnerが最終snapshotを保存してから終了codeを判定するよう修正した。

Build39は2d09147からのdirty build、source digest `0932d952b7823e22e7e2ea36f69130f7433d5db04dc0b132fb1ff61c2b743f27`、
APK SHA256 `cf0dd0372ba03a6d5b536a9240afb4087a4b87aaff2f00ca4440f4c1f32f1e14`。
raw artifactは`w01-builds/20261105T071245-2076632/`、`w01-rebased-host2.log`、`w01-apk-tests39.log`、
`w01-release39.log`、`w01-release-package39.json`、各`*-headless39.log`とchecksへ保存した。
`scope=native-instrumentation`の結果はUI復帰として扱わず、通常caseのUI gateを別に残す。

raw logは設定された作業rootの`eidolon-phase5-implementation/w01-deps*.log`へ保存している。
T04の実機検証候補を変更しないよう、W01は独立checkoutで作業してから製品へ統合する。
