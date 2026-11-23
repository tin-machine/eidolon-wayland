---
id: android-ime-shared-apps-plan
title: foot・GTK・KDE/Qt で共通に使う日本語入力の計画
type: architecture
design_status: proposed
feature: android-ime
created: "2026-11-23"
last_reviewed: "2026-11-23"
baseline_commit: "0e1f6741484a3132e52e2bc4ac2706143cc553c2"
depends_on: [android-ime-design]
related_sources:
  - native/core/src/compositor/ime.rs
  - android/app/src/main/java/io/github/tinmachine/eidolon/TerminalImeController.kt
---
# foot・GTK・KDE/Qt で共通に使う日本語入力

Quest の物理キーボードで、Termux の foot、Ubuntu の Mousepad（GTK）、Dolphin（KDE/Qt）のどの窓でも、同じ操作で読みを入力し、候補を選び、確定・取消・英数復帰できるようにする。アプリは今のように個別の空間ウィンドウとして扱う。

最初の試作は **方式 2 を Quest の Termux ネイティブ環境で構築する**。Linux の既存の変換操作と辞書を利用し、Eidolon は入力先と候補の配置を担当する。候補を右トリガーで選ぶなど、空間での操作に既存 UI が合わないと分かった時点で方式 3 を検討する。[既存の Android IME / Wayland 計画](android-ime-wayland-plan-20261107.md)の案 A が下表の 1、案 B が 2 に対応する。方式選定は試作後に行う。

## 比較の出発点

ビルド 134 では Stealth Keyboard の「物理キーボード接続時に変換候補ウィンドウを表示しない」をオンにすると、起動済みの foot で 2 回とも「日本語」の確定と英字への復帰を確認した。同じ foot でオフに戻すと 2 回とも変換前にパネルが閉じた。Eidolon 再起動後の新しい foot では、オンでも開始に検査用の補助が必要で、変換後の英字は届かなかった。これらは ADB の HID と Stealth を使った結果。Meta 標準 IME、GTK、Qt の物理キー変換はそれぞれ別に確認する。[Quest 3 のフォーカス調査記録](quest-stealth-focus-20261123.md)

## 五つの実装方式

