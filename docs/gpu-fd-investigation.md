# GPU計測FDの切り分け

## 対象と分かっていること

Quest 3 / Meta Spatial SDK 0.15.0で、Build128の停止20回は成功したが、
切断後の全FDが294→312へ増えた。追加runも293→313。
キーマップの強参照循環は修正済みで、Waypipe/fixture/keymapのFD・mappingは全反復ゼロだった。

残ったFDは`/sys/devices/virtual/kgsl/kgsl/thread/<tid>/active_time`。
44個中41個のTIDが当該processの`/proc/<pid>/task`になく、30秒後も残っていた。
当初は全て終了済みthreadと分類したが、別processのTIDも含み得るため、その分類を訂正する。
以下の独立実験では、自分で作成・joinしたTIDだけを残存判定に使う。
製品側のGL ownerはsurface/contextを破棄し、`eglReleaseThread`も呼んでいる。

同梱SDKの`bolt::analyzer::getProcessGpuStat()`を静的に確認すると、次の流れだった。

1. KGSL thread directoryと`/proc/self/task`を列挙する。
2. 両方に存在するTIDについて、TID→`StatFile`のcacheを引き、無ければ追加する。
3. 計測fileを読み、読取失敗時にはcacheから取り除いてcloseする。
4. 消えたTIDは2へ進まず、既存cache全体から消滅TIDを回収する処理も見当たらない。

このため「読取失敗時の解放」はあっても、「thread消滅時の解放」へ到達しない可能性が高い。
実行時のopen元stackは未採取であり、この静的解析だけで実機の全FD増加を説明したとはしない。
SDK binaryやdisassemblyはrepositoryへ含めない。

## 製品処理を除いた比較実験（Build131）

同じdebug APKに、次の二つの入口を用意した。releaseには含まれない。

| 条件 | 起動するもの | 起動しないもの |
| --- | --- | --- |
| platform | Android instrumentationとEGL14 | SDK、製品native、SSH、Wayland |
| sdk | VRFeatureと空のSDK Scene、同じEGL14処理 | 製品native、製品panel、SSH、Wayland |

各processで、空threadを3回、別threadでEGLを10回、同一threadでEGLを10回実行する。
EGLは16×16のPbufferへ2秒間描画する。各cycleでcurrent解除、surface破棄、context破棄、
`eglReleaseThread`の戻り値を検査する。共有displayへの`eglTerminate`は両条件とも行わない。
threadをjoinした後、30秒待機して残存FDを採取する。

計測はreadlinkだけで行い、GPU計測file自体を開かない。FD番号・TID・thread生存・FD総数を記録する。
`/proc/self/maps`からSDK/製品nativeのload有無だけを記録し、maps本文・画面・入力・秘密情報は保存しない。
空thread、新しいGL thread、再利用したGL threadのTIDに対応するFDを別々に集計する。
SDKのFDを外からcloseしたり、非公開SDK APIを書き換えたりしない。

```bash
scripts/build-android 131
# 生成APKを対象Questへ通常のadb install -rで導入する。
scripts/quest-gpu-fds --serial "$EIDOLON_QUEST_SERIAL"
```

この実験は対象アプリを再起動する。製品のSSH接続・app実行中は変更前に拒否する。
SDK Sceneが追跡・境界案内でFOCUSEDにならなければtimeoutとして記録する。
開発時にGuardianの一時pauseを許可する場合だけ`--pause-boundary`を付ける。
finallyで元値とprox_farへ戻し、製品UIを起動する。実sleep・HMD視認の試験ではない。

保存先は設定済みwork rootの`eidolon-gpu-fds/<run>/`、または`--artifact-root`の指定先。
`platform.json`、`sdk.json`に個々の試料、`result.json`に検査結果と復旧結果を残す。
`status=observed`は実験完了、`acceptance=not_run`は製品の長時間受入れ未実施を意味する。
未完了・版不一致・製品混入・EGL回収失敗・thread条件不一致・待機不足は拒否する。

## 実機結果（2026-11-06）

Quest 3 / Android API 34 / Meta Spatial SDK 0.15.0で二条件とも完走した。
Guardianを一時pauseしてSDKのFOCUSED状態を確保し、終了後に元値へ戻した。
製品nativeは両条件とも未load、SDKはsdk条件のみload。全EGL解放APIが成功した。

| 観測 | SDKなし | 空のSDK Scene |
| --- | --- | --- |
| 空thread 3本に対応する最終FD | 0 | 0 |
| 別GL thread 10本に対応する最終FD | 0 | 10 |
| 同一GL threadで10反復した最終FD | 0 | 1 |
| GPU計測FD全体（開始→終了） | 0→0 | 20→31 |
| process全FD（開始→終了） | 71→75 | 229→248 |

