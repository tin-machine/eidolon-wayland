# I6 第1実装単位: 固定ケース・証拠契約・独立 validator

2026-11-15（日本時間）。本実装の開始点は main
`74b1b2d22fc8fa10d978e7533e08e4565366efed`（PR #32 merge後）。
[I6 詳細設計](../../linux-real-app-v3-i6-detailed-design.md) の調査baseline
`0e0df43a2ce4734a00a36f830cc24a80e277bec5` は過去の観測として維持する。

**今回利用できるのは、I6 reportの構造・参照・所有相関・artifact整合性を独立に検査する機能である。**
実SSH/Registry runner、実Host描画、GUI/file/pixel oracleは後続の実装であり、
この単位のunit test成功やPR mergeを、I6 Host acceptanceの成功へ読み替えない。
I6は引き続きmanual discoveryとし、resolver manifestの登録taskには追加しない。

後続の [第2実装単位 N01: 実SSH／Inspect-only](i6-n01-inspect-only.md) では、
通常Registry runner、disposable環境、読み取り専用trace、N01専用oracleを接続する。
この文書の第1単位時点の未実施記録は維持し、N01の実施結果と正式I6全体の未完了を区別する。

## 1. 開始時の差分と今回の境界

開始時に最新mainと作業treeを確認し、I6実装の重複はなかった。
open PR #30は構造解析の別スコープであり、この実装の依存にしない。
関連文書は移動しておらず、I2/I4のcontextを別々に取得して確認した。

| 対象 | 既にあるもの・再利用するもの | 今回追加したもの | 検証方法・後続境界 |
| --- | --- | --- | --- |
| CLI | A0 feasibility、private artifact root | stage別の判定・終了値、保存reportのread-only再検査 | feasibilityの0/2互換とacceptanceの拒否を検査 |
| ケース集合 | 詳細設計§11の28 family | 87 variant、533 assertion参照を固定したmanifest | family/variant/assertion削減、期待outcome変更を拒否 |
| JSON契約 | B1のstrict JSON parser | acceptance専用schemaと1 MiB writer | duplicate/unknown key、型・版・上限・UTF-8拒否 |
| 証拠 | 既存product sourceと所有契約 | owner/target join、bounded trace、census、artifact reader | 欠落・改変・cross-run/capture/source・不正回収順の拒否 |
| source | Git、15-source inventory | 実checkoutの前後照合、canonical tree digest、source bytes照合 | compiled binary/package/render/goldenの実検証は後続 |
| 通常product | Registry/Runtime/I2〜I5/C1/C2 | 今回は変更なし | 実SSH/Inspect-onlyから後続で接続 |
| Host renderer | 現行stubと共通compositor | 今回は変更なし | 実EGL/readback未実装。stub成功を証拠にしない |
| CI | HostのPython discovery、trusted-base scope | 既存routingへ追加テストを載せる | 正式I6 acceptance jobはまだ有効化しない |

設計書との対応は§2（profile/R・S・T）、§11（ケース集合）、§13（回収と失敗の分離）、
§14–15（schema/validator/CLI）、§18（テストとCI）である。
設計書§19の大きなPR案から、独立してレビューできる証拠・判定基盤を先に切り出した。

## 2. 固定manifestと期待outcome

profileは `i6-host-v1`、revisionは `eidolon-i6-host-acceptance-v1`、
verification IDsは `I3-T20` / `V11`。
[cases-v1.json](../../../scripts/fixtures/linux-desktop-v3-acceptance/cases-v1.json) の全rowと全assertionが必須である。
reportに `required` / `optional` を追加して必須集合を変更する方法はない。

| 集合 | 固定数 |
| --- | ---: |
| family | 28（N01–N11、F01–F12、C01–C05） |
| required variant | 87 |
| assertion参照 | 533 |

manifestのraw bytesのSHA-256をvalidatorがpinする。
空白だけの変更も新しいdigestとなるため、ケース変更は実行前にレビューし、コードのpinと一緒に更新する。

```text
e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33
```

各variantの意味と既存sourceの根拠は
[fixture README](../../../scripts/fixtures/linux-desktop-v3-acceptance/README.md) を参照する。
特に次の条件を事前に固定した。

- N03の未知app IDは `InvalidArgument`、変更scope/revision/旧catalogは `NotReady`、
  先行Launchが所有を保持する時点の二重Launchは `Busy`。queue受理とcommand完了を区別する。
