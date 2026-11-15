# Codex から実行する自動発見型 Deep 構造解析

このツールは、Git 管理ソースから調査対象を発見し、Python の関数・呼出候補・限定的な制御フローと局所的なデータ依存を、元のソース位置へ戻れる形で提示する。
Codex が調査する箇所を選ぶための読み取り専用ツールであり、製品コードの正しさや Host / Android / Quest 受入れの判定器ではない。
[最初の実コード評価](source-structure-deep-evaluation-20261115.md)に、固定 main の件数、slice、リファクタリング候補と未解決境界を記録する。

## 方針と PR #30 との関係

2026-11-15 の実装開始時点で、[PR #30](https://github.com/tin-machine/eidolon-wayland/pull/30) は未マージだった。
確認した head は `f37096304ef6dc6a6793f559f0912a0f6fd4e0af`。
同 PR の `scripts/structure.py` と `scripts/structure/retirement.json` は、人が確認した意味的契約の索引と変更監視である。
この Deep ツールは当時の main から独立して実装し、それらのファイルを main に存在するものとして読み込まなかった。
その後 PR #30 は main `480e1b5` へマージされた。これは後の状態変化であり、このツールが契約索引に依存するという意味ではない。
現在も自動解析は `retirement.json` を契約索引として解釈・変更せず、人手レビュー済み契約と candidate graph を分離する。
全 tracked files の snapshot 鮮度を検証するための bytes 照合には含まれるが、その契約辺を自動解析の結果へ取り込まない。

本書は、PR #30 の[旧評価計画](https://github.com/tin-machine/eidolon-wayland/blob/f37096304ef6dc6a6793f559f0912a0f6fd4e0af/docs/source-structure-post-i5-evaluation-plan-20261114.md)にある、Lightweight を先に完成させる将来方針と、全評価を PR #30 へ継続反映する運用を、**この新しい Deep 実装について置き換える**。
同計画に記録された過去の計測、`scoped_only` 判定、契約レビューを新しい解析結果として書き換えない。
Lightweight の完成や人手による対象ファイルの全件登録は、Deep 実行の前提にしない。

実装開始時の main の `AGENTS.md` に PR #30 固定の継続指示はなく、その指示は PR #30 の branch にのみ存在した。
恒久的な作業規則は「現在の PR と branch を確認し、既存差分を保持し、別責務の実装を独立した PR として扱う」とする。
一時的な PR 番号や作業 branch の継続先は、各 PR 本文とその時点の作業記録に置く。

## 最初の実装単位

| 機能 | この単位で扱うこと |
| --- | --- |
| Repository Deep | Git 管理ファイルを自動発見し、選択根拠、役割、言語、解析状態を記録する。Python の AST と compiler の symbol table を読み、調査候補を示す。 |
| Targeted Deep | Repository Deep が発見した正確な symbol ID を選び、選択関数の CFG / DFG、関数単位の呼出候補、テストの file 参照候補から限定近傍を得る。 |
| Source provenance | source revision、ファイル位置、入力の同一性、generator / profile / Python 実行系を記録する。 |
| 鮮度検証 | 現在の source と生成条件から再検証し、古い artifact や改変された artifact を拒否する。 |
| Contract Graph | 自動生成物とは分離する。自動辺をレビューなしに正式な契約へ追加しない。 |
| PDG / Rust / Kotlin | この単位では実装しない。未対応と残課題を明示する。 |

解析器は対象ファイルを import / execute しない。製品 runtime、SSH、SDK、テストも起動しない。
生成物の保存だけを行い、source、設定、registry、Git index、branch を書き換えない。
通常開発の前に常時実行する required CI は追加しない。
この実装の `scripts/**` 変更自体は、既存の [CI change-scope policy](ci-change-scope-20261113.md) に従う。

## 対応する Python 実行系

CLI の対応範囲は **CPython 3.12 / 3.13 / 3.14** とする。
`python3` がどの版を指すかに依存せず、`python3.12`、`python3.13`、`python3.14` のいずれかを明示する。
この範囲はツールの実行方針であり、異なる版で同じ source が必ず構文受理されるという意味ではない。
CLI は対象 repository を開く前に実行系を検査し、対応外の Python implementation / minor version を exit 2 で拒否する。

**一つの artifact の生成・`verify`・`targeted-deep` には、同じ interpreter を使用する。**
generator identity は implementation 名と完全な `sys.version_info`（major / minor / micro / release level / serial）を含む。
3.12 と 3.13 の違いだけでなく、patch version が変わった場合も別の生成条件となる。
parser の版をまたぐ artifact 互換性は提供しない。更新した interpreter を使うときは新しい artifact を生成する。

### Annotation scope による構文差

[Python 3.14 の execution model](https://docs.python.org/3.14/reference/executionmodel.html#annotation-scopes)では、通常の関数・変数 annotation にも annotation scope が使われ、その scope 内の `:=` は許可されない。
例えば、非 generic 関数の引数 annotation `def f(x: (alias := int)): ...` のような、3.12 / 3.13 では受理できる形式も、3.14 では構文エラーになる。
このツールは実行中の parser と context-sensitive な compile 検査に従い、該当ファイルを `parse_failed` にする。互換性のために禁止された構文を成功扱いしない。

[3.12](https://docs.python.org/3.12/reference/executionmodel.html#annotation-scopes)と[3.13](https://docs.python.org/3.13/reference/executionmodel.html#annotation-scopes)でも、PEP 695 の型パラメータや generic 関数などに導入された annotation scope では、すでに `:=` が禁止されている。
「古い Python なら全 annotation で代入式が使える」とは扱わない。
型パラメータによる shadowing と、nested definition の default で enclosing scope が束縛される場合も、同名 module 関数への call candidate を保守的に抑制する。

## Codex の基本手順

1. 現在の main、対象 branch、関連 open PR と差分を確認する。解析対象の source と generator を確定する。
2. 実行する CPython の版を選び、clean checkout で `repository-deep` を実行する。全ファイルを手書きで列挙する必要はない。
3. coverage と未対応理由を読み、関数の責務集中、分岐、局所データ依存、呼出候補から調査対象を選ぶ。
4. artifact 内の正確な symbol ID を使って `targeted-deep` を実行する。hop 数と出力上限を目的に合わせる。
5. slice に記録された source location を開き、要件、関連テスト、所有境界を照合する。
6. 複雑さの分類と反証条件を記録する。製品修正は別の実装判断とし、変更後には新しい確定 revision から再生成する。

以下は repository root から CPython 3.12 を使う例。`--repo` は対象 repository の root を指定する。
使用可能な引数は各 subcommand の `--help` でも確認できる。上限は下表のとおり。

```sh
python3.12 scripts/deep_structure.py repository-deep --repo .

python3.12 scripts/deep_structure.py repository-deep --repo . \
  --output /tmp/eidolon-deep-py312.json

python3.12 scripts/deep_structure.py verify --repo . \
  --artifact /tmp/eidolon-deep-py312.json

python3.12 scripts/deep_structure.py targeted-deep --repo . \
  --artifact /tmp/eidolon-deep-py312.json \
  --symbol 'ARTIFACTに記録された正確なsymbol ID' \
  --hops 2 \
  --max-nodes 40 --max-edges 80 --max-bytes 32000
```

CPython 3.13 または 3.14 を選ぶ場合も、生成と検証の組を同じ interpreter にする。次の各組は別 artifact を作る例であり、版をまたいで同一結果になるという主張ではない。

```sh
python3.13 scripts/deep_structure.py repository-deep --repo . \
  --output /tmp/eidolon-deep-py313.json
python3.13 scripts/deep_structure.py verify --repo . \
  --artifact /tmp/eidolon-deep-py313.json

python3.14 scripts/deep_structure.py repository-deep --repo . \
  --output /tmp/eidolon-deep-py314.json
python3.14 scripts/deep_structure.py verify --repo . \
  --artifact /tmp/eidolon-deep-py314.json
```

初版の slice は辺を両方向へ辿る限定近傍であり、行番号指定や前方 / 後方の program slicing は実装していない。

| 引数 | 既定値 | 許容範囲 |
| --- | ---: | ---: |
| `--hops` | 1 | 0〜3 |
| `--max-nodes` | 80 | 1〜200 |
| `--max-edges` | 160 | 0〜600 |
| `--max-bytes` | 65,536 | 1,024〜262,144 |

`--exclude GLOB` は追加除外を明示する。複数回指定でき、指定順や重複に依存する選択にはせず、profile と artifact に残す。
pattern は Python の `fnmatchcase` で解釈し、`*` は `/` にも一致する。
**生成時に指定した `--exclude` は、`verify` と `targeted-deep` でも同じ集合を指定する。** 省略・変更すると profile mismatch で拒否する。
対象の事前登録として使わず、除外が必要な理由を作業記録にも書く。

既定の cache は repository の Git 管理ディレクトリ内の `eidolon-deep/` に置く。
通常 checkout では `.git/eidolon-deep/` であり、Git worktree では `.git` がディレクトリとは限らないため、CLI が報告する保存先を使う。
Repository Deep は `repository.json`、Targeted Deep は `slice.json` を保存し、標準出力へ保存先と summary を返す。`verify` は保存せず検証結果を返す。
`--output` は `.json` を指定する。checkout 内は専用 Git cache または明示的に ignored な保存先だけを許可し、tracked file や Git metadata を上書きしない。
checkout 外では新しい `.json` を作成できる。既存ファイルは、このツールの repository / targeted artifact kind を持つ場合だけ置き換える。
出力先や親 directory の symlink は拒否する。生成 graph を source の正本として commit しない。

## 自動発見と coverage の読み方

対象の起点は Git 管理ファイルの一覧であり、特定の `scripts/linux/` ファイル一覧ではない。
拡張子と Python shebang を用いて言語候補を判定し、除外規則を適用する。
既存の Python helper が増えた場合も、同じ discovery 規則で次の snapshot に現れる。

**ファイルの役割と解析結果は別の軸である。** Test / Fixture であっても Python AST を読めることはあり、Production source 候補であっても言語未対応や関数内未対応構文がある。
次の分類を混ぜて一つの「解析済みファイル数」にしない。

| 区分 | 意味 |
| --- | --- |
| Production source candidate | path 規則から製品・補助ツール候補としたもの。実際の配置・実行を証明しない。 |
| Test / Fixture | ディレクトリ・ファイル名から区別した検証用 source。解析しただけではテストを実行したことにならない。 |
| Generated / Vendor | 除外理由を残して parser の対象外とする。これらの内部を解析したとは扱わない。 |
| Unsupported language | source 候補として発見したが、この版に対応 parser がないもの。Rust / Kotlin / JNI 境界を暗黙に解決しない。 |
| Excluded source / non-source | 明示除外や既定規則で選ばなかったもの。適用した規則と理由を残す。 |
| Parse failed | Python 候補として読んだが、AST / symbol table の生成または context-sensitive な compile 検査に失敗したもの。AST 生成だけが成功しても解析済みとはしない。ファイル単位の失敗として残す。 |
| AST analyzed | ファイルの `analysis_status=analyzed`。関数や呼出式を抽出できたもの。全関数の CFG / DFG 成立とは別に数える。 |
| Deep supported / unsupported | 関数ごとの制御・データ解析の対応範囲。未対応構文の種類と位置を隠さない。 |

summary は `tracked_files`、`source_candidates`、`parsed_python_files`、`files_with_supported_deep_function`、`cfg_dfg_supported_functions` などを別々に返す。
`source_by_role`、`source_by_language`、`source_by_status` の集計は異なる軸なので足し合わせない。

未追跡 / ignored ファイルは committed analysis scope の外であり、inventory に含めない。存在していてもエラーにはせず、import / execute もしない。
tracked source 以外を暗黙の製品 source として graph に混ぜない。
生成物、外部 SDK、submodule 内部、build が生成・埋め込み・配布する全関係の解決は、この coverage から推論しない。
特に Rust の `include_str!` と Python の関係は、Python 自体を発見できても、埋め込み関係の意味解析まで完了したことにはならない。

## Python の解析契約

### 関数と呼出候補

Python 標準ライブラリの `ast` から宣言と call site の位置を抽出し、[`symtable`](https://docs.python.org/3.14/library/symtable.html) から compiler が決定した local / free / global の束縛区分を得る。
local / free の判定を、AST の代入 target の手書き収集だけで再実装しない。
symbol ID は `path::qualname@definition_line`。method は `Class.method`、nested function は `outer.<locals>.inner` として scope を区別する。
行番号は 1 始まり、column は Python AST に従う 0 始まりの UTF-8 byte offset である。
AST の各関数と compiler の function scope を、関数名と定義行が一致する一意な table で対応付ける。
table が見つからない、または一致が複数ある場合は、scope を推測せず call target を unresolved とし、その関数の CFG / DFG も unsupported にする。
関数の `scope_resolution` は `compiler_symtable` または `unresolved`。対応が曖昧な場合の理由は、位置付きの `analysis_limit` に残す。
call site が存在することと、実行時の callee が一意に定まることを分けて記録する。
単純な名前による call のうち、compiler がその関数 scope で global と分類し、同じ module の一意な関数宣言へ結び付けられるものだけを **candidate** とする。
module 側の再束縛、decorator、動的変更等への保守的な検査も維持する。compiler の global 判定は、実行時 dispatch の確定ではない。
ローカル名の shadowing、再束縛、動的 receiver、別 module、外部 library、decorator などを解決できない場合は `unknown` / unresolved とする。
関数名が似ていることだけで caller / callee や関連テストを確定しない。

型パラメータの free binding、nested definition の default による enclosing local の束縛も、compiler の区分に従う。
遅延評価される type alias の式を enclosing function body の即時 call として数えない。
comprehension 内の call は、function scope と同一だと仮定せず unresolved に残す。この版では comprehension を含む関数の CFG / DFG も未対応である。
該当する call の理由は `nested_expression_scope_unresolved`、型パラメータを含む free variable のデータ利用は `free_variable_environment_not_modeled` とする。

class の private name などで AST の名前表記と compiler の名前表記が一致しない場合は、その関数の CFG / DFG を位置付きの `analysis_limit` で unsupported とする。
同名の module 関数を推測で結ばず、該当 call は unresolved に残す。独自の name mangling 規則で補完しない。
compiler が正規化した declared global は、mangled name も含めて module candidate の抑制に使う。

`related_tests` は、Test / Fixture 内の import module 名や `.py` path 文字列の basename が、production 候補の filename と一致する場合の **file 単位の lexical reference candidate** である。
同名ファイルが複数あれば候補集合を残し、関数呼出やテスト coverage を確定しない。
import の存在や候補辺だけで、その関数の全分岐、resource ownership、実 SSH / JNI / SDK を検証しているとは判断しない。

### CFG：限定された normal-flow

CFG は関数内の statement-level graph とする。
対応する構文について、順序、条件分岐、loop、明示的な終了や継続を fixture で確認する。
式全体の実行、call、演算子、属性アクセスなどが正常に完了する場合の局所モデルであり、暗黙の例外、descriptor、ユーザー定義演算子、外部 call の副作用は完全には表さない。

未対応構文を含む関数は、不明な statement を素通りさせて「完全な CFG」を作らない。
関数と call site の source 情報は残し、CFG / DFG が得られない理由を関数単位で示す。
特に `try` / `finally`、context manager、async / generator、pattern matching の cleanup や非局所制御を、通常の直列 statement と同一視しない。

| 構文 | 初版の扱い |
| --- | --- |
| `Assign` / `AugAssign` | 単一の `Name` / attribute / subscript target を CFG に置く。DFG の定義は局所 `Name` だけ。 |
| `AnnAssign` | `Name` target のみ。値がない annotation は変数を宣言するが、値の定義にはしない。 |
| `Expr` / `Pass` / `Import` / `ImportFrom` | statement-level の通常順序。import 先のコードを解析のために実行しない。 |
| `If` / `While` / `For` | 分岐と loop。`For` の target は単一 `Name` に限定し、iterable の初回評価と各 iteration の束縛を区別する。 |
| `Break` / `Continue` / `Return` / `Raise` | 明示的な制御移動。`break` が loop の `else` を通らないことを区別する。 |
| 短絡論理式 / 三項式 / chained comparison | 式内制御は分割せず、読み得る局所名を保守的に記録する。条件の実行可能性は解かない。 |
| `Try` / `TryStar` / `With` / async / `Match` | 関数全体の CFG / DFG を unsupported とする。 |
| comprehension / generator / lambda / `yield` / `await` | scope・遅延評価・中断の意味論を未実装として、関数全体の CFG / DFG を出さない。 |
| nested function / class の宣言、`global` / `nonlocal` / `del`、walrus、unpack / chained assignment、`assert` | 関数全体の CFG / DFG を unsupported とする。nested function の索引自体は別 symbol として残す。 |
| `exec` / `eval` / `locals` / `globals` / `vars` の直接呼出 | namespace への動的作用を未対応として扱う。 |

loop の初回未実行経路では、iteration target が束縛済みだと決め付けない。
iterator が返す値そのものの provenance は、この Name DFG の対象外である。

### DFG：局所変数の reaching definitions

DFG は、compiler の symbol table による局所名の区分と対応 CFG を使い、局所 `Name` の定義がどの使用へ到達し得るかを求める。
free variable を module global や局所定義へ置き換えて解決しない。
分岐の合流では複数の到達定義を保持し、loop は固定点まで伝播する。
同名変数が途中で再定義された場合は、その経路の古い定義を置き換える。

これは値の一致、alias、object field、container mutation、process state、file descriptor の所有権を証明する解析ではない。
局所名 `owner` の定義と使用が結ばれても、その object が唯一の owner であるという契約にはならない。
未定義の可能性、曖昧な依存、未対応構文は source review に残す。

### PDG は次の独立単位

この版では PDG を出さない。
構文木の「条件文の内側にある」という関係を、意味的な control dependence と呼ばない。
将来追加する場合は、CFG の支持範囲、終了・非終了の扱い、post-dominator に基づく制御依存、DFG の精度をそれぞれ fixture で検証する。
先に実用的な CFG / 局所 DFG と slice の有用性を確かめる。

## source provenance・決定性・stale 拒否

artifact は次の入力と結び付ける。

- source の exact revision と Git tree の全追跡対象、HEAD と一致する index。
- 全 tracked blob の内容と mode。解析 node には元の path / line / column も付ける。
- generator の実装内容と版、選択・除外 profile、CPython の完全な parser version。
- graph 本文、coverage、未対応理由を含む生成 payload。

source、generator、profile、CPython の完全な版が一致する条件から、同じ JSON を生成する。
時刻、絶対 checkout path、実行ごとに変わる ID を解析内容の識別に使わない。
source は抽出の前後で検査し、途中での変更を正しい snapshot として保存しない。
公開インターフェースは新しい process で起動する CLI である。自身の package / parser / repository module は既存 `.pyc` を使わず、読み込んだ bytes を compile し、その digest とディスク上の source を照合する。
`-B` だけでは既存 bytecode の読込みを防げないため、同サイズ・同 mtime の古い cache を含む負例も検査する。内部 Python API を独自の永続 interpreter へ import する運用は、この loader 契約の対象外である。

`verify` と `targeted-deep` は、その artifact が現在の入力から得られるものかを確認する。
HEAD が進んだ、tracked source を変更した、generator / profile が変わった、JSON が壊れた、payload を編集した、といった場合は非ゼロで拒否する。
旧 artifact の source SHA 欄だけを現在の SHA へ書き換えて再利用しない。新しい確定入力から再生成する。

このツールでの clean checkout は、index と全 tracked blob / mode が HEAD と一致することを意味する。
未追跡 / ignored ファイルの有無は判定条件に入らず、その内容は committed scope 外である。
解析対象外の tracked 文書変更でも、そのまま current snapshot として受け入れない。
dirty checkout を自動 stash / reset / commit しない。必要なら別の clean worktree を用意する。
検証成功は入力と派生物の整合を意味し、製品の安全性や実機の受入れ成功を意味しない。

## Bounded slice の読み方

slice は指定した symbol の **undirected bounded candidate neighborhood** である。
関数単位の `call_candidate` は全解析関数を対象とし、そのうち選択した一関数だけ CFG / DFG node まで展開する。
関数から statement への辺は `contains_statement` と明記する。これは包含関係であり、control dependence ではない。
関連テストへの辺は `test_file_reference_candidate` であり、関数の coverage や実行結果を表さない。

node 数、edge 数、出力 bytes に上限を設ける。seed 自身も node 上限に含める。
`complete`、`omitted_nodes`、`omitted_edges` と未対応理由を読む。
`complete=true` は選択した hop 内で候補近傍を省略していないという意味であり、repository 全体や実行時の全依存を網羅するものではない。
`outside_hop_edges` は hop 境界をまたぐ既知の辺数を示す。`unresolved_data_uses` は unbound の可能性や global / builtin への未解決依存を、node が予算外になっても source location とともに保持する。
neighbor 関数にも `unknown_kinds` / `blocking_reasons` を残し、同名 file のテスト参照には `candidate_paths` / `ambiguous` を残す。
seed と必須の unknown / call-site / unresolved-data 情報が byte 上限に収まらない場合は、未知を隠して短縮せずエラーにする。

正確な symbol ID を使い、同名の別関数を推測で選ばない。
`path::qualname` の短縮形も一意に選べる場合だけ受け入れるが、再現する作業記録には `@definition_line` 付き ID を残す。
調査の途中で範囲を広げるときは、その理由を記録して別の seed や hop 数で再取得する。
関連 source やテストが slice に現れないことは、「依存がない」「テストがない」の証明にはならない。

## リファクタリング候補の評価

自動生成された候補の既定評価は **Unknown Complexity** とする。
関数長、branch 数、fan-in / fan-out、DFG の辺数だけで分割を推奨しない。
Codex が source と要件を照合した後、次の分類と証拠を作業記録へ追加する。

| 分類 | 判断に必要な材料 |
| --- | --- |
| Accidental Complexity | 重複した変換や独立した責務など、必要な契約を保ったまま削減できる具体的な根拠。変更前後の反例・テスト。 |
| Essential Complexity | 単一 owner、generation 分離、fail-closed、cleanup 順序など、現在の構造が守っている要件と失敗例。 |
| Unknown Complexity | 解析範囲外、動的依存、要件不足、実機未確認など、判断を阻んでいる事実と次に読む source。 |

候補ごとに source revision / location、観測した依存、仮説、変更で失われ得る不変条件、必要な検証、未確認事項を残す。
Linux helper の Stop / Recover / Journal finalization は、複数の資源を一つの owner が扱う必要がある。
この集約を単に「大きい関数」として分割すると、排他や終了証拠が分散する可能性があるため、所有境界から判断する。

Resource ownership、generation isolation、fail-closed、B1 golden vector、v2 / v3 / Termux 互換性、Stop / Recover / Journal finalization、JNI / SDK lifecycle の契約を維持する。
自動 graph の辺は candidate に留める。source review、契約との照合、必要なテストを経ずに Reviewed Contract Graph へ昇格させない。

## 次の解析領域

初版の結果を使い、Linux remote helper の process / private bus / lease / Stop / Recover に関係する Python 関数を選ぶ。
特定の四つのファイルを固定した許可リストにせず、実際に発見した call site、依存、未対応構文に従って範囲を変える。

Rust は Tree-sitter、rust-analyzer、compiler が出力する情報などの利用を比較し、まず symbol / source provenance と unknown の境界を定める。
async、trait dispatch、macro 展開、`Drop`、feature / `cfg` を構文木だけで解決済みとしない。
Kotlin は compiler / language server / Tree-sitter などを検討し、coroutine、virtual dispatch、SDK callback、JNI、Android lifecycle を別の未解決境界として保持する。
既存 compiler と同等の完全な意味解析器を独自実装することは、この取り組みの目標にしない。

Repository Deep で調査対象を発見できること、Targeted Deep の出典付き slice から安全に source review を進められることを評価する。
探索時間や見落としの改善を主張する場合は、既知課題に合わせた fixture だけでなく、未使用の調査課題と維持コストを含む比較が必要である。

## 参考資料

- Python 公式: AST と source location（[3.12](https://docs.python.org/3.12/library/ast.html) / [3.13](https://docs.python.org/3.13/library/ast.html) / [3.14](https://docs.python.org/3.14/library/ast.html)）。AST grammar は Python の版に依存するため、実行系も生成条件として記録する。
- Python 公式: [`symtable` — compiler の symbol table](https://docs.python.org/3.14/library/symtable.html)。関数 scope の識別と local / free / global の区分を得る。解析対象を import / execute する API ではない。
- Python 公式: annotation scopes（[3.12](https://docs.python.org/3.12/reference/executionmodel.html#annotation-scopes) / [3.13](https://docs.python.org/3.13/reference/executionmodel.html#annotation-scopes) / [3.14](https://docs.python.org/3.14/reference/executionmodel.html#annotation-scopes)）、[PEP 695](https://peps.python.org/pep-0695/)。型パラメータの scope と、版に依存する annotation の構文を区別する。
- Python 公式: [Compound statements](https://docs.python.org/3/reference/compound_stmts.html)。loop / `else`、`try` / `finally`、`with`、async などの実際の言語意味論を fixture の根拠にする。
- Git 公式: [git-ls-tree](https://git-scm.com/docs/git-ls-tree)、[git-cat-file](https://git-scm.com/docs/git-cat-file)。確定 tree と object 内容を得るための一次資料。
- homecluster-runbook: [source_selection.py](https://github.com/tin-machine/homecluster-runbook/blob/4254b272a187df97f6c9bc346ff4c3aaa324b6aa/.agents/skills/homecluster-source-structure/scripts/source_selection.py)、[snapshot_contract.py](https://github.com/tin-machine/homecluster-runbook/blob/4254b272a187df97f6c9bc346ff4c3aaa324b6aa/.agents/skills/homecluster-source-structure/scripts/snapshot_contract.py)、[deep_snapshot.py](https://github.com/tin-machine/homecluster-runbook/blob/4254b272a187df97f6c9bc346ff4c3aaa324b6aa/.agents/skills/homecluster-source-structure/scripts/deep_snapshot.py)、[slice_graph.py](https://github.com/tin-machine/homecluster-runbook/blob/4254b272a187df97f6c9bc346ff4c3aaa324b6aa/.agents/skills/homecluster-source-structure/scripts/slice_graph.py)。自動発見、固定入力、鮮度検証、bounded slice の参考。
- homecluster-runbook: [campaign 評価と適用限界](https://github.com/tin-machine/homecluster-runbook/blob/4254b272a187df97f6c9bc346ff4c3aaa324b6aa/docs/operation/pi-local-structural-graph-campaign-evaluation-20261003.md)。同実装の lexical control region や conservative finally dispatch を完全な PDG / CFG として移植しない。

この文書は手動で発見する開発ツールの説明であり、D5 / D6 の feature manifest や I6 の acceptance gate を変更しない。
