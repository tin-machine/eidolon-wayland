# L7a B1: v3共有契約と固定golden vector

2026-11-10（日本時間）。B1は**隔離された純粋な契約moduleと互換性fixture**を追加する。
通常v3、製品bundle、永続profile、Android保存、native admission/watchdogを有効化する変更ではない。
検証対象headと実行結果はPR本文へ記録する。ここにコードがあること自体をCI成功や実機受入れの証拠にしない。

## 基準と変更境界

開始時の最新mainは[`a586f042583ac0869dc6f15e06cacae96db9bd7b`](https://github.com/tin-machine/eidolon-wayland/commit/a586f042583ac0869dc6f15e06cacae96db9bd7b)。
PR #6のmerge `78e53117c0b9a0e82bf2e5ef70f2cbb127de978d`後にはB1引き継ぎ文書だけが追加されていた。
open PR、B1の先行実装、追跡された追加のAGENTS.md等はなかった。
GitHubのcommit/treeを照合した最新mainから`feat/linux-desktop-v3-contract`を作成した。
通常cloneの認証が使えなかったため、既存objectとGitHub connectorで不足objectを取得し、
commitとtree `2fcae5c56f8d07ac6a022088efdbe86da3b2c645`の完全一致を検査した。

最新mainの[host/Android](https://github.com/tin-machine/eidolon-wayland/actions/runs/42654012744)と
[専用A0](https://github.com/tin-machine/eidolon-wayland/actions/runs/42654012621)も成功していたが、B1の結果には数えない。
[B1引き継ぎ](linux-real-app-b1-handoff-20261110.md)、[詳細設計](linux-real-app-l7a-design-20261109.md)、
[GUI設定](linux-real-app-a0-gui-settings-20261109.md)、[再起動](linux-real-app-a0-restart-20261109.md)、
[A/A0基盤](linux-real-app-a0-foundation-20261109.md)を現コードと照合した。

| 成果物 | 入口 |
| --- | --- |
| Pythonの値・codec・hash・関係照合 | [linux_desktop_v3_contract.py](../scripts/linux_desktop_v3_contract.py) |
| Rustの値・codec・hash・関係照合 | [linux_desktop_v3](../native/model/src/linux_desktop_v3/mod.rs)、[JSON codec](../native/model/src/linux_desktop_v3/json_codec.rs) |
| Python/Rustの純粋な起動表示model | [Python](../scripts/linux_desktop_v3_presentation.py)、[Rust](../native/model/src/linux_desktop_v3/presentation.rs) |
| A/A0と共有するmanifest validator | [linux_real_app_manifest.py](../scripts/linux_real_app_manifest.py) |
| 共通の固定期待値 | [linux-desktop-v3-contract.json](../scripts/tests/fixtures/linux-desktop-v3-contract.json) |
| 独立した言語実装からの検査 | [Python tests](../scripts/tests/test_linux_desktop_v3_contract.py)、[Rust tests](../native/model/src/linux_desktop_v3/tests.rs) |
| Androidのidentity決定 | [storage identity契約](linux-real-app-b1-android-identity-20261110.md) |
| native/SDKへの対応 | [presentation契約](linux-real-app-b1-presentation-20261110.md) |

Rustは既存`eidolon-model`内へ隔離し、`lib.rs`のmodule宣言以外に既存native経路を変更しない。
Pythonはapp/process/filesystem/時刻/環境を操作せず、明示された値だけを検査する。
source/bundle検査、I/OのCASや実ownerの証明は後続である。

`available` / `production_acceptance_passed` / `quest_verified`はfalse、
[acceptance入口](../scripts/linux_real_app_check.py)は`APP_V3_NOT_IMPLEMENTED`のまま。
通常`Pins::parse` / `Binding` / `Protocol` / journal / SSH bootstrap / Inspect / Launch / Recoverのv2経路は変更しない。

## 1. JSONと型を先に固定する

JSONの仕様は[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)を参照し、相互運用上曖昧な入力をさらに制限する。
重複memberを辞書へ読み込んでから検査すると先の値が消えるため、Pythonのobject-pairs hookとRustの専用Visitorで、
**raw bytesのdecode時**に重複を拒否する。Rustの`serde_json::Value`へ先に読んでから未知fieldだけを検査する方式にはしない。

| 項目 | 固定値・規則 |
| --- | --- |
| protocol revision | `eidolon-linux-desktop-v3` |
| pins / app manifest / wire envelope schema | integer `2` / `1` / `1`。このPRはwire state machineを実装しない |
| raw pins上限 | 4096 bytes、空入力不可 |
| その他のB1 record/vector上限 | 16384 bytes、空入力不可。golden全体fileの上限とは異なる |
| JSON domain | object/array/string/strict bool/null/符号付き64bit整数のみ。float、NaN/Infinity、`-0`、孤立surrogate、BOM、invalid UTF-8を拒否 |
| 深さ | rootを0として32以下 |
| 数字とbool | schemaやUIDで`true == 1`を認めない。`1.0`も整数へ丸めない |
| native ID・session・profile revision | 正のsigned i64をcanonical decimal **string**で表す。先頭0・符号・number型・overflowを拒否 |
| remote UID | JSONではnonzero u32整数、argvでは同じ値のcanonical decimal string |
| hash / nonce / storage IDs | 64 / 16 / 32桁lower hex。大文字を黙って小文字へ正規化しない |

ID codecとfull targetには既存の[ids.rs](../native/model/src/ids.rs)と[TargetKey](../native/model/src/surface.rs)を利用する。
`StorageInstallationId`と`ProfileStorageId`は名目上も別の型であり、既存credential installationの別名ではない。

canonical bytes `C(v)` は **recursive sorted keys、compact separators、ensure_ascii=true、UTF-8、末尾改行なし**。
日本語は`\uXXXX`、補助面はlowercase surrogate pair、DELは`\u007f`、slashはそのままにする。
[Python jsonのensure_ascii](https://docs.python.org/3/library/json.html#json.dump)に合わせた独自の限定domainであり、RFC 8785/JCSを実装したとは呼ばない。
Rustも同じescapeを独立に生成する。UTF-8の生日本語とASCII escapeのどちらの入力も受け、canonical outputは一つにする。
`-0`はPython/Rustのnumber分類差を避けるため拒否する。Rust固定依存の
[serde_json 1.0.154のnumber parser](https://github.com/serde-rs/json/blob/v1.0.154/src/de.rs)もnegative zeroを浮動小数として扱う。

pins、manifest、context、storage、capture、owner、selectionは全field必須・未知field不可。
nullableな表示証拠もfield自体の省略は認めず、未観測なら明示`null`とする。
Pythonは直接constructor、Rustは通常Serde経路でもschema/固定値の検査を迂回できない。

Rustでは[Serdeのstruct decoder](https://github.com/serde-rs/serde/blob/v1.0.229/serde_derive/src/de/struct_.rs)が位置arrayも受理するため、
全recordとnested objectにmap-onlyの入口を置く。既存TargetKeyは変更せず、v3側の入口だけでarrayを拒否する。
[serde_jsonのenum decoder](https://github.com/serde-rs/json/blob/v1.0.154/src/value/de.rs)が受けるsingle-key objectも使わず、
固定enumはStringからのみ構築する。parentは空struct variantと未知field拒否を組み合わせ、
[unit visitor](https://github.com/serde-rs/serde/blob/v1.0.229/serde/src/private/de.rs)の余剰field読み捨てを避ける。
この3種類の代替表現も共有fixtureで拒否する。well-formed JSONでも不正なreason型/値なら`PROTOCOL_REASON`とする。

### 既存manifestを再定義しない

[inspector](../scripts/linux_real_app_inspect.py)の純粋なmanifest検査を共有moduleへ抽出した。
固定15 field、`mousepad`、`/usr/bin/mousepad`、`window_app_ids=["mousepad"]`、settings/launch/state policyと
ASCII 33〜126の1〜128文字という既存package version/architecture条件を維持する。
inspectorの`InspectionError("APP_PACKAGE_UNSUPPORTED")`と、受け取った同一objectを返すAPIも維持する。

inspectorは固定sibling fileを読み込む。単独testや別名のA0 loaderでも、別suiteが追加する`sys.path`へ依存しない。
inspectorの既存canonical/digest、128KiBの検査record decoder、package/復元guardは変更しない。
B1の16KiB manifest parserは新しい純粋境界であり、既存inspector decoderの上限を変更したものではない。

## 2. hash入力と版選択

`H(v) = lowercase_hex(SHA256(C(v)))`とする。hash自身をそのpreimageのfieldへ追加しない。

| 導出値 | 正確な入力 |
| --- | --- |
| app manifest hash | 検証済みmanifest schema 1の15 field全体 |
| build-id | `protocol_revision`, `base_bundle_sha256`, `desktop_bundle_sha256`, `app_manifest_sha256`の4 field object |
| v3 context / scope | 下記10 fieldのcontext全体。`scope=H(context)` |
| catalog revision | `protocol_revision`, `scope`, `pins`（schema 2 object全体）, `entries`（固定descriptor配列）の4 field object |
| host-user binding | [identity文書](linux-real-app-b1-android-identity-20261110.md)のdomain・canonical host key hash・username |
| storage CAS token | 検証済み7 field storage record全体の`H(record)`。I/O CASの実施済み証明ではない |

contextの固定fieldは次のとおり。

```text
schema=1, backend="linux_desktop", protocol_revision="eidolon-linux-desktop-v3",
session, profile_revision, profile_binding, nonce, uid,
installation_id, profile_storage_id
```

session/revision/binding/nonceは認証済み接続からcaptureする。UIDと2つのstorage IDsを別々に含める。
scopeにruntime handleを含めない。runtimeはnative originの外側所有として別に比較する。
pins変更は全pinsを含むcatalog revisionを変え、保存revision/UID/ID変更はscopeも変える。
profile bindingの既存式を変更しない。永続directoryのdata keyにはrevision付きbindingを使わない。

`DecoderVersion::selected`は**事前に選択・検証したv3 pins**とremote revisionの一致だけを受理する。
v2/v4の自己申告、schema 1 pinsをv3として受ける暗黙upgrade、v3からv2へのfallbackは拒否する。
実際の版別dispatchはB2で旧v2と並べて実装する。

## 3. 固定catalog / selection / reason

| 順序 | id | label | mode | icon |
| --- | --- | --- | --- | --- |
| 0 | `gtk3-desktop` | `Linux アプリ` | `gui` | `builtin:script` |
| 1 | `mousepad-editor-v1` | `Mousepad` | `gui` | `builtin:editor` |

descriptorは`schema=1`, `namespace=linux-desktop`, `protocol_revision`, `scope`, `revision`, `entries`,
`arbitrary_exec=false`, `terminal=false`, `instance_limit=1`の9 field。
二項目の固定順・label・mode・icon・件数を含めて照合し、重複、未知項目、並べ替えを拒否する。
`builtin:editor`はE2で割り当てる組込みicon tokenであり、任意path/画像取得を許可しない。

**B1のdescriptorにはavailability/attestationを載せない。** constructorを呼ぶだけでavailableにできる口を設けない。
B2のwire catalogはこのdescriptorと固定vectorを利用し、別途検証したsource/package/probe証拠を厳密なwrapperで照合する。
その時点でも両項目の検証が必要であり、Mousepad不在をv3成功へ読み替えない。通常v2は別選択のまま残す。
これは純粋contractと実行権限を分けるためのB1での明確化である。

selectionは`scope/revision/app_id`の3 fieldだけ。構文検査と現在のcontext/pinsとの一致を別々に検査する。
一致してもLaunch許可ではなく、B2/C2のowner・source・package・回収gateがさらに必要。

公開reasonは既存Aの固定tokenに詳細設計のorigin/window/state/起動表示期限/出力制限等を加え、
`Reason` enumとgoldenに全値を固定した。`StopReason`の4値、`FailureReason`の7値は別enumとして固定し、
大文字公開reasonと小文字terminal reasonを混ぜない。未知reasonは拒否する。
実wire envelopeと各message payloadの実装はB2/C2へ残す。

## 4. serve / Recoverの型付きvector

これらは**引数値の契約**であり、SSH commandを生成・実行しない。
[既存v2 owner](../native/transport/src/linux_desktop/owner.rs)と
[v2 helper](../scripts/linux/eidolon_linux_desktop_session.py)のindex 0〜10を維持し、3値を追加する。

| index | 値 | 型 |
| --- | --- | --- |
| 0 | `serve` / `recover` | 固定enum |
| 1 | session | positive i64 decimal string |
| 2 | profile revision | positive i64 decimal string |
| 3 | profile binding | hex64 |
| 4 | nonce | hex16 |
| 5 | UID | nonzero u32 decimal string |
| 6 | base bundle SHA256 | hex64 |
| 7 | desktop bundle SHA256 | hex64 |
| 8 | Waypipe SHA256 | hex64 |
| 9 | SHM fixture SHA256 | hex64 |
| 10 | desktop demo binary SHA256 | hex64 |
| 11 | app manifest SHA256 | hex64 |
| 12 | storage installation ID | hex32 |
| 13 | profile storage ID | hex32 |
| 14（Recoverだけ） | expected boot identity | lowercase `8-4-4-4-12` hex UUIDまたは`-` |

serveは14、Recoverは15要素。すべてJSON stringであり、余剰要素、自由path/command/argv、数字型を拒否する。
decodeしたvectorはcapture済みcontextとpinsへ再照合し、構文上は正しいID/hash差替えも拒否する。
`-`は既存のPrepared ACK喪失時と同じ未知bootの表現であり、無条件cleanを意味しない。

## 5. source closure / bundle hashの方向

最終file集合・source hashはB2/C統合で確定する。B1は架空のhelper、空file、未実装時の成功fallback、製品pins exporterを追加しない。
fixtureの反復hexは合成値であり、記載されたpackage versionも実packageの導入証拠ではない。

後続builderとAPKのsource allowlistは、次の規則を同じ変更で満たす。

1. 明示したv3 entrypointの全推移的Python importを列挙し、このPRの純粋contract/manifest moduleも含める。
   standard library以外は固定のbundle内でhash検証したbytesから解決し、自由`sys.path`やrepo checkoutに依存しない。
2. importで参照するtemplate、private bus設定、GIO settings source/binary/probe/登録情報、固定demo source/binaryを含める。
   schema/dependency fingerprintの検証入力も対象を明示する。動的にfile名を組み立てて未pin codeをloadする経路を許さない。
3. manifestの実行file/module/dependency hashを確定した後にapp manifestをcanonical化する。
   desktop bundleは完成したsource/binary/templateとapp manifestの参照を固定する。
4. desktop bundle自身のdigestや最終build-idを、そのhash対象bytesへ埋め込まない。
   build-idは最後に4 fieldの式で計算し、immutable directory名として使う。生成物をhash入力へ戻す循環を作らない。
5. `include_bytes!`、source-hashes、builderのfile allowlist、manifest、未知file/import拒否testを一緒に更新する。
   missing/unpinned/modifiedな推移的依存を「動作には不要」と黙認しない。B2/Cの未統合sourceを実行許可しない。
6. A0合成GUIの読み取りobserverを製品bundleへ混ぜない。製品policyのGIO settings moduleとは別用途である。

既存[A0 inspector](../scripts/linux_real_app_inspect.py)のdependency fingerprintは、照合済みpackage/build evidence、
sort済みdependency record、system keyfile policyを含む検査recordから導出する。
B1はこれをhash形式のopaque値として束縛し、依存の実在確認を代行しない。
Recoverではimmutable helper/manifestの検証と現在のapp packageの起動条件を分ける。package更新後の回収を禁止する新条件をB1で足さない。

## 6. 表示契約の実行可能な最小model

[詳細](linux-real-app-b1-presentation-20261110.md)のNativeOrigin、4通りのParent、role、既存full targetとruntimeを型にした。
StartedとMappedの両順序を処理し、mainのWindowIdと最初の採用時刻を固定する。
同じCreateのcommand_seq/action、SDK所有、ACK、renderer frameをそれぞれ検査する。

同ownerのterminal/取消/期限切れを、staleな表示証拠の照合より先に確定する。
valid-formatの旧ACKが終了や期限切れへの遷移を妨げない。別ownerのStarted/terminalは現在ownerの証拠にしない。
構文不正・混在owner等を拒否した場合、状態を部分更新しない。D2はnative時計による期限評価自体を外部入力のparse成功に依存させない。

unmapまたはcurrent Create消失で旧Createを退役扱いにし、再表示には同mainのstrictly newer targetと新しいcommand_seqを要求する。
旧ACK/frameをそのまま再掲してreadyへ復帰できない。元の期限は延ばさない。
fallbackは**失敗した現在Createの観測と同時にnativeが`fallback=true`で一度だけ許可する**。
そのtargetを退役させ、元のSDK期限内で新しいCreateを待つ。既に終端失敗へ進んだ後のfallbackや、成功証拠だけでのfallback申告を拒否する。
fallback後の再失敗は`APP_PRESENTATION_FAILED`。正常ready後はstartup期限を再武装しないが、表示証拠を失えばreadyを外す。

このmodelはI/Oやtimerの実装ではない。SDK/renderer/nativeからの合成観測を整合検査するだけであり、remote JSONからoriginをmintしない。
nativeの`Outcome::Matched`やv2/Termuxのorigin不在契約は変更しない。

## 7. 固定vectorと検証

fixtureはraw JSON文字列、期待canonical bytes、期待digest、拒否入力、関係照合、storage変更、表示traceを保存する。
テスト時に同じ実装で期待値を再生成しない。Python/Rustが同じfileを別々に読む。
KotlinのE1/D2 testsもこのfileをread-only resourceとして利用し、独自の似たfixtureへforkしない。

固定集合は54件のcanonical record、429件のraw不正入力、34件の関係不一致、11件のstorage変更、91本の表示trace。
独立レビューで発見したenum object、位置array、parent余剰field、旧Createの再利用も含む。

新しい値を固定するときは、仕様差分・両言語の変更・旧vectorとの非互換を一つのPRでレビューする。
合わなくなったgoldenを機械的に更新して成功にしない。

主な検査入口:

```bash
python3 scripts/check-design
python3 -m unittest discover -s scripts/tests -p 'test_linux_desktop_v3_contract.py' -v
env -u PYTHONPATH python3 -m unittest discover -s scripts/tests -p 'test_linux_real_app_inspect.py' -v
cargo test --locked -p eidolon-model linux_desktop_v3
cargo fmt --all -- --check
scripts/check-host --suite all
```

新suiteは既存host/Android Python discoveryとhostのmodel suiteに含まれる。
既存の[host/Android workflow](../.github/workflows/check.yml)と[専用A0 workflow](../.github/workflows/linux-real-app.yml)を、B1の新PR headで実行する。
専用A0のGUI保持/保護Preferences、実process異常終了/復元保護/全worker回収、125秒先行保持後のfresh応答は維持する。
[共有lifetime](../scripts/fixtures/linux-real-app-wayland/lifetime.json)の110/120秒と360/370秒は変更しない。

[固定v2の5 source golden](../scripts/tests/fixtures/linux-desktop-v2-e401918-sources.json)とsource自体、v2のhash/schema/bootstrap/journal/Recoverを維持する。
ローカルでRust、実GUI、非root process/socket等を実行できない場合は、その理由をPRに記録し、skipをpassへ加算しない。
CIで補えた範囲も実行したheadへ紐付ける。Quest、物理入力、通常v3 acceptanceはB1成功から補完しない。

## 8. 次の担当への受渡し

| 単位 | B1から引き取るもの | そのPRで完成させるもの |
| --- | --- | --- |
| B2 | pins/manifest/canonical/hash/catalog descriptor/reason/14・15 vector | 実builder、固定source closure、版別wire decoder/dispatch、immutable配置、実pin出力 |
| C1 | storage IDs・remote owner schema・capture照合・変更規則 | no-follow root/lock/reopen、永続/一時分離、実I/O所有・回収barrier |
| C2 | C1とcontext/selection/reason、固定source規則 | private bus/settings/復元guard、lease/drain/Close/Stop/Recover、B2との最終hash統合 |
| E1 | legacy identity対応・7 field record・host/user/CAS/変更vector | 生成/保存/保持/交代/部分publish処理。既存profile v2 wireは維持 |
| D1/D2 | origin/parent/role/target/表示trace | 専用listener accept、実main admission、native時計、SDK失敗ACK・一回fallback・全世代回収 |
| F0/F1/E2/F2 | 共通fixtureとB1境界 | 実v3 host acceptance、Android通常UI、旧APK更新Recover、Quest受入れ |

B2、C1→C2、E1は別branchで並行化できる。B2の最終bundle hashはCの完成sourceに依存するため、
統合担当がfile集合・実hash・結合CIを揃えてから製品pinsを確定する。
今回これらの製品実装は開始しない。catalog二項目は二つの同時leaseを意味せず、`instance_limit=1`を維持する。
