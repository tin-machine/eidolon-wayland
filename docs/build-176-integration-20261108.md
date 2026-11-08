# Build176: 通常IMEと正面配置の確認・作業中断点

2026-11-08。ユーザーの相談のため、新しい検証には着手せず、実装・検証結果と未統合の作業を保存して中断する。
NX01〜18のgoalは未完了。物理入力・かな漢字変換・HMDの確認は最終段階に残す。

## APKとPC検証

導入済みAPKのsourceは`f09a3b4b36c21e834646e9cf2f364208d52d6e1a`、dirty=false、入力digestは
`06ffe25a75aef7df89354915ea7e4a69a941a587aa802ba613b47e9f7bba238e`。
その後の`22b049e`はhost operatorの追加で、APK入力digestが一致することを確認した。

| 検証 | 結果 |
| --- | --- |
| debug/release assemble・lint・package | 両方PASS |
| JVM debug / release | 294件 / 196件、失敗・error・skipなし |
| Python統合 | 453件中446件実行、7件skip、PASS |
| 通常Freedoomのhost operator | 8件PASS、実ゲーム操作は未実行 |

| APK | SHA-256 |
| --- | --- |
| debug | `f71f2de1f737ed21b1a222ac41bc78df7faafe218500f92b1bbe9e84651e3a37` |
| unsigned release | `f8bf633534a9cc145b44c8f7953783738c88898a51e38b6e9efb2181c05a9500` |

旧175をnative-depsの10cyclesで停止し、transport Idle・Mapped0・pending cleanupなしを確認して176を導入した。
source archive、JUnit XML、APK、全試行の記録は、設定済みwork rootの`eidolon-ime-implementation-20261107/`に保持する。

## 通常IMEのON/OFF

`normal-ime176/008e7ed97c9cb34ccb97fd6f61e523c6/`はPASS。
通常製品入口のONで初期ownerをView作成前に確保し、native BOUND・View/Window/InputConnectionのfocus・raw入力の遮断を確認した。
OFFでは同じruntimeと完全なtarget identityを保ち、Android/native/controllerの解放後、直接入力への復帰を確認した。
[Build175のactive観測バグ](build-175-integration-20261108.md#通常imeの観測バグ)の修正確認に相当する。

この検査は固定要求による切替であり、物理Ctrl+Space・かな漢字変換・HMD上の候補表示は未確認。
IME providerの導入・切替は行っていない。

## 単独ウィンドーの正面配置

`window-recenter176/438fbdc1a8a711178e40bafb567459b6/`はPASS。
固定した移動前位置から実SDKメニューを操作し、SetPoseControl、native/model/SDKのgeometry一致、
内容サイズの維持、RECENTERING表示、直接入力の復帰を確認した。
全targetの退役と通常runtimeの復帰、製品設定を変更していないことも確認した。

これは単独窓の結果。重なった複数窓の選択・正面配置は未受入れ。
SDKのnode matrixを世界座標と扱わない理由は[正面配置case](nx12-recenter-sdk-case-20261108.md)を参照する。

## グループ化の検証失敗

`window-groups176/2974f8e4221a7e05d059920e0b2bdcb5/`は、`stage=first`、
`IllegalStateException: GROUP_OWNER_CHANGED`、`WindowGroupsCase.kt`の158行で失敗した。
このguardはruntime handle・scene・Activity可視状態・XR FOCUSEDをまとめて確認している。
どの条件が変わったかは現記録では未確定。group geometryの不成立や追跡障害と断定しない。
group command完了は記録されておらず、集約・展開の実機成功には数えない。

終了時にはReady/FOCUSED、transport Idle、pending cleanupなし、Mapped0、通常の診断パネルへ復帰した。
元の製品設定は維持され、環境復帰エラーもなかった。停止指示に従い、再試行は行っていない。

## Freedoomの登録と残件

Build175の[Android登録APIの試行](build-175-integration-20261108.md#ランチャー登録の環境差)に続き、
`renameat2(RENAME_NOREPLACE)`経路で登録に成功した。
既定のterminal/editorを維持してFreedoomを1項目だけ追加し、catalogは計3項目になった。
source/APK・asset/engine/runtimeの照合、他の項目の維持を確認している。

登録はゲーム起動の成功を意味しない。通常ランチャー経由の起動・固定キー・二段階close・所有Stopは未実行。
これらの[固定operator](freedoom-normal-gui-probe-20261108.md)は、同じjobの起動receiptとSDK退役の証拠を照合する。
停止時点では新たなゲーム検証を開始していない。

## 未統合コードの保存

以下はmainへ取り込まず、再開用の作業ブランチに保存する。
限定検証の成功を、通常runtimeや実機での受入れに広げない。

| ブランチ | 保存する範囲と残る確認 |
| --- | --- |
| `codex/nx18-linux-gui-c2-176` | 通常Linux GUI用helper/demo、Pins/Binding/typed protocol。Python13件・Rust8件・Cビルド・transport限定clippy成功。SSH actor・lease・専用journal・runtime・通常UIは未接続 |
| `codex/nx17d-menu` | グループ操作メニューのモデルと通常操作への接続作業。JUnit31件・18 SDK sourceの限定型検査成功。通常実SDKの操作は未受入れ、接続部分のpeer reviewも未実施 |
| `codex/nx12-recenter-overlap-177` | 既存の有限配置候補モデルを最新main基点へ移した準備。モデル8件と既存27件は元checkpointで成功。Host/Adapterの接続・複数窓の実geometry確認は未実装 |

停止時のbranch先端は、Linux GUIが`43767a4`、groupメニューが`3cb4e27`、recenter準備が`f3b21a4`。
3つのtreeはいずれもcleanで、担当agentのbuild・fixtureは終了した。

## 再開順

1. ユーザーとの相談結果を計画とgoalへ反映する。それまでは開発・実機操作を再開しない。
2. group caseの失効条件を個別に観測できるようにし、所有条件を緩めず原因を確認する。
3. 通常Freedoomの起動・入力・close・Stopを実測し、未統合のgroupメニューと複数窓recenterをreviewして統合する。
4. 通常Linux GUIの未接続部分、統合回帰、物理入力・かな漢字変換・HMD確認へ進む。

端末の近接センサー対策とguardian pauseはユーザー指定どおり維持する。
既存Phase5/6の残る受入れ条件も、今回の自動検証だけで完了にしない。
