# NX16a: 九枠の予約・解放モデル

## Build163/164で確認した範囲

Build163でrequest/session/nonce付きfoot markerと実windowの相関を確認した。
Build164のSDK固定case `2124a9e2d7eeeb0a65df3a33eb21ca4e`もPASSし、
JNIの実runtime handleに結び付いた台帳でcenter/rightの二件がBoundになった。
片側をcloseした後はoccupied1/rightだけになり、survivor入力を維持した。
停止後はoccupied0/entries空、cleanup_required=false、通常設定不変を確認した。
台帳capacity9と表示limit2は分けて報告している。九枚表示の合格ではない。

debug build/lint/package/JUnit140件、独立したhost compositor19件が成功。
host modelは先行統合時76件成功。shared host targetの別checkout cacheを使い回さないよう、
host Cargo所有lockと自checkoutのentrypoint mtime更新を行ってから検証した。
三→六→九枚、逆順map/穴再利用、独立client、予算、光学/HMDの受入れは後続に残る。

以下は初期model checkpointと段階的な接続記録。基準`7d00288`では通常APKの窓数上限を変更していない。
[最大9枚の計画](next-phase-termux-startup-linux-gui-plan-20261107.md)を段階化する。
`native/model/src/window_slots.rs`を追加し、受付と描画完了、窓消滅と資源回収を分けて扱う。

## 現在の境界と統合前の課題

| 層 | 現状 | 次の統合で確認すること |
| --- | --- | --- |
| model limits | TOPLEVELS=8 / VISIBLE_PANELS=2 / CLIENTS=4 | 通常toplevel・表示・独立clientの上限を分ける |
| native panels | visible配列2、空きへmapped順に割当、showで交換 | 予約されたslotを正本にし、他窓を勝手に詰めない |
| workspace | slotごとの横並びpose、app_id/slotによる保存復元 | 共通grid基底、新規未配置だけの既定、保存済み自由poseを優先 |
| Runtime launch | opening_window一件。NX16b前半でfootのrequest/receipt/markerを照合 | Boardへの予約接続、並行起動と退役の扱い |
| compositor | 9枚未対応。上限超過はwl_surface.post_error | 余剰窓の拒否で同clientの既存窓を巻き込まない |
| StreamLocal | 実装側MAX_CHANNELS=8、別途model定数も存在 | 九clientと補助接続の実数、channel予算の正本 |
| Kotlin/SDK | bindings>=2、枠1/2のUI | 非同期pending・退役中を含む上限、三→六→九の配備gate |

初回調査時はRuntimeの「新Mappedなら完了」に注目したが、既存のTermux helperにはすでに
nonce/terminal ID付きfoot app_idとrequest ID付き受付receiptがあった。NX16b前半はこの経路を接続した（下記）。
このfoot固有のmarkerで一般GUIやプロセス停止権限まで成立したことにはしない。
所有が不明な窓を「最初の新Mapped」という理由だけで予約へ結び付けない。

compositorのprotocol errorは同clientへ影響するため、容量超過の新toplevelだけへcloseを要求し、
未応答の拒否済みresourceを別のbounded表で扱う方式等を検討する。既存窓の強制closeを容量確保に使わない。
このcheckpointはそのprotocol変更を含まない。

## 予約モデル

位置の順は中央→右→左→上→下→左上→右上→左下→右下。
Boardは一つのRuntimeHandle/sessionを所有し、leaseは位置・単調token・起動requestまたは自発窓のoriginを持つ。
lease/drain IDは既存の正のsigned 64-bit型を使い、JSONでもdecimal stringを保持する。保存設定へは入れない。

`reserve`は起動受付を単一ownerで線形化して呼び、request採番は単調増加とする。
同じ生きたrequestはExistingとして同じleaseを返すので、呼出元は再度launchを送らない。
退役中のrequestはCleaningUp、終了済み/古いrequestはStaleRequest。完了順は逆転しても予約位置を変えない。
10件目はFullを返し、既存の窓や予約を移動・closeしない。利用者の再試行は新requestとする。

| 状態/操作 | 枠を保持する条件 | 解放条件 |
| --- | --- | --- |
| enqueue前の予約 | 起動が未提出 | cancelで即解放 |
| enqueue済み予約 | 取消や失敗後も保持 | 捕捉したdrainのRemoteとNative ACK |
| Bound | 表示/非表示/グループ背面でも保持 | toplevel自体が消えた後に退役へ |
| 窓の退役 | Native/SDK回収待ち | 同drainのNativeとSDK ACK |

受付code、取消要求の送信、timeoutだけを回収ACKにしない。
呼出元が同drainに捕捉したchild/窓/SDK target群の消滅を検査し、その結果をacknowledgeへ渡す。
古いlease・別runtime・別drain・別窓のACKは新しい予約を解放しない。
枠が解放されたら優先順で最初の空きを再利用し、他のBound窓を詰め直さない。

