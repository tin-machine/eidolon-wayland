# 07 — 固定fixture・回帰・実機受入れ

[入口](README.md)。ここに列挙するテストは**実装時に追加する検査**。文書追加時に製品テストを実行したという意味ではない。

## 1. 固定fixtureの読み方

| fixture | 用途 |
|---|---|
| [geometry-v1.json](fixtures/geometry-v1.json) | f64 solver、既知pose、非対称角、Cylinder UV、canvas alignment |
| [wire-v1.json](fixtures/wire-v1.json) | 追加commandの正常rawと厳密拒否。runtime acceptanceとは別 |
| [lifecycle-v1.json](fixtures/lifecycle-v1.json) | pure modelのevent順序・境界時刻・失敗時期待値 |

fixture schemaは1、固定IDは試験用。geometry.expectedは独立した数学計算で固定し、test中にSUTから再生成しない。expected値更新には変更理由、独立計算、reviewを必要とする。

wireの正常rawはparseに成功することだけを意味する。実runtimeのscope/資源状態は別testで作る。変異caseはraw stringを変更してからparserに入れる。重複keyをValue/JSONObjectで一旦読んでから作り直してはいけない。

lifecycle fixtureのtarget alias C/R/Lはbase.targetsのfull TargetKeyへ展開。`all`は同tickに3件の正当なACKを渡すpure-model用略記。実SDKの成功をまとめて偽装するAPIではない。caseごとにfresh model、clock、counterを作る。

## 2. Rust model tests

追加test名は以下を固定し、08の作業票の完了条件にする。

```text
workspace_layout::geometry::tests::symmetric_three_matches_golden
workspace_layout::geometry::tests::asymmetric_envelopes_preserve_gap
workspace_layout::geometry::tests::yaw_basis_rotates_positions_and_normals
workspace_layout::geometry::tests::one_meter_wide_three_is_rejected
workspace_layout::geometry::tests::invalid_pose_and_nonfinite_are_rejected
workspace_layout::geometry::tests::all_results_validate_after_f32_conversion
workspace_layout::wire::tests::shared_raw_vectors
workspace_layout::wire::tests::nested_objects_and_duplicates_are_strict
workspace_layout::operation::tests::shared_state_traces
workspace_layout::operation::tests::deadline_wins_over_last_ack
workspace_layout::operation::tests::wrong_target_and_old_round_do_not_advance
workspace_layout::operation::tests::first_failure_is_not_overwritten
workspace_layout::operation::tests::counter_overflow_blocks
```

f64絶対誤差1e-9、f32 pose成分2e-6をgolden検査に使う。SDK rayの0.005m/0.002UV許容値と混同しない。q/-q両方の同値を検査。

追加property tests: distanceやwidthを小さくランダムに変え、成立した候補は全pose finite、q norm、gap、role一意、cap内を満たす。乱数seedを保存し、失敗を再現可能にする。property testsは固定goldenの代替ではない。

## 3. Core integration tests

| ID | ケース | oracle |
|---|---|---|
| R01 | 3窓中1つ無効 | desired/base/Preferences/SDK command数が全て不変 |
| R02 | 2枚だけgeometry ACK | completionなし、全入力不可、保存intent0 |
| R03 | 最後のACKとclose同tick | 成功0、復旧へ、first_failure保持 |
| R04 | Surface generation交換 | 旧target ACK拒否、新窓へpose転用0 |
| R05 | theme/canvas/size変化 | capture失効。ContentSeq増加のみは継続 |
| R06 | SDK failure後、restore成功 | base元どおり、save intent0、outcome restored |
| R07 | restore timeout | blocked、通常Stop可能、未知資源をcleanにしない |
| R08 | duplicate/old ACK | 正当なroundを進めない/巻き戻さない |
| R09 | Undo後の再Undo | 一度だけ成立。redoや二重反転なし |
| R10 | manual move後のUndo | CW_UNDO_STALE、現poseを保持 |
| R11 | 保存前/後のクラッシュ | 原子的old/new document。途中3枚配置を保存しない |
| R12 | rename後fsync失敗 | layout appliedとsave failedを別表示 |
| R13 | 同app_id3窓 | 接続内配置とUndo成功、永続推測更新0 |
| R14 | Pointerのrevision省略 | 未管理targetだけ互換受理、管理済みは拒否 |
| R15 | Key/Focus/IME/controls迂回 | CW gate中のWayland配送0 |
| R16 | CW blockedでDisconnect | renderer/native/SDK/remoteの既存回収が進む |

snapshotのsuccess boolだけをassertせず、window pose、保存bytes、実Wayland fixtureの受信counter、target別資源を照合する。既存group/slot testsと同じFixtureを必要箇所だけ部品利用し、FootOrigin確認をlayoutで再実装しない。

## 4. Kotlin JVM tests

`WorkspaceLayoutProtocolTest`, `WorkspaceLayoutSdkControllerTest`, `PanelInputLeaseTest`, `WorkspaceLayoutUiTest` を追加。

