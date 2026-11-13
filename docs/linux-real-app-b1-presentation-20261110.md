---
id: linux-desktop-v3-b1-presentation
title: Linux Desktop v3 B1 presentation evidence contract
type: contract
design_status: proposed
feature: linux-desktop-v3
component: presentation
phase: B1
created: "2026-11-10"
last_reviewed: "2026-11-13"
baseline_commit: 151639486d41f6117a7e44c2d977af19ee430ee6
depends_on: [linux-desktop-v3-b1-contract]
related_sources:
  - native/model/src/linux_desktop_v3/presentation.rs
  - scripts/linux_desktop_v3_presentation.py
---
# L7a B1: origin・parent・表示成立の共有契約

2026-11-10（日本時間）。対象は[B1引き継ぎ](linux-real-app-b1-handoff-20261110.md)の純粋な型・検証・固定vectorである。
[L7a詳細設計](linux-real-app-l7a-design-20261109.md)の表示契約を現実装と照合し、D/E/Fが共有する意味をここで固定する。
B1ではnative accept/admission/watchdog、SDK callback、通常UIへ接続しない。
fixtureの成功は合成された証拠の整合性を意味し、実originの生成、SDK表示、実機入力、Quest受入れの成功ではない。
`available` / `production_acceptance_passed` / `quest_verified` はfalse、v3 acceptanceは未実装のままとする。

## 1. 既存型と照合する境界

| 対象 | 現実装の一次情報 | v3共有契約への対応 |
| --- | --- | --- |
| full target | [TargetKey](../native/model/src/surface.rs) | `session/window/scene/generation` の全fieldを比較する。runtimeはこの型に含まれない |
| ID表現 | [ID codec](../native/model/src/ids.rs) | 正のsigned 64-bit IDを正規decimal stringで表す。数値JSON・0・符号・先頭0・overflowを拒否する |
| Create ACK | [runtimeのAdapterApplied/AdapterFailed](../native/core/src/runtime.rs)、[panel command表](../native/core/src/panels.rs) | `command_seq + target + action` が一致し、Create成功には同targetのrenderer frameが必要 |
| SDK所有 | [runtimeのsdk_bindings/diagnostic_sdk_bindings](../native/core/src/runtime.rs) | runtimeごとの所有を照合し、診断panelの所有を実用mainの証拠にしない |
| renderer | [TargetReportとReport](../native/render/src/lib.rs) | 同targetのframe数に加え、全体の`paused`とtargetの`presentation_paused`を区別する |
| accept | [ClientState/ProtocolState::accept](../native/core/src/compositor/protocol.rs) | 現在はoriginを持たない。v3ではnative ownerから付与する経路が別途必要 |
| parent | [WindowRecord/WindowReportとsnapshot生成](../native/core/src/compositor/snapshot.rs) | 現在の`Option<WindowId>`は未解決parentを`None`へ畳むため、v3のparent正本としてそのまま使用できない |
| 現行main照合 | [v2のcorrelate/owned_windows](../native/model/src/linux_desktop.rs) | nonce付きapp_idと現在のroot再探索はv2の契約として維持する。v3へ流用しない |
| SDK取得と通知 | [SpatialPanelAdapter](../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt)、[NativeSessionHostのsdkBinding/ackPanel](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt) | Entity、Surface、SceneObject/listener、native attach、成功ACKを別々の証拠として扱う |

TargetKeyが同じでもruntimeが異なれば別の所有者である。SDKのEntity IDもscene終了後に再利用され得るため、Entity ID単独で相関しない。
既存`Outcome::Matched`の意味を変えず、v3の`presentation_state`を独立させる。

## 2. NativeOrigin

`NativeOrigin`は次の全fieldを持ち、fieldの欠落・余剰・重複を拒否する。

| field | 型と意味 |
| --- | --- |
| `runtime` | 既存`RuntimeHandle`と同じ正のsigned 64-bit decimal string |
| `session` | 既存`SessionId`と同じ表現 |
| `request` | captureしたLaunchの`CommandSeq`と同じ表現。後続heartbeatの要求番号へ更新しない |
| `nonce` | 既存Launchと同じ16桁lower hex |
| `scope` | v3契約で導出した64桁lower hex |
| `app_id` | 明示選択した固定catalog ID |

originの正本は、Launch enqueue前にnativeが予約したowner/contextである。専用display/listenerのaccept時に付与し、
clientからWindowRecord/WindowReportへ伝播する。app_id変更・remap・SDK generation交換で別originへ付け替えない。
当該listenerを別GUI/Termux/runtimeで共有せず、listener directory、local Waypipeのlive ownerと終了状態を照合する。
この生成・伝播はDの製品実装であり、B1のparserが実現するものではない。

