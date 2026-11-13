---
id: android-ime-design
title: Android IME and Wayland text input design
type: architecture
design_status: proposed
feature: android-ime
created: "2026-11-07"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: []
related_sources:
  - native/core/src/compositor/ime.rs
  - native/model/src/ime.rs
---
# 日本語入力計画: Android IMEを先に検証し、Wayland text-input-v3へ接続する

作成日: 2026-11-07。状態: **ユーザー指定で実装を再開。A1/A2の限定試作とC1を準備中。案A採用・実機IME受入れは未完了**。
調査基準: `dde7941f28170b9ba58d3a25f2ab10ef5d549408`。実装開始時にmainとの差分、APK、Quest OS、IMEの版を再確認する。
作成時はmain更新`2b6a38032bc1d8777644d14dbef96b2843c36bdf`を取り込み、NX09を保留してNX10を先行していた。
追加レビュー後、ユーザーが日本語入力の実装開始と、その後の残ゴール継続を明示したため、`795f184`からNX09を優先して再開した。
本書を日本語入力の実装方針とする。実装・実機検査の進捗は[実装記録](android-ime-implementation-20261107.md)へ分離する。
2026-11-07の実現性レビューは`b0b28ecad8a26b15255e807d7adf404752c724b9`を基準に実施した。
当初のレビューでは固定依存とfoot sourceを確認し、IME表示条件・編集能力・古いtransactionの破棄条件を補った。その時点では実機検証と実装再開は未実施だった。
追加の独立レビューは`8b0fcb4`を基準に、Quest/SDK・protocol・実装費用の三担当で実施した。
以下へSDKの操作復帰、固定Smithayの具体的な反例、試作準備と採用判定の分離を反映した。

## 1. 決定と既存計画との関係

ユーザーは、複数のLinuxアプリを空間上で使う最終目標に合わせ、compositorへ`text-input-v3`のサーバー側を追加する方針を選択した。
**案A: Quest/Android側のIMEを利用する方式を先に小さく検証し、必要な日本語入力が成立すれば採用する。
案A固有の制約で成立しない場合は、案B: Termux側のFcitx5と日本語エンジンを利用する方式へ進む。**
実装量の増加を許容するが、端末固有の成立性を確認する前に大きな橋渡し実装へ進まない。

