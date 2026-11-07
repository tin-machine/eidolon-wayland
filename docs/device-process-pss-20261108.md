# Eidolon自身のPSS

NX14の追加値。Androidの[Debug.getMemoryInfo](https://developer.android.com/reference/android/os/Debug#getMemoryInfo(android.os.Debug.MemoryInfo))で
現在のAPK processだけを取得する。[totalPss](https://developer.android.com/reference/android/os/Debug.MemoryInfo#getTotalPss())の
kBを1024倍してbytesへ変換し、共通の「設定と詳細」にMiBで表示する。
共有分を按分したPSSであり、Termuxの子や端末全体、GPUの全使用量を合算した値とは扱わない。

既存の単一device detail workerを使い、可視時に初回、その後30秒周期で取得する。
描画/UI threadで読み取らず、窓数ごとにreaderを増やさない。
計測失敗はunavailable、初回はpending、90秒超過や時計逆行はstale、Activity停止はstoppedとして値を消す。
停止前のworker結果はlifetime tokenで拒否する。PSSの失敗を熱状態や保存先容量の欠測へ波及させない。

diagnosticsの`process_pss_*`はstatus/bytes/sample time/固定reason/scopeだけを持つ。
process名やmemory mappingの本文は収集・保存しない。
単位・範囲・停止/復帰・期限・workerの失敗分離をhostで検査し、実API値と更新・停止/復帰は次の統合APKで確認する。

## Build154の確認

debug build・lint・APK検査・JUnit 107件成功後、同じhelper版でQuestへ導入した。
製品UIDからPSSを取得でき、異なる二つの採取時刻で更新した。
Homeへ移動すると`stopped/null`、復帰後はそれ以前より新しい時刻の`available`へ戻り、
CPU・熱状態・保存先容量も継続した。表示のHMD可読性と計測負荷の比較は未受入れ。

導入直後は前版のdiagnostics fileが最初のpublishまで残るため、検証側はAPK版の一致を待ってから採取する。
初回は旧版を拒否して終了した記録を保存し、新版writerの待機を追加して再検証した。
保存先は作業rootの`eidolon-ime-implementation-20261107/pss154-observations.json`と`builds/20261108T015521-811286/`。
