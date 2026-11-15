# Host/JNI・SDK 境界レビューと索引の探索比較

2026-11-15。[初版の評価](source-structure-retirement-review-20261115.md)の次の手順1を実施した。
対象 source は `0e0df43a2ce4734a00a36f830cc24a80e277bec5` に固定。
初版の22関係・registry を変えずに調査した。アプリ実装、保存したローカル案、remote は変更していない。

続報: [監視範囲・IME の所有境界・追加3問の評価](source-structure-watch-ownership-20261115.md)。
以下は初版を固定した時点の記録であり、その後の watch 補強結果とは区別する。

## 相談したい判断

**索引は既知の契約を再確認する補助として継続し、起動・表示への拡張より先に、監視対象ファイルと所有範囲を補う。**
現時点で「レビュー全体が速くなった」「未知の不具合探索にも十分」とはいえない。

source の接続を追い、既存 JVM 39テストと SDK 境界の模擬11ケースが成功した。
今回の範囲で保存を誤許可する新しい確定不具合は見つかっていない。
一方、初版の鮮度検査が見ない重要ファイルを9個確認し、未登録ファイルの判定を変えても `ok` が維持されることを再現した。
これはアプリの不具合ではなく、索引を利用する際の具体的な適用限界である。

## Host → JNI → native の照合

| 境界 | source で確認できたこと | 今回の証拠の限界 |
| --- | --- | --- |
| Host worker / admission | [Host](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt) の runtime handle は volatile、作成・保存進行は単一 scheduled worker。通常操作は [DebugSdkProductExecutor](../android/app/src/main/java/io/github/tinmachine/eidolon/DebugSdkProductExecutor.kt) が enqueue 時と実行時の epoch を照合 | `RuntimeAdmissionTest` は実 executor とモデルを実行する。Android Host 本体は実行していない |
| JNI 宣言と実装 | [NativeBridge](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeBridge.kt) の static `nativeSnapshot(Long):String`、`nativeRequestStop(Long):Int`、`nativeDestroy(Long):Int` と [Rust bridge](../native/android/src/bridge.rs) の引数/戻り値、status_entry macro の対応を照合 | 実 ELF/JVM による製品 JNI の結合確認は未実施 |
| snapshot envelope / census | [registry](../native/android/src/registry.rs) は同 handle の live snapshot だけを返し、stale に runtime を合成しない。[diagnostics](../native/core/src/diagnostics.rs) の14 field、[ID serializer](../native/model/src/ids.rs) の decimal string と [NativeReclamation.decode](../android/app/src/main/java/io/github/tinmachine/eidolon/RuntimeSaveCoordinator.kt) を照合 | Kotlin の型・欠落・負数・foreign handle の拒否は既存テストで成功。Rust→Kotlin を一続きに動かした証拠ではない |
| Destroy と保存 | Host Port は nativeDestroy の実結果を返し、成功時だけ runtimeDestroyed で handle を0へ移す。Coordinator は保存開始時の runtime/Owner と SDK ticket から StopReceipt を作り、barrier の前後で admission を照合 | モデル Port と実 settings file の結合テストが成功。native の停止性や実 SDK をモデル結果で代替しない |
| SDK release の戻り値 | `sdkEvidence` は captured lease.runtime/target/create_command/adapter を JSON 化。[Runtime.command / observe_sdk](../native/core/src/runtime.rs) は一般 command queue より前に ownership ledger を同期更新し、Retired は renderer fence と同 target を検査して SDK binding を外す | source では Accepted を単なる enqueue 成功と混同していない。実 JNI 経由の race/failure 注入は未実施 |
| JNI error | Rust BridgePolicy は例外を設定し runtime を fail へ移す。既存の pending exception を成功値へ読み替える設計ではない。Host の通常保存進行は Exception を保持する | panic/Java exception の実 JNI 試験は今回未実施 |

## SDK の資源生成入口まで追った結果

