# NX16a: 九枠の予約・解放モデル

2026-11-08。基準`7d00288`。**model-only checkpointであり、通常APKの窓数上限は変更しない。**
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
