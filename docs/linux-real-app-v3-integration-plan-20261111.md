# L7a: 通常v3統合からQuest自動検証までの実装計画

2026-11-11（日本時間）。状態: **詳細計画。以下の新規統合は未実装・未検証**。
基準main: `55bba9bd02043241e46bf8e9df5997d470f885c7`（PR #11 merge）。

目的は、C1/C2のsource pinningを終えるだけでなく、通常ランチャーからMousepadを起動し、
nativeの所有・表示・入力・回収を経て、Quest 3上の自動検証まで完成させることである。
ユーザーによる装着・物理キーボード・コントローラーの受入れは最後にまとめる。
人間による確認を待つことを、独立に実装・自動検証できる項目を止める理由にしない。

本書は[L7a設計](linux-real-app-l7a-design-20261109.md)のB2最終統合、D、E2、Fを具体化する。
[B1契約](linux-real-app-b1-contract-20261110.md)、[presentation契約](linux-real-app-b1-presentation-20261110.md)、
[B2統合境界](linux-real-app-b2-integration-20261110.md)、[C1](linux-real-app-c1-handoff-20261110.md)、
[C2](linux-real-app-c2-helper-20261110.md)の所有・互換性条件は維持する。
過去のhandoffにある「未提供source」「未マージ」は、その文書作成時点の状態として扱う。

## 1. 着手時に確認できたこと

| 項目 | 実際の状態 | この作業で必要な対応 |
| --- | --- | --- |
| B1 | Context/Pins/Selection/Storageと純粋なStartup model、goldenあり | 製品の実観測をmodelへ接続する |
| B2 | builder、immutable validator、v3 owner/journal/Recoverあり | commit pinning、通常controllerとの接続、実bundle/SSH結合検査 |
| C1 | 永続profile・lock・release実装済み | APIを再実装せずC2経由で使う |
| C2 | PR #11とレビュー修正をmerge済み | 実package/bundle/通常transportと結合する |
| Android E1 | LinuxStorageRepository/Storeあり | NativeSessionHostは未使用。実回収barrierを接続する |
| Android pins/catalog | schema 1、固定GTKデモ一項目 | v2を保ったままv3設定・二項目catalogを追加する |
| native通常経路 | v2用Request/Report/Selectionとnonceベース窓採用 | v3 actor、listener origin、固定main、SDK証拠を追加する |
| acceptance | A0と既存v2の検査がある | 通常v3用の独立したhost/Quest結果を追加する |
| Quest | 着手時点でWi-Fi ADB接続可、起動済み、AC給電を観測 | 各run前に接続・電源・XR状態を再確認する |

実装対象repositoryはPi direct/structural graphの登録対象外だったため、sourceを直接確認する。
RAGに新規B2文書が未登録だったため、ローカル実体と登録状況を確認し、docsだけのindex同期を依頼した。
同期のbusyは完了として扱わない。計画の根拠は上記固定revisionの実sourceである。
IP、認証情報、APK内のprofile/pins本文はこの文書へ転記しない。

## 2. 固定する統合入力

| 入力 | 確定値・根拠 |
| --- | --- |
| C1 commit | PR #10 head `8450772476a1331efbf5f80e34ee3f57351b2744` |
| C1 source | `scripts/linux/eidolon_linux_persistent_profile.py`。基準mainと上記commitのblob一致を確認済み |
| C2 commit | PR #11修正head `34f7a8240f4be0c1a232b172536805ffa618081d` |
| C2検証 | PR headでhost/Android/GIO/実Mousepad・Waypipeの4CI成功。通常v3/Questの成功とは区別する |
| 初回統合base | `55bba9bd02043241e46bf8e9df5997d470f885c7`。treeはC2検証headと一致 |
| inventory | `scripts/linux-desktop-v3-sources.json`。現存15 source、missingなし、c1/c2 commitは現在null |
| source table | `native/transport/src/linux_desktop/v3/sources.rs`。runtimeとAPKが同じbytesをpinする |

新しい統合commit自身のSHAを同じcommitのinventoryへ埋め込まない。
上記C1/C2完成commitを履歴上の統合基点として記録し、実行するsource bytesは現在のcompiled hashで別に照合する。
統合中にC2本体の追加修正が必要なら、修正commitを先に確定し、後続commitで基点を更新する。

## 3. 先に決める境界

### 3.1 v2互換とv3の入力

- v2の固定5 source、11/12引数、schema 1 pins、旧journal、Recoverを変更しない。
- v3の9-field pins、14/15引数、manifest、storageの意味はB1から変更しない。
- decoderはローカルに保存した版で選ぶ。remote bytesやparse失敗から別版へfallbackしない。
- 通常のGUI leaseは一つ。GTKデモとMousepadを同時起動する拡張は行わない。
- v2 pendingがある間はv3開始・設定交代を拒否し、旧captureでRecoverする。
- v3 pendingもcapture済みprofile/pins/source/storageを使う。現在設定で上書きして消さない。

### 3.2 pinsだけでは不足するapp manifestの受け渡し

現行 `v3::Owner::open()` は `ExpectedEvidence` を要求し、その生成にはPinsとManifestが必要である。
通常UIの現在の保存物はv2 pinsだけなので、ここを曖昧にしたままactorを配線しない。

採用案は、builderから**静的な接続設定一式**を出力して保存する方式とする。
新設する `--print-config --build-id <build-id>` は同じimmutable検証を経て次の厳密なobjectを出す。

```text
schema: 1
kind: "linux-desktop-v3-connection"
pins: B1の9-field Pins object
app_manifest: B1のManifest object
```

未知field、重複key、上限超過、manifest hashとpinsの不一致を拒否する。
このwrapperはoperator/Android設定の形式であり、B1 pinsやremote wireへfieldを追加しない。
自由argv/path/module/source hashを設定へ入れない。source hashesはAPKのcompiled tableから得る。
`--print-pins` は従来どおりpinsだけを出し、app/probeを起動しない。

Androidではv3用configuration型・repositoryを追加し、profile bindingとCAS tokenへ束縛する。
v2の保存形式はそのまま読み書きできる。v3設定の保存完了はruntime起動許可ではなく、
storage整合性・旧owner回収・source/pins検査をすべて通してからnativeへ渡す。

### 3.3 所有・表示・回収の正本

| 情報 | 正本 | 信用しない代替 |
| --- | --- | --- |
| NativeOrigin | Launch受理前のnative予約、専用listenerのaccept | title/app_id、remote JSON、同UID |
| app実行条件 | compiled sources、pins、Manifest、実Inspect/Start | remoteのavailable boolだけ |
| main/dialog | 同origin、実parent履歴、固定main WindowId | snapshotのroot再探索、dialogのmain昇格 |
| ready | 同runtime/full targetのCreate ACK・SDK binding・renderer frameと可視性 | Startedのみ、総frame数、診断panel |
| storage交代 | E1 CASと実runtime/SDK/remote回収barrier | UIから渡す「回収済み=true」 |
| remote cleanup | C2 proof/receiptとB2 channel/router回収 | process正常exit、journal削除だけ |