本書をNX09再開時の方式選定・検証順・共通IME契約の正本とする。
[従来の日本語入力計画](japanese-input-plan-20261107.md)にあるFcitx優先の順序を更新し、同書のTermux調査は案Bの根拠として再利用する。
[NX01〜18の全体計画](next-phase-termux-startup-linux-gui-plan-20261107.md#implementation-order)の独立作業は継続可能。
一般GUIの個別受入れはNX15、別LinuxホストはNX18へ接続する。

- 通常のローマ字入力、Spaceによる変換・候補選択、確定・取消・英数復帰を目標にする。SKK固有操作を通常利用の要件に戻さない。
- 旧ttyskkのprobe・学習データ・失敗記録は保持する。その成功を案A/Bの受入れに流用しない。
- 本書のtask ID、command/event名、file分割案は設計案であり、実装済みAPIや利用可能な検査コマンドではない。
- 本書の設計案と実装済みの範囲は、実装記録と照合する。計画の記載だけを実装・受入れの証拠にしない。

## 2. 確認した現状と未確定事項

| 対象 | 調査基準で確認したこと | 本計画で必要なこと |
| --- | --- | --- |
| foot | [導入記録](termux-wayland-tools.md)では1.28.0、`+ime`、日本語fallback font検出。固定sourceの`ime.c`は周辺テキスト送信・削除に非対応 | 周辺テキストなしで実IMEから確定文字が届くことを確認。再変換は初期受入れに含めない |
| Rust compositor | [ProtocolState](../native/core/src/compositor/protocol.rs)にIME用globalは未接続 | `text-input-v3`、focusと状態反映の実装 |
| Smithay | [coreの依存](../native/core/Cargo.toml)は0.7.0固定。text-input managerはinterface version 1。標準input-method dispatchは文字イベントをserial検査前に送る | 接続方法とtransaction全体の事前検査をIME-C1で確定する。不一致`done`を文字の取消に使わない |
| Waypipe | [依存lock](../native/deps.lock.json)は0.11.3固定 | 実際の両端buildで必要protocolとイベント転送を検査 |
| 物理キー入口 | [PhysicalKeyboard](../android/app/src/main/java/io/github/tinmachine/eidolon/PhysicalKeyboard.kt)が物理deviceを検査しnativeへ配送 | IME所有のキーとraw配送の排他制御 |
| Androidの窓 | [MainActivity](../android/app/src/main/java/io/github/tinmachine/eidolon/MainActivity.kt)がActivityに加えPresentationのWindowへhook | IMEのeditor View・Window・display・focusの実測 |
| 入力解除 | [NativeSessionHost](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt)と[共通契約](design/contracts.md)にepoch、focus token、urgent resetがある | compositionと遅延callbackにも同じ失効条件を適用 |
| 案Aの成立性 | 日本語IMEの選択可否、物理キー変換、MR内の候補UIは未検証 | IME-A1/A2で判定 |
| 案Bの成立性 | [既存調査](japanese-input-plan-20261107.md#更新-termuxのfcitx5を使う2026-11-07)にpackage不足等の根拠がある | 実機apt、Wayland有効化、エンジンのBionic対応を検査 |

Meta公式の[Spatial SDK Input](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-ui-input/)は、
`SpatialTextField`等とシステムキーボードの連携を説明している。日本語・外部キーボード・この独自compositorの成功証拠ではない。
Androidの[View.onCreateInputConnection](https://developer.android.com/reference/android/view/View#onCreateInputConnection(android.view.inputmethod.EditorInfo))と
[InputConnection](https://developer.android.com/reference/android/view/inputmethod/InputConnection)を利用する側を実装する。
既存IMEを利用する案Aでは、かな漢字変換エンジンや`InputMethodService`の自作を前提にしない。

## 3. 実施順と判断

| ID | 作業 | 開始条件 | 次へ進む条件 |
| --- | --- | --- | --- |
| IME-00 | 現行版・実機・入力経路の棚卸し、固定テキストの検証準備 | NX09の実装再開時 | 記録と再現入口がそろう |
| IME-A1 | 小さなSpatialパネルの標準入力欄で日本語を試す | IME-00 | 日本語変換・実キーボード・候補視認の必須条件がPASS |
| IME-A2 | 独自editor host / InputConnectionとキー経路のprobe | IME-00後、A1と同じdebug APKへ準備可 | A1と比較し、端末相当の編集契約とMRのfocus条件でPASS |
| IME-C1 | 共通text-input-v3とWaypipeのwire fixture | IME-00。Aの検証待ちに独立して進行可 | 両端でprotocol・状態・文字列・回収を確認 |
| IME-A3 | Android adapterから一枚の実footへ接続 | IME-A1/A2の採用ゲート、IME-C1 | 未確定表示・確定一回・取消・英数復帰がPASS |
| IME-B1 | TermuxのFcitx5＋日本語エンジンの成立性probe | 案A固有のFAILを記録した場合 | 専用環境でWayland frontendとかな漢字変換が成立 |
| IME-B2 | input-method/virtual-keyboard/popupを接続 | IME-B1、IME-C1 | 実footで案A3相当の入力条件がPASS |
| IME-C2-T | 選んだ一方式の二窓・foot/tmux/nvim・障害復帰 | IME-A3またはIME-B2 | 第8節の端末受入れを完了。NX09の初期範囲 |
| IME-C2-G | GTK/Qt等の対応GUI一例で編集能力を検証 | IME-C2-T、NX15の描画・通常入力 | 第8節のGUI固有受入れ。端末版NX09の完了待ちにしない |

ユーザーの「手動確認はできるだけ最後」という希望を維持し、自動で準備・検査できる部分を先に完成させる。
**IME-A1/A2の実USキーボード・候補視認は、案Aの採用を確定するためのゲート**であり、NX10等の独立実装を止めるゲートにしない。
装着確認をまだ行えない場合はBLOCKEDとしてIME-C1や限定したadapter fixtureを進める。
未確認のまま案Aの本格統合・採用完了へ進めたり、案Bの条件を満たしたと扱ったりしない。
「試作を準備できる」と「方式を採用できる」を分ける。A1の標準欄とA2の局所composition hostを
同じdebug APKの比較caseとして先に用意し、自動検査後に一度の装着機会で順に比較できるようにする。
ここでA1不合格ならA2の結果を案A全体の成功に使わない。GUIへの能力拡張はC2-Gへ分離する。

### 判定を分ける

| 結果 | 意味 | 次の行動 |
| --- | --- | --- |
| PASS | 指定した環境・範囲で必須条件を満たした | 次の段階へ進む。範囲を拡大解釈しない |
| FAIL-A | 日本語IMEの利用不可、物理変換・候補表示・terminal相当編集の不成立等、案A固有の制約を再現できた | 根拠と試した限定的な修正を記録し、案Bへ進む |
| FAIL-COMMON | text-input状態機械、Waypipe、UTF変換、共通focus等の不具合 | 共通部分を直す。これだけを根拠に案Bへ切り替えない |
| BLOCKED | ADB/SSH offline、Guardian、装着確認待ち、必要な環境情報不足 | 再開条件を記録。案Aの失敗に数えない |

既定IMEが日本語に対応しないだけでは、全Android IMEの不成立と断定しない。
端末で利用できる対応IME・subtypeと選択方法を調べる。Gboard等が導入・選択可能とは事前に仮定しない。
限定probeで端末制約を解消できず、OSの保護や入力focus契約を破る回避策が必要になる場合はFAIL-Aの理由にする。
一般的な実装バグは修正して再試験し、原因・変更・結果のない反復を続けない。
標準欄だけ成功し独自hostだけ失敗した場合は、まずcallback・EditorInfo・局所編集能力の差を調べる。
adapterの実装不備を案A固有の制約に数えず、対応不能な編集能力などを切り分けてから判定する。

## 4. 案Aの先行probe

### IME-00: 条件を固定する

- source SHA、APK versionCode/hash、Quest OS build/API、Spatial SDK版、IME package/version/subtype、端末localeを記録する。
- 使用する実USキーボードの接続方式・配列・認識結果を記録する。JIS専用キーやFキーを必須にしない。
- 現在のIME選択・設定、Eidolonの英数入力が動く条件を記録し、probe後に元へ戻せるようにする。
- 初回はdebug専用の小さなパネルと固定テキストを使う。通常のシェルへ実験用の入力を送り込まない。
- 再現条件と操作一覧を先に用意し、自動入力・実キーボード・HMD視認の結果を別欄に記録する。

### IME-A1: 標準入力欄での確認

同じSpatial SDK/通常XR session内に小さなパネルを一つ置き、`SpatialTextField`または対応する標準Android入力欄を載せる。
`SpatialTextField`はMeta Horizon OS UI Setのcomponentであり、現行のSpatial SDK 0.15.0依存だけで利用可能とは仮定しない。
初回は既存依存で動くComposeの標準入力欄またはAndroid `EditText`でもよい。UI Setを選ぶ場合は互換版を固定し、SDK全体の更新をprobeの前提にしない。
ここではfoot/Waypipe/Rustへの文字送信をまだ接続しない。既存rawキー転送がprobeのキーを先取りしない入力経路を用意する。
2D Activityだけでの成功は参考結果とし、Eidolonと同じPresentation/display条件で実施する。
Androidの[複数displayでのIME仕様](https://source.android.com/docs/core/display/multi_display/ime-support)には、
入力先display・既定display・非表示という配置の違いがある。Horizon側の統合を実測し、一般Androidの制約だけで可否を決めない。
同仕様にはsystem所有でないvirtual displayへのIME表示制約もある。対象Presentationのdisplay ID、取得可能な所有者・flags・IME policy、
editorのattach/Window token、View focus、Window focus、実際のIME target/displayを記録する。Spatialパネルの所有者を推測で決めない。
[InputMethodManager](https://developer.android.com/reference/android/view/inputmethod/InputMethodManager)はViewとWindow双方のfocusを要求する。
`showSoftInput`の成功返却は表示完了ではないため、取得できるWindowInsets/IME状態とHMDでの候補視認を分けて記録する。

| 検査 | 必須条件 |
| --- | --- |
| 日本語 | 実キーボードで`nihongo`等を入力し、Spaceで候補を選び「日本語」として確定できる |
| 候補 | HMD内で未確定文字と候補を読め、次/前候補・選択・取消が操作できる |
| 確定 | Enterによる確定と、確定後の改行を区別できる。二重入力・意図しないsubmitがない |
| 英数復帰 | US配列で再現可能な切替があり、英数字・記号・Ctrl/Alt/Esc・矢印へ戻れる |
| 物理キーボード | 通常の実外部キーボードで上記が成立する。ソフト入力のみでは合格にしない |
| focus | 入力欄・操作ボタン・別パネルへ移動しても、文字を誤配送しない |
| パネル操作の復帰 | IME接続中・確定後・ソフト鍵盤非表示後にcontroller/handで別欄・ボタン・別パネルを操作し、英数入力と日本語入力へ戻れる |
| XR状態 | キーボード/候補表示前後のActivity、Window、XR session状態を観測でき、既存の入力停止条件と両立する |

画面キーボードによる操作は補助検査。物理キーボードを使うと候補まで消える、候補が別displayに出る、
キーボード表示でXR focusが失われ入力不能になる、といった条件も記録する。
ソフトウェアキーボード本体を畳んでも候補が使えるか確認し、不可の場合は必須条件の成否と操作上の制約を分ける。
候補の厳密なカーソル追従は後段で評価できるが、候補が読めない・選べない状態をPASSにしない。

#### 固定Spatial SDKのポインター配送を独立に確認する

SDK 0.15.0の元AARを`javap`で確認すると、`PanelDisplayBase.dispatchEvent(MotionEvent, boolean)`は
保持する`InputMethodManager.isAcceptingText()`がtrueの場合、後続のMotionEvent配送前にreturnする。
[isAcceptingTextの契約](https://developer.android.com/reference/android/view/inputmethod/InputMethodManager#isAcceptingText())は
文字編集を受け付ける状態の判定であり、ソフト鍵盤の可視性判定ではない。
この内部処理の根拠は公開API説明ではなく固定AAR（SHA256
`f409e9b9dfda689df2fa6a574e6857b41e2f1aff7c1a56c7ba80c6d60370a873`）のbytecodeである。
Viewパネルconstructorのcontextから取得したIMMとeditor/Presentation側IMMの関係は実測待ちで、
すべてのパネルや物理キーが停止すると断定しない。この静的所見だけではFAIL-Aにしない。

- 未接続・composition中・確定後・鍵盤だけ非表示を分け、controller/handのclick/hover/scroll件数を確認する。
  constructor相当とeditor側のdisplay、IMM状態、View/Window focusを入力本文なしで記録する。
- 鍵盤closeのみ、`clearFocus`と非editorへのfocus移動、host接続失効・局所取消・再同期を別々に試す。
  native resetや`hideSoftInput`だけでAndroid入力接続が終了したとは扱わない。
- クリックが止まってもprobeを終了できる独立したdebug制御を用意する。
  新しい復帰ボタンも、この配送経路で押せると確認できるまでは脱出手段に数えない。
- 同じ操作をA2でも確認し、接続を再作成した後に日本語入力を再開できることまで一巡とする。

### IME-A2: 独自入力Viewと端末に近い契約

標準入力欄と比較するため、attachされたViewと正しいWindow tokenを持つeditor hostを用意し、
`onCreateInputConnection`、`onCheckIsTextEditor`、`EditorInfo`を設定する。
試作準備はA1と並行してよいが、採用判定はA1→A2の順を守る。
ゼロ寸法・未attach・任意の不可視ViewでもIMEが利用できるとは仮定しない。
初期probeは見える検査用Viewで行い、製品で必要なhostの配置に変えて同じ試験を通す。

Android公式の[InputConnection契約](https://developer.android.com/reference/android/view/inputmethod/InputConnection)に従い、
未確定範囲、選択、batch edit、`setComposingText`、`commitText`、`finishComposingText`、
`setComposingRegion`、削除、取得、`sendKeyEvent`、editor actionを実際のIMEがどう使うか調べる。
`SpatialTextField`のString差分をそのまま確定文字として送る実装にはしない。

- stock editorと独自editorの差、callbackの順序・種類・長さ・世代を固定fixtureで比較する。
- 周辺テキストを提供できない端末相当の条件でも変換できるか確認する。通常のEditTextの完全な文書bufferを前提にしない。
- 変換中のSpace/Enter/Esc/Backspaceがrawキー経路へ重複して流れず、変換対象外の操作は一度だけ届くことを確認する。
- 実際のIMEが`commitText`だけを使う場合も許容し、preedit表示・候補選択等の製品要件を満たすかで判定する。
- Window、入力欄、選択したWayland窓、XRの各focusを識別し、同じbooleanへまとめない。
- 「IME表示でwindow focusが失われる」現象は専用probeで原因を確認する。`VISIBLE`中の無条件キー送信などでgateを外さない。
- 完全な周辺編集を必須とするIMEや、このhost条件では候補を表示できないIMEは案Aの制約として記録する。

hostは`EditorInfo.initialSelStart/End`と`updateSelection`を整合させ、batch完了時に選択・未確定範囲を通知する。
`requestCursorUpdates`は第5.5節で実際に通知できる能力だけを返す。
`closeConnection`時は局所buffer・batch・通知予約を解除するが、このcallbackが来ない失効でも同じ解除を行う。
Android APIの戻り値は方法ごとの契約に従い、提供不能な周辺テキストを「文書全体が空」として返さない。

## 5. 共通のWayland入力契約

### 5.1 所有者と境界

案Aの経路は、Android IME → Kotlin editor host → JNI → Rust protocol owner → Waypipe/SSH → foot/対応GUIアプリ。
案BではTermuxのFcitx5からRust protocol ownerへ入力し、クライアント向けのtext-inputとfocus契約を共用する。
既存の[四つの実行領域](design/contracts.md)を維持する。

| 所有者 | 保持するもの |
| --- | --- |
| Kotlin | Android editorの局所的なEditable/composition/selection、InputConnection、Window/displayとの対応 |
| Rust protocol/model owner | 有効なsession/window/surface、input epoch/focus token、text-input状態とserial、採用backend、配送順序 |
| GL owner | 所有済み描画snapshot。IME protocol objectやAndroid入力状態を持たない |
| transport | 既存Waypipe/SSH接続と子processの寿命。確定文字を独自SSH stdinへ別送しない |

KotlinのEditableはAndroidとの契約に必要な局所状態であり、Linuxアプリの全文書の正本にしない。
Rustのtarget/世代情報を各callbackに束縛し、JNI越しには所有済み文字列と検査済み範囲だけを渡す。
同期のInputConnection読取りのためにUI threadからWaypipe/SSHの応答待ちをしない。利用可能なsnapshotと能力を返す。

### 5.2 text-input-v3とSmithay

[Waylandの仕様原文](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/unstable/text-input/text-input-unstable-v3.xml)
（[原文の生成表示](https://wayland.app/protocols/text-input-unstable-v3)）に沿って、
managerの公開、seatとの対応、enter/leave、enable/disable、content type、surrounding text、cursor rectangle、commitを扱う。
採用するinterface versionは固定Smithay・foot・Waypipeで実際に共通対応する版にする。
protocol名の`v3`とinterface versionの数値を混同しない。
[Cargo.lock](../Cargo.lock)の`wayland-protocols 0.32.13`同梱XMLはversion 2だが、
[Smithay 0.7.0のmanager](https://github.com/Smithay/smithay/blob/v0.7.0/src/wayland/text_input/mod.rs)はversion 1を公開する。
初期fixtureは実際にbindするversion 1の契約で固定し、XMLの版/hash・広告版・bind版を記録する。
最新XMLにあるversion 2専用eventやcursor rectangleの適用タイミングを、version 1の実装へ混ぜない。

- クライアントからのrequestはpending/currentを分け、`commit`で反映する。enabledかつ現在focusを持つ対象だけへ送る。
- `preedit_string`/`commit_string`等を一つの編集transactionとして扱い、対応する`done(serial)`で反映する。
  serialは当該text-input objectのcommit回数に対応させ、seatのkeyboard serialや独自request IDを代用しない。
- 同じsurface内の編集欄変更、連続するenable/disable、object破棄、client切断にも対応する。
- [Smithay 0.7.0のtext_input](https://docs.rs/smithay/0.7.0/smithay/wayland/text_input/index.html)を使う前に、
  input_methodとの結合、直接イベントを送る公開API、focus hookの挙動を固定sourceで確認する。
  型の存在だけでAndroid backendを接続できるとは判断しない。
- 案Aの論理構成だけでは外部公開input-method-v2は必須でない。ライブラリ接続上必要になる範囲はIME-C1で確定する。

固定0.7.0の[text_input_handle.rs](https://github.com/Smithay/smithay/blob/v0.7.0/src/wayland/text_input/text_input_handle.rs)では、
input-method instanceがないとtext-input requestを破棄する。
[seat/keyboard.rs](https://github.com/Smithay/smithay/blob/v0.7.0/src/wayland/seat/keyboard.rs)のtext-input enter/leaveもinstanceの有無に依存する。
従って、標準managerへAndroidの確定文字送信だけを足す設計では足りない。IME-C1で次の二案を比較して決定記録を残す。

| Smithayとの接続案 | 検証する内容 |
| --- | --- |
| APK内の信頼されたinput-method-v2 bridge client | 標準の状態機械を再利用し、Androidとの間でactivate/deactivate・編集transactionを変換する。専用event loop・serial・停止処理を含めてprobeする |
| 明示したbridge APIのpatch、または自前text-input dispatch | Android backendを直接接続する。enable/disable、pending/current、serial、focusの所有範囲を定義し、標準delegateを無修正で使えるとは見積もらない |

前者を小さな比較用spikeとして試してよいが、標準状態機械を無修正で再利用できる優位は未確定である。
下記fixtureを共通基準とし、必要patchの範囲・停止契約・公開APIを後者と同等に比較してから一方式を選ぶ。
両方式を製品レベルまで作ったり、案Aのために案Bのpopup/keyboard grab全体を先に作ったりしない。内部bridgeだけへglobalを見せる
[Smithayのclient filter](https://github.com/Smithay/smithay/blob/v0.7.0/src/wayland/input_method/mod.rs)を利用し、
同じseatで案Aのbridgeと案BのFcitxを同時に有効にしない。
後者で`with_active_text_input`を使う場合は、同じhandleを再lockする`done`をclosure内から呼ばない。
固定依存への変更は[native/patches](../native/patches/smithay/README.md)とlockへ記録し、展開済みsourceを直接編集しない。

**文字イベント送信前の検査を、内部bridge採用の条件に追加する。**
固定版の[input_method_handle.rs](https://github.com/Smithay/smithay/blob/v0.7.0/src/wayland/input_method/input_method_handle.rs)は、
`commit_string`/preedit/削除をclientへ送った後、input-methodの`commit(serial)`で一致を検査し、不一致ならtext-inputへ`done(0)`を送る。
しかしtext-input仕様の不一致serialは編集文字を破棄する命令ではない。
また[foot 1.28.0のime.c](https://codeberg.org/dnkl/foot/src/tag/1.28.0/ime.c)は不一致`done`でpending確定文字を残してreturnするため、
後続の正常`done`へ持ち越す経路がsource上にある。これは静的読解による懸念であり、現行Eidolonで再現済みの不具合とは扱わない。

- IME-C1は「古い確定文字 → 不一致serial → 正常なpreedit-onlyまたは空のdone」を同じ対象で検査する。
  次の`commit_string`はfootの古いpendingを上書きするため、それだけでは残留を検出できない。
  footは通常のleave/enterでpendingを消す。窓切替は、古いbackend要求が切替後に送出される別の競合として検査する。
- backendの編集要求を所有済みpending transactionに蓄積し、commit時に対象・activation世代・serial・範囲を全体検査してからclientへ送る。
  既に送った文字を不一致`done`で取り消す設計は禁止する。案Aの信頼されたclientにも遅延・競合はあるため例外にしない。
- この条件を満たせない標準delegateは無修正で採用しない。bridge API/dispatchのpatchを先に設計し、案Bでも同じ検査を通す。
- 不正enum、UTF-8境界外のoffset、負の寸法、過大surrounding、同一clientの複数text-input objectをfixtureへ含める。
  固定版のrequest処理にはenum変換の`unwrap`等があるため、delegateの存在を入力検証済みの根拠にせず、session全体のpanicを防ぐ。

固定版の[text_input_handle.rs](https://github.com/Smithay/smithay/blob/v0.7.0/src/wayland/text_input/text_input_handle.rs)にも
静的監査による懸念がある。通知loopが最初の同一client objectで打ち切られ、destroy処理はactive IDを消さず、
enable処理は以前のpending属性を初期化しない。一般的な「destroy/複数object」の項目だけで済ませず、C1冒頭で次を試す。

- 同一clientで二つ作成→両方enter→Aをenable→Aをdestroy→Bをenable/commit→Bへ正常入力。
  単一objectの破棄・再作成も確認する。
- 以前の編集欄のsurrounding/content typeを未commitのpendingへ設定→新しいenable→commit。
  新しい欄がsurroundingを提供しなくても、以前の文字・属性が新activationへ渡らないことを確認する。

これは現行Eidolonでは未接続のIME処理に対するsource上の指摘であり、実機で再現済みとは扱わない。
必要修正量を上の接続案比較へ戻し、active ID・pending/current・focusの所有者を一つに保つ。

IME-C1は独立text-input clientと入力backend fixtureで、まず同一Display、次に実Waypipe両端を通して検査する。
registryのglobal/version、focus、preedit/commit/done、順序、切断・再接続とFD/queue回収を比較する。
[Waypipe 0.11.3のREADME](https://gitlab.freedesktop.org/mstoeckl/waypipe/-/blob/v0.11.3/README.md)は、
FDを含まない未認識拡張の転送を説明している。専用text-input XMLが見つからないだけで非対応とは判定しない。
source上の転送機構と、このAPK helper/Termux packageでの成立を分け、実際の両端binaryで検査する。
再接続はEidolonが所有する接続の再生成を含めて検査し、上流の別binary/実装の透過再接続機能を前提にしない。

### 5.3 Android編集操作とUTF変換

| Android側の操作 | bridgeの契約 |
| --- | --- |
| `setComposingText` | 現在の未確定範囲を置換し、pending preeditへ反映する |
| `commitText` | 局所状態の未確定範囲/選択を更新し、確定文字を一度だけ配送する |
| `finishComposingText` | 文字を残して未確定状態を終了する。既送信の確定文字を重複送信せず、未送信のpreeditを失わない |
| `beginBatchEdit` / `endBatchEdit` | ネストを管理し、途中状態の通知と最終状態を区別する |
| 選択・周辺文字・削除 | クライアントが実際に提供する範囲と能力だけを使う |
| `sendKeyEvent` / editor action | raw経路との重複を除き、Enter確定と改行/実行の意味を分離する |

`finishComposingText`を取消へ対応付けないことは[Android公式仕様](https://developer.android.com/reference/android/view/inputmethod/InputConnection#finishComposingText())に基づく。
一対一のcallback置換では不十分なため、Android側の編集結果とWayland側の既送信状態からtransactionを作る。
確定直後に`finishComposingText`が続く場合や、同じbatchで確定と次のpreeditが発生する場合も検査する。
同じbatch内の複数`commitText`も検査する。複数の`commit_string`を一つの`done`までに送るだけでは
footのpendingが上書きされるため、前半の確定文字を失わない編集結果を生成する。

周辺テキストの読取り能力と、確定済み範囲の選択・カーソル移動能力を分ける。
text-input-v3には任意の確定済み位置へカーソルを移動するeventがないため、GUIがsurroundingを提供しても
Androidの任意`setSelection`や再変換まで対応できるとは扱わない。
A2で`commitText(..., 0)`、composition外の`setSelection`、確定済み範囲への`setComposingRegion`を試し、
表現不能な要求への返却・局所状態の再同期を固定する。初期footの編集能力は局所composition内に限定する。

AndroidのJava char位置はUTF-16 code unit、WaylandはUTF-8 byte offsetである。
`deleteSurroundingTextInCodePoints`は別の単位として扱い、補助漢字・絵文字・結合文字を含む範囲変換を試す。
文字列とoffsetを一緒に変換し、surrogate/UTF-8 code pointの途中を境界にしない。
Androidに孤立surrogateが来た場合、NUL、過大payloadの扱いも契約化し、黙って切り詰めない。
surrounding textの仕様上限4000 UTF-8 bytesと、JNI command/queueの上限は別に検査する。
preedit・commit・局所Editableにも上限を定め、transactionの途中だけを配送して残りを捨てない。

固定foot 1.28.0の`ime.c`はsurrounding textを送らず、その削除にも対応しない。確定文字は通常入力時にfoot自身がPTYへ渡す。
したがって初期hostは「現在の局所compositionを編集し、確定時に一度渡す」という能力で試し、確定済み端末文字の再変換・周辺削除を約束しない。
footを更新した場合はこの能力を再確認する。GTK/Qt入力欄のsurrounding textは別の能力profileとして検査する。
text-input仕様は周辺テキスト未提供と空文字列を区別する。表示済み画面を編集可能な全文書とみなさず、
未提供の周辺テキストを捏造したり、削除byte数を同数のBackspaceへ変換したりしない。
通常の物理BackspaceとIMEの周辺編集要求を区別する。この条件でのIME互換性はA2/A3の受入れ対象にする。

### 5.4 キーの所有者と解除

[PanelKeyboardBridge](../android/app/src/main/java/io/github/tinmachine/eidolon/PanelKeyboardBridge.kt)は現在、
nativeへのforwardを通常のWindow配送より先に試す。IME接続時は「IMEに渡す」「既存raw配送」「操作UI」の所有を決める。
消費済みキーのdown/up、repeat、IMEから返る未処理キーを追跡し、循環・二重配送・押しっぱなしを防ぐ。
Ctrl/Alt/Escを一律に横取りせず、変換中の取消とtmux/nvimの操作を両方検証する。
Androidにはpre-IME/IME/post-IMEの段階があるため、`dispatchKeyEvent`からfalseを返すだけでIMEへ渡るとは設計しない。
[AOSPの入力段階](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android14-dev/core/java/android/view/ViewRootImpl.java)を参照し、
Horizon/Spatialの実経路でcallbackが来る段階と未処理キーの取得方法を確認する。

2026-11-07のユーザー指定で、切替ショートカットは**Ctrl+Space**とする。
Eidolon内の「IMEへ入力するモード」と「Linuxへ直接キーを送るモード」を切り替える。
利用するIMEと日本語の選択はQuest側の設定で行う。アプリがIME内部の言語・変換モードを自由に強制できるとは扱わない。

- 左右のCtrlに対応し、1回のSpace押下で1回だけ切り替える。repeat、遅いkeyup、Ctrlを先に離す操作を検査する。
- Shift/Alt/Metaを併用する別ショートカットは対象外。切替を受理したCtrl/SpaceはLinuxとAndroidへ重複配送しない。
- 切替前にLinuxへ届いたCtrl downをurgent resetで解除する。Android接続待ちでも解除を遅らせない。
- IME側に先に消費される可能性を考慮し、IME中の終了操作はpre-IME入口でも検証する。
- 接続準備中・IME入力中・直接入力・解除失敗を画面に区別して示す。切替要求を出しただけで「日本語入力可能」と表示しない。
- 終了時は未確定を取り消し、native reset/disableの完了とAndroid入力接続の解除を確認してから直接入力へ戻す。

[Android InputMethodManager](https://developer.android.com/reference/android/view/inputmethod/InputMethodManager)のAPI権限と、
[AOSP 14の未処理キー処理](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android14-release/services/core/java/com/android/server/policy/PhoneWindowManager.java)を参照する。
AOSPでは未処理のCtrl+Spaceが物理キーボードの配列切替へ進む場合がある。Horizon OSでの実際の処理と競合は実機確認し、
Eidolonが切替を受理しない状況でOSまで一律に遮断する実装にはしない。

各IME transactionには、既存target tuple、`input_epoch`、`focus_token`に加え、入力接続世代と順序番号を束縛する案を使う。
同じ窓へ戻った場合も古いInputConnectionを再利用しない。Kotlin側の早期検査に加えてRust ownerで再検査する。
`ACCEPTED`はenqueue成功であり、確定文字の適用成功とは区別する。

focus変更・surface破棄・scene再生成・配置編集・layout変更・device変更・sleep・XR/Activity喪失・切断・Stopでは、
旧入力接続を失効させ、未確定を取り消し、必要なkey releaseとbackendの解除を順序付きで処理する。
新しい窓への自動確定はしない。通常queueが満杯でも、既存urgent reset経路で失効を伝える。
Waylandの`disable`はclientからのrequestである。compositor/backendの失効、clientのenabled状態、
keyboard focusのenter/leaveを区別し、同じ窓への復帰でもactivateとeditor状態を再同期する。
同一世代内の未確定表示更新は置換可能でも、確定transactionを間引かない。BUSY/失敗時に送信済みか不明なcommitを自動再送しない。
二重確定防止と入力不能の案内を優先し、復帰後は新しい入力接続から再開する。

### 5.5 候補UIと座標

案Aの候補はAndroid IME/OSが描く。Eidolonが候補一覧を取得して自由に描き直せるとは仮定しない。
Waylandのsurface-local cursor rectangleから、本文viewport・scale・letterbox・Android editor Viewへの対応を作る。
[CursorAnchorInfo](https://developer.android.com/reference/android/view/inputmethod/CursorAnchorInfo)等の通知は対応能力を確認して使い、
Android display上の座標とXRのworld座標を同一視しない。
[Builderのlocal-to-screen変換](https://developer.android.com/reference/android/view/inputmethod/CursorAnchorInfo.Builder)に合わせ、
clientが提供していない文字ごとのboundsを推測で作らない。
入力hostとfootパネルのdisplay/Windowが異なる場合に、候補をどこへ出せるかは実機で判定する。

最初は読みやすい一定の候補位置でも成立性を確認し、その後に窓移動・resize・二窓切替への追従を評価する。
候補が画面外へ出る、別窓に残る、毎回別入力欄へ打って貼り付ける必要がある状態は完成扱いにしない。
案Bのinput popupは通常のアプリtoplevelと区別し、親入力対象に追従させる。
将来の九枚のアプリ枠とは別に、popupのSHM/描画/layer資源を上限付きで計測・回収する。

## 6. 案Bへ移る場合

案AのFAIL-A判定には環境、再現手順、失敗箇所、試した限定修正、未解決理由を付ける。
案Bに移ってもIME-C1のclient側protocol、focus世代、UTF変換、検査fixtureを再利用する。
案A/Bを同時に入力可能にせず、切替時は入力解除・未確定取消・世代更新後に選んだbackendだけを有効にする。
実行中の案A障害に対し、変換途中で案Bを自動起動する動作は初期実装へ入れない。

### IME-B1: packageとエンジン

従来調査の[Termux fcitx5 recipe](https://github.com/termux/termux-packages/blob/5c3ea65e18dc46c0fd97cd0d6084708bac762cd9/x11-packages/fcitx5/build.sh)は
`ENABLE_WAYLAND=OFF`であり、[Mozc追加要望](https://github.com/termux/termux-packages/issues/29614)も参照されている。
これは固定snapshotの根拠であり、実装時のapt候補・導入済み版・上流の変更を再照合する。

- 第一候補はFcitx5＋Mozc。既存package、Wayland有効化したTermux向けbuild、MozcのARM64/Bionic対応と辞書を順に調べる。
- 初回は専用prefix/config/data/runtimeと専用D-Bus sessionでprobeし、他用途のFcitxやsession busを置換しない。
- 足りない依存・build工程・license・辞書容量・起動/RSSを記録する。APKへの変換エンジン同梱へ直行しない。
- 変換エンジン単体の成功と、Fcitx frontend・候補UI・実footの成功を分ける。
- Mozcが成立せず別エンジンを検討する場合は、通常のかな漢字変換という要件と追加費用を示す。SKKへの復帰を既定にしない。

### IME-B2: compositorへの接続

[FcitxのWayland説明](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland)と
[waylandimserverv2.cpp](https://github.com/fcitx/fcitx5/blob/master/src/frontend/waylandim/waylandimserverv2.cpp)を固定版と照合し、
`input-method-v2`に加えて`virtual-keyboard-v1`の必要性を検査する。
既存調査では両globalが揃ってから初期化する実装が確認されているため、text-input-v3だけの公開で完了にしない。

keyboard grab、未処理キーの返送、keymap/modifier、activate/deactivate、input popup surface、辞書/学習の保存、
IME終了と再起動を所有する。仮想キーボードから返したキーを再度同じIMEへ流すloopを防ぐ。
一般アプリclientへIME/仮想キー注入の権限を無条件で開放しない。
APK側で見えるWaypipe proxyのUID/PIDだけではTermux内の個々のclientを識別できると仮定せず、
専用接続/起動で識別できる境界とglobal filterの効き方を小さく検証する。

案BでもWaypipeを通ったcandidate popup、keyboard grab/返送、複数clientの分離を実測する。
Fcitxの起動成功やGTK/Qt用の環境変数だけを、native Wayland footでの成功へ広げない。

## 7. 変更箇所と実装の分け方

| 区切り | 主な変更候補 | 完了時に残すもの |
| --- | --- | --- |
| Android probe | `android/app/src/debug/`の専用case/panel、必要最小限の入力経路選択 | A1/A2結果、実機条件、失敗分類 |
| 共通モデル | `native/model/src/`のIME状態・target/transaction型、`commands.rs` | 世代/順序/UTF範囲の検査 |
| Protocol | `native/core/src/compositor/`にtext-input adapter、既存input/focusとの接続 | 独立clientとbackend fixture、Waypipe wire結果 |
| Android bridge | `InputAdapter`/`NativeSessionHost`/`NativeBridge`、`native/android/src/bridge.rs`、必要なら新規ImeInputConnection | 局所編集状態とRust所有状態の境界、定数/返却値の一致 |
| 寿命・診断 | `native/core/src/runtime.rs`/`session.rs`/`shutdown.rs`、Androidのscene/Window解除 | 切断・満杯・Stopでの回収、本文なし診断 |
| 案B追加分 | 外部Fcitx用input-method接続、`virtual_keyboard`/popup、Termux setup/helperと依存lock | 固定build/辞書/license、専用process/bus回収。案A内部bridgeが使うinput-method基盤は共用可能 |
| 受入れ | 既存`check-host`/`quest-check`へ専用caseを追加、NX09/15/18記録 | 自動結果とHMD/物理結果の分離 |

ファイル名やcase名は実装前に現行構造へ合わせる。上記の未実装caseを既存コマンドとして案内しない。
debugパネルは既存の[BuildHooks](../android/app/src/debug/java/io/github/tinmachine/eidolon/BuildHooks.kt)の入口を優先して再利用する。
[NativeCheckInstrumentation](../android/app/src/debug/java/io/github/tinmachine/eidolon/NativeCheckInstrumentation.kt)は
sceneを起動しないため、共通protocol/寿命の検査とSpatial IME表示の検査を分ける。前者の成功を後者へ流用しない。
先行probe、共通protocol、案A接続、選択後の統合受入れを別commit/PRに分け、失敗段階を局所化する。
案Bのpackage移植を案Aの成功待ちなしに大規模着手しない。既存調査・少量のread-only確認は再利用できる。

## 8. 受入れと証拠

NX09の初期完了はC2-Tの端末受入れまでとする。以下のGTK/Qt固有の編集能力と候補位置、
GUI向けsurrounding対応はC2-G/NX15、remote Linuxの受入れはNX18で追加する。
初期端末版でも誤配送防止・通常英数への復帰・操作不能からの復帰は省略しない。

| 範囲 | 必須の確認 | 合格に置き換えないもの |
| --- | --- | --- |
| Protocol | focus/enable/disable/commit、preedit/commit/done、serial、destroy、再接続。不一致serialの後の正常transaction、複数object、不正入力の隔離 | global名が見えるだけ。不一致`done`を送っただけで破棄成功とすること |
| 文字列 | 日本語・ASCII混在、補助漢字、絵文字、結合文字、選択と境界、過大入力 | ASCIIのみのfixture |
| 実foot | surrounding textなしで未確定描画、候補選択、確定一回、取消、英数復帰、Enter確定と実行の分離 | clipboard貼付け、既存日本語の表示。EditTextの全文書bufferだけでの成功 |
| キー | 変換中/外のSpace/Enter/Esc/Backspace、Ctrl/Alt、矢印、repeat、二device | IME確定とrawキーの両方を配送する動作 |
| パネル操作 | IME接続中/確定後/鍵盤非表示後のcontroller・hand操作、接続失効と再開 | SDKの鍵盤表示ログだけ。操作できない復帰ボタンを置いただけ |
| 二窓 | Aで未確定中にBへ移動、Aへ再入、旧callback、窓close、配置編集、resize | 同一入力欄内だけの試験 |
| tmux/nvim | pane移動、prefix、insert/normal、取消/確定、外側IMEの状態とアプリ状態の関係 | paneやnvim modeを自動認識できるという推測 |
| 一般GUI | text-input-v3対応の小さなGTK/Qt入力欄一つ。readonly/編集欄切替、surrounding text、dialog | foot成功だけで全GUI対応とすること |
| 障害 | IME停止/再接続、queue満杯、device変更、sleep、XR喪失、Stop、scene再生成 | 通常終了一回だけの試験 |
| 実利用 | 使用IMEと実USキーボードを記録し、HMDで候補・未確定・入力先を確認 | ADB仮想キーやinstrumentationのみの結果 |

アプリ/GTK/Qtの版とtext-input対応を個別に記録する。X11/XWayland、全toolkit、全IME、音声入力を一括して保証しない。
tmux paneやnvim modeはcompositorのsurface focusとは異なるため、自動モード切替は別機能として扱う。
NX18では、入力先のLinuxへAndroid IMEを入れずに日本語が渡ることを、別hostの実clientで確認する。

通常診断はbackend、状態、対象ID/世代、失敗理由、callback/配送/取消件数、所要時間、queue/resourceだけを記録する。
ユーザーの入力本文・未確定文字・候補・学習辞書は保存しない。固定fixtureの期待値照合と私的な実機artifactは通常診断から分ける。
foot 1.28.0はdebug levelでpreedit/commit本文を出すため、通常の実入力でfootのIME debug logや`WAYLAND_DEBUG`を有効にしない。
Androidのeditor dumpやprotocol traceも本文を含み得る。詳細traceは固定fixture専用とし、集約ログから除外する。
GUIがpassword/PIN/sensitiveの用途を提供する場合はAndroidの入力属性・[学習抑制要求](https://developer.android.com/reference/android/view/inputmethod/EditorInfo#IME_FLAG_NO_PERSONALIZED_LEARNING)へ対応付け、前の窓のsurrounding textを渡さない。
その要求をIMEが尊重するかは別に検証する。footはterminal用途しか通知しないため、shell内のパスワード入力を自動判別できるとは扱わない。
HMD画像が必要な場合は室内を含む可能性があるため、既存のprivate artifact方針に従いrepositoryへ追加しない。

## 9. 実装再開時に残す記録

各taskについてsource/APK/OS/IME/foot/Waypipeの版、再現入口、必須条件ごとの結果、失敗分類、試した変更、
回収結果と次の作業を[NX09以降の継続記録](extensions-nx09-nx18-20261107.md)へ追記する。
詳細artifactの保存先は作業開始時の`codex-work-root`設定から取得する。

再開時点ではIME-00/A1/A2/C1を準備中、A3/B1/B2/C2-T/C2-Gは未着手。案Bは条件付きであり、A採用時にBを実装する必要はない。
文書追加、Android標準入力欄の成功、共通wire試験の成功、実foot成功、HMD受入れを別々の到達点として記録する。
再開後に最初に実装するのはIME-00とIME-A1の小さなSpatial入力probeである。