- F11の4取消variantは回収不能faultを重ねず、元requestの完了とproduct `complete` を要求する。
- F12のcallback保持/dispose失敗は途中のexact-lease pendingを確認してから同leaseだけを修復する。
  旧Scene delete不足は修復せず `retained_pending` を要求する。
- F05の通常floodはterminalを待つ前にwriter FDを解放する。保持variantはproduct結果固定後に解放する。
- Session/journal/origin未割当の境界は、対応するallocation sealを要求する。
  存在しないfinalizerやorigin retirementを成功として埋めない。N11は旧v2専用の回収assertionを持つ。
- C01–C05は `contract_only` / `not_applicable`。C04は実native gateへの到達を必要とし、
  mock gateの成功を実product経路の証拠へ変換しない。

## 3. Reportとvalidator結果を分ける

実装の入口は次の3ファイルである。

- [linux_real_app_v3_evidence.py](../../../scripts/linux_real_app_v3_evidence.py): strict schema、固定manifest、source/owner/trace/artifact検査。
- [linux_real_app_v3_acceptance.py](../../../scripts/linux_real_app_v3_acceptance.py): 未実施report生成と保存reportのread-only再検査。
- [linux_real_app_check.py](../../../scripts/linux_real_app_check.py): stage dispatchとstage別終了値。

新規実行では `acceptance.json` と `acceptance-validation.json` を別々に作る。
標準出力は後者と同じvalidator結果である。
保存report再検査は既存ファイルを書き換えず、現在のcheckoutを期待sourceとして標準出力へ結果を出す。

| 内容 | 意味 |
| --- | --- |
| raw reportの `status` | producerの `pass` / `fail` / `incomplete` / `cancelled`。成功のauthorityにしない |
| raw caseの `status` | 上記に加え `not_run` を持つ。未実施variantをrequiredから除かない |
| validator結果の `status` | 検査から再計算。raw reportにはない `invalid` も返す |
| `evidence_contract_valid` | reportを実際に検査し、schema/参照/所有/整合性がinvalidでないこと。必須証拠の充足やproduct成功とは別 |
| `missing` | 必須variant/assertion/owner/census/artifact/source等の不足。skip成功にはしない |
| `unverified_assertions` | metadataを読めても、独立product oracleで意味をまだ検証していないassertion数 |
| `product_oracles_verified` | 今回は常にfalse。file/pixel/実protocolの独立oracleは後続 |
| `host_v3_acceptance_passed` | 今回は常にfalse。入力reportのtrueやexit=0で変更できない |

全87variantと全533assertionのmetadata・trace参照を揃えた合成reportでも、
独立oracleが未実装なので `I6_ORACLE_UNAVAILABLE` / exit 2 となる。
実際の構造検査結果をproductの意味的な検証から分け、今後もrequired集合を減らして成功へ変更しない。

`available`、`production_acceptance_passed`、`quest_verified`、`physical_input_verified` はfalseを維持する。
coverageはSDK=`synthetic`、Android保存/Quest/物理入力=`not_observed`、実reboot=`not_run`、旧APK upgrade=`I7_only`。
HostのS evidenceは実Meta SDK/Surface/JNI/Kotlin/E1 CAS/active marker/Android再起動の証明ではない。
reportをproductのadmissionや保存barrierを開ける入力にも使用しない。

## 4. Strict schemaと所有・資源検査

### 4.1 上限と型

| 対象 | 上限・型 |
| --- | --- |
| acceptance reportと専用writer | 1 MiB。既存A0 writerの128 KiB上限は維持 |
| schemaのfamily容量 | 64。標準profileのrequired集合は固定28 |
| variant配列等 | 最大256。標準manifestは87variant |
| artifact数/相対path | 256 / ASCII 240 bytes |
| 個々のartifact | 1 MiB以内 |
| trace event | 64 KiB以内 |
| case traceの合計 | 4,096 eventsかつ1 MiB以内 |
| JSON nesting | 既存B1 parserの32を維持 |
| 正のi64 ID/sequence | JSON numberではなくcanonical decimal string。0/先頭0/上限超過を拒否 |
| run/owner/target ID | 32桁hex。remote nonceは16桁hex |

duplicate/unknown key、NaN/Infinity、float、invalid UTF-8、lone surrogate、bool-as-int、trailing bytesを拒否する。
free-form errorは受け付けず、固定reason enumを使う。credential、private key、profile本文や任意exception本文を出力しない。