## 4. 実装順序と小さな完了単位

| ID | 範囲 | 依存 | 完了条件 |
| --- | --- | --- | --- |
| I0 | 対象host・Quest・保存状態のread-only確認 | なし | バージョン、接続、旧pending、対象packageの対応可否を記録 |
| I1 | B2 pinning・実bundle・設定export | I0 | 完全sourceで実bundleを生成・再検証し、実pins/manifestを出力 |
| I2 | v3通常SSH controllerと回収 | I1 | 同actorでInspect→Launch→Stop、失敗・Recover・旧v2障壁を検証 |
| I3 | native originと固定main/dialog admission | I2 | 専用listener由来のoriginと実parentを製品窓へ伝播 |
| I4 | 起動追跡とSDK表示証拠 | I3 | B1 Startupを実観測で進め、期限・取消・遅着・全世代回収を検証 |
| I5 | Android storage・設定・二項目ランチャー | I2/I4 | E1の実barrier、v3設定保存、通常操作・状態表示が成立 |
| I6 | 通常v3 host acceptanceとCI | I1〜I5 | 合成応答でなく実helper/SSH/Waypipe/Mousepad経路で検証 |
| I7 | APK配備とQuest自動受入れ | I6 | 実APKの表示・合成入力・保存・終了・Recoverの証拠を採取 |
| H | 人間による最終受入れ | I7 | 装着・物理操作の確認。今回の自動作業の最後に残す |

独立したtests/UI parser等は先行実装できるが、gateを先に成功へ変更しない。
一つの契約/model/dispatchは同じ変更で統合し、source pinningと対応する拒否テストを分離しない。
各単位でcommit・再開記録を残し、途中の完成範囲と未確認範囲を明記する。

## 5. I0: 対象環境と既存状態

1. 作業開始ごとにcodex-work-rootを読み、専用task directoryへrevision/run/次actionを保存する。
2. `adb devices -l`、boot完了、給電・battery、現在APKのversion/source digestを取得する。
3. 通常Linux profileの選択状態、v2/v3 pending、native/SDK/forwardの稼働を本文なしで確認する。
   認証key・token・入力本文をログへ出さない。旧pendingを手で削除しない。
4. 対象SSH host/UID/host keyを既存profileと照合する。接続できたことだけで正しいhostとしない。
5. read-only inspectorでMousepad/package/dependency/schema/backend対応と既存state容量を検査する。
6. 既存hostのpackageが対応外なら、固定Ubuntu環境・専用非rootユーザーの隔離検証hostでA0/I6を成立させる。
   本来のdesktopのpackageやユーザー設定を差し替えて試験を成立させない。

対象host/package版がまだ未確定であることを残す。CIのMousepad 0.6.3/GTK実測値を、別のhostへ推測適用しない。
対応hostが準備できるまで、UI/model/controller等の独立実装を進める。

## 6. I1: source pinning・bundle・設定export

変更入口:

- `scripts/linux-desktop-v3-sources.json`
- `native/transport/src/linux_desktop/v3/sources.rs`
- `scripts/linux_desktop_v3_bundle.py` / `linux_desktop_v3_build.py`
- `scripts/build-linux-desktop-v3-bundle`
- `scripts/tests/test_linux_desktop_v3_{bundle,bootstrap}.py`

実施内容:

1. C1/C2 commitを固定し、現在のnullを前提にしたtestsを「完成sourceなら成功、欠落/未確定なら拒否」へ更新する。
   `require_complete()` 自体を削除しない。欠落・未知・改変sourceの拒否は残す。
2. compiled table、inventory、AST import closure、生成物集合、hash計算の整合性を検査する。
3. 対象hostで既存base bundleを検証し、実settings module/probe/demoをcompileしてimmutable v3 bundleへ追加する。
   既存build-idやv2 bundleは置換しない。inputsの再照合後にのみ実pinsを出力する。
4. `--print-config` と厳密な設定parserを追加する。Manifest/Pinsの既存parser・hash式を再利用する。
5. bootstrap snapshot loaderを通して実C2 helperを起動する結合検査を追加する。
   unit testが未統合gate解除だけで通常GUIの成功を主張しないようにする。

必須拒否: c1/c2未確定、source一件欠落/改変、未pin import、manifest/module/probe/登録情報不一致、
未知file、既存build-id、pinsとmanifestの組違い、v2 directoryへの配備。

## 7. I2: v3通常controller

主な入口は `native/transport/src/session.rs`、`linux_desktop/dispatch.rs` とする。
v2 `linux_desktop/runtime.rs` を分岐だらけにせず、新規 `linux_desktop/v3/runtime.rs` にv3のactor調整を置く。
B2 `v3::{owner,journal,recovery,receipt}` を再実装しない。

- configurationを版別の型へ一度だけ解決する。v3側はPins/Manifest/StorageRecordを保持する。
- 認証済みhandleでUID確認後、`Binding::capture()` と `ExpectedEvidence::capture()` を生成する。
  manifestのhash照合に加え、source mapはcompiled exact集合へ照合する。
- Journal reserve → owner Prepared → Inspect/catalog → 明示Launch一回 → forward/Started → terminal/回収を同actorで所有する。
- Start前に専用local socketを作成し、request/nonce/scopeを予約する。再接続時のStart自動再送をしない。
- catalog選択はB1 Selectionの型で渡し、click時にもsession/revision/scope/app IDを照合する。
- callback/reportは版を含む型で区別する。v3状態をv2 Reportへ偽装しない。
- Stop/EOF/lease/切断/失敗の全経路で、channel close、forward取消、router drain、remote証拠を別々に記録する。
- Recoverは新しい認証済み接続と捕捉済みjournalを使う。previous connection closedはI/O ownerの実証拠から得る。
- journal clearはremote回収・channel/router回収に加え、呼出側のnative/SDK全世代回収を満たした時だけ行う。

テストは既存SSH actor fixtureを拡張し、catalog idle中のheartbeat、Start ACK喪失、partial EOF、
重複terminal、古いrequest、Stop中切断、回収未確定、v2 pending阻止を含める。
I6では合成SSH応答の成功を実C2結合の代わりにしない。

## 8. I3: listener originと窓採用

対象: `native/core/src/compositor/{protocol,snapshot}.rs`、listenerを所有するcompositor入口、
`native/core/src/{runtime,session,window_slots,panels}.rs`、必要なwindow model。

1. Launch受理時にB1 `NativeOrigin` をnativeで作成する。値はruntime/session/request/nonce/scope/app ID。
2. 専用listenerを同originに束縛し、acceptでClientDataへ不変に登録する。
   `ProtocolState::accept()`、WindowRecord、WindowReportまで伝播させる。
   Termux・旧v2・診断clientはv3 originを得ない。
3. parentの既存Optionを壊さず、v3観測用にnone/resolved/unresolved/invalidを表現する。
   parent lookup失敗を親なしへ変換しない。破棄・remap時にもrole履歴を維持する。
4. B1 Startupへの観測を作り、StartedとMappedの両順序を扱う。
   Mousepadは固定main一つ、同originの実parent chainがそこへ到達するdialogだけ採用する。
