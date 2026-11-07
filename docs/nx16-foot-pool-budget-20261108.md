# NX16: foot九窓とSHM pool件数の境界

2026-11-08のread-only調査。九枠SDKケースの次試行に備えた設計判断であり、
Build165の初回一窓timeoutをpool拒否と断定する記録ではない。製品の予算・起動方式は変更していない。

## 固定sourceと実測

通常起動は`eidolon-session.py`のfoot 1.28.0 server一つへfootclientで窓を追加する。
`--override=tweak.max-shm-pool-size-mb=0`は[過去の512MiB仮想pool問題](waypipe-forwarded-shm-checks.md)の対策として維持している。

[foot 1.28.0のshm.c](https://codeberg.org/dnkl/foot/src/tag/1.28.0/shm.c)と
[terminal.c](https://codeberg.org/dnkl/foot/src/tag/1.28.0/terminal.c)の取得済み固定sourceを確認した。
archive SHA256は`4296be402b5684d049534598e69db92b918f92beac9dab76b585207045f0b037`。
今回読んだ六ファイルもarchiveと一致した。Web表示は取得できなかったため、以前取得・検証済みの同版の実体を使った。

- 各terminalはそれぞれgrid用のbuffer chainを持つ（terminal.cの1366行付近）。
- `shm_get_buffer`はそのchain内の同じ寸法のbufferだけを再利用する。
  再利用できない場合は`get_new_buffers(count=1)`が新しいmemfdと`wl_shm_pool`を作る（shm.cの327/473/589行付近）。
- gridのcached bufferにはchain自身の参照が残る。ほかの窓のchainへ統合するallocatorはない。
  複数bufferを一poolへ作る`shm_get_many`は、通常gridの経路では使わない。
- 固定Waypipe 0.11.3もcreate_poolのobjectを個別に追跡し、そのmessageを転送する。
  `tracking.rs`のcreate_pool分岐で窓間のpoolを一件へ統合しない。

したがって、この固定構成の九つのMapped footは、gridだけで最低九つのpoolを必要とする。
buffer待ち、寸法変更中の旧/新buffer、検索・overlay・CSD等の補助chainがあれば、さらに増え得る。
寸法を縮小しても件数の下限は減らず、max-shm-pool-sizeの値も件数対策にはならない。

Build165の別の通常一窓では、pool_count=1、SHM bytes=3,555,328、拒否0を観測した。
これは一窓の下限と整合する観測であり、九窓のpeakの実測ではない。

## 現予算の意味と候補

nativeはpool objectのdestroy時だけでなく、Smithayが保持するbuffer参照の終了後、実mapping/FD解放まで件数・bytesを計上する。
その計上を早めたり、client間で予算名義を分けるだけの対策は行わない。
現在のpool8件/clientを超えるcreate_poolはWayland protocol errorであり、同じfoot clientの既存窓も失われ得る。
余剰toplevelだけをsoft-closeする機能とは別の境界である。

| 候補 | 変更と限界 |
| --- | --- |
| 件数のみ有限の16件/client等へ見直す | 小さな変更。client64MiB・総128MiB・pixel/copy等のbyte予算を維持する。最大mapping/FD/metadata数は増えるため、3/6窓の推移と9窓/resize peakを測って決める。16で任意負荷が安全とは未確認。 |
| 複数foot server/clientへ分割する | 8件/clientを維持できるが、長寿命process/PGID、server socket、launch receipt、workload registration、Stop/失敗回収のowner設計を変更する。単にfootclientを別processにしても描画clientは分かれない。 |
| foot allocatorを共有pool化する | client側patch/build配布とbuffer offset/参照寿命/resize/全chainの設計が必要。upstream既定の小さな設定変更では実現しない。 |
| 寸法低減・補助描画の削減 | bytes/peak抑制には有用だが、九gridの最低九poolを8件へ収められない。単独の解決策にはしない。 |

pool8件は旧二窓構成の実装予算であり、利用者の固定要件ではない。
九窓へ合わせて件数だけ有限に見直す案を、独立server化より小さい候補として比較する。
byte制限が同じでもFD/metadata上限は別なので、無制限化はしない。
例えば16件/client・client最大12なら、単純上限は192 poolであり、現96件からの増分も確認する。

## 次の判定

1. 診断v2で初回pose/SDK gateを成立させ、3/6窓のpool_count/bytes/pixel、拒否、窓の継続を記録する。
2. 一窓当たりの定常値と追加・resize時のpeakを分け、有限の件数候補と既存byte予算への収まりを判断する。
3. 予算を変更した場合は別APK/source/nonceとして九窓を検査する。上限拒否で同一clientの既存窓を失う可能性も受入れへ含める。
4. 九つの独立client、HMD可読性、物理入力は、単一foot server九窓の結果から推定しない。
