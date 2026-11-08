# NX12: 窓を正面へ戻す操作の接続準備

既存の窓メニューに「正面へ」を追加するコードを用意する。viewer pose の実機読み取りと通常操作の受入れを分けるため、この checkpoint の `WindowRecenterPolicy.ENABLED` は **false** である。メニューと Host の両方で入口を拒否し、既存の「大きく・小さく・戻る」を維持する。

## 一回の操作

既存の menu/Move 共通 `WindowOperationTicket` を使い、窓を選び直したり別の move owner を作ったりしない。menu の reset ACK・新しい input epoch・Android IME 解放が済んでから、明示選択時の viewer pose を一回読む。前後の runtime、scene、SDK scene handle、XR Focused、theme revision、full target、入力 intent と model 配置を照合する。

候補は viewer の高さを保った yaw 方向 1.5m 前方である。SDK 0.15 の `removePitchAndRoll()` と同じ quaternion 計算を、読み取り専用 probe で使った pure 関数から再用する。非有限・非 unit・真上/真下の特異点・許可座標範囲外は拒否し、各軸を勝手に clamp しない。室内座標や姿勢の値は診断へコピーしない。

読み取りと前後確認を 100ms 以内、読み取り完了から enqueue 前の確認までを 250ms 未満に制限する。これはアプリ内で古い候補を送らないための期限であり、公開されていない tracking timestamp や tracking-valid を代用するものではない。`tracking_freshness=not_exposed` を維持する。

Host は入力の直列化境界内で、同 menu owner、Android 解放、最新 native baseline と group 入力 gate を確認し、一回だけ `set_pose_control` を送る。次を順に確認する。

1. enqueue Accepted と native commandComplete Accepted。
2. 同じ request ID の model pose と、元の full target の `applied_pose`。
3. 現在の SDK 本文 pose、controls/MODE の準備。
4. 元の窓への通常 focus 復帰と、さらに新しい epoch と同じ full target の focus token。

物理サイズ・size command・logical pixel・producer buffer・装飾 canvas は最初の値に固定する。pose 更新の urgent reset と focus 復帰の reset を各一回だけ許容し、余計な epoch、外部 intent、別 request/pose、scene/target 変更は取消になる。ACK 拒否・期限切れに対して自動再送や viewer の再読込を行わず、native が既に受理した配置を古い値へ戻すこともしない。

SDK menu の回収に失敗した場合は、view と操作 owner を保持して次の回収まで入力を止める。group 操作の取消でも size と recenter の両 capture を同じ owner で回収する。グループ化された窓の通常配置変更は既存 native の geometry gate を維持する。

## 表示と資源

有効化後は既存テーマの anchor/offset/diameter/gap に従うメニューに一項目を追加し、MODE は「正面配置を確認中」「入力復帰待ち」を分ける。独立の Surface/Android Display は作らない。メニュー label の固定 512×256 texture/bitmap/direct-buffer slot を再用し、四項目の場合だけ物理高を広げる。増分は一つのボタンの 3 entity/1 listener で、常時 controls の四ボタン数は変わらない。

位置更新は既存 one-shot pose 適用であり、新しい移動アニメーションを追加しない。reduced motion でも追加の補間はない。500ms の group collapse/expand とは別の機能である。

## 検証と残り

純粋な配置候補・操作状態・native JSON・既存サイズ capture の検査 27 件と、実 SDK を参照する Kotlin 11 source の型検査が成功した。誤った owner/target、旧 ACK、寸法変更、余計な epoch、時刻逆行、期限境界、epoch overflow、古い viewer sample、取消後の再実行を拒否する。

このコードの型検査と pure test は、Quest の viewer sampling、メニューへの実 SDK hit、正面配置・他窓不変・raw 入力復帰・全 owner 回収の実機成功を意味しない。viewer probe の実測を確認した後で有効化と固定 SDK case を別に進める。HMD の見やすさと物理操作は最終段階に残す。
