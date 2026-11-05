# SDK rayと共通座標変換（I01）

2026-11-05、Quest 3/API34・Spatial SDK 0.15.0で、実quadの四隅と中央のUVを測定した。
左上原点であり、Uは右、Vは下へ増える。これは合成rayと専用のsource Entityによる
SDK API検査で、物理コントローラーの成功とは区別する。
I01は共通変換とSDK校正まで実装済み。実入力のWayland配送・focusはI02/I03以降で接続し、
物理操作の受入を最終段階へ残してin_progressとする。

## 共通変換

`native/model/layout.rs`の`Viewport`をrendererと入力で共有する。
縦横比を保つscale、letterboxの原点、pixel rectangleへの変換、pixel→window logicalを一箇所で定義する。
`native/render/compositor.rs`は同じ型を再公開し、従来のGPU描画の丸め規約を維持する。

`native/model/input.rs`はUV原点を明示して正規化し、余白・非finite値・範囲外を拒否する。
右/下端は半開区間で、外側のhitを隣のwindowの端へclampしない。
`surface_hits`は手前からsurface候補を返し、snapshotに含まれるgeometry/popupのoffsetを一度だけ差し引く。
bufferのscale/transformをsurface logical座標へ二重適用しない。
Waylandのinput region、実focus、event送信はcore ownerのI03で評価する。

host検査は、UV=1/負値/NaN/Infinity、letterbox、pixel rectangle、geometry offset、popup clip/重なり順、
全8buffer transform×scale1/2を含む。`scripts/check-host --suite input`は3test、
layoutを含む全modelは14test。`--suite all`ではinputをmodelの内訳として示し、0testを成功扱いしない。

## SDK境界

`InputAdapter`はSDK callbackから対象tupleをimmutableな文字列として取得し、UI threadへ渡す。
世代変更で古いcaptureを破棄し、遅延handoffもcaptureの同一性を照合する。
通常の入力は記録しない。debug検査が作成した専用source Entityだけを限定採取する。
ButtonBitsのSDK定数からsymbolicなchanged/stateを作り、magic numberを使わない。
現時点ではWayland配送を接続していないため、listenerは入力を消費しない。

`RayCase`は診断quadの四隅(.01/.99)と中央(.5/.5)に対して実SDKの
`Scene.lineSegmentIntersect`を呼び、Entity、UV、`HitInfo.point`、segmentの始点/終点を保存する。
その後に`Scene.onInput`へTriggerR press/upを注入し、10callbackとstopInputを照合する。
source Entityとcaptureを回収し、通常SDK描画が続いていることも確認する。

`synthetic_input=true`、`synthetic_visibility=false`、`physical_input_verified=false`を必須とする。
観察したcallback threadは、UI threadから合成入力を渡した経路の値である。
物理コントローラーからのcallback threadを測ったとは扱わない。

API signatureは固定0.15.0 AARのclass/bytecodeと実機で確認した。
取得できた[公式HitInfo説明（v0.12.0）](https://developers.meta.com/horizon/reference/spatial-sdk/v0.12.0/com_meta_spatial_runtime_hitinfo/)では
pointはworld space、textureCoordinateはUVと説明されている。
0.15.0の同名参照URLは取得できなかったため、古い例の呼出signatureは採用せず、
現物AARの二つのVector3を取るAPIを使う。world pointの意味は実測でも照合した。

## 試行と結果

- Build64はKotlin compileで失敗した。`HitInfo.entity`のnullable型と浮動小数literalの構文を修正し、未完成APKを導入しなかった。
- Build65 `20261105T133505-dc62958e`は四隅のUVと8callbackまで成功したが、
  正確な中央へ垂直に投げるsegmentがnullを返し、45秒でtimeoutした。
  同じ間もRuntimeはReady/FOCUSED/可視、GPUは2,493frameまで進んでおり、
  permissionや描画停止の失敗ではなかった。
- Build66ではray測定と入力注入を分離し、視点側から同じ既知の点を狙う斜めsegmentを使った。
  実際の交差pointも期待world位置と照合し、UVや位置の許容幅は広げていない。
  `20261105T134258-ad71e3d4`と、Runtime再作成後の`20261105T134629-45558250`がともにpassした。
  中央の垂直segmentは両方で依然nullであり、別fieldへ残した。
  quadの交差判定における数値境界が疑われるが、SDK内部の原因は断定しない。

Build66のUVは次の値だった。

| 点 | U | V |
| --- | ---: | ---: |
| 左上 | 0.0100083 | 0.0100035 |
| 右上 | 0.9900013 | 0.0100035 |
| 左下 | 0.0100084 | 0.9899967 |
| 右下 | 0.9900014 | 0.9899966 |
| 中央 | 0.5000048 | 0.5000000 |

world pointの最大差は約0.000010mで、PC gateは0.0001m以内を要求する。
press/upのchanged/state、stop、callback→UI handoff、世代、回収も両runで合格した。

共通Viewportの移設後、Build66で全36stage+patternのGPU readback
`20261105T134602-9d892bff`、foot/ImageReaderの連続出力
`20261105T134609-a7cf18fc`、foot/通常SDKの再生成・停止後復帰
`20261105T134621-2b99e29a`もpassした。
Rust79/Clippy/fmt、Python実APK41、launcher16、debug/release/lint/package検査が成功。

最終APKはBuild66、SHA256 `81be0d0f98f6c2dfca228f700091ac99331fb84cae88520dcb9f762b94d49ae4`、
source digest `2bbb4007133b988ad94422028e68200d96da4628557300d584c9a933e4f38749`。

## 再現

```bash
scripts/check-host --suite input
scripts/build-android 66
adb -s QUEST_SERIAL install -r /absolute/path/to/eidolon-debug.apk
scripts/quest-check --serial QUEST_SERIAL --case ray
```

通常UI/SDKの診断quadが対象で、`--instrumentation`は使わない。
caseとnonceだけを受け取り、任意ray座標や入力本文をADB intentから受け付けない。
生ログ・snapshot・再開メモは作業rootの`eidolon-phase5-implementation/`に保存する。
