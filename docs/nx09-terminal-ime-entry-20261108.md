---
id: android-ime-nx09-terminal-entry
title: NX09 terminal IME entry implementation record
type: historical
feature: android-ime
created: "2026-11-08"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: [android-ime-design, android-ime-implementation-record]
related_sources:
  - android/app/src/test/java/io/github/tinmachine/eidolon/ImeSessionConnectionTest.kt
---
# NX09: 通常ターミナルの IME 入力入口

2026-11-08。通常の foot に対して、メインパネルの「IME入力」または物理キーボードの Ctrl+Space で Android IME の入力経路を開始・終了できる実装。Android が選択した IME を使い、アプリ側で日本語や特定の変換方式を自動選択しない。画面の表示も「IME入力」とし、接続成立をかな漢字変換の成功と同一視しない。

## 対象と開始

通常入口は、すでにフォーカスされている foot だけを対象にする。同じ session/nonce の認証済み helper 起動応答、accepted な terminal launch receipt、一意な app_id marker、Mapped window、適用済みの full SDK target、実描画を照合する。GUI アプリの app_id 申告だけでは許可しない。通常入口で新しい端末を開いたり別窓へ自動的にフォーカスしたりしない。helper の `cleanup_pending` は稼働中も true となる回収責務の印なので、終了開始の判定には使わない。running、EOF/exit/finished なしを確認する。

`NativeSessionHost.beginImeOwner()` は UI thread と `InputIntentSerial` の同じ境界で runtime、session/window/scene/generation、入力 epoch、focus token、input intent を固定する。raw キー配送を停止して `ImeSessionConnection.start()` を呼ぶ。Backend enable 自体が native urgent reset なので、Ctrl+Space の先行 Ctrl down は View/InputConnection を待たず解除する。enqueue の受理と native 完了 ACK は別々に追跡する。

その後、Meta SDK の Presentation Context が届いてから `attachImeEditor(owner, context)` を一度だけ呼ぶ。古い owner、終了済み接続、外部操作で失効した ticket からの attach は拒否する。準備期限は既存の 8 秒で、遅い View や IC の到着によって延長しない。

## 共通 controller と終了

`TerminalImeController` が通常版・debug 版で同じ PanelKeyboardBridge、SDK panel、独自 editor、Android window handoff を管理する。明示操作後の固定 SDK tap は editor を選択するための既存経路であり、文字やキー列を注入しない。1 秒の同 window token の handoff 期間後、実際の Window focus を確認する。外部画面への離脱、pause、XR 非 FOCUSED、scene 終了は接続を閉じる。

通常の OFF は同 owner の reset/disable ACK、新しい epoch、Android View focus/IMM の解放を確認してから、固定した元の full target に一度だけ focus を送る。さらにその ACK と新しい focus token が一致してから直接入力へ戻す。途中で対象が消えたり外部の入力意図が変わったりした場合、別窓へ付け替えない。失敗した配送・focus を自動再送しない。

終了処理中は「直接入力への復帰を確認中」と表示する。native または Android の解放が確認できない場合は入力停止を維持し、接続を停止・再開する必要があることを表示する。SDK bridge/entity の回収に失敗した場合も参照を保持して回収を再試行し、解放済みとは扱わない。通常入口の終了に debug case の強制打切り期限は使用しない。

局所 editor の 4,000 UTF-8 bytes、押下所有 64 件、ACK 32 件、期限、generation/activation/edit_revision/sequence の契約は維持する。IME 本文・周辺文脈・キー列を通常 diagnostics や ACK map へ保存しない。確定文字は既存の Wayland IME transaction、非文字キーは同じ固定フォーカスへの既存経路を通る。

## 通常版と検証版

通常版にも panel 登録、Ctrl+Space、UI ボタン、lifecycle と native `SetImeBackend` を配線する。backend の既定値は無効のまま。通常開始時の対象制限と native の target/epoch/activation/serial 検査を維持する。Linux 固定 probe や debug-fixtures を release へ開放する変更ではない。

`ImeTerminalCase` は debug sourceSet の薄い wrapper とし、固定 nonce、閉じた editor への合成 callback 検査、固定 case の metadata 出力を残す。既存の debug 自動端末準備はこの probe に限り、通常入口では行わない。probe 終了後も最後の配送件数を保持し、未記録を 0 件へ置き換えない。

## 検証と残る受入れ

- 純粋 JVM: 76 件 PASS。既存の editor/bridge/session/復帰/shortcut に加え、認証済み起動との一致、旧 nonce/別 session/scene/generation/GUI/重複 claim の拒否、View 到着前の各準備段階で OFF→古い IC/ACK 拒否を検証した。
- cached Kotlin compiler と実 Meta SDK/Android/Compose 依存による debug 23 source / release 13 source の変更箇所の型検査を実施。これは APK 全体の Gradle/lint/package 検証や実機動作の代用ではない。
- 初回の限定型検査は Compose 依存の不足で失敗し、既存 cache の依存と Compose compiler plugin を追加して再検証した。別 target/cache の複製や実機設定変更は行っていない。
- 保存済みの通常 foot の実機 snapshot を純粋な eligibility 判定へ入力し、受理と世代違いの拒否を確認。初回は上述の cleanup_pending の意味を取り違えて live を誤拒否したため、状態判定と回帰 fixture を修正して再実行した。新 APK の実機成功ではない。
- package 検査は debug `ImeTerminalCase` と固定閉鎖 editor fixture の release 混入も拒否する。
- 通常版 APK の導入、通常 UI/Ctrl+Space の実操作、実 IME のかな漢字変換・候補選択・物理キーボード・HMD は、この checkpoint では未確認。US 配列を前提とし、ユーザー指定どおり function key 検証は要求しない。

次の検証は通常の foot をフォーカスして ON→BOUND→OFF→直接入力復帰、準備中 OFF、外部離脱/対象 close、続けて再 ON の順に行う。その後に実 IME と物理入力を確認する。NX17 group の後方窓・移動/再配置中は、group の final input gate を IME の begin/bind/transaction/nontext 配送にも統合してから有効にする。
