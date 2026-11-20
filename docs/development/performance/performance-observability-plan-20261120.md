---
id: performance-observability-plan-20261120
title: Eidolon Wayland Performance Observability plan
type: architecture
design_status: proposed
feature: performance-observability
component: quest-android-native-host
created: "2026-11-20"
last_reviewed: "2026-11-20"
---
# Performance Observability — Quest 3 / Android / Linux Host

**Status: proposed / unregistered / documentation only.** 本書の追加は、計測機能の実装、Questでの計測成功、SLI/SLO承認、I6/I7/Hの受入れ完了を意味しない。実装時は最新main、対象SDK/Horizon OS、観測権限、現行コードを再調査する。初回実機手順は[runbook](performance-observability-quest-runbook-20261120.md)。

## 1. 目的・前提と既存契約

最終目標は、Quest 3のMR空間で、別Linuxホスト上の複数のGUIアプリを独立したウィンドウとして快適に扱うこと。性能を「軽そう」という推測でなく、**空間描画の安定性、リモート内容の鮮度、入力応答、資源消費と回収**の独立した証拠で評価する。

- **現在のLinux Desktop v3は同時に1 GUIアプリを起動する契約**。Mousepad 0.6.3 / GTK3の1窓を基準にする。Termux/footの9窓実績を「複数の独立したv3 Mousepadの実証」へ読み替えない。
- 現行経路はSSH / Waypipe、Wayland/Smithay、Rustのprotocol/core/render、JNI、Kotlin/Spatial SDK、EGL/GLES、SDK Surface / XR compositor。実際のwire符号化やGPUアップロードAPIは**ソースと計測で同定**し、独自video encoder、`glTexSubImage2D`、zero-copy等の採用を前提にしない。
- 既存の`scripts/quest-observe`、`scripts/observation.py`は電池/温度/PSS/FD/CPU、runtime/queue/ownership等の低頻度採取を持つ。**再利用し、新しい高頻度トレースに置き換えない**。
- 現行`native/render/src/lib.rs`の`FrameTiming` / `frame_timing` はdraw＋swapの**CPU wall time（GPU待ちを含む）**であり、GPU timestamp・XR present・photonの計測値ではない。[既存計測の意味](../../design/theme-animation.md)、[GPU FD調査](../../evidence/experiments/gpu-fd-investigation.md)を維持する。
- [v3統合計画](../../features/linux-desktop-v3/linux-real-app-v3-integration-plan-20261111.md)、[I6詳細設計](../../features/linux-desktop-v3/linux-real-app-v3-i6-detailed-design.md)、[I6残件](../../features/linux-desktop-v3/i6-remaining-work-20261119.md)、[Phase 6](../../design/phase-6.md)、[既存操作手順](../../guides/usage.md)と矛盾させない。実装有無・CI成功・Host acceptance・Quest/HMD物理受入れは異なる状態。

**非目標:** この設計PRでのSDK改造、GPU rendererの先行最適化、v3同時起動上限変更、HMD acceptanceの代替、製品にADBや外部collectorを常時必須化すること、秘匿情報の採取。

## 2. ボトルネックのモデルと責任境界

```text
Quest: Android/SDK input → JNI/Rust → Wayland seat
                                      ↓ Waypipe / SSH
Host:                         GUI input → surface commit
                                               ↓ Waypipe / SSH
Quest:           compositor → render queue → EGL swap → SDK Surface → XR compositor
```

上記は**論理的な観測点**であり、WaypipeとEidolonの各転送段階を1本のシリアル処理と断定しない。各イベントの「enqueue/dequeue、処理開始/完了、拒否/破棄、surface generation、ownership barrier」を分け、並行実行・backpressure・GPU非同期処理を表現する。

| 観測ドメイン | 最初に確認する問題 | 適した手段 |
| --- | --- | --- |
| Kotlin/Android | 入力dispatch、Main thread停止、JNI境界、GC/メモリ | Android Studio Profiler、Perfetto/Atrace |
| Rust/native | CPU hot path、thread runnable/blocked、lock、allocator、Tokio scheduling | simpleperf（利用可否確認）、Perfetto、既存診断 |
| GPU/EGL/SDK | import/copy/upload、draw、swap、GPU/compositor time、surface再生成 | OVR Metrics、必要時ovrgpuprofiler・AGI（**Questで対応可否を確認**） |
| Host/Linux | Wayland commit、render queue、transport、Mousepad処理 | Linux側Perfetto/traceまたは上限付きアプリイベント、host資源監視 |
| Network | RTT/jitter、delivery、queue/backpressure、frame drop | 送受信イベント、既存transport診断、独立ネットワーク試験 |
| Long run | thermal throttling、CPU/GPU level、PSS/FD/texture滞留、再接続劣化 | OVR Basic CSV、quest-observe、停止/再起動evidence |

