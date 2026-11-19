# I6 残件一覧 — 2026-11-19 JST の source 確認記録

**同日追記 — settings 契約 revision の明示承認。** ユーザーが §3.1 の契約変更を承認したため、
[settings lifecycle v2](i6-n07-settings-lifecycle-v2.md) と7時点の観測を実装する。
以下の調査基準 SHA／v1 の拒否結果／当時の「未承認・未実装」は履歴として保持する。
v2 の新規収集・独立再検証を別途要求し、旧 artifact を再解釈しない。
この承認は Golden、人の画像レビュー、B5、正式 N07/I6 Acceptance を含まない。

この文書は **dated status record** であり、新しい受入れ契約や設計承認ではない。
I6 は引き続き manual discovery とし、この文書を resolver manifest に登録しない。
後続変更を読む際は、以下の基準 SHA と現在の source / PR / CI を比較する。

| 調査基準 | 値 |
| --- | --- |
| 最新 main | `ee9192d12957a17aaa3d61bd6e8ba1ca86a99d6f` |
| 調査対象 | [PR #52](https://github.com/tin-machine/eidolon-wayland/pull/52)、Open / Draft / 未マージ |
| 調査した PR HEAD | `d6f9ff852ae4185460e884cb61b0a5858bb7b38b` |
| 同 HEAD の CI checkout/source | `6065e2e1bfd6453587336c2669930af782638b51` |
| 固定 profile | `i6-host-v1` / `eidolon-i6-host-acceptance-v1` |
| 対応 verification IDs | `I3-T20` / `V11` |

PR HEAD と CI checkout は別 commit である。ここでの実行結果は
[L7a run 44311868353](https://github.com/tin-machine/eidolon-wayland/actions/runs/44311868353)
の記録を参照し、将来の HEAD の成功を先取りしない。

## 1. 全体の現在地と数え方

現在は **N01〜N06 の専用 runner・独立 typed oracle・部分 Acceptance 接続**まで存在する。
N07 は段階的に実装中で、B4 の二セッション収集を追加した PR #52 はこの基準時点では Draft である。
N07 の完了だけでは I6 全体の完了にならない。

[固定 manifest](../../../scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json) を再集計した結果は、
**28 families / 87 required variants / 533 assertion 参照**である。
manifest bytes の SHA-256 は変更していない。

```text
e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33
```

| 範囲 | Families | Variants | Assertion 参照 | 現在の実装・証拠境界 |
| --- | ---: | ---: | ---: | --- |
| N01〜N06 | 6 | 14 | 117 | 専用実 runner / typed oracle / 部分 Acceptance 接続あり。基準 HEAD の個別 GUI jobs は成功 |
| N07〜N11 | 5 | 8 | 79 | N07 は段階実装中。N08〜N11 の正式 I6 runner / oracle 接続が残る |
| F01〜F12 | 12 | 24 | 221 | 正式 I6 の故障注入・回復 case / oracle 接続が残る |
| C01〜C05 | 5 | 41 | 116 | 契約・負例テストの下地はあるが、正式 I6 case の証拠接続が残る |
| 合計 | 28 | 87 | 533 | I6 Host Acceptance 未達 |

**117 は、既存 semantic oracle が扱う manifest の assertion 参照数である。**
同一の正式 run で `117/533 verified` を集約した記録ではない。
個別 job の成功、各部分 receipt の独立検証、全 profile の受入れを区別する。
残る 73 variants / 416 assertion 参照にも、既存 product 実装や unit test を再利用できる部分がある。
これは開発工数や完了率の推定ではない。

[manifest README](../../../scripts/fixtures/linux-desktop-v3-acceptance/README.md) の
`complete` は **期待 product outcome** の列であり、実装完了や実行済みの意味ではない。
`R` / `S` / `T` は証拠の種類であり、追加の case family ではない。

正式接続の根拠は [evidence validator](../../../scripts/linux_real_app_v3_evidence.py) の
artifact kind と typed receipt dispatch である。基準 source が受理する専用 receipt は
`n01_receipt`〜`n06_receipt` であり、N07 / F / C の専用 semantic oracle は未接続である。
古い docstring の「N01/N02」の記述だけで現在の範囲を判断しない。

## 2. 正常系・互換系 N01〜N11

以下の必要観測は [I6 詳細設計 §11.1](../../linux-real-app-v3-i6-detailed-design.md) と
[固定 manifest](../../../scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json) に対応する。

| Family | Variants / assertions | 内容 | 現状・残件 |
| --- | ---: | --- | --- |
| N01 | 1 / 8 | Inspect-only → Stop、Start=0、未割当 seal、SSH / helper / journal 回収 | 実 runner と [独立 oracle](../../../scripts/linux_real_app_v3_n01_oracle.py) が接続済み。全体集約と外側環境証拠は §6 の別境界 |
| N02 | 2 / 20 | GTK デモと Mousepad の通常 Launch、実 frame と合成 SDK | 実 runner と [独立 oracle](../../../scripts/linux_real_app_v3_n02_oracle.py) が接続済み。実 Meta SDK / Quest の証明ではない |
| N03 | 5 / 35 | 未知 app ID、scope / revision 変更、旧 catalog、二重 Launch | 実 runner と [独立 oracle](../../../scripts/linux_real_app_v3_n03_oracle.py) が接続済み。queue Accepted と実 command completion を区別 |
| N04 | 1 / 10 | 125 秒以上 idle、heartbeat、前後の fresh Ctrl+O / Esc | 実 runner と [独立 oracle](../../../scripts/linux_real_app_v3_n04_oracle.py) が接続済み |
| N05 | 1 / 10 | 固定 Open / Edit / Save As、exact bytes / digest、元 file 不変 | 実 runner と [独立 oracle](../../../scripts/linux_real_app_v3_n05_oracle.py) が接続済み |
| N06 | 4 / 34 | Open cancel、Save As confirm、未保存 Close cancel、Close confirm | 実 runner と [独立 oracle](../../../scripts/linux_real_app_v3_n06_oracle.py) が接続済み。既知の間欠失敗と reason 分類は §7 に保持 |
| N07 | 1 / 13 | Line Numbers OFF→ON、同 storage / 新 nonce の再起動、keyfile と独立 pixel | A / B1 / B2 / B3 は main に存在。B4 は Draft / 全体条件未達。Golden / theme / B5 は §3 |
| N08 | 2 / 19 | 独立 precursor control と fresh storage の分離 | 別 bus/display の先行 identity / digest、先行 GUI と設定 bytes の不変、新対象の応答を実証する runner / oracle が必要 |
| N09 | 2 / 18 | session candidate / autosave candidate の起動前拒否 | Inspect 前の専用 storage 配置、app spawn=0、候補 / 文書 / 設定 bytes 不変、helper 回収の runner / oracle が必要 |
| N10 | 2 / 20 | dialog 反復と target generation 交代 | 発行された全 renderer / 合成 SDK generation の receipt、I3/B1 GC、finalizer を照合する runner / oracle が必要 |
| N11 | 1 / 9 | frozen v2 pending → 旧 Recover | 固定旧5 source / journal / version dispatch、v2 pending 中の v3 拒否、旧 capture 回収の実互換 runner / oracle が必要 |

N08 の precursor は独立 A0 control であり、同一 account に第二の通常 v3 lease を作る試験ではない。
N11 の旧 source / journal 互換性は、同一署名の旧 APK からの update 成功を証明しない。

## 3. N07 の直近残件

[N07 段階実装](i6-n07-a-independent-oracles.md)、[B3](i6-n07-b3-pixel-review.md)、
[B4](i6-n07-b4-two-sessions.md) の境界を維持する。

### 3.1 B4 の設定契約の整合

基準 PR の実収集は、同一 StorageRecord / C1 root の実2セッション、6状態の Renderer Readback、
別プロセスの fingerprint、両セッションの product retirement を保存・独立再検証している。
しかし正規 keyfile 観測は、key absent → exact lowercase `true` / 60 bytes → `true` / 458 bytes となった。
第一 Close 後・第二 Launch 前の補助観測は 149 bytes である。

同一読み取り由来の inventory は、第一 Close までに Mousepad window-state の4 keys、
第二 Open 後に GTK file-chooser の12 keys が追加されたことを示す。
三時点の他設定 digest と ON 後 / 再起動後の bytes SHA / size 不変条件に違反するため、
`validate_file_sequence()` は `N07_UNEXPECTED_SETTING_CHANGE`、full collection は
`N07_B4_COLLECTION_INCOMPLETE` を返す。

これは GLib atomic flush による inode / ctime 変更だけの拒否ではない。
通常保存動作と固定した設定不変契約の整合が、B4 自体の残件である。
後続でこの問題を修正する場合も、直接設定編集・preseed・観測時点の移動・自動保存 key の無条件除外により
既存 Oracle を黙って弱めず、製品挙動と契約への影響を明示する。

既存 A0 / foundation もこの判断の根拠になる。
[profile snapshot](../../../scripts/linux_real_app_wayland.py) は `state/window` を通常設定に含め、
[A0 evidence validator](../../../scripts/linux_real_app_evidence.py) は起動後の
`preferences_count >= 4` を要求する。
[正常 Close / restart の記録](../../linux-real-app-a0-restart-20261109.md) も通常 window geometry の保存・保持を明記している。
そのため、B4 を通す目的だけで backend の保存領域を分離しても、既存設定契約への影響を避けられない。

将来の設計候補は、**明示的な revision を持つ settings suboracle** である。
初期 key absent、target の exact `true`、同一 storage / root、未知 key や非対象 preference の変更拒否は厳格に維持し、
通常の自動 UI state は exact key 集合と lifecycle に結び付く因果観測で別途検証する案が考えられる。
これは **未承認・未実装** の候補であり、自動保存 key の一律無視を意味しない。
固定 manifest の13 assertionsを維持できるかも含めて契約レビューを行い、基準時点の旧 Oracle は変更しない。

6画像を保存したことを、有効な candidate や正式 N07 の成功へ昇格しない。
基準収集は `candidate_created=false` / `full_collection_valid=false` の rejected diagnostic packet である。

### 3.2 完全 fingerprint と独立 Golden

- 両セッションの fingerprint は 12/13 項目、未取得は effective theme である。
  Adwaita の設定入力を実効値の観測と扱わず、安全な read-only 観測が必要。
- 正式条件を満たす独立6状態 reference、reviewed ROI / mask / tolerance、完全 fingerprint を揃える。
- 人による独立レビュー、reviewer / origin acquisition / commit の記録、別レビューによる exact SHA pin が必要。
- reference acquisition と後の Acceptance run を分離する。観測 run 自身を expected image にしない。

[production pixel comparator](../../../scripts/linux_real_app_v3_n07_pixel.py) の
`APPROVED_SHA256=None` と `N07_GOLDEN_UNREVIEWED` を維持する。
基準時点では人の Golden 承認も `menu_identity_verified` も成立していない。

### 3.3 B5 の正式 receipt / Acceptance 接続

保存済み native、独立 supervisor keyfile、承認済み pixel、両 session の回収を専用 oracle で照合し、
typed N07 receipt を [正式 evidence validator](../../../scripts/linux_real_app_v3_evidence.py) へ接続する。
基準時点ではこの接続は未実装であり、N07 は **0/13 verified** のままである。
後の別 Acceptance run で13 assertionsすべてが成立した場合にだけ `N07/same_storage_restart` を complete とする。
それでも残りの I6 required cases は省略しない。

## 4. 故障・回復系 F01〜F12

既存の C2 / Runtime / SDK 回収実装や unit tests は再利用できるが、
[I6 詳細設計 §11.2〜12](../../linux-real-app-v3-i6-detailed-design.md) の
以下を正式 I6 の source-bound 実ケース / oracle として接続する作業が残る。

| Family | Variants / assertions | 必要な故障・回復観測 |
| --- | ---: | --- |
| F01 | 1 / 11 | 実 Started frame を受理直前に一度 drop。Start再送 / 期限resetなし、同 Runtime / capture Recover |
| F02 | 1 / 10 | client→sshd の bytes / EOF を blackhole。実 C2 lease expiry、旧 I/O fence、同 capture Recover |
| F03 | 1 / 7 | 実 control EOF、C2 `input_eof`、同 capture receipt と回収 |
| F04 | 3 / 24 | 所有している live pidfd に限った app / dbus / remote Waypipe kill、元 failure 保持、回収 |
| F05 | 4 / 41 | 低 rate 中 Stop、stdout 上限、stderr 上限、write-end 保持。保持 variant は所定 pending を要求 |
| F06 | 1 / 11 | receipt collision による部分 publish、所有 blocker だけの除去、同 capture Recover |
| F07 | 1 / 8 | completion proof 前の helper owner 喪失。same-boot Unknown、journal、新規開始拒否を保持後に rescue |
| F08 | 3 / 30 | journal publish、rename 後 directory fsync、unlink 後 directory fsync の限定 fail-once と retry |
| F09 | 1 / 8 | Inspect 後 package 変更。Launch 拒否と旧 immutable Recover の独立性 |
| F10 | 1 / 10 | Host process kill / restart。新 process の資源0件を旧 owner の回収証拠に流用しない |
| F11 | 4 / 32 | Session 前、catalog 待ち、Launch pending、Ready 後の取消。元 request の完了、所有境界、回収 |
| F12 | 3 / 29 | 合成 SDK callback 保持、dispose 失敗、旧 Scene delete 不足。同 exact lease の pending / 修復 / 所定 outcome |

期待 outcome と cleanup policy は [固定 manifest](../../../scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json)
が決める。観測結果に合わせた `complete` / `retained_pending` の選択や、rescue を product cleanup とする扱いは不可。

## 5. 境界・判定器 C01〜C05

| Family | Variants / assertions | 正式 I6 へ接続する証拠 |
| --- | ---: | --- |
| C01 | 5 / 19 | config / storage / catalog / mixed version / byte 上限の固定 golden と拒否変異 |
| C02 | 5 / 20 | 期限ちょうど、および heartbeat と Inspect / Launch / Stop / EOF の競合。fake clock、元 deadline、送信 future 保持 |
| C03 | 3 / 9 | synthetic boot changed / same / 矛盾 receipt。`pipes_eof` を捏造しない |
| C04 | 6 / 24 | wrong Runtime / capture、stale ACK / generation / input epoch / focus token が実 native gate で無効になること |
| C05 | 22 / 44 | report / artifact の破損・欠落、cross-run / capture / source、owner / generation 不一致、trace overflow、first failure 保持 |

C系を「テストも存在しない」と解釈しない。
[evidence tests](../../../scripts/tests/test_linux_real_app_v3_evidence.py)、
[adversarial tests](../../../scripts/tests/test_linux_real_app_v3_evidence_adversarial.py)、
[acceptance entrypoint tests](../../../scripts/tests/test_linux_real_app_v3_acceptance.py)、
既存 model / transport / runtime の契約テストに下地がある。
ただし、それらの unit test 成功は、固定 C case の全 assertions を満たす正式 I6 receipt ではない。
C04 は到達先の native gate まで必要で、gate 自体を mock した結果へ置き換えない。

## 6. 全体 runner・集約・外側回収

個別 family の実装とは別に、以下が I6 の完了条件として残る。

1. **全 profile の execution runner。**
   [run_acceptance()](../../../scripts/linux_real_app_v3_acceptance.py) は基準時点では
   全 required cases を `not_run` / `I6_RUNNER_UNAVAILABLE` として保存する第一段階の entrypoint である。
   個別 N01〜N06 runner はあるが、全87 variantsを同じ正式 profile として実行・集約する runner ではない。
2. **未接続の semantic oracle と全体 PASS 判定。**
   [evidence.py](../../../scripts/linux_real_app_v3_evidence.py) は schema / reference / source / owner / cleanup を検査するが、
   残る product / file / pixel oracle を省いて全体成功にはしない。
   最終分岐も、未接続 oracle がある間は `I6_ORACLE_UNAVAILABLE` へ fail-closed にする。
   残る全 required assertions の実検証を実装した後に、全体 PASS の経路を接続する。
3. **外側環境の破棄証拠。**
   N02〜N06 の typed Host receipt は product retirement を証明するが、Docker 等の外側環境破棄を証明しない。
   現在の validator はそれらの `environment_disposal == "not_yet"` を明示的に要求している。
   N01 の部分判定もこの未完了境界を正式全体成功には流用しない。
   外側 supervisor の別証拠契約を設け、product result 固定後の disposal と通常回収を区別する必要がある。
4. **正式 profile の CI と保存済み再検証。**
   全 variant / assertion、source と artifact provenance、所有関係、cleanup policy、失敗履歴を独立に照合する。
   異なる実行の部分成功を無条件に合成せず、CI checkout / PR HEAD / main merge を区別する。

現時点の `host_v3_acceptance_passed=false` を維持する。
構造上完全な report、producer の `status=pass`、全体 manifest の存在だけでは PASS にしない。

## 7. 既知課題と CI 実行環境

以下の issue は調査時点で open である。相互に同じ原因だと断定しない。

- [Issue #45: 自然 GUI Close と異常終了の reason / terminal outcome 分類](https://github.com/tin-machine/eidolon-wayland/issues/45)。
  正常 Close の因果関係と回収が成立しても `APP_WINDOW_UNSUPPORTED` が残る分類問題。
  N06 専用 oracle の判定と、製品 reason の改善は別である。
- [Issue #50: 実 Ctrl+O 配送後の間欠的 Dialog 未観測](https://github.com/tin-machine/eidolon-wayland/issues/50)。
  同一 SHA の再実行成功は根本原因修正を意味しない。
- N06 の過去の `I6_TRACE_OVERFLOW`、および B4 / N06 の Close reason / sequence 競合も
  [B3](i6-n07-b3-pixel-review.md) / [B4 の失敗履歴](i6-n07-b4-two-sessions.md) として保持する。
  #50 の Dialog 欠落や設定不変条件違反へ同一視せず、再発時の artifact を分けて診断する。
- 基準 HEAD の Android / foundation は、APT install の既存240秒期限・exit124で停止した。
  [CI APT/DNS runbook](../../ci-apt-dns-reliability-20261112.md) の実行環境問題として扱い、
  I6 oracle / product deadline を緩和する理由にしない。build / GUI 検査前に停止した job の成功を主張しない。

## 8. I7 / H と分離する項目

[統合計画 §11.1〜13](../../linux-real-app-v3-integration-plan-20261111.md) と
[I6 詳細設計 §20](../../linux-real-app-v3-i6-detailed-design.md) に従い、以下を別ゲートに残す。

| 後続 gate | 必要な追加証拠 |
| --- | --- |
| I7 / V12 | 実 APK、Meta Spatial SDK、Surface / callback、通常 Android storage 保存、Quest 上の起動 / 入力 / Recover / 再起動 / Scene 交代 |
| I7 の update 互換 | 同一署名の旧 APK からの update と実保存状態。N11 の旧 source / journal 互換とは別 |
| H | 装着しての文字視認、window / dialog の位置、物理キーボード、controller、passthrough、操作感 |
| 実 reboot profile | 実 boot 変更後の証拠。C03 の synthetic boot provider 成功で補完しない |

Quest 3 実機検証は I6 の Host 実装を進める前提条件ではない。
Host の合成 SDK、ADB screenshot、合成入力から、実 XR panel の視認や物理入力の成功を推定しない。

## 9. 推奨する実装順序

1. **直近の CI 環境障害を改善し、B4 の設定契約を整合させる。**
   既存失敗 artifact を保持し、全体成功と部分取得を分ける。
2. **N07 の effective theme / 独立6状態 reference / Golden review / B5 を進める。**
   reference 取得と後の Acceptance run、実装と人の承認を別工程にする。
3. **N08〜N11 の正常系・分離・互換を追加する。**
   N07 の観測機構を再利用しても、receipt / identity / storage を不正に共有しない。
4. **F01〜F12 と C01〜C05 を、所有境界ごとに分けて実装する。**
   純契約系は並行可能だが、正式 receipt と required assertion の対応付けを省かない。
5. **全 profile runner、外側 disposal 証拠、集約 validator、CI を接続する。**
   全28 families / 87 variants / 533 assertionsの現 source の実証拠をそろえてから I6 PASS を判定する。
6. **I7 / H へ引き継ぐ。**
   Host で確定した範囲、端末で追加する証拠、既知課題を明示する。

この順序は実装の優先案であり、固定 manifest の required 集合や各 case の意味を変更しない。