### 4.2 相関とtrace

case内のownerはruntime/admission epoch、Connect/Launch request、session、capture/context digest、
I/O generation、nonce、scope/native originに結び付く。targetはfull TargetKey相当の
session/window/scene/generationにCreateSeq/adapter IDを加えてownerへ結び付ける。
traceは宣言されたowner/targetの全fieldと一致する必要があり、別run/case/capture/sourceや旧generationを混ぜられない。
未割当fieldのnullは未割当の境界でのみ使い、Launch/native originが割当済みなら関連IDの欠落を拒否する。

同owner内のsequenceはtraceファイルをまたいでも重複できず、各trace内で単調増加する。
異なるclock domainのtimestampの大小に頼らず、owner単位のsequenceで順序を検査する。
overflowはcase/trace間で一致しなければinvalid、一致したoverflowでもincompleteにする。

runtime stopより前にorigin retirementとjournal finalization、または該当resourceのallocation sealが必要である。
Registry destroy AcceptedにはRuntime shutdownと生存censusを先行させる。
fixture rescueと環境破棄にはproduct result固定を先行させる。
この順序検査は証拠の構造を検査するものであり、trace event名だけから実product操作の成功を認定しない。

### 4.3 Census、artifact、失敗

censusはremote process/pidfd/pipe/profile lock/receipt、SSH channel/forward/router/I/O fence、
local Waypipe/pipe、Wayland client/object/surface/SHM、native window、全renderer/SDK generation、
journal finalizer、origin retirement、Runtime shutdown、Registry destroyを個別に追跡する。
`not_created` には同owner・該当resourceのallocation sealが必要である。
renderer/SDKは発行済み全target generationを列挙し、一つの新しいgenerationの回収で旧generationを隠さない。

artifactはprivate rootから各ancestorのdirectory FDを保持し、no-followで開く。
絶対path、`..`、空path成分、backslash、symlink、hard link、FIFO/device、他UID、group/other権限、
size/digest不一致、読み取り中のinode/ancestor/root差替えを拒否する。
読み取り上限とfstat検査を行い、artifactのrun/case/owner/target/sourceおよび参照eventを照合する。
欠落だけのartifactはincomplete、存在するが内容・参照が矛盾するartifactはinvalidとして区別する。
`opaque` artifactはbyte整合性のみを検査し、typed receiptやGUI oracleを充足した扱いにはしない。

部分合格を計算するときも、必須artifactと参照の欠落は、そのcase/variantの証拠不足として扱う。
公開する `missing` の `artifact/...`／`typed_receipt/...` 表記とは別にcaseへの帰属を保持し、
case名をprefixに持つ診断だけで充足を判定しない。宣言されていないartifact、宣言済みだが
実fileがないartifact、読めないtrace参照、意味的に検証できないtyped receiptは部分合格を成立させない。
別caseの証拠不足は、そのcaseに帰属させる。

`verified_case_oracles` へ入れるcaseは、独立oracleと必要な参照が成立し、case自身が
`status=pass` かつoperator cancelされていない必要がある。N01自身のcancelは
`n01_inspect_only_passed=false` とし、全体の `cancelled / exit 130` と矛盾させない。
完了済みN01とは別のcaseのcancelは、run全体をcancelledにするが、N01の完了済み証拠を取り消さない。
N01 smokeでは `environment_disposal` の未完了だけを部分合格の対象外とする既存境界を保ち、
正式I6にはその不足を残す。その他の欠落をこの例外へ含めない。

`first_case_failure` と `cleanup_failures[]` は別に保持する。
期待されたproduct failureは `first_product_failure` に記録し、それ自体を予期しないcase failureにしない。
負例の正しいUnknown/Pending保持とrescue完了は両立するが、product completeへ昇格しない。

## 5. Source provenanceの範囲

共通のsource recordは実checkout commit、tracked path/mode/contentからのcanonical tree SHA-256、dirty状態、
build features、任意のPR head識別を持つ。read-onlyの `source_snapshot` はbinaryを検査せず、
`build_features: []` を「binary buildについて主張しないcheckout観測」のmarkerとして返す。
feature無しでbinaryをbuildしたという証拠ではない。treeのencodingは、pathのraw bytesをhex化した値、Git mode、
content SHA-256のrowをsortしたcanonical JSONである。symlinkはtarget bytesをhashし、参照先を追わない。
採取中のHEAD/index/status変化を検査し、実行前後とvalidator側期待sourceを照合する。
dirty/untrackedは非成功、矛盾するsourceや前後の変更はinvalidとなる。