単にCPU使用率が低い・FPSが高いから良好とはしない。**レイテンシーのクリティカルパスと待機理由**を追う。特に`eglSwapBuffers` CPU時間とGPU execution timeは別物。

## 3. SLIの定義（同名異義を禁止）

| SLI / 対象 | 定義、集計単位、観測の境界 | 注意 |
| --- | --- | --- |
| XR frame stability | app FPS、stale/dropped frame countまたはratio、app/compositor GPU time、最大連続stale frame。Questのリフレッシュレート設定ごとに集計 | Metaの定義に合わせる。app FPSとHMD compositorのfpsを同一視しない |
| Remote update latency | Linux側の**有意な内容更新**がWayland commitとして見えてからQuest側SDK Surfaceへ提出完了するまでの時間。p50/p95/p99と分布、別途drop/timeout | まずは software submit latency。GPUのpresent完了とは呼ばない |
| Input response latency | Questで入力を観測してから、それに帰属できる**視覚変化**のSDK提出まで。操作種別別にp50/p95/p99 | 入力からホスト反映までを分離。未応答を欠測として除外しない |
| Input-to-photon | 入力から**実表示**まで。外部センサ/高速度撮影等で校正した場合だけ使用 | software submitからの単純推定をphotonと命名しない |
| Update freshness / staleness | 送る必要が生じた更新が現在も表示未反映である継続時間、および更新の無い静止状態の区別 | 最終commitからの経過時間を静止Mousepadの劣化と誤認しない |
| Queue/backpressure | 待ち行列長、enqueue→dequeue待機、coalesced/dropped、blocked producer、byte budget | queue 0の瞬間値だけで判断しない。長さと待ち時間の双方を測る |
| Resource/thermal | PSS/RSS、FD、owned texture/bytes、thread count、CPU/GPU level、thermal/throttle、battery/充電 | HMD温度、周囲温度、熱状態、AC給電を分ける。電池温度だけでthrottle判定しない |
| Retirement/recovery | Close/Stop→cleanup barrier、失敗理由、再接続後の初回応答、資源baseline復帰 | 現行のowner/generation/cleanup_provenやI6受入れ契約を変更しない |

**分母/母集団:** run、session、window、input type、update type、visible/hidden、success/timeout/dropを明示する。runをまたぐ中央値だけでp99を生成しない。少数イベントのp99は参考値扱い。warm-upとsteady stateを分離し、未観測・未対応・不合格を明確にする。

### 3.1 仮の目標値（契約・CI gateではない）

以下は**候補SLOであり実測から調整**する。固定値を受入れ条件に昇格させない。

- XR stale/dropped frame ratio < **0.1%**（定義・分母を確定後）。
- Remote meaningful update → SDK submit p95 < **50 ms**、p99 < **100 ms**。
- Quest input → correlated SDK submit p95 < **80 ms**。
- 30分時点の同条件PSS増加 < **5%**（GC/初期cache/OS変動と回収条件を揃える）。
- instrumentation overhead目標：off vs onの主要SLI差 < **2%**。計測誤差・試行間変動を超えて判定できた場合だけ適用。

リフレッシュレート72Hzの1周期は約13.89msだが、CPU/SDK/ネットワーク各段階に一律13.89msの予算を与える意味ではない。

## 4. 相関IDと時刻の契約案

**既存のv3 protocol/golden/journal/StorageRecordを変更しない設計を優先。** デバッグ/観測側の追加識別子で表現できるかを先に確認する。どうしてもwire変更が必要な場合は互換性・版選択・旧 v2/v3 の dispatch を変更箇所のテストと一緒にレビューする。

