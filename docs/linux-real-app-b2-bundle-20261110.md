# B2: v3 bundle、source closureとimmutable配置

2026-11-10（日本時間）。**C1/C2の完成source・commitが未提供であり、B2の最終統合は未完了**。
この文書は[B1契約](linux-real-app-b1-contract-20261110.md)を継承する。
schema/hash式とB1固定vector、固定v2の5 source、既存bundle directoryは変更しない。
本変更の構造検査やbyte publisher検査を、実v3 bundle/pins・通常acceptanceの成功とは扱わない。

## 入口と責務

| 入口 | 責務 |
| --- | --- |
| [build-linux-desktop-v3-bundle](../scripts/build-linux-desktop-v3-bundle) | build、read-only print-pins、不足sourceの確認 |
| [linux_desktop_v3_build.py](../scripts/linux_desktop_v3_build.py) | builder専用compiler、app manifest生成、排他的publication |
| [linux_desktop_v3_bundle.py](../scripts/linux_desktop_v3_bundle.py) | 厳密bundle schema、source/import closure、immutable bytes検証 |
| [linux-desktop-v3-sources.json](../scripts/linux-desktop-v3-sources.json) | Python/Rustで一致させる一つのsource inventory |
| [bundle tests](../scripts/tests/test_linux_desktop_v3_bundle.py) | 欠落依存、hash/field/import拒否、同build-id保護、no-follow検査 |

builder専用moduleをremote source集合へ混ぜない。remote bundle validatorはstdlib、B1 contract、既存inspectorだけに依存する。
固定GIO module/probeは[既存builder](../scripts/linux_real_app_build.py)を再利用する。
コンパイラやgio-querymodulesの子process所有・出力上限・回収も同じ既存境界を利用する。
build/print-pinsはMousepad、固定GTK demo、settings probe、version/helpコマンドを起動しない。
`available` / `production_acceptance_passed` / `quest_verified`はfalseのままである。

## source所有と不足API

inventoryはsource名からrepository相対pathへの固定対応を持ち、SHA256を自身に埋め込まない。
inventory自身も同じbasenameでbundleへ入り、APKの`include_bytes!` hash集合と照合される。

| 所有 | source / API |
| --- | --- |
| B1 | `linux_desktop_v3_contract.py`、`linux_real_app_manifest.py`。既存byteとgoldenを維持 |
| B2 | `linux_desktop_v3_wire.py`、`linux_desktop_v3_bundle.py`、inventory、SSH bootstrap/journal/版dispatch |
| 既存共有source | `eidolon-linux-session.py`、`eidolon_linux_process_owner.py`、inspector、fixed demo C、settings C、固定probe C |
| C1（未提供） | `eidolon_linux_real_app.py`。永続profile、owner record、lock、no-follow reopen、回収barrier |
| C2（未提供） | `eidolon_linux_desktop_v3.py`、`eidolon_linux_desktop_session_v3.py`、`linux-desktop-v3-bus.conf.in`。通常helper、private bus、settings、起動guard、lease/drain/Close/Stop/Recover |

C側の4名は受渡し予定のsource名である。実際のC1/C2 PRへ合わせた変更が必要なら、B2統合で一括してinventory、Rust include/hash集合、bootstrap、testsを更新する。
現時点でC1/C2の依存PR・commitは未提供のため、`integration.c1_commit/c2_commit`は明示`null`。
空helper、成功stub、仮の製品hashは置かない。

固定base helperのfile名はv2と同じ`eidolon-linux-session.py`を維持する。
C2からは固定alias `import eidolon_linux_session`で参照し、bootstrapのverified-bytes loaderがその一つのfileへ対応させる。
自由な`importlib` path指定をC側へ要求しない。