自発的なdialog等はadopt_unrequestedで、既存予約を除く空きへ割り当てる。
Unmappedや隠す操作をwindow_goneへ渡してはならない。通常toplevelの寿命とSDK Surfaceの再生成を区別する。

## 初期gridの占有範囲

Envelopeは本文、NX12bの三ボタンとMODEの占有範囲を、同じ本文plane上で合成する。
採用済み装飾canvasの透明余白も避けたい場合は、content/canvas/originと物理寸法をwith_canvasへ渡す。
全窓で共通の右/上基底へこのoffsetを適用する。head追従や自由移動後の再整列には使わない。

既定本文1.2×0.9m、標準controls、余白0.08mの場合、本文/操作部のpitchは約1.385×1.164m。
1280×1280の装飾canvasを含む代表例では約1.58×1.58mとなる。
既存の中心y=1.4mへそのまま九枠を足すと、下段が既存pose範囲外になり得る。
このモデルはlocal planeの非重複までを検査し、部屋内の適切な中心・距離・HMD可読性を解いたことにはしない。
本統合では共通originと全slotのpose検査を追加し、保存済み自由poseを勝手にgridへ戻さない。

## 資源と実機gate

[NX12b](nx12b-window-move-mode-20261108.md)を含め、九枚では三ボタン81 entity/27 listener、MODE各9 object/mesh/material/textureとなる。
本文9、装飾visual9を加えると、代表的なアプリ周辺SDK entityは108。主操作UIやlauncher等は別に数える。
MODEの要求RGBAは1.125MiB、CPU固定pixel poolは最大2.25MiB。共有CPU poolの保持とactive GPU ownerを混同しない。

1024×768 RGBA九枚は単一コピーで27MiB、1280×1280 canvas九枚は56.25MiB。
これはSDK/SHM/GL等の多重buffer総量ではない。九枚すべて2048²ならsource一枚ずつだけでも144MiBとなり、既存128MiB予算を超える。
上限だけ上げて既存pixel/SHM予算を無制限化しない。予算に入らない場合の拒否・既存窓継続を別に検証する。

次は小さい統合checkpointで進める。

1. launch結果とtoplevelの所有correlation、余剰toplevel拒否、SDK回収ACKとslot drainを接続する。
2. 上限・UI・初期poseの各層を同期し、SDK三枚→六枚→九枚へ進む。
3. 九つの独立client、逆完了/取消/穴再利用、10枚目拒否、他窓継続を実機で確認する。
4. 装飾OFF/静止/回転、CPU/RSS/FD/SHM/SDK/GPU、入力、停止・再接続・sleepを記録し、最後にHMDで受け入れる。

hostのmodel全63件が成功（新規slot/geometry 11件）。逆完了、pending取消、cleanup順序、旧ACK、重複予約/窓、
自発窓、request/token枯渇、四隅/最小最大本文の非重複、装飾canvas拡張を含む。
`CARGO_INCREMENTAL=0 cargo test --locked -p eidolon-model`で再現できる。
隔離checkoutではworkspace解決に必要なgenerated Smithayを既存の生成物へ向け、gitignore対象の子symlinkを利用した。
sourceのみをcommitし、生成物は含めない。構造preflightはrepository非対応のためsourceを直接確認した。


## NX16b前半: 起動requestとfoot窓の照合

上限変更より先に、既存の`terminal_launches`（request/terminal ID/受付成否）と
`io.github.tinmachine.eidolon.foot.t-<nonce>-<terminal_id>`のapp_idを照合する純粋モデルを追加した。
Runtime側でsessionとforward nonceを先に検査し、通常toplevel列だけを候補に渡す。
Popupは別のcompositor列であり候補に含めない。古い既存窓、別nonce、無関係なdialog、
未受付/未mapped、重複receipt、重複markerを起動成功として扱わない。

`runtime.window_launch`はsession/request IDと`phase`を返し、成功時のみterminal/window IDを加える。
`queued`→受付/窓待ち→`matched`または`rejected/cancelled`を区別する。
曖昧な状態は待機を継続する。UIの「ウィンドウを開きました」も、そのrequestの`matched`と
同じ窓がmapped選択肢に存在する場合だけ表示する。単に別の新窓が現れただけでは完了しない。
既存の起動直列化と8秒のUI確認期限は維持する。期限到達をslot回収ACKへ読み替えない。

app_idはclientの自己申告なので、これは信頼済みreceiptと組み合わせた配置相関である。
この値を任意プロセスのkill権限として使わない。現在のcatalogはfoot内terminal起動であり、
一般GUI用markerや複数の独立Wayland clientについての合格ではない。
Termux helperの変更、窓数上限の変更、slot BoardとSDK drainの接続はこの前半に含めない。

model全70件成功（追加した相関7件を含む）。逆順map、無関係な窓、別nonce、既存窓、
受付欠落/未確定/拒否、duplicate、未mapped、初回request無し、wire ID表現を検査した。
`CARGO_INCREMENTAL=0 cargo check --locked -p eidolon-core -p eidolon-transport`も成功。
Android compile/SDK/Questはroot担当の後続gateであり、このcheckpointでは未実施。

