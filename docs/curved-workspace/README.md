# Curved Workspace — 実装用詳細設計 v1

作成: 2026-11-11 / Status: **詳細設計。製品コード未実装、Quest受入れ未実施**。
基準: `36ebbfd4728ecb669e296491940171b7fb32b58b`。

[構想・調査・数式・性能評価を含む上位計画](../curved-workspace-immersive-terminal-design-20261111.md)を実装可能な契約へ具体化する。上位計画を削除・短縮しない。本書群はその補足であり、下記で明示した決定を、上位計画の「候補」「例」より優先する。実装開始時には最新mainとAGENTS.mdを再確認し、基準から変更された接続箇所を記録する。

## 読む順序

| 文書 | 正本にする判断 |
|---|---|
| [01-contracts](01-contracts.md) | 型、wire、識別子、上限、reason、API責務 |
| [02-geometry](02-geometry.md) | 符号、3枚・9枚、Envelope、数値アルゴリズム |
| [03-runtime](03-runtime.md) | 一括操作、ACK、失敗優先順位、rollback、Undo、保存完了の意味 |
| [04-android-input](04-android-input.md) | SDK adapter、入力停止と再開、listener寿命、既存経路の迂回防止 |
| [05-persistence-presets](05-persistence-presets.md) | preset、保存schema、migration、一時配置、手動override |
| [06-cylinder](06-cylinder.md) | 隔離PoC、SDK能力証拠、曲面入力、テーマの数学的接続 |
| [07-tests](07-tests.md) | 固定fixture、否定試験、実機oracle、性能、合格条件 |
| [08-work-items](08-work-items.md) | 小さい実装単位、許可ファイル、依存、受入れ、引継ぎprompt |

実装担当は全repoを一度に編集しない。08の一つの作業票を選び、指定文書と指定sourceだけを読む。新しいAPI名は本書では**追加する契約**であり、基準mainに存在するという意味ではない。

## 今回固定した設計判断

| ID | 決定 |
|---|---|
| D01 | 初版は既存3窓すべてを選んだ `radial_three`。3窓以外は拒否。9窓上限は変更せず、一般化は後続。 |
| D02 | Flat/Fan/Arcは配置、Cylinderは形状。別enumにする。 |
| D03 | 初版は中央の適用済みposeを基準にする。viewer取得は後続recenterのみ。開始時中央がpitch/rollを持つ場合は拒否し、勝手に水平化しない。 |
| D04 | 全窓を同じ高さ・等距離、左yaw負・右正。本文だけでなく装飾・操作部Envelopeを含める。 |
| D05 | 適用中は別のpresentation overlayを使う。既存 `SetPose` を3回送信して実装しない。 |
| D06 | 入力停止・geometry ACK・listener準備を全対象で確認し、その後一回だけbase配置をcommitする。 |
| D07 | 初版は操作後のkeyboard focusを空にする。明示click/選択で取得する。古いfocus tokenは復元しない。 |
| D08 | 保存失敗は配置失敗と別。実配置成功後に保存が失敗しても配置を自動rollbackしない。 |
| D09 | Undoは同runtime・同target集合・未変更revisionで一段のみ。通常操作と同じ確認手順で戻す。 |
| D10 | 同app_id複数窓の誤復元を避ける既存規則を維持。永続PlacementId、launch相関、Anchor導入をここで再実装しない。 |
| D11 | Phase 2保存は別々のfileを同時commitせず、旧Preferencesとpresetを一つの新version envelopeへ収める。旧fileはmigration後も保持。 |
| D12 | 一時Bring Here/recenterは保存baseを変更しない。return以外の配置変更は一時表示中に拒否。 |
| D13 | Cylinder製品初版は静的shapeを作成時に決定。動的reshapeとanimationはPoCに限定し、実Surface寿命が不明なまま通常経路へ入れない。 |
| D14 | SDKが観測できない事実は成功boolで補わない。未確認項目は06の能力gateで止める。 |
| D15 | 相関IDsを使い回さない。WindowSlot/leaseは位置の交換に使わない。SSH/Waypipe/process owner/journalを変更しない。 |

