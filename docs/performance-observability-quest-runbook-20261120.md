# Quest 3 Performance Observability — 実機採取Runbook（初期案）

**Status: proposed / not executed / manual discovery.** この手順は性能計測のためのoperator文書であり、Questでの採取完了やI6/I7/H acceptanceを主張しない。[設計とSLI](performance-observability-plan-20261120.md)を先に読む。既存の[開発環境/ADB](development-automation-and-debugging.md)、[通常の利用・観測](usage.md)、[Phase 6](design/phase-6.md)を優先する。

## 0. まず記録するもの（再現性）

- **Quest:** HMD型番、Horizon OS build、Android API level、GPU/CPU、refresh rate、battery/AC、室温、MR/passthrough、Guardian・装着/可視、睡眠/復帰、Wi-Fi接続。
- **Eidolon:** Git SHA / APK versionCode、debug/release/profileable設定、Spatial SDK版、Host Git SHA、Waypipe版、Mousepad 0.6.3設定/StorageRecordの同一性。ただしpath・鍵・設定本文は保存しない。
- **計測:** OVR Metrics Tool版、選択したBasic/Advancedのmetric一覧、Perfetto/Android Profilerの有無、採取時のADB経路、開始/終了時刻、warm-up、run id、サンプル欠損・工具による負荷。
- 同じrunと呼べるのは**同じ実行構成と測定条件**の場合だけ。電池・熱・Wi-Fiやfocus/Guardianが変われば条件を分ける。
- アプリがまだQuestで通常起動・実入力・正常終了できない場合、**先に通常v3の機能smoke/受入れを進める**。性能テストのために偽の成功証拠を作らない。

## 1. P0: OVR Metrics Tool + 既存のQuest観測