5. main破棄後に新rootをmainとして取り直さない。未知origin/二つ目のroot/循環/重複/10窓目は拒否する。
6. 表示slotと入力の両方で同じadmission結果を使う。非採用窓へのfocus/inputを通さない。

純粋modelのgoldenに加え、実Wayland clientのaccept・parent・destroy/remapを使うcompositor testsを追加する。
既存Termux、v2 GTKデモ、popup/subsurface、最大9窓の回帰を確認する。

## 9. I4: 起動追跡・SDK・rendererの実証拠

`native/model/src/linux_desktop_v3/presentation.rs` の `Startup::begin/advance` を正本とし、
`native/core/src/runtime.rs` で実観測を一つのtickへ集約する。独自の別watchdogは作らない。

- Launch受付でactivity visible/current scene/XR Focusedを確認し、monotonic t0を固定する。
- main待ち20秒、初回main採用からSDK待ち10秒。`now >= deadline` は失敗。
- remap、generation交代、装飾fallbackで元期限を延ばさない。ready後はstartup完了をlatchする。
- 同runtime/full TargetKeyのCreate command sequence/action/ACK、非診断SDK binding、frameを照合する。
- paused、presentation_paused、scene不一致、前提喪失を観測へ反映する。遅着成功で復帰させない。
- 最初の失敗とcleanup pendingを別に残す。terminal/取消/期限を同tickのreadyより優先する。
- 失敗は同ownerのStopへ接続する。mainだけでなく旧generation・dialog・fallback前targetも回収する。

Android側入口は `SpatialPanelAdapter.kt` と `NativeSessionHost.kt` のSDK ACK/binding/diagnostics処理。
SDK側の `ready=true` 一個でnativeの判定を置き換えない。
成功ACKがなくてもframe総数が増えるケースや、旧scene/dialogの証拠混入を拒否するtestsを設ける。

## 10. I5: Android保存・ランチャー・状態表示

主な対象: `LinuxDesktopPins.kt`、新規v3 configuration型/repository、`LinuxDesktopCatalog.kt`、
`LinuxStorageStore.kt`、`NativeSessionHost.kt`、`ApplicationLauncher.kt`、接続設定UI、
`native/model/src/commands.rs` とnative config受取境界。

### 保存と実回収barrier

1. E1の `LinuxStorageRepository` を使用する。installation/profile storage IDを別実装で生成しない。
2. productWorker側のruntime admissionを閉じ、対象captureに対するStop→native shutdown/destroy→SDK disposalを待つ。
3. 実観測から `ReclamationEvidence` を作る。connection/worker/target/panel/binding/window/operationが全て0で、
   remote cleanup pendingがない時だけ `ReclamationBarrier.withReclaimed()` 内でsave/resumeを実行する。
4. 同じcapture tokenとCASを保存完了・readbackまで保持する。途中のprofile編集・runtime再生成で失効させる。
5. initial enrollment、同host/user編集、host/user交代、クラッシュ途中transaction再開を区別する。
   E1のBlocked/PendingをMatchedへ推測変換しない。旧設定での診断/Recover入口を維持する。

### 通常操作

- v2とv3設定を明示的に区別する。v3 config import時はPins/Manifestを厳密に検証する。
- native configへv3 configurationと保存済みStorageRecordを同じprofile captureで渡す。
  storage IDをdebug broadcastや自由なLaunch payloadから指定しない。
- v3 catalogは固定GTKデモ/Mousepadの二項目。未知ID/label/mode/重複項目を拒否する。
- 既存v2 command/selectionは維持し、v3 Launchには専用のtyped command/Selectionを追加する。
  同名commandの緩いoptional fieldで両版を混在させない。
- UIはnativeのawaiting_main/awaiting_sdk/ready/stopping/pendingを表示する。
  起動中を「表示済み」と表示せず、初回失敗と回収状態を分ける。
- Launch二重押しを同一requestで排除する。再起動は旧lease全回収後の新nonceとする。
- v3固定reasonを日本語messageへ対応させ、STATE_LIMITを未導入と誤表示しない。

JVM testsは設定parser、catalog、保存CAS、回収barrierの各未完了資源、stale capture、
旧設定読み込み、途中transaction、error表示を対象にする。parserの実装をコピーした期待値は作らない。

## 11. I6: 通常v3 host acceptanceとCI

`scripts/check-linux-real-app --stage acceptance` を実装し、`APP_V3_NOT_IMPLEMENTED` のplaceholderを
実結果へ置き換える。A0 `feasibility` は別結果のまま保持する。
新しいacceptance module/fixtureを分け、既存A0 observerやv2 evidenceへv3成功を混ぜない。

検証hostは固定package、実bundle、専用非root account、専用SSH、production compositor/Waypipeを使う。
C1がpasswd HOMEを検証するため、環境変数HOMEの差替えだけを隔離として扱わない。
専用の新storage IDsを使い、通常利用のsettings/Documentsを試験fixtureにしない。

| ケース | 必須の実証拠 |
| --- | --- |
| 実Inspect/Launch | bootstrap snapshot検証、C2の実probe、同actorのcatalog/Started、同origin mainとframe |
| idle/長時間 | 複数lease期間のheartbeat、総runtime上限なし、Stopへの応答 |
| Open/Edit/Save As | 固定合成UTF-8 fileをGUIで開き、ASCII追記し、別名保存。実bytesをhostで比較 |
| dialog/Close | 実parent/target、取消と確定、app終了と全回収を区別 |
| 設定保存 | GUI変更→通常Close→同storage新nonceで再起動。設定とGUI状態を照合 |
| 独立性 | 別bus/displayの先行instanceと別storageへの設定漏れなし |
| 復元保護 | 合成session/autosave候補があるとspawn前拒否。候補・文書・設定bytes不変 |
| 異常系 | 出力過多、dbus/app終了、EOF、lease切れ、Start ACK喪失、cleanup/receipt失敗 |
| Recover | 同boot成功receipt、boot変更、同boot未知、部分publishから再試行。保存PIDへのsignalなし |
| 更新互換 | 固定旧v2 source/journalで新APK側の旧Recover。package変更後はLaunch拒否とRecover可否を別検査 |
| 全回収 | process/pidfd/pipe、forward/router、SHM、native/SDK全target世代、journalの個別証拠 |

fixture応答だけのunit testはprotocol拒否の証拠とし、上表の実C2/Mousepad成功には数えない。
GUI本文や入力内容を通常ログに保存せず、固定fixtureのdigest・一致bool・counter・targetだけを記録する。
旧APK/updateの試験は署名/端末SDKに合う既存artifactの有無を先に確認し、v2互換を実施できなければ未実施と記録する。

CIでは既存host/AndroidとA0を維持し、通常v3 acceptanceを独立step/jobへ追加する。
必要package/権限不足によるskipは成功に数えない。実行件数、head SHA、package fingerprint、build-id、run/jobを保存する。

## 12. I7: Quest 3の自動検証

### 準備と実行