## 基準コードで確認した接続点

以下は基準SHAのsource確認であり、新機能の動作証拠ではない。

- [commands.rs](../../native/model/src/commands.rs): `Command::parse`, `request_id`, `needs_input_reset`。schema=1、CommandSeq/TargetKeyが既存形式。ACK追加ではこの三箇所を同時に更新する。
- [workspace.rs](../../native/core/src/workspace.rs): `Controller::set_pose`, `pose_applied`, `remember`, `take_pending_save`, `storage_result`, `sync`。`remember`はdesired poseを保存するため、途中overlayをここへ流してはいけない。
- [runtime.rs](../../native/core/src/runtime.rs): SetPose/resize成功後のremember、Pointer/Focus/Keyとinput_resetsの照合、groups.ownerの入力・geometry gateを持つ。新経路はここを迂回しない。
- [preferences.rs](../../native/core/src/preferences.rs): `workspace/layout.json`、atomic renameとfsync、保存report。I/Oをstate mutex外で行う既存分離を維持する。
- [window_group_owner.rs](../../native/core/src/window_group_owner.rs): World、Ticket、GeometryAck、completion上限8、input_allowed/blocks_geometry、geometry適用の先例。groupのFootOriginをlayout authorityへ転用しない。
- [SpatialPanelAdapter.kt](../../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt): `applyGroup`, `acknowledgeGroup`, `applyControls`, `physicalGeometryMatches`、Surface退役、`setAbsoluteTransform`。新layoutをgroupとは排他的なoverlayとして扱う。
- [InputAdapter.kt](../../android/app/src/main/java/io/github/tinmachine/eidolon/InputAdapter.kt): callback時のticket取得とdragのreceiver照合。新layoutの入力leaseではcallback時に最新ticketへ付け替えない。
- [NativeSessionHost.kt](../../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt): `InputTarget`、input intentions、keyboard focus、product worker/SDK thread分離、debug入力連続性を持つ。debug helperを製品の成功証拠へ流用しない。

開始時mainは上記。直前の文書追加以外の変更はない。前回調査でAGENTS.mdはなし。直近PR #11/#10/#9はclosed/merged。基準headのCI run `43025509314` と `43025509135` は照会時in_progressで、成功とは記録しない。親headのfmt失敗は上位計画の履歴であり、今回修正しない。新しい実装の検証はその実装headで行う。

## 完了の意味

「設計完了」は、実装者が型・順序・保存方式・失敗処理を独自判断しなくてよい状態を指す。SDKの未公開挙動やHMDでの読みやすさの実測完了を意味しない。Cylinderは未知事項を放置するのではなく、試験方法・合否・不成立時の動作まで固定する。

初版の完了: 3窓の同target poseだけが変わり、入力誤配送0、途中配置保存0、全ACK前の成功0、失敗時の復旧またはblockedが説明可能。host/JVM成功とQuest成功は別記録。

## 要求と対応

| 元の要求 | 詳細設計 |
|---|---|
| 現行Rust/Kotlin調査、3方式比較 | 上位計画、README、01 |
| 数式・角度・9窓・重なり | 02、geometry-v1.json |
| 型・API・責務・focus・旧event拒否 | 01、03、04 |
| 保存、手動調整、増減、中央変更、Undo | 03、05 |
| SDK Cylinder 10項目、SHM、popup | 06、上位計画 |
| 初PR差分、テスト、Quest、長時間、性能 | 07、08 |
| 安全性と所有権 | 全文書の不変条件、特に03/04 |
| 小さいLLMへの実装引継ぎ | 08の作業票・依存DAG・共通prompt |

新規source・CLI・fixture runnerは本書ではまだ作っていない。`fixtures/`のJSONは設計時に固定した入力・期待値で、製品実装から生成したテスト結果ではない。