| 入口 | 登録・回収の接続 |
| --- | --- |
| [SpatialPanelAdapter.create](../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt) | diagnostic / legacy / v3 を共通に reserve → native registration → registered → SdkResourceScope → Entity allocation の順で扱う。native 登録拒否は副作用なしとして record を収集する |
| native panel / decoration panel の factory | Scene と Entity の captured owner を取得し、scope.produce で同期 factory を数える。通知は scope.post を通る。別 Scene の同じ Entity ID に付け替えない |
| [WindowControls](../android/app/src/main/java/io/github/tinmachine/eidolon/WindowControls.kt) / [WindowModeBadge](../android/app/src/main/java/io/github/tinmachine/eidolon/WindowModeBadge.kt) | adapter が同じ scope を渡し、constructor を produce 内で実行する。子 Entity は createEntity、future は sceneObject、入力 listener は scope.listener を通る。badge の直接作る mesh/material/texture は保持された owner の close 対象 |
| [WindowMenuView](../android/app/src/main/java/io/github/tinmachine/eidolon/WindowMenuView.kt) | 通常 menu / group menu の controller から同じ scope を渡す。view を保持してから initialize し、initialize は produce 内。子 Entity/future/listener と close failure を追う |
| [SdkResourceScope](../android/app/src/main/java/io/github/tinmachine/eidolon/SdkResourceScope.kt) | create 前の callback ticket、future 呼出前の producer 加算、seal 後の late object disposal、post 拒否時の failure、finally の drain を持つ |
| 同 file の SdkSceneDeletionFence | 同 DataModel の delete listener が即時に permission を取り下げる。受け取った delete と後続 lateExecute を区別する。別 Scene の pass で完了しない |
| adapter.destroy | SDK dispose、renderer detach、readyToRelease の後に captured native release の実 Accepted を確認して nativeReleased / collectCompleted へ進む |

現在の production caller は nullable scope の引数を渡していることを確認した。
型の default `null` や fallback が存在するため、将来追加された caller まで自動的に保護されるとはいえない。
今回の模擬 probe も全 caller の漏れ検査ではない。

### 所有範囲として残った判断

- `MainActivity` の control / launcher panel は Scene/Activity の UI。onSpatialShutdown で破棄し、runtime を止めても設定操作のために残る。単に ledger 外という理由で runtime 資源の登録漏れとは判定しない。
- `TerminalImeController` の product IME UI は captured runtime/editor owner を参照する一方、Entity は同 ledger 外。Host は UI へ close を post し、IME Run は100ms tickで owner の消失等を見て Entity を破棄する。**保存完了まで IME Entity の削除を待つ契約なのか、入力を失効させれば UI の回収を後続処理としてよいのかが今回の確認範囲では確定しない。** 通常 runtime binding と同一扱いにはしないが、Activity UI として無条件に除外もしない。
- mesh creator / SDK が管理する SceneObject の生成・破棄、Scene 閉鎖後に callback が来る実際の順序は SDK lifecycle の証拠が必要。モデルの delete/pass を実 SDK の保証へ読み替えない。
- debug source set にも ThemeSurfaceProbe、ImeProbe 等の Entity 生成入口がある。設定保存入口には debugSdkLease / probeRecovery の gate があるが、全 debug fixture の開始・停止との結合は今回網羅していない。今回の通常 window/control/menu の照合を、debug variant 全体の登録漏れ検証とは扱わない。

IME は所有範囲・契約確認の課題であり、今回「旧 Entity があるので保存で破壊的な不具合が起きる」とは断定していない。
Scene の delete/pass が取得できない場合に回収を pending のまま保持する挙動は、現在の設計と一致する。

## 実行した検査

[任意 probe と再現コマンド](../scripts/structure/probes/README.md)を追加した。
既存 Gradle cache の Kotlin 2.4.21、JDK、host cc を使用した。Android SDK や新しい dependency は導入していない。

| 検査 | 結果と範囲 |
| --- | --- |
| 既存 `RuntimeSaveCoordinatorTest`、`RuntimeSettingsIntegrationTest`、`SdkEvidenceBridgeTest`、`RuntimeAdmissionTest`、`RuntimeOriginStopTest` | **39件 PASS**。アプリの該当 Kotlin source と実ファイル用 C/JNI fixture を実行 |
| 実 SdkResourceScope + 実 SdkEvidenceBridge、Android/SDK double | **11件 PASS**。seal 中の allocation、queued callback、late future、post/dispose failure、別 Scene、欠けた delete/pass、unexpected deletion を検査 |
| 初版索引の Python tests | **17件 PASS** |
| 製品 NativeSessionHost/JNI、実 Meta SDK、Rust tests、remote/Quest/HMD | 今回未実行 |

前回は Gradle の test task が SDK location 不在で起動前に失敗した。
今回の39件は別の offline JVM runner による成功であり、Android Gradle build が成功したという更新ではない。
SDK double は callback 順序をテスト側から与える。実機の scheduling、通知 API の適合性、SDK 内部の解放は検証していない。