## NX16b中間: 余剰toplevelを同clientから切り離す

通常toplevel上限を超えた新しいroleへ、client全体を終了するprotocol errorを送る処理を変更した。
新roleだけへ`xdg_toplevel.close`を送り、WindowId/通常窓一覧/描画へ登録しない。
拒否roleは既存の全wl_surface上限64内で追跡し、roleまたはsurfaceの消滅で記録を回収する。
拒否と交差したcommitはSHM copyを行わず、bufferのreleaseと使用tokenを通常の共有使用検査へ返す。
拒否roleを親にするpopupも表示しない。既存の窓・popupの入力や通常のSHM/pixel予算は維持する。

`app.toplevel_rejections`は累積拒否数、`app.rejected_toplevels`は現在保持する拒否role数。
拒否済みresourceを正常な表示枠数へ加えず、拒否を無視するclientへ最古の窓を強制closeして空きを作らない。
これは容量超過toplevelに関する局所的な変更である。surface/SHMの別予算や不正protocolのerrorまで
client単位の影響をなくしたものではない。表示可能枚数2・通常toplevel上限8・client上限4はまだ変更しない。

固定wire fixture `eidolon-toplevel-capacity-v1`は一つのclientで現上限までmapし、
余剰roleのclose、configure前に交差したattachの破棄とbuffer release、既存窓の同一ID維持と更新継続、
拒否roleの消滅、一窓を閉じた後の新IDでの再作成、最後の全resource回収を検査する。
同fixtureを含むcompositor関連18件がhostで成功した。

```sh
CARGO_INCREMENTAL=0 cargo test --locked -p eidolon-core compositor:: -- --test-threads=1
```

これは同じWayland client内の保護の検査であり、九つの独立client、SDK九枚、HMD表示の合格ではない。

## NX16b後半の前提: Runtime予約台帳

純粋Boardを通常のtarget-driven Runtimeへ接続した。JNI registryの実handleをleaseへ渡し、
追加要求はworker上で予約→transport受付→enqueuedの順に進む。受付されなかった予約だけを直ちに戻す。
同じrequestを再enqueueしない。相関済み窓をBoundへ結び、receipt前にmapしたnonce付きfoot窓を
自発窓として別の位置へ先取りしない。最初の窓を観測するまでは追加を待ち、全窓close後の再追加は可能。

`runtime.window_slots`には予約容量、現在の表示上限、occupied、各lease/state、
未割当窓、終了確認待ちを記録する。**この段階では台帳を接続しただけで、panelの表示先は従来の二枠である。**
次segmentで同じlease位置をpanel割当へ使い、上限と初期gridを同期する。
現時点の`capacity: 9`は予約モデル容量で、`display_limit: 2`や実機九枚合格を置き換えない。

BoundはUnmappedでも保持する。toplevelの消滅後、同session/windowのactive render targetがなくなったことと、
全世代のSDK bindingおよびpanel commandの回収を別々に確認して枠を解放する。
runtime停止では通常panel ACKが受理されなくなるため、render所有thread/contextの終了とSDK所有解除を確認する。
この観測は入力先/Surface所有の回収であり、ドライバ内部全メモリ量の測定ではない。

受付済み起動の失敗/取消には、現在のhelperからterminal単位の終了ACKがない。
タイムアウトやnegative receiptだけで予約を空けず、remote/forward/local/protocol/SHM/directoryを
回収した`Session::closed`を待つ。接続を閉じ直す必要がある状態は`cleanup_required`で区別する。
別nonce/別sessionへ切り替える前にも旧台帳が空くことを待ち、同sessionのrequest/token floorは維持する。
SDKの回収が失敗した場合に予約だけを初期化して先へ進まない。

adapterの追加8件は、逆完了、dialogによる予約位置の先取り防止、receipt前map、Unmapped、
native先/SDK先の回収、失敗起動の保守的保持、enqueue拒否、九枠にpendingを加えた上限、旧scopeを検査した。
core全体は61件成功・1件前提不足・1件既存ignoreとなり、前提を整えた実waypipe 100frameの1件再試験も成功した
（合計62件成功・1件ignore）。`eidolon-android`のhost cargo checkも成功。

再現時は、実sessionテストのproducerがcheckout内`target/debug/eidolon-shm-fixture`に必要である。
`CARGO_TARGET_DIR`で共有targetを指定した隔離checkoutでは、そのbinaryだけをignored directoryから参照する。
別worktreeの同名crateをshared targetへbuildした後は古いmtimeによる誤cache採用があり得るため、
作業用owner lock内で自checkoutのworkspace crate entrypointのmtimeを更新し、Cargoとfixture実行を連続させる。
cache全消去や新しい巨大target複製は不要である。Android/SDK/Quest検証はrootの後続gate。
