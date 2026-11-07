# SDL小probeのsource配備

`scripts/quest-install-sdl-probe`は固定C sourceとPython runnerをTermuxへ配備する。
コンパイル・package導入・SDL起動は別の操作であり、配備の成功をQuest描画の受入れへ数えない。
[SDL/SHM検証計画](sdl-shm-probe-20261107.md)と組み合わせて使う。

## コンパイルと共有runtimeの確認

source配備ツールはTermux packageを変更しない。SDL/Mesaを追加した際は、既存共有runtimeとの整合も別に確認する。
Build154では新Mesaの`libgallium-26.2.6.so`が新しいlibc++ APIを必要としたが、
既存`libc++=27c`を維持したままpackage追加自体は成功した。
vendorの実dlopenで未解決symbolを特定し、`libc++=30`だけの更新でそのloader失敗を修復できた。
根拠と範囲は[固定stageの結果](sdl-egl-stage-probe-20261108.md#build154の実機結果と次の切り分け)に記録した。

当日のTermux同UIDでの確認手順は次の通り。30は実測時の候補版であり、今後も無条件に固定する運用値ではない。

```bash
dpkg-query -W -f='${Package} ${Version}\n' 'libc++'
apt-cache policy 'libc++'
apt-get --simulate install --only-upgrade 'libc++=30'
# 当日は更新1件・追加0件・削除0件。対象版と影響範囲を記録して実行した。
apt-get install --only-upgrade 'libc++=30'
```

更新前後の`libc++_shared.so`のcanonical path/SHAと必要symbolのexportを記録し、
新しい短命processでvendorをdlopenして確認する。JSONやlibraryの存在だけでは依存解決の成功を保証しない。
SDL/Waylandのstage・SHM・SDK描画・停止回収はその後に別々に判定する。

## sourceの配備

```bash
# 対象のQuest 3を照合してから、USB serialまたは照合済みWi-Fi endpointを指定する。
scripts/quest-install-sdl-probe --serial "$EIDOLON_QUEST_SERIAL"
```

host側のartifactは実行時に読み直す`codex-work-root`配下の`eidolon-sdl-probe-install/`へ保存する。
`--artifact-root`で別の絶対pathを指定できる。`--run-id`は省略時に32桁hexを生成し、指定した場合も既存runを再利用しない。

配備先はTermux homeの`.local/state/eidolon/nx11/<run-id>/`で、次の3fileだけを置く。

| file | 内容 |
| --- | --- |
| `sdl-wayland-probe.c` | repositoryの`scripts/fixtures/sdl-wayland-probe.c` |
| `eidolon-sdl-probe.py` | repositoryの`scripts/termux/eidolon-sdl-probe.py` |
| `manifest.json` | schema、run ID、source commit/dirty、両sourceのsize/SHA-256 |

`run-as com.termux`の`HOME`はapp data directoryを指す場合があるため、その値を保存先に使わない。
実際に起動したTermux Pythonのcanonical prefixを検査し、`files/usr/bin/python*`から`files/home`を導出する。
結果へ`home_source=python_prefix`を記録する。固定の`/data/data/.../home`や現在のWayland socket名を配備先へ埋め込まない。
Android標準の`files`親が0771の場合は、ownerとgroupが同一appのUID/GIDでworld-writeがない場合に限り許容する。
`usr`・`bin`・home以下の共有書込み禁止は維持し、端末上の既存modeを変更しない。

転送JSON全体のSHA-256とUIDを照合し、内容のsize/hash・固定filename・manifestを検査してから書き込む。
homeと親directoryは同UID・共有書込み不可、親symlinkは`openat`と`O_NOFOLLOW`で拒否する。
新規runは0700、source/manifestは0600とし、既存fileを置換しない。manifestは最後に保存し、全fileの読戻し後だけ成功を返す。
失敗途中のrunが残った場合も同じrun IDで上書きせず、保存ログを確認して新しいrunを使う。
製品helper・SSH認証・通常設定・`.eidolon/s-*`の所有session directoryは変更しない。

host側は`payload.json`、同じ`manifest.json`、`installer.log`、照合済み`result.json`を保存する。
manifestの`source_commit`だけでdirty treeの内容を特定せず、fileごとのhashを再現の基準にする。

PC検査:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.tests.test_sdl_probe_install -v
```

localhost fixtureでは、同UIDへの新規配備・private mode・hash読戻し、既存run不変、home/親symlink拒否、
不正filename/重複JSON/上限/型/UID拒否、実機と同じstdin envelopeとPython prefixからのhome導出を確認する。
実機での配備・Bionic compile・SHM描画・停止回収はrootの次の検証として記録する。
