# NX17c: 2枚のfootを使うgroup SDK固定検査

`window-groups`はnonce専用設定の2枚のfootを使い、native group操作と既存SDK窓の接続を検査するdebug専用caseである。
同一Wayland client内の2つのtoplevelであり、独立client数の検査ではない。
通常groupメニュー、物理キーボード、HMDでの見やすさ、本文入力の成功は、このcaseの合格に含めない。

## 起動と所有

導入済みdebug APKとsource identityの一致、接続先の識別を通常のoperator手順で確認してから、
新しい32桁hex nonceを一度だけ渡す。実行中に別Activityを挟む継続操作は不要である。

```sh
nonce=$(openssl rand -hex 16)
adb -s "$EIDOLON_QUEST_SERIAL" shell am start -W \
  -n io.github.tinmachine.eidolon/.MainActivity \
  --es case window-groups --es nonce "$nonce"
```

`BuildHooks`の既存固定case/nonce検査と`SdkCaseEnvironment`を通る。
製品設定を変更せず、private設定で`builtin`テーマを明示選択してから窓を開く。
runtime/sceneと各窓のfull targetを捕捉し、途中のruntime/scene/generation交換はこの初回caseでは失敗とする。
全体180秒と各stageの有限期限を持ち、同条件の自動再試行は行わない。
最終結果は`no_backup/diagnostics/latest.json`の同nonceの`check`へ出す。
通常の`scripts/quest-check`にこの新caseのvalidatorはまだ追加していないため、既存caseのvalidatorで代用しない。

## 判定する内容

1. 2枚ともpanel適用、実SDK binding、初期pose/物理サイズACK、成功draw、4操作ボタン、MODE、Host入力先が揃うまで待つ。
2. nativeのcaptured target/revisionへ`collapse`を一回送る。受付ACKとgroup完了receiptを分離する。
3. アニメーション中のSDK ray hitとUVから実quad中心を復元し、始点・終点と異なる2つの中間位置を記録する。SDK componentの希望poseだけでは合格にしない。
4. 全memberのgeometry ACK後、前面だけHost入力先とbody hitが有効になり、背面のcontrol/colliderが無効になることを確認する。
5. 背面のpresent数とtheme player clockが200ms以上変わらず、同期間に前面clockが進むことを確認する。
6. 固定Shiftの合成DOWNを一回だけnativeへ送り、`next_front`で押下が解放され、epochが更新されることを確認する。本文・物理入力の検査には広げない。
7. `expand`後、保存workspaceと物理サイズが不変で、両窓が元の配置へ戻り、最新producer frameのdrawが再開することを確認する。
8. 停止後、旧runtimeがSTALE、group/SDK操作部/MODE/menu/geometry ACKのownerが0、通常設定が元のdigestへ復帰したことを別々に確認する。

`last_readiness`にはglobal条件と窓別のpanel/frame/pose/physical/controls/MODE/input targetの固定boolを残す。
timeoutでも最後の条件とcompactなstage証拠を保持する。
本文、preedit、候補、任意のログ文字列、full native snapshotはcaseのstage証拠へ保存しない。
中間位置はnonce専用の固定gridから生成する。通常のroom由来配置を使った検査へ無断で広げない。

## 合格範囲と残件

このcheckpointのsourceは実Android/Meta SDKのclasspathで限定型検査する。
実機のgroup caseは未受入れであり、`result=pass`に加え`product_settings_untouched=true`が揃った実行を親operatorが保存して初めて当該範囲を合格とする。
中間2点は実geometryが移動した証拠で、HMDで500msが滑らかに見えたことの代用ではない。

reverse、close/new member、reduced motion、scene/Surface交換、準備中非表示から最初のSurface drawが成立すること、9枚のgroup、通常メニュー操作は次のcaseで確認する。
完全にcoplanarな別窓との重なりは公開global rayの別gateとして残し、短rayの成功へ読み替えない。