1. 検証済みheadからAPKをbuildし、APK digest/source digest/version、接続設定のcaptureを記録する。
2. Wi-Fi ADB疎通・給電・boot・空き領域を再確認し、通常のinstall/updateを行う。設定を消す初期化を既定にしない。
3. 開発中は既に承認された非装着継続方針を維持する。`prox_close` 等は変更前・実行結果・実際のXR状態を記録する。
   近接制御を自動で元へ戻して検証を中断させない。電源/追跡で止まった場合も独立作業は継続する。
4. Boundaryで停止した場合だけ、既に依頼されたmetavr/ADBの一時停止手順を利用可能性から確認する。
   passthroughへの影響があればその項目を別結果にし、通常GUI実装全体を失敗扱いにしない。
5. 通常設定・storage保存・ランチャー操作を通してv3 GTKデモ、Mousepadを順に起動する。

### 自動operatorと証拠

新設するv3専用operatorは `quest-observe-normal-linux-gui` と別entrypointにする。
例: `scripts/quest-check-linux-real-app`。固定case/nonce/timeout/artifact rootだけを受ける。
debug receiverは通常UI/controllerと同じcommandを起動し、完了状態・origin・ACK・frameを偽装しない。
任意shell/argv/path/任意保存先をdebug wireへ追加しない。

- 同runtime/request/session/nonce/scope/main/full targetのdiagnosticsを連続採取する。
- Surface callback、Create ACK、SDK binding、renderer frame、Focused、pause状態から表示成立を確認する。
- 合成入力は製品のfocus/current-target gateを通す。ADB `input` がnative textへ届いたという仮定は置かず、
  edit/saveの実file oracleとinput receiptで到達を確認する。
- Open/Save As/設定dialog、通常Close、明示Stop、再接続、同storageでの設定保持を検査する。
- 旧generationへのinput、失効catalog、起動中のscene/visibility変化、切断時の回収を検査する。
- 正常系・失敗系それぞれでSDK/native/SHM/forward/process/journalの回収を観測する。
- timeout/例外時も同runへStopし、回収できたかを別に記録する。本文・credentialを保存しない。

ADB screenshotは2D Android画面の補助記録とし、XR内のpanel視認の証明にしない。
合成入力成功を物理キーボードの成功へ読み替えない。

## 13. 人間の確認に残す項目

自動群が通った後、次の短い手順を一度に提示する。最後の確認用APK/versionと状態を固定しておく。

1. Questを装着し、ランチャーのGTKデモ/Mousepad、文字の読みやすさ、dialog位置・前後関係を確認する。
2. US配列の物理キーボードで英数・Backspace・左右矢印を入力する。自作keyboardのfunction keyは既存指定どおり対象外。
3. コントローラーによるfocus、Open/Edit/Save As、設定変更、Close/Stopの操作感を確認する。
4. 再起動したMousepadの設定/保存fileと、必要なpassthroughの見え方を確認する。

かな漢字変換、他のLinuxアプリ、自由catalog、音声、GPU/Xwaylandの合格を、このL7a試験から補完しない。
遠隔IME等の別gateは既存計画に残す。

## 14. 完了の定義と記録

今回の自動作業はI0〜I7の実装・実行可能な自動検査を終え、Hを明示的に未確認として引き渡すところまで。
既存の `available` / `production_acceptance_passed` / `quest_verified` は、それぞれの定義を満たした証拠でだけ更新する。
実行できなかった検査や人間必須の条件が残る総合gateをtrueにしない。
startup modelのテスト成功やA0の成功を、通常v3の表示・入力成功として流用しない。

各checkpointに以下を残す。

- source revision、branch/PR、APK/version/digest、bundle build-id、package fingerprint。
- 実行コマンド、run/job、成功/失敗/skip/未実行の件数と理由。
- 最初の失敗、試した対策、対策後の結果、回収状態。失敗を上書きして消さない。
- 人間に残る手順、環境上の制約、次の具体的なaction。

作業用artifactはcodex-work-rootのtask下へ0700/0600で保存する。再利用する設計・判断はrepository文書へ要約する。
push直前にremoteの更新を確認し、他のcommitを上書きしない。検証済みの単位でcommit/pushする。
人間の確認が残る段階では、上位NX全体やPhase 5/6の総合goalを完了にしない。

## 15. 最初の実装checkpoint

最初は **I0/I1だけ**を一つの実装単位にする。
source pinning、設定export、実bundle検証と対応する拒否テストを揃え、結果を記録した後にI2へ進む。
D/E/Fの未実装を隠して通常ランチャーへv3を公開しない。
対象host/package成立性、Manifestの受け渡し、storageの実回収barrier、listener originの伝播が
後続の主要な結合点であり、本書の順序で一つずつ確かめる。

## 16. 実装詳細: 型・保存形式・責務の分割

以下は**追加するAPIと実装方法の設計**であり、現存APIと区別する。
コード例の型名は実装時の推奨名。B1/B2の既存wire、hash式、固定reasonは変更しない。
外部契約を変える必要が判明した場合は、本書・共有fixture・全言語の利用箇所を同じ変更で更新する。

### 16.1 設定codecとexport

| 層 | 追加・変更箇所 | 入出力と責務 |
| --- | --- | --- |
| Python | `scripts/linux_desktop_v3_build.py` | `verified_export(build_id)`で現在の`print_pins`の静的検証を共通化し、検証済みPins/Manifestを返す |
| CLI | `scripts/build-linux-desktop-v3-bundle` | `--print-config`をbuild/print-pins/source-statusと排他的に追加。`--build-id`必須、`--reviewed-package`併用不可 |
| Rust | 新規`native/model/src/linux_desktop_v3/configuration.rs` | `ConnectionConfig::parse(&[u8])`、`record()`、`sha256()`。B1のPins/Manifestを保持する |
| Kotlin | 新規`LinuxDesktopV3Config.kt` | 同じraw入力とcanonical/hashの受理・拒否。独自のManifest許容条件を増やさない |
| 共通fixture | 新規`scripts/tests/fixtures/linux-desktop-v3-configuration.json` | raw重複key、未知key、型、上限、hash、canonical bytes、異なるbundleの組合せの期待値 |

入力wrapperは3.2の4 fieldだけとし、全体をUTF-8 **32,768 bytes以下**、Pinsを4,096 bytes以下、
Manifestを16,384 bytes以下に制限する。既存B1の整数/文字列・最大長・object-only条件を維持する。
raw JSONの重複key検出前に`JSONObject`等で再serializeしない。UTF-8不正・重複keyは両言語で拒否する。
Rustは既存`parse_json`→既存型のparse→`manifest.matches_pins`の順で検査する。
Manifestを別読みにした後で未検証のfileへ交換されないよう、builderの検証済みsnapshotから返す。
build-idはPinsから導出し、別の自由入力fieldとして保存しない。

成功出力はwrapperそのものとし、`available=true`等の実行結果を付けない。
`--print-pins`の成功objectと失敗時の既存形式・非zero終了は維持する。
wrapperのcanonical digestはローカル設定のCAS/結合にだけ使い、B1 catalog revisionの式へ混ぜない。

### 16.2 native設定と版の選択

