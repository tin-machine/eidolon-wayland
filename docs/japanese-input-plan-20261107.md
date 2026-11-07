# 次期日本語入力: まずfoot内でかな漢字変換する

作成日: 2026-11-07。調査基準: `40692ec` / Build133。**NX05を実装・検証中**。
[実装記録](extensions-nx01-nx08-20261107.md)の固定版ttyskkはAndroidへビルド・導入し、
QuestのTermux UID上の専用PTYで漢字候補・確定・取消・英数復帰の自動検証が成功した。
HMD/物理キーボードとfoot内の見え方は最終確認に残す。
ユーザー要件は「かな漢字変換ができればよい」。US101互換キーボードを継続使用し、Fキーを必須にしない。
全体の着手順は[次フェーズ計画](next-phase-termux-startup-linux-gui-plan-20261107.md#implementation-order)のNX05 / NX09に従う。

## 現状と最初の到達点

現行は物理KeyEventをWaylandのkeycode/keymap/modifierへ送る経路であり、JIS設定はキー配置の切替である。
APK側にAndroid IMEの未確定文字・確定文字をfootへ渡すInputConnection連携はなく、
compositorにWayland text-input/input-methodの公開globalもない。
Termuxのfootが`+ime`でビルドされていることや、日本語fallback font・UTF-8 clipboardがあることだけで、
かな漢字変換が利用可能とは判断しない。[入力の既存設計](phase-5-rust-window-manager.md)と
[導入記録](termux-wayland-tools.md)を参照する。

最初の到達点は**Eidolonに表示したfoot内で、読みの入力→漢字候補→確定・取消→英数復帰ができること**。
候補と現在の変換モードが同じ端末内で見え、端末から普通のシェルへ確定した日本語を渡せる構成を優先する。
既存の英数入力は維持し、日本語用の専用起動から試す。全てのshellを無条件にwrapperで包む設定にはしない。

## 小さく試す方式と費用の分け方

| 方式 | 小規模検証の候補 | 判断 |
| --- | --- | --- |
| Termux内の端末用入力方式 | `uim-fep` + Anthy/SKK、またはPTY wrapper型の`ttyskk`等 | 第一候補。既存キー配送とfootの文字描画を使い、Eidolonの一般IME連携を追加せず試せる可能性 |
| エディター内だけで変換 | nvim等に閉じた入力方式 | 補助候補。そこでの成功をshell・他アプリの日本語入力成功へ広げない |
| Android IME / Linux GUI全般 | composition、候補UI、Wayland text-input/input-method等の連携 | 後続の大きな設計。今回の最小要件の前提にしない |

[uim-fep公式説明](https://github.com/uim/uim/wiki/Uim-Fep)には端末アプリ用の入力、Anthy/SKK等のエンジン、状態行がある。
[ttyskkの上流説明](https://github.com/barewalker/ttyskk/blob/main/README.md)はPTY内のSKK変換と確定文字の子プロセスへの送信を扱う。
後者の対応環境の説明はLinux/macOSであり、Android/Termux対応の証拠ではない。
計画時点では両者とも現Questへの導入成功・Bionic互換性・ユーザー受入れは未確認だった。
NX05のttyskkの導入と専用PTYでの成立は上記の実装記録を参照する。ユーザー受入れはまだ別条件。配布package・依存・license・辞書を先に調べる。
SKKは変換の区切りを利用者が指定する操作方式なので、一般的なローマ字連文節変換と同じとは説明しない。
NX05はttyskkで進める。継続採用とNX09の範囲は実機の使いやすさも確認して判断する。

NX05は一つの端末・一つの変換方式で可否と追加費用を判定する区切り。
大きなTermux移植やGUI用IME基盤が必要ならここで見積りを改訂し、ほかの小さい拡張を先へ進める。
NX09は成立した構成を継続利用できるようにする作業であり、単発probe成功で完了にしない。

## NX05: 最小probe

1. 実TermuxのARM64/Bionic、利用可能package・ビルド条件、辞書の取得元・版・hash・licenseを記録する。
   まずローカル辞書の変換を選び、外部サービスへの入力送信を前提にしない。
2. ユーザーの通常設定をバックアップし、専用設定・専用辞書/学習file・専用起動で一枚のfootに限定する。
   Termux一括更新やsshd/共有foot serverの置換を最小probeへ混ぜない。
3. シェルで固定の非機密な読みから漢字を選び、確定、候補切替、取消、Backspace、英数/日本語切替を試す。
   かなが出るだけで合格にしない。確定のEnterが同時にシェルのコマンド実行を起こさないことも確認する。
4. 候補・モード表示、全角幅、行末折返し、上下左右、Ctrl/Alt/Esc、repeat、resize、終了時の端末復元を確認する。
   必要なsocket/temp pathはTermuxの環境から決め、通常Linuxの`/tmp`等を固定前提にしない。
5. 自動確認の後、HMDと実USキーボードで読み→候補→漢字確定を最後に確認する。
   ADB仮想キー・日本語の貼付け・既存日本語の表示だけでは物理入力の合格にしない。

## NX09: 継続利用と既存機能との整合

- 動作した専用起動を再現可能な手順とし、必要ならランチャーに「日本語ターミナル」を追加する。
  NX08未完成でも手動の専用起動から進められる。通常のfoot起動は引き続き利用可能にする。
- 辞書と学習データは適切な永続先へ置き、read-only共有辞書と利用者の書込み先を分ける。
  再起動や更新で学習を消さず、辞書欠落・書込み不可・破損は分かる表示と英数へ戻す操作を持つ。
- tmux有無、pane移動、nvimの挿入/normal切替と組み合わせる。wrapperとtmuxの順序を記録する。
  終了signal、job control、PTY resize、termios、所有process/FDの回収、窓別workload対応を確認する。
- 二枚間のfocus切替、配置編集、キーボード抜差し、sleep/復帰、切断を試す。
  他窓への誤確定や古い未確定文字の再送を起こさない。変換中は端末側だけに状態がある方式では、
  Eidolonのfocus喪失で自動取消できると想定しない。旧窓へ未確定を保持するか取消するかを実測して案内する。
- かな/英数・候補表示は入力方式自身の確かな状態を利用する。
  APK側の[入力停止理由](next-phase-termux-startup-linux-gui-plan-20261107.md#input-status)と、端末内の変換モードを混同しない。
  連携なしにキー操作から「あ/英数」を推定しない。ユーザーの入力文・候補・辞書内容は通常診断へ記録しない。

GUIゲーム・GTK/Qt・remote Linux全体で使えるIMEは別の到達点。
この最小構成でどこまで入力できたかを、shell / tmux / nvim / 一般GUIごとに記録する。