標準library以外のimportは同じ固定集合にあるflat moduleに限る。全Python sourceのASTを検査し、未実行branch内の未pin import、relative import、`sys.path`/alias変更、自由dynamic loader等を拒否する。
既存inspectorのmanifest読込みは、**固定file名・loader式そのものが一致する3つのcall**だけを例外にする。
file名だけを根拠に任意loaderを許可しない。import機構自体の根拠は[Python importlib公式仕様](https://docs.python.org/3/library/importlib.html)。
AST監査はPython sandboxではない。実行許可はAPKに固定したsource hashと、全bytes照合を終えたbootstrap loaderが担う。

## hashの方向と生成物

1. 明示source集合とbuild済みbinary/registration bytesを固定する。
2. 既存inspectorでpackage所有、root所有build evidence、実Makefile設定、依存hash、schema、system settingsを検査する。Mousepadの版番号だけでは成功しない。
3. B1の15 field app manifestをcanonical化する。
4. source、binary、設定登録情報、app manifest、検査recordの全file hashを`desktop-bundle.json`へまとめる。
5. canonical `desktop-bundle.json` bytesのSHA256を`desktop_bundle_sha256`とし、B1の4 field式で最後にbuild-idを計算する。

`desktop-bundle.json`の固定fieldは次の6つ。未知・重複・欠落field、誤型・誤revision、非canonical bytesを拒否する。

```text
schema=1, backend=linux_desktop, protocol_revision=eidolon-linux-desktop-v3,
base_bundle_sha256, app_manifest_sha256, files={fixed relative path: SHA256}
```

生成物は`app-manifest.json`、`reviewed-package.json`、`package-inspection.json`、`eidolon-gtk3-desktop`、
`gio/libeidolon-settings.so`、`gio/giomodule.cache`、`linux-settings-probe`、`settings-backend.json`の8つ。
settings moduleのsource/binary/probe/登録情報を相互照合する。合成GUI用observerは含めない。
bundle自身のdigestや最終build-idはhash対象fileへ戻さない。`desktop-bundle.json`自身もその`files`集合へ含めない。

## 配置と上書き拒否

配置先は固定である。

```text
<real-home>/.local/lib/eidolon-linux-desktop-v3/<build-id>/
```

既存baseは`<real-home>/.local/lib/eidolon-linux/`のまま保持する。
builderは同じparent配下の新規private stagingへ全bytesを書き、file/directoryをfsyncし、
[Linux renameat2のRENAME_NOREPLACE](https://man7.org/linux/man-pages/man2/rename.2.html)でpublishする。
事前existence検査後に別processが空directoryを作った場合も、そのdirectoryを置換しない。
このsyscallが使えない場合に、上書き可能なrenameへfallbackしない。

bundle-rootは0700、完成build-id directoryとgio directoryは0500、source/manifest等は0400、
固定demo/settings module/probeだけ0500。ownerとregular file/link countを照合する。
各path componentを[O_NOFOLLOWとdir_fd](https://docs.python.org/3/library/os.html#os.open)で開き、
fileのopen前後・path identityを比較する。未知file、追加module、symlink/hardlink、mode不一致は拒否する。
同build-idは空でも既存でも上書きしない。旧build-idは削除しない。

## RecoverとLaunchを分ける

`validate_bundle(home, pins, source_hashes, inventory_raw)`はcaptureされたtrusted source集合を引数とし、
immutable directory/manifest/file/hashと保存されたpackage検査recordだけを照合する。
この関数は現在の`/usr/bin/mousepad`、package database、依存libraryへ問い合わせない。
base helper/Waypipe/SHMの固定pins照合はimport前のSSH bootstrapが行う。

`validate_launch_package(verified_bundle)`は別入口で、現在のpackageと依存fingerprintを既存inspectorへ再照合する。
C2はinspect/startの適切な時点でこれを呼び、その後もprofile lock・復元guard等の起動条件を満たす必要がある。
RecoverでこのLaunch専用入口を呼ばない。package更新・削除後に正しい旧immutable ownerを回収する経路を保持する。
実回収とreceipt生成はC2の責務であり、B2のsource検証だけでclean済みへ変換しない。

## 現在の操作と最終統合条件

```bash
scripts/build-linux-desktop-v3-bundle --source-status
scripts/build-linux-desktop-v3-bundle --reviewed-package /absolute/reviewed-package.json
scripts/build-linux-desktop-v3-bundle --print-pins --build-id <captured-64-lower-hex>
```

現在、source-statusは4つの不足sourceとnull commitを返し、build/print-pinsはcompilerや製品filesystemに触れる前に`BUNDLE_SOURCE_MISSING`で拒否する。
低水準publisherのtestsは任意の非実行byte fileでatomic配置を確認するだけで、架空helperから製品pinsを出さない。
未提供Csourceを含むbundle全体の成功、現packageを削除した後の**実owner** Recover、通常v3受入れはまだ検証できない。

最終統合では確定したC1/C2 commitを取り込み、4 sourceと全推移的依存を確認し、inventory・Python allowlist・Rust include/hash表・bootstrapとtestsを一つの変更で揃える。
source/manifest/settings/probeの実bytesからbuildし、未知file/import拒否と結合CIをそのheadで成功させてから、実bundle/pins完成と判断する。
依存PRは自動mergeしない。E1はstorage保存、D/E2はnative admission/表示/通常UI、Fは実v3・更新Recover・Quest受入れを引き続き所有する。
