# NX12d: 窓ごとの表示サイズと操作メニュー

2026-11-08。三つの既存ボタンの左へ淡い紫の「…」を追加し、その窓のメニューから
「大きく」「小さく」「戻る」を選べる実装を追加する。テーマのanchor/offset/diameter/gapに従い、
move/focus/closeの既存位置と旧テーマschemaは維持する。正面復帰はviewer poseの実測が必要なので、この区切りには含めない。
実機のSDK・HMD受入れは、この文書のコード実装・host検査とは分けて記録する。

「大きく」は両軸1.1倍、「小さく」はその逆数で、一段階ずつ元へ戻せる。
縦横比、中心pose、logical pixel寸法、producer buffer寸法、装飾canvasのpixel寸法を変えず、
既存のbody Scaleだけで物理寸法を変える。両軸0.25〜3mの範囲外なら操作を無効にし、軸別clampはしない。
新たなAndroidSurface、VirtualDisplay、任意script、利用者指定のnative commandは追加しない。

## 対象と入力の所有

一つの共通operation ownerでMoveとMenu/サイズ操作を排他にする。同時に開くメニューは一枚だけである。
各操作はDOWNで捕捉したRuntime/session/scene/window/generation、theme revision、入力intentとサイズstampへ束縛する。
現在のglobal selected windowを後から引き直して代用しない。
通常pointer/key/reset/focusと操作の最終enqueueはHostの同じ入力直列化境界を使う。
通常管理UIからのlogical/physicalサイズ変更も入力をresetし、古い窓横操作を失効させる。

1. 操作の入口でheld keyとIMEを解除する。専用resetのAccepted ACK、新しいepoch、Android側の解除がそろってから項目を有効にする。
2. 項目UPで固定stepを一回だけ選ぶ。nativeはfulltarget/可視性/XR/theme revisionと、捕捉したsize/size_commandを再検査する。
3. enqueue Accepted、native処理Accepted、同requestのmodel寸法、実SDKのquad/UV確認後のphysical-size ACKを別に待つ。
   寸法でcontrols/MODEが再生成されても、操作ownerはwidgetと別に保持し、新四ボタンの準備まで確認する。
4. 元の窓へのfocusを通常経路で要求し、さらに新しいepoch、同じfulltargetのfocus tokenと配送gateを確認して完了する。
   メニューの「戻る」はサイズを変えず、この入力復帰だけを行う。

外部focus/入力intent、pause、scene/runtime/target/theme変更、期限切れは取消になる。
取消後に別窓を奪う自動focusを行わない。nativeが既に受理した寸法を、古い値へ巻き戻す処理も追加しない。
古いACKや遅いcloseは同じownerだけを解放する。SDK cleanupに失敗したownerをゼロへ読み替えず、直接入力を安全側に止める。
窓横MODEは操作準備・メニュー・サイズ確認・入力復帰・終了待ちを区別する。

## 予算と資源

常時四ボタンにより、九窓のcontrolsは108 entity/36 listenerになる（旧81/27から+27/+9）。
MODE/body/visualまで含めたアプリ周辺entityは135。主UIとlauncherは別である。
一枚のメニューは三つの独立した丸いhit領域と、入力を遮らないlabel quadを持つ。
メニューの増分は10 entity/3 listener、label用object/mesh/material/textureが各一つ。
labelは512×256 RGBA、一texture要求512KiB、CPUはbitmap+direct bufferの固定一slotで1MiB。
再オープンでdirect bufferを増殖させず、native label資源は明示ownerで解放する。
成功したSDK destroy呼出しの所有カウンターと、GPU/SDKの遅延回収を実測した受入れは区別する。

従来の三ボタンで成立したcontrols/move/九枠の受入れは、そのAPK/sourceの結果として保持する。
四ボタン用fixture revisionへ更新し、旧結果を新しい資源数の実機合格へ読み替えない。

## 固定SDK検証の入口

debug case `window-size`（`window-size-v1`）は通常の二footを使う。
実SDK ray→通常listener→Host→nativeを通し、held key解除後のメニュー、拡大、縮小、
サイズを変えない「戻る」、別窓focusによる取消、他窓・pixel寸法の維持を確認する。
原寸へ戻した後は、既存の片側close/古いtarget拒否/残窓の合成key配送/停止回収も確認する。
メニュー・controls・MODE ownerゼロと通常設定のdigest復元を、完了条件へ含める。
メニューclose後の旧rayが旧entityへ当たらないことは、古いcached HitInfoの配送まで検証した意味にはしない。

```sh
nonce=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case window-size --es nonce "$nonce"
adb -s "$EIDOLON_QUEST_SERIAL" exec-out run-as io.github.tinmachine.eidolon \
  cat no_backup/diagnostics/latest.json > window-size-metadata.json
```

開始だけをActivityへ渡す。結果のcase/nonce/revisionに加え、`result=pass`、
`product_settings_untouched=true`、environment failureなしまで待つ。
入力は合成SDK/キーであり、物理キーボード、文字表示、HMDの見やすさは別の受入れである。
本文や部屋画像を保存せず、サイズ用checkpointは対象・寸法・stage・資源だけのcompact metadataとする。

## このcheckpointの検証

サイズcapture、Move、配置の純粋JUnit 23件が成功した。
既存classを先にロードした初回のhost runnerでは旧三ボタンclassとの不整合が三件出たため、
今回コンパイルしたclassをclasspathの先頭に置いて、同じ23件の成功を確認した。
Rustのphysical関連5件とcore型検査、workspace/all-targets clippyのwarningsエラー化が成功した。
独立のread-onlyレビューでは、初期化・SDK close失敗時のowner喪失二件を修正し、限定した再確認で追加の確定問題はなかった。
ViewはControllerへ登録してからnative資源を初期化し、回収が失敗したController/Bindingは次tickの再試行まで保持する。
SDK destroy例外の実機fault injection、Android全体のbuild/lint/package、新四ボタンの実機結果は後続gateである。


## GTK owned resize追加後の統合checkpoint

現mainのReceiptLedgerによる保存順の照合、Xvfbのdisplayfd/GTK実接続それぞれの有限待機、
入力intentの直列化と一回だけのGTK resize交換tokenを保持した基準へ統合した。
通常管理UIのpose/logical/physical size操作と、Move/Menuの開始は同じInputIntentSerial境界で古い検査入力を失効させる。
旧runtimeの退役resetが現在runtimeへ送られないguard、旧trialの遅いcallbackが新trialを失効させないguardも維持する。

四ボタンの九枠caseは `window-slots-v4-four-controls` とし、各窓12control entity/4listenerを要求する。
通常の九枠ready/stopとGTK operatorの開始前・最終回収では、menuのowner/cleanup/entity/listener/object/mesh/material/textureが
すべてゼロであることも確認する。Mappedやpanelの消滅だけを回収完了にしない。
Build169の三ボタン九枚PASSは従来の証拠として残し、四ボタン九枚の実機受入れは別に行う。

統合後の限定検証は純粋JUnit46件、ResizeState10件、command schema12件、workspace/all-targets clippy、
実SDKを使うKotlin19sourceの型検査、GTK operator30件が成功した。型検査中のsource hash不変も確認した。
全Gradle/APK/Quest検証はroot operatorへ渡し、この記録だけでは実機のサイズメニュー/四ボタン九枚を合格にしない。
