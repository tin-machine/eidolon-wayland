# Quest 3 Performance Observability — 実機採取Runbook（初期案）

**状態:** §2 の待機時 CPU 計測は Quest 3 で実行済み。ほかのシナリオの実施状況は各記録で確認する。[設計とSLI](performance-observability-plan-20261120.md)、[Quest 開発前チェック](../quest-development-preflight.md)、[開発環境/ADB](../development-automation-and-debugging.md)、[通常の利用・観測](../../guides/usage.md)を参照。

## 0. 障害の初動と比較条件

単発の障害はビルド・操作・症状を手掛かりに、失われやすい記録から採取する。再現比較を行う段階で以下の環境条件を記録する。

- **Quest:** HMD型番、Horizon OS build、Android API level、GPU/CPU、refresh rate、battery/AC、室温、MR/passthrough、Guardian・装着/可視、睡眠/復帰、Wi-Fi接続。
- **Eidolon:** Git SHA / APK versionCode、debug/release/profileable設定、Spatial SDK版、Host Git SHA、Waypipe版、Mousepad 0.6.3設定/StorageRecordの同一性。ただしpath・鍵・設定本文は保存しない。
- **計測:** OVR Metrics Tool版、選択したBasic/Advancedのmetric一覧、Perfetto/Android Profilerの有無、採取時のADB経路、開始/終了時刻、warm-up、run id、サンプル欠損・工具による負荷。
- A/B 比較では変更対象と両側のビルドを記録し、その他の条件をそろえる。電池・熱・Wi-Fi や focus/Guardian の変化は時刻とともに記録する。
- **無応答・停止失敗の初動**では、再起動前に取得できる Android の ANR・終了記録、対象プロセス、UI スレッドのスタック、Eidolon の回収状態を確認する。既に起動し直した場合は旧プロセスの記録を照合する。採取時のスタックは原因候補として記録する。正常に動く操作区間が取れたら、その区間を P0 のベースラインに使う。

## 1. P0: OVR Metrics Tool + 既存のQuest観測