`RuntimeConfig`には新規`linux_desktop_v3: Option<String>`を追加する。
内容は厳密なruntime用object `{configuration: <16.1のobject>, storage: <B1 StorageRecord>}` とする。
raw文字列で保持してから厳密parserへ渡し、重複keyを潰さない。通常command全体の既存上限内に収まることも検査する。
現行の`COMMAND_BYTES`とJNI受渡し上限は16,384 bytesである。
import時の空白を含む32,768 bytesのraw文字列をそのままruntimeへ入れず、
厳密parse後のcanonical objectからruntime用文字列を生成する。
profile/storage/その他設定を含む最終configのUTF-8 bytesとJNI境界を検査し、上限超過を明示拒否する。
Manifestの許容最大文字列・JSON escapeを含むround-tripテストを作り、通常command上限の一括引上げは行わない。

| profile / 設定 | 受理 |
| --- | --- |
| Linux profileなし、v2/v3設定なし | 既存Termux/未設定経路 |
| `linux_profile_v2` + `linux_desktop_pins`のみ | 従来v2 |
| `linux_profile_v2` + `linux_desktop_v3`のみ | v3。storageが同profile captureに一致することも必須 |
| 両版の設定あり、profile欠落、storageだけ、異なるinstallation/profile | 起動拒否。別版への再parseなし |

内部の`connect_profile::DesktopConfig`は`V2 { profile, pins, files }` /
`V3 { profile, configuration, storage, files }`のenumへ変更する。
`ConnectSpec::linux_desktop_v3(...)`を追加し、既存v2 constructorは維持する。
`files`はAndroid私有directoryから得る。operator wrapperやLaunchから指定しない。
接続時にprofile・版・pins・manifest・storageをcloneして固定し、後の保存でlive actorを書き換えない。
`dispatch::Version`と既存`captured_version/validate_recovery_selection`を使い、版判定を複製しない。

### 16.3 コマンド・catalog・report

新規commandは`linux_desktop_v3_inspect`、`linux_desktop_v3_launch`、`linux_desktop_v3_recover`。
共通fieldは既存どおり`schema:1`、`request_id`、`session`。
LaunchだけがB1 `selection:{scope,revision,app_id}`を持つ。nonce/storage/argv/path/readyを入力に追加しない。
Stopは既存のsession停止経路へ合流させる。
`request_id`はnativeコマンドのIDであり、B2 ownerが発行するwire request sequenceと混同しない。

catalogはB1の`catalog_descriptor/validate_catalog`を正本とし、固定IDは
`gtk3-desktop`と`mousepad-editor-v1`。後者のWayland app_id `mousepad`とは別である。
transportは検証済みcatalogからのみSelectionを作り、Androidは表示と選択の保持を担う。
クリック時にruntime/session/catalog revisionを再確認し、二重clickは二つ目のStartをqueueしない。
コマンド受付の`ACCEPTED`は起動受付の意味で、表示成立は後述のpresentation reportで通知する。

既存`session::Report.desktop`をv3として再利用せず、`desktop_v3`と`desktop_v3_recovery`を追加する。
native snapshotには`linux_desktop_v3` objectを追加し、最低限次を分けて持つ。

- 相関: protocol revision、runtime、session、launch request、nonce、scope、固定app ID。
- transport: preparing/catalog/forwarded/started/stopping/pending/complete、各回収bit、journal pending。
- presentation: B1 summaryのstate/main/startup_completed/first_failure/cleanup_pending/fallback_used。
- 現target: runtime + `TargetKey{session,window,scene,generation}` + Create command sequence。
- 結果: 最初の失敗、後続の回収失敗、操作中か、再Launch可能か。成功bool一個へ統合しない。

UIの日本語messageは固定reasonから生成する。remote stderr、パス、文書内容をmessageへ埋め込まない。
catalog待機、main待機、表示準備、表示中、停止処理、回収要、設定不一致を別表示にする。

## 17. 実装詳細: actorと回収を循環待ちにしない

### 17.1 actorの状態遷移とmailbox

新規`v3/runtime.rs`の`serve()`は、認証済みhandle/router、Binding、ExpectedEvidence、files、
容量1のLaunch mailbox、cancel、report/catalog callbackを受ける。
Launch値は`{request_id, local_socket, selection}`。originの作成はcoreで行い、transportはwire identityとの一致を検査する。

| 状態 | 操作・次状態 | 失敗時に残す証拠 |
| --- | --- | --- |
| 接続・UID確認 | `Binding::capture`、`ExpectedEvidence::capture`、v2/v3 pending検査 | exec前失敗はremote未要求。local割当は別途回収 |
| preparing | `Owner::open`のbefore_execで`Journal::reserve`、Preparedのboot IDを`journal.prepared`へ永続化 | exec送信が不確定ならReservedを残す |
| inspecting | 同OwnerへInspect、厳密catalog/evidence検証 | boot捕捉後の失敗をStart未実行と区別 |
| catalog | heartbeat継続、明示Launchを一件だけ待つ | UI離脱/Stopでもhelperを放置しない |
| forwarding | Selection再照合、local socket検査、router arm、ForwardRequested永続化、streamlocal_forward | remote forward要求のACK喪失を未要求に戻さない |
| starting | StartRequested永続化後、一度だけStart。Started受理後にStartedを記録 | ACK喪失でも再送しない |
| running | heartbeatとterminal監視。総稼働時間の制限なし | terminal理由と回収証拠を分離 |
| stopping | Stop要求、channel/forward/routerとlocal側の回収 | 後述のgate不成立はpending |
| complete | 同ownerの全回収後だけjournal clear、次のInspectを許す | 新Launchは新nonce/新catalogで行う |

v3 `Owner`には現在`next_launch()`がない。v2 ownerと同様、**mailboxをchannel/timer待ちの箇所だけに参加**させる。
外側で`select!(Owner::next(), mailbox.recv())`を行わない。heartbeat等の送信futureを中断すると、
protocolはin-flightなのに送信途中、という状態を作るためである。
v3専用`next_launch`/共通内部pollを追加し、送信を開始したら既存のbounded enqueueが終わるまで次commandを扱わない。
待機中にLaunchが届いても、未応答heartbeatの元ACK期限を保って応答を処理してからStartする。
Stop/cancelはqueued Launchに優先し、旧mailboxはactor終了時に閉じる。

新たなtime limitは既存ownerのprepare 10秒、ACK 5秒、Stop 8秒、close 3秒、heartbeat 5秒を基準とする。
実装時にこれらをUIの起動待ち時間と混ぜない。B1の20秒/10秒はnativeの別時計である。
`busy`のpoll、再接続、heartbeat受理によって元のdeadlineを延長しない。

### 17.2 journal削除とnative/SDK退役の二段階

基準sourceの`v3/recovery.rs::recover()`は、transport cleanup後に`journal.clear()`まで呼ぶ。
通常統合では、これをそのまま呼ぶとSDK targetが残っていても次のownerを開始できる。
逆に「nativeDestroyまで待ってからjournal clear」とすると、shutdownがjournalを待ち、循環待ちになり得る。

