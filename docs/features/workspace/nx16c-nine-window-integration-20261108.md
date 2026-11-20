# NX16c: 九枠と表示・SDKの接続

2026-11-08。NX16a/bの[予約台帳と回収境界](nx16a-slot-reservations-20261108.md)を通常表示へ接続するcheckpoint。
Build169で単一foot serverの九窓を実SDKまで確認した。九つの独立client、継続性能、HMDの合格は別に確認する。

## 表示と配置

通常toplevel、表示、SDK bindingを最大9にそろえた。client受入とStreamLocalは表示枚数と分け、
九つの独立描画clientに加えて補助/空接続の余地を含む12の有限予算とした。
StreamLocalの双方向copy buffer上限は768KiB（12×2×32KiB）。pixel128MiB、SHM総128MiB、
同clientのSHM64MiB、buffer最大2048²のbyte/寸法予算は変更しない。
後述のBuild168実測を受け、surface件数は64→128、同clientのpool件数は8→16へ調整する。

通常Runtimeのpanelは、BoardのBound leaseを正本として同じslotへ作る。
予約中、逆順map、Unmapped、窓を閉じて回収待ちの穴を、他の窓で勝手に埋めない。
九つの順は中央→右→左→上→下→左上→右上→左下→右下。
新規未配置の既定中心は`[0,2.0,1.5]m`、共通planeのpitchは縦横1.8m、向きは共通の正面とする。
全中心のposeは既存の有効範囲に入り、下段中心は0.2m、上段中心は3.8mになる。
本文全体が部屋の床/天井内に収まる保証やHMDの快適さは、この数値検査には含まれない。

保存済みのposeは優先する。同じappを後から開いてapp_idが曖昧になっても、
生存中の窓の手動poseを既定へ戻さない。曖昧なappの新しい保存/復元を推測で行わない従来の制限は維持する。
SDK Entityは同session/window/slotのworkspace poseが届いてから作り、旧横並びfallbackで先行生成しない。
管理画面は枠1/2への移し替えを廃し、各窓の入力選択と閉じる操作を使う。IME終了確認を待ってから直接入力先を選ぶ。
既存の丸いfocus/close/move、MODE、テーマ位置、CPU/RSS/I/Oリングの仕様は維持する。

## 超過と退役

10件目の予約はBusyで、既存の窓をcloseして空きを作らない。command enqueueのAcceptedと処理完了のBusyを混同しない。
予約できない自発的Mapped窓も表示へ混ぜず、捕捉した現在session/windowへcloseを一度要求する。
通常toplevelの別予算を超えたroleには、同client全体へのprotocol errorではなく新roleだけのcloseを送る。
拒否に従わないresourceも既存のsurface/SHM予算に従う。

予約枠の解放にはtoplevel消滅とnative/SDK回収が必要で、Unmappedやtimeoutは解放条件にしない。
単一foot serverへの起動はまだ一件ずつ直列化する。起動完了が曖昧なまま次の結果へ予約を付け替えない。
同時に無関係な窓が増えnative側の上限へ達した場合、未完了の起動は確認待ちのままになり得る。
その場合も別窓を成功結果にせず、接続を閉じて回収する。並列起動やterminal単位の終了ACKは後続で扱う。

## 資源の目安

| 対象 | 九枚の計算値/条件 |
| --- | --- |
| 1024×768 RGBA source | 一コピー27MiB |
| 1280² RGBA装飾canvas | 一コピー56.25MiB |
| source+canvas | 一コピー83.25MiB。旧/新frame同時保持や他のRGBAを含まない |
| 単一foot clientのSHM | 3MiB bufferを二枚/窓なら54MiB、三枚/窓なら81MiB。64MiB制限を超え得る |
| SHM pool件数 | 固定footは最低一窓一pool。旧8件では九窓不可、件数調整版は16件 |
| SDK操作部 | 三ボタン81 entity/27 listener、MODE9 object/mesh/material/texture |
| MODE pixel | GPU要求1.125MiB、CPU固定pool最大2.25MiB。driver実使用量ではない |
| アプリ周辺entity | 操作部81+MODE9+本文9+装飾visual9=108。主UI/launcherは別 |