- `run_id`、`session_epoch`（新nonce/世代と区別）、`window_id`、`surface_generation`、`content_update_id`（更新sequence）、`input_event_id`（内容を含まない相関用）。IDは再起動・recreateで誤結合しない範囲と寿命を持たせる。
- event schema案：`schema_version`, `run_id`, `source`, `source_clock`, `monotonic_ns`, `session_epoch`, `window_id`, `generation`, `event_type`, `correlation_id`, `duration_ns?`, `outcome`, `drop_reason?`, `bytes?`。実際の定数名は実装PRで固定。
- event例：`input_observed` / `input_forward_enqueued` / `host_input_received` / `wayland_commit` / `transport_sent` / `transport_received` / `decode_or_copy_begin/end` / `render_enqueued/dequeued` / `egl_swap_begin/end` / `sdk_surface_submitted` / `retirement_barrier_complete`。実経路にない段階は記録しない。
- **同一process内**は単調時計でdurationを求める。hostとQuestの時計は独立であり、生のtimestamp差を遅延としない。双方向の繰り返し交換でoffset・drift・RTT・不確かさ`epsilon_ns`を評価し、各runの推定区間を保存する。NTPの`RTT/2`は非対称ネットワークで厳密ではない。信頼できない区間は`cross_host_latency=unavailable`とする。
- **非同期境界:** CPUでのsubmit、EGL swap戻り、GPU fence/timestamp（対応を確認）、compositor present、photonsを別イベント/時計として保持する。submit/ACKだけを表示成功へ昇格しない。
- 入力と画面更新は必ずしも1:1でない。曖昧な対応を`ambiguous`、表示更新なしを`no_visual_change_expected`、期限超過を`timeout`に区分し、最短の更新を恣意的に選ばない。

## 5. 計測の実装方針と侵襲性

1. **Quest P0:** Meta Horizon Developer Hub（Performance Analyzer / Metrics）、OVR Metrics ToolのBasic HUD/CSVを第一選択とする。取得可能なメトリクス/実際の版をrunごとに固定。Advancedは原因追跡時だけ使用し、Basicとのオーバーヘッド差を記録する。
2. **Quest P1:** Android/Kotlinでは標準Trace API/Jetpack tracing、Rust/nativeでは利用可能ならNDK ATrace互換APIの薄いラッパを優先する。Perfetto traceでscheduler、thread、GC、EGL周辺と紐付ける。RustにPerfetto C++ SDKを無条件導入しない。必要時に別案を比較。native CPU hot pathはデバッグシンボル保持とsimpleperfを検討。
3. **P2 Host↔Quest:** 最初は**制限付きイベントと既存診断の橋渡し**で相関を証明する。Perfetto単独ですべてのホスト・Quest・XRのトレースが揃うという前提を置かない。
4. **GPU P4:** OVRのApp GPU / Timewarp（Compositor側）を起点にする。ovrgpuprofilerとAGIは、実機で権限・サポート・計測歪みを確認してから利用。GLES timer queryやfenceはextension/コンテキストの成立とディスジョイント条件を検査し、GPU側完了として明示する。
5. **通常時:** サンプリングとbounded ring/counterで**低オーバーヘッド・debug opt-in**。detail tracingは期限・容量上限・事後offを設ける。per-frame無制限JSON/ADB同期を禁止。統計対象のskip/drop/overrun数は必ず持つ。
6. **レイヤーの切り分け:** 追加のパフォーマンススレッドが既存のGL executor 1本/1 job、retirementと所有の契約、SDK GPU計測FDに与える影響を回帰検査する。監視のためにGL threadを増やさない。

## 6. 実験設計とボトルネック判定

### 単一v3アプリ（早期に実施可能）

同一ビルド内のシナリオ比較では APK・Host build と Mousepad の設定・保存領域をそろえる。修正前後は §7 の A/B 条件で比較する。起動、接続、入力、Close→retirement→再起動を分けて記録する。

| シナリオ | 運用 | 重視する指標 |
| --- | --- | --- |
| A idle | Mousepad静止（入力・更新なし） | XR安定、CPU/GPU idle、PSS/FD、不要な描画/転送 |
| B editing | 一定の速度・量の入力/編集、保存 | input response、commit、queue、freshness |
| C pointer | スクロール、メニュー、popup、ダイアログ | transient frame、focus、pointer反応、drop |
| D resize | panel/Wayland resize、視点変更、必要な再生成 | buffer割当、swap時間、GPU memory、stale |
| E restart | Close / Stop / reconnect / new nonce / 同一Storage | 退役、初回更新、資源復帰、設定保存への影響 |
| F fault | network jitter/disconnect、host app exit、Surface再生成 | bounded retry、stale/timeout/回収、ユーザー状態 |