N01 runnerの `native.build_source` はbuild時の実checkoutに加えて、固定Cargo entrypointが必須にする
product feature `build_features: ["host-acceptance"]` を埋め込む。build helperは実Cargo.tomlの
`eidolon-android`／`eidolon-core`／`eidolon-transport` の必須dependency宣言を確認する。
N01 oracleはnative側のfeatureをこの固定集合、validator側checkoutのmarkerを空集合とそれぞれ照合し、
残る `commit`／`tree_sha256`／`dirty`／`pr_head` はcanonical完全一致を要求する。
featureの違いを理由に物理sourceの差異を許容しない。一般acceptance CLIにbuild機能やfeature指定optionは追加しない。

`inputs.compiled_source_hashes` は、既存15-source inventoryが指す **現在のsource bytes** を照合するfieldである。
名前にcompiledが含まれていても、今回のPython検査が実行binaryからhashを抽出したことや、
そのbinaryが同じbytesでbuildされたことを証明するものではない。
inventory自体のdigestと、include_bytes入力に相当するsource hashの一致までを検査する。

package/bundle/render/goldenのfieldはformatと前後一致を検査するが、
実package、immutable bundle、compiler成果物、Mesa/font/theme fingerprint、独立goldenの実物照合はまだ行わない。
架空のfingerprintやrun内で生成したexpected画像をlockとして登録していない。
後続では実checkout SHAとPR head/synthetic merge SHAを明示し、実物のoracleを追加する必要がある。

## 6. CLIと終了値

repository rootで、新しいprivate artifact directoryを指定する。
次のコマンドは今回の実装では **exit 2が正常な未完了結果** であり、実アプリを起動しない。

```sh
i6_artifacts="$(mktemp -d /tmp/eidolon-i6.XXXXXX)"
python3 scripts/check-linux-real-app --stage acceptance --artifact-root "$i6_artifacts"
```

source前後snapshot、全87variantの `not_run`、未実装runnerのreasonをraw reportに残し、独立validatorへ渡す。
artifact rootは絶対path・本人所有・private・空である必要がある。既存resultを上書きしない。
`--reviewed-package` を付けても、現在のacceptanceでpackage検証やproduct実行が有効になるわけではない。

保存reportは、同じcheckoutからread-onlyで再検査できる。

```sh
python3 scripts/check-linux-real-app --stage acceptance \
  --artifact-root "$i6_artifacts" \
  --acceptance-report "$i6_artifacts/acceptance.json"
```

| exit | validator結果 | 意味 |
| --- | --- | --- |
| 0 | pass | 全requiredの独立oracle成立時の将来の成功。今回のacceptance実装からは返さない |
| 1 | fail | 予期しないcase/assertion failure、cleanup failure、固定した期待outcomeとの不一致 |
| 2 | incomplete | 未実施、環境/source不足、required証拠欠落、trace overflow、product oracle未実装 |
| 3 | invalid | strict schema、artifact、source、owner/target/traceの矛盾・改変 |
| 130 | cancelled | operator cancel。cleanup完了を推定しない |

report検査では **invalid → cancel → case failure → incomplete** の優先順位を維持する。
CLI開始前のfresh-root不正/非空は既存契約どおりexit 2で拒否し、未検証reportを有効とは報告しない。
KeyboardInterruptからのcancel結果も未検証の証拠を有効と認定しない。

feasibilityは従来の `a0_passed` による0/2を維持する。
`--acceptance-report` をfeasibilityへ指定するとargument errorにする。
A0成功がacceptanceの終了値やHost passを決めることはない。

## 7. 検証と環境上の未実施範囲

この単位の検証入口は次のとおり。最終実行件数・結果・対象head SHAとCI状態はPR本文に記録する。

```sh
python3 -m unittest discover -s scripts/tests -p 'test_linux_real_app_v3*.py' -v
python3 -m unittest discover -s scripts/tests -p test_linux_real_app_check.py -v
python3 -m unittest discover -s scripts/tests -p test_ci_scope.py -v
python3 scripts/docs.py validate
python3 scripts/check-design
python3 scripts/docs.py context --feature linux-desktop-v3 --task I2
python3 scripts/docs.py context --feature linux-desktop-v3 --task I4
git diff --check
```