GL threadのjoin後、各段階で30秒以上待ってもSDK条件では作成TIDのFDが残った。
製品のSSH・Wayland・描画ownerなしで再現したため、SDK利用時のGPU計測FD保持を切り分けられた。
新規GL threadごとに1個、再利用threadでは1個という結果は、静的に見たTID別cacheとも整合する。
ただし、runtimeのopen元stackは未採取であり、全FDの差分をこの関数だけで説明したとはしない。
release実行での発生、30秒を超える長期回収、L04の長時間受入れも未検証である。

Build131のdebug/release assemble・lint・JUnit・同梱/入口検査は成功。
実APK指定のPC検査77件もskipなしで成功した。
比較入口はdebug限定。判定器は不完全な証拠を拒否し、製品接続中は停止前に拒否する。
実機終了後は通常UIのReady、SSH未接続、Termux sshd稼働、Guardian元値への復帰を確認した。
HMD視認や物理キーボード操作は実施していない。

## 試行錯誤と再現時の注意

1. Wi-Fi ADBがofflineになった。対象再接続とPC側server再起動では直ちに戻らず、後に接続が復帰した。
2. Build130初回は、起動直後の`adb exec-out`が空stdout/exit 0を返し、JSON解析で中断した。
   結果fileの未作成と同様に期限付きで待つよう修正し、空読取後の正常結果をhost testへ追加した。
3. 次の試行ではSDKなしの比較が完走したが、SDK Activityが2D扱いとなり起動待ちでtimeoutした。
   Build131で検証用Activityへ`singleTask`とMAIN/VR intentを設定した。
   `am start -W`の完了を待つ代わりに、実際のscene ready・FOCUSEDとnonce付き結果を待つ。
   宣言は[Metaの組込手順](https://developers.meta.com/vr/documentation/spatial-sdk/add-spatial-sdk-to-app/)を参照した。
4. Build131の短い起動確認ではFOCUSEDへ到達した後、Guardian案内が前面に出て可視性検査で失敗した。
   そこで許可済みの一時pauseを使い、本比較を完走した。失敗も含めた全runで復旧操作を記録した。

## 対応候補と限界

恒久対応の第一候補は、SDK側で消えたTIDのcacheを回収する修正である。
現SDKの公開設定から、このGPU計測だけを停止できることは確認できていない。
Build131までは調査用入口・採取器・記録の追加であり、SDKのFD保持そのものの修正ではない。

## 製品側のGL thread再利用（Build132以降）

Android processごとにGL executorを1本保持し、runtimeごとに一つの描画jobを貸し出す。
停止時はGL資源・NativeWindow・queue・画像・theme・runtime参照を全回収してから完了を通知する。
idle中はchannel待機だけで、旧runtimeを保持しない。共用threadの存在とjob数を別々に診断へ出す。
GPU待ち中に新しいjobを受け付けず、panicやstall後は代替threadを作らない。
詳細な停止契約は[Phase 6設計](design/phase-6.md#l01)に記す。

hostでは12回の同一TID再利用と参照解放、GPU待ち中の二重起動拒否、panic後の再生成拒否、
stall判定後にGPUが戻っても故障状態を維持することを検査した。描画20件を含む全host suiteが成功した。

Build132の実機20回では、GL ownerのTIDが全て同一で、GPU計測FDは33個のまま増えなかった。
切断は最大350 ms、runtime停止は最大212 msで、所有資源とSDK Entityの回収も確認した。
一方、切断直後の全FDは初期化2回を除いて302〜305。追加した幅2以下の基準には届かず、総合結果はfailedとした。
この幅だけで別の漏れがあるとは断定しない。

Build133では切断直後の試料を残し、一律1秒待った同じ切断状態も採取する。
FD総数と分類は同じ一覧から数え、GPU計測・socket・pipe・anon_inode・memfd・device・file・読取前消滅を区別する。
最小値の時点を選ばず、本来の切断所要時間もそのまま記録する。幅2の基準は維持する。
この短い反復試験は、release実測・L04の2時間試験・HMD/物理入力の受入れを代替しない。

Build133の20回は同じ基準でpass。GL TIDは全回同一、GPU計測FDは33個で一定だった。
初期化後の全FDは304〜306、変動した分類は`anon_inode:sync_file`の58〜60だけで、他の分類は一定だった。
切断後の待機は1001〜1034 ms、最大切断348 ms・runtime停止224 ms。
正常停止時のGL job/所有資源ゼロと通常SDK UIへの復帰も確認した。
thread再利用により今回観測していた反復ごとのGPU FD増加は止まったが、SDKのcache自体を直した結果ではない。

再開後の初回試験はTermux/sshd未起動によりSSH_CONNECT_FAILEDで止まった。
既存serviceを起動し固定鍵で認証できたことを確認してから、同じAPKで再試行した。
また、失敗後の保存済みdiagnosticが新processと一致しない場合は、製品接続中でないと断定せず拒否した。
通常UIを前面にして現PIDの診断が更新された後、接続数ゼロを再確認している。
