# NX11: Chocolate DoomとFreedoomの最小検証準備

2026-11-08時点。固定Kopper無効対照でEGL1.5/SDL GL・GLES contextが成立し、
SDL software描画120frames/SHM120commit/SDK更新・退役・元foot維持と所有process回収も確認した。
ゲームの実行・package導入・通常GUI解禁はまだ行っていない。

## 固定するengineとデータ

[Termux公式recipe](https://github.com/termux/termux-packages/blob/fbdb3c03610c697322ac608e77cea2b553f49ef3/x11-packages/chocolate-doom/build.sh)の
Chocolate Doom 3.1.1を使う。実機のapt候補も3.1.1だった。SDL2/SDL2-compat、SDL2_mixer、SDL2_netに依存する。
当日の版固定simulateは更新0・追加7・削除0で、Chocolate Doom3.1.1、SDL2_mixer2.8.2、SDL2_net2.4.0、
fluidsynth2.6.2、libxmp4.7.4、libmodplug0.8.9.1.r461-1、opusfile0.12-4だった。まだ導入していない。

ゲームデータは商用WADを含めず、[Freedoom公式v0.13.0](https://github.com/freedoom/freedoom/releases/tag/v0.13.0)のPhase1を使う。
[COPYING](https://github.com/freedoom/freedoom/blob/v0.13.0/COPYING.adoc)に従い、WADとともにCOPYING/CREDITS/CREDITS-MUSICも保持する。
hostで公式archiveを取得し、[公開CHECKSUM](https://github.com/freedoom/freedoom/releases/download/v0.13.0/freedoom-0.13.0-CHECKSUM)の
SHA-256と一致することを確認した。署名keyの独立照合は行っておらず、PGP検証済みとは扱わない。

| 配布物 | bytes | SHA-256 |
| --- | ---: | --- |
| freedoom-0.13.0.zip | 24143781 | `3f9b264f3e3ce503b4fb7f6bdcb1f419d93c7b546f4df3e874dd878db9688f59` |
| freedoom1.wad | 28795076 | `7323bcc168c5a45ff10749b339960e98314740a734c30d4b9f3337001f9e703d` |
| COPYING.txt | 1644 | `7c62f2c520769c798774f416637eec4921e5f0aafdac1245ae7c8a8cf65fe102` |
| CREDITS.txt | 15872 | `72364333d295c598d75ef9553d3b3661ad0b60e27271c87b7637c74b2f46918a` |
| CREDITS-MUSIC.txt | 9388 | `2bb77677bbc4e587bba44ccb6514ee0f5397c7bb8ae9580b9188d3d6a57c0a31` |

## 自然終了と対話操作を分ける

最初は元WADのDEMO1から128ticだけを取り出した固定demoを検討する。
このWADのDEMO1はversion109、1 player、episode1/map6、13byte headerと4byte/ticである。
headerと128ticを保持し、終端byte `0x80`を付けると526 bytes、SHA-256は
`6cdd695fa613d6291c4e0fae9fef5237337cba806a4464beaf945934201487ce`になる。
これは計算と形式確認までで、実engine再生は未確認。

`-playdemo eidlsmok.lmp`なら[upstreamの通常終了](https://github.com/chocolate-doom/chocolate-doom/blob/chocolate-doom-3.1.1/src/doom/g_game.c)を
使える見込みで、約3.7秒の内容を描画して終了する。`-timedemo`はupstreamがbenchmark完了にもI_Error/exit(-1)を使うため、
最初の自然終了検証には使わない。ゲーム内の入力を検証する回は別に`-warp 1 1 -skill 1`で起動する。
demoの記録入力を物理キーボードの受入れへ数えない。

## prepareだけを行う最小実装案

1. hostの専用preparerで固定archiveのsize/hashを検査し、WADとlicense/creditsだけを選択読込みする。
   zipの一括extractはせず、重複名・symlink・path traversal・サイズ超過を拒否する。
2. Termux homeから導出した`.local/share/eidolon/freedoom/0.13.0/<run-id>/`へ専用assetを置き、
   config/saveは`.local/state/eidolon/freedoom/<run-id>/`へ分ける。directory0700/file0600、既存runは置換しない。
   payloadの各size/hashとmanifestを照合し、途中失敗のrunも上書きしない。製品の所有session directoryへassetを置かない。
3. 現行catalog schemaの`mode="gui"`、builtin game icon、固定実行file/argv/cwdを持つlaunch definitionを生成する。
   保存済みcatalogへ自動追加せず、`GUI_NOT_VERIFIED`を維持する。terminalへの偽装や任意envのCLIは追加しない。
4. 検証済みGUI lifecycleへ接続する際に固定SDL software profileを使用する。現在のcatalogにはenv fieldが無いため、
   profile接続も後続実装とし、prepareを実行成功と扱わない。

描画条件は[Chocolate Doomのsoftware renderer](https://github.com/chocolate-doom/chocolate-doom/blob/chocolate-doom-3.1.1/src/i_video.c)に合わせ、
`force_software_renderer=1`、`smooth_pixel_scaling=0`、window640×400、`-nosound -nomouse -nograbmouse`とする。
`show_endoom=0`、`graphical_startup=0`と専用`-config/-extraconfig/-savedir`で既存ユーザー設定を読み書きしない。
Waylandの2環境値は認証済みの現行leaseから引き継ぎ、Mesa CPU描画等の固定profileはSDL検証結果に合わせる。

## 実行へ進む条件と後続の検証

1. SDLのSHM/SDK更新・退役とhelperの所有Stop/取消を先に成立させる。
2. 版固定apt simulateを再確認して必要packageだけ導入し、engine実ELFと共有runtimeの版/hashを記録する。
3. 固定短demoの自然終了、SHM content更新、SDK追加窓・退役、元foot継続、所有process回収を同jobで確認する。
4. 対話回でfocus・通常キーdown/up・focus喪失時のrelease・window close・Stopを確認する。
   観測したPIDをsignal対象へ流用せず、helperが生成して保持したhandle/groupだけを回収する。
5. 一回の自然終了と製品Stop/反復、合成キー配送とHMD/物理操作を分けて記録する。HMD確認は最後に残す。

## host準備の実装

`scripts/prepare-freedoom-probe`は、上記固定archiveだけを検査し、Phase1 WAD、license/credits、
526byteの短demo、専用config templateとmanifestを新規0700/0600のhost directoryへ用意する。
archiveの所有・mode・regular file・size/SHA・読取り前後identityを確認する。
zip全体をextractせず、重複名、traversal、symlink、特殊file、暗号化、不明compressionと上限超過を拒否する。
WAD directory境界、DEMO1の版/1 player/header/source SHAと生成短demoの既知SHAも照合する。
license/creditsが欠ける場合は準備を拒否する。過去runや途中失敗runを上書きしない。

```sh
scripts/prepare-freedoom-probe --archive /absolute/path/freedoom-0.13.0.zip \
  --artifact-root "$EIDOLON_TASK_ROOT/freedoom-prepared-host"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_freedoom_probe_prepare.py -v
```

新しい`launch-plan.json`は**host準備専用schema**であり、launcher catalogへ渡す定義ではない。
`demo128`と`interactive`の固定caseだけを生成し、自由argv/envやterminal modeへの偽装は無い。
engineの実binary SHAは未取得として明記し、modeはgui/available=false/`GUI_NOT_VERIFIED`を維持する。
gameのSDL backendはwayland、描画条件は既存SDLで成立したsoftpipe/Kopper無効profileとする。
Waylandの2環境値は将来の実行時に認証済み現行leaseから取得し、古いsocket/nonceを流用しない。

configはimmutable assetから新しいowned stateへコピーして使うtemplateである。
実engineの`-config/-extraconfig`へ渡す先はstate側とし、終了時のconfig保存でasset pinを壊さない。
このscriptはADB/Termux転送、apt、engine起動、通常catalogへの登録を行わない。
`prepared`はhost file検証の完了だけを示す。

## 実行と通常launcher opt-inまでの残作業

| 段階 | 必要な条件・証拠 |
| --- | --- |
| package | root operatorが版固定simulateを再確認し、必要7packageだけ導入。Chocolate Doom3.1.1の実ELF/UID/mode/SHAと依存runtimeをpinする |
| assets導入 | Termux実Python prefix→HOMEから専用新規asset/state directoryを導出。固定manifest/size/hashを再検査し、owned fileだけ0600で保存。28MB超のWADを引数へ入れず有限stream転送する |
| 所有binding | 固定engine argv/cwd/新stateを生成。SDL Wayland app_idの制御方法を一次sourceと実metadataで確認し、実Popen handle/UID/starttime・full targetを結び付ける |
| demo128 | `-playdemo`の自然終了0、SHM本体/content進行、SDK追加窓とframe/退役、Board/binding/control/MODE解放、元foot維持、owned group/reap/pipeを同jobで確認する |
| 対話と停止 | 固定focus/epoch/intentで合成キーdown/upとcloseを分離確認。ゲームへの反応、focus喪失release、所有Stop・反復・切断を別caseにし、自然quitとsignal終了を混同しない |
| profile付きGUI opt-in | 任意env fieldではなく型付きsoftware Wayland profileをhelper/native契約へ追加。検証済みprofile/engine/assetの明示opt-inだけを許可し、未検証GUIの既定拒否を維持する |
| 最終確認 | HMD視認/物理キーボード/通常launcher操作を利用者が確認する。demoの記録入力を物理入力の合格に数えない |

現在のcatalog `Definition`にはenv/profile fieldが無い。helperの`summary`、`selected_definition`、
`launch_definition`はGUIを拒否し、native `Entry.valid`もGUI availableを拒否する。
一箇所のgateを外すだけでは通常起動を実装できないため、helper・native・launcher表示の契約を合わせてから
明示opt-inへ接続する。今回のhost準備ではこれらを変更しない。