| 方式 | 変換中に利用者が見るもの | 必要な実装と選ぶ条件 |
| --- | --- | --- |
| 1. Android IME の連携を改善 | Quest で選んだ Stealth が変換し、選択中の窓に未確定文字が出る。候補の別窓は Stealth の設定に従う。 | 既存の編集配送を利用する。ランチャーを閉じた直後の Android のキー配送先と Wayland の入力先をそろえ、開始と英数復帰を直す。現行版との比較に使う。 |
| 2. Fcitx5 を共通 IME にする | Mozc が変換し、Fcitx5 の候補一覧が入力中の窓の近くに出る。 | Eidolon のコンポジタと Fcitx5 を接続する。選択窓の有効化・解除、キーの受け渡し、未処理キーの返送、候補ウィンドウの配置を扱う。既存の候補操作や辞書管理を利用できる。 |
| 3. Eidolon が候補一覧を描く | Mozc が返した候補を Eidolon のパネルに表示し、物理キーや右トリガーで選ぶ。 | Mozc にキーと候補選択を送り、未確定文字・候補・確定結果を受け取る。Eidolon がページ送り、取消、窓切替時の終了まで扱う。文字サイズ、距離、向きを VR 向けに調整したい場合の候補。[Mozc の入出力仕様](https://github.com/google/mozc/blob/master/src/protocol/commands.proto) |
| 4. 全アプリ共通の Android 入力画面を開く | 通常の Android 入力欄で変換し、「この窓へ入力」で確定文を送る。 | 入力開始時の宛先を記憶し、復帰時に同じ窓がまだ有効か確認する。短文や検索語向き。本文を見ながら何度も編集する場合は画面の往復が増える。 |
| 5. ホストの Linux デスクトップごと表示する | Ubuntu のデスクトップ内にアプリと IME の候補が一緒に表示される。 | ホスト側で日本語入力を完結させられる。表示・入力の単位がデスクトップになり、個別の空間配置や Quest 上の Termux アプリの扱いが変わる。デスクトップ単位で使いたい場合の代案。 |

## Fcitx5＋Mozc の入手とビルド

Termux 側で Mozc を動かす案の経緯は [Issue #90](https://github.com/tin-machine/eidolon-wayland/issues/90) にある。

Quest の Termux（Android ARM64、Bionic）に Fcitx5 を置き、同じ ABI の Mozc アドオンと辞書を用意する。Debian や PRoot の導入はこの試作の対象外とする。最初に Quest 上でコンパイルし、メモリ・温度・生成ツールの制約が判明した工程は Ubuntu へ移せる。Ubuntu で作る場合も、Quest に配る実行ファイルと共有ライブラリは Termux 向けにビルドする。

1. Quest に配布版 Fcitx5 とビルド依存を入れる。ソースと辞書の版を固定し、まず CMake 版の Mozc アドオンを個別の作業ディレクトリに作る。この実装は変換エンジンをアドオン内で動かせる。ビルド用データと実行に必要なファイルの容量を分けて記録する。[Fcitx5 Mozc の CMake 版](https://github.com/fcitx-contrib/fcitx5-mozc)
2. 専用の設定と固定のテスト入力で、Quest 上の Fcitx5 がアドオンをロードし、候補選択・確定できることを確認する。配布レシピでは Wayland が無効なので、Eidolon との接続に必要な Fcitx5 のモジュールも同じ版・ABI で構築する。[Termux の Fcitx5 レシピ](https://github.com/termux/termux-packages/blob/4977ed6e93ae288cddba46d15e6ffcc4287bd4b6/x11-packages/fcitx5/build.sh)
3. 下記の順に入力先と候補窓を接続する。IME 接続は Eidolon が起動したプロセスに限る。異常終了時とフォーカスを失った時は変換状態を破棄し、物理キーを直接入力へ戻せるようにする。

再開時は[ネイティブ構築の作業ログ](termux-native-mozc-build-log-20261123.md)で最後に通った段階と次の未確認点を確認する。失敗した試行は、条件・症状・変更点を追記してから再試行する。

## 試作順と判断材料

1. **現行経路を基準にする。** 方式 1 で、起動済みの窓と「Eidolon 再起動 → ランチャー → 新しい窓」の両方について、開始、変換結果、英数復帰を個別に記録する。
2. **Termux ネイティブで変換を確かめる。** Fcitx5 が Mozc を読み込み、`nihongo` から複数の候補を出して「日本語」を選べるかを見る。ここで分かるのはエンジンとパッケージの成立性まで。
3. **Eidolon に接続して方式 2 を試す。** アプリ側は `text-input-v3`、IME 側は `input-method-v2` と Fcitx5 の使用版が要する `virtual-keyboard-v1` を確認する。選択窓のランタイムにキーを一度だけ渡し、未処理キーの二重配送を防ぐ。まず foot で動かし、早い段階で下の GTK・Qt の入力欄に広げる。Qt は版や入力モジュールで経路が変わるので、実際に使った版・経路を記録する。[Fcitx の Wayland・GTK・Qt 対応](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland)
4. **候補表示を装着して比較する。** 候補が本文を隠さないか、窓を動かしても読めるか、物理キーで選べるかを確認する。方式 2 で入力まで成立しても候補の視認性やコントローラー操作が不足する場合は、方式 3 の小さな候補パネルを試す。方式 4・5 は用途に応じて選択する。

## 共通入力の受入れ

| 実機で使う入力欄 | 確定結果の確認方法 |
| --- | --- |
| Termux の foot | 「日本語」が端末に届いたことを確認する。変換確定の Enter でシェルのコマンドが実行されないこと、直接入力に戻すと同じ端末に英字が届くことも見る。 |
| Ubuntu の Mousepad（GTK） | 本文に「日本語」を入力してテストファイルに保存し、開き直して内容を確認する。 |
| Ubuntu の Dolphin（KDE/Qt） | 一時ファイルの名前変更欄で変換し、確定後のファイル名を Ubuntu 側でも確認する。取消の試行では元の名前が残ることを確認する。 |

3 アプリとも同じ開始操作で、読みの表示、候補切替、確定、取消、英数復帰まで試す。さらに 2 窓を同時に開き、変換途中で別の窓を選ぶか元の窓を閉じて、未確定文字や遅れて届いた確定が別窓へ入らないことを確認する。ADB で確認した経路、開始を補助した試行、装着者が物理キーボードで操作した結果は分けて記録する。