これらは**性能測定**であってI6の28 families / 87 variantsやQuestの物理受入れを自動で満たさない。ホストで作ったfixtureのpixel成功はHMD視認を証明しない。

### 多ウィンドウと複数セッション

現行の1セッションでも、本文だけの場合と「開く」ダイアログを加えた場合で描画・入力・回収を比較できる。独立アプリの同時起動数を増やす試験は、その数をサポートした段階で追加する。

独立GUIアプリ数`1 / 2 / 4 / 8 / 16`を**段階的な負荷の仮説値**として使う。SDK layer/compositor制限やGPUメモリ、製品の起動上限を超えて強制実行しない。上限未満で観測できる範囲を正式な試験行とする。

必ず`visible_windows`、`active_updating_windows`、`independent_sessions`、panel解像度、更新面積を分離。全窓静止、1窓のみ編集、全窓高頻度更新の3条件で、CPU/GPU/network bytes、VRAM相当、p99、dropを比較する。Termux/foot 9窓は別baselineとしてラベルを付ける。

**計測後に評価する最適化候補**（このPRでは実装しない）：dirty-region tracking、frame coalescing（途中状態と入力応答の意味を保持）、visibility-aware scheduling（非表示でもライフサイクル・通知を破綻させない）。効果だけでなく画質・入力と回収への退行を評価する。

### 長時間・熱（P5）

最短の比較用runはwarm-upとsteadyを分けて少なくとも3反復を推奨。次に同条件で5分/30分/2時間の長時間runを行い、時間帯別p95/p99・stale・CPU/GPU level・thermal・PSS/FD・batteryを比較する。給電方式、充電率、室温、MR環境、HMD装着/非装着、refresh rate、Guardian/OS状態、Wi-Fi AP/帯域、remote host負荷を固定または明記する。環境の変更があればrunを分ける。

## 7. 成果物・エビデンス・判定

障害切り分けでは、対象ビルド・操作・症状・観測元を既存の作業記録に残す。以下は再現可能なベースラインや比較結果を保存するときの構成例。各 run に用いた採取元だけを収録する。

```text
private-artifact-root/<run_id>/
  environment.json     # Quest OS/build, SDK, APK git SHA/build, host SHA, GPU, refresh rate, tool versions
  scenario.json        # action, window/update mode, duration, sampling mode, heat/power/network conditions
  metric-source.json   # 実際のOVR metrics名・取得成功/不可、時計精度、欠測・overhead
  quest-observe/       # 既存の出力を参照/コピー（取得可能な場合）
  ovr-basic.csv        # OVR Metrics Toolからoperatorがexportした場合のみ
  perfetto.pftrace     # opt-in取得に成功した場合のみ
  trace-events.jsonl   # 将来の上限付きイベント計測（実装後のみ）
  result.json          # measured, missing, unsupported, unverified, failedの区別と統計
```

- 原則ローカルの**非公開**artifactへ保存。`git add` / public Actions artifact / release APKへの生ログ同梱を禁止。画面画素、スクリーンショット、キー内容、clipboard、ファイル/ウィンドウタイトル、private IP、host key・SSH・storage情報は採取しない。画像やraw traceが必要な検証時は別の明示的許可と秘密情報の確認を必須とする。
- 個別window ID等は低cardinalityのrun内相関に限定し、無期限のPrometheus labelにしない。集計用seriesはbounded。`event_dropped`、`sampling_ratio`、時計`epsilon`、取得失敗、ツールoverheadを記録する。
- **比較の成立条件:** A/B で変更する要因（修正ビルド、計測 on/off 等）を定め、両側の版と条件を記録する。その他のワークロード・端末設定・採取条件をそろえ、指標に必要なサンプル数と欠測率を確認する。別時計をまたぐ指標では同期の不確かさも評価する。満たさない場合は`inconclusive`。実際のp99が改善してもコンテンツの欠落・遅れや画質劣化が増えれば不合格。
- Performance regressionをCI required checkへ直結する前に、再現性、ノイズ、機器排他、測定の侵襲性を検証する。shared runnerでの模擬テストはprotocol/統計/閾値ロジックのみ。