remote JSON、app_id、title、同UIDという事実からoriginを生成しない。
local peer credentialに見えるWaypipeのPIDを、remote MousepadのPIDと解釈しない。
originは認証済みSSH/専用displayの配送相関であり、改変されたremote hostや同UIDの敵対processに対するアプリ実行証明ではない。

**B1のfixture JSONはnative側の合成観測を表す。** これを通常wireから受理する新しい権限にしない。
Started、window、ACK、frame、terminalの各証拠は、現在の予約ownerと照合してから判定に使用する。
別ownerのterminalを現在のLaunchの停止理由へ流用してもならない。

## 3. Parentと固定role

parentは次のtagged unionで表す。variantに記載したfield以外は拒否する。

```json
{"kind":"none"}
{"kind":"resolved","window":"7"}
{"kind":"unresolved"}
{"kind":"invalid"}
```

`none`はxdg parentが明示的に存在しない状態であり、lookup失敗の代用ではない。
`unresolved`は親参照があるが有効なWindowIdへ解決できない状態、`invalid`は失効・破棄済み等で利用不能な状態である。
未知のkind、`resolved`のwindow欠落、他variantのwindow追加も拒否する。

mainとして採用するには、同origin、新規のWindowId、明示的に親なし、Mapped、固定manifestにあるapp_idと、同LaunchのStartedが必要である。
Mousepadの実測app_idは`mousepad`であり、GApplication/schema IDやtitleから補完しない。
既存demoはnonce付きmain/dialog app_idの制約を維持する。

mainの採用はlease中に一度だけ行い、WindowId・origin・mainというroleを固定する。
Started先行/窓先行の両順序を許すが、両者が揃うまで表示slotへの採用は完了しない。
起動前のWindowIdは採用せず、複数rootがある場合も一つを恣意的に選ばない。

同mainの一時unmap/remapは再検査できるが、最初の採用時刻とWindowIdを保持する。
mainの破棄後は終了観測・回収へ進み、新しい同app_idのrootへ差し替えない。
過去にdialogと分類されたwindowは、親消失後もdialogであり、mainへ昇格しない。
現在のsnapshotだけでrootを再探索する方法では、この履歴を維持できない。
unmapや非表示はClose完了、process終了、所有解放の証拠ではない。

dialogは同originの有効なxdg parent chainが固定mainへ到達するものだけを許す。
途中nodeの別origin・失効・循環・自己parent・WindowId重複を拒否する。重複はmapへの変換前に検出し、最後の値で上書きしない。
同app_idまたは空app_idの実dialogはあり得るため、Mousepadにdemo専用の`.dialog` suffixを要求しない。
chainは全体９窓の既存上限内で検査し、10枚目や二つ目の独立mainを表示・入力へ採用しない。
popup/subsurfaceの既存同client tree制約は別途維持する。

## 4. Presentation状態と同targetの証拠

| `presentation_state` | 意味 |
| --- | --- |
| `awaiting_main` | 同LaunchのStartedと、採用可能な同originのmainがまだ揃わない |
| `awaiting_sdk` | 固定mainを採用済みだが、現在targetのSDK表示成立条件が揃わない |
| `ready` | 下記の表示成立条件がすべて現在の同targetで成立した |
| `stopping` | terminal・取消・期限・回復不能失敗により、同ownerの停止/回収へ進む |
| `pending` | 回収未確認が残り、正常終了/再利用を認定できない |

`first_failure`と`cleanup_pending`を別に保持する。
起動時の最初の失敗を、後続のCANCELLEDや回収の失敗で上書きしない。
遅着Started/Mapped/ACK/frameがstopping/pendingをreadyへ戻すことはない。

readyには、次の条件をすべて要求する。

1. captureしたLaunchのStartedと、同originの固定main bindingがある。
2. そのmainが現在Mappedである。
3. 現在runtimeとTargetKeyの全fieldが一致する。
4. 現在のCreate panelがapplied、errorなしである。
5. 成功ACKの`command_seq`・action=`create`・full targetが当該Createと一致する。
6. 同runtime/full targetの非diagnostic SDK bindingが存在する。
7. 同full targetのrenderer frame数が１以上である。
8. renderer全体の`paused`と当該targetの`presentation_paused`がともにfalseである。
9. activityがvisible、現在sceneがtargetに一致、XRがFocusedである。

dialog・診断panel・旧runtime・旧scene・旧generationの証拠を合算しない。
frameの総数、SDK bindingの総数、Entityの存在、remoteのStartedだけではreadyにしない。
SDK binding/ACK/frameを一つの独立した「ready=true」申告で置き換えない。
実入力には、表示成立後も既存のcurrent-target/focus gateを必要とする。

## 5. 有限な起動追跡

