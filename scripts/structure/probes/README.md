# 境界レビュー用の任意 probe

[2026-11-15 の境界レビュー](../../../docs/source-structure-boundary-review-20261115.md)を再現する補助。
アプリ source を直接コンパイルするが、Android source set や既存 CI には追加しない。
SDK/環境の追加導入、remote 接続、実機操作は行わない。

```bash
review_base="$HOME/.local/state/homecluster-agents/codex-work"
if [ -f "$HOME/.config/homecluster-agents/codex-work-root" ]; then
  IFS= read -r review_base < "$HOME/.config/homecluster-agents/codex-work-root"
fi
review_out="$review_base/eidolon-boundary-review"
python3 scripts/structure/probes/run.py models --output-dir "$review_out/probe"
python3 scripts/structure/probes/run.py sdk --output-dir "$review_out/probe"
python3 scripts/structure/probes/run.py ime --output-dir "$review_out/probe"
python3 scripts/structure/probes/run.py save-ime --output-dir "$review_out/probe"
python3 scripts/structure/probes/compare.py --output-dir "$review_out/comparison"
python3 scripts/structure/probes/compare.py --questions scripts/structure/probes/boundary-questions.json --output-dir "$review_out/boundary-comparison"
```

`run.py` は `android/build.gradle.kts` の Kotlin version とローカルの Gradle dependency cache を使う。
必要な jar がなければ非0で終了し、取得はしない。Java 17 target を扱える JDK が必要。
使った source/jar の SHA-256、compile log、result log を出力先へ保存する。

- `models`: 実際の Coordinator/Admission/SDK ledger/settings repository と既存5 suite をコンパイル・実行する。
  Android SDK は使わない。実ファイルテストは既存 `HostLinuxStorageFixture` の C/JNI helper を host `cc` と JDK headers で一時生成する。
  これは filesystem 用テスト JNI であり、製品の `NativeBridge` や Rust JNI の検証ではない。
- `sdk`: 実際の `SdkResourceScope.kt` と `SdkEvidenceBridge.kt` を使い、Android Handler と Meta の型・通知を小さな double へ置換する。
  seal と allocation の交差、callback/future drain、delete 通知、後続 pass、Scene 閉鎖、失敗を11ケースで検査する。
  double の通知順を明示的に与えるので、実 SDK の scheduling/API 適合性、全 adapter caller の網羅性は保証しない。
- `ime`: 実 `ImeSessionConnection` / `ImeRawFocusReturn` と依存する純 Kotlin model、既存2 suite を使う。
  Host / TerminalImeController / Android IMM / SDK Entity / 設定保存の結合試験ではない。
- `save-ime`: 実 RuntimeSaveCoordinator と実 ImeSessionConnection を一緒に動かし、UI queue / owner clear / Entity disposal の double を注入する。
  3件は現行で publication と UI 終了が独立していることの characterization。提案する終了条件の成功判定には使わない。
  [設計案と将来の回帰条件](../../../docs/source-structure-ime-retirement-contract-20261115.md)を参照する。
- `compare.py`: 同じ固定 SHA/registry の3問について、固定 `rg` と slice の候補・bytes・参照先を保存する。
  source-only の自律レビュー、独立した reviewer、正答率、読解時間の比較ではない。時間はコマンド自身の実測のみ。
  検索結果は一致行、slice は手動確認済み条件と参照なので、bytes もその異なる内容の比較である。
  `--questions` は編集前に固定した追加3問と必要 source の目安を読み、両方式および和集合で参照が足りるかを記録する。
  question packet はレビュー基準 SHA と照合し、監視・登録 source が基準と異なる場合は拒否する。
  文書・ツールだけの commit 後も再実行でき、実行時 HEAD と基準 SHA は結果へ別々に記録する。
  必要 source の存在だけで正答を証明せず、本文を読む。

`.kt` doubles はここだけに置き、製品の build へ混ぜない。probe 成功は実機受入の証明にしない。
