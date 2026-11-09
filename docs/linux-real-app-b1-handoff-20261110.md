# L7a B1: v3共通契約の確定と並行開発への引き継ぎ

2026-11-10（日本時間）。状態: **A0はPR #6で実測成功・mainへマージ済み。B1は未実装で、次に作成するPRの範囲を本書で定める**。
本書の追加はB1の実装やv3通常起動の完了を意味しない。B1/B2/C1/C2/E1等は計画上の分割名であり、GitHubのPR番号ではない。

目標はQuest 3の広い作業空間で複数のLinuxアプリを扱うこと。
次は小さなB1を確定し、B2（bundle/protocol）、C1→C2（永続profile/通常helper）、E1（Android保存モデル）を別branchで並行開発する。
今回のB1依頼では後続の製品実装まで始めず、各担当が共有できる実行可能な契約を完成させる。

## 1. 引き継ぎ時点の確定した状態

最初に最新main、関連PR、CI、現在存在するAGENTS.md等を再確認する。
以下は本書作成時の基準であり、このcommitへ戻して作業する指示ではない。

| 項目 | 記録 |
| --- | --- |
| マージ済みPR | [PR #6](https://github.com/tin-machine/eidolon-wayland/pull/6) |
| merge commit / 本書作成前のmain | [`78e53117c0b9a0e82bf2e5ef70f2cbb127de978d`](https://github.com/tin-machine/eidolon-wayland/commit/78e53117c0b9a0e82bf2e5ef70f2cbb127de978d) |
| 検証済みPR head | `fa68708ff4dc1f8f62f6d73178ea78655f5443f9` |
| PR CIがcheckoutしたmerge | `8b79b6cf0fc2cb05d1c38a584150745d54dc0491` |
| 上記2 merge commitの共通tree | `ce59c4eac22190b4c5e54fd9c98532078f0d5abd` |
| PRの専用CI | [42645647118](https://github.com/tin-machine/eidolon-wayland/actions/runs/42645647118)、成功 |
| PRのhost/Android CI | [42645647229](https://github.com/tin-machine/eidolon-wayland/actions/runs/42645647229)、成功 |
| マージ後mainの専用CI | [42651282557](https://github.com/tin-machine/eidolon-wayland/actions/runs/42651282557)、成功 |
| マージ後mainのhost/Android CI | [42651282523](https://github.com/tin-machine/eidolon-wayland/actions/runs/42651282523)、成功 |

PR headとmergeの差は、並行してmainへ入ったREADME更新である。PR head自体とmergeのtree全体が一致したとは記録しない。
上記のCI成功はB1の新しいheadの検証へ流用しない。

A0は `APP_A0_PASSED` / `a0_passed=true`。
`available=false` / `production_acceptance_passed=false` / `quest_verified=false`、pendingは `v3_acceptance` / `quest` のままである。
実測の内訳・skip・限定した成功範囲はPR #6本文と[GUI設定の実装記録](linux-real-app-a0-gui-settings-20261109.md)を参照する。
PR headでは専用Python/GIO 243件とRust fixture 9件が成功し、125秒先行保持後の新しい応答は126,880 msだった。
通常Close/Stop/実Mousepad異常終了後の再起動、復元候補の起動前保護拒否、先行instanceの独立、全worker回収も実測している。

現在の[acceptance入口](../scripts/linux_real_app_check.py)は依然 `APP_V3_NOT_IMPLEMENTED`。
A0の合成GUI observer、fixture、receiptが存在しても、通常helperやQuest表示が完成したとは扱わない。

## 2. 読む順序と既存実装

1. 本書でB1の成果物・境界・後続への受渡しを確認する。
2. [L7a詳細設計](linux-real-app-l7a-design-20261109.md)の版/pins、永続state、origin、表示期限、旧Recoverの契約を読む。
3. [GUI設定の実装記録](linux-real-app-a0-gui-settings-20261109.md)、[PR1の再起動実装](linux-real-app-a0-restart-20261109.md)、[A/A0基盤](linux-real-app-a0-foundation-20261109.md)を現コードと照合する。
4. [A0の旧引き継ぎ](linux-real-app-a0-next-steps-20261109.md)は履歴として読む。PR #4時点の表や末尾のPR1向けプロンプトを今回の依頼へ読み替えない。

| 再利用・確認するもの | 現在の入口と限界 |
| --- | --- |
| manifestの厳密schema、package照合、復元guard | [linux_real_app_inspect.py](../scripts/linux_real_app_inspect.py)。A/A0で実装済み。製品v3への接続は別 |
| GIO settings module / probe | [builder](../scripts/linux_real_app_build.py)。A0 buildは `production_ready=false`。読み取り専用GUI observerとは別のmodule |
| 現行builder / pins | [build-linux-desktop-bundle](../scripts/build-linux-desktop-bundle)、[transport mod](../native/transport/src/linux_desktop/mod.rs)。v2/schema 1が現行 |
| 現行bootstrap / journal | [owner.rs](../native/transport/src/linux_desktop/owner.rs)、[journal.rs](../native/transport/src/linux_desktop/journal.rs)。旧版のsource/引数/回収を維持する |
| 合成profile | [linux_real_app_wayland.py](../scripts/linux_real_app_wayland.py)。同process内capabilityの検査用profileであり、製品の永続profileとして流用しない |
| Androidの既存identityと保存 | [CredentialStore.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/CredentialStore.kt)、[LinuxProfileStore.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxProfileStore.kt)、[初期化](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt) |

既存manifest検査を別の緩い定義で複製しない。純粋contractを共有するために整理する場合も、既存A/A0の拒否条件と結果を維持する。

## 3. B1の完成単位

推奨タイトル: **L7a: v3共有契約と互換性golden vectorを固定**。

B1は、I/Oやprocess起動を伴わない **parse / validate / canonicalize / hash** を中心とする。
Python/Rustの隔離されたv3契約moduleと固定fixtureを作り、同じ入力から同じcanonical bytes/digestを得ることを検査する。
Kotlinの後続担当も同じfixtureを使える構成にするが、Androidの保存や通常UI接続は実装しない。
新しいv3 moduleはコンパイル・テストできる状態とし、現行の通常起動経路からは有効化しない。

| B1で完成させる成果物 | 後続へ残す実装 |
| --- | --- |
| pins schema 2 / app manifest schema 1の厳密な型・field・固定値・上限 | builderによる製品pins出力・配備 |
| canonical JSON、manifest hash、build-id、context/scope、catalog revisionの入力と固定vector | 実helperの最終hash、source closure、immutable directoryへの配置 |
| storage IDsの型・意味・保存/変更契約とrecordのschema | ID生成・file作成・atomic/CAS保存・既存profileへの追加 |
| 固定catalog二項目、selection、既知reasonと版選択の契約 | 現行Catalog/Protocol/Binding/journal/Inspect/Launch/Recoverへのdispatch |
| serve 14引数 / Recover 15引数の型付きvectorと検証 | SSH command生成・実行、helper読込み |
| origin/parent/presentationの最小契約と固定fixture | compositor accept、main admission、timer、SDK callback、通常UI |
| cross-language goldenと偽陽性拒否テスト | 実v3 host acceptanceとQuest受入れ |

現行 `Pins::parse` / `Binding` / `Protocol` / journal / SSH commandへschema 2を途中接続しない。
v3の純粋parserが完成したことを、通常wire state machine、runtime、Recoverの完成として記録しない。
実装file名は最新構造に合わせて決め、既存v2 sourceへのv3分岐追加を避ける。

### 3.1 版・hash・引数

[詳細設計](linux-real-app-l7a-design-20261109.md#バージョンpins固定catalog)の既定契約を基準に、実装で未定義な入力順・型・境界をB1で確定する。

- protocol revisionは `eidolon-linux-desktop-v3`。pins schema 2、wire envelope schema 1、app manifest schema 1を混同しない。
- pinsは既知fieldだけ、4096 bytes以内。hashは64桁lower hex、storage IDsは32桁lower hex。manifestの固定policyと実測Wayland app_id `mousepad` を維持する。
- canonical JSONはsorted keys、compact separators、UTF-8。hashするbytesと末尾改行の扱いをfixtureへ明記する。重複key、余剰field、不正型を曖昧に正規化しない。
- build-idは `protocol_revision / base_bundle_sha256 / desktop_bundle_sha256 / app_manifest_sha256` の4キーのcanonical JSON hash。
- v3 context/scopeはcaptureした接続profile binding、2つのstorage IDs、protocol revision等を束縛する。catalog revisionを含め正確な入力集合を確定し、v2の式を変更しない。
- catalogは既存demoとMousepadの固定二項目・固定順序。selectionは `scope/revision/app_id`。二項目を同時実行できるという意味ではなく、L7aの `instance_limit=1` を維持する。
- v3 serve 14引数、Recover 15引数は型付きvectorとして検査する。自由path/command/argvを受ける口を追加しない。v2の11/12引数は不変。
- decoderは起動前に選んだ版で決まり、remote自己申告で昇格/降格しない。B1ではこの版選択の型と拒否契約までを固定する。

### 3.2 最終helper hashを待たずにB1を完了させる

固定の合成hashを **test fixtureだけ** に用いて、型・hash入力・canonical化を検査してよい。
その値を検証済みの製品pinsとして出力・保存・配備せず、架空のhelper fileや、helper不在を許容する製品manifestを作らない。

hashの依存は、source/binary/template → app manifest → manifest参照を含むdesktop bundle → build-idの順にする。
bundle自身のhashや最終build-idをそのbundleのhash対象bytesへ埋め込まない。
build-idは最後に配置directory名として導出し、自己参照を作らない。

B1ではpin対象の役割・依存の方向と検証規則を決める。Cのsource/importが未確定なら、最終file一覧・実hash・source closureはB2/C統合で確定する。
Pythonの推移的import、template、GIO settings source/binary/probe/登録情報までpin対象に含める方針とし、任意sys.pathやrepo checkoutの存在へ依存させない。
新sourceを実行許可用 `include_bytes!` 等へ接続する段階では、builder/source_hashes/manifest/testsを同じ変更に揃える。
合成GUI用の読み取りobserverを通常bundleへ混ぜず、製品設定policyを実装するGIO moduleと区別する。

### 3.3 storage identityはB1で意味を確定し、生成・保存はE1へ渡す

現コードにはdefaultとremote-linux用の2つのCredentialStore identityがある。
[NativeSessionHost.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)のLinuxProfileStoreは **default側の `identity.installation`** に束縛されている。
remote-linux credential identityや新しいstorage IDで既存 `ConnectionProfileV2.installation` を置換しない。

[CredentialStore](../android/app/src/main/java/io/github/tinmachine/eidolon/CredentialStore.kt)はUUID由来32桁hexとtemp file fsync/atomic renameを使うが、expected bytesのCAS・directory fsync・cross-instance共通lockは備えていない。
[LinuxProfileStore](../android/app/src/main/java/io/github/tinmachine/eidolon/LinuxProfileStore.kt)にはそれらを含む保存境界がある。
既存UUIDの利用が危険という意味ではなく、「16 random bytes・atomic/CAS」というv3保存契約を既に満たすとは推定しない。

**推奨する出発案は、storage専用の `installation_id` を定義し、既存2系統のcredential identityを変更しない方式。**
これをB1で現コードと照合して採否を確定し、理由・record schema・legacy bindingとの対応を記録する。
既存default identityの値を再利用する別案なら、UUIDを互換値として採用する設計改訂とstorage record側の所有/CAS条件を明記する。
意味の選択をE1担当へ未決定のまま渡さない。B1ではどちらの方式でも実際のID生成や移行は行わない。

共通して必要な契約:

- 内部名ではstorage用installationとlegacy profile/credentialのinstallationを区別する。wireの固定名との対応を定義する。
- storage recordは所属する既存アプリinstallationも照合する。別installationからコピーしたrecordを自動採用しない。
- IDはAndroid noBackupに保持し、APK更新で保持、再installで新規。IDの喪失・破損・部分保存を、無断の再生成や旧stateの別名採用で成功にしない。
- `profile_storage_id`は同host key/userで保持。IP/port/表示名/revision/session nonceから導出しない。host key/user変更では新規とし、旧データは削除しない。
- 既存profileへの追加は全回収後の明示保存。connection profileのv2 wire schemaと旧binding/pins/journal/Recoverを維持する。
- v3 context/scope/journalにはcaptureしたprofile bindingと2つのstorage IDsを別々に束縛する。永続directoryのdata keyにはrevision付きbindingを使わない。
- CAS競合、部分publish、readback失敗時に、新旧のrecordを混合して有効な保存として扱わない。

### 3.4 D/E/Fへ渡す最小の表示契約

ここは型・意味・固定fixtureを確定する。実compositorの変更や起動watchdogをB1へ含めない。

| 契約 | B1で確定する意味 |
| --- | --- |
| origin | nativeがLaunch enqueue前に予約したowner/contextへ束縛。専用listenerのacceptから伝播し、remote JSON/app_id/titleで生成しない。local Waypipe peer PIDをremote Mousepad PIDと解釈しない |
| parent | 明示的な親なし / 解決済みWindowId / 親参照ありだが未解決または無効を区別する。未知parentをrootへ丸めない |
| mainの役割 | lease中に一度採用しWindowId/origin/roleを固定。Started先行/窓先行を両方扱い、孤立dialogや新rootへ差し替えない |
| presentation | main待ち / SDK表示待ち / ready / stopping・pendingの意味、公開enum/reason、既存full target型との対応を定める |
| ready | 現在の同じfull targetのMapped main、Create panel適用、SDK binding、成功ACK、renderer初frame、activity/scene/XR前提が必要 |
| 期限と競合 | Launch受理からmain待ち20秒、最初のmain採用からSDK待ち10秒という設計値。terminal/取消/期限超過をready候補より優先。remap/世代交換/fallbackで元期限を延長しない |
| 失敗と回収 | 最初の起動失敗とcleanup pendingを別に保持。遅着Started/Mapped/ACKでreadyへ戻らない |

実装時の根拠は[acceptのClientState](../native/core/src/compositor/protocol.rs)、[WindowReportとparent](../native/core/src/compositor/snapshot.rs)、
[既存full-target ACK](../native/core/src/runtime.rs)、[SDK Surface処理](../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt)にある。
v2/Termuxのorigin不在や既存 `Outcome::Matched` の意味を変更せず、v3側だけで必要な証拠を要求する。
20秒/10秒を実機で検証済みの保証値とは記録しない。

## 4. 維持する契約とB1の検証

固定v2の5 sourceと[既存golden](../scripts/tests/fixtures/linux-desktop-v2-e401918-sources.json)はbyte-for-byte維持する。

- `scripts/linux/eidolon-linux-session.py`
- `scripts/linux/eidolon_linux_desktop.py`
- `scripts/linux/eidolon_linux_process_owner.py`
- `scripts/linux/eidolon_linux_desktop_session.py`
- `scripts/fixtures/gtk3-desktop-demo.c`

既存goldenを変更後のsourceから作り直さない。v2 pins/schema/hash/catalog/11・12引数/bootstrap/journal/Recoverも維持する。
現在のA0 gate、実プロセス異常経路、復元保護、全worker回収、125秒先行保持とfresh応答を維持する。
[共有lifetime](../scripts/fixtures/linux-real-app-wayland/lifetime.json)は通常Python110/Rust120秒、先行Python360/Rust370秒。
起動前から固定するPython期限をcallbackでリセットせず、片側だけの延長をしない。

### B1の必須検証

- 期待canonical bytes/digestを固定fixtureへ保存し、Python/Rustで独立に一致する。テスト実行中に同じ実装で期待値を生成して自己照合するだけにしない。
- 欠落/余剰/重複field、不正型、boolをintegerとして受ける入力、hash/IDの桁数・大文字、上限超過、誤revision、ID差替え、stale selectionを拒否する。
- v2をv3へ暗黙変換せず、v3をv2 parserへ通さない。既知sourceの検証方針を緩めない。
- storageの継続/交代の判定、origin/parent/presentationの別owner・旧世代・誤役割・遅着証拠を固定caseで区別する。I/OやSDK成功をmockから認定しない。
- B1 moduleのimport/parse/hashがapp/process起動・profile作成・環境変更・通常dispatchを行わない。製品用pinsの公開出力を追加しない。
- `scripts/check-design`、関連Python/Rust検査、既存host/Android CIと専用A0 CIをB1のPR headで確認する。実行不能・skipは理由と未確認範囲を分け、過去headの成功を代用しない。

`available` / `production_acceptance_passed` / `quest_verified`はfalseを維持し、acceptanceの未実装判定も維持する。
B1の完了は「共有契約と固定vectorが検証済み」であり、bundle実物、v3 runtime、通常helper、Questの完成ではない。

## 5. B1後の並行開発へ渡すもの

| レーン/PR単位 | 所有する範囲 | B1から必要なもの |
| --- | --- | --- |
| B2: v3 bundle/protocol | builder、v3 source/decoder、版dispatch、pins、immutable配置 | schema、hash式、bootstrap vector、reason、source closureの規則 |
| C1: 製品の永続profile | owner record、root/lock、no-follow、reopen、永続/一時領域の分離 | storage identity/record、所有・回収barrier、固定path/policy |
| C2: 通常helper | bus/settings/起動guard、lease/drain、Close/Stop/Recover | C1、検証済みcontext/bundle、Started/cleanup/receipt契約 |
| E1: Android保存モデル | version付きstorage record、生成/保持/交代、atomic/CAS | legacy identityとの対応、record schema、変更判定vector |
| D1/D2の先行準備 | origin/main admission、SDK表示追跡のmodelと拒否case | origin/parent/presentation/full-target/reason契約 |
| F0の先行準備 | v3受入れschema、偽陽性拒否、旧APK更新fixture | 版/source/storage/origin/target/cleanupの証拠契約 |

B2とCは別branchで実装できるが、最終bundle hashは完成したCのsourceに依存する。
最終source pinningと結合CIを統合担当が揃える。共通protocol/model/fixtureを複数担当が独立に改変しない。
E1はcodec/repository中心に分け、NativeSessionHostへの通常UI接続はE2でまとめる。
D2ではnative watchdogと必要なSDK失敗ACKを一つの完成単位にし、F1の実v3 host検査・E2通常UI・F2更新Recover/Questへ進む。

対象Linux/packageのread-only確認と旧APKの保存方針は並行準備できる。
現在のinspectorはdpkg所有・build evidence・既定GSettings backend等も照合するため、一般的なMousepad 0.6.3や他distributionを対応済みとしない。
CI artifactは[現設定で3日保存](../.github/workflows/check.yml)なので、更新Recoverで使う旧APK/version/hash/source revisionを期限付きartifactだけに依存させない。

keyboard起点popupの改善、複数アプリ同時起動、receipt退役/長期利用などは別単位である。
L7aのcatalog二項目・1 lease/1 mainから、複数lease・slot配分・個別Stop・接続全体回収へ広げる変更をB1へ混ぜない。

## 6. 次セッション用の初期プロンプト

以下はB1の実装とPR作成を依頼する文章である。本書をmainへ保存する作業自体ではB1を実装しない。

```text
tin-machine/eidolon-waylandの開発を継続してください。
目標はQuest 3の広い作業空間で複数のLinuxアプリを扱うことです。
今回は「B1: v3共有契約と互換性golden vectorの固定」を実装し、PRを作成してください。マージは行わないでください。

最初に最新main、関連PR、CI、現在のAGENTS.md等の作業指示を確認し、最新mainから新しいbranchで作業してください。
次の文書を主な引き継ぎとして読み、計画と現実装を照合してください。
https://github.com/tin-machine/eidolon-wayland/blob/main/docs/linux-real-app-b1-handoff-20261110.md

併せて読む文書:
- docs/linux-real-app-l7a-design-20261109.md
- docs/linux-real-app-a0-gui-settings-20261109.md
- docs/linux-real-app-a0-restart-20261109.md
- docs/linux-real-app-a0-foundation-20261109.md

PR #6はマージ済みです。引き継ぎ時のmerge commitは78e53117c0b9a0e82bf2e5ef70f2cbb127de978dです。
A0は実Mousepad/WaypipeでAPP_A0_PASSEDとなり、PRおよびマージ後mainの専用・host/Android CIも成功しています。
これは過去の基準です。後続変更を確認し、このcommitへ戻したり、過去headの成功をB1の検証へ流用したりしないでください。
古いA0文書のPR #4時点の状態表やPR1向け初期プロンプトは履歴です。A0/PR1/PR2を再実装せずB1へ進んでください。

■ B1の実装範囲
- Python/Rustの隔離されたv3契約moduleで、純粋なparse/validate/canonicalize/hashを実装する。
- pins schema 2、manifest schema 1、canonical bytes、manifest hash、build-id、context/scope/catalog revisionの入力を固定する。
- storage IDsの意味と型、固定catalog/selection/reason、serve 14引数・Recover 15引数の型付きvectorを確定する。
- Androidの既存2系統のidentityと保存契約を確認し、v3 storage installation_idとの対応をB1で決定・記録する。既存ConnectionProfileV2のbindingは維持する。
- origin、親なし/未解決parent、mainの役割、presentation状態、同full targetのACK/frame、期限/terminal優先の最小契約を確定する。
- 固定golden vectorと偽陽性拒否テストを用意し、後続のB2/C1→C2/E1担当が同じfixtureを使えるようにする。

■ 境界と維持条件
- B1は実行可能な共有契約の完成まで。通常Pins/Protocol/Binding/journal/SSH bootstrap/Inspect/Launch/Recoverへv3を接続しない。
- 製品builder/pins出力、配備、永続profile、bus、storage ID生成・保存、native admission/watchdog、Android通常UIは後続へ残す。
- test内の合成hashを製品pinsとして出力しない。未実装helperのplaceholderで成功させず、最終source closure/hashはB2/C統合で確定する。
- hashの自己参照を作らず、推移的import/template/module/probeまでpin対象に含める規則を定める。
- 固定v2の5 sourceと既存golden、v2 hash/schema/bootstrap/journal/Recoverを変更しない。
- 既存A0 gate、実プロセス異常経路、復元保護、全worker回収、共有lifetime、125秒先行保持とfresh応答を維持する。
- available/production_acceptance_passed/quest_verifiedはfalse、v3 acceptanceは未実装のまま維持する。

■ 検証と納品
既存validatorを確認して再利用し、未知/重複/余剰field、不正型、誤版、ID差替え、stale/別owner/旧世代の証拠を拒否してください。
関連Python/Rust検査、専用A0 CI、既存host/Android CIを新しいPR headで確認してください。skipは合格に数えず、実行できなかった範囲と理由を明記してください。
変更根拠と一次情報をリポジトリ内へ記録し、PR本文には変更点、検証結果、B2/C1→C2/E1への受渡しを日本語でまとめてください。
独立した調査・実装・レビューは並行化して構いませんが、共通protocol/model/goldenは統合担当を一人にし、今回B2/C/Eの製品実装まで開始しないでください。
B1が既に完了していた場合は重複実装せず、その状態と次の依存関係を報告してください。
```
