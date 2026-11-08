# NX09: 通常 IME の初期 owner 経路を確認する

2026-11-08。既存の `ime-terminal` は先に検証 panel を作ってから ON にする。通常 UI/Ctrl+Space は、先に入力先の owner を取得して raw 入力を止め、後から panel/View が届く。この違いを確認するため、debug 限定の `ImeProductCommandReceiver` を追加する。

## 固定操作

DUMP 権限、明示 component/action、extra が `case` と `nonce` だけであることを検査する。case は `ime-product-toggle` 一つ。Activity を起動せず UI thread で **`TerminalImeController.toggle()` を一回だけ**呼ぶ。現在の通常 controller が無ければ ON、あれば OFF として結果に `operation` を記録する。

通常の foot がすでにフォーカスされていることが前提。通常の verified foot/fulltarget/epoch/intent gate を使い、新しい端末を開く、別の窓を選ぶ、文字を注入する、IME の言語を選ぶ処理はない。別の固定 SDK case や debug IME probe と並行して開始しない。正常 UI の操作や外部離脱で owner が変わったら、確認対象を付け替えず失敗とする。

nonce は 32 桁小文字 hex。1 process につき 64 操作を上限として消費済み nonce を保持する。満杯になっても古い nonce を忘れて再実行することはせず、新規操作を拒否する。ON と OFF はそれぞれ新しい nonce を使う。

```bash
adb -s "$EIDOLON_QUEST_SERIAL" shell am broadcast \
  -n io.github.tinmachine.eidolon/.ImeProductCommandReceiver \
  -a io.github.tinmachine.eidolon.IME_PRODUCT_COMMAND \
  --es case ime-product-toggle --es nonce "$EIDOLON_IME_PRODUCT_NONCE"

adb -s "$EIDOLON_QUEST_SERIAL" exec-out run-as io.github.tinmachine.eidolon \
  cat no_backup/diagnostics/latest.json | \
  jq --arg nonce "$EIDOLON_IME_PRODUCT_NONCE" '.check | select(.case=="ime-product-toggle" and .nonce==$nonce)'
```

同 source の debug APK、UID/version/source digest は既存の導入確認で照合する。release manifest には receiver を登録せず、package 検査も receiver class の混入を拒否する。

## 結果の意味

最大 15 秒、180 polls まで metadata を観測する。run ticket、runtime、session/window/scene/generation を最初の通常 owner に固定する。次の状態を別々に確認する。

- ON: 直後の `owner_before_view=true` を記録し、同 run の native phase BOUND、View/Window focus、IC active、native raw block を確認して `observed / normal_bound` とする。
- OFF: 同 run が終了し、native CLOSED/raw block 解除、Android 解放、controller 終端、`raw_return=COMPLETE`、元の fulltarget への直接入力 focus が成立して `observed / normal_direct_restored` とする。
- native 回収、Android 解放、controller 終端、raw 復帰は別の bool。別 owner の値を回収の証拠へ流用しない。終了済みの最後の report も、返却時に現在 runtime・captured input intent/target・raw focus を再照合する。古い COMPLETE の保存値だけでは現在の復帰を合格にしない。
- `running` は確認中、`timeout` は期限内に証拠が揃わなかった状態。`fail` は owner 変更・復帰取消など、`rejected` は通常入口が受理しなかった状態。timeout 後に自動で再 toggle しない。

正常な OFF に伴う `connection_failure='closed'` は、配送・raw 復帰の失敗と同一視しない。一方、`cleanup_unconfirmed`、raw return FAILED/CANCELLED、新しい run、期限ちょうどの遅い成功は確認済みにしない。止まっている owner を勝手に再開したり、別窓へ focus を戻したりしない。

本文・キー列・focus token は記録しない。保持する最後の通常 run は metadata 一件だけで、通常 release UI はこの観測経路を使わない。既存 `ime-terminal` の厳密 JSON keys は維持し、operator の既知 token に `editor_attach_failed`、`SDK_CLEANUP`、`sdk_cleanup_pending` を補う。

`actual_ime_verified`、`physical_shortcut_verified`、`hmd_verified` は false のまま。ここで成功しても、実かな漢字変換、候補選択、物理 Ctrl+Space の受入れにはしない。

## PC 検証

判定モデル 9 fixtures、既存 operator schema 7 fixtures が PASS。保存済み Build174 の共通 IME ON/OFF metadata も strict schema で再読込できることを確認した。実 SDK Kotlin の型検査、manifest の DUMP/明示登録、release class 除外を別々に確認する。この通常初期 owner 経路の実機動作は次 APK の確認事項であり、共通 debug harness の既存成功から推測しない。