## 8. 実装PR単位・依存順・Definition of Done

| PR単位 | 成果物 | 必須条件とgate |
| --- | --- | --- |
| **P0** Quest metrics baseline | OVR Basic HUD+CSV、Meta Performance Analyzerの使い方、現行quest-observeとの同条件サンプル、run fingerprint | **Quest operator実測**または明示`blocked/not_run`、取得可能metrics表、削除/復旧手順 |
| **P1** Android/native/Perfetto | Kotlin/Rustのbounded trace spans、Perfetto取得、既存draw/swapとの対照、symbolized native CPU flamegraphの実現性 | on/off overhead比較、debug/release差、GL single-executor維持 |
| **P2** Host↔Quest correlation | ID/世代設計、イベント採取、時計offset・drift・不確かさ、input/update latency分析 | 同一host/Quest内span検査、cross-host ambiguous/clock skew/drop負例、未表示を成功にしない |
| **P3** windows scalability | 実現可能なwindow数でidle/partial/all-update比較、layer/visibility/active区別 | 単一セッションの親窓・ダイアログから開始。独立アプリの比較は製品が対応する同時起動数で実施 |
| **P4** GPU deep dive | OVR Advanced/ovrgpuprofiler/AGI実機capability probe、GPU timer/fence候補、ボトルネック報告 | unsupportedはskipではなく`unsupported`。CPU swap時間との混同を否定 |
| **P5** soak/thermal/leak | 5/30/120分時間帯別比較、熱/クロック/メモリ/FDと回収試験 | 既存quest-observeとPhase 6 L03/L04を併用。長時間・HMD結果は独立 |
| **P6 (optional)** SLO/trigger/CI | 実測に基づくSLI/SLO提案、異常時boundedリングバッファ保存、性能退行アラート/実機ラボCI | ノイズ/overhead/clock/failureを統計的に評価するまでrequiredにしない |

通常のベースライン収集は P0 から始める。無応答や停止失敗はその場で終了記録・UI スレッド・回収状態を調べ、必要な P1 計測だけを追加できる。P2 の時計校正は Host と Quest をまたぐ遅延を公表するときに行う。P5 の長時間試験は熱・リークを調べるとき、P4 は GPU 待ちを示す観測が得られたときに選ぶ。P6 の閾値・CI 化は再現性を確認した後に判断する。

## 9. 使用ツールと一次情報

| Tool | 目的 | 一次情報と制約 |
| --- | --- | --- |
| Meta Horizon Developer Hub / Performance Analyzer | Quest上のCPU/GPU/FPS等の可視化・記録 | [Meta Performance Analyzer and Metrics](https://developers.meta.com/vr/documentation/native/android/ts-mqdh-logs-metrics/) |
| OVR Metrics Tool | Basic/Advanced HUD、session CSV、熱/FPS/stale/GPU | [Meta OVR Metrics Tool](https://developers.meta.com/vr/documentation/native/android/ts-ovrmetricstool/)、[stat definitions](https://developers.meta.com/vr/documentation/native/android/ts-ovrstats/)、[overhead guidance](https://developers.meta.com/vr/documentation/native/android/ts-ovr-best-practices/) |
| Perfetto / Android Trace | scheduler/threads、app slice、native trace、SQL集計 | [Perfetto SDK](https://perfetto.dev/docs/instrumentation/tracing-sdk)、[Trace Processor](https://perfetto.dev/docs/analysis/trace-processor)、[Android custom events](https://developer.android.com/topic/performance/tracing/custom-events) |
| Android Studio Profiler / simpleperf | Java/Kotlin/GC/Native CPU、symbol化 | [Android profiling guide](https://developer.android.com/studio/profile)、[Android NDK simpleperf](https://android.googlesource.com/platform/system/extras/+/refs/heads/main/simpleperf/doc/README.md) |
| AGI | GPU/system/frame詳細解析（**Quest対応保証なし**） | [AGI](https://developer.android.com/agi)、[supported devices](https://developer.android.com/agi/supported-devices) |

上記リンクは検討・設計の根拠であり、Quest 3の採用OS/SDKですべてのAPI・カウンタが使えるという実機保証ではない。