SHM poolは複数bufferを収容できるため、窓数から実pool数やbuffer総量を断定しない。
九枚が計算上/実機で予算拒否になったら、観測した寸法/pool/一時ピークを記録し、
寸法調整または限定された件数/予算案を検討する。合格させるためにbyte/pool上限を無計測で緩めない。
九つの2048² sourceだけでも144MiBとなるので、最大寸法九枚の同時成立を保証しない。

既定footの本文1.2×0.9mと1280canvasは、最大の許可済みテーマcontrols/四anchorを含めて1.8m pitchで離れる。
小さいGUI/dialogは最低1280canvasが本文より物理的に大きくなるので、任意GUIの装飾visual全体の非重複は保証しない。
透明visualは入力collisionを持たず、body/controlsの入力面と分ける。光学的重なり、可読性、移動後の快適さはHMDで確認する。

## 検証と次の受入れ

hostではmodel71件、core63件（既存ignore1件）、StreamLocal3件が成功し、Android/transport crateのhost checkも成功した。
追加検査は九枠の順序とpose範囲、通常foot canvas/controlsの幾何、逆順map時の固定slot、
Unmappedの穴、重複割当拒否、旧ACK拒否、同app追加時の既存pose保持を含む。
既存の余剰role/接続拒否の実Wayland fixtureと実waypipe100frameも通過した。

```sh
CARGO_INCREMENTAL=0 cargo test --locked -p eidolon-model -p eidolon-core -- --test-threads=1
CARGO_INCREMENTAL=0 cargo test --locked -p eidolon-transport forward::tests -- --test-threads=1
CARGO_INCREMENTAL=0 cargo check --locked -p eidolon-android -p eidolon-transport
```

Android compile/SDK配備後は次の順で判定する。過去の二枚SDK caseの合格と九枚の合格は分ける。

1. 三枚→六枚→九枚で、`window_slots.occupied/entries`、各panelのslot/target/applied、render frame増加、
   workspace pose適用、adapter binding、controls/MODE所有数を照合する。
2. `app.clients/pool_count/pool_bytes/pixel_bytes`、FD/メモリ/CPU/SDK/GL、装飾OFF/静止/回転時を記録する。
   単一foot serverの九窓と九つの独立Wayland clientを別々に確認する。
3. 10件目拒否、未知の自発窓、閉じた枠のSDK回収後の再利用、他窓の入力継続を検査する。
   native commandの受理、モデル更新、SDK適用、実入力を別段階で判定する。
4. stop/reconnect/scene終了で全SDK owner・台帳・native参照が回収されることを確認し、最後にHMDで高さ/視野/操作を受け入れる。

StreamLocalの旧固定validatorは512KiB/8channelを前提にするため、次APKの768KiB/12channelに対応する。
過去artifactを読むときは、既知の旧/新予算とaccepted/peakの整合を検証し、任意の大きな値を許容しない。
構造preflight/queueはこのrepositoryが非対応なのでsourceを直接確認した。

## Build165の最初の実機結果と診断の追加

Build165（source `d24b25d`、debug/lint/package/JUnit144件成功）の固定case
`window-slots` はnonce `fd01f2208ddfd86597c6de3a77cdd06d` の `first` でtimeoutとなった。
最初の1枚が全ready条件を満たさず、3/6/9枚やpool超過まで進んだ証拠はない。
通常設定への復帰は成功した。timeout判定が最終snapshot保存より先にあったため、
初回結果から失敗した条件を特定できなかった。

同じBuild165で既存 `window-move` case（nonce `d54e2359e47a112e7253a80f2dd6579b`）は成功した。
別に通常1footの生成直後と遅延後を確認し、遅延後にはpanel applied、
workspace applied poseとfull targetの一致、render frame増加を確認した。
通常の保存配置とnonce別private preferencesのcase既定配置は区別する。
これらの結果は9枚の合格には含めない。

次版は固定ready条件をglobal/各窓のboolへ分解し、timeout前の最新compact snapshotと
待機stage/経過時間も結果へ残す。SDK/pose/inputの成立条件は緩めない。
静的循環調査とcore回帰では、未appliedの9panelから初期poseを得られること、旧sceneを拒否することを確認した。
新しい診断を入れた後に再実行し、原因と次の変更を記録する。

