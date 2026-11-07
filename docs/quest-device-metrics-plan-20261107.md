# Quest 3の端末情報・負荷メトリクスの調査と次期計画

調査日: 2026-11-07。基準revision: `74555aa`。状態: **計画のみ。製品への実装開始待ち**。
[次フェーズ計画](next-phase-termux-startup-linux-gui-plan-20261107.md)の端末情報に関する補足。
接続されたQuest 3はAndroid API 34。現行のMeta Spatial SDKは0.15.0。
compileSdk 36でビルドできても、端末にAPI 35以降の機能があるとは判断しない。

## 結論と計測対象

バッテリー、端末のメモリー、Eidolon自身のCPU・メモリー、IP・経路、熱状態、空き容量は
Androidの標準APIから取得する設計にできる。端末全体のCPU/GPU使用率は別途検証が必要。
**APIの存在、ADB shellでの取得成功、Eidolonの通常権限での取得成功は別の証拠**として記録する。
今回は公式仕様・使用中SDKの公開クラス・USB ADBでの読み取りを調査した。
製品UIDでのAPI呼び出し、APK更新、設定変更、負荷試験は行っていない。

計測対象は次の三つに分ける。各窓のリングへ端末全体の負荷を混ぜない。

- **Quest全体:** バッテリー、RAM、ネットワーク、熱状態、ストレージ。共通の状態表示の候補。
- **Eidolon:** APKプロセスのCPU・メモリー、描画処理時間、転送量、資源数。
- **ウィンドウの作業:** Termuxのshell・子プロセス等。既存計画のCPU/RSS/Disk I/Oと対応付ける。

## 通常アプリからの取得候補

「取得候補」はAPI条件の確認を指し、このQuest上で製品UIDから全項目を実測済みという意味ではない。

| 項目 | API・取得内容 | 制約・表示上の意味 |
| --- | --- | --- |
| バッテリー残量・充電 | `BatteryManager`、`ACTION_BATTERY_CHANGED`。残量%、充電/放電/満充電、電源接続種別 | 基本表示に採用可能。`level / scale`を正規化する。特権の`BATTERY_STATS`は不要 |
| バッテリー温度・電圧 | battery intentの`temperature`、`voltage` | 単位は0.1°C、mV。CPU/GPUの温度とは別 |
| 電流・残容量・残り時間 | `BATTERY_PROPERTY_CURRENT_NOW/AVERAGE`、`CHARGE_COUNTER`、`ENERGY_COUNTER`、`computeChargeTimeRemaining()`等 | ハードウェア依存の追加項目。電流はμAで正が正味充電、負が放電。未対応値や推定不能を0に置き換えない |
| 端末のRAM | `ActivityManager.getMemoryInfo()`の`totalMem`、`availMem`、`lowMemory`、`threshold` | totalはOSが使えるRAM。availableは回収可能分を含む。`total - available`は使用量の目安であり、全アプリRSSの合計ではない |
| EidolonのCPU | `Process.getElapsedCpuTime()`の差分 / 単調時計の経過時間 | 自プロセスのCPU時間。1コア100%の定義なら複数コアで100%を超える。端末全体の使用率と混同しない |
| Eidolonのメモリー | `Debug.getMemoryInfo()`のPSS等、`getNativeHeapAllocatedSize()`、Java heap | PSS、native heap、Java heapは別指標。GPUや共有画像をすべて足し上げた正確な総量とは限らない |
| 熱状態 | `PowerManager.getCurrentThermalStatus()`とlistener、任意で`getThermalHeadroom()` | none〜shutdownの段階と余裕の推定。CPUの温度そのものではない。headroomは未対応・過頻度でNaNになり得る |
| ストレージ容量 | 自アプリの保存領域に対する`StatFs`のtotal/available bytes | ファイルシステムの容量。Disk I/Oの速度や使用率とは別 |
| IP・経路・DNS | `ConnectivityManager`と`LinkProperties` | IPv4/IPv6、interface、gateway、DNS、MTU。`ACCESS_NETWORK_STATE`の追加が必要 |
| 接続状態 | `NetworkCapabilities`、network callback | Wi-Fi/VPN等、検証済み接続、従量制等。OSの到達性判定だけで任意の接続先の疎通を保証しない |
| Wi-Fi電波・リンク | `WifiInfo`のRSSI、周波数、送受信リンク速度等 | `ACCESS_WIFI_STATE`等、呼出経路の権限を確認する。リンク速度はインターネット実効速度ではない |
| 通信量 | `TrafficStats`の端末合計・自UIDの送受信bytesの差分 | 合計は全interfaceを対象とする。Wi-Fiだけ/インターネットだけの量と表示しない。他UIDの自由な取得はできない |

根拠: [BatteryManager][battery]、[MemoryInfo][memory]、[Process][process]、[Debug][debug]、
[PowerManager][power]、[StatFs][storage]、[ConnectivityManager][connectivity]、
[LinkProperties][links]、[WifiInfo][wifi]、[TrafficStats][traffic]。