## 固定3問の探索比較

比較条件は source revision、初版 generator/registry、Android profile を固定。
graph digest は `cea607a5b0f234b50f29f336088104b88bf9e326afa68edc20ab12606bde13c7`。
`rg` は下表の固定語で Kotlin/Rust の source/test を検索。slice は初版と同じ入口・hop を使った。

| 問い / rg の語 | rg 一致行の bytes / 候補 file | slice JSON bytes / 参照 file | 観測 |
| --- | --- | --- | --- |
| Destroy だけで保存できるか / `nativeDestroy\|StopReceipt` | 4,230 / 12 | 6,413 / 3 | slice は残存資源と所有者条件に集約。最初の rg に出ない RuntimeSettingsIntegrationTest へ案内できた |
| 新しい runtime=0 で resume できるか / `resumePublication\|SAVE_OWNER_UNKNOWN` | 3,216 / 5 | 5,998 / 3 | 両方から必要な file が見つかる。slice は token 別名の Host 側条件を添える |
| remote 完了だけで journal を消せるか / `finalize_reclaimed\|publish_v3_finalized` | 1,753 / 4 | 7,612 / 4 | slice は最初の rg に出ない native runtime の join 入口を含む。一方、rg は registry 外の transport/session/tests.rs も発見する |

**3問すべてで slice JSON の方が大きかった。** 現状の JSON は条件・test 参照・unknown・metadata を含む。
読むべき候補を絞る効果はあるが、単純な token/bytes 削減を根拠に採用できない。
native の問いでは候補 file 数も減っておらず、両方を併用する意味がある。

この1回では rg は各約6–7ms、全 graph 生成は約141ms。これはコマンド自身の時間だけであり、review の所要時間ではない。
source/test 本文の確認と、registry の作成・更新費用も別に必要である。

重要な比較上の制約:

- 3問は registry を作るときに使った既知の問い。独立した評価問題ではない。
- 同じ reviewer が source を知っているため、cold context の読解時間や正答率は測定していない。
- rg の一致行と、手動確認済みの主張を含む slice は異なる出力。これだけで一方のレビュー品質が高いとは結論しない。
- 今回は探索量・参照漏れ・適用限界の比較まで。独立 reviewer による S4 全体の完了とは扱わない。

## 登録範囲外の変更を見逃す再現

追加の境界調査で選んだ9ファイルが、初版の登録ファイルにないことを確認した。
これは全 caller・未登録ファイル数の網羅値ではない。

`SdkResourceScope.kt`、`SpatialPanelAdapter.kt`、`WindowControls.kt`、`WindowModeBadge.kt`、`WindowMenuView.kt`、
`NativeBridge.kt`、`native/core/src/diagnostics.rs`、`native/model/src/ids.rs`、`native/model/src/commands.rs`。

別の固定 SHA checkout へ初版索引をコピーし、`SdkResourceScope.createEntity` の `ticket.valid()` 条件だけを外した。
その状態でも `build` は `status=ok`、artifact は byte 単位で同一、旧 artifact の `verify` も成功した。
検査後に元の bytes を復元した。通常 checkout のアプリ source は変更していない。

初版は「登録範囲だけを監視する」と明記しており、その仕様どおりの結果である。
しかし、SDK の安全性に関わる関数を読んでいたつもりで artifact を再利用すると、実用上の見逃しになる。
**意味的な辺の登録範囲と、変更を検出する監視対象を分離する**ことを優先したい。
全 caller を自動解決する前でも、codec・副作用入口・生成側を監視対象へ追加し、変更時に確認を促すことはできる。

## 次の判断案

1. **先に索引の監視対象を補う。** 上記9ファイルと関連 caller の分類を確認し、辺が未登録でも変更を検出する。`status=ok` を読む箇所には登録範囲を明示する。
2. **IME/UI と runtime の所有境界を明文化する。** 保存を妨げない Activity UI と、元 runtime の回収を待つ対象を区別し、必要な結合テストを選ぶ。
3. **その後に利用方法を再評価する。** 人が読む出力は小さくできる余地がある。未知の質問で rg 併用時の見落としと保守費を比較してから、起動・表示へ広げる。

Host の大きな切り出しや回収方式の変更は、この判断の後に行う。今回の結果だけで保存したローカル案を廃棄しない。
