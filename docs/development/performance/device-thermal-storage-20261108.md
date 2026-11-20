# NX14b: OSの熱状態とEidolon保存先の空き容量

既存の「設定と詳細」に、OSから報告されたthermal statusと、Eidolonのapp data directoryを含む
ファイルシステムのavailable/total bytesを追加する。今回の範囲はこの2項目だけで、
PSS・全体CPU・GPU・電流や、温度に応じたアプリの自動制御は追加しない。

## 測定値の意味

thermal statusは[PowerManagerの公開API](https://developer.android.com/reference/android/os/PowerManager#getCurrentThermalStatus())
を使う。取得と変更listenerはAPI 29から利用でき、追加permissionやAPI 35以降の機能には依存しない。
コード0〜6をOS報告として扱い、取得例外・非対応・範囲外の値をNONEへ置き換えない。

[AndroidのThermal API説明](https://developer.android.com/games/optimize/adpf/thermal)
では、未完全対応の端末が実際の状態によらずNONEを返す場合も示されている。
この実装はheadroom推定を行わないため、NONEを「実測で温度が正常」「対応確認済み」と呼ばない。
画面には「OS熱状態 NONE（未対応端末との識別はできません）」と表示する。
これは温度センサーの摂氏値ではない。

| API結果 | データ |
| --- | --- |
| 0〜6を取得 | `available` + `thermal_code`。0も実際の戻り値として保持 |
| API level不足、service無し、UnsupportedOperationException | `unsupported` + null |
| 権限拒否、読取り例外、未知のstatus code | `unavailable` + null + 固定reason |
| 初回待ち / 停止 / 90秒超の古い値 | `pending` / `stopped` / `stale` + null |

空き容量は `StatFs(context.applicationContext.dataDir.absolutePath)` から
[`availableBytes` と `totalBytes`](https://developer.android.com/reference/android/os/StatFs) を読む。
availableはファイルシステム上でアプリが利用可能とされる空き、totalは同じファイルシステムの総容量である。
表示はGiB（2の30乗bytes）へ変換する。available=0は取得成功の「空き0」であり欠測ではない。
負数、total=0、available>totalは不正値として破棄する。

この値はEidolon専用quotaやTermux専用空き、Questの販売時の総容量を示さない。
予約領域を含むfreeBytes、他appの使用量、キャッシュ回収後の割当可能量は取得しない。
実際のapp data pathは診断へ出力しない。

## スレッド・周期・停止

- 既存5秒tickから30秒以上経過したときに取得を要求する。電池通知の連打でStatFsを連続実行しない。
- アプリ全体で最大1本の専用workerを使う。取得、PowerManagerのlistener登録/解除はUI・描画threadで実行しない。
- 最新subscription、1件のpoll要求、最新thermal callbackだけを保持する。古い要求を無制限にqueueへ積まない。
  UIへの更新も最新snapshotへまとめる。listenerは状態表示用であり全変化履歴の記録ではない。
- Activityの停止で公開値をstopped/nullにし、opaqueなlifetime tokenを失効させる。
  実行中のStatFsが後から戻っても旧tokenの結果は公開しない。Activity再作成でも追加workerを起動しない。
- 古いlistenerは同じworkerで解除してから新listenerを登録する。解除に失敗した場合は旧handleを1個だけ保持し、
  新listenerを増やさず、次の開始/要求で解除を再試行する。固定ログ `thermal_listener_remove_failed` を残す。
  その間の旧callbackは失効済みとして破棄する。
- pollingのtimestampはworkerが読取りを始めた時点、listener値はcallbackを処理した時点のmonotonic clockである。
  UIへ届くまでに90秒を超えた値やclock逆行はstaleへ落とす。ハードウェアが測定した正確な時刻を保証しない。
- 既存の電池/RAM/IP/自プロセスCPUは従来どおり収集する。新しい値だけが更新された場合に、他の測定時刻を進めない。

workerがOS呼出しで長時間待たされる場合、別threadを増やして追い越さない。
この間は古い値をstaleにし、OS呼出しが戻った後に停止・解除を処理する。
停止済みという表示はアプリ側の収集/公開停止を意味し、OSのlistener解除完了を偽って保証するものではない。

## 診断と検証

既存device metrics JSONへ以下を追加する。

- `thermal_status/code/sampled_monotonic_ms/reason`、`thermal_listener_status`。
  `thermal_hardware_support` は常に `not_verified`。
- `data_fs_status/available_bytes/total_bytes/sampled_monotonic_ms/reason`。
  `data_fs_scope` は `eidolon_data_dir_filesystem`。

pure JVM testの12件は、NONEと取得不可の区別、容量のゼロ/上限/範囲外、30秒の制限、90秒の欠測、
停止・再開・遅着callback拒否、blocked reader下の1000要求/通知のまとめ処理、
listener解除失敗時の登録数制限、古い通知より新しい明示読取りを優先する順序を検証する。
Androidの実API取得・listener解除・画面表示は統合ビルドと実機で別途確認する。

測定条件の確認は、Eidolonを前面で起動して初回取得、30秒後の更新、Homeへ移動してstopped/null、
復帰してpending→新しい値への移行を観測する。熱負荷を意図的に上げる操作は必要ない。
NONEが返ってもthermal実装の完全対応や高温時の通知を確認できたとは扱わない。

## Build152の実機確認

2026-11-08、debug build・lint・APK検査とJUnit 104件が成功したAPKをQuestへ導入した。
製品UIDでthermal statusは`available / code=0`、listenerは`registered`を取得した。
hardware supportは`not_verified`を維持し、NONEから対応済み・正常温度とは判断していない。

app data filesystemはtotal 110,166,224,896 bytes、available約104,053,125,120 bytesを取得した。
約30秒の異なる2時点で更新し、既存の電池・CPU値も継続して取得できた。
Homeへの遷移でthermal/filesystemとも`stopped/null`、復帰後は以前より新しい観測時刻の値へ戻った。
これは公開値とlifetimeの検証であり、高温時の通知、listener解除失敗の実機再現、HMDでの可読性は未受入れ。

個別値・版・source入力archiveは、作業rootの`eidolon-ime-implementation-20261107/`にある
`details152-observations.json`、`details152-lifecycle.json`、`builds/20261108T011823-774530/`へ保存した。
