# TermuxのWaypipe・foot・固定producerの準備（W05）

W04のAPK側Waypipeに対応するtoolsをQuestのTermuxへ導入した。
この文書は版・features・font/terminfo・独立producerの準備を記録する。
続く[W05の実機検査](waypipe-forwarded-shm-checks.md)ではSSH経由の100frame/hashとfootのbuffer受信まで成功した。
SDK表示はD01/D02の受入に残る。

```bash
# 対象を識別し、x11 repositoryと固定版・fontを導入する。
scripts/quest-prepare-wayland-tools --serial "$EIDOLON_QUEST_SERIAL" --install
# 再確認だけなら--installを付けない。
scripts/quest-prepare-wayland-tools --serial "$EIDOLON_QUEST_SERIAL"

# 固定SHM clientをTermuxで実行できるNDK API34/arm64 PIEとして生成する。
scripts/build-wayland-fixture
scripts/quest-install-shm-fixture --serial "$EIDOLON_QUEST_SERIAL"
```

scriptはQuest 3/API34以上を照合し、TermuxのUIDで実行する。
既存のsshd・鍵・profileを変更しない。raw apt/版/配備ログは設定された作業rootの
`eidolon-phase5-implementation/termux-wayland-tools/`と`fixture-install/`に保存する。

## 2026-11-05の導入結果

| package | 実測版 |
| --- | --- |
| waypipe | 0.11.3、features `lz4 zstd`、unavailable `dmabuf video vaapi` |
| foot | 1.28.0、`-pgo +ime +graphemes +toplevel-tag +blur -assertions` |
| fontconfig / fontconfig-utils | 2.18.3 |
| ttf-dejavu | 2.37-8 |
| x11-repo | 8.4-1 |
| liblz4 / zstd | 1.10.0 / 1.5.7 |
| ncurses | 6.6.20260307+really6.5.20250830 |

Waypipe/footは明示版を指定する。版が配布されていなければ失敗し、別版へ黙って追従しない。
fontconfigからDejaVu Sans Monoと日本語を含むfallback fontを検出した。
foot-extra terminfoは4086bytes、SHA256
`9596e67694d38edfa82aa4c57cf4d2ea6ea10b6e25912a9b245a64dfdd69140a`だった。
実入力・IME・HMD可読性の合格はこの導入結果に含めない。

最初の試行ではmain repositoryだけのapt情報にfootが見えず、x11-repoを導入した。
続いてfontconfig自体は導入できたが`fc-list`が無く失敗した。
Termuxではtoolsがfontconfig-utilsへ分かれているため、それを追加して成功した。
初回`20261105T094441-2251903`の失敗と、再試行`20261105T094531-2253798`の成功を保持している。

## 固定producer

`native/fixtures/shm-client/src/main.rs`のCLIは`--version`と`--pattern`だけ。
既存の同UID fixtureと同じ非対称premultiplied画像100frameを送り、protocol counterをJSONで返す。
Waypipe serverが設定する`WAYLAND_DISPLAY`を使い、CLIで任意socket/commandを受けない。
build結果は`native/generated/remote-fixture/`にsource digest・ELF hash・DT_NEEDEDとともに保存する。
配備はstdin転送、hash、owner/regular file、atomic replaceを検査する。

最初のELF SHA256は`79763dc791241712557cabe2def011f2b7e7816a673a7b72785474dd3442b498`、
DT_NEEDEDはlibdl.so/libc.so。Termux UIDで`--version`が成功した。
`eidolon-shm-client-v1`と100frameを照合したが、まだこの時点では画像を転送していない。
source更新後は再buildし、保存metadataと現在sourceが一致したものだけを配備する。

remote launcherは、forward登録前にWaypipe/producer/footの版を照合する。
version子のstdout/stderrは各4KiB、deadlineは3秒とし、過大出力でも保持量を増やさず回収する。
版違い・symlink producer・version出力過多の拒否はhostで検証した。
新しい照合関数をTermuxで直接実行し、両modeの実版とproducer SHAが一致した。
Build49でSSHのTools message・実画像・停止を一貫して検査した。
footには`--override=tweak.max-shm-pool-size-mb=0`を固定で渡す。
既定512MiBの仮想pool最適化を無効にし、compositorの64MiB/client上限内で実bufferを受信する。
利用者のfoot.iniは変更しない。原因調査とBuild47/48の失敗もW05文書へ記録した。
