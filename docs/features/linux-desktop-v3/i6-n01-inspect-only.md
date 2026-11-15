# I6 第2実装単位: N01 実 SSH／Inspect-only

以下の開始時記録と修正前の実測は、その時点の状態を保持する。
同日のユーザー依頼による C2 synthetic HOME 修正は末尾の追記を参照する。

2026-11-15（日本時間）。実装の基準とした最新 main は
`0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4`。
[PR #33](https://github.com/tin-machine/eidolon-wayland/pull/33) の merge 状態を GitHub で確認し、
その証拠基盤を再利用して N01 を実装する。初回確認では #33 は open だったが、作業中の merge 後に
最新 main へ fast-forward した。その後の再取得で PR #30（構造解析）の merge と最新 main
`480e1b5fe2fedcc69d0be0c9eb3d5bb931635bdd` を確認した。追加は文書と構造解析で、N01 実装との重複はない。
当時の open PR #35（Python Deep 解析）も本単位の依存ではなかった。その後 #35 の merge
`97dfdbbc13e00227b8285d09b0e880cdc6b6b130` も取り込み、C2 修正開始時の最新 main と一致することを確認した。

文書は移動していなかった。[入口](../../README.md)と Git 履歴を確認し、
I2 と I4 の resolver context を**別々に取得**した。両者の proposed／drift=check_required を
実装承認へ読み替えていない。I6 は引き続き manual discovery とする。

## 完了境界

N01 が検証するのは、専用環境での通常 Registry → Runtime → Controller → 実 SSH → 通常 C2 の
Inspect → Stop → 同 capture の finalizer → Runtime 停止 → 生存 census → 実 destroy の経路である。
Mousepad や GTK demo の Launch は送らず、**Start 送信数 0** を product の送信境界で数える。

| 判定 | 意味 |
| --- | --- |
| `n01_inspect_only_passed` | N01 の8 assertionを独立 oracle が照合した product smoke の結果 |
| `verified_case_oracles` | 意味的な照合を実施した case の集合。N01のみ実装 |
| `host_v3_acceptance_passed` | 正式 I6。N01だけでは false のまま |
| `product_oracles_verified` | I6全体の oracle。未実装ケースを含むため false のまま |
| N01 wrapper の exit 0 | N01成功と、正式acceptanceが未完了であることを両方確認 |
| `acceptance-validation.json` の exit 2 | ほかの required variant／未実装 oracle が残る正式I6の未完了 |

固定 manifest は **28 family／87 required variant／533 assertion参照**を維持する。
digest `e2d0d80c35fc5494415a627a7a11d162b127fecc38646535afacf9f08ff40f33` は変更しない。
実 EGL、GUI、file/pixel oracle、実 SDK、Android 保存、I7/V12、Quest/H は検証していない。
Host fixture の `admission_epoch: "1"` はこの実行の ordinal であり、Android coordinator の epoch の証拠ではない。

## 実行構成と source の束縛

| 実装 | 責務 |
| --- | --- |
| [Host runner](../../../scripts/fixtures/linux-real-app-v3-host/src/main.rs) | 通常 parser／Registry／credential scope／command／poll／destroy を使用。private credential FDを消費 |
| [build identity](../../../scripts/fixtures/linux-real-app-v3-host/build_identity.py) | build時の実 Git commit、tracked tree digest、dirty状態、必須 `host-acceptance` featureをrunnerへ埋込。descriptorの自己申告を使わない |
| [Host trace](../../../native/transport/src/host_acceptance.rs) | default-off `host-acceptance` featureの読み取り専用・boundedな観測。public setterなし |
| [supervisor](../../../scripts/linux_real_app_v3_n01.py) | 明示したdisposable containerだけをprovision。専用account、実HOME、鍵、sshd、実FS／process census |
| [N01 oracle](../../../scripts/linux_real_app_v3_n01_oracle.py) | Python B1契約を用いたcatalog／wire／owner／capture／回収順序の独立照合 |
| [既存validator](../../../scripts/linux_real_app_v3_evidence.py) | `n01_receipt` typed artifactを検証。ほかのrequired行を保持 |
| [Docker wrapper](../../../scripts/ci/check-linux-desktop-v3-n01) | clean commitのcloneからimageをbuild。container実行・限定artifact採取・外側の再照合・破棄の試行 |
| [Dockerfile](../../../scripts/fixtures/linux-desktop-v3-acceptance/Dockerfile.n01) | Ubuntu 24.04、Rust 1.95.0、既存の固定native／Waypipe builderとrunner build／test |
| [image準備](../../../scripts/fixtures/linux-desktop-v3-acceptance/prepare-n01-image) | 別の実Docker containerで未改変の固定Mousepad builder／install／inventoryを実行 |

source inventoryの15ファイルとcompiled source tableを実bytesで照合する。
一般のread-only `source_snapshot` の `build_features: []` はbinary buildを主張しないcheckout markerである。
N01の `native.build_source.build_features` は、固定Cargo entrypointの実dependency宣言を確認した
`["host-acceptance"]` でなければならない。oracleは両markerを区別して厳密に照合し、
`commit`／`tree_sha256`／`dirty`／`pr_head` は全fieldのcanonical完全一致を要求する。
空または未知のnative feature、物理sourceの不一致はinvalid、一致していてもdirtyなsourceは非成功である。
remote bundleは既存のbuilderが実passwd HOME配下へ生成し、`--print-config --build-id` を前後に実行する。
通常 `Profile`、`StorageCapture`、`StorageRecord`、`ConnectionConfig`、`RuntimeBinding`、
`RuntimeConfig` のparserを通す。profile本文・秘密鍵は公開artifactへ含めない。

コンパイル用 `eidolon-build` と製品実行accountは分離する。image内にはdriver/app accountを作らず、
実行開始時に `eidolon-driver`（UID12001）と `eidolon-app`（UID12002）の不在とHOMEの不在を確認して作る。
HOMEは `/home/eidolon-driver` と `/home/eidolon-app`、mode 0700、祖先はroot所有・書込不可である。
環境変数HOMEの差替えや単一UIDのnamespaceを、C1の成立証拠として扱わない。

BuildKitのbuild stepでは既存Mousepad builderのdisposable環境guardを満たさなかったため、
package準備は `--network bridge` の別のfresh Docker containerで実行する。
非rootの `eidolon-build` が固定sourceを取得・buildし、rootがpackageをinstall、非rootがinventoryを採取する。
既存guardは変更せず、Docker markerやGitHub環境を偽装しない。準備前後にdriver/app accountとHOMEの不在を確認し、
準備containerの停止とexit 0を確認してからimageをcommitする。元のENTRYPOINTを復元して照合する。
最小Ubuntu imageでは、package installが成功してもdoc配下のbuild evidenceが欠落することをCIで観測した。
準備時は実dpkgのpath filterを記録し、当該packageのinstallに限って元のMousepad doc payloadを保持する。
build evidence JSON／config.h／generated Makefileは、実debのpayloadとinstall後のbytesを直接照合する。
欠落した証拠を再生成したり、inspectorの条件を緩めたりはしない。

N01の実行containerは完成imageから新規作成し、`--network none`、loopback sshd、PID上限256、メモリ6GiB、CPU4。
host PID／network namespace、通常HOME、desktop socket、ADB、Docker socketを共有しない。
rootはprovisioning／sshd／独立観測を担当し、Registry runnerとC2は異なる非root accountで動かす。
SSH host keyとclient keyを実行ごとに生成し、通常profileへ実host keyをpinする。TOFUやForceCommandは使わない。
Ubuntuのtag自体をreview済みimmutable image lockとは呼ばず、外側の `container.json` に実image IDを記録する。

## Stop と回収の観測

同じcaptureについて、少なくとも次の連続した証拠を要求する。

1. Journal reservationと実C2のPrepared／Inspect／catalog。request、session、context、scope、pins、sourceが一致する。
2. 未割当launch gateを封鎖したNoLocalAllocation seal。Stop送信より先に成立する。
3. 通常DisconnectによるStop、remote terminal、channel close、router／I/O retirement。
4. 必要な場合は保持中の同Runtime／同captureでlive Recoverし、その実receiptとgeneration bridgeを照合する。
5. journal unlinkとdirectory fsync、および同capture finalizerの実publication。
6. origin retirement条件を満たした後のRuntime全停止、最後の生存snapshot、実 `Registry::destroy == Accepted`、Registry数0。

新Runtimeの空ledger、process exit、Drop、timeout、Stale、journalの不在だけを回収証拠にしない。
runnerは失敗時も元Runtimeで回収を進め、retirementが成立しなければ通常のRuntime全停止／destroy成功を記録しない。
最初の失敗と回収失敗を別に保持する。

supervisorはrunner終了後、**専用sshdを停止する前**にapp／driver processとsshd descendantを数える。
OS側のreapを最大3秒read-onlyで観測するが、productのdeadline／leaseを変えず、残存processをkillして0へ作り替えない。
実profile.lockとHOMEのflock、journalのowner.lock／io.lock、pending.jsonも独立に観測する。
lock file欠落は「解放済み」に変換しない。これらのFS観測だけでnative finalizerを代替しない。

観測traceは最大4,096 event／1MiB、各eventは64KiB以内。producerをblockせず、overflow／取得競合はstickyな不足として残る。
別owner／別capture、Start送信、stop前seal欠落、finalizer前全停止、census前destroy、実complete不足は成功にならない。

通常のRegistry snapshotにはUI座標等の浮動小数点が含まれる。証拠の整数限定JSON契約を維持するため、
runnerは同じ実snapshotからcensus必須fieldだけを選んで保存する。元の値と型を保ち、非zeroの所有数や
残存app／panel／targetを消さない。制御の待機判定には通常の完全なsnapshotを使用する。

fixtureのsshd停止とcontainer削除は、productの観測結果とは分けて試行する。
失敗経路ではproduct回収が未成立でもfixture救済を行うが、これをproduct回収成功へ読み替えない。
内部reportの `environment_disposal` は `not_yet` のままである。
外側wrapperは削除前のcontainer終了codeと救済判定を記録し、EXIT trapでcontainer／imageの削除を試行する。
削除成功のreceiptは採取していないため、N01 wrapperのexit 0は環境破棄完了を証明しない。

## 実行方法

Linux上でDockerを利用できる、cleanでcommit済みのcheckoutから実行する。
この明示wrapperは環境を作るため、通常のacceptance CLIやread-only inspectorから自動的には呼ばない。

```sh
scripts/ci/check-linux-desktop-v3-n01 --artifact-root /absolute/private/empty/n01-artifacts
```

既存directoryの内容を上書きしない。wrapperはGitのclean cloneだけをDockerへ渡し、未追跡fileや秘密情報をbuild contextへ含めない。
正常時の主要出力は次のとおり。

- `n01-receipt.json`: native観測と独立environment観測を同run／owner／sourceへ束縛したtyped receipt。
- `n01-validation.json`: N01のみの独立判定と正式I6未完了の境界。
- `acceptance.json`: 全87variantを残したraw report。
- `acceptance-validation.json`: raw reportとartifactから再計算した正式I6の判定。
- `container.json`: image ID、実行source commit、attachとcontainerの終了code、停止救済の判定。削除完了の証明は含まない。
- `n01-native.json`: 失敗解析用のoptionalなnative観測。必須receiptやassertionの代替にはしない。

環境不足や実行失敗時はN01成功receiptを生成しない。出力された固定reason、`fixture_stage`、未実施行を確認する。
runnerがreceiptを返せない場合も、`diagnostics` に終了code／signal、経過時間、固定stage、限定したpanic／loader分類を残す。
解析対象のstderrは末尾2,048 bytesだけで、本文・panic message・thread名・profile／鍵は保存しない。
これらの診断はproduct receiptやoracleの代わりにはしない。
native JSON自体が不正な場合はbyte数とSHA-256、固定reasonだけを保持し、未解析本文を公開しない。
N01の入力判定は `invalid / exit 3`、正式acceptanceの未完了判定は別々に記録する。
不正なtyped receiptも保存し、独立validatorが `invalid / exit 3` を決定する。環境不足へ読み替えない。
同一の実行sourceをcheckoutし、取得したartifactをprivateに保持したまま既存CLIで再照合できる。

```sh
python3 scripts/check-linux-real-app --stage acceptance \
  --artifact-root /absolute/private/n01-artifacts \
  --acceptance-report /absolute/private/n01-artifacts/acceptance.json
```

CIがsynthetic merge commitで実行した場合は、PR headではなくreceiptの実source commitと一致させる。
現在のPR workflowはmerge用checkoutを使用し、`source.commit` と `container.json.source_commit` に実行commitを記録する。
`source.pr_head` は `null` であり、PR headとの対応はGitHub Actionsのrun／PRメタデータで別に確認する。
receipt自体がPR headを束縛したとは扱わない。
source／artifactの相違を無視するオプションは設けない。

## 検証記録と残件

この実装を開始したローカル環境では、UID1000、capabilitiesなし、NoNewPrivs=1で、
専用2account・passwd書込権限・Docker／sudo・Rust／Waypipe実行fileがなかった。
単一UIDのuser namespaceは作れたが、必要な2UIDの代替にはしていない。
Rust公式配布への通常の直接HTTP確認も約5秒でtimeoutした。既存HOME／desktop／ADBには触れていない。
したがって、ローカルのPython拒否検査と、CI containerの実Rust／SSH結果は分けて記録する。

最終head、実施件数、実SSHの結果、CI状態はPR本文に記載する。
[既存のCI scope](../../ci-change-scope-20261113.md)と必須checkを維持し、
追加jobは `N01 real SSH / Inspect-only (partial I6)` と命名する。正式 `v3_acceptance` jobの成功とは呼ばない。

後続は通常Launchと実EGL/readback、GUI／設定永続化、故障注入／v2互換、残りrequired集合のoracleである。
I7/V12とHの証拠は引き続き独立して必要になる。

## 2026-11-15: 未改変C2のsettings probeによるN01未達

本節は C2 修正前の実測記録であり、末尾の修正後 source の成否を示さない。

PR head `3099a2dea07646d3076f0b92a61e169079f26feb` に対する
[N01 job 132737706508](https://github.com/tin-machine/eidolon-wayland/actions/runs/43689526255/job/132737706508) は、
実行source `cdd6629edefe039a39a597d5b88340fef4846496` で実SSHと未改変C2に到達した。
[artifact 14152345487](https://github.com/tin-machine/eidolon-wayland/actions/runs/43689526255/artifacts/14152345487) の
ZIP SHA-256は `08fd947eb41505b7cc286a5f583ddec3880b6d7f94c96e60561a20c62ee5a880`。
**この実行のN01 smokeは不合格であり、catalogの成功を観測していない。**

実traceには `Prepared`、`Inspect` 送信、その後の
`pending { reason: tool_inspection, public_reason: APP_SETTINGS_UNVERIFIED }` がある。
`Start=0` で、immutable bundleの実行前後の入力も一致した。
失敗後には同Runtime／同captureのRecover、finalizer、Runtime shutdown、最後のcensus、
実 `Registry::destroy == Accepted` とRegistry数0を観測した。
これらの失敗後の回収観測を、catalog、NoLocalAllocation seal、通常Inspect→Stopの成功には読み替えない。
独立oracleはcatalog／disconnect／seal等を不足として残し、verified assertionは0、
`n01_inspect_only_passed=false`、正式acceptanceもfalseである。

失敗原因は、固定helperのsynthetic probeと固定settings backendのHOME契約の矛盾である。

| 固定source | 実際の条件 |
| --- | --- |
| [C1 profile environment](https://github.com/tin-machine/eidolon-wayland/blob/cdd6629edefe039a39a597d5b88340fef4846496/scripts/linux/eidolon_linux_persistent_profile.py) | `HOME` はpersistent profileの `home`、`XDG_CONFIG_HOME` は同profileの `config` |
| [C2 run_settings_probe](https://github.com/tin-machine/eidolon-wayland/blob/cdd6629edefe039a39a597d5b88340fef4846496/scripts/linux/eidolon_linux_desktop_v3.py) | Inspectの `synthetic=True` ではsession runtime配下に `probe/config` を作り、`XDG_CONFIG_HOME` だけを変更する。`HOME` は元のままで、`probe/home` も作らない |
| [settings backend profile_filename](https://github.com/tin-machine/eidolon-wayland/blob/cdd6629edefe039a39a597d5b88340fef4846496/scripts/linux/eidolon_settings_backend.c) | `HOME == dirname(XDG_CONFIG_HOME)/home` と、その実private directoryの存在を必須とする。不成立なら `APP_SETTINGS_UNVERIFIED`／exit 78で停止する |

[固定C2 session](https://github.com/tin-machine/eidolon-wayland/blob/cdd6629edefe039a39a597d5b88340fef4846496/scripts/linux/eidolon_linux_desktop_session_v3.py) の通常Inspectは
このsynthetic経路を使用するため、専用sshd、passwd HOME、package、GIO moduleを正しく準備しても
上記の不一致は残る。環境変数の外側からの上書きやdirectoryの追加では、helper内部で組み立てる
`HOME` と `XDG_CONFIG_HOME` の文字列不一致を正当に解消できない。
この時点の実装はC2、settings backend、source inventory／pins、product deadline／leaseを変更していない。
当時の未改変C2という境界でこのblockerを解消することはできず、source変更とそのpin更新が別途必要だった。

[修正前のfixture回帰](https://github.com/tin-machine/eidolon-wayland/blob/949fa503948890647655ad5901f10d5dced0d7dd/scripts/tests/test_i6_n01_settings_blocker.py) は無変更の
`run_settings_probe` のspawn境界を捕捉し、`HOME=persistent/home`、
`XDG_CONFIG_HOME=runtime/probe/config`、必須の `runtime/probe/home` が不在であることを検査する。
通常HOMEやpersistent settingsへ試験値を書かず、probe実行前に停止する。
この回帰のpassは既知blockerの再現を意味し、実GIO probe、N01、GUI／EGL／SDK／Android保存の成功ではない。
ローカルには `gio-2.0`／`gmodule-2.0` の開発用pkg-config情報がなく、実GIO moduleの再実行は未実施である。
実runtimeの失敗は上記CI receipt、HOME不整合の詳細はこのspawn観測と固定C sourceの条件を根拠とする。

```sh
# 修正前 head 949fa50 の checkout での再現用。現在の回帰は次節を参照。
python3 -m unittest discover -s scripts/tests -p test_i6_n01_settings_blocker.py -v
```

28 family／87 required variant／533 assertion参照と未実装oracleを保持する。
この既知の不合格をCI skip、allow-failure、成功receiptの補填で隠さない。

## 2026-11-15 追記: C2 synthetic probe の専用 HOME 修正

ユーザーの追加依頼により、未改変 C2 という初回実装の境界をこの修正に限って更新した。
`run_settings_probe(synthetic=True)` は既存の private な `runtime/probe` 配下へ
`home` を mode 0700 で作成し、probe に渡す `HOME` をその path に設定する。
`XDG_CONFIG_HOME=runtime/probe/config` と同じ root に揃え、backend が要求する
HOME の文字列・実 directory・所有者・mode の条件を満たす。
元の環境辞書、persistent profile、non-synthetic probe の HOME は変更しない。
settings backend の拒否条件、product deadline／lease、frozen v2、固定 case manifest は維持する。

### Source の更新順

[統合計画 §2](../../linux-real-app-v3-integration-plan-20261111.md) に従い、C2 本体と対応回帰を
先に commit `b32eaf18bf0613e928c036a884fc19aac38a68b3` として確定し、後続 commit の
`scripts/linux-desktop-v3-sources.json` で `integration.c2_commit` をその実 SHA へ更新する。
C1 基点と15 source／generated の集合は変更しない。inventory 自身も15 sourceの一つであり、
最終 checkout から native runner と immutable bundle を再 build する必要がある。

Rust の `include_bytes!`／`current_hashes()`、Python の source snapshot、通常 builder が
修正 C2 と更新 inventory の実 bytes をそれぞれ照合し、新しい bundle manifest／build ID／pins を生成する。
既存の source mismatch 拒否を維持し、旧 binary／bundle／receipt を新 source の証拠として流用しない。

### 回帰と実測の境界

- [C2 環境回帰](../../../scripts/tests/test_i6_n01_settings_probe.py): 実 C2 関数の spawn 境界で、
  synthetic HOME／config の整合、private ownership／mode、元 profile と non-synthetic 環境の不変性を確認する。
  この境界検査は実 GIO／SSH の成功を意味しない。
- [実 GIO 回帰](../../../scripts/tests/test_linux_settings_backend.py): 同じ C2 が生成した環境で
  実 backend／probe の成功、旧 HOME・非 private HOME・HOME 欠落の exit 78 拒否、persistent keyfile 不変を検査する。
  CI の `EIDOLON_REQUIRE_GIO_TESTS=1` では依存不足を skip 成功にしない。ローカルの開発依存不足は別に記録する。
- N01 の正常 Stop では実 product の `RemoteReport.reason="stop"` と wire の `Stopped.reason="stop"` を
  phase／stop_reason と合わせて照合する。未知の理由や不一致を正常 Stop へ読み替えない。
  同 owner／capture、seal→Stop→finalizer→全停止→census→destroy の要件は維持する。

```sh
python3 -m unittest discover -s scripts/tests -p test_i6_n01_settings_probe.py -v
EIDOLON_REQUIRE_GIO_TESTS=1 python3 -m unittest discover -s scripts/tests -p test_linux_settings_backend.py -v
```

修正後の実 N01 の対象 head、実行 source、receipt digest、CI の成否は PR #34 に記録する。
catalog が取得できても、独立 oracle の全 N01 条件を満たすまで成功とはしない。
N01 が成功した場合も、他86 required variant と未実装 oracle は未完了のまま保持する。
