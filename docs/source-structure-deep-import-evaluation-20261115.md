# Python Deep：repository import候補の拡張評価（2026-11-15）

## 固定条件・過去記録との区別

基準main: 97dfdbbc13e00227b8285d09b0e880cdc6b6b130（PR #35 merge）。PR #30の手動Contract Graphとは独立。
GitHubの再帰Git Tree APIで基準mainに976のblobと193の拡張子pyのpathを確認した。これはCLIのtracked/source/analyzed/parse_failed/unsupported集計ではなく、shebangにも未対応である。PR headに対する完全なDeep artifactの件数、digestと全tracked snapshotは、その版のclean checkoutで生成して初めて確定できる。
[初回評価](source-structure-deep-evaluation-20261115.md)に記録された旧source 0a0454fとそのdigest・結果は書き換えない。

## 解決するsubsetと保守的な境界

Git管理sourceを既存規則で自動発見し、AST parse成功したPythonに対してのみ候補を作る。module-level直下、無条件importの各aliasを対象とする。import module、import module as alias、from module import function [as alias]、packageの解析成功したinit chainがあるrelative importを扱う。途中のinitが除外・parse_failed・symlinkであれば未解決とする。
module候補はimporterと同一ディレクトリを仮のimport rootにした単一のpy、またはinit付きpackageとする。別tracked directoryに同名basenameがあればabsolute importを曖昧として拒否し、relativeはpackage init chainを要する。除外・parse失敗・欠落moduleも未解決に留める。
import先は単一かつ無修飾のtop-level defと、既存のmodule candidateチェックで結べる場合だけcallee候補にする。decorator、reexport、class method、namespace package、star、conditional、try、local import、dotted importでaliasがないものは推測しない。
callerの関数root bindingは既存CPython symtableの一意なfunction scopeがglobalと判定した場合のみ使う。引数、型パラメータ、closure、comprehensionの呼出は昇格しない。名前のStore/Del、dynamic namespace操作を保守的に拒否する。これはfalse-negativeを許容する検出でありruntime immutabilityの証明ではない。
単一attributeのmodule.function呼出とdirectなimported function呼出のみ対象とする。長いattribute chain、receiver dispatch、monkey patch、動的load、実deploymentのsys.pathは解決しない。
既存same-module candidateとrepository_import candidateを区別し、import元locationとsource fingerprint、alias、対象moduleとcallee定義位置、探索規則、未解決理由を証拠として保持する。いずれもresolvedではなくcandidateである。
生成物のprofile/generator IDと新resolverのsource-only loader保護を更新し、verifyでは全source、generator、profile、parser、payload一致を再計算する。CFG/DFG意味論は変更せず、PDGと方向付きprogram sliceは未実装のまま。

## source読解比較の3問

基準mainにおける、通常のrgとsource読み合わせの起点は以下。graph側は新たなfixtureとCIで確認する対象であり、両者を比較したelapsed time・token・見落とし率は未測定。既知sourceを同じreviewerが辿る有利さがありblind comparisonでもない。

| 質問 | 従来のrgとsource入口 | graph / sliceで検証するsource候補 | 未解決と追加読解 |
| --- | --- | --- | --- |
| Stop cleanupの呼出元 | scripts/linux/eidolon_linux_desktop_session_v3.py:16、:99、:105、:144 / eidolon_linux_desktop_v3.py:482 | session_v3.py の _cleanup → desktop_v3.py の cleanup_owner | owner.finishのreceiver型、single-owner、完了→EOF→profile releaseと例外 |
| process identityの境界 | session_v3.py:14、:286 / eidolon_linux_process_owner.py:32 | session_v3.py の serve → process_owner.py の identity | 本番PYTHONPATHとdynamic binding、generation一致、実プロセスの証拠 |
| profile owner確認の境界 | eidolon_linux_persistent_profile.py:318、process_owner.py:32、:45 | _read_ownerと既知sourceを再読解 | read前後identity、TOCTOU、FD owner、error経路 |

比較結果にはgraphによる到達source、余分な候補、unknown、追読箇所、生成・保守コストを分けて記録する。未使用の質問・別reviewerのblind比較は今後の課題。数値の推測補完は禁止する。

## refactor候補と維持する契約

Accidental Complexityの仮説: launch_mousepadと_start_demoのsocket検査、固定Waypipe prefixの重複。固定argv/adapter allowlistは必要。
Essential Complexity: cleanup_ownerのsingle-ownerと完了/EOF/profile release順序、_read_ownerのread前後identity。
Unknown Complexity: run_settings_probeの環境準備と監視、recovery_receipt / ProcessOwner.finish / session serveの例外・所有境界。
関数長や辺数で分割を勧めない。Resource ownership、generation isolation、fail-closed、B1 golden、v2/v3/Termux、Stop/Recover/Journal finalization、JNI/SDK lifecycleを変更しない。製品source/runtimeのrefactorなし。

## 次の独立実装単位

優先1: try/finallyのcompletion reason（normal/return/raise/break/continue）とfinallyによる上書きを、例外handler/with抑制と区別してfixtureで支持。PDGより先。
優先2: 未知質問・別reviewerでの探索効果と生成コスト比較。
優先3: Rust/Kotlinのcompiler/Tree-sitter/LSP adapterの比較。async/trait/macro/Drop、coroutine、JNI/SDK callbackを構文だけで確定しない。
優先4: 支持済みCFG/DFGに基づくPDGと方向付きslice。related_testsはlexical candidateのまま。heap/alias、include_str!、build active sourceも未対応。

