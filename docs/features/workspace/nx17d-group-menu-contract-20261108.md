# NX17d: 通常メニューのgroup操作契約

先行checkpointは純粋な所有・wire契約を定義した。続くSDK/Host接続は、既存の窓メニューへgroupページを加える。
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

既存の4つの窓ボタン、サイズ変更、正面復帰を保持し、group項目を同じ単一menu owner内の別ページへ接続する。
通常メニューの4項目と窓の4ボタン数を混同しない。navigation項目を追加する場合は、既存SDK caseの行数期待値も明示的に更新する。
500msの移動中にglobal入力lockを保持せず、別GUIの通常入力を止めない。
この純粋契約だけでHostの最終enqueue、実menu hit、通常IMEとの排他、raw focus復帰やSDK資源回収を合格とはしない。

## HostとSDKへの接続

menuボタンのDOWNだけが専用permitを捕捉できる。通常のfocus/close/moveや本文入力は、このpermitで解禁しない。
最終`window_group`送信は通常入力と同じ`InputIntentSerial`内でfresh native stateを読み、捕捉permitと期待revisionを照合する。
request IDとcapture状態を先に登録し、一度だけenqueueする。操作受付後はnativeが表示遷移を所有するため、メニューownerを500ms保持し続けない。

新しい`WindowGroupMenuController`は、初期化に先立って外側controllerへ登録する。ページ交換は同じwindow operation ownerを保持し、前ページの回収成功後にのみ次のViewを作る。
回収例外ではView/operation ownerを残し、次ページとgroup送信を止める。size/recenterの既存回収条件も維持する。

アニメーション中は前面のmenuボタンだけhit/listenerを許可する。settlingでは従来どおり4ボタンを実geometry検査し、ACK後の背面controlは隠す。
MODEは開いているgroupメニューと直接入力停止を表示し、既存の1窓1texture/最大9slotの枠を維持する。
menuは同時1枚、最大5行で16 Entity/5 listener、label GPU RGBA 0.5MiB/CPU pixel slot 1MiBの有限枠とする。Surface/Android displayは増やさない。

groupを操作できる2枚以上の通常メニューではnavigationを加え、recenter有効時は5項目になる。
recenter有効時の1枚の通常メニューは従来の4項目のまま（recenter有効化前は各1項目少ない）。窓ボタン数は4個のままである。
固定`window-size`のv3 caseはnavigationを含む正確なaction集合を要求する。以前の4項目版の実機合格を新しいUI合格へ読み替えない。

groupページの「戻る」と操作受付後は、自動で最新の窓へfocusを移さない。直接入力には通常のfocusボタンを使う。
将来の自動復帰は同じ捕捉intent/request/frontの別契約として追加する。通常メニューへの実SDK入力、動いているボタンの操作感、reverse/close/new/reduced motion、scene交換、HMDは未受入れである。

## 保存時点の検証と停止位置

- 純粋な入力gate/wire/表示選択と新menu契約: 31件PASS、検査中のsource hash不変。
- 実Android/Meta SDKのclasspathによる関連18 sourceの限定型検査はPASS、source hash不変。通常Gradle/APK・実機menu操作は別の受入れとして残す。
- D1純粋契約は独立read-only reviewで追加の確定blockerなし。D2のHost/SDK/controller接続は独立review未完了。

D2を含まないBuild176の固定native/SDK group caseは、`first`で`GROUP_OWNER_CHANGED`となった。
このguardはruntime、scene、activity visibility、XR FOCUSEDを一括で確認しており、失効した項目は未確定である。
group操作やgeometryの失敗原因と断定しない。通常設定のdigest復帰、Ready/FOCUSED/Idle、pending cleanupなしは確認された。
同条件の再試行は行わず、既存caseも未受入れのまま保存する。

ユーザーの停止指示に従い、このsourceは未受入れcheckpointとして保存する。再開時は、最新mainへ差分を整合し、D2のowner/cleanup review、通常size/recenter/menuの固定SDK検査、groupの実機検証の順で進める。
