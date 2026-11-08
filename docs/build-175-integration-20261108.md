# Build175: 通常GUI配線と視点姿勢の実機検証

2026-11-08。APK source `da754f9`、入力digestは
`6c24e8e4c7a5ec7e4d625fef8eab6bee5fc342fe7fd81e39b67c2f15eb4209b2`。
固定Freedoomの[型付きGUI配線](freedoom-typed-gui-connection-20261108.md)、
通常IMEの固定入口検査、groupのnative/input gate、視点姿勢の読取りを統合した。

## 検証済み範囲

- debug/releaseのassemble・lint・package検査が成功。JVMは265/175件、失敗・error・skipなし。
- model106件、transport66件、GUIをfootグループへ分類しないcore回帰1件、workspace all-target clippyが成功。
- Python442件中435件実行・7件skip、Termux114件が成功。
- 旧Build174を10cycles/Idle/pending cleanupなしへ停止後、3file helper bundle197202 bytesを配備した。
  hash鎖の照合と、新APKで通常footの起動・明示focusが成功した。

| APK | SHA-256 |
| --- | --- |
| debug | `41ddae20b34bdcf54d959352fd834d0c40469e12a9ea5fab4cf8c1857f975d44` |
| unsigned release | `eeea5366989dea93064a871b60e0cf2d9d2362dddfe66282028225ad95bcc4b1` |

通常GUIの実起動・操作・SDK退役は、この配備/foot成功からは推測しない。
GUI/foot receiptの統合修正は[所有境界の記録](gui-foot-ownership-boundary-20261108.md)に分けた。

## 視点姿勢の読取り

`viewer175/80d7d5b17fc582461b84e1aafa5dee6f/`で固定20samplesがPASS。
同runtime/sceneとrender進行を確認し、20回とも有限・正規化・bounds内の正面候補を計算できた。
SDKのyaw計算と一致し、motionは観測、零原点は観測しなかった。
全体3239ms、SDK読出しの最大計測値0ms（計時分解能未満）だった。

native命令・SDK mutation・追加resourceはいずれも0。
tracking timestamp/valid flagは公開APIに無いため`tracking_freshness=not_exposed`を維持する。
motionやrender進行をtracking精度・鮮度の証明にはしない。
生のviewer poseや室内写真は記録せず、正面配置の実適用・HMD確認もfalseのまま保持した。
次の[正面配置case](nx12-recenter-sdk-case-20261108.md)で操作を検証する。

## 通常IMEの観測バグ

`normal-ime175/58f5b503d832aba208bdb14a26bd96d2/`のONは受理され、
owner_claimed/owner_before_view/current runtime/input intentがtrue、native phaseはENABLINGだった。
しかしJSONのactiveがfalseとなり、固定検査は`initial_owner_unconfirmed`で失敗した。

原因は`JSONObject.apply`内で`active === this`としたため、RunではなくJSON receiverと比較していたこと。
切替要求の拒否とは区別し、BOUNDや通常入口の成功には数えない。
JSON構築前にRunのactiveを捕捉する修正を`d609e15`へ分離した。
失敗した所有sessionはnative-deps10cyclesで停止し、Idle/pending cleanupなしへ回収後に視点検査を行った。
修正後の通常ON/OFF、実かな漢字変換、物理Ctrl+Spaceは別の受入れに残す。

## ランチャー登録の環境差

導入済み175と同じsourceを隔離checkoutに用意し、host-only登録ツールを追加してsource digestを照合した。
inspectは既定2項目・catalog未作成・asset/engine/runtime検証trueで成功した。
初回登録はTermux Python3.14.8に`os.link`が無く失敗した。
例外tracebackのsource全文を記録せず、class/能力/固定reasonを調べる方針へ修正した。

Androidの公開libc linkatは存在したが、専用owned fixtureの実callはerrno13で拒否された。
一方、renameat2のRENAME_NOREPLACEは新規採用に成功し、既存targetにはerrno17で失敗してinodeを保持した。
fixtureは全回収済み。通常catalogとゲームデータは変更されていない。
この根拠で[登録ツール](freedoom-normal-launcher-20261108.md)のAndroid経路を補完する。
単純な上書きrenameへのfallbackは行わない。

## 保存と次の確認

設定済みwork rootの`eidolon-ime-implementation-20261107/`にsource175 archive・JUnit・APK・全試行を保存した。
rootのbuild/operatorとagentのisolated source作業を分離し、ADB/配備はrootだけが実施する。
端末の近接センサー対策とguardian pauseは維持している。

Build174までをpushした`74f1134`のGitHub Actions `42427524852`はhost/android/reportすべて成功した。
Build175以降のmain変更は、続く統合実機検証とfetch後にpushする。
次は通常IMEの修正確認、正面配置の単独成立、groupのSDK case、固定Freedoomの通常起動・入力・二段階close・Stopである。
重なる複数窓の正面配置、通常Linux GUI、物理/HMD、既存Phase5/6の残る条件も別に保持する。