1. 開発者権限とADB端末の対象serialを確認し、[Meta Performance Analyzer / Meta Horizon Developer Hub](https://developers.meta.com/vr/documentation/native/android/ts-mqdh-logs-metrics/)からOVR Metrics Toolを導入・有効化する。toolと実機OSの組み合わせで可能な方法を選ぶ。
2. 原則**OVR Basic preset**を選択。少なくともFPS、stale frames、App GPU Time、CPU/GPU utilization、CPU/GPU level、battery/thermal情報のうち**実際に取得できた項目**を記録する。Compositor GPU Time等の名前は表示/CSVと公式定義を照合し、未提供なら`unsupported`。
3. OVRの**Metrics Recording/Report Mode**で、同じQuest runのCSVを後からexportする。HUDだけを見た場合はCSV収集済みと扱わない。撮影/画面キャプチャには内容露出の危険があるため、初期はグラフと数値だけを扱う。
4. 既存`quest-observe`の構成と用途を確認し、同条件の**別プロセスのADB採取**として可能な範囲で実行する。測定負荷/干渉を避けるため、OVRのみのrunとOVR+quest-observeのrunを比較する。
5. 開始時の OVR recording/HUD 設定を控え、測定後にその状態へ戻し、CSVを**公開Git外**のアクセス制限されたartifactsへ保存する。ADB接続切れは欠測であり、製品sessionが切れた証拠ではない。

既存operatorの**コマンド形**（新規ツールを実装したという意味ではない）：

```sh
# 既存のQuest側プロセスが動作中で、ADB対象が確定していること
scripts/quest-observe --serial "$EIDOLON_QUEST_SERIAL" \
  --scenario power-lifecycle --condition '給電・1 Mousepad・静止・短時間診断' --duration 60 --interval 10

# 既存のsoakケースは長時間計測用。通常の製品接続/入力は別途行う
scripts/quest-observe --serial "$EIDOLON_QUEST_SERIAL" \
  --scenario soak --condition '給電・1 Mousepad・編集条件' --duration 7200
```

`quest-observe`は**30/120分の時系列**を採取する既存スクリプト。remote appの自動起動・物理入力・OVR CSV取得は行わない。`environment.json` / `samples.jsonl` / summaryの`acceptance: not_run`は実装済みの意味を維持する。短時間例は採取器の動作確認用。終了コードが 0 でも端末が途中で応答しなくなるとサンプルに欠測が残る。`result.json` の `sampling_complete`、`complete_samples`、`same_process`、`diagnostics_advancing` と各サンプルの `errors` を確認して比較できる区間を決める。

## 2. P1: PerfettoとCPUボトルネックの診断

### 待機時 CPU を繰り返し測る

1. 同じ Quest、Eidolon ビルド、表示 Hz、給電と熱状態で、Eidolon にフォーカスを戻す。HUD を一時的に非表示にし、操作を止めて安定してから測る。基準は「Linux 窓なし」と「検証用の Linux 窓1枚を表示して静止」。接続中のホスト数と窓の装飾設定も記録する。
2. UI・ランチャー・SDK 連携や回収の変更前後、または操作に反応しない・カクつく事象を再現できたときは、同じ条件の30秒トレースを採る。各 run の FPS、前フレーム再利用、GPU 時間も記録する。定期的に傾向を見るときは [既存の低頻度採取](#1-p0-ovr-metrics-tool--既存のquest観測)を使い、スケジューリングトレースや関数サンプルは原因を調べたい区間だけ採る。
3. Perfetto で Eidolon の CPU 実行時間をスレッド別に集計し、上位のスレッドで実行・実行待ち・スリープの並びを見る。Sleep と短い実行を周期的に繰り返す場合は、その周期を実装と突き合わせる。CPU 上の実行時間の合計を計測秒数で割ると、平均で何コア分使ったか分かる。
4. 原因候補が絞れたら、同じ場面で Simpleperf を別 run として15秒採る。`simpleperf report --sort comm,dso,symbol --children` でスレッド・ライブラリ・関数を確認する。呼び出し元に子関数の時間も含まれる行は重複する。時間を使ったライブラリや呼び出し経路をきっかけにソースを調べる。
5. 計測後に検証用の窓を閉じ、回収完了を確認し、HUD を元に戻す。比較用にビルド・条件と結果を残す。生のトレースは非公開の作業ディレクトリに保存する。

Wi-Fi ADB を所有する Ubuntu Desktop で、実機を確認してから実行する。`EIDOLON_QUEST_SERIAL` は確認済みの Wi-Fi ADB 対象、`EIDOLON_PROFILE_DIR` は非公開の採取先、`EIDOLON_ANDROID_NDK` はインストール済みの NDK ルートとする。まず窓なしで実行し、窓1枚の場合は出力名を分けて採る。

```bash
scripts/quest-observe --serial "$EIDOLON_QUEST_SERIAL" --scenario power-lifecycle \
  --duration 60 --interval 5 --condition '90 Hz・給電・HUD 非表示・窓なし' \
  --artifact-root "$EIDOLON_PROFILE_DIR"
```

上の集計で CPU に変化があったときは、以下を同じ場面で順に実行する。

```bash
umask 077
mkdir -p "$EIDOLON_PROFILE_DIR"
metavr -d "$EIDOLON_QUEST_SERIAL" perf capture --mode cpu \
  --app io.github.tinmachine.eidolon --duration 30000 \
  --output "$EIDOLON_PROFILE_DIR/idle"

ANDROID_SERIAL="$EIDOLON_QUEST_SERIAL" python3 "$EIDOLON_ANDROID_NDK/simpleperf/app_profiler.py" \
  --disable_adb_root --ndk_path "$EIDOLON_ANDROID_NDK" --app io.github.tinmachine.eidolon \
  -nb -o "$EIDOLON_PROFILE_DIR/idle.perf.data" -r '-e task-clock:u -f 199 -g --duration 15'

"$EIDOLON_ANDROID_NDK/simpleperf/bin/linux/x86_64/simpleperf" report \
  -i "$EIDOLON_PROFILE_DIR/idle.perf.data" --sort comm,dso,symbol --children
```

`task-clock:u` はユーザー空間の CPU サンプル。OS 上の実行・待機は Perfetto で見る。Rust のシンボルが足りないときは同じビルドの未 strip ライブラリを用意し、`app_profiler.py -lib` でシンボルを補う。その場合は `-nb` を外し、非公開の作業ディレクトリで実行する。[Perfetto のスケジューリング解析](https://perfetto.dev/docs/data-sources/cpu-scheduling)・[Meta の Simpleperf 手順](https://developers.meta.com/vr/documentation/spatial-sdk/ts-simpleperf/)

Perfetto の SQL クエリで上位を一覧にできる。収録全体の平均なので、操作を含む計測では UI で対象区間を選んで集計する。

```sql
SELECT t.name AS thread, t.tid,
       ROUND(1.0 * SUM(s.dur) / (trace_end() - trace_start()), 3) AS cpu_cores
FROM sched AS s
JOIN thread AS t USING (utid)
JOIN process AS p USING (upid)
WHERE p.name = 'io.github.tinmachine.eidolon' AND s.dur > 0
GROUP BY s.utid
ORDER BY cpu_cores DESC;
```

- Kotlin・Rust 内の特定処理とスケジューリングを時刻で照合する場合は、その処理に計測 span を追加してから採る。スレッド別 CPU 時間は上の手順で既存 APK から採れる。
- PerfettoはQuest OS権限が許す方法（MVDH提供、Android system tracing、profileable app）で、**短時間・期限付き**取得。UIでスレッドのrunnable/blockedとCPU周波数、Kotlin/JNI/native、render、GC、EGL swapを重ねる。
- Trace APIが見えない場合は、app profileable設定・trace category・native/Java instrumentation・OS制限を区別して報告。特権化/root化によって無理に突破しない。
- CPU hot pathの詳細が必要であれば、debug symbolを別保管してsimpleperfで調査する。symbol uploadおよびAPK配布ライセンス・メモリ挙動は別レビュー。
- GPUの処理時間として`frame_timing.total_us`（CPU draw+swap wall time）を再ラベルしない。GPU timer/fenceやOVRを併記。

### Quest 3 の初回計測

対象は 0.1.111 の開発 APK（`3f7bee9`、main の `1af1cfe` と同じソースツリー）。Wi-Fi ADB、給電、90 Hz、Eidolon にフォーカス、HUD 非表示。窓1枚の条件では Ubuntu のランチャーに掲載されている Dolphin を起動して操作を止めた。CPU の基準値は Perfetto を止めた状態で、約25秒間のスレッド別 `/proc` CPU 時間の差分から取得した。

| 平均 CPU 使用量・描画指標 | 窓なし | Dolphin 1窓・静止 |
| --- | --- | --- |
| Eidolon プロセス全体 | 約0.96コア | 約1.27コア |
| ホスト処理 `eidolon-host` | 0.38コア | 0.47コア |
| Android のメインスレッド | 0.16コア | 0.19コア |
| 窓の状態取得 `eidolon-panel-snapshot` | 0.11コア | 0.12コア |
| Rust の `eidolon-core` 2本の合計 | 0.06コア | 0.16コア |
| トレース区間の平均 FPS / 表示 Hz | 90.52 / 90 | 90.56 / 90 |
| トレース区間の前フレーム再利用 / GPU 時間平均 | 4回 / 0.92 ms | 6回 / 3.03 ms |

30秒指定の Perfetto は両方で一部のチャンクを取りこぼした。保存されていたデータの時刻幅は約27秒で、CPU 上位の順位は `/proc` と一致した。FPS・再利用・GPU の各値は、この時間帯の Meta ランタイム報告を時刻で照合した。Perfetto の影響を避けて CPU の基準値を比較するときは、同条件の `/proc` 計測を使う。短いスパイクや起床回数まで比較するときは、Perfetto の Stats で欠測を確認して採取時間やバッファを調整する。

15秒の Simpleperf では、窓なしで `NativeSessionHost.tick` の配下が全ユーザー空間 CPU サンプルの約40%。その内側には `nativeSnapshot` での全状態の JSON 生成、Android 側の JSON 解析、`linuxConfigured` からの保存設定の結び付け確認が見つかった。別スレッドの `SpatialPanelAdapter.poll` の配下も約12%で、こちらも動いている runtime の全状態を取得していた。呼び出し配下の割合なので、これらを合算して削減量には使わない。

### 計測から見つかった簡素化候補

1. **保存設定の組み合わせを確認するタイミングを絞る。** `linuxConfigured` は保存プロファイルとストレージ記録の結び付けを同じ状態のまま繰り返し計算していた。読み込み・保存・切り替え時に確認した結果を世代と一緒に保持し、待機中の表示更新で使う。実際の接続・起動・保存で使う対象の照合は、その操作の時点で行う。最初はこの変更だけを比較する。[現行の呼び出し](../../../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)・[保存設定の照合](../../../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxDesktopV3ConfigRepository.kt)
2. **毎回必要な窓の状態と詳細診断の粒度を見直す。** ホスト処理は16 ms、パネル側は処理完了後32 msの間隔で、同じ runtime の全状態をそれぞれ取得する。窓の生成・変更・回収を示す世代付きの小さな状態を先に読み、詳細の再取得は状態変化や診断出力に合わせる案を試す。SDK の回収がホスト側の待機中も進むよう、パネルから独立して読めることと、所有者・世代の照合を受入条件にする。[パネル側のポーリング](../../../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt)
3. 変更前後で、窓なし／1窓静止の CPU と FPS・再利用回数を比較し、入力、窓の作成と回収、再接続も通す。削減幅はこの比較で判定する。

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

## 4. P3/P4: 窓数と GPU の調査

- **v3単一アプリの制約がある間に独立アプリ2/4/8/16を合格と宣言しない。** 制限緩和後も、Quest layer上限・アプリpanelとシステム消費・同時可視数・GPU memoryを先に実測する。
- 1セッションでは本文だけの状態と親窓＋ダイアログを今から比較できる。複数セッションは対応済みの数まで段階的に増やす。**全静止／1窓のみ高頻度更新／全窓高頻度更新**をそれぞれ測る。visibleとactiveを必ず分離。
- P0/P1でGPU仮説が支持されたときだけ、OVR Advancedやovrgpuprofilerの有効化、Android GPU Inspectorの**実機互換性check**、GLES timer queries/fencesを検討する。QuestでAGI frame captureが使えるとは限らない。
- 同じ場面でCPU swap time、GPU App Time、Compositor Time、stale/dropped、Host更新遅延を突き合わせる。描画最適化による画質/入力/回収への副作用を確認する。
- **将来の最適化候補**：dirty region、frame coalescing、visibility-aware scheduling。変更単位で前後を比較し、入力・画質・回収への影響を記録する。

## 5. P5: 長時間/熱と復旧

- 固定シナリオの5分（初期）/30分（warm/thermal）/2時間（soak）で、少なくとも開始直後・中盤・終盤の時間帯ごとに分布を比較する。30分だけで2時間passとしない。
- CPU/GPU level、App GPU Time、stale、clockの制限、battery temp、PSS、FD、owned render resources、SSH/Waypipe生存・再接続を時間軸で重ねる。
- 既存のGL executor 1本使い回し/FD保持問題（[GPU FD調査](../../evidence/experiments/gpu-fd-investigation.md)）と比較し、計測機能が新しいGL threadやresource leakを作らないことを確認する。
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
