# L7a B2: v3 bundle・protocol・版別dispatchの統合境界

2026-11-10（日本時間）。**B2の最終統合は未完了**。B1契約に基づくbuilder、wire、
版別transport、immutable source検証、SSH引数、journal/Recover照合を追加する。
C1/C2の完成source・commitが未提供なので、製品bundle/pinsの完成、通常helperの動作、
v3 acceptance、Quest受入れを主張しない。マージは行わない。

## 基準と依存PR

開始時に[PR #7](https://github.com/tin-machine/eidolon-wayland/pull/7)はopen・未マージで、
現在headは`80321efa9a508f2870fe8da3b5c869f2f15633a9`だった。
mainは`a586f042583ac0869dc6f15e06cacae96db9bd7b`。
PR #1〜#6はmerged、B2/C1/C2/E1の公開PRはなかった。
この時点のtreeにAGENTS.md等の追加作業指示はない。

B1現headから独立checkoutと`feat/linux-desktop-v3-b2`を作成した。
GitHubから取得したrecursive treeとローカル全784 blobを照合し、
tree `0e0d95bde000919cf4161dc6744a8d5611ea2a94`の一致を確認した。
通常git cloneの認証は利用できなかったため、既存objectをコピーしGitHub connectorで照合した。
B1 branch `feat/linux-desktop-v3-contract`をbaseとするdraft PRで受け渡す。
B1が更新・マージされたら差分を確認して追従し、同じ最終headで再検証する。
開始時のB1/main CIの成功をB2の検証へ流用しない。

正本は[B1共有契約](linux-real-app-b1-contract-20261110.md)、
[storage identity](linux-real-app-b1-android-identity-20261110.md)、
[presentation契約](linux-real-app-b1-presentation-20261110.md)と
[既存golden](../scripts/tests/fixtures/linux-desktop-v3-contract.json)。
[着手前handoff](linux-real-app-b1-handoff-20261110.md)と
[L7a設計](linux-real-app-l7a-design-20261109.md)の未決定事項は、B1で確定済みの意味へ戻さない。
[A/A0](linux-real-app-a0-foundation-20261109.md)、
[再起動](linux-real-app-a0-restart-20261109.md)、
[GUI設定](linux-real-app-a0-gui-settings-20261109.md)の実装とgateを維持する。

## 一つの契約と所有範囲

| 担当 | 正本・入口 | 所有する内容 |
| --- | --- | --- |
| B1 | Python/Rust `linux_desktop_v3`、既存contract golden | schema、型、canonical JSON、hash式、catalog descriptor、selection、14/15 argv、storage意味。B2でforkしない |
| B2 wire統合 | [Python wire](../scripts/linux_desktop_v3_wire.py)、[Rust protocol](../native/transport/src/linux_desktop/v3/protocol.rs)、追加wire fixture | wire envelope/payload、固定catalog証拠wrapper、相関、decoder/state machine、Recover receiptの形式 |
| B2 bundle統合 | [inventory](../scripts/linux-desktop-v3-sources.json)、[bundle validator](../scripts/linux_desktop_v3_bundle.py)、[builder](../scripts/build-linux-desktop-v3-bundle)、[APK source table](../native/transport/src/linux_desktop/v3/sources.rs) | 推移的closure、immutable file/hash/manifest、ビルド、pins出力、最終C source pinning |
| B2 transport統合 | [dispatch](../native/transport/src/linux_desktop/dispatch.rs)、[owner](../native/transport/src/linux_desktop/v3/owner.rs)、[journal](../native/transport/src/linux_desktop/v3/journal.rs)、[Recover](../native/transport/src/linux_desktop/v3/recovery.rs) | 事前選択した版・capture・source・journalとSSH引数を照合。認証済みhandleでの要求・応答側 |
| C1 | `scripts/linux/eidolon_linux_real_app.py`（予定） | remote永続root、owner record、lock、no-follow/reopen、永続/一時領域、復元guardの保持 |
| C2 | `eidolon_linux_desktop_v3.py`、`eidolon_linux_desktop_session_v3.py`、固定bus template（予定） | private bus、settings実probe、通常helper、lease/drain/Close/Stop、実process回収、成功receiptの生成 |
| E1 | Android storage codec/repository/store | storage ID生成・保持・交代・atomic/CAS保存。既存profile v2 wireは維持 |
| D/E2/F | native/Android/受入れ | origin admission、固定main、watchdog、SDK ACK/frame、通常UI、実v3 acceptanceとQuest |

wire payloadとreceipt形式はB2の追加契約/fixtureをC2が参照する。
C側でprotocol・journal・bootstrapを並行して再実装しない。
C2の`main(argv)`はB1のserve14／Recover15を同じcontractで検査する。
remote helperがcaptureを採用してよいことと、実profile/processを所有・回収したことは別の条件である。

## source closureと現在の未統合条件

inventoryはbundle相対名からrepo相対pathへの唯一の対応表を持つ。
source自身のhash・最終bundle hash・build-idはinventoryへ入れない。
inventory bytes自体を一つのsourceとしてpinするため、同じbuildでfile集合とC commitの記録も束縛される。

現時点の未提供sourceは次の4点であり、空fileや成功placeholderは追加しない。

- `scripts/linux/eidolon_linux_real_app.py`（C1）
- `scripts/linux/eidolon_linux_desktop_v3.py`（C2）
- `scripts/linux/eidolon_linux_desktop_session_v3.py`（C2）
- `scripts/linux/linux-desktop-v3-bus.conf.in`（C2）

`integration.c1_commit`と`integration.c2_commit`は`null`。
依存PR番号・完成commitは未確定であり、存在しないPR/commitを推定して記録しない。
名前はC側との統合APIであり、実sourceが異なる構成になった場合にはB2がinventory・APK source table・
closure検査・manifest・testsを同じ変更で合わせる。

APKの`current_hashes()`は**現存するcompiled sourceのhash**であって製品pinsではない。
`validate_hashes()`はそのexact集合を照合し、`require_complete()`が未提供sourceとC commit未確定を別途拒否する。
新しいjournal reserve、SSH serve、Recoverの実行境界で完了gateを通す。
source不足をremoteの自己申告やcatalogのboolで解除できない。

build/print-pinsは現在、固定reasonで未完了を返す。appを起動して不足を補わない。
ビルド工程はCLI側に置き、remoteに同梱するbundle validatorへcompiler用moduleを推移的importさせない。
runtime closureにはB1 contract/manifest、wire、inspector、固定base/process owner/demo、settings source/probe、
C1/C2 helper、bus templateを含める。生成物のmodule/probe binary、GIO登録情報、package検査記録もmanifestに束縛する。
A0のGUI observerを製品bundleへ入れない。

Pythonのimport closureは全同梱Python sourceをASTで検査する。
通常importはstdlibまたはpin済みmoduleだけを許可し、未pin import・自由な動的loader・sys.path追加を拒否する。
既存inspectorの固定manifest sibling loaderは、exact ASTとpin済みbytesの両方で限定する。
この検査はレビュー済みsourceのallowlist監査であり、任意のPythonを安全に実行するsandboxではない。
SSH bootstrapは[Pythonのisolated mode `-I`](https://docs.python.org/3/using/cmdline.html#cmdoption-I)と`-S`を使用し、
file hash照合後のsnapshotから[import loader](https://docs.python.org/3/library/importlib.html#importlib.abc.Loader)で読み込む。
current directory、PYTHONPATH、user/site package、未検証のpycを実行元にしない。

hashの順序は **source/binary/template → app manifest → desktop manifest → build-id**。
desktop manifestの`files`にはdesktop manifest自身を入れず、自己参照を作らない。
build-idはB1の4 field式だけを使い、
`<verified-home>/.local/lib/eidolon-linux-desktop-v3/<build-id>/`へ追加する。
同一build-idを含め既存directoryは置換しない。v2の固定directoryにも書き込まない。
immutableのfile集合・mode・hash・unknown fileを検査し、同じ名前に置かれた別bytesを採用しない。

## 版別dispatchとRecover

decoderは保存された版を呼出側で先に選択する。remote bytesから版を選ばず、
v3 parse失敗をv2 parserへ再送して成功させない。v2は既存型と関数をそのまま使用する。
v3はB1のContext/Pins/Selectionを束縛し、異なるID・scope・catalog revision・nonce・requestを拒否する。
wireのsyntax受理とprocess/表示成功は同じ意味ではない。

RecoverのB2側はcapture済みcontext/pins/source、journal版、expected bootとreceiptを照合する。
C2側が実際のowner・pipe・子孫・remote stateを回収し、B2契約に合うreceiptを生成する。
同bootのunknown、旧helperの欠落・改変、誤版journalをcleanへ変換しない。
v2 pendingがあればv3開始を拒否し、旧helperと旧pinsを使う既存Recoverに残す。
保存PIDへの後日signalを追加しない。

`validate_bundle()`はimmutable source/manifestの検査だけを行う。
`validate_launch_package()`は現在のpackage/dependencyを既存inspectorへ照合する別APIで、Inspect/Launch側だけに適用する。
package更新・削除があっても旧immutable helper/manifest/receiptに基づくRecoverの可能性を残す。
RecoverでMousepadやhelp/version commandを起動しない。

## 最終統合を完成と判断する条件

1. C1/C2のPR・完成commitを確認し、内容と所有APIを照合してB2へ取り込む。依存PRは無断でマージしない。
2. 全推移的import・template・module/probe/登録情報を確定し、inventory・include_bytes・source hashes・manifest・拒否検査を一つの統合変更で揃える。
3. 固定inspectorと実settings probeをC2のInspect/Launchへ接続する。Mousepadの版文字列やremote boolだけでは対応済みにしない。
4. 実source/binaryでimmutable bundleを構築・再検証し、B1式から導出した実pinsを出力する。合成値を製品出力へ使わない。
5. 同一最終headでB1/B2 Python/Rust、既存host/Android、専用A0、およびC2との結合CIを確認する。skip・未実行を成功へ数えない。

それまではdraftと「B2の最終統合は未完了」を維持する。
結合完了後もD/E/Fのgateを省略しない。

## 互換性と検証記録

固定v2の5 sourceと[元golden](../scripts/tests/fixtures/linux-desktop-v2-e401918-sources.json)はbyte-for-byte維持する。
v2 hash/schema、固定directory、11/12引数、journal、bootstrap、Recoverの意味を変更しない。
B1共有goldenも変更せず、B2の追加wire fixtureはPython/Rustが同じ保存済み期待値を読む。
テスト実行中に実装から期待値を再生成して通さない。

既存A0のGUI設定、異常process経路、復元保護、全worker回収、共有lifetime、125秒先行保持後のfresh応答を維持する。
`available` / `production_acceptance_passed` / `quest_verified`はfalse、
通常v3 acceptanceは`APP_V3_NOT_IMPLEMENTED`のまま。

最終PR本文に検証head、CI run/job、実行数とskip理由、結合状態を記録する。
新しいコードの存在や過去headの成功は今回の受入れ証拠にしない。
ローカルではRust 1.95.0を導入して共有goldenと追加transportを検査し、
実GUI/Android/非root owner等の環境依存範囲は新headのCI結果と分けて報告する。