採用する順序は次のとおり。**origin単位の退役gate**と**設定保存用のruntime全体のbarrier**を分ける。

1. nativeが当該originのLaunch/input/新target admissionを閉じる。
2. actorがremote Stop/terminal、channel EOF/close、forward取消、router drainを確かめる。
3. coreがlocal Waypipeを停止・joinし、listener/display/SHMを回収する。
   SDK adapterは旧generation、dialog、fallback、現在targetのすべてをdispose/releaseする。
4. coreはoriginに属するlocal worker、client、target、SDK binding、pending operationが0であることを確認する。
   この段階では、退役確認を待つtransport coordinator自身や診断専用panelを「アプリの残存target」と数えない。
5. 同captureのremote/transport証拠と4の退役証拠が揃ったときだけ、journal finish/clearを完了する。
6. 設定保存の場合はここからさらに接続・coordinator・診断panelを含む全runtimeを停止し、
   shutdown complete→nativeDestroy accepted→E1の保存callbackへ進む。

`recover_remote()`相当の内部処理と`finalize_reclaimed()`相当の処理へv3回収を分ける。
前者は既存receipt検査と`Journal::finish`まで、後者はprocess内の退役証拠を要求してclearする。
remote証拠が揃ってもlocal退役未完了なら、durableなComplete stageとjournalを保持する。
公開v3 Recover入口にはgateを必須にし、従来の無条件clear経路を通常callerから到達不能にする。
wire/remote receiptのschemaにSDK情報を追加せず、B2 journalの既存fieldも意味を変えない。

退役証拠にはruntime/session/request/nonce/scopeとcapture generationを束縛する。
JSONから作れる`reclaimed=true`、別runtimeのゼロcounter、古いDestroy ACKを受け付けない。
gate待ちはboundedとし、期限切れはpendingを残してworkerを回収する。次回Recoverは同captureから再開する。
crash後は既存のprevious connection閉鎖証拠と新runtimeの実資源を再確認し、diskのCompleteだけでSDK退役を推測しない。
正常終了・失敗・Recoverのすべてが同じfinalizerを通る。v2 Recoverの形式・振舞いは維持する。

### 17.3 失敗の保持

`first_failure`は一度だけ設定し、後続Stop timeoutやreceipt書込失敗は`cleanup_error`へ記録する。
typed parserのErrを`ready=false`として無視せず、同originの停止へ接続する。
自然終了のterminalはscope検査後に採用し、同tickのreadyより優先する。
app内のClose、装飾のClose、強制Stopは操作intentを別記録にする。main消失だけでprocess回収済みとしない。
main破棄がremote terminalより先着した場合も別rootを採用せず、既存B1どおり停止へ進める。
順序によるpresentation理由と、remoteの`process_exited`・回収成功を別々に記録し、理由を後から消さない。

## 18. 実装詳細: listener・親履歴・表示証拠

### 18.1 nativeでしか作らないorigin

追加入口は`core/session.rs::Session::start_v3(..., origin, ...)`と
`ProtocolState::accept_with_origin(..., origin)`とする。既存`start/accept`はoriginなしのまま残す。
originはLaunch予約時のruntime/session/request/nonce/scope/app IDから生成し、
binding/context/selectionへ`NativeOrigin::matches`で照合する。remote payloadからparseして採用しない。

`Session`がoriginと専用listenerを一緒に所有し、listenerを再利用して別originへ付け替えない。
現行のprivate runtime directory、local Waypipeのlive owner、socket所有検査を引き継ぐ。
accept時点でclosing/local worker失効ならclientを受理しない。同UIDだけをownerの証明にしない。
`ClientState`へ`Option<NativeOrigin>`を固定し、toplevel作成時にWindowRecordへコピーする。
snapshotへも同値を運び、app_idの変更でoriginが変更されないことを検査する。

### 18.2 parentとroleをsnapshotから毎回推測しない

現行`snapshot.rs::reset_unmapped_toplevel()`はrole attributesを初期化し、子のparentを親の旧parentへ書き換える。
v3ではこの結果を「最初からparentなしだった」と解釈してはいけない。
Waylandの通常処理は維持し、その処理前にv3用のparent/role履歴を採取する。

新規`core/linux_desktop_v3.rs`にoriginごとの`WindowHistory`を置く。
WindowIdごとに最初のrole、最新の実parent状態、life、破棄済みかを保持し、
unmap/remapを同WindowIdの履歴として扱う。WindowIdの再利用は既存契約どおり禁止する。
parentのnone/resolved/unresolved/invalidはB1型へ変換し、lookup失敗と未設定を区別する。
roleは最初の採用時に固定し、main→dialog・dialog→main変更を拒否する。
main破棄とrole履歴はowner退役まで保持するが、破棄済み全dialogを無制限にsnapshotへ積み上げない。

1. 同originで、Launch前の窓集合に含まれない正しいapp_idのrootをmain候補とする。
2. main未採用の間はcandidateを保持し、SDK target/inputを公開しない。StartedとMappedの両方を待つ。
3. 採用後はそのWindowIdを固定する。dialogは同originの実parent chainが固定mainへ到達するときだけ採用する。
4. second root、循環、未知parent、異origin、role変更は停止理由を残す。
5. slot/layout/pointer/keyboard/IME targetは同じadmitted集合を参照する。描画だけ許して入力を漏らさない。

`Observation.global_window_count`は製品が管理する全窓の現行定義に合わせる。
9を超える入力をmodelへ渡してparse失敗にするのでなく、10枚目の作成拒否をcoreで記録する。
`windows`は現在originのlive/unmapped窓と必要な破棄観測に限定する。
破棄観測を含むtickでもB1の件数条件を満たすよう、slot解放と観測の順序を決め、
9枚同時＋dialog破棄/再作成の境界テストを追加する。popup/subsurfaceを独立toplevelとして数えない。

### 18.3 Observationを作る順序

同じcore tick内で、次の順に観測をまとめる。

1. 現在runtime/session/originを照合し、transport terminal/cancel/visibility/scene/XRを採取する。
2. compositorをdispatchし、WindowHistoryとadmitted集合を更新する。
3. current main用Createと、その**同じ**runtime/target/sequence/actionのACKだけを取得する。
4. SDK bindingとrendererの当該target frame・pause状態を取得する。
5. monotonic clockから一度だけ`now_ms`を採り、B1 `Startup::advance`を一回呼ぶ。
6. stopping/pendingなら新規target/inputを閉じ、同ownerの停止を要求する。readyなら初回完了をlatchする。

最初のmain採用tickは`current_create=None`でmodelを進め、採用されたmainからCreateを発行する。
次tick以降に実Create/ACKを渡す。未採用candidateへ先回りしてSDK panelを作らない。
retired targetの遅着ACK/frameは破棄して旧世代の解放処理だけに使い、現targetの証拠へ混ぜない。
core内で誤って不一致証拠を組み立てた場合は、modelの`PRESENTATION_STALE`を握り潰さない。

証拠の収集元は次のように固定する。

