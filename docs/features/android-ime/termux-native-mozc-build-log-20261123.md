---
id: android-ime-termux-native-mozc-log
title: Quest の Termux ネイティブ Fcitx5＋Mozc 構築記録
type: historical
feature: android-ime
created: "2026-11-23"
last_reviewed: "2026-11-23"
baseline_commit: "4a7f4702e44cdc3f473f79e0f7096eb23b72a58a"
depends_on: [android-ime-shared-apps-plan]
---
# Quest の Termux ネイティブ Fcitx5＋Mozc 構築記録

[共通 IME 計画](japanese-input-shared-apps-plan-20261123.md)の試行記録。固定語は `nihongo` →「日本語」。各行はその段階で観測できた結果を示す。

| 日付・環境 | 試したこと | 観測と次の判断 |
| --- | --- | --- |
| 2026-11-23 Quest 3、Android API 34、Termux aarch64 | ビルド前の依存を調査 | clang 21、Python、Wayland scanner、D-Bus があり、空きは約 96 GiB。Fcitx5 と CMake は未導入。 |
| 同日 Quest、依存解決 | `wayland-protocols` を指定して apt の模擬インストール | パッケージ名が見つからず失敗。Termux のパッケージ一覧で `libwayland-protocols` を確認。 |
| 同日 Quest、依存導入 | Termux 配布の Fcitx5、CMake、Ninja、ECM を導入 | Fcitx5 5.1.23-1 と CMake 4.5.1 のセットアップ完了。PRoot・Debian は導入していない。 |
| 同日 Ubuntu、ソース選定 | Fcitx5 Mozc の CMake 版の CI とサブモジュールを確認 | アドオンと生成済み辞書を用意して Quest でビルドする経路がある。CMake 版 `d55a0517e127c24def4d498d2428c39f956525ae`、Mozc `1527a4cd1217cec4611cd4bc8d46f8c66ea79e6a`。 |
| 同日 Quest、依存導入の再試行 | `gettext` と `libwayland-protocols` を指定 | gettext 1.0 とプロトコル 1.49 を導入。 |
| 同日 Quest、Mozc 構成 | 上流 CI の protobuf パッチと配布辞書を使い CMake を実行 | Clang 21.1.8 と同梱 protobuf 34.1.0 で生成まで成功。辞書ヘッダー（48,692,305 バイト）の SHA-256 は `f918272cdd0ba0d0ce850bfe18588994ea4eecf889e9ef387a48f2014c960f61`。 |
| 同日 Quest、Wayland 構成 | Fcitx5 5.1.23 に Termux のパッチを適用し、Wayland を有効にして CMake を実行 | システムの Yoga と同梱 Plasma XML を使用して生成成功。上流の `waylandimserverv2.cpp` で、input-method-v2 と virtual-keyboard-v1 の両方が初期化条件になっていることも確認。 |
| 同日 Quest、検証用 compositor | 専用ディレクトリの深いパスを `XDG_RUNTIME_DIR` にして Sway を headless 起動 | IPC ソケット名が Unix ソケットの長さ制限を超え、Sway が終了。Termux の `$TMPDIR` 直下に短い専用ディレクトリを作ると起動した。 |
| 同日 Quest、検証用キー配送 | wtype で専用の foot に英字を送る | wtype のキーマップ生成が固定パス `/tmp` で失敗。Termux の一時ディレクトリを使う検査用ビルドで再試行し、foot の端末から `eidolon` と改行を読み戻せた。Eidolon・Mozc を介さない比較用の結果。 |
| 同日 ソース確認、Termux 対応 | Mozc の Android 用のプロファイル決定箇所と CMake の入力を確認 | Java からディレクトリを渡す Android 版の経路は、このプロセスでは空のパスを返す。上流にある XDG の処理を使い、Android の OS 情報取得に必要なソースをビルド対象へ加え、生成するバージョンの対象を Android に設定した。 |
| 同日 Ubuntu、クロスビルド | Quest の充電が約 11% のため、Ubuntu の Clang 21 から Android API 24・ARM64 向けのビルドも試行 | 最初の CMake のリンクテストは `-lgcc` が見つからず失敗。Termux と同じ compiler-rt を選び、CMake のキャッシュを更新すると構成成功。Quest のヘッダー・Fcitx5 ライブラリと、同じ protobuf 34.1 のホスト用 protoc を使用。 |

## 再開位置

次は Quest でアドオンを構築し、Fcitx5 へのロードと変換を別々に確認する。Wayland の候補表示、foot・GTK・Qt の入力、装着時の操作感は未検証。

## ビルドを再現する

Quest の Termux（x11-repo 有効）でリポジトリのルートから実行する。Fcitx5 の版と辞書のハッシュはスクリプトで照合する。生成済み辞書を更新する場合は、Mozc のリビジョンと候補の検査結果を一緒に記録する。

```sh
pkg install x11-repo
pkg install fcitx5 clang cmake ninja git curl python gettext pkg-config extra-cmake-modules nlohmann-json libwayland libwayland-protocols
scripts/termux/ime/build-mozc
scripts/termux/ime/verify-mozc
scripts/termux/ime/build-wayland
```

作業先は `~/.local/share/eidolon-ime-native/d55a051`。引数で別の作業先、`JOBS=1` で並列数を指定できる。配布用の共有ライブラリと登録ファイルはその中の `stage/` に集める。変換検査は毎回専用の一時プロファイルを使う。実機で日常利用している辞書や Android の既定 IME への書き込みはない。
