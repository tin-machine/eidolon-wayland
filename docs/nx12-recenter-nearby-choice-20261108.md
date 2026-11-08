# NX12: 重なる正面候補を避ける純粋な計算の準備

`WindowRecenterChoice` は未接続の pure helper であり、通常メニュー、Host、SDK 配置へはまだ使わない。専用 1 窓 case から、2 窓が同じ平面になる場合の検証へ進むための準備である。

viewer は一回だけ capture したものを使い、yaw / 高さを保つ。既定の 1.50m、1.47m、1.53m、1.44m、1.56m、1.41m、1.59m、1.38m、1.62m の順で最大九候補を調べる。1.5m は現在の既定値であり、ユーザーが固定指定した距離ではない。最初の通常候補そのものを計算できない場合は、その場で拒否し、別軸への clamp や viewer 再読込をしない。

他の窓は最大八枚。body の pose / physical size から oriented box を作り、平面の前後に各 12.5mm、辺に 5mm の余裕を持たせる。十五の separating axes を用い、接触・丸め境界は占有中として扱う。同じ平面でも横へ離れている窓は depth 変更を強制せず、交差する傾いた窓や九候補とも占有された場合は候補なしとする。入力の pose / size / identity 不正、選択窓の混入、重複 identity、上限超過は選択しない。結果の `toString` は reason / candidate index / 件数だけを含む。

これは **body geometry の候補予測** に限る。controls や別アプリの UI の mesh、room mesh、SDK の実 collider は含めない。したがって候補があることを「衝突しない」「正面に表示できた」「ACK 済み」の証拠にはできない。

## 次の接続条件

1. 同 runtime / scene の現在の full target、実 applied pose / size、theme revision と他窓の配置を一度 capture し、選択窓以外の一覧が complete であることを確認する。pure helper の opaque identity はその wire 照合の代わりにならない。
2. Host の最終直列化境界で、選択窓の既存 baseline に加えて他窓の captured geometry が変わっていないことを確認する。別窓を動かして空けたり hidden にしたりしない。
3. 一回選択した候補だけを送る。SDK へ送った後で、失敗候補を飛ばして次の候補を無断再送しない。
4. 長い global ray が間の別窓を拾う問題と、同一平面の問題を分ける。候補の body / controls を測る短い normal ray、native の同 request/full target の ACK、実表示と raw focus 復帰を必要とする。希望 Transform の読み取りだけに緩めない。
5. 固定 2 窓で coplanar / 遮蔽の両方を観測し、操作開始後の非選択窓の pose / size / full target が不変であること、取消と回収まで確認する。

この checkpoint の pure tests は SDK / Quest / HMD の合格を意味しない。メニューへの製品採用は上記の接続と実機 gate の後で行う。
