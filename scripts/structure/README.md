# Eidolon 専用の契約索引（試行版）

回収・設定保存のレビューで確認した関係を、source、不変条件、負例を含むテストへ結ぶ。
homecluster の graph、queue、DB、設定には依存しない。Python 標準ライブラリと Git だけを使う。
[レビューと適用判断](../../docs/development/source-structure/source-structure-retirement-review-20261115.md)がこの試行の基準である。
続報の[監視範囲・所有境界・追加3問](../../docs/development/source-structure/source-structure-watch-ownership-20261115.md)、
[IME 終了証拠の接続案](../../docs/development/source-structure/source-structure-ime-retirement-contract-20261115.md)も参照する。

## 複数アプリの終了と枠の再利用

同じ CLI の `--scope multi-app-retirement` で[複数アプリの契約索引](multi-app-retirement.json)を選ぶ。従来の設定保存・終了処理は既定の `retirement-settings` で確認する。各 scope はそれぞれのレビュー時点からソースとテストの鮮度を検査する。

Dolphin と KWrite が動いている状態で Dolphin を閉じるときは、Dolphin を起動した runtime に応じて次をたどる。

| どの所有者を確認するか | 起点と確認内容 |
| --- | --- |
| Ubuntu のプロセス | `linux.cleanup` からプロセス終了、出力の EOF、同じ枠の回収記録へ。ホスト再起動時は `linux.receipt` で元の記録と起動 ID を照合する。 |
| Quest の主 runtime | `primary.proof` で対象の終了と回収結果を確認。主 runtime は残りのアプリを管理し続ける。 |
| Quest の追加 runtime | `app.exit` → `app.stop` → `app.destroy`。JNI と Rust は同じ handle の終了を待ち、`registry.remove` でその番号だけを解放する。KWrite の枠は稼働を続ける。 |
| ランチャーと Termux | `capacity.rule` はカード表示と起動直前の両方に使う。`termux.destroy` は独立した Termux runtime の終了を扱う。 |

```sh
python3 scripts/structure.py check --scope multi-app-retirement --profile android-product
python3 scripts/structure.py slice --scope multi-app-retirement --profile android-product --node app.destroy --hops 2 --max-bytes 32000
python3 scripts/structure.py slice --scope multi-app-retirement --profile host-model --node registry.remove
```

## 使い方

repository root から実行する。サービス接続、ビルド、テスト実行、source 編集は行わない。

```sh
python3 scripts/structure.py check --profile android-product
python3 scripts/structure.py check --profile host-model
python3 scripts/structure.py slice --profile android-product --node save.receipt
python3 scripts/structure.py slice --profile android-product --node save.resume
python3 scripts/structure.py slice --profile android-product --node proof.finalize --hops 2 --max-bytes 32000
python3 scripts/structure.py slice --profile android-product --node window.create --hops 2 --max-bytes 32000
python3 scripts/structure.py slice --profile android-product --node scope.future --max-bytes 32000
python3 scripts/structure.py slice --profile android-product --node window.destroy --max-bytes 32000
```

`build --profile android-product` は全登録関係を JSON で標準出力へ返す。
保存する場合は作業用 directory へリダイレクトする。
`verify --profile android-product /path/to/graph.json` は、現在の HEAD、source、generator、registry、profile と全内容を再照合する。
他 profile の artifact、内容を改変した artifact、前の HEAD の artifact は拒否する。

既定 slice は1 hop・24 node・32 edge・16,000 bytes、上限は3 hop・80 node・160 edge・65,536 bytes。
hop は呼出方向に限らず両方向へ辿る。辺に付けた source/test 参照は分割しない。
`complete=false` と `omitted_edges` があれば、その範囲の関係をすべて表示できていない。
`complete=true` でも登録済みの指定 hop 範囲に限る。repository 全体の網羅性ではない。
profile が除いた辺は `profile_excluded_edges` に残す。上限を下げ過ぎて必須 metadata も入らない場合は非0で失敗する。

## 証拠の意味

