# SDK SurfaceとGL所有権の実測

対象は製品APKのMeta Spatial SDK 0.15.0 / Quest 3 / Android API 34。独立probeの結果と製品受入を区別する。HMD・物理入力はユーザー指定により最後にまとめる。

## 所有権

Activityごとに一つの`VideoSurfacePanelRegistration`を持つ。`SpatialPanelAdapter`がEntity / Java Surface / input listenerを保持し、Application scopeのhostとRust registryはこれらを保持しない。現在は固定session/window一枚で、接続・WindowManagerによるID発行は後続taskで置き換える。

callbackの元thread IDを記録してMain Looperへ配送する。Entityと全tuple `(session, window, scene, generation)`を照合する。未知Entity・旧bindingの通知は捨て、作成中の保留は一件・5秒上限。公開APIにないSurface破棄callback・registration解除を仮定しない。

JNIだけが`ANativeWindow`の追加参照を取得し、enqueue拒否時は回収する。Java local ref/JNI envを保存せず、SDK所有Java Surfaceへ`release()`しない。同じSDK Scene内で行う自前破棄の順序は以下。

1. 全tuple付き`surface_lost`をenqueue。
2. GL ownerが描画停止→current解除→自分のEGLSurface/context→追加NativeWindow参照を回収。
3. nativeが`surface_detached`を返す。snapshotにも最新64件のackを保持。
4. Kotlinが同tupleのackを確認しlistener解除→Entity.destroy。
5. 次世代を生成。再生成中の連打は抑止。

SDKと共有し得るEGLDisplayへ`eglTerminate`しない。EGLは専用threadで完結し、共有lock中に呼ばない。停止はqueue外のflagで受け付け、GL owner回収前にはStopped/Destroy成功を返さない。2秒超のGL call待ちは`RENDER_STALLED`。別threadからGPU所有物を破棄しない。

## 製品Build 12 / 14で観測

元callbackもSDK操作も今回の試験ではmain thread。順序は`entity_created → surface_callback → attach → panel_setup → listener_added`。再生成は`recreate → detach要求 → surface_detached → listener_removed → entity_destroyed → 次世代entity_created`。将来も同じthread・順序になるというSDK保証とはしない。

Build 12/14ともsurface 10回、pattern、surface-cycle 20回、runtime 10回、helper 10echoが成功。1024×768、四隅のRGBA readback、frame更新を確認。Qualcomm Adreno 740 / OpenGL ES 3.2。終了時はEntity一枚・listener一件・pendingゼロ。旧世代detachと同tupleの重複attachは現targetを壊さなかった。

Build 14の20回再生成ではFD数288で一定、native heapは45,449,448→46,542,168 bytes。SDK/allocatorの増加を含む短時間測定で、長期リークなしとは判定しない。各cycleの値とcallback traceはprivate artifactの`latest.json`へ保存。

ADB `KEYCODE_HOME`で実際に離れたとき、非表示・pause・frame数不変を確認。`am start`でActivity/XR可視性と描画が復帰した。controller Menu操作や非装着sleepの代用にはしない。

hostは20世代、stale detach、duplicate、pause、context/Surface loss、driver待ちを検査。注入したdriver待ち中もsnapshot/停止要求が100ms未満で戻り、待ちを解除するまでowner終了を報告しなかった。実機でdriver stallを起こした結果ではない。

## 残る確認

- HMDの色・上下方向・動く矩形・現実背景。
- controller Menu、実sleep/wake、SDK主導のSurface消失。
- ray callback / UV方向。現在のlistenerは所有権確認用で入力処理はI01以降。
- 外部画像の旧世代frame拒否。現在のpatternはownerが現targetだけに生成し、画像snapshot投入はW02以降。

R01/R02/R03は自動部分の成功を記録し、上記まで完了扱いにしない。独立した後続実装は進める。

## 再現

```bash
scripts/check-host --suite all
scripts/build-android "$EIDOLON_VERSION_CODE"
scripts/quest-smoke --serial "$EIDOLON_QUEST_SERIAL" --apk "$EIDOLON_APK" --require-focus
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case surface
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case pattern
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case surface-cycle
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case runtime
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case helper
```

runnerは導入APK/current source digest/build_info/case/nonce/frame/回収条件/MainActivity復帰を照合する。過去の成功やswap成功をHMD視認と混同しない。

最終Build17でも全5ケースがpass。queue満杯の非表示pauseとGL_CONTEXT_LOST分類のhost検査を含む。FD287〜288、20再生成のheap45,450,392→46,601,672 bytes。描画中製品runtimeの停止・回収8ms。Build17のsource/APK hashとrun名は[進捗](../progress.md)を参照する。

## 連続Activity再生成で見つかったSDK所有物の寿命

Build95の通常layoutの直後にtwo-windowsを実行すると、後者の実Activity再生成で停止した。
`20261105T232228-ee330bb6`のlogでは、旧SceneのpollがSDK DataModel失効後にEntity.destroyを繰り返し、
`DataModel.nativeDeleteEntity: Invalid pointer`とadapter例外が発生していた。
再生成時にはEGLのalready-connected/BAD_ALLOCも記録された。
単独のBuild94 two-windowsは成功していたため、連続実行の結果を独立に残す。

nativeのdetach ACKまでSDK Entityを保持する順序は、Scene自体の終了をまたげない。
Build96では`onSpatialShutdown`内でlistener/input/Entityを一度だけ破棄し、SDK DataModelが有効な間に返す。
それに先立ちnative detachをenqueueする。ACK待ちではSDK objectへ触れず、
native ownerが自分のEGLと追加ANativeWindow参照を回収するまでbinding情報だけを保持する。
SDKが所有するJava Surfaceはreleaseしない。Main LooperでGLの完了待ちは行わない。
同じScene内の通常Surface再生成は、引き続きdetach ACK後にEntityを破棄する。
旧adapterのmetricsで新Sceneの観測値を上書きしないようにもした。

CLIはSDK adapter例外を検出した時点で失敗を保存し、通常UI復帰の成功扱いや長いtimeoutまでの見逃しを防ぐ。
修正後はlayout→two-windowsを含む同じ連続SDK suiteで確認する。

Build97ではlayout→two-windowsの両方で実Activity再生成と製品UI復帰まで進み、旧Sceneの例外は出なかった。
two-windowsのPC描画待ちの不足は別件として記録した。
また、配置UIを左へ移した後の診断パネルは、左端が手前のUIと重なる位置に残っていた。
通常ray検査で期待するEntityと異なる交差を検出したため、Build98では診断パネルを正面x=0へ移した。
実Linux windowの位置を動かしたり、重なったhitを合格にしたりせず、SDK/PC双方の既知geometryを合わせた。
