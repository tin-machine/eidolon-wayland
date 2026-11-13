---
id: android-ime-implementation-record
title: Android IME implementation and validation history
type: historical
feature: android-ime
created: "2026-11-07"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: [android-ime-design]
related_sources:
  - android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt
  - native/core/src/compositor/ime.rs
---
# 日本語入力の実装・検証記録

2026-11-07開始、2026-11-08更新。基準`795f184`。[設計](android-ime-wayland-plan-20261107.md)のNX09をユーザー指示で再開した。
通常の日本語入力の有効化と案A採用は、A1/A2の実キーボード・候補表示の確認を待つ。
本記録の合成入力を、実IMEのかな漢字変換やHMDの受入れへ読み替えない。

## Build149〜151: IME接続と直接入力への復帰

単一の物理キーボード入口とpre-IME入口にCtrl+Spaceのownerを接続するdebug端末パネルを追加した。
通常UIの自動有効化は行わず、[明示probe](android-ime-terminal-probe-20261108.md)を開始した間だけ使用する。
切替前のCtrl解除、変換中のraw排他、終了時のnative reset/disable ACK・Android接続解除・
元のSDK targetへのfresh epochによるfocus復帰を別々に確認する。
入力状態表示も接続・終了・復帰待ちを区別し、待機中を通常の入力可能状態に見せない。

| 版 | 観測・変更 |
| --- | --- |
| 149 | build/lint/package/JUnit 92件成功。最初は入力先が準備できたが、後続のfocus解除後にtoggleが受理されなかった。app/SSH/SDK targetは正常、native focusだけがnullだった |
| 150 | 同nonceの明示focus要求を追加。新しい要求とfocus epoch/tokenの観測を照合し、旧metadataで即完了しない。ONは`BOUND`、View/Window focus・IC接続が成立。OFFでnative解除は済んだがIMMが接続を保持し、raw復帰は期限切れとなった |
| 151 | close後のeditorを永久失効させ、text editor判定false・新IC=null・focus/click不可にする。旧served viewのrestartInputでnullを通知し、非text親へfocusを移す。ON→OFFでAndroid解除と元targetへのraw focus復帰がともに成功 |

Build151のON時は`BOUND`、View/Window focus・IC active・IMM active/acceptingがtrueだった。
OFF時は`raw_return=COMPLETE`、`android_released=true`、IMM active/accepting=false、raw gate解除、
editor退役を確認し、最後にprobe停止ACKを取得した。失敗時のgateを緩めて成功扱いにはしていない。
継続操作はDUMP権限付きdebug receiverへ送り、Activity起動によるpauseを避ける。statusはmetadataの読取りだけである。

Build151のdebug/release双方でbuild・lint・APK検査とJUnit 92件が成功した。
release DEXにはIME probe/case/receiverのclass descriptorが存在しないことも確認した。
PC scriptsは140件中133件成功・実APK指定7件skip。通常の物理Ctrl+Space、実かな漢字変換、
候補視認・文字配送の最終受入れは未完了である。今回のON/OFF要求は固定debug操作から行った。

| 項目 | Build151識別 |
| --- | --- |
| debug APK SHA-256 | `1f6a0ba5d29a3362a5d8e7c3a89eee377519df512e01b7b59bd45c8142bdb29e` |
| source digest | `706fc9884861308733d16a9e8f8a80c1fcf23861f7487707e3a10d7ef700534d` |
| 基準commit | `37f7c51` + source archive保存済みの未commit変更 |
| release APK SHA-256 | `9488c0a9b49a6601f69bb733c92537f5151a92b056b61ce466bc11c5e07da2b5` |

個別試行は作業rootの`terminal149/`・`terminal150/`・`terminal151/`、実行scriptとsource archiveは同taskへ保存した。

## Build146の統合compile

