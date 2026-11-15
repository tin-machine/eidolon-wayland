# Python Deep 初回評価 — 2026-11-15

## 結論と評価条件

Git 管理 source の事前リストを作らず、Repository Deep → 関数選択 → Targeted Deep → 元 source の確認まで実行できた。
**調査範囲の発見と局所依存の読解には利用できるが、Stop / Recover 全体の意味解析やリファクタリング安全性の証明には至っていない。**
特に例外・context manager と cross-module dispatch が、次の Python 解析で埋めるべき主要な穴だった。

解析対象は、その時点の最新 main [`0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4`](https://github.com/tin-machine/eidolon-wayland/commit/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4) の別 clean worktree。
この PR 自身の解析器・テスト・文書を追加する前の製品 repository を測った値であり、マージ後に `--repo .` で得る件数とは異なる。
PR #30 は head `f37096304ef6dc6a6793f559f0912a0f6fd4e0af` の未マージ状態として確認し、その契約索引は取り込まず、依存もしなかった。
並行中の I6 PR #34 の source は、この snapshot に含めていない。

使用した実行系は CPython 3.12.14、profile は `python-local-v1`、追加 `--exclude` はなし。

| 入力・生成物 | SHA-256 |
| --- | --- |
| generator | `fe722d7efd9991cb0ddb3ae13853bb713b0f7bfda7ab579bfe5fea16802255da` |
| profile | `be1bc739d0fa751b6b73a0dcf931d81b4f49499080e336e2f7fb7835b8a34d7b` |
| repository artifact | `73bd8c357128835e8b47b618b19e3abbc38a11d15b8fa11a43ae87cca86ae8ff` |

generator digest は CLI、package、Python adapter、repository / slice 実装の全内容と Python 実行系を含む。
生成物は Git 管理外 cache に保存した。23,209,802 bytes の repository JSON を2回独立生成し、byte 単位の一致と `verify` の成功を確認した。
途中の旧 generator で作った artifact は、実際に `verify` が exit 2 で拒否した。
所要時間の改善やバグ発見率は比較測定していない。

## 自動発見した範囲

| 指標 | 件数 | 意味 |
| --- | ---: | --- |
| Git tracked files | 949 | source 以外も含む固定入力 |
| source candidates | 671 | 拡張子 / shebang で発見 |
| Production source candidates | 471 | 製品・補助ツール候補。配置の証明ではない |
| Test / Fixture candidates | 200 | 検証用 source 候補 |
| AST を解析した Python files | 241 | Production 132、Test / Fixture 109 |
| 少なくとも1関数で CFG / DFG を得た files | 189 | ファイル全体の完全解析を意味しない |
| Unsupported-language files | 430 | 下表の言語・スクリプト |
| Parse failed | 0 | この revision と実行系での値 |
| Generated / Vendor 規則に一致した source | 0 | 既定 path 規則による分類結果 |
| Excluded source | 0 | 追加除外なし。この source では symlink 等の除外もなし |
| Non-source files | 278 | 文書、設定、asset 等。言語意味解析はしない |

拡張子 `.py` の182ファイルに加えて、**Python shebang を持つ拡張子なしの59ファイル**を発見した。
特定の4ファイルや既存 source root を許可リストにしていないため、`scripts/check-native-package` 等も自動で inventory に入った。

| 未対応の言語分類 | files |
| --- | ---: |
| Kotlin (`.kt` / `.kts`) | 242 |
| Rust | 173 |
| C | 8 |
| shader | 1 |
| その他の shebang | 5 |
| shell 拡張子 | 1 |

関数は **3,277**。限定 CFG / Name DFG が得られたものは **1,282**、関数全体を unsupported としたものは **1,995**。
Production 候補に限ると1,364関数中527対応 / 837未対応、Test / Fixtureは1,913関数中755対応 / 1,158未対応だった。
主な blocking reason は `With` を含む837関数、chained / unpacking assignment 594、`Try` 510、unpacking for target 362、generator expression 359、nested function宣言239。
同じ関数に複数の理由があるため、理由別件数は合算しない。

PDG は未実装。Rust / Kotlin の構文や意味論、JNI / SDK callback、build による active source / feature の解決も未実装である。

## Linux remote helper の結果

4ファイルは discovery の入力一覧ではなく、**発見済み inventory から今回評価した範囲**である。

| file | functions | CFG / DFG 対応 | 未対応 |
| --- | ---: | ---: | ---: |
| `scripts/linux/eidolon_linux_desktop_v3.py` | 51 | 29 | 22 |
| `scripts/linux/eidolon_linux_process_owner.py` | 14 | 4 | 10 |
| `scripts/linux/eidolon_linux_persistent_profile.py` | 44 | 27 | 17 |
| `native/transport/src/linux_desktop/v3/bootstrap.py` | 12 | 6 | 6 |
| 合計 | 121 | 66 | 55 |

次の4つは実 CLI の `targeted-deep` を実行した。既定の1 hop / 80 nodes / 160 edges / 65,536 bytesを使用した。

| 選択 symbol | CFG nodes / edges | DFG edges | slice nodes / edges | bytes | 判定 |
| --- | ---: | ---: | ---: | ---: | --- |
| `eidolon_linux_desktop_v3.py::cleanup_owner@482` | 19 / 20 | 15 | 26 / 61 | 31,775 | 対応 subset |
| `eidolon_linux_persistent_profile.py::_read_owner@318` | 10 / 8 | 15 | 20 / 65 | 43,153 | 対応 subset |
| `eidolon_linux_desktop_v3.py::run_settings_probe@448` | 22 / 23 | 22 | 30 / 75 | 41,578 | 対応 subset |
| `eidolon_linux_desktop_v3.py::recovery_receipt@703` | 0 / 0 | 0 | 21 / 49 | 51,233 | `Try` により CFG / DFG 未対応 |

全sliceで `omitted_nodes=0 / omitted_edges=0 / complete=true` だったが、これは**1 hop のモデル化済み近傍を予算で削っていない**という意味だけである。
4件の `outside_hop_edges` はそれぞれ57 / 64 / 58 / 67。未知の動的辺はこの件数にも入らない。
`recovery_receipt` の `complete=true` は解析の意味的完了ではなく、表示した関数・call候補・test参照近傍が切り詰められていないことを示す。

### 1. `cleanup_owner` — 制御順序と不明な heap 条件を区別できる

[固定source](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/linux/eidolon_linux_desktop_v3.py#L482-L499)では、deadlineを持つloop内でoutputをdrainしながら `owner.finish` を繰り返す。
source reviewでは、`report` の完了判定とpipe EOF確認が正常に返った後に `profile.release()` へ進むことを確認した。
DFGは、初期値 `report=None` とloop内の再定義が後続のreadへ到達し得ること、`deadline` が複数のloop判定へ使われることを残す。

ただし11 call site中9件は未解決であり、`report["complete"]` のheap値、`owner.finish`、`drain.pump`、`profile.release` の意味は証明しない。
`require` の例外効果をcallerのCFGへ展開する解析でもない。graphから「必ず回収が完了する」とは判断できない。
[既存のrelease条件テスト](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/tests/test_linux_persistent_profile.py#L259)とC2 fixtureをsource上で照合したが、この解析ツールはそれらを実行しない。

### 2. `_read_owner` — 見かけ上似た検査を削除しない根拠になる

[固定source](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/linux/eidolon_linux_persistent_profile.py#L309-L325)では、`before` のstat stampをread後の `_file_at` へ渡し、`raw` の長さ、read後のsize、parseしたownerをそれぞれ確認する。
15本の局所DFG辺と6件の同一module call候補から、before / raw / after / parsedが異なる検査へ渡ることを追える。
この前後確認は「同じ関数を2回呼ぶ重複」ではなく、identityと読込み内容を結び付けるための順序としてレビューすべきだった。

alias、filesystemの並行変更、`os.pread` やcontract parserの動作は未解決である。
[既存のdirectory / owner / lock replacement負例](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/tests/test_linux_persistent_profile.py#L191)を参照し、局所graphの成立だけでその契約の検証済み状態を更新しない。

### 3. `run_settings_probe` — 次に読む境界を具体化する

[固定source](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/linux/eidolon_linux_desktop_v3.py#L448-L466)では、synthetic環境準備、追跡付きchild生成、deadline内のpoll / drainが一関数にある。
局所DFGは `child` と `deadline` の利用を追える。一方、`probe_env["XDG_CONFIG_HOME"]` の変更はheap更新であり、Name DFGの定義辺にはならない。
19 call site中16件は未解決。environment生成を安全に切り出せるかは、呼出側のsynthetic / persistent条件と、失敗時にどのownerが資源を回収するかの確認を要する。

### 4. `recovery_receipt` — 大きな関数の辺を作らないことも成果

[固定source](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/linux/eidolon_linux_desktop_v3.py#L703-L790)は45 call site、13個の `If / Try` 等のbranch構文を持つ。
同一moduleの26 call候補と関連テストの所在は抽出できるが、fdのfinally回収や例外時のpending receiptを扱うCFGは出さない。
「大きいので分割」ではなく、[partial proof / final writeのretryテスト](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/tests/fixtures/linux_desktop_v3_c2_cases.py#L104-L145)に照らして、必要なcompletion reasonを保持できる例外CFGが次に必要だと分かった。

## リファクタリング候補の評価

自動分類はすべて `unknown`。以下はgraphを入口に、固定sourceを追加確認した人手レビューであり、Reviewed Contract Graphへの追加でも製品修正でもない。

| 分類 | 候補・観測 | 判断と次の検証 |
| --- | --- | --- |
| Accidental Complexityの候補（未確定） | `launch_mousepad` と別fileの `_start_demo` に、forwarded socketのlstat / owner検査とWaypipe prefix組立の重複がある | 固定launcher adapterを残し、共通検査 / prefixだけを共有できるか検討する。任意argv受付へ一般化しない。socket不正・uid不一致・固定app引数・bundle/source hashの回帰が必要。 |
| Essential Complexity | `cleanup_owner` のsingle-owner回収と、完了・EOF・profile releaseの順序 | ownerやdeadlineを複数の独立workerへ分散させる分割を勧めない。将来の整理でも同じownerと失敗時のpendingを維持する。 |
| Essential Complexity | `_read_owner` のread前後identityとowner内容の検査 | 前後の `_file_at` を重複として削除しない。TOCTOU / replacement負例に戻って評価する。 |
| Unknown Complexity | `run_settings_probe` の環境準備と監視の分離 | 局所変数は追えるが、dict更新・filesystem作用・cross-module callerが未解決。責務の切り分け仮説は立つが、現時点で分割を推奨しない。 |
| Unknown Complexity | `recovery_receipt`、`ProcessOwner.finish`、session `serve` | 例外・所有権・再試行が中心。現在のCFG対応範囲では分割安全性を判定できない。 |

重複候補のsourceは[Mousepad adapter](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/linux/eidolon_linux_desktop_v3.py#L469-L479)と[demo adapter](https://github.com/tin-machine/eidolon-wayland/blob/0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4/scripts/linux/eidolon_linux_desktop_session_v3.py#L153-L165)。
session fileは当初例示された4ファイルの外だが、全体inventoryに含まれていたため調査を広げた。
ただし `core.launch_mousepad` 等のcross-module callは現在unresolvedであり、この境界の確認には `rg` と直接source読解を併用した。自動callee解決の成果として数えない。
Accidental Complexityとして削除・共通化してよいと確定した箇所や、新しい製品bugは、この評価だけでは報告しない。

## 検証と再実行

ローカルで次を確認した。

- Deep unit tests **69件 PASS**：Python意味fixture 33、Git / artifact / slice 35、stale generator bytecode 1。
- CI scope regression **27件 PASS**、docs resolver regression **25件 PASS**。
- docs registry validate、`scripts/check-design` **31 tasks / 45 checks**、`git diff --check` PASS。
- 上述の実repository二重生成・byte一致・verify・4つのtargeted sliceが成功。
- 途中の旧generator artifactを実際に拒否。unit fixtureでは改変payloadのchecksumを再計算しても拒否すること、source / mode / index / HEAD / profile / parser変更、抽出中・slice中の変更、曖昧なsymbol・依存、破損JSONを検査。
- 同サイズ・同mtimeの古い `.pyc` が通常の `-B` importでは使われる条件を作り、このCLIがsource bytesと一致するコードをロードすることを検査。

製品source、B1 golden vector、v2 / v3 / Termux、runtime、CI workflow、required checkは変更していない。
このツールの新規unit testは既存hostのunittest discoveryに含まれるが、製品graphの鮮度や内容をrequired CIで毎回検査するjobは追加していない。
ここに実SSH、GUI、JNI、実SDK、Quest成功の証拠は含まれない。GitHub Actionsの状態はPRの実headに対して別途確認する。

このPRのtoolを持つcheckoutから、同じ製品sourceを再評価する例：

```sh
git worktree add --detach /tmp/eidolon-deep-source \
  0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4
python3 scripts/deep_structure.py repository-deep \
  --repo /tmp/eidolon-deep-source --output /tmp/eidolon-deep.json
python3 scripts/deep_structure.py verify \
  --repo /tmp/eidolon-deep-source --artifact /tmp/eidolon-deep.json
python3 scripts/deep_structure.py targeted-deep \
  --repo /tmp/eidolon-deep-source --artifact /tmp/eidolon-deep.json \
  --symbol 'scripts/linux/eidolon_linux_desktop_v3.py::cleanup_owner@482'
```

同一artifact digestの再現には上記generator / Python / profileの一致も必要。
変更後の解析器で旧sourceを分析することは可能だが、異なるgeneratorとして新しいartifactを作る。

## 次の独立単位

1. **Pythonのimport aliasとmodule境界の候補解決**。shadowing、relative import、動的load、monkey patchを明示し、`core.*` / profile / process ownerのsourceをつなぐ。runtime-resolvedへ昇格させない。
2. **限定した例外・cleanup CFG**。`return / raise / break / continue` と `finally` のcompletion reason、例外handler、`with` の抑制を別fixtureで検証する。finallyへの合流で理由を失う実装は採用しない。
3. **Rust / Kotlinのparser adapter比較**。まずsource位置・symbol・明示unknownから入り、runtime / transport / WindowHistory / SDKの実依存に従って対象を選ぶ。async、Drop、trait、macro、coroutine、callbackを独自の完全意味解析器で解き直さない。
4. PDGや方向付きprogram sliceは、上記の支持範囲と未知を保った後に独立評価する。現在のcontains辺をcontrol dependenceへ改名しない。

使い方・恒久的な解析契約は[Deep解析ガイド](source-structure-deep-analysis.md)を参照する。