新規テストはmanifest削減/期待値変更拒否、strict schema、所有相関、trace/census、no-follow artifact、
失敗と回収失敗の分離、完全metadataでもproduct oracle未確認なら非成功となる条件、CLI stage互換を扱う。
合成traceやfixtureはTの契約検査であり、R/Sの実行結果ではない。

ローカル環境の存在確認ではPython/Git、`sshd`、`mousepad`、Javaを確認した。
現PATHではcargo/rustc/Waypipe/eglinfo/kotlincを確認できず、
`eidolon-driver` / `eidolon-app` の専用passwd accountも存在しなかった。
実効UIDは1000であり、provisioning用の昇格権限の可否は確認していない。
sshd/Mousepadが存在することから、固定版、実接続、実C1 HOME/祖先所有、package適合性が成立したとは判断しない。

今回、実SSH、EGL/pbuffer/readback/fence、pidfd fault broker、GUI golden採取、Rust/JVMの実行は未実施である。
通常desktop/HOME/profile/Documents/settingsや共有ADBにはfaultを注入していない。
これらを環境skip成功にせず、後続の必要な実装単位で設計書§21の最小実験を行う。

CIは既存の[change-scope policy](../../ci-change-scope-20261113.md)を維持する。
`scripts/**` の変更なのでHost/Android/GIO/Mousepadの既存routing対象であり、Python検査はHost discoveryへ入る。
docs-onlyの重いbuildを増やさず、required checks、Documentation integrity、CodeQL、trusted-base classifierを弱めない。
このPRで正式 `Normal v3 Host acceptance (I3-T20/V11)` jobを有効化しない。

## 8. 次PRの開始条件と完了境界

次は **N01: 実SSH/Inspect-only→Stop** を独立単位にできる。
この証拠基盤を含む最新main、または依存関係を明記したstacked branchをbaseにする。
先行PRを無断でmergeしない。

1. 専用非root accountと実passwd HOME、専用sshd、host-key pin、credential所有を持つdisposable環境を準備する。
2. 既存builder/通常parser/Registryから実C2 Inspectを実行し、Start=0とNoLocalAllocation sealを採取する。
3. bounded traceを実観測へ接続し、同captureのStop、必要なlive Recover、I2 finalizerを進める。
4. 旧ownerの回収証拠を保持したままRuntime全停止へ進み、最後のcensusと実Registry destroy Acceptedを記録する。
5. N01専用の独立protocol/ownership oracleを実装し、N01 smokeの成功と残りrequiredが未実施の正式acceptanceを分ける。

N01はappをspawnせず、実frame/Readyを要求しないため、実Host EGLの完成を前提にする必要はない。
NoLocalAllocationを空配列やprocess exitに置き換えず、origin未割当と実helper/journal回収を区別する。
続いて実描画・通常Launch、GUI/永続化、fault/Recover/v2、全体CIを進める。

### マージ後の次セッション用プロンプト

```text
tin-machine/eidolon-wayland のI6開発を継続してください。
今回までの証拠・判定基盤PRがmerge済みか実際に確認し、必ず最新mainを取得してください。
AGENTS.md、docs/README.md、docs/features/linux-desktop-v3/README.mdから現在の文書配置を確認し、
移動済み文書はGit履歴と入口から追跡してください。古いpathへ作り直さないでください。
I6詳細設計と docs/features/linux-desktop-v3/i6-evidence-validation.md、固定manifestを読み、
I2/I4 contextを別々に取得してください。I6はmanual discoveryです。

最初の候補はN01の実SSH/Inspect-onlyです。最新source/open PRとの差分を確認してから、
専用account/実passwd HOME/sshd/通常config/Registry/未改変C2/bounded traceを接続し、
Start=0、NoLocalAllocation seal、同capture Stop/必要なRecover/I2 finalizer、
Runtime全停止、最後のcensus、実Registry destroy Acceptedを検証してください。
N01には実EGL/frame/Readyを混ぜず、N01 smokeと正式I6全体の合格を区別してください。
全28family/87variant/533assertionをrequiredに保持し、未実施ケースや未実装oracleを成功扱いしないでください。
実測した環境制約を記録し、独立に可能な実装・テスト・commit・push・新規PR・CI確認まで進めてください。
PRは別途依頼するまでmergeしないでください。先行未mergeなら依存baseを明記してください。
```
