# 通常Freedoomの固定GUI検証

この段階はsourceとhost fixtureの準備であり、通常GUIの実機合格は未確認である。
固定demo128の合格を対話起動・物理キーボード・IME・HMDの合格へ転用しない。

debug APKの`FreedoomGuiCommandReceiver`はDUMP権限と固定action、`case`/32hex`nonce`だけを受ける。
`freedoom-keys`と`freedoom-close`は登録済み固定ID `freedoom`を通常catalog reload→
production `NativeSessionHost.launchRegistered`で起動する。自由定義/argv/env/target/PID/キー本文は受けない。
Hostのenqueue callbackはrequest IDと受理codeだけを返す。起動完了は別にGUI modeのlaunch receiptと
専用app_id cookie、実mapped窓、full target、SDK描画を照合する。

起動前のfoot full target/epochで既存InputTrialを捕捉し、外部input intentの喪失を終端まで保持する。
GUI窓が現れるまでに別の入力があった場合も、遅いfocusを返さない。
runtime/session/scene/Waypipe lease PID/start/group/nonce/UIDが変われば拒否し、現在のfocusやepochへ取り直さない。
GUIへのpointer/keyは同じInputTrialの条件付きdispatchを使う。仮想キーボードをAndroidへ登録したり
`adb input`を使うものではなく、既存GTKと同じtarget-bound synthetic native scopeである。

`freedoom-keys`は固定のEsc→Up→Enter→Yを使う。各段階で配送counter、未押下、同じfocus token、
content前進を確認してから次へ進む。初期main menu選択0、Upの末尾wrap、Quit選択、Y確認は
[Chocolate Doom3.1.1のm_menu.c](https://github.com/chocolate-doom/chocolate-doom/blob/chocolate-doom-3.1.1/src/doom/m_menu.c)に基づく。
この固定列以外の入力は受けず、functionキーを使わない。
最終的なゲームの反応は子のnatural returncode0とGUI receiptで確認する。配送だけで入力成功としない。

`freedoom-close`は最初のfull-target closeのenqueue/ACKを記録する。
同じ窓/targetの残存とcontent前進を確認した場合は`first_close_pending`とし、同じsealed trialで二回目closeを送る。
上流の`ev_quit`は一回目で終了確認、確認中の二回目でQuitを実行する。最初のcloseだけで終了成功にしない。
二回目のenqueue/ACKと自然exitを分け、TERM/KILLやpendingを自然終了へ変換しない。

CloseWindowTargetはnative input resetを伴うため、捕捉epochへ固定した通常key条件をclose後へ流用しない。
`FreedoomCloseProgress`は送信済みrequest最大2件とそのACK、base epochから対応件数の増分、
事前にsealed trialへ登録した自身full targetの退役reset1回だけを許す。
epoch→ACK、ACK→epochのどちらの公開順も6秒以内だけ待ち、逆行・余分なreset・遅れた成功を拒否する。
focus/UPの公開順は既存`GtkInputProgress`のWAIT_FOCUS契約を再用し、focusが先に出ても
DOWN→UPの既知counter途中だけを待つ。余分/逆行/無関係なheldを新baselineにしない。

自然終了の判定にはゲームworker receiptの実child/UID、資源回収、pin不変、PIDFD0、TERM/KILL0も必要である。
元foot継続とGUI全full-targetのnative panel/Board/SDK binding/Entity/listener/controls/MODE回収は同じjobのhost観測で確認する。
raw Wayland、入力本文、画面画像は保存しない。

`freedoom-stop`は同じactor/nonceの明示cleanup専用である。通常`disconnect`と同じproduct workerへ渡し、
enqueue実行時のruntime/sessionも再検査する。新しいsessionへ遅れたStopを返さない。
この場合は元foot/anchorも終了するため、自然exitや元foot継続の合格とは分ける。
元の失敗理由を残し、Idle/pending cleanup空と全SDK/Board owner0を別に確認する。
回収失敗や外部ownerへ交代した場合はpendingを保持し、保存PIDへsignalしない。