- raw schema/field/ID一致、unsupported schema、unknown reason。
- old runtime/scene/target/revision、listenerSerial相違、receiver相違、重複callback。
- callback取得後・Host enqueue前・JNI前のそれぞれにinvalidateを注入。
- 新しいinput ticketへ古いUVを付け替えない。
- Applying中にclose/resize/theme/Activity destruction。
- 1枚だけSDK failure、遅いfutureの成功、旧futureのdispose失敗。
- controlsが存在する/しないcase、menuとgrab cancel失敗。
- initial command ACCEPTEDをUIのcompleteにしない。
- applied + saved / memory_only / failed / restored / blockedの表示。
- managed targetを普通のmove/resize/group経路で操作してもepoch/revisionが保たれる。

Fake SDKは明示した順序で結果を返す。Fakeのgeometry=trueからHMD可読性やSurface実寿命を主張しない。

## 5. 保存とpreset tests

- legacy byteを変更しないmigration、既存v2拒否、壊れたv2からlegacyへのfallback禁止。
- duplicate `(app_id,slot)`を外側/内側に持つdocumentを拒否。
- LocalPanelPoseの負Yは許可、解決後world poseの範囲外は拒否。
- 合計16件、64KiB、unknown schema、symlink、mode/owner、write/fsync/readback失敗。
- temporary Bring Hereの途中で無関係設定を保存しても一時poseは保存されない。
- role穴を維持、新窓だけfill、manual overrideを自動で動かさない。
- 1/2/3/6/9窓、10枚目拒否、異なる寸法、上下段角度の極値。
- 一時recenter/Return、scene交換、対象close。別generationへ一時配置を再bindしない。

## 6. 実Quest試験

既存SDK debug leaseを使う固定caseを追加。製品利用中のsessionを奪わずBusyで拒否する。操作者の通常設定・文書をfixtureにしない。raw artifactはprivate作業領域へ置く。

| ID | 実試験 | 必須証拠 |
|---|---|---|
| Q01 | 3枚Flat・yaw0/±20/±40 | 実SDK segment hit、receiver、UV、Wayland echo |
| Q02 | 初版一括Apply/Undo | 全ACK、input gate、slot/WindowId不変 |
| Q03 | 遅着callback/Surface交換 | 旧event配送0、新targetへの流用0 |
| Q04 | pointer down外へdrag/up、scroll | capture維持、他面UV拒否、stuck button0 |
| Q05 | 通常move/resize/group回帰 | 管理済みtargetでも入力・復帰が成立 |
| Q06 | Cylinder pattern grid | query/callback座標系、誤差、backface/範囲外 |
| Q07 | reshape/resize/Activity再生成 | Surface identityと全回収を別々に観測 |
| Q08 | plain foot Cylinder | 文字、pointer、popup/subsurface、logical resize |
| Q09 | theme Cylinder | 非対称canvas/body/controlsの位置とUV一致 |
| Q10 | 3→6→9窓・終了反復20回 | FD/Surface/Entity/texture/SHMが安定し終了baselineへ戻る |
| Q11 | 30〜60分のコード・ログ・文書 | 読解/選択ミス、作業時間、頭位yaw、主観疲労 |
| Q12 | 同条件Flat/Cylinder性能 | p50/p95 CPU/GPU frame time、upload/present数、熱・power傾向 |

Q03のSDK内部queue試験が保証できなければ通常UIは有効化しない。メッセージqueueのFake試験だけで代用しない。

## 7. 性能とUXの判定

同じsource/APK、SDK/OS、font、pixel数、更新頻度、theme、refresh rate、給電条件で比較。staticとscrollingを分け、warm-up区間を除いて5分単位のサンプルと30〜60分の継続観測を保存する。

初期の警戒値（測定値ではない）: Flat比p95 app GPU/CPU frame timeが10%以上増える、missed-frame率が1 percentage point以上増える、反復回収後の資源が単調増加する場合は採用を止めて分析する。clock変化や測定ノイズが大きければinconclusiveとし、都合の良い一回だけを採用しない。

読みやすさはframe timeだけで合格にしない。0/O、1/l/I、行末選択、長いログの同一タスクを条件順序を変えて比較。Quest3ではeye trackingを前提にせず、頭位yawと主観評価を使う。文字角度の表は上位計画を参照。

## 8. 実行コマンドとゼロ件防止

```bash
# 既存入口。repo rootから実行。
python3 scripts/check-design
cargo fmt --all --check
cargo test --locked -p eidolon-model workspace_layout -- --list
cargo test --locked -p eidolon-model workspace_layout
scripts/check-host --suite all
scripts/build-android VERSION_CODE
```

新filterは実装前には0件になり得る。成功終了コードだけで合格にしない。`--list`とJUnit XMLから必須test名が存在し、executed>0、required failed=0、required skipped=0を確認する。VERSION_CODEは現在の配備規則に従う。fixtureのJSON parse成功はnative/JVMのtest成功とは別。

新しいQuest case名は08の実装票で既存 `scripts/quest-check` へ登録してから実行する。未実装のCLIを文書のコマンド例だけで実行済みにしない。

## 9. 完了receipt

各作業票の引継ぎにsource SHA、変更file、実行command、test実行/失敗/skip件数、CI run/job、Quest case/OS/SDK、first failure、cleanup結果、未実施理由を残す。UI/SDK/host/physicalそれぞれの合格を別fieldにする。

最終UI enableにはFlatのmodel/core/JVM/Q01〜Q05・停止回収を要求。Cylinderは06のgateを追加。手動受入れ未実施ならそのfieldはnot_runのまま。小さいモデルが「テストを追加した」ことを「動作した」へ書き換えないよう、08のreport templateを使う。