電流等の`getIntProperty()`未対応は現行targetでは`Integer.MIN_VALUE`、`getLongProperty()`は
`Long.MIN_VALUE`となる。充電完了時間は推定不能なら-1。値ごとの契約で利用可能性を判定する。
`MemoryInfo.freeMem`はAPI 37であり現行端末には使わない。`availMem`を「完全な未使用RAM」と呼ばない。
API 34の`advertisedMem`は販売上の容量との説明用候補で、OS利用可能量と別に扱う。
headroomの1.0はsevereの目安であり、単純な「余裕100%」に変換しない。

現在の[manifest](../android/app/src/main/AndroidManifest.xml)の権限宣言は`INTERNET`のみ。
IP取得に追加する`ACCESS_NETWORK_STATE`は通常権限であり、危険な権限の実行時ダイアログは不要。
SSID/BSSID等の位置情報に関係するWi-Fi項目は、権限や位置情報設定により伏せられる。
SSID表示のための追加許可を、バッテリー・RAM・IP表示の必須条件にはしない。

## Quest全体のCPU/GPUと描画情報

USB ADBのshellでは`/proc/stat`を読み、二回のCPU時間差分から全体のCPU busy率を計算できた。
しかしAndroid 14の[AOSP SELinux policy][selinux]は一般アプリの`proc_stat`読み取りを制限する。
Eidolon内で`top`等の外部コマンドを起動してもADB shellの権限にはならない。
[HardwarePropertiesManager][hardware]もCPU使用量・CPU/GPU温度・fan情報の取得に
device owner/current VR service等の条件がある。通常のVRアプリをそのVR serviceと見なさない。

開発時には[Meta Quest Developer Hubのmetrics][mqdh]等が候補になる。
開発ツールで値が見えることを、出荷APKで常時取得できる根拠にしない。

[XR_META_performance_metrics][xrmetrics]には次のようなcounter候補がある。

- デバイスのCPU平均・最大コア使用率、各コアの使用率、GPU使用率。
- アプリ/コンポジターのCPU・GPUフレーム時間、コンポジターのdrop数等。

拡張の有無、`xrEnumeratePerformanceMetricsCounterPathsMETA`で列挙されたpath、
各読取結果のvalid flag・単位を確認する。仕様にpathが載っていても端末が提供するとは限らない。
現行Spatial SDKが所有する`XrInstance/XrSession`から安全に利用できる公開経路は未確認。
別のXR sessionを勝手に作る方式は採用しない。対応SDK/APIの調査を独立した小規模検証にする。
仕様は計測に応じたアプリ動作変更を推奨していないため、採用する場合もまず分析・診断表示に限る。
このcounterで画質やリング動作を自動調整する設計にはしない。

使用中の0.15.0 AARを`javap -public`で確認した範囲では、次の公開APIが存在した。
これらの存在だけで端末全体のCPU/GPU使用率を取得できるとは判断しない。

- `Scene.getConfirmedFrameRate()`。
- `SpatialInterface.getFrameTiming()`と`FrameTiming`の予測表示周期・wait frameの時刻/周期。
- `PerfLogger.startTiming()/endTiming()`によるアプリ内の区間計測。
- `setPerformanceLevel()`は性能レベルの設定であり、使用率を読むAPIではない。

表示周期の逆数、アプリの更新頻度、実際の描画完了FPSは区別する。
既存のEidolon描画・転送・資源診断と合わせる場合も、それぞれ計測区間と対象を表示する。

## 「ネットワーク接続先のIP」の区別

| 知りたい対象 | 取得・表示の方針 |
| --- | --- |
| Quest自身のLAN IP | `LinkProperties.linkAddresses`。IPv4とIPv6を別に表示し、複数アドレスを許容する |
| 外への通信を中継するルーター | 対象networkのroutesからdefault gatewayを確認する。IPv4/IPv6の違いを扱う |
| 接続中のWi-Fiアクセスポイント | SSIDは名前、BSSIDはMACアドレス。APの管理IPをそれらから一般的に特定することはできない。gatewayとも同一とは限らない |
| Eidolonが接続するサーバー | 自分が所有するsocket/transportの実際のpeer IP・portを表示する。設定hostnameやDNS候補だけとは区別する |
| インターネットから見たpublic IP | NAT/VPNで変わる。端末のinterface情報だけでは確定できず、必要なら外部確認サービス等を別途設計する |
| 他の全アプリの通信先 | 通常のアプリ権限で一覧取得できる前提にしない |

default networkとWi-Fiが同じとは限らないため、VPNや複数network時に意味を明示する。
製品内でinterface名を`wlan0`へ固定しない。切断・DHCP更新・network交替はcallbackで追う。
通常のEidolon→Termux接続は[利用手順](usage.md)にある`127.0.0.1:8022`で、
QuestのLAN IPやWi-Fi ADBの接続先とは別。Wi-Fiが切れても端末内通信が成立する設計を維持する。

## 今回のUSB実測と再現手順

