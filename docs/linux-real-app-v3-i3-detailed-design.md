---
id: linux-desktop-v3-i3
title: Linux Desktop v3 I3 listener origin and window admission detailed design
type: detailed-design
design_status: proposed
feature: linux-desktop-v3
component: window-admission
phase: I3
created: "2026-11-14"
last_reviewed: "2026-11-14"
baseline_commit: 6f8f57a67399abb0ef88f9e3658b1e6fbaff0b55
depends_on: [linux-desktop-v3-b1-presentation]
related_sources:
  - native/core/src/compositor/protocol.rs
  - native/core/src/compositor/snapshot.rs
  - native/core/src/compositor/input.rs
  - native/core/src/compositor/pointer.rs
  - native/core/src/session.rs
  - native/core/src/window_slots.rs
  - native/core/src/runtime.rs
  - native/model/src/linux_desktop_v3/presentation.rs
---

# I3 詳細設計 — verified listener origin / WindowHistory / window admission

**状態:** 実装前の提案設計。I3の製品コード、host acceptance、Quest実測の完了を意味しない。本文の「提案API」は存在を保証しない。既存APIと区別する。  
**目的:** Quest 3のLinux Desktop v3で、認証済みI2 Launchから発生した *正しいWayland client* の *正しいmain/dialog window* のみを製品slot・renderer・入力へ導く。Mousepad 0.6.3 / GTK3を最初の実用アプリとする。  
**変更方針:** B1 golden、v2/Termuxのwire・ownership、C1/C2/E1、既存9窓制限を変更しない。I2とI4の作成途中PRに対する**必要修正も§11に明記**する。

## 0. 調査基準と正本

2026-11-14 JSTにGitHub上で確認した基準。日付・SHAは設計時点の固定値であり、後日の実装時は取り直す。