時刻はmonotonic clockの観測値であり、wall clockやremoteの時刻を使用しない。
初期値は設計上の上限であり、対象Questで検証済みの保証値ではない。

| 期限・競合 | 固定する規則 |
| --- | --- |
| Launch受付 | nativeがactivity visible・現在scene・XR Focusedを確認する。未成立ならStartをenqueueしない |
| main待ち | Launch受理時刻`t0`から20,000ms |
| SDK待ち | StartedとMappedが揃い、mainを最初に採用した時刻から10,000ms |
| 期限の境界 | `now >= deadline`で期限切れ。ちょうど期限のready候補も受理しない |
| 同tickの競合 | owner/contextを検証した証拠について、terminal・取消・期限超過をready候補より優先する |
| remap・世代交換 | current targetを再照合するが、固定mainのWindowId・最初の採用時刻・元期限を維持する |
| 装飾fallback | 一度だけ元期限内で許す。新targetで全証拠を再照合し、再失敗・期限切れで停止する |
| ready前の前提喪失 | activity/scene/XR前提を失ったら起動を取消す。時計を停止して待たない |
| ready成立後 | `startup_completed`をlatchし、startup watchdogを再起動しない |
| 失敗後 | 同ownerのbounded Stop/回収へ進む。自動再Launchや通常Close要求への置換をしない |

固定reasonはmain期限に`APP_LAUNCH_TIMEOUT`、SDK期限に`APP_PRESENTATION_TIMEOUT`、
回復不能表示失敗に`APP_PRESENTATION_FAILED`、起動取消に`APP_STARTUP_CANCELLED`を用いる。
unknown origin/非対応窓は`APP_ORIGIN_UNKNOWN` / `APP_WINDOW_UNSUPPORTED`の意味を維持する。
不正・未知reasonを自由文字列として許可しない。

実行可能modelでは、unmapまたはcurrent Create消失でそのCreateを退役扱いにする。
再表示には同mainのstrictly newer targetと新しいcommand_seqを要求し、旧ACK/frameの再掲を拒否する。
fallbackは失敗した現在Createの観測にnativeが`fallback=true`を付け、一度だけ許可する。
この一回は旧targetを退役して新Createを待つ回復可能な処理であり、元の10秒期限を延ばさない。
回復不能な最初の起動失敗を記録した後やready成立後に、このflagで復活させることはできない。
既に成功した新targetとともにfallbackを事後申告する形式は受理しない。

ready後のvisibility・remap・leaseの通常運用はD/EおよびL7bの範囲である。
起動完了latchは「以後常に表示できる」という主張ではなく、起動用20秒/10秒watchdogを再武装しないための記録である。
旧targetは全世代の退役追跡に残し、SDK disposalとnative回収が確認できるまで所有を返さない。

## 6. 共通goldenで固定する拒否条件

B1の共通goldenはPython/Rustで同じ合成証拠を検査し、後続Kotlin/model検査でも利用する。
実行時に同じ実装から期待値を再生成して自己照合しない。

| 入力・変異 | 必要な判定 |
| --- | --- |
| Startedのみ／Mappedのみ | awaiting_main。順序の一方を誤って即成功にしない |
| Started先行／Mapped先行の後、正しい組合せ | 同じmainを一度採用 |
| runtime/request/nonce/scope/app_idの一項目だけ差替え | 証拠を拒否 |
| 明示親なしを未解決parentへ変更 | rootへ採用しない |
| dialogのparent消失 | mainへ昇格しない |
| main破棄後の新root | mainを差し替えない |
| parentの別origin・循環・重複WindowId | chainを拒否 |
| ACKのruntimeだけを変更 | TargetKeyが同じでも拒否 |
| ACKのsession/window/scene/generationを各一つ変更 | full target不一致として拒否 |
| ACKのcommand_seq/actionを変更 | Createの成功に流用しない |
| frameがdialog・診断panel・旧target由来 | readyにしない |
| SDK bindingだけ・Entityだけ・frameだけ存在 | readyにしない |
| global pauseまたはtarget pause | readyにしない |
| 前提喪失・terminal・取消とreadyが同tick | readyへ進めない |
| readyが期限ちょうどに到着 | timeoutを優先 |
| remap/fallback後に元期限が経過 | 新targetの時刻で期限を延ばさない |
| first failure後にCANCELLED/cleanup pending | 元の失敗を保持 |
| stopping/pending後に遅着成功証拠 | readyへ戻らない |
| ready後の世代交換 | startup watchdogを再武装しない |

この表は後続D/E/Fにも適用する共有拒否条件であり、B1の合成検査だけで全ケースのSDK/Quest実測が済んだという意味ではない。
製品への接続時には、listener由来のorigin、実parent履歴、現在targetのACK/frame、全世代回収を実経路で確認する。
