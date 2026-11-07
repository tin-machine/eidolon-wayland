# GTK resize: PC観測後だけ元サイズへ戻す

Build 173では、Android側のowned replacementは800×500と640×360の両方を適用できたが、
拡大直後に戻していたため、PC側の周期的な診断採取が800×500を見逃した。
Androidの完了やCのresize counterだけでSDKの観測成功に読み替えず、既存の独立SDK判定は不合格のまま残す。

修正後の固定検査は次の順序になる。

1. `gtk-resize` が800×500のowned replacementを要求する。
2. 同request/old full target/serial/新target・SDK geometryの既存gateを通過したら、Androidは`LARGE_READY`で保持する。
3. PCはCの`resize_large_seen`、現在の800×500 logical sizeとapplied configure、同request/serial、同full targetの成功draw・SDK bindingを採取する。
4. 採取済みsequence/target/request/frame/serialだけを証拠へ残し、同nonceの`gtk-resize-return`を一回送る。
5. Androidは同trial/current targetの既存atomic dispatch gateを通して640×360を要求し、`WAIT_RETURN`へ進む。復帰・閉鎖・資源回収の確認は従来どおり行う。

固定BroadcastReceiverはdebug manifestのDUMP権限とaction/nonce/extra限定を維持する。
Activityを開かず、追加の自由入力・script・対象選択APIを作らない。
準備前・重複・別nonce・終了後のreturnは受理しない。
`LARGE_READY`の待機は最大8秒、case全体の期限は35秒を維持し、期限内にPCが観測できなければ失敗として回収する。
外部input intent・scene・runtime・target交換は取消のまま。同世代の一時的なrepaint待ちはtarget交換へ誤分類しない。

Android診断には固定の`resize_observation.state`と`return_requests`（0または1）を追加した。
最終PC判定ではACK metadataと保存済みSDK sampleの同sequence/full target/size/frameを再照合する。
入力本文、キー本文、Wayland trace、認証材料は記録しない。

## このcheckpointの検証

- 純粋Kotlinの一回受付・早過ぎる通知・target交換・取消・重複通知: 4件PASS。
- 固定operatorのPython検査: 40件PASS。C/SDK観測後に一回だけreturnし、元サイズ・閉鎖・SDK/Board回収まで進む通しのhost fixtureを含む。
- 実Android/Meta SDK classpathによる変更関連6 sourceの限定型検査: PASS。
- Gradle/APK/Questの実機受入れは統合後に別途行う。物理入力・IME・HMD確認の合格にはしない。
