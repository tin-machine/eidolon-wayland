---
id: android-ime-quest-stealth-focus-20261123
title: Quest 3 で物理キーボードの IME 入力が消える場合の調査記録
type: historical
feature: android-ime
created: "2026-11-23"
last_reviewed: "2026-11-23"
baseline_commit: "0e1f6741484a3132e52e2bc4ac2706143cc553c2"
depends_on: [android-ime-nx09-terminal-entry]
related_sources:
  - android/app/src/main/java/io/github/tinmachine/eidolon/TerminalImeController.kt
  - android/app/src/main/java/io/github/tinmachine/eidolon/ImeEditorView.kt
---
# Shift＋Space のあと IME 入力が消える場合

Quest 3 の Eidolon、Stealth Keyboard、Termux の foot で調べた。Shift＋Space は Eidolon の入力先を切り替える。Ctrl＋Space は Stealth の入力モードを切り替える。以下の時刻は端末の現地時刻。テストには専用の空の foot と固定語だけを使った。

## 以前の成功と今回の失敗

| ビルド・条件 | 観測 | 判断 |
| --- | --- | --- |
| 132–133、IME に接続済み | Stealth の辞書には「にほんご」→「日本語」が出たが foot に確定文字が届かなかった。Android は同じ入力欄に二度接続を要求し、採用した接続と Eidolon が受け付ける世代がずれていた。 | 開始後の編集通知が失われる別の問題。[PR #89](https://github.com/tin-machine/eidolon-wayland/pull/89) に記録した。 |
| 134、同じ HID を接続したまま foot 本文を選択 | Shift＋Space、Stealth の日本語モードで nihongo → Space → Enter。スクリーンショットに確定前の「日本語」が写り、foot の端末に「日本語」、直接入力に戻したあとに英字が届いた。装着者も通常版で変換を確認した。 | Android が採用した接続を使い、入力完了までフォーカスを保った試行は成功した。同じビルドでも開始時にフォーカスを失う試行があった。 |
| 134、11 月 23 日 17:55:32 | Android 入力欄の view/window focus と接続が一度そろった。約 0.1 秒後に window focus が外れ、Quest の FocusPlaceholderActivity と XR の一時離脱を観測。約 0.5 秒でパネルが閉じた。入力イベントは 0 件で、開始履歴の IME は Stealth。 | 今回観測した失敗は変換より前の段階。システム画面が現れた理由、当時どの窓を最後に指したかは確定できていない。 |
| 試作 135、開始時のフォーカス喪失後に同じ foot を再選択 | 前の入力接続を閉じてパネルを残し、同じ窓の新しい入力所有者を作れた。空間パネルにフォーカスを渡すと再び Quest がフォーカスを取得した。 | パネルを残す変更だけでは連続して入力できなかった。 |
| 試作 136、Eidolon 本体の Android ウィンドウが選ばれた場合 | エディタの view/window focus が両方 true でも Android 入力接続が開かず、準備期限後に終了した。実機の本体ウィンドウに ALT_FOCUSABLE_IM が設定されていた。 | Android のウィンドウフォーカス、IME の接続先、Wayland の入力先を別々に確認する必要がある。 |

## 再現と判定

1. アプリのビルド、Eidolon のプロセスの再起動有無、foot を起動した時点、キーボードの接続時点、直前に選んだ場所を残す。Quest の既定 IME と現在の入力接続を読み取る。
2. foot 本文、ランチャーや検索欄、再起動直後、すでに起動済みの foot を分けて試す。各試行で Shift＋Space を押し、XR の状態、Android のフォーカスと接続、Eidolon の対象窓を確認する。
3. 実際の Stealth に HID キーで nihongo → Space → Enter を送り、foot の疑似端末に「日本語」の UTF-8 が届いたことを確認する。直接入力に戻し固定の英字も確認する。ボタンや検査用の入力補助を使った試行は操作経路を併記する。
4. 開始中断の試験では未確定文字を再送せず、再選択前や別窓に文字を配送しないことも調べる。テスト用の入力補助付き APK と通常 APK、ADB での観測と装着者による確認を区別する。

読み取りには次を使う。端末 ID、入力本文、部屋が写ったスクリーンショットは共有記録に含めない。

```sh
adb -s "$QUEST_ADB_SERIAL" shell settings get secure default_input_method
adb -s "$QUEST_ADB_SERIAL" shell settings get secure show_ime_with_hard_keyboard
adb -s "$QUEST_ADB_SERIAL" shell dumpsys window windows
adb -s "$QUEST_ADB_SERIAL" shell dumpsys input_method
adb -s "$QUEST_ADB_SERIAL" logcat -d -s EidolonIme:I
```

## 追加の比較

- 通常版 134 の以前の装着時ログでは 17:29:38 に入力接続が成立し、17:29:44 までに Wayland の編集 10 件がすべて適用された。別の 17:23 の試行では Android が選択していた IME は Meta 標準だった。今回の 17:55 の失敗時は Stealth だったので、同じ「変換できない」症状でも調べる箇所が異なる。
- 試作 137 では本体ウィンドウの IME 除外フラグを入力中だけ解除した。Android 接続は成立したが、複数回の再選択で Stealth が自分の表示領域の寸法不一致（780×355、期待値 850×425）を記録し、その数十ミリ秒後に Quest が FocusPlaceholderActivity を開いた。XR が VISIBLE になった。これは時系列の一致であり、寸法が変わった原因は未確定。本体ウィンドウへ入力欄を移す変更は、この試験では回避策にならなかった。

### 起動順とフォーカスを変えた試験（137）

| ADB で再現した条件 | 結果 |
| --- | --- |
| HID キーボードを接続したまま Eidolon プロセスを終了・起動。foot 起動前、本体ウィンドウにフォーカス | Shift＋Space で IME セッションの開始なし。 |
| 同じプロセスでランチャー検索欄を選択。foot 起動前 | Shift＋Space を押しても foot 用の IME セッションの開始なし。 |
| そのまま foot を新規起動し、本文を選択。Android のフォーカスは Eidolon の空間パネル | HID の最初のショートカットで開始できず、検査用の通常切替呼び出しを使用。再選択しても入力待ちへ戻った。 |
| 同じ foot を残してもう一度本文を選択。Android のフォーカスは本体ウィンドウ | 物理 Shift＋Space で開始したが、その後再選択しても入力待ちへ戻った。上の 2 例とも日本語は未確認。 |
| 先に foot を起動し、その後 HID を接続。開始時のフォーカス喪失を検査用に一度発生させた | 待機画面を保持。同じ窓を選び直して新しい接続を作れたが、その後の実際の XR フォーカス喪失も繰り返した。 |

Stealth の設定画面で、当該端末の「物理キーボード接続時に変換候補ウィンドウを表示しない」はオフ、「未入力時に定型文を常時表示」はオンだった。この設定は Android の「物理キーボード使用時の画面キーボード表示」とは独立している。[Stealth Keyboard 公式マニュアル](https://zutch.sakura.ne.jp/StealthKeyboard_manual.html#hardware-keyboard)