| 対象 | 検査したhead / 状態 | I3で利用するもの |
| --- | --- | --- |
| main | 6f8f57a67399abb0ef88f9e3658b1e6fbaff0b55 | B1モデル、既存Session / compositor / slot、PR-01共有型 |
| [I2 PR #28](https://github.com/tin-machine/eidolon-wayland/pull/28) | 996dc3764d916cf504033e14b1cc8db49b0274f5 / Draft、未マージ | LaunchAdmission / LaunchPermit、NativeOrigin::capture、sessionのreserve/start/abort、回収handoff |
| [I4 PR #16](https://github.com/tin-machine/eidolon-wayland/pull/16) | e3b9d39b5993d553cba2d5c8e43a4178c3a1a5f7 / Draft、未マージ | NativeObservationBuilder、V3PresentationObserver、V3Report::validate_current、SDK retirement ledger |
| [統合計画](linux-real-app-v3-integration-plan-20261111.md) | mainの§8、§18、V06〜V08 | I3の要件・受入れ条件 |
| [B1 presentation契約](linux-real-app-b1-presentation-20261110.md) | main | NativeOrigin / Parent / Role / Startup / golden |
| [I2詳細設計](linux-real-app-v3-i2-detailed-design.md) | mainの§12 | local準備 / journal回収の引き継ぎ |
| [I4詳細設計](linux-real-app-v3-i4-detailed-design.md) | main | 起動期限 / SDK証拠 / 同tickのreducer |

PR #28は実v3 transportのInspect、catalog、予約、Stop、deferred finalizationを持つが、core側の製品Launch commandは意図的にNotReadyである。PR #16はI4純粋・橋渡し部品が主で、I3のlistener/WindowHistoryは未実装。**いずれもmainへのマージやCI完了を推定しない。**

本書は提案時点ではD5のmanaged manifestにI3を登録しない。I3は現在未登録タスクであり、設計PRのレビュー・マージ後に別途manifestの登録可否を判断する。Front Matterのproposedを「承認済み」と読まない。

## 1. 保護する不変条件

I3-INV-01: originはI2の予約済みLaunchからnativeが生成し、専用Wayland listenerでacceptしたClientDataへ一度だけ固定する。remote JSON、title、app_id、PID、UID、SDK Entityはorigin発行元にならない。  
I3-INV-02: 1つのv3 Sessionは1つのimmutable originと1つの専用listener/display所有域を持つ。v2、Termux、diagnostic clientにはv3 originを付けない。  
I3-INV-03: WindowIdは同Session内で単調増加・再利用しない。各toplevelのClientData originをWindowRecordへコピーし、後からapp_idを変えても同一性は変わらない。  
I3-INV-04: mainは「そのLaunchのStarted」かつ「新規・same-origin・明示parentなし・manifestに合うapp_id・Mapped」を満たしたものを**一度だけ**固定する。Started/Mappedは順不同。  
I3-INV-05: dialogは既採用mainへ到達するsame-originの有効なxdg_toplevel parent chainだけ許す。unmap、parent破棄、roleリセット、remapでdialogをroot/mainに昇格しない。  
I3-INV-06: main破棄後の新rootをmainとして選び直さない。第二root、循環、self-parent、重複WindowId、role変更、foreign originを不正扱いにする。  
I3-INV-07: public panel / window slot / renderer Create / focus / pointer / keyboard / IMEのすべてに**同一のadmission projection**を使う。renderはadmittedでもSDK現target証拠が不十分なら入力を許可しない。  
I3-INV-08: 最大9個のtoplevel / slotを超えて採用しない。popup/subsurfaceは親toplevelの内部であり独立したmain/dialogとして数えない。  
I3-INV-09: Stop開始後はnew client・new role・new panel・inputを閉じるが、既存窓・SDK target・forward・journalの**回収証拠**は失わない。unmapをdestroy、request_stopをreaped、SDK destroy要求を削除完了と呼ばない。  
I3-INV-10: v3失敗をv2へfallbackしない。異常・不確実性はfail closedかcleanup pendingに保持し、Readyやrelaunch_allowedへ昇格しない。

脅威モデル上の限定: local Waypipe listener上のoriginは、nativeの予約・受理経路との**配送相関**を証明するものであり、remote hostの改竄耐性や同UID内の敵対processを暗号学的に識別するものではない。SO_PEERCREDによるUID確認は補助防御であって所有権の代替証拠ではない。

## 2. 実装するアーキテクチャ

~~~mermaid
flowchart TD
  A["I2: authenticated Inspect / fixed catalog"] --> B["LaunchAdmission.reserve()"]
  B --> C["move-only LaunchPermit + NativeOrigin"]
  C --> D["I3: PreparedLocalV3 / Session::start_v3"]
  D --> E["private listener + Waypipe socket"]
  E --> F["ProtocolState::accept_with_origin"]
  F --> G["ClientState (immutable origin)"]
  G --> H["WindowRecord / ordered compositor events"]
  H --> I["I3 WindowHistory + WindowAdmission"]
  I --> J["I4 NativeObservationBuilder + Startup.advance"]
  J --> K["same-tick main adopted / evidence verdict"]
  K --> L["slot + panel/renderer permitted set"]
  K --> M["input permitted per current TargetKey"]
  L --> N["SDK Create/ACK/binding/frame evidence"]
  N --> J
  I --> O["close admission / retire all generations"]
  O --> P["I4 SDK retirement tickets"]
  P --> Q["I2 ReclamationAuthority / journal finalizer"]
~~~

設計上の正本は次の3層に分ける。

- **所有の正本:** I2のimmutable captureとnative Session / listener / accepted client。ここ以外でoriginはmintしない。
- **窓の正本:** I3 WindowHistory。Wayland ObjectId、immutable WindowId、実parent状態、role履歴、destroy/unmapイベント、admission lifecycleの唯一のwriter。
- **表示の正本:** I4 B1 Startup + 実SDK/renderer同target証拠。WindowHistoryから作った観測を受け取るが、WindowHistoryへ偽の採用/Readyを逆書きしない。

compositor owner threadでaccept/dispatch/WindowHistory更新を直列化する。UI、SSH actor、SDK callbackはimmutableな通知を送るのみで、Wayland stateを他threadから操作しない。snapshotの単純な現在値を時間的な証拠の代わりにしない。

## 3. 既存ソースとの対応と変更ファイル

| 場所 | 現在の事実 | 提案する変更 |
| --- | --- | --- |
| native/core/src/compositor/protocol.rs | ClientStateはCompositorClientStateのみ、acceptはDefault ClientStateを挿入 | ClientStateにOption<NativeOrigin>、v3専用accept_with_origin、拒否済clientの閉鎖 |
| native/core/src/compositor/snapshot.rs | WindowRecordにoriginなし、WindowReport.parentはOption<WindowId>。reset_unmapped_toplevelでrole attributesをresetし子parentを旧parentへ付け替える | origin保存、v3向け4値parent履歴イベント、unmap/destroy前の原情報を採取 |
| native/core/src/session.rs | start_with_setupがlistener/display/local Waypipeを構築、dispatchがstate.accept、refresh_statsがv2 nonce/app_id相関を使う | Session::start_v3を追加しversionとoriginを固定。v3専用accept、WindowHistory feed、v2専用GUI判定を分離 |
| native/core/src/linux_desktop_v3.rs（新規） | 未存在 | WindowHistory / WindowAdmission / PreparedLocalV3 / retirement tracker（複雑なら同ディレクトリ内に分割） |
| native/core/src/runtime.rs | slot観測admissible、panel layout、input pathにv2 gui_windowsが流れる | I2 Launch Permit→I3 Local→I4の一体配線、v3のadmitted集合を各sinkへ統一 |
| native/core/src/window_slots.rs | Board(CAPACITY=9)、admissible flag、retirement ACK | v3でのadmission setを反映。拒否窓を勝手にadopt_unrequestedしない |
| native/model/src/linux_desktop_v3/presentation.rs | mainにはParent::None、dialogにはResolved path、role固定。I4 PR #16にNativeWindowFactが追加 | B1 schema/goldenは温存し、I3の純粋history projectionを既存型へ変換 |
| native/core/src/startup_observation.rs（#16） | I4のV3PresentationObserver / newly_adopted_main / main_input_eligible | 実historyを供給し、adoptionとCreate発行順序を接続 |
| android/.../SdkEvidenceBridge.kt（#16） | SDK退役lease/ledger部品 | dialog・mainの全generation回収証拠をI3停止gateと結ぶ（I4担当） |

**注意:** AppMode::LinuxDesktopは現状v2経路でも利用される。v3かどうかは検証済RuntimeBinding/ConnectSpecから決まる**native専用version marker**で区別し、app_id・nonce・UI文字列の形から推測しない。既存AppMode enumやv2 snapshot形式の変更は必須にしない。

## 4. I2 → I3：Launch予約とlocal資源の所有権

### 4.1 現在利用できるI2 #28の実API

- transport/session.rs: reserve_linux_desktop_v3_launch(runtime, session, request, selection) -> Result<LaunchPermit, Status>
- transport/session.rs: start_linux_desktop_v3(permit, local_socket: &Path) -> Status
- transport/session.rs: abort_linux_desktop_v3_launch(permit)
- LaunchPermit: origin(), selection(), request(), generation()。Clone / Deserialize不可。
- LaunchAdmission: reserveでone-shot CAS; enqueueは絶対socket pathを検査。permit未消費dropはgateをsealしhelperをcancel。
- transport/session.rs: take_v3_reclamation()、publish_v3_finalized()。journalはtransport所有。
- NativeOrigin::capture()はI2 #28に存在する。I3が別のorigin生成アルゴリズムを追加しない。

### 4.2 native ownerが実装する順序（厳密）

1. runtime選択v3、session、authenticated Inspect catalog、固定Selection、scene/stop条件、旧v3 pending、slot余力を検証する。v2選択/不整合の場合は**予約前**に拒否する。
2. **I2 reserve**でmove-only LaunchPermitを一つ取得する。二重reserve/再送は行わない。
3. permitのimmutable originに対するlocal準備情報（下記§11 I2-REQ-1）を取得し、Session::start_v3を同期構築する。socket bind、Display、ProtocolState、local Waypipe worker、private directoryのすべてをPreparedLocalV3が所有する。
4. listenerとClientStateのoriginを一度だけ設定してから、I2 start_linux_desktop_v3(permit, socket)を呼ぶ。ここでStartはまだ起動成功ではない。**Accepted != Started != mapped != SDK Ready**。
5. Start APIが非Acceptedまたはlocal準備が部分失敗なら、permit drop/abortとlocal Stop/退役を実行。作成済み資源を捨てたままErrだけ返さない。途中に出たソケット/子processも回収。
6. 同tickで最終状態を公開するとき、transport phase、I3 snapshot、I4 observationを一度だけreconcileする。Stop/terminalが同turnなら先に閉鎖する。

提案API（必要な最小port。実装者は所有型とResultを整理してよいが状態規則を変えない）:

~~~rust
// Native-only; never Serialize/Deserialize and never supplied by Android.
struct CapturedLocalV3 {
    origin: NativeOrigin,
    nonce: String,                 // captured Contextから取り出した同じ値
    session: SessionId,
    request: CommandSeq,
    generation: u64,
}
struct PreparedLocalV3 {
    session: Session,              // includes listener/display/LocalClient
    socket: PathBuf,               // only from the owned RuntimeDirectory
    retirement: LocalOwnerTracker,
}
struct LocalPreparationFailure {
    reason: &'static str,
    partial: Option<PreparedLocalV3>, // partial ownership MUST survive
}
fn prepare_local_v3(
    permit: &LaunchPermit,
    captured: &CapturedLocalV3,
    paths: &ValidatedLocalPaths,
    options: session::Options,
) -> Result<PreparedLocalV3, LocalPreparationFailure>;
~~~

CapturedLocalV3/ValidatedLocalPathsは**新設候補**であり、実装済みではない。実際はpermitから生成した非cloneのleaseあるいはborrowed typed contextでもよい。nonceをremote/Androidの文字列から再構成しない。validated pathは既存native configurationとRuntimeDirectoryの所有境界を利用し、任意command引数を受け取らない。

### 4.3 二重実行/Stop競合

- reserve済みだがlocal未完成: new Launchは不可。Stop時にpermitをdrop、partial localを閉じ、必要なremote pendingを維持。
- enqueue済み: actorが1回だけStartRequested journalをdurableにしてStartする。actorがmpsc(1)から受信してもgateを再開かない。
- Started未確認: main候補を保持できるがpanel/input公開は不可。
- ACK喪失/remote不明: Start再送禁止。I2 Stop/RecoverとI3 local回収をそれぞれ実施し、cleanupが不明ならpendingのまま。
- callback/arrivalが新しいsession/generationへ遅着: old owner側の退役にのみ計上し、current originへ昇格させない。

## 5. native listener / client origin propagation

### 5.1 受理ポート

既存ProtocolState::acceptはv2/Termux/diagnostic用としてそのままorigin無しに維持し、次を提案する。

~~~rust
impl ProtocolState {
    pub fn accept_with_origin(
        &mut self,
        dh: &DisplayHandle,
        stream: UnixStream,
        origin: &NativeOrigin,
        admission: &ListenerAdmissionToken, // native-only owner lease
    ) -> Result<Client, &'static str>;
}
struct ClientState {
    compositor: CompositorClientState,
    origin: Option<NativeOrigin>,  // set only at accept; no setter
}
~~~

ListenerAdmissionTokenはnative内部だけのtoken（新設候補）。**同じv3 Sessionのlistener identity、runtime/session/generation/closing状態と照合**し、clientを挿入する。古いlistenerが閉じられた場合は受理しない。検証後にinsert_clientし、途中失敗時はclient/FDが確実に閉じるようにする。

既存CLIENTS=12の上限を維持し、同originでも受理数を無制限にしない。ソケットディレクトリの所有者/mode、socket identity（可能ならdev/inode）、Waypipe workerとのlive bindingを所有中に検証する。同UID/PIDだけの照合ではv3 originを発行しない。

### 5.2 roleへの伝播

- XdgShellHandler::new_toplevelからToplevelSurface.wl_surface().client() / client dataを読み、originをWindowRecordに**一度コピー**する。
- WindowRecordを失う前のdestroy/unmap/parent変更点では、origin、immutable WindowId、parent raw ObjectId、life、role状態のイベントをcore ownerのhistoryへ残す。
- WindowReportにはv3専用のOption<NativeOrigin>とv3 parent evidenceを追加するか、既存WindowReportとは別のinternal VersionedWindowFactを作る。v2/診断の既存JSON・goldenを壊さないよう、新fieldはserde skipまたは非公開型とする。
- app_id、title、同UIDは*ウィンドウ分類を検証する属性*にすぎず、origin同一性の決定因子ではない。
- foreign-client client stateの欠落/不整合はv3で拒否し、当該toplevelとbufferをpublicへ公開しない。v2/Termuxの現行挙動を変えない。

**不変性テスト:** accept A→v3 origin A→Window A、app_id変更→依然A、unmap/remap→依然A、別listener B→同app_idでもB。v2 acceptでoriginはNone。close後再acceptは拒否。

## 6. WindowHistory：時系列の正本

### 6.1 保持する状態（core owner専有、提案型）

~~~rust
enum ParentEvidence {
    ExplicitNone,
    Resolved(WindowId),
    Unresolved,    // raw parent exists but lookup failed / reset obscured it
    Invalid,       // self/cycle/foreign/destroyed/contradictory parent
}
enum WindowRole {
    Candidate,
    Main,
    Dialog,
    Rejected,
}
struct WindowHistoryEntry {
    id: WindowId,
    origin: NativeOrigin,
    first_client: /* stable client identity, internal only */,
    role: WindowRole,             // Main/Dialog once assigned, never switches
    parent: ParentEvidence,       // v3 historical fact, not window.parent Option
    life: Life,                  // Mapped / Unmapped / Destroyed
    ever_mapped: bool,
    destroy_seen: bool,
    last_event_seq: u64,
}
struct WindowHistory {
    owner: NativeOrigin,
    next_event_seq: u64,
    fixed_main: Option<WindowId>,
    main_destroyed: bool,
    entries: /* bounded live + necessary retiring tombstones */,
    denied: /* bounded diagnostics; never public authority */,
}
~~~

WindowHistoryは**所有・parent・roleの正本**である。B1 Startupのmain adoptionは表示条件を決める正本であり、両者の間でmainが不一致ならfail closed。WindowHistory自身が独立した起動watchdogやReady stateを持たない。

### 6.2 正しいparent分類

現行snapshot.rsのWindowReport.parentは、xdg_toplevel.parent()をWindowIdにlookupできない場合にNoneへ畳む。さらにreset_unmapped_toplevel()はrole attributesをdefaultにし、子parentを旧parentへ書き換える。これはv3の「明示parentなし」の証拠には**使えない**。

- **ExplicitNone:** 初期の正当なrootとして、親が本当に設定されていないことを観測した状態。過去にdialogだった窓がunmapでNoneになっても該当しない。
- **Resolved(id):** same displayの実xdg parentがliveなWindowRecordに解決され、同じNativeOriginを持つ。
- **Unresolved:** raw xdg parentはあるがWindowRecord未発見、またはunmap/破棄操作で正しい過去の親関係が現在値から判別不能。rootへ変換しない。
- **Invalid:** self-parent、循環、異origin、destroy済親、role切替などを検出。表示/入力に採用しない。

**イベント採取位置:** reset_unmapped_toplevelのrole attributes初期化・子parent書換の**前**、forget_toplevelのWindowRecord removalの**前**に履歴へhookする。top-level lifecycle/role初期化時にも採取する。Smithayがset_parentの直接callbackを公開しない場合は、dispatch完了時の生parentを更新しつつ、初期化前の生状態を必ず保持する。「イベントを観測できなかったので親なし」は許さない。曖昧な場合はUnresolvedで拒否する。Smithay internalを無理にforkするより既存ownerのbefore-reset hookを優先。

### 6.3 時系列とbounded memory

- WindowIdは既存のmonotonic allocatorから生成し、同一Session内で再利用禁止。途中のinvalid/rejected roleでもIDを使い回さない。
- historyは「現存最大9窓」＋固定main tombstone＋SDK退役待ちのWindowId/TargetKey＋小さな有界診断ringで構成する。完了したdestroy済dialogを永続に保持しない。
- popup/subsurfaceは各toplevel内のtreeとして扱い、独立role/WindowIdを割り当てない。
- event queue overflow、retiring target増加、trace欠落を黙って切り捨てない。admissionを停止/抑止し、原因を記録。**古い証拠を最新に転用しない**。
- 長期間にわたるdialog作成/破棄の後も、live + pending resourcesに比例する有界保持とする。既存B1 Startupのroles BTreeMapは見たWindowIdを蓄積するため、§11 I4-REQ-3が必要（pure model側のメモリ上限も別に確認）。
- main破棄はowner退役まで固定し、代替rootを採らない。dialog tombstoneのGCとmain固定を混同しない。

### 6.4 history更新トランザクション

1. compositor dispatchでnew_toplevel、parent、map、unmap、destroyを収集。
2. 現WindowRecordのorigin、parent raw、WindowIdを束縛し、イベント番号の順で履歴を更新。曖昧なparentをExplicitNoneへ推測しない。
3. 最新snapshotの生存窓と突合し、見失った窓を即destroy認定しない（destroy callback/owner cleanup証拠が必要）。
4. ownerでadmission判定を確定してから公開admitted集合を再計算。revoked windowをslot/layout/target/inputから同じturnに失効させる。
5. I4用NativeWindowFactを生成する。観測に必要なunmapped/destroyedイベントは有限に保持し、過去の全dialogを毎tick列挙しない。
6. Stop/Pendingのreclamation trackerはevent結果を保持する。I4 projectionで消えたWindowIdも未退役targetの証拠から消さない。

## 7. 固定mainとdialogのadmission

### 7.1 main

- B1 AppId::Editorはmanifestに記録された実Wayland app_id「mousepad」、Demoは既存nonce付きapp_id規則を維持。catalog ID「mousepad-editor-v1」とWayland app_id「mousepad」を混同しない。
- before集合はLaunch reservation時点で同Displayに存在したWindowId。現在の新専用listenerでは通常空でも、空であると決め打ちしない。
- Candidate: originが同じ、beforeに無い、正しいapp_id、ExplicitNone、Mappedという**必要条件**だけを満たしてもI2 Startedなしでは未採用。
- I2 StartedとMappedの両方を観測してB1 Startupが同mainの採用を返したらWindowHistory.fixed_mainへ一度だけcommit。
- 複数root候補が同時存在する場合、Map順やWindowId順で恣意的に選ばず拒否・失敗へ進める。single-main確定後のsecond rootも拒否する。
- main採用と同tickではSDK Createを前倒ししない。次のtickから初めてcurrent mainのCreate/evidenceを扱う（#16 observerのcontract）。
- main Unmappedは同IDの復帰を待てるが、role/first adopted time/20s・10sの元deadlineを作り直さない。main Destroyedは終端/Stop。新rootは再採用禁止。

### 7.2 dialogと現行の9窓上限

- 固定mainが成立していない間、dialogのidentity候補は保持できてもpublic panel/inputを作らない。
- dialogはsame-origin、Mapped、最新parent chainが固定mainに到達、途中の各nodeがlive/validであることを検査する。app_idは同一または空もあり得る。v2 demoだけの「.dialog」suffixをMousepadへ強要しない。
- 同一parentを多段にたどる際はvisited集合と深さ上限9。未解決、destroy、循環、self、異origin、role-changeは拒否。
- 10枚目はcompositorのtoplevel予算でreject/closeし、既存9枚を巻き添えにしない。観測構築時に10枚のB1 Window配列を作ってparse失敗にするのではなく、core拒否カウンタへ記録する。
- 選択済dialogも各TargetKeyのSDK表示証拠が無いなら入力許可しない。これを単なるmain Readyフラグから伝播しない。
- 一時unmapはslot・role・退役ticketを維持し、入力を即時失効。destroy後に実native/SDK退役を確認してからslot返却する。

### 7.3 admission decision（提案）

~~~rust
struct AdmissionSnapshot {
    origin: NativeOrigin,
    fixed_main: Option<WindowId>,
    admitted: BTreeSet<WindowId>,     // layout / slot / Create candidates
    revoked: BTreeSet<WindowId>,      // same-turn close + input reset
    rejected: Vec<RejectReason>,      // bounded, no secrets
}
fn reconcile_history(
    history: &mut WindowHistory,
    transport_started: bool,
    before: &[WindowId],
    current_windows: &[RawCompositorWindow],
) -> Result<AdmissionSnapshot, AdmissionFailure>;
~~~

この関数だけで「SDK-visible Ready」は返さない。主張できるのはWayland ownershipとadmissionだけ。I4のPresentationSummary/current evidenceと統合するまでReadyにしない。

## 8. slot / renderer / pointer / keyboard / IMEの実接続

現行coreではSession::refresh_stats/layout_windows/owns_window、window_slots::Controller::observe、runtimeのpanel layoutsとSDK target生成、Session::focus_window/key/ime/pointerが別経路から窓へ到達する。**window_slotsのadmissibleだけを変更する実装は不十分**。

- 既存v2 LinuxDesktopのgui_windowsはnonce/app_idベースのlegacy ownershipとしてそのまま残す。v3でこの配列を同名の再探索へ流用しない。
- v3用のSession内部versionとAdmissionSnapshotを持ち、layout_windows / slot observe / panel layouts / surface attachのいずれも同じadmitted集合を使用する。
- focus_window、key、IME text / surrounding text、pointer event、hover/pointer capture、resize/close、およびpopup keyboard routeは、current origin/window/scene/targetがadmittedかをnative境界で検証する。API入口・seat focus変更両方にgateを置き、誤って既存focusが残ったときはreset_inputする。
- **二重ゲート:** (1) I3 admitted(window) AND (2) I4 same-current-target input evidence。mainは#16 main_input_eligibleとI3 fixed_mainの一致が最低条件。dialogは個別TargetKeyのCreate ACK、SDK binding、renderer frame等が揃うまで不可（§11 I4-REQ-2）。
- revoke/Stop/scene change/generation swap時は、UIからの次イベントを待たずnativeでkeyboard/IME/pointer captureを失効。旧SDK callback・旧SceneEpochからのfocusを新targetへ流用しない。
- 窓を実際にCloseする制御と、退役途中で入力のみ閉じる制御は分離。非admitted窓の存在は資源回収台帳から消してはいけない。
- 既存Termux・v2・Popup/Clipboard・diagnostic SDK targetの制約を増減させない。v3全拒否で他モードの入力を誤遮断しない。

### 投影の優先順位

~~~text
Native owner + same-session window        -> can be considered
I3 admitted (fixed main / valid dialog)    -> may own slot / panel Create
I4 exact current SDK target established    -> may receive input
Stop / stale scene / revoked / pending      -> immediately deny both new Create and input
Retiring target                            -> kept ONLY for cleanup; never for input
~~~

B1 Startupが同tickで「newly_adopted_main」を返しても、現在tickのpanel/slotへ先行Createを挿入しない。I3 commit完了→次tickのCreate→ACK/binding/frameという順序を固定する。

## 9. I4 Observationへ渡すデータとtick境界

I4 #16の既存API：Observation::native_builder(origin, now_ms)、NativeWindowFact、V3PresentationObserver::begin/tick、ObservedPresentation { presentation, sdk_target, newly_adopted_main, main_input_eligible }。

I3が提供するもの:

~~~rust
struct I3WindowCut {
    origin: NativeOrigin,       // from accepted native listener
    global_window_count: u32,   // exact current product toplevel count
    windows: Vec<NativeWindowFact>,
    admitted: BTreeSet<WindowId>,
    fixed_main: Option<WindowId>,
    input_revoked: BTreeSet<WindowId>,
    owner_alive: bool,
    admission_open: bool,
}
fn snapshot_for_i4(&self) -> Result<I3WindowCut, WindowHistoryError>;
~~~

I3WindowCutは新設候補のcore internal型であり、JSON入力や外部RPCではない。I4に渡すwindowは必ず実accept起源のoriginを持ち、parentは§6の4値分類。global_window_countには実同時生存toplevel数を使い、拒否済10枚目やpopup/subsurfaceの扱いをB1 validatorと一致させる。foreign originを「同一originに書き換えて」収めない。

**同一core turnの順序:**

1. runtime/session/LaunchPermitの現行owner、Stop/remote terminal、現在lifecycle/sceneを確定。
2. Session::dispatchがlistener受理・Wayland dispatchを完了。WindowHistoryにイベント反映し、公開admission cutを固定。
3. I2 StartedとI3 current-origin windowを合わせ、I4 NativeObservationBuilder.windowsへ渡す。同時にactual panel Create/ACK/sdk/rendererのimmutable snapshotを取得。
4. monotonic now_msを一度取得し、I4 V3PresentationObserver.tick()を**一回**呼ぶ。terminal/取消はReadyより優先。
5. newly_adopted_mainがあればI3.fixed_mainと一致を再検査してcommitする。**このturnのcurrent_createはNone**。
6. presentation/SDK evidence/同tick admission gateからslot、panel、inputを公開する。model Err、scope不一致、Stopなら既存inputをresetし退役へ。
7. mainのCreateは次turn以降。遅着ACK/frameは旧TargetKeyのcleanupだけに使う。

I4モデルがhistoryの正本へ逆書きしない。I3による「窓候補/所有」の判定とI4による「起動/表示」の判定が矛盾した場合、公開Readyを作らない。

## 10. Stop、local/SDK退役とjournal barrier

1. stop intent / terminal / main destroy / fatal admission errorを受けたら**最初に**listener admission、new toplevel admission、new Create、focus/inputをsealする。
2. I2 controllerへ同owner Stopを送り、remote process/pipe、SSH channel、forward/routerの完了を別個に確認。local shutdownをtransport/SDK callback待機workerと循環待ちさせない。
3. local Waypipe OwnedProcess/workerを停止・joinし、Display clientsを切り、window/protocol/pixels/SHM/pool / RuntimeDirectory / listenerを**実回収**。session_closedだけでSDK targetは消えたとみなさない。
4. I4へ**main + dialog + decoration fallback + 過去generation + diagnosticを含む現owner関連の全target**のretirement intentを渡す。SDK EvidenceBridgeのdeleteObserved / later cleanup pass / callback・producer count、renderer detach、native binding releaseで回収確定する。
5. I2 take_v3_reclamation()でremote Complete後・Tokio/SSH I/O世代終了後のmove-only handoffを受ける。transport ReclamationAuthority::confirm(LocalRetirement)はtrusted core集計からのみ呼ぶ。SDK削除要求、ring失効、timeoutだけをtrueにしない。
6. 同generation/captureでfinalize_reclaimed()→publish_v3_finalized()。失敗または証拠不明ならjournal/cleanup pendingを保持。新しいsessionの同名originで肩代わりしない。
7. I5のruntime全体のReclamationBarrierはさらにconnection/coordinator/diagnostic/nativeDestroy等を必要とする。I2のrelaunch_allowedを保存許可と取り違えない。

**cold crash時の限界:** 現I2はIoGenerationFenceで過去SSH/Tokio世代終了を確認できるが、過去local Waypipe process、listener、SDK Entityのdurable rosterを自動証明しない。I3はCrashRetirementProviderを考慮するが、reboot/旧process不明を「local clean」と推定しない。最初の製品結合では証拠がない場合pending固定とする。自動Recoverの完全成立には識別子のdurable roster、process identity/starttime、SDK世代の実退役を別途設計・検証する。

## 11. PR #28（I2）・PR #16（I4）へのスコープ別変更要求

以下は**現在のPR成果物と突き合わせた設計レビュー**。この設計PRは両PRのコードを変更しない。Requiredは製品のI3+I4最終統合前に解消する条件であり、必ず既存PRへ直ちにpushしなければならない意味ではない。

| ID | Owner | 判定 | 現時点の事実 / 追加修正 |
| --- | --- | --- | --- |
| I2-REQ-1 | I2 #28、またはI3統合adapter | **Required** | LaunchPermitはorigin/selection/request/generationを返すが、Session::start_v3のprivate socket directoryへ使う同captureのnonceを型付きで取得する公開portが不足。validated nonceを返すgetterか非serialize CapturedLocalV3 capabilityを設ける。remote/UI文字列やorigin JSONの再parseで代用しない |
| I2-REQ-2 | I3 core、I2 review | **Required at integration** | core RuntimeのLinuxDesktopV3Launchは#28で意図的NotReady。I3が予約→partial owner保持→Session::start_v3→I2 startの**唯一の**接続を実装し、早期Failed/Stopでも回収する。I2のone-shot CAS、journal/wireを変更しない |
| I2-REQ-3 | I3/I2 integration | **Required** | take_v3_reclamation / ReclamationAuthority.confirmへのLocalRetirementは同owner・同generation・actual local+SDK退役から生成。coreのfinalizer所有・無循環待ちを実装し、remote Completeだけでclearしない |
| I2-REQ-4 | I2 #28 owner | **Review** | origin/session/generationのstale retry、Recoverのprevious owner、partial local failure、cold crash pendingに対しtest fixtureとactual routeを確認。実host/Waypipe smokeが未検証なら完了とは書かない |
| I4-REQ-1 | I4 #16 + I3 core | **Required** | #16のV3PresentationObserverはI3 history/tickを前提とするが、runtime実配線は未完了。same-tick snapshot、first adoptionの次tick Create、Stopの即時fail closedを接続する |
| I4-REQ-2 | I4 #16 | **Required** | #16のmain_input_eligibleはmainのみを表す。dialogごとのcurrent full TargetKey + Create/ACK + SDK binding + renderer frameを検証したinput eligibilityを提供し、I3のadmitted setとANDする。main Ready一つを全dialogの入力許可に転用しない |
| I4-REQ-3 | I4 #16/B1 model owner | **Required before long-lived product signoff** | Startup.roles (BTreeMap<WindowId, Role>)は過去に観測した全WindowIdを蓄積する。I3だけ履歴をGCしても長寿命dialog連続生成でboundedにはならない。B1 invariantを維持しつつretired/tombstone retention、または安全な上限/背圧を設計して1000+ open/closeを試験。golden意味を変える場合は契約改定レビューが必要 |
| I4-REQ-4 | I4 #16 | **Required** | SdkEvidenceBridge / SdkRetirementHubは現在未接続のledger部品。実SpatialPanelAdapter callback、same-scene delete observer、renderer detach、全generationの回収をnativeへ返し、I2 finalizerの入力とする |
| X-REQ-1 | #28 + #16 maintainer | **Required before merging both** | 両PRはnative/model/src/linux_desktop_v3/report.rsを変更し、#28はRemoteCleanup / ControllerFault / relaunch、#16はvalidate_current / Ready・relaunchを変更。片方の版で全体を上書きしない。特にjournal live中Ready、boot_changed remote cleanup、runtime破棄とorigin再起動の分離を合成して両testを通す |
| X-REQ-2 | #28 + #16 maintainer | **Required** | 両PRはpresentation.rsにも変更。#28 NativeOrigin::captureと#16 NativeObservationBuilder/Startup拡張を保持し、契約のversion、parser、golden一致を回帰 |
| X-REC-1 | I3/I4 implementation | **Recommended** | local owner cut→window cut→I4 evidence cutに単調turn IDを付け、異なるtickのreportを合成した場合にfail closed。current TargetKey以外の集計値を成功証拠へ変換しない |

**変更不要と判断したもの:** B1のNativeOrigin JSON形式/Parent tagged union、I2のone-shot CASや15秒lease、C2 Python wire / Start再送規則、v2/Termux transport、B2 journal format、E1 storage identity、window_slots CAPACITY=9。実コードで新しい反証が出た場合のみ別契約レビューを行う。

**既存PRの重複実装を避ける:** #28で実装済みのNativeOrigin::capture、LaunchPermit、remote Complete finalizerをI3が再作成しない。#16で実装済みのNativeObservationBuilder、V3PresentationObserver、SDK evidence bridgeをI3が独自状態機械に複製しない。I3はconnection provenanceとwindow historyおよびadmission/retirement接続だけを所有する。

## 12. テスト計画と合格oracle（V06/V07/V08の具体化）

| ID | 層 / 手順 | 必須期待結果 |
| --- | --- | --- |
| I3-T01 | v3 listener Aでclient X、旧v2 listenerでclient Y、同app_id | Xのみorigin A。YはNone、混入不可 |
| I3-T02 | v3 socket close後accept、違うruntime/sessionからoriginを付替 | 拒否。過去client/WindowRecord不変 |
| I3-T03 | clientのapp_id/titleを変更 | originは変わらず、role条件に反すればrevoke |
| I3-T04 | Startedのみ→Mapped、Mappedのみ→Started | どちらも同じmain一度採用。先行panel/input無し |
| I3-T05 | main候補が同tick二つ、after-main second root | 恣意的選択無し、APP_WINDOW_UNSUPPORTED系で停止/拒否 |
| I3-T06 | parent lookup失敗、destroy済親、unmapがparentをreset | ExplicitNoneへ昇格せず、main/dialogを誤採用しない |
| I3-T07 | same-origin 2層dialog、異origin親、self/cycle、重複WindowId | 有効chainだけ採用。他は拒否 |
| I3-T08 | dialog→main、main→dialog、main destroy後新root | role/main固定。置換せずStop |
| I3-T09 | 同WindowIdのunmap/remap | 元role/first adoption time/deadlineを保持。旧targetで入力不可 |
| I3-T10 | 9窓生存、10枚目生成、dialog destroy/new dialog | 超過拒否、既存9を保持、releaseはnative+SDK evidence後 |
| I3-T11 | popup/subsurface追加/削除 | 独立windowに計上せず、親のinput制約を維持 |
| I3-T12 | non-admitted targetにfocus/pointer/IME/key/resize/close | seat副作用なし / 旧captureをreset |
| I3-T13 | dialog admittedだがSDK ACK/renderer frame無し | panel lifecycleは進むがinput不可。正しい同target証拠後のみ許可 |
| I3-T14 | v3 Startup Ready→scene/generation交換→旧ACK遅着 | current input失効、旧generationはretirementだけ |
| I3-T15 | Start ACK喪失、Stop同turn、partial prepare失敗 | Start再送なし、permit二重発行なし、資源pendingを残す |
| I3-T16 | remote Complete先行、local/SDK delete遅延 | journal保持、confirmed local後だけclear |
| I3-T17 | cold crash + stale journal + local証拠不明 | relaunch禁止、pendingを保持 |
| I3-T18 | dialog連続open/close 1000回、9 live境界、SDK遅延 | live/pending依存の有界memory、権限漏れなし。B1 roles蓄積も検出 |
| I3-T19 | v2 desktop / Termux / GTK fixture、IME、既存9窓 | golden/操作/回収に回帰なし |
| I3-T20 | full path: 実SSH + C2 + Waypipe + production compositor + Mousepad | real main採用、dialog開閉、file oracle、Stop/回収。fake SSHだけを実host acceptanceと呼ばない |

Rustのpure history testsだけではV06は満たせない。**実Wayland client**によるaccept/parent/unmap/destroy（Smithay実dispatch）を最低限追加する。Core integrationではI2のmove-only permitをstub/mockで複製せず、必要に応じて既存transport fixtureで動かす。I4のgoldenと新native_builder tests、Java/Kotlin SDK test、legacy v2 testsを全て維持する。

実装時のコマンド例（実行環境に依存、ここでは未実行）:

~~~sh
python3 scripts/docs.py validate
python3 scripts/check-host
cargo fmt --all -- --check
cargo test --workspace
python3 scripts/check-linux-real-app --stage acceptance --artifact-root /private/test-artifacts
~~~

acceptanceのCLIが未実装なら「not_run」とし、host/Questの成功として報告しない。Quest 3実機の表示/物理入力、I5 storage CAS、I6/I7最終受入れはこの設計PRの対象外。

## 13. 実装の分割・PR統合順序

CI実行回数を減らすため、細切れPRではなく**最終的にI3+I4を同じ統合PRで検証する**方針を推奨する。既存#16はI3+I4のDraftなので、重複する実装PRを平行してmergeしない。

| checkpoint | 成果物 | gate |
| --- | --- | --- |
| P0 | #28の実装・mainとの基準を確定。#16と競合箇所をレビュー | baseline SHA固定、契約差分が明瞭 |
| P1 | I3-REQ-1のtyped local capture、Session::start_v3 / accept_with_origin | 同origin accept / v2 None / partial failure |
| P2 | WindowHistory / parent/role lifetime、main/dialog admission、9窓 | V06とT01〜T11、全入力源の単一admission |
| P3 | I2 permit→local socket→I4 Startupのsame tick接続、per-target input gate | V07、T12〜T15、No Ready from ACK alone |
| P4 | I3/I4全世代 retirement→I2 handoff/finalizer、cold crash pending | V08、T16〜T18、実回収証拠 |
| P5 | #28/#16 source conflict調整、host/Android/CI全件、実host smoke | 必須case PASS、実機未確認は明記 |

実装の基本順: mainへ#28を安全に統合→#16へ最新mainを取り込む→I3を#16に実装してI4と結合→同HEADでCI/レビュー。#28をまだmergeできない場合は明示的なstacked baseを用い、異なる親を持つPRを単純に成功合算しない。#16のタイトルとscopeはI3+I4のままでよい。

### 小さいLLM向け引き継ぎ規則

- 最初にAGENTS.md、docs/README.md、本書、統合計画§8/18、B1 presentation、PR #28/#16の最新HEADとchanged filesを確認。
- 「既存」を再利用し、「提案API」はsourceと差分が一致するまで実装済みとみなさない。
- WindowHistoryの更新とI4 Startupの更新を別のstate machineにしない。アプリ由来のoriginを絶対に信用しない。
- 必ずscope内のsource修正＋その経路のtestを同じPRへ含める。純粋fixtureの通過で製品Readyを主張しない。
- 確実に回収できないときはpendingで止める。単にタイムアウトしたことを削除完了として使わない。
- 実測結果（CI run ID、host fixture、Quest evidence）は設計書とは別の検証記録とし、観測していないものを合格としない。

## 14. 設計レビューで未決・事前検証が必要な点

1. **Parent event visibility:** 使用中Smithay版でxdg_toplevel.set_parent変更をどのcallback/観測時点から確実に取得できるか。reset/destroy前hookで足りない場合は、未知parentをUnresolvedにしてfail closedし、protocol integration testで確かめる。
2. **Nonce typed access:** #28のLaunchPermitが完全にprivate captureを保持する一方、I3 local directory初期化には同じnonceが必要。getter/capabilityの具体形を#28所有者と決定する。
3. **Dialog input evidence:** #16のmain_input_eligibleをdialogに流用せず、per-target input-ready証拠をどちらのmoduleが公開するかAPIを確定する。
4. **B1 role retention:** 長時間dialog churnでStartup.rolesが無制限に増える点。I4側のbounded retention/背圧を必須acceptanceに追加し、必要なら別の小さなB1契約改訂PRとしてレビューする。
5. **Crash cleanup:** I2 IoGenerationFenceはlocal Waypipe/SDKのcold-crash終了の証明ではない。自動復旧の範囲拡大にはdurable resource rosterまたは外部の独立検証器が必要。それまではpending保持。
6. **Merge composition:** #28/#16のreport.rsとpresentation.rsの両系統変更を合成した後、同HEADでB1 golden、transport/reclamation、I4 SDK/Ready testsを実施する。

関連ソース（設計基準）:
- [main compositor protocol](https://github.com/tin-machine/eidolon-wayland/blob/6f8f57a67399abb0ef88f9e3658b1e6fbaff0b55/native/core/src/compositor/protocol.rs)
- [main compositor snapshot](https://github.com/tin-machine/eidolon-wayland/blob/6f8f57a67399abb0ef88f9e3658b1e6fbaff0b55/native/core/src/compositor/snapshot.rs)
- [main Session](https://github.com/tin-machine/eidolon-wayland/blob/6f8f57a67399abb0ef88f9e3658b1e6fbaff0b55/native/core/src/session.rs)
- [I2 LaunchAdmission implementation](https://github.com/tin-machine/eidolon-wayland/blob/996dc3764d916cf504033e14b1cc8db49b0274f5/native/transport/src/linux_desktop/v3/runtime.rs)
- [I2 transport session ports](https://github.com/tin-machine/eidolon-wayland/blob/996dc3764d916cf504033e14b1cc8db49b0274f5/native/transport/src/session.rs)
- [I2 deferred finalizer](https://github.com/tin-machine/eidolon-wayland/blob/996dc3764d916cf504033e14b1cc8db49b0274f5/native/transport/src/linux_desktop/v3/reclamation.rs)
- [I4 NativeObservationBuilder](https://github.com/tin-machine/eidolon-wayland/blob/e3b9d39b5993d553cba2d5c8e43a4178c3a1a5f7/native/model/src/linux_desktop_v3/presentation.rs)
- [I4 V3PresentationObserver](https://github.com/tin-machine/eidolon-wayland/blob/e3b9d39b5993d553cba2d5c8e43a4178c3a1a5f7/native/core/src/startup_observation.rs)
- [I4 SDK bridge](https://github.com/tin-machine/eidolon-wayland/blob/e3b9d39b5993d553cba2d5c8e43a4178c3a1a5f7/android/app/src/main/java/io/github/tinmachine/eidolon/SdkEvidenceBridge.kt)
