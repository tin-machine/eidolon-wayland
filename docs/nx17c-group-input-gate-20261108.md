# NX17c: Hostの窓別group入力gate

nativeのgroup reportを通常workerで読み、captured runtime/full targetごとの入力可否を
不変の小さなgateへ変換する。各キーイベントで大きなnative snapshotを取得し直さない。
通常の最終native配送gateも維持するので、Hostのcache更新前に届いた操作を背面へ通さない。

- 群がない通常状態は従来の入力対象を維持する。
- 群の移動・終点確認中、準備中の新member、安定した背面は入力対象から外す。
- 同じsession/windowでもscene/generationが変われば、旧targetの許可を読み替えない。
- 検証済み群に含まれない独立GUIの入力は継続する。群の起動所有が失効したsessionは停止する。
- 欠落・未知・矛盾したgroup wireは入力停止とする。nativeの同じ版の`window_groups` reportが前提であり、旧nativeとの混在はサポートしない。
- 最大9member/9候補と正のIDを検査し、重複や上限超過を拒否する。

既存の`inputTarget`、保留中focusの最終enqueue、keyboard focus cache、物理キー最終enqueueに適用する。
通常IMEのbeginは最新native snapshotでも照合し、capture、Backendの有効化、Focus/Transaction、非文字キーの配送と
直接入力へのcaptured focus復帰にも同じgateを使う。
Reset/Backendの無効化は止めず、既存のnative ACKとAndroid IMM解除による回収を維持する。
グローバルな選択状態で別窓を補完したり、背面を自動的に前へ切り替えたりしない。

この段階はHost入力境界まで。SDK本文・ring・操作部・MODEの一体移動、group geometry ACK、
normal menu、groupの固定実機caseは後続で接続する。追加Surface/texture/Entityを作らず、
既存のwindow操作排他やring仕様も変更しない。

検証は純粋な許可モデルと実`org.json`でのwire parserを合わせて10件PASS、
実Android/Meta SDK classpathによるHost関連5 sourceの限定型検査PASS。
Gradle/Questでgroupの表示や入力ができたとはみなさない。
