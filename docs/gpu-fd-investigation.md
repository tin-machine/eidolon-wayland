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
製品側でGL threadを再利用すれば増加を抑えられる可能性はあるが、thread寿命・停止契約を変えるため、
その設計と反復・長時間試験が別途必要になる。既存のowner回収基準を緩めて合格扱いにはしない。
今回の変更は調査用入口・採取器・記録であり、SDKのFD保持そのものの修正ではない。
