# NX16c: 九枠と表示・SDKの接続

2026-11-08。NX16a/bの[予約台帳と回収境界](nx16a-slot-reservations-20261108.md)を通常表示へ接続するcheckpoint。
コードとhost検証の段階であり、Quest九枚の表示/性能、九つの独立client、HMDの合格は別に確認する。

## 表示と配置

通常toplevel、表示、SDK bindingを最大9にそろえた。client受入とStreamLocalは表示枚数と分け、
九つの独立描画clientに加えて補助/空接続の余地を含む12の有限予算とした。
StreamLocalの双方向copy buffer上限は768KiB（12×2×32KiB）。surface64、pixel128MiB、SHM総128MiB、
同clientのSHM64MiB/pool8件、buffer最大2048²の予算は変更しない。

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
| SHM pool件数 | 一窓一poolでも九窓は9件となり、同clientの8件制限に達し得る |
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
