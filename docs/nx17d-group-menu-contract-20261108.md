# NX17d: 通常メニューのgroup操作契約

このcheckpointは通常メニューを接続する前の純粋な所有・wire契約であり、通常UIの動作はまだ変更しない。
native/SDKのgroup表示を検証する`window-groups` caseとは別に扱う。

本文の入力許可と、窓の操作メニューを開く許可を分ける。
アニメーション中は本文のFocus/Key/Pointer/IMEを止めたまま、検証済みfoot群の前面にだけ専用メニュー許可を作る。
nativeの現在のruntime/full target、scene、theme revision、group ID/revision、member集合を捕捉し、
最新の選択窓や別generationへ読み替えない。
group revisionを保ったまま`animating`から`settling`、`stable`へ進むことは許す。
別revision、member追加・消失、外部入力intent、scene/runtime交換、所有失効は捕捉済みメニューを取り消す。

`WindowGroupMenuWire`はnativeのbounded metadataだけを読む。
新しい群は2〜9枚の検証済みcandidate、全窓のbase pose/物理サイズACK・panel・成功drawが必要である。
既存群はfoot scope・前面・source ready・現full target・panel・drawを確認し、group移動中にbase pose ACKを再要求しない。
既存群はclose後の最後の1枚にも「展開」を提供できる。1枚での新規collapseや前面切替は提供しない。
Nativeの最終command処理でも、launch/Boardと期待group ID/revisionを従来どおり検証する。

`WindowGroupMenuCapture`は、同ownerのreset ACK、Android IME解放、fresh epochを待ってからメニューを有効にする。
選択後はSDKメニュー資源の回収成功を待ち、一回だけcommandを送る。
回収失敗中に送信・別ページ作成・操作owner解放を行ってはならない。
BUSY、拒否、期限超過を自動再送せず、遅い別request/別ownerのACKも使わない。
送信後にnative自身がgroup revisionを更新することは想定内だが、同じ現在owner/requestのcommand ACKだけを受け取る。
このACKは操作受理であり、SDK geometry適用やgroup配置完了とは区別する。

既存の4つの窓ボタン、サイズ変更、正面復帰を保持し、group項目は同じ単一menu owner内の別ページへ接続する予定である。
通常メニューの4項目と窓の4ボタン数を混同しない。navigation項目を追加する場合は、既存SDK caseの行数期待値も明示的に更新する。
500msの移動中にglobal入力lockを保持せず、別GUIの通常入力を止めない。
この純粋契約だけでHostの最終enqueue、実menu hit、通常IMEとの排他、raw focus復帰やSDK資源回収を合格とはしない。
