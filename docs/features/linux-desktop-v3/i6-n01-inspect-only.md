# I6 第2実装単位: N01 実 SSH／Inspect-only

2026-11-15（日本時間）。実装の基準とした最新 main は
`0a0454f0e4f6c6dce6407a4dfb2c3f0b67385fd4`。
[PR #33](https://github.com/tin-machine/eidolon-wayland/pull/33) の merge 状態を GitHub で確認し、
その証拠基盤を再利用して N01 を実装する。初回確認では #33 は open だったが、作業中の merge 後に
最新 main へ fast-forward した。関連 open PR #30（構造解析）は本単位の依存ではない。

文書は移動していなかった。[入口](../../README.md)と Git 履歴を確認し、
I2 と I4 の resolver context を**別々に取得**した。両者の proposed／drift=check_required を
実装承認へ読み替えていない。I6 は引き続き manual discovery とする。

## 完了境界

N01 が検証するのは、専用環境での通常 Registry → Runtime → Controller → 実 SSH → 未改変 C2 の
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
| [build identity](../../../scripts/fixtures/linux-real-app-v3-host/build_identity.py) | build時の実 Git commit、tracked tree digest、dirty状態をrunnerへ埋込。descriptorの自己申告を使わない |
| [Host trace](../../../native/transport/src/host_acceptance.rs) | default-off `host-acceptance` featureの読み取り専用・boundedな観測。public setterなし |
| [supervisor](../../../scripts/linux_real_app_v3_n01.py) | 明示したdisposable containerだけをprovision。専用account、実HOME、鍵、sshd、実FS／process census |
| [N01 oracle](../../../scripts/linux_real_app_v3_n01_oracle.py) | Python B1契約を用いたcatalog／wire／owner／capture／回収順序の独立照合 |
| [既存validator](../../../scripts/linux_real_app_v3_evidence.py) | `n01_receipt` typed artifactを検証。ほかのrequired行を保持 |
| [Docker wrapper](../../../scripts/ci/check-linux-desktop-v3-n01) | clean commitのcloneからimageをbuild。container実行・限定artifact採取・外側の再照合・破棄の試行 |
| [Dockerfile](../../../scripts/fixtures/linux-desktop-v3-acceptance/Dockerfile.n01) | Ubuntu 24.04、Rust 1.95.0、既存の固定native／Waypipe builderとrunner build／test |
| [image準備](../../../scripts/fixtures/linux-desktop-v3-acceptance/prepare-n01-image) | 別の実Docker containerで未改変の固定Mousepad builder／install／inventoryを実行 |

source inventoryの15ファイルとcompiled source tableを実bytesで照合する。
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
