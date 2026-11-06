# GPU計測FDの切り分け

## 対象と分かっていること

Quest 3 / Meta Spatial SDK 0.15.0で、Build128の停止20回は成功したが、
切断後の全FDが294→312へ増えた。追加runも293→313。
キーマップの強参照循環は修正済みで、Waypipe/fixture/keymapのFD・mappingは全反復ゼロだった。

残ったFDは`/sys/devices/virtual/kgsl/kgsl/thread/<tid>/active_time`。
44個中41個が終了済みthreadのもので、30秒後も残っていた。
製品側のGL ownerはsurface/contextを破棄し、`eglReleaseThread`も呼んでいる。

同梱SDKの`bolt::analyzer::getProcessGpuStat()`を静的に確認すると、次の流れだった。

1. KGSL thread directoryと`/proc/self/task`を列挙する。
2. 両方に存在するTIDについて、TID→`StatFile`のcacheを引き、無ければ追加する。
3. 計測fileを読み、読取失敗時にはcacheから取り除いてcloseする。
4. 消えたTIDは2へ進まず、既存cache全体から消滅TIDを回収する処理も見当たらない。

このため「読取失敗時の解放」はあっても、「thread消滅時の解放」へ到達しない可能性が高い。
実行時のopen元stackは未採取であり、この静的解析だけで実機の全FD増加を説明したとはしない。
SDK binaryやdisassemblyはrepositoryへ含めない。

## 製品処理を除いた比較実験（Build130）

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
scripts/build-android 130
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

## 検証状況

Build130のdebug/release assemble・lint・JUnit・同梱/入口検査は成功。
判定器では、不完全な証拠を拒否し、接続中の製品をforce-stopしないことを確認した。
Wi-Fi ADBが途中で切れたため、現時点ではBuild130の実機導入と二条件の比較は未実施。
静的に判明したcache回収の問題と、実機比較の結果は分けて記録する。

次はplatformとsdkの結果を比較し、SDKありでだけ作成TIDのFDが残るかを確認する。
同じthreadを再利用すると残存が一個に留まるかも確認する。
製品側で恒久的に回避する変更やSDK更新は、この結果と所有権の維持を確認してから判断する。