- [retirement.json](retirement.json) の辺は人手の source review に基づく。anchor は空白を除いた行の完全一致で所在を検査する。Rust/Kotlin の symbol/call 解決ではない。
- `unique_anchor` はその行が一意であることだけを表す。コメント内か、型が一致するか、動的呼出が成立するかを parser で証明しない。
- `reviewed_revision` の Git blob と現在の HEAD の登録ファイルを丸ごと比較する。関数名を変えずに本文が変わった場合や、テストだけ変わった場合も `needs_review` にする。
- 登録ファイルに未 commit の差分がある場合は拒否する。索引スクリプト・registry 自体の試作中差分は digest へ反映する。repository 全体が clean という意味ではない。
- `status=ok` は索引の整合性、登録ファイルの鮮度、明示した監視 source の無変更。実装の安全性、全呼出元の確認、テスト成功、実機受入の証明ではない。
- `tests_execution=not_run_by_this_tool` を常に出す。テストは辺の条件の一部を扱う参照であり、JNI/SDK/remote まで結合した検証とは限らない。
- commit 済みの登録 source 差分や監視だけの source 差分には、`build` は課題入りの JSON と終了コード1を返す。`slice` は再レビューまで拒否する。登録 source の未 commit 差分、履歴の欠落、参照不正などは終了コード2。

## 関係の索引と source の変更監視

`files/nodes/edges` は人が確認した関係、`watch` はそれより広い変更検知の範囲である。
辺を追加せずに監視対象を増やせる。監視ファイルを意味的にレビュー済みとは扱わない。

- Android は `android/app/src` 以下の `.kt/.java`、Rust は `native/{android,core,model,transport,render,theme,fixtures}` 以下の `.rs` を監視する。root/suffix/reason は registry に宣言する。
- 基準 SHA・現在 HEAD の Git tree と実ファイルを比較する。追加・削除・内容・実行 mode の変更を記録する。rename は削除と追加として表示する。
- 指定範囲の ignored/untracked source も含む。source や配下 directory の symlink は追わずエラーにする。抽出前後で一覧と内容の一致も確認する。
- `watch.changes` は `reviewed_to_head` と `head_to_worktree` を分ける。監視だけのファイルの変更でも `needs_review`、終了コード1。旧 artifact の `verify` と新しい `slice` は拒否する。
- 初版の監視399ファイル / `files` 外383に対し、SDK の狭い6関係を追加した後は `files` 外381、登録22ファイルとの和集合403（4つの build 設定は suffix 外だが従来の登録ファイル検査を受ける）。この数は網羅率ではない。

両 profile とも同じ監視範囲を保守的に検査する。host profile でも Android source の変更で再レビューになる。
registry は `reviewed_revision` を監視の比較基準にも使うが、監視ファイルの意味を一括承認したという意味はない。
変更を確認して基準を進める際に、新 caller の ownership や辺・テストの追加が必要かを判断する。

root/suffix 外のファイル、C/XML/生成物/vendor/SDK内部、任意の新 source root は監視しない。
既存 root の追加 caller は変更として見つかるが、その呼出・副作用・所有者を自動解析しない。
Cargo/Gradle 設定の全 include 解決も行わない。`unknowns` は各 slice に必ず残る。

現在の Android profile は55 node / 28関係。SDK の追加6関係は reserve、native registration → allocation、
callback ticket、future producer、delete と同 Scene pass、実 Accepted 後の native release を対象にする。
辺の `test_scope` に、参照テストが ledger model を実行し adapter/JNI を実行しないことを記す。
optional SDK probe は実 scope/fence + doubles の証拠として別に参照する。IME の新 receipt は未実装なので辺にしない。
host profile は44 node / 16関係で、Android/SDK adapter の12辺を除外表示する。

## 環境 profile

`android-product` は Android model + Android adapter/JNI + native を含める。
`host-model` は JVM/native モデルを対象にし、Host/JNI adapter を除く。
これらはレビューの関心領域であって、Cargo/Gradle が解決した active graph ではない。
target、variant、cfg/features の未解決状態を出力へ含める。Linux remote の専用 profile はまだ実装していない。

物理ホストごとに parser を複製する方式は採らない。環境差は profile と環境間契約へ持たせ、
Git 鮮度検査・共通 envelope・slice 制限を共有する。production と fixture の証拠も区別する。

## 更新と検証

1. 変更した source と関連テストを直接確認し、不変条件と未確認境界を更新する。
2. `reviewed_revision` を更新する場合は**全登録ファイルと監視一覧の差分**を確認する。一部だけ見て全辺を承認し直さない。監視追加を semantic review と混同しない。
3. 両 profile の `check` と対象 slice を実行する。同一条件の2回の `build` が byte 単位で一致することを確かめる。
4. `python3 -m unittest discover -s scripts/tests -p test_structure.py -v` で索引ツールの負例を検査する。

現段階では graph 鮮度の required CI は追加していない。上記 unit test は一時 Git fixture を使うため、
アプリ source の変更だけで既存 CI に registry 更新を強制しない。明示的な `check` をレビュー入口で使う。
CI scope の既存方針に従い、この `scripts/**` 差分は通常の共有コード扱いになる。