1. 開発者権限とADB端末の対象serialを確認し、[Meta Performance Analyzer / Meta Horizon Developer Hub](https://developers.meta.com/vr/documentation/native/android/ts-mqdh-logs-metrics/)からOVR Metrics Toolを導入・有効化する。toolと実機OSの組み合わせで可能な方法を選ぶ。
2. 原則**OVR Basic preset**を選択。少なくともFPS、stale frames、App GPU Time、CPU/GPU utilization、CPU/GPU level、battery/thermal情報のうち**実際に取得できた項目**を記録する。Compositor GPU Time等の名前は表示/CSVと公式定義を照合し、未提供なら`unsupported`。
3. OVRの**Metrics Recording/Report Mode**で、同じQuest runのCSVを後からexportする。HUDだけを見た場合はCSV収集済みと扱わない。撮影/画面キャプチャには内容露出の危険があるため、初期はグラフと数値だけを扱う。
4. 既存`quest-observe`の構成と用途を確認し、同条件の**別プロセスのADB採取**として可能な範囲で実行する。測定負荷/干渉を避けるため、OVRのみのrunとOVR+quest-observeのrunを比較する。
5. 測定後にOVRのrecording/HUDをoffに戻し、CSVを**公開Git外**のアクセス制限されたartifactsへ保存する。ADB接続切れは欠測であり、製品sessionが切れた証拠ではない。

既存operatorの**コマンド形**（新規ツールを実装したという意味ではない）：

```sh
# 既存のQuest側プロセスが動作中で、ADB対象が確定していること
scripts/quest-observe --serial "$EIDOLON_QUEST_SERIAL" \
  --scenario power-lifecycle --condition '給電・1 Mousepad・静止' --duration 1800

# 既存のsoakケースは長時間計測用。通常の製品接続/入力は別途行う
scripts/quest-observe --serial "$EIDOLON_QUEST_SERIAL" \
  --scenario soak --condition '給電・1 Mousepad・編集条件' --duration 7200
```

`quest-observe`は**30/120分の時系列**を採取する既存スクリプト。remote appの自動起動・物理入力・OVR CSV取得は行わない。`environment.json` / `samples.jsonl` / summaryの`acceptance: not_run`は実装済みの意味を維持する。最初のP0短時間実験では`--duration`を縮めてもよいが、短縮runを30分/2時間達成と記録しない。

## 2. P1: PerfettoとCPUボトルネックの診断

- Kotlin・Rustの計測点は、先に実装PRで明示的な計測spanを追加してから利用する。現行APKに独自`trace_event_id`があると仮定しない。
- PerfettoはQuest OS権限が許す方法（MVDH提供、Android system tracing、profileable app）で、**短時間・期限付き**取得。UIでスレッドのrunnable/blockedとCPU周波数、Kotlin/JNI/native、render、GC、EGL swapを重ねる。
- Trace APIが見えない場合は、app profileable設定・trace category・native/Java instrumentation・OS制限を区別して報告。特権化/root化によって無理に突破しない。
- CPU hot pathの詳細が必要であれば、debug symbolを別保管してsimpleperfで調査する。symbol uploadおよびAPK配布ライセンス・メモリ挙動は別レビュー。
- GPUの処理時間として`frame_timing.total_us`（CPU draw+swap wall time）を再ラベルしない。GPU timer/fenceやOVRを併記。

## 3. P2: Host → Questフレームと入力相関

**まだ実装されていない将来の操作。** 設計PR後の別実装PRでevent schema / ID / clock精度のテストを先に固定する。

```text
run/session generation
  Quest input (input id) ----> Host dispatch ----> GUI update (content seq)
                                      |                 |
                  Host monotonic -----+                 +--> send --> Quest receive
                                                                |
                Quest monotonic --------------------------------+--> GL/Sdk submit
                [cross-host clock offset ± uncertainty]       [not photon]
```

- 描画更新と入力イベントを取り違えない。静止frameのno-op、coalesced、surface recreate、old generation、遅いFrameのdiscard、非可視、cancel、自然終了で負例を取る。
- タイムスタンプ差分を出す前に、host/Quest clock origin、calibration interval、drift、RTT/asymmetryの可能性、uncertaintyを確認する。
- latencyを`software_submit`と`display_photon`に明確に分ける。後者は必要になった時に外部の高速度カメラ/センサ等で取得し、HMDレンズ・撮影位置の校正を含める。
- `p99`の低下だけでは合格にしない。表示変化の検出、miss/drop、持続backpressureの増加を合わせて評価する。

## 4. P3/P4: 多窓とGPUの詳細（製品対応後）

- **v3単一アプリの制約がある間に独立アプリ2/4/8/16を合格と宣言しない。** 制限緩和後も、Quest layer上限・アプリpanelとシステム消費・同時可視数・GPU memoryを先に実測する。
- 1窓→実現可能な枚数へ段階的に増やす。**全静止／1窓のみ高頻度更新／全窓高頻度更新**をそれぞれ測る。visibleとactiveを必ず分離。
- P0/P1でGPU仮説が支持されたときだけ、OVR Advancedやovrgpuprofilerの有効化、Android GPU Inspectorの**実機互換性check**、GLES timer queries/fencesを検討する。QuestでAGI frame captureが使えるとは限らない。
- 同じ場面でCPU swap time、GPU App Time、Compositor Time、stale/dropped、Host更新遅延を突き合わせる。描画最適化による画質/入力/回収への副作用を確認する。
- **将来の最適化候補**：dirty region、frame coalescing、visibility-aware scheduling。原則として性能baselineを取得してから別設計・別PR。

## 5. P5: 長時間/熱と復旧

- 固定シナリオの5分（初期）/30分（warm/thermal）/2時間（soak）で、少なくとも開始直後・中盤・終盤の時間帯ごとに分布を比較する。30分だけで2時間passとしない。
- CPU/GPU level、App GPU Time、stale、clockの制限、battery temp、PSS、FD、owned render resources、SSH/Waypipe生存・再接続を時間軸で重ねる。
- 既存のGL executor 1本使い回し/FD保持問題（[GPU FD調査](gpu-fd-investigation.md)）と比較し、計測機能が新しいGL threadやresource leakを作らないことを確認する。
- power/thermalを比較するとき、充電器・ケーブル・バッテリー残量・室温・冷却状態とHMDの装着/焦点状態を記録する。放熱条件の異なるrunを同等と扱わない。
- Stop後のowned resource cleanup、SDK UI復帰、sleep/reboot/Guardianによる中断、ADB観測喪失を別に分類する。

## 6. 判定と報告のテンプレート

| 項目 | 記入する内容 |
| --- | --- |
| Test identity | run id、Git SHA、APK/build、Host SHA、Quest/Horizon OS、SDK、ツールver |
| Scene/workload | A–Fケース、入力条件、窓数（v3単一）、表示/更新状態、warm-up、duration |
| Runtime | FPS/stale、App GPU / compositor time、温度/level、PSS/FD、CPU、queue |
| Remote/input | 適用可能なら遅延のp50/p95/p99、correlated/unknown/drop/timeout、clock uncertainty |
| Trace artifacts | Private pathのみ。CSV/Perfetto有無、サンプル欠測、overhead、raw imageの有無 |
| Verdict | `measured` / `inconclusive` / `unsupported` / `not_run` / `failed`（試験種別ごと） |
| Cleanup | OVR/trace off、ADB条件復旧、Eidolon Close/Stop、owned resources確認 |
| HMD acceptance | `not_run`、または**別に実施した人の装着・物理操作証拠**へリンク |

例として`Quest OVRの収集成功`と`Host↔Quest latencyの校正未実施`は同時に成立する。**「採取できた」≠「閾値に合格した」≠「ユーザーが快適に使える」**を保持する。

## 7. 安全・秘密情報・トラブル

- `adb` / diagnostic accessはデバッグ限定。鍵/nonceの秘密部分、Host address、SSH認証・設定本文、clipboard、入力文字、画面/ウィンドウタイトルは記録しない。raw Perfetto/スクリーンショット/CSVに意図しない情報があれば**公開前に必ずレビュー**する。
- 限定公開ローカル`artifact-root`以外には生計測を送らない。CI artifact/PR descriptionへprivate pathやtraceを埋めない。
- 計測によるCPU/GPU干渉、ADB Wi-Fi断、thermal override、unknown guardian/focus時は試験を中断/分類する。元のアプリ・ヘッドセット設定に戻せなかった場合は`cleanup_failed`。
- 現行CIに新しい実機依存のrequired checkは追加しない。host側でevent/clock/validatorの純粋テストを追加した後、実機テストは明示的なoperated runで行う。

## 一次情報

- [Meta: Performance Analyzer and Metrics](https://developers.meta.com/vr/documentation/native/android/ts-mqdh-logs-metrics/)
- [Meta: OVR Metrics Tool](https://developers.meta.com/vr/documentation/native/android/ts-ovrmetricstool/)
- [Meta: metric statistics](https://developers.meta.com/vr/documentation/native/android/ts-ovrstats/)
- [Meta: overhead best practices](https://developers.meta.com/vr/documentation/native/android/ts-ovr-best-practices/)
- [Android: custom system trace events](https://developer.android.com/topic/performance/tracing/custom-events)
- [Perfetto: Trace Processor](https://perfetto.dev/docs/analysis/trace-processor)
- [Android GPU Inspector: supported devices](https://developer.android.com/agi/supported-devices)