Build167の診断v2（nonce `aff2d2a35b7cab60b498a1fe4069609e`）では、
first窓の全個別gateがtrueで、globalでfalseなのは `theme_selected` だけだった。
private preferencesの初期値はOffだが、fixtureはbuiltinの選択を送らずcyan-orbitを要求していた。
正常配置/SDK/入力targetの成立を確認し、通常設定は不変のまま復帰した。
診断v3はbuiltin選択→実load/revision採用確認→初回footの順に変更する。
製品の成立条件やpool予算は緩めていない。次は3/6枚の資源実測から9枚へ進む。

## Build168: 六枚の実測と有限件数の調整

nonce `c4c6e43d6e7bfa12276cf862e8493b6c` でtheme setupが成立し、三枚・六枚の
panel/pose/SDK draw/controls/MODEが全てreadyとなった。

| 同一foot client | surface数 | pool数 | pool bytes | pixel bytes |
| --- | ---: | ---: | ---: | ---: |
| 三窓 | 31 | 3 | 9,117,696 | 22,861,760 |
| 六窓 | 61 | 6 | 18,235,392 | 32,539,520 |

七枚目の追加中にREMOTE_EXITとなり、pool_rejectionsは0だった。六枚で61surfaceの観測と、
ProtocolStateが64surfaceで同clientへprotocol errorを送るsource条件が整合する。
この版にはsurface拒否counterが無いため、旧実行にcounter値があったとは主張しない。
通常設定へ復帰し、六窓分のSDK ownerは回収できた。九枚の合格ではない。

次版は有限件数だけをsurface128/pool16に調整し、surface拒否counterも保存する。
footの九窓は約91surface/9poolが必要で、同じ静止寸法のSHMは約27.4MBとなる。
client SHM64MiB、全SHM128MiB、pixel128MiB、最大buffer寸法、転送buffer、時間上限は維持する。
十二client全ての小poolが残る場合の件数枠は最大192であり、FD/owner回収を別に確認する。
検索・overlay・全窓同時resize等の一時ピークは未測定で、任意の最大寸法九枚を保証しない。

hostの実capacity fixtureは九窓と追加の空surfaceで91surfaceを保持し、
余剰toplevelの拒否、既存client継続、枠再利用、全object/surface/SHM/FD/mapの回収まで検査する。
pool単体の追加回帰は、九つの約3MiB poolの同時保持、16件を超える拒否、
最後のArc参照までの計上、64/128MiBのbyte上限不変を確認する。

初回host再検査では、旧PoolLimit fixtureが9poolで拒否を期待して失敗し、
同じprocess内のfixture lockがpoisonして後続検査へ波及した。
17poolを保持して超過を要求する固定wireへ更新した後、compositor20件が成功した。
検査をskipしたり、poisonを無視して成功へ読み替えたりしていない。

## Build169: 九窓の固定SDK caseが成功

source `39ae43d`、nonce `0bec06ae40fb9240e6858661976c604e`、fixture `window-slots-v3` がPASS。
builtin選択後、三→六→九枚の指定slot/pose、全panel適用、各targetのSDK bindingとdraw、
controls/MODE所有数、入力targetが成立した。九枚時はsurface91/pool9、SHM27,353,088bytes、
pixel42,217,280bytesで、surface/pool拒否counterは共に0。

十枚目をBusyで拒否して既存窓を維持し、一枚を閉じた後のnative/SDK/panel drain、
空きslotへの再追加、残窓のpose不変と合成キー配送を確認した。
停止後はBoard entries/occupiedと九窓のSDK ownerがゼロになり、通常設定不変・別runtime復帰も成功した。
通常診断SDKの復帰bindingと、停止した九窓の回収を混同していない。

debug build/lint/package/JUnit167件、release build/lint/package/JUnit141件、
91surfaceの実wire/byte上限回帰を含むcompositor20件、feature付きworkspace clippyが成功。
これは単一foot client・固定寸法・合成入力の受入れで、独立九client、検索overlay/同時resize、
HMDの高さ/視野/読みやすさ、持続FPS/メモリの合格には広げない。
