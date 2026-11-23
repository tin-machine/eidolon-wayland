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

## 再開位置

次は Quest でアドオンを構築し、Fcitx5 へのロードと変換を別々に確認する。Wayland の候補表示、foot・GTK・Qt の入力、装着時の操作感は未検証。