| B1 field | 製品側の観測元 |
| --- | --- |
| `panel` | nativeのpanel planと実apply結果 |
| `ack` | `SpatialPanelAdapter`からのCreateに対する既存command ACK |
| `sdk` | register/release_sdk_bindingのnative管理表。diagnosticを区別 |
| `frame` | rendererのTargetKey別frame counterと両pause状態 |
| `scene` / `activity_visible` / `xr_focused` | 現在のAndroid/XR lifecycle通知。古いsceneのキャッシュを使わない |

startup完了後のscene/世代交換で20秒/10秒を再武装しない。
`startup_completed=true`を保ちつつ、現在表示が未成立ならUIは再表示準備中とし、入力を現targetへ限定する。
装飾fallbackはB1が許す起動前一回だけで、元のSDK deadlineと失敗targetの回収義務を保持する。

## 19. 実装詳細: Androidの保存transactionと実barrier

### 19.1 既存writerの一本化と設定の有効化

`LinuxStorageStore`は既存`LinuxProfileStore`と同じ`linux-profile/remote-linux.json`を扱う。
E1登録後も`saveLinuxConnection()`から旧storeへ直接書くとrecord/anchorと食い違うため、
E1の登録証跡があるprofile編集は必ずE1の`save/resumePending`へ回す。
証跡なしの旧v2利用だけは既存writerを維持する。破損した証跡を「なし」と扱わない。

新規`LinuxDesktopV3ConfigRepository/Store`は`noBackupFilesDir/linux-desktop-v3/connection.json`へ保存する。
保存objectは`{schema:1,kind:"linux-desktop-v3-saved",profile_binding,storage_record_sha256,configuration}`。
atomic publish/no-follow/CAS/fsync/readbackは既存storage/profile file adapterを再利用する。
Saved.tokenは保存bytesのdigest、configurationの検証は16.1を使う。
期待tokenとprofile binding/storage digestが違えば保存・読み出し利用を拒否する。

有効な版は`noBackupFilesDir/linux-desktop/active-backend.json`で明示する。
objectは`{schema:1,protocol_revision,profile_binding,configuration_sha256,storage_record_sha256}`。
v2のstorage digestはnull、configuration digestは従来pinsのcanonical digest。
v3は16.1のconfiguration digestとE1 record digestを持つ。未知版や両方を表す値は拒否する。
既存v2にmarkerがないケースは、v3 config/登録証跡もない時だけlegacy v2として読める。
v3登録後のmarker欠落・破損は設定不整合にし、旧v2へ暗黙復帰しない。

有効化手順は次で固定する。これはE1と別fileを跨ぐため、途中失敗を全体rollback成功とは扱わない。

1. UIで選んだ版、profile案、raw config、各expected tokenをcaptureし、厳密parseだけ先に行う。
2. runtime admissionを閉じ、全回収barrierを取得する。
3. 必要なE1 save/resumeを実施し、Matchedのprofile/recordをreadbackする。
4. 同じcaptureに結合したv3 configをCAS publish/readbackする。
5. active-backend markerを最後にCAS publish/readbackする。
6. 全fileを再読し一致した場合だけ、次runtimeのconfigを生成してadmissionを開ける。

3〜5の途中で停止したら、旧markerと新fileの組合せを採用しない。起動時のread-only reloadで不一致を示し、
明示的な保存再開へ誘導する。E1のPendingは`resumePending`、config/markerの未完了は対応するCASを使う。
新storage IDの再生成、旧設定での自動Launch、成功だったかの推測はしない。
v3からv2へ明示的に戻す場合も全回収後にmarkerを更新し、v3 storage/configは削除せず保持する。

### 19.2 workerとSDK thread

全保存操作のownerを一つにし、`productWorker`は要求を提出するだけとする。
runtimeの開始/停止を所有する既存`worker`がadmission epochを進め、Connect/Launch/Recover/再生成を制御する。
SDK操作はUI threadへ非同期依頼する。UI threadを待つworkerと、そのworkerを待つUIの相互待ちを作らない。
`ReclamationBarrier.withReclaimed`のcallbackへ入る前に停止証拠を揃え、callbackは保存だけを同期実行する。
UI/SDK callbackはruntime/scene/epochで照合し、保存中の新runtime生成と古いcallbackの採用を拒否する。

現行`stopRuntime()`はshutdown snapshotとnativeDestroy結果を記録する。
これをtypedなStopReceiptへ拡張し、E1へ渡す全counter・remote pendingを同runtimeから取り出す。
`handle==0`で早期returnしたことは`stoppedRuntime>0`の代わりにならない。
同epochの実StopReceiptがあれば再使用し、なければ接続/Launchを許さない最小runtimeを生成・停止して実証拠を得る。
以前のprocess/connection回収が不明な場合は、その最小runtimeのゼロcounterで不明分を消さずRecoverを先行する。

`SpatialPanelAdapter.close()`はActivity/scene自体の終了用なので、通常の保存では無条件に呼ばない。
新規`retireRuntime(runtime, epoch, callback)`を追加し、対象runtimeの全bindingを退役させ、
diagnostic/window/装飾を含むdispose、native detach/release、pending callbackの処理を確認する。
adapterの今後のscene利用を閉じず、disposal失敗を空配列へ置き換えない。
StopReceiptは最後のnative snapshotとadapterの同epochの結果を結合し、
一項目でも未完了なら保存せず、何が残ったかだけを固定reason/counterで通知する。

## 20. 実装詳細: 検証caseと判定器

### 20.1 実装と一緒に増やす検査

| ID | テスト配置・入力 | 合格条件 |
| --- | --- | --- |
| V01 | 新configuration共通fixtureをPython/Rust/JVMで読む | canonical/digest/受理集合一致。重複・未知・サイズ超過・組違い拒否 |
| V02 | builder実成果物、compiled inventory、source gateの欠落変異 | 完全な場合だけexport可、変更後のinputs再検査、不完全/未知sourceで実行不可 |
| V03 | `v3/owner/tests.rs`へidle/heartbeat送信途中/Launch/Stop競合 | 送信futureを途中破棄せず、Startは最大一回、元deadline維持 |
| V04 | 新`v3/runtime` SSH fixture、Start ACK喪失/forward失敗 | journal stageと要求実態一致、再送なし、remote/local各cleanup独立 |
| V05 | Recover receipt成功＋SDK遅延/旧generation未解放 | journalを保持し、同capture退役後だけclear。crash再開も一致 |
| V06 | 実Wayland clientで別listener/同app_id/親不明/unmap/remap | origin偽装不可、parent喪失をrootにしない、main/role固定 |
| V07 | B1純粋model＋core adapterの実tick | Started/Mapped逆順、期限ちょうど、terminal同tick、旧ACK、scene交代、fallbackの判定一致 |
| V08 | 実9窓、dialog連続開閉、input target切替 | 10枚目拒否、履歴上限、旧target入力拒否、既存Termux/v2回帰なし |
| V09 | Kotlin設定save、E1各publish境界とactive marker crash | 新ID再生成なし、未完成pairで起動不可、明示resume可 |
| V10 | worker/SDK callback遅延・dispose失敗・並行profile編集 | 全資源回収前の保存なし、古いepochの成功証拠を採用しない |
| V11 | 下記host通常v3 fixture | 実SSH/実C2/実Mousepad/production compositorで起動・操作・回収 |
| V12 | Quest operator＋実Android保存adapter | 実APKのSDK証拠、合成入力のfile oracle、保存再読・再起動・回収 |

