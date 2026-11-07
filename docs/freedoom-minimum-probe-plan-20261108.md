# NX11: Chocolate DoomとFreedoomの最小検証準備

2026-11-08時点の計画。SDL/Mesaのloader整合化後もEGL初期化は未合格のため、
ゲームの実行・package導入・通常GUI解禁は行っていない。

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
