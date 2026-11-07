# 次期日本語入力: まずfoot内でかな漢字変換する

作成日: 2026-11-07。当初の調査基準: `40692ec` / Build133。
**現在はユーザー指定により、Termux側のFcitx5と日本語エンジンを再利用する方式を優先する。**
SKK独自の操作を通常の日本語入力として要求しない。以下のttyskkの成功は旧方式のprobe記録として保持する。
[実装記録](extensions-nx01-nx08-20261107.md)の固定版ttyskkはAndroidへビルド・導入し、
QuestのTermux UID上の専用PTYで漢字候補・確定・取消・英数復帰の自動検証が成功した。
HMD/物理キーボードとfoot内の見え方は最終確認に残す。
ユーザー要件は「かな漢字変換ができればよい」。US101互換キーボードを継続使用し、Fキーを必須にしない。
全体の着手順は[次フェーズ計画](next-phase-termux-startup-linux-gui-plan-20261107.md#implementation-order)のNX05 / NX09に従う。

## 更新: TermuxのFcitx5を使う（2026-11-07）

ユーザーはSKKの操作体系を避け、Termux内のFcitxパッケージを再利用したいと希望した。
第一候補はFcitx5＋Mozc。Fcitx5は入力の枠組みであり、かな漢字変換には日本語エンジンが別途必要になる。
変換エンジンはTermux側のprocess/packageとして動かし、EidolonにはWaylandの入力・候補表示の接続を追加する。
既存packageで足りない場合も、Termux向けの追加packageやWaylandを有効にした再ビルドを先に検証する。
直ちにMozcをAPKへ組み込む設計へ切り替えない。

### 確認できたこと

- Termux公式の[`fcitx5/build.sh`](https://github.com/termux/termux-packages/blob/5c3ea65e18dc46c0fd97cd0d6084708bac762cd9/x11-packages/fcitx5/build.sh)
  は5.1.23 revision1を定義しているが、`-DENABLE_WAYLAND=OFF`である。
  この定義のままのpackageを入れるだけでは、現在のnative Wayland footにIMEは接続されない。
- 同revisionのrepository tree全体にMozc/Anthy/kkcのpackage定義は見つからなかった。
  [Mozcの追加要望](https://github.com/termux/termux-packages/issues/29614)はopen。
  公式recipeの有無と実機のapt候補・導入済み状態は区別する。実機照会はWi-Fi ADBがofflineとなり未完了。
- Eidolonの`ProtocolState`にはtext-input/input-method/virtual-keyboardの接続がまだない。
  同梱Smithayには`text_input`、`input_method`、`virtual_keyboard`の実装があるため、これを土台にする。
- Fcitx5の[Wayland実装](https://github.com/fcitx/fcitx5/blob/master/src/frontend/waylandim/waylandimserverv2.cpp)は
  input-method-v2とvirtual-keyboard-v1の両globalが揃ってからIMEを初期化する。
  text-input-v3の公開だけでは完成しない。候補popup・focus・未処理キーの返送も必要。
- GTK/QtのFcitx用環境変数はtoolkit連携用であり、footのWayland IME対応の代用にはしない。
  公式の[Wayland説明](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland)と
  [日本語エンジン一覧](https://fcitx-im.org/wiki/Input_method_engines)を参照した。

### 実装する順序と成立条件

1. Termuxのapt候補と依存・既存設定を確認し、Fcitx5のWayland有効化とMozcのBionic対応を小さく検証する。
   SKK専用の入口・辞書修正の追加は保留し、通常の英字入力を維持する。
2. PC上の専用Wayland client/IME fixtureでtext-input-v3・input-method-v2・virtual-keyboard-v1の経路を検証する。
   未確定・確定・取消・未処理キー返送、押下解除、IME切断を含む。IME権限を一般clientへ無条件に公開しない。
3. TermuxのFcitx5を所有する専用D-Bus/sessionと起動・停止へ組み込む。既存の他用途Fcitxやsession busを置換しない。
   候補popupを親窓に結び、アプリ窓9枚の別枠として計測・回収する。別窓へ未確定文字を送らない。
4. 実footでローマ字→Space変換→候補選択→Enter確定、取消、英数切替、tmux/nvim、resize・再接続を確認する。
   設定と学習を更新・再起動で保持し、辞書不在やIME失敗では理由と英字入力へ戻す操作を示す。
5. 最後にUSキーボードとHMDで受け入れる。一般GUIはNX15、別hostはNX18で別に確認する。

今回の変更でNX09は小さいPTY wrapperの仕上げより大きい作業になる。
Termux移植の成立性を確認しつつ、独立するNX10以降の実装を止めない。

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
旧NX05のprobeはttyskkで進めた。継続採用は取りやめ、現在のNX09は上記のFcitx5優先方針に従う。

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