2026-11-07にUSBでモデルを照合後、API 34、battery、RAM、`/proc/stat`、
Wi-Fiアドレス/経路、`/data`容量、thermal serviceを読み取った。Eidolonプロセスも稼働していた。
USB接続前の既知Wi-Fi ADB宛て再接続は`No route to host`だったが、その後のUSB読取は成功した。
充電表示とUSB給電は確認できたが、ケーブルの給電能力や負荷時の充電収支を検証したわけではない。
具体的な端末識別子・IP・時刻付き実値とコマンド出力はprivate作業記録に保存した。
保存先設定の`quest-device-metrics-20261107/`に`device-snapshot-20261107.json`と
`device-summary-20261107.json`、SDK公開APIの調査記録を置く。

次のコマンドは読み取りだけを行う。`EIDOLON_QUEST_SERIAL`には`adb devices -l`で
接続を確認した対象serialを設定する。読み取れない場合はそのエラーを残し、設定変更へ進まない。

```bash
adb devices -l
adb -s "$EIDOLON_QUEST_SERIAL" shell getprop ro.product.model
adb -s "$EIDOLON_QUEST_SERIAL" shell getprop ro.build.version.sdk
adb -s "$EIDOLON_QUEST_SERIAL" shell dumpsys battery
adb -s "$EIDOLON_QUEST_SERIAL" shell cat /proc/meminfo
adb -s "$EIDOLON_QUEST_SERIAL" shell ip -o -4 addr show wlan0
adb -s "$EIDOLON_QUEST_SERIAL" shell ip -o -6 addr show wlan0
adb -s "$EIDOLON_QUEST_SERIAL" shell ip -4 route show table all
adb -s "$EIDOLON_QUEST_SERIAL" shell df -k /data
adb -s "$EIDOLON_QUEST_SERIAL" shell dumpsys thermalservice
adb -s "$EIDOLON_QUEST_SERIAL" shell cat /proc/stat
sleep 1
adb -s "$EIDOLON_QUEST_SERIAL" shell cat /proc/stat
```

CPUの`cpu`行は先頭8項目（user〜steal）の差分を合計し、guestを重複加算しない。
今回のbusy目安は`100 * (Δtotal - Δidle - Δiowait) / Δtotal`。
約1秒のサンプルであり、持続負荷・Eidolon自身のCPU使用率・性能保証ではない。

## 実装を再開した後の確認順

1. **通常権限で基本値を検証する。** バッテリー、total/available RAM、自プロセスCPU/PSS、
   IP/gateway、thermal status、空き容量を、製品と同じUID・権限で取得する。
   ADBとの同時刻比較で桁・単位・対象を確認し、充電/切断/network交替/復帰を試す。
2. **任意の追加値を検証する。** 電流・残り時間、Wi-Fi詳細、通信量、thermal headroom、
   SDK frame timingの対応状況を確認する。端末全体CPU/GPUはSDK連携方式が成立した場合だけ追加する。
   取得不可の追加値があっても基本値の実装を止めない。
3. **共通の状態表示へまとめる。** 窓別リングとは別に、Questの残量・RAM・接続状態等を表示する案を検討する。
   画面の配置・外観は未決定であり、この調査だけで新しい表示UIを確定しない。

取得頻度の初期案はCPU/通信差分1秒、RAM 5秒、PSS/空き容量30秒、
thermal headroom 10秒以上とし、battery/network/thermal statusは通知を優先する。
PSS等の重い計測を描画threadで行わず、単一のcollectorで窓数に応じた重複計測を避ける。
停止・再接続時にlistener/workerを回収し、経過時間は単調時計で測る。
`valid / unsupported / permission_denied / stale`と観測時刻・単位を区別し、
切断前のIPや古い負荷を現在値として表示しない。個別の不能や例外は他項目へ伝播させない。
計測自体によるCPU・描画遅延・電池消費を、有効/無効の比較で確認する。

[battery]: https://developer.android.com/reference/android/os/BatteryManager
[memory]: https://developer.android.com/reference/android/app/ActivityManager.MemoryInfo
[process]: https://developer.android.com/reference/android/os/Process#getElapsedCpuTime()
[debug]: https://developer.android.com/reference/android/os/Debug
[power]: https://developer.android.com/reference/android/os/PowerManager
[storage]: https://developer.android.com/reference/android/os/StatFs
[connectivity]: https://developer.android.com/reference/android/net/ConnectivityManager
[links]: https://developer.android.com/reference/android/net/LinkProperties
[wifi]: https://developer.android.com/reference/android/net/wifi/WifiInfo
[traffic]: https://developer.android.com/reference/android/net/TrafficStats
[selinux]: https://android.googlesource.com/platform/system/sepolicy/+/refs/tags/android-14.0.0_r1/private/app_neverallows.te
[hardware]: https://developer.android.com/reference/android/os/HardwarePropertiesManager
[mqdh]: https://developers.meta.com/vr/documentation/spatial-sdk/ts-mqdh-logs-metrics/
[xrmetrics]: https://github.com/KhronosGroup/OpenXR-Docs/blob/main/specification/sources/chapters/extensions/meta/meta_performance_metrics.adoc