Host接続・ACK期限監視とCtrl+Spaceの判定基盤を含め、build・lint・APK検査・JUnit 64件が成功した。
APK SHA-256は`0f8fd699c234a94aaf5f6b4baa4e5848b199b8966e2c9b43a4f4d1410b0b35db`、
source digestは`611a9e39c0385150abcd189a2ff7d6142e43bc54b493fb4985dfdf27ba2507d5`。
基準commitは`795f184`で、未commitの入力source archiveをAPKと一緒に保存した。
Ctrl+Spaceは左右Ctrl、repeat、遅いkeyup、pause/device変更時の所有をpure testで確認した段階で、通常UIへはまだ接続していない。
IMEへ接続する前のCtrl解除をさらに早める修正と、SDKのクリック経路を通すprobeはBuild146以後の変更として扱う。

スリープ対策も再確認した。`prox_close`でAsleepからAwakeへ移り、
`automation_disable`後は約180秒で再びAsleepへ移った。
[手順と復帰条件](development-automation-and-debugging.md#無人運用とライフサイクル試験)を更新した。
## Build147の追加検証

Android接続待ちのCtrlを即時解除する`start()`と、SDKの公開`PanelDisplay.dispatchEvent`を通す固定tap probeを追加した。
debug build・lint・APK検査とJUnit 68件が成功。APK SHA-256は
`766998cd2696f9ad137f46542b2f262f4acebe853cd75dfc0c88209ed455c71e`、source digestは
`26eec1886b8772132a5953d0c5e38c2a47e69047fb675e319e80bd26c2058ab2`。
CLI/schemaはSDKへの要求提出・Viewへのtouch到達・Window focus・framework接続生成を分けて判定する。
PC scriptsは112件中105件成功・実APK指定が必要な7件をskip。実APK破損検査7件はBuild146指定で別に成功した。

Build147導入前にWi-Fi ADBがofflineとなり、再接続もtimeoutした。先の休止復元後に発生したが、接続断の原因をsleepと断定しない。
ユーザーの電源ボタン操作後に同一機体を再照合でき、Build147を導入した。以後はユーザー指示により`prox_close`を維持し、自動解除しない。

最初の起動はAwakeのままtimeoutし、Eidolon processはなく境界ダイアログが前面にあった。
以前の許可に基づき`debug.oculus.guardian_pause`を空値から`1`へ変更し、MainActivityを明示起動した後、再試行が成立した。
`metavr`は未導入。この条件を通常の境界・passthrough受入れへ流用しない。

SDK tapの9段と停止ACKを完了し、A/B/Cすべてで`window_focus=true`、`imm_active_view=true`、
`imm_accepting_text=true`、`showSoftInput=true`を観測した。frameworkのInputConnection生成増分はA=2/B=1/C=2、
Viewに届いたDOWN/UPはpanel 0が各2、panel 1が各1だった。
終了時は全欄のView focus/IMM active/accepting textが解除されたことも個別に確認した。
これはSDKのクリック経路で実framework接続が作られた証拠であり、実かな漢字変換・物理入力・HMD候補の成功ではない。
Ctrl+Spaceの通常UI接続とendpointの実機配送は未完了として区別する。

## Build148の統合と既存入力の確認

IME終了時のUI/worker競合を独立レビューで修正した。古いconnectionを取得したworkerが、
UIの停止回収後にraw gateだけを閉じる順序があり得たため、identity照合とgate更新・closeを同じUI taskへまとめた。
endpoint未生成時は従来どおり早期returnし、reset/stopの同期focus無効化を維持した。
独立したNX14aの自プロセスCPU表示も統合した。

Build148のdebug build・lint・APK検査・JUnit 80件が成功し、Questへ導入した。
APK SHA-256は`adf93b42e17c4dc717a021df9140bdcd5ff7ea50a2e53477ded824b5f7bba2fe`、
source digestは`99c33c71a91d987ca656edd3c1b9b65c574d6f121dedb5fd0b7eec4cf24d22a6`、基準commitは`f749e70`。
既存のキーボード自動検証8段階と通常UI復帰が成功した。これは合成入力であり、物理キーの追加受入れとはしない。
自プロセスCPUは製品UIDで5つの異なる約5秒区間を取得し、有限・非負・1コア100%の定義を確認した。
releaseは直前のBuild147でbuild・lint・APK検査・JUnit 68件が成功し、debug入口が含まれないことを確認済み。
Build148全体のrelease検査を実施済みとはしない。

## Build145で確認した範囲

Build144の後にnative wire v2と固定15操作の比較probeを統合し、同じQuest 3/API34へ導入した。
Hostの実runtime接続endpointはこのAPKには含まれず、後述のPC検証までである。

| 項目 | 識別・結果 |
| --- | --- |
| APK versionCode | 145 |
| APK SHA-256 | `942016a5d1e6443fac90ddf6b141f0108a10d33c0217778200d5c714b59186f4` |
| source digest | `828c6afac5819a3e639445b94a84f3cd5d77cd5b340cbfeee0a506beb1b0fd82` |
| source commit / dirty | `795f18408c158c12cfad0335b1b61dce2a9be926` / `true` |
| IME wire v2 | 実機instrumentation総合PASS。13 checkpoint、配送19、拒否34、不正client隔離8 |
| 資源・runtime | 残client/object/surface/IME objectすべて0。runtime再生成・停止・破棄、`pending_cleanup=[]`を確認 |
| Android固定操作 | A/B/Cのfocus・show・hide・restart・clear-focus、全15操作と終了時解除観測を完了。本文注入なし |
| Android入力接続 | 各欄のrequestFocusはtrueだが、panel/editorの`window_focus=false`、showSoftInputはfalse、framework接続callbackはA/B/Cとも0 |
| 独立synthetic | 非attachの別Viewへの直接InputConnection fixtureはPASS。実IME接続の成功には含めない |
| 終了 | 同nonceの停止ACKと破棄要求を確認。直後の`views_detached_observed=false`で回収完了は未判定 |
| 受入れ | 実IME・物理USキーボード・HMD・pointer復帰はすべて`not_verified` |

awake条件での15段すべてで、Activityはdisplay 0の`window_focus=true`、比較パネルは別displayの
`window_focus=false`だった。View単位のfocusは移動できたが、IMMの入力接続は生成されなかった。
この観測だけで原因をスリープ、権限不足、IME非対応のいずれかに断定しない。
SDKのWindow/displayとAndroidの接続条件を追加調査する。proximityを変えた試行の結果はまだ含めない。

## PC上の実foot・Waypipe・PTY検証

固定した未改変foot 1.28.0を使い、Eidolon ProtocolState → Waypipe client/server → foot →
footが開いた専用PTY sinkという実経路を3回連続で検証した。既存シェルやSSHのstdinへは入力していない。

- 10 checkpoint。未確定描画のpixel変化を確認し、その間のPTY配送は0 bytes。
- 取消後は固定した空画面へ全layerのpixelが復元し、PTYは0 bytes。旧activation拒否後も0 bytes。
- 固定日本語3文字の確定はUTF-8 9 bytesを一度だけ受信。確定後もpixelが復元した。
- 正常配送4・拒否1、client/object/surface/window/pixel/poolの残数0、所有process group 3つの終了を確認。

footがserialを照合した後に受理し、PTYへ一度だけ届けた証拠である。
pixel比較はこの固定空画面での差分と復元を検査しており、字形、HMD可読性、全Unicode、対話シェルの操作は未確認。
PC fixtureの依存ライブラリ・build設定はTermux版と同一とは扱わない。入力本文や受信bytesそのものは保存していない。

初回はfoot既定の512MiB SHM poolが既存上限に抵触したため、managed launcherと同じ
`--override=tweak.max-shm-pool-size-mb=0`をfixtureへ指定した。serverの上限は緩和していない。
別の初期試行では固定300ms待機の取消pixel比較が不一致となったが、そのrunの原因は断定できない。
最終fixtureは空の初期画面と描画条件を最大2秒待つ方式とし、3連続PASSを確認した。

## Build144で確認した範囲（履歴）

API34のQuest 3へdebug APKを導入した。基準commitからの未commit sourceであるためdigestを併記する。

| 項目 | 識別・結果 |
| --- | --- |
| APK versionCode | 144 |
| APK SHA-256 | `86fc1dda2b93303da452bcd0f6f4257d78574def0b725d303138147e35e607bb` |
| source digest | `8c4eb57b4808c87c3c2707b19094b5393c3a45d70ae268f8e81ac9725b72aa9b` |
| OS / locale | `UP1A.231005.007.A1` / `ja-JP` |
| 選択IME | Oculus標準KeyboardInputMethodService、versionCode `1106998721` |
| IME subtype | metadataで`ja-JP`、mode=`keyboard`を確認 |
| IME wire v1 | 8 checkpoint、不正client隔離8、配送7、拒否23、残client/object/surface/IME objectすべて0 |
| runtime | 再生成・停止・破棄に成功。最初の失敗と復旧は下記 |
| Android比較パネル | 2枚・標準入力欄A/B・独自editor Cがattach、別Viewへの固定InputConnection検査PASS |
| パネル終了 | 同nonceの停止ACKと破棄要求を確認。直後の`views_detached_observed=false`なので回収完了は未判定 |
| 未確認 | 実IMEの変換候補、物理USキーボード、HMD視認、IME使用後のpointer復帰 |

比較パネルの観測時には全editorの`window_focus=false`だった。attachだけでAndroidの入力接続が成立したとは扱わない。
Build145ではfocus/show/hide/restart/clear-focusの固定操作を追加し、上記のとおり実frameworkの接続生成は観測できなかった。
既定の`show_ime_with_hard_keyboard=0`は変更していない。この値やsubtypeだけで物理キーの日本語変換可否は決めない。

## 試行錯誤と対処

1. 最初の実機IME wire本体は成功したが、instrumentation末尾の再生成runtimeに以前のTermuxセッションの`pending_cleanup`が1件残り、総合FAILとなった。
   通常のSSH接続で、保存済みhost pinとcredentialを照合してremote reconcileを実行した。
   journalを削除せず、正常接続・pin不一致・認証失敗・取消の4ケース成功を確認。その後、同じAPKでIME wireを再実行し、`pending_cleanup=[]`と全runtime回収を確認した。
2. PCのfull core testではkeymap FDの開始前個数が0から1となり、後続がlock poisonで失敗した。
   同processの独立keymap testだけが共有`FIXTURE_LOCK`を取得していなかった。修正前に並列実行で再現し、生成から破棄までlockを保持するtest修正後、keymap4件とcore52件が成功した（既存ignored 1件）。
   leakのassertと製品側の所有規則は変更していない。
3. 実Waypipe両端の検査で、snapshot取得と配送の間にcursor rectangleだけが更新されると、古いserialとして変換が拒否される競合を再現した。
   Build145では意味のある編集状態に`edit_revision`を付け、同revision内の既知serialだけを許容した。wireの`done`は適用時点の最新commit回数で送る。
   queue済みの本文を新targetへ付け替えたり、失敗した確定を再送したりしない。
4. 全clientで共有するIME object上限64だけでは、一つのclientが正常な別clientを切断させられた。
   client単位16、全体64の上限へ分け、Aが上限を保持した状態でBが入力できること、A超過の隔離後もBが継続することを検査した。
5. KotlinでIME subtypeの非公開`subtypeId`参照がcompileエラーになった。公開APIの`hashCode()`とlanguage tag/modeへ変更した。

## Build145に含めた実装

- native wire v2は13 checkpoint、配送19・拒否34、不正client隔離8。geometry競合、semantic変更拒否、接続失効、serial wrap、client別上限を追加した。
  wrap検査はcounterを境界へseedしたwire検査であり、40億回のcommit実行ではない。
- Android側は局所compositionの上限を4000 UTF-8 bytesへ合わせ、UTF-16/UTF-8の境界を検査する。
  `last_sequence`をnativeから受けて採番し、局所editor generationとnative sequenceを区別する。
- runtimeはSDKのsession/window/scene/generation、focus token、input epochを照合する。
  受付ACKと配送ACKを区別し、queue満杯でもurgent resetによって古い入力を失効させる。
- `nativeImeUi`はUI専用の取得経路で、surrounding本文を通常snapshot、poll event、ログへ載せない。
  backendは既定off、当面の有効化はdebugの限定fixtureのみ。通常UIの日本語入力の受入れは未完了。

## Build145以後のendpoint実装

Android editorから実runtimeへの接続を実装し、pure JVMで既存28件と新規15件の計43件がPASSした。
このpure検証に加え、Build146/147でAndroid facadeを含むcompile・JUnitを確認した。実IME変換の成功とは区別する。

- private snapshotはschema・数値型・文字列IDを厳密に確認し、foot profileではsurrounding本文を取り込まない。
- 接続時のruntimeとSDK targetを固定し、backend enable後のurgent reset完了と新しいepoch、focus ACK、private snapshotの一致を経てbindする。
- queue受付と配送ACKを区別し、ACKは最大32件・8秒期限。本文をACK mapへ保持しない。worker停止中もUI側の監視で期限を適用する。
- 接続待ちのIME callbackはbufferへ保存せずfalseを返す。古いgeneration・target・epoch、拒否・期限切れの文字を再送しない。
- rawキーとIME経路を排他とし、post-IMEの非文字キーも固定した入力先へだけ送る。
- 終了時はAndroidのhide/clearFocus、native reset/disable両ACK、epoch更新を確認する。
  Android focus/IMMの解除とnative ACKは別に観測し、解除未確認のままraw入力を再開しない。

独立レビューで終了後のAndroid focus保持を指摘され、hide/clearFocusと解除観測を追加した。
通常UIでは有効化せずdebug明示APIのみである。Ctrl+Spaceによるon/offは別途実装・検証中で、
Eidolonの配送モード切替とIME内の日本語選択を同一視しない。一般Android APIで日本語モードを強制できるとは約束しない。

## 再現入口と記録

自動パネル操作は[probeの手順](android-ime-probe-20261107.md)を使用する。
native wire検査は、導入済みAPKと現在のsourceを一致させてから実行する。

```bash
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case ime-wire --instrumentation --timeout 120
scripts/check-host --suite model
scripts/check-host --suite lifecycle
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/tests/test_ime_wire_result.py
```

個別ログ・端末識別子・保存APK・source入力archiveは、実行時に読み直す作業rootの
`eidolon-ime-implementation-20261107/`へ0600で保存した。主要な証拠の相対pathは以下。

| 証拠 | 作業root内の相対path |
| --- | --- |
| Build145 APK・source入力archive | `builds/20261107T231946-685496/` |
| Build145実機wire v2 | `quest-checks/20261107T232315-d6ccd0b6/` |
| awake比較probe start/exercise/stop | `probe145-awake/` |
| 実footの固定入力・再現入口・3run | `foot-pty/README.md` |
| endpoint契約・利用例 | `editor/runtime-endpoint-API.md` |
| pure JVM 43件の実行引数・結果 | `editor/runtime-stage/test-commands.json`、`test.log` |

各pathは上記task directoryからの相対であり、公開文書には端末識別子、個人IP、室内画像、実IME本文を複製しない。
Build145以後のendpoint修正をBuild145の実機成功へ含めない。
Wi-Fi ADBが一度復帰した後にofflineとなった事例も記録した。sleepが原因と断定せず、再接続後に機種・物理端末を照合してから検査を続けた。

## 次の確認

1. 開発中の`prox_close`を維持し、境界一時停止の条件も記録して実機検証を続ける。
2. debug endpointとCtrl+Space ownerを接続し、native配送ACKと終了後の直接入力先の復帰を検証する。
3. 接続が成立した段階で、かな漢字変換、物理US入力、IME終了後のpointer/raw復帰とHMD視認を確認する。