既存B1/B2/C1/C2/E1とv2 frozen-source検査を削除せず、上記を追加する。
fixtureのexpected digestを実装から毎回生成して合格させない。
controller/modelだけのテストはV11/V12の代わりにならない。

### 20.2 host acceptanceの実行境界

`scripts/check-linux-real-app --stage acceptance --artifact-root <private-dir>`の既存入口から、
新規`linux_real_app_v3_acceptance.py`へ委譲する。対象packageは既存`--reviewed-package`で指定する。
現在のCLI終了値は`a0_passed`だけを参照するため、stageごとに結果validatorと終了値を分岐する。
feasibilityは従来条件、acceptanceは新規validatorで必須caseと回収の成立を確認して0、それ以外は非zeroとする。
fixtureは専用account/SSH host key/temporary storage/immutable bundleのIDを捕捉し、
通常の`ConnectSpec`→v3 controller→C2→local Waypipe→production ProtocolStateを起動する。
SSH mockを実host acceptanceと名付けない。hostにはQuest SDKがないため、SDK表示成功はV12の責務とする。
hostに合成SDK adapterを使うcaseはその旨を別記録し、実SDK証拠と合算しない。

GUI oracleは既存A0の固定fixture/入力生成/byte照合を部品として利用できるが、
appのspawnを直接行って通常controllerを迂回するA0実行全体は流用しない。
固定UTF-8入力fileは試験専用Documentsへ準備し、GUI操作後の別名file digest/sizeを比較する。
設定はA0で成立した可変キーを使い、変更・Close・同storage新nonceでの再起動まで実行する。
強制切断等の注入は専用fixture ownerが操作し、本来のdesktopや共有ADBを停止しない。

受入れ結果は`acceptance.json`へ、schema/stage/revision/run ID、bundle/package fingerprint、
各caseのpass/fail/not_run、最初のreason、cleanup結果、証拠file digestを保存する。
stage全体は必須caseがすべてpassの時だけpass。skipを集計から除外しない。
`production_acceptance_passed`と`quest_verified`等の既存flagは元の判定範囲を維持し、
hostだけでQuestやHを含む条件をtrueへ変更しない。

### 20.3 Quest operatorの具体的な契約

新規operatorのCLIは`--serial`、`--case`、`--artifact-root`、`--timeout-seconds`を基本とし、
caseを`inspect/demo/editor-io/editor-settings/reconnect/recover`の固定集合にする。
timeoutはcaseごとの既定値と上限を持つ。run nonceはoperatorが生成し、APKのsource digestと共にcaptureする。
同時に二つのcaseを走らせず、終了時にrunごとのfinal receiptを必ず要求する。

debug receiverはdebug sourceSetだけへ置く。run ID・case ID・期待source digestを検査して、
`NativeSessionHost`の通常設定/Inspect/Launch/Focus/Close/Stopへ委譲する。
実機のprofile/storage値は通常保存物を使用し、broadcastで差し替えない。
合成text/保存名はAPK側の固定fixtureに限定し、任意shellや任意remote pathを渡せないようにする。
run開始前にユーザーが利用中のownerがあればbusyを返し、別runのStopを発行しない。

各操作は時間固定のsleepで成功とせず、相関済みreceiptを待つ。
正常系ではcatalog→Launch受付→Started→固定main→Create ACK/binding/frame→focus→入力→
dialog/save file oracle→Close→remote/local/SDK回収を順に判定する。
いずれかのtimeoutでは同runへStopし、診断採取を終えた後、回収成功と本来のcase失敗を別記録する。
`editor-io`は実file oracleを取得できない場合にnot_run/失敗とし、入力APIのACCEPTEDだけでpassにしない。
全自動case完了後、H用に通常UIを起動して渡し、物理入力・視認は未確認と明記する。

## 21. 実装単位の依存・引き継ぎ

| commit単位 | 変更範囲 | 次へ進める条件 |
| --- | --- | --- |
| A | I0記録、I1 pinning/codec/export | V01/V02、実bundle成功。対象package不成立なら環境課題を明記し独立実装へ |
| B | I2 v3 actor、mailbox、deferred journal finalizer | V03〜V05。通常UIはまだv3を選べない |
| C | I3 origin/WindowHistory、I4 Observation adapter/SDK evidence | V06〜V08、全世代退役gateをBへ接続 |
| D | I5 E1実barrier、設定repository/marker、launcher/状態表示 | V09/V10、v2設定回帰、runtime設定の排他表を検証 |
| E | I6 実通常v3 fixture、CI job、判定結果 | V11と同headの既存host/Android/A0検査。環境不足をskip成功にしない |
| F | I7 Quest operator、APK配備、実保存/SDK自動検証 | V12、H用のAPK/version/短い操作手順を固定 |

一つのcommitへすべてを詰めず、各単位で実装差分・検証・未実施を残す。
privateな環境値や詳細run artifactはwork-rootへ、設計修正と再利用できる結果は本書または関連記録へ追記する。
本書の設計完了はA〜Fの実装完了ではない。現時点では**計画のみ**で、V01〜V12とHは未実施である。

### 検証コマンドの使い分け

以下はrepository rootから実行する。新規caseは各単位で既存suiteへ登録し、未登録の個別実行だけで完了にしない。

```bash
# 計画・依存と既存suite。実装単位の対象を選び、統合時にallを実行する。
python3 scripts/check-design
scripts/check-host --suite all

# I1: source状況、実hostでのbuildと静的export（--print-configはこれから実装）。
scripts/build-linux-desktop-v3-bundle --source-status
scripts/build-linux-desktop-v3-bundle --reviewed-package "$EIDOLON_REVIEWED_PACKAGE"
scripts/build-linux-desktop-v3-bundle --print-config --build-id "$EIDOLON_V3_BUILD_ID"

# I6: 実通常v3。実装前はAPP_V3_NOT_IMPLEMENTEDを返す既存入口。
scripts/check-linux-real-app --stage acceptance \
  --reviewed-package "$EIDOLON_REVIEWED_PACKAGE" --artifact-root "$EIDOLON_ACCEPTANCE_ROOT"

# I7: 下のoperatorはこれから実装。実機接続確認とbuild後に実行する。
adb -s "$EIDOLON_QUEST_SERIAL" get-state
scripts/build-android 1
scripts/quest-check-linux-real-app --serial "$EIDOLON_QUEST_SERIAL" \
  --case editor-io --artifact-root "$EIDOLON_QUEST_ARTIFACT_ROOT"
```

環境変数にはI0で確認した対象とprivate artifact directoryを設定し、sample IPや推測build-idを使わない。
新規通常v3 CIは`.github/workflows/linux-real-app.yml`に追加し、既存A0 jobを置換しない。
`.github/workflows/check.yml`のhost/Android結果と、実通常v3 jobの結果を同じheadへ束縛する。