## CIで得られた予備観測（2026-11-15）

初回PR head f774b930でGitHub Actions のhost検査を実施した（system Python 3.12）。
新規import候補fixtureの13件中、実Linux source3ファイルを一時Git repositoryにコピーして実行し、_cleanup → cleanup_owner（call line105、import line16、callee line482）とserve → identity（call line286、import line14、callee line32）を確認する回帰はPASSだった。ほか11件PASS、parse_failed境界1件はERRORだった。
**host suite全体は1138件中 failure 1 / errors 32 / skipped 10で失敗**した。主因は既にparse_failedと記録したsourceを新resolverが再度AST.parseしてSyntaxErrorを起こした回帰であり、後のhead 1305ae6bで再解析対象の選択を修正した。検証済みheadの結果を未検証の新headの成功と混同しない。

この予備比較で実際に得られた新しい発見は「もともと手動で辿っていた2本の依存を、import位置とsource provenanceがあるcandidate edgeとして再現できた」という点まで。未知の依存の発見、時間/トークン短縮、誤った候補の実コード全件率は測っていない。今後の新しいheadの全テストと完全なrepository-deep計測は別の検証証拠とする。

## a973e8e レビューへの修正（2026-11-15）

対象は PR #36 head `a973e8ee8def30fe99392525f91f540961df6c13`。開始時mainは上記 `97dfdbb` のまま、既存PRの履歴を維持して修正する。以下は以前の成功記録を置換するものではない。

### 修正した4点

1. CLI統合テストはcalleeだけでなく `e["from"] == target` でcallerも照合し、該当辺が1本であることを検査する。2-hop近傍に含まれる別callerの144行を、選択した `_cleanup` の105行と取り違えない。
2. `__finish` のようなprivate属性の綴りは、class内でcompilerが参照する名前と同一とは限らない。独自manglerやclass推測を導入せず、二重underscoreで始まり二重underscoreで終わらない属性callを `private_attribute_mangling_not_modeled` へ落とす。この版ではclass外の同じ綴りも保守的に未解決とする。通常名、single underscore、`__finish__`、明示的な `_C__finish`、module-level `from service import __finish as done` は従来の候補条件を維持する。
3. 全compiler scopeの `is_declared_global()` が返す正規化済み名をalias再束縛検査にも使う。class body、method、nested function、generic classの `global __core` は `_C__core` のcandidateを抑制する。同一module候補と同様、宣言だけでも保守的に抑制し、AST名だけから再束縛を判断しない。
4. callerとexporterで同じ直接呼出のnamespace guardを共有する。`exec` / `eval` / `globals` / `locals` / `vars` / `setattr` / `delattr` がimport先にあれば、そのmoduleのexport候補を抑制する。module経由とfrom-importの双方に `target_module_dynamic_namespace_unsupported` と、`target_module_boundaries` の操作名・位置・source fingerprintを残す。

仕様根拠: [Python private name mangling](https://docs.python.org/3.14/reference/expressions.html#private-name-mangling)、[compiler symbol tables](https://docs.python.org/3.14/library/symtable.html)。attribute名はsymtableの変数binding一覧だけでは判定できない。動的namespace guardは既知の直接呼出を構文上検出するsubsetであり、alias経由の動的実行や任意callの効果を解決したものではない。nested・未評価scope内も保守的に拒否するため、候補の取りこぼしはあり得る。

### 回帰と証拠の範囲

新規 `test_deep_import_guards.py` は8 tests。元resolverに対してprivate属性・正規化global・dynamic exporterの負例で計27 failure（subtestを含む）を再現し、修正後は8 tests PASS（CPython **3.13.5**, final/0）。さらに既存import fixtureのうち実製品sourceコピーを必要としない16 tests、新規のbounded slice / provenance / payload改変拒否1 test、既存stale-pyc 1 testがPASS。ローカル実施は合計**26 tests**であり、Deep suite全件の成功を意味しない。

ローカルはGitHubへ直接cloneできないため、固定headから取得した解析器・fixtureのbytesをGit blob SHAで照合した検証用コピーを使用した。利用可能なPythonは3.13.5のみ。実製品source3ファイルを読む2つのintegration test、残る既存Deep tests、3.12/3.14の再実行、docs/design/CI scope検査はこのローカル結果に含めず、最新headのCI結果と区別する。追加後のDeep tests定義数は99から**108**（+9）となるが、定義数を成功数に読み替えない。

旧resolverの実CLIで作った固定Git fixtureのartifactは、sourceを変えず修正版CLIでverifyするとexit 2（stale）になった。新しいresolverファイルは既存generator hash / source-only loaderの保護下にあり、profile/Python版を跨ぐ互換は追加しない。動的exportのunknownは予算を絞ったsliceにも残し、操作位置を改変してpayload checksumを再計算したartifactもverifyで拒否した。

製品source、CFG/DFG、Contract Graph、CI workflowには変更を加えていない。最新headのCI結果はPR本文へ別途記録し、従来の未測定な全repository計測・効果比較・3版全件検証の完了をこの修正から推論しない。
