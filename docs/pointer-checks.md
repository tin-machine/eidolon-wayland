# Pointer・focus・captureの検証

2026-11-05、I03実装中。HMDと物理controllerの受入れは最終段階に残す。
固定の合成入力、実Wayland clientが受信したwire、SDKの物理操作を区別する。

## 配送と座標

SDKのlistenerは表示済みで適用ackを得た製品targetだけを消費する。
callbackでfull target、入力epoch、source、UV、button/axisの小DTOを作り、
JNI queueへ渡す。Wayland resourceをKotlin/GLへ持ち出さない。
Coreは実EGL寸法と共通Viewport、window geometry、surfaceの重なり順、
`input_region`を使って宛先を選ぶ。buffer scale/transformを入力へ二重適用しない。

trigger L/RはBTN_LEFT、B/YはBTN_RIGHT、thumbstick clickはBTN_MIDDLEへ明示変換する。
keyboardのXKB offsetをpointer buttonへ足さない。
pointer hoverとkeyboard focusを分け、primary downだけがkeyboard focusを移す。
最初に押したsurfaceとsourceをreleaseまで保持し、別controllerがgrabを奪わない。
capture中の面外座標はclampせず、releaseを元のsurfaceへ返す。
unmap/destroy、grab中のlogical geometry変更、入力resetでは押下を解消する。

keyboardにはreset epochに加えて、focusを選ぶたびに変わる`focus_token`を使う。
同じwindowへ戻った後も古いkeyを受け付けない。
pointerのprimary downでkeyboard focusが変わっても、pointerのreleaseのepochは無効化しない。
これは同じepochをすべてのfocus変更へ流用してreleaseを落とすことを避けるためである。

## SDK 0.15.0で調べた挙動

現物AARを`javap`で調べ、`Scene.onInput`が押下中に元のSceneObjectへ
**別の面のHitInfo**を渡す経路を確認した。`HitInfo.entity`とreceiverのEntityが異なる場合は、
別面のUVを元の面の座標として使わず、最後の有効座標を保つ。
`onNoIntersectionInput`はclick buttonがすべて離れたときに`stopInput`を呼ぶ。
その経路では不足するupを元surfaceへ合成し、pointer leaveを送り、keyboard focusは保つ。
I01の診断用source captureは引き続き製品配送と分ける。

scrollは公開listenerの`semanticType`と`scrollInfo`、実際のthumbstick方向buttonを使う。
`onPointerEvent`でaxisを預け、`onInput`でcontinuous rateへ変換する。
方向buttonの場合は各axis -1/0/1、scrollInfoがある場合はその実値を使い、
欠けた入力を架空のanalog値で埋めない。基準rateは240 logical px/s、初回16ms相当、
callback間隔は最大100ms相当として長い休止の分を一度に再生しない。
同一msのcallbackでは移動量が0でも停止とはせず、実axisがneutralになったときだけstopする。
epoch変更ではpending scrollを捨て、旧thumbstick heldを再生しない。
continuous axisなのでwheelのdiscrete stepを作らない。source/axis/stop/frameを通知する。
Androidの正のVSCROLLは上向き、Waylandは下向きなのでverticalの符号を変換する。
[Android MotionEvent](https://developer.android.com/reference/android/view/MotionEvent)、
[Wayland protocol](https://wayland.freedesktop.org/docs/html/apa.html)を参照した。
SDKのAPI説明は[InputListener](https://developers.meta.com/vr/reference/spatial-sdk/v0.14.0/com_meta_spatial_runtime_inputlistener/)と
[ISDK input](https://developers.meta.com/vr/documentation/spatial-sdk/spatial-sdk-isdk-listen-to-input-events/)を参考とし、
signature・実装の判断は今回固定した0.15.0の現物で照合した。ISDKの全面互換を主張しない。

最初はSDKの`PanelScrollState`を利用する案だったが、Build70でKotlin internalのため
コンパイルが失敗した。JVMのpublic表示だけで公開Kotlin APIと判断した誤りである。
reflectionや可視性の抑制を使わず、上記の公開callbackを使う小さなrate変換へ置き換えた。

## 独立Wayland clientの検査

二つの32×32 toplevelと、位置(8,6)・16×16のchildを作る。
childのinput regionは(4,4)から8×8だけを有効にする。
producer64×96に上下16pxのletterboxを設け、次を実際のwireで確認する。

1. letterboxはhitせず、childの有効領域で(8,8)のenterを受信する。
2. childに描画があってもinput region外なら、下のrootへ入力を送る。
3. primary downはkeyboard focusをrootへ移す。別sourceはcaptureを奪えない。
4. 面外dragでchild local(-12,8)のmotionを受信し、upを同じchildへ返す。
5. 別windowのhover/right clickではkeyboard focusを維持し、primary downで切り替える。
6. continuous source、horizontal 2.5 / vertical -15、両axisのstop/frameを受信する。
7. Shiftと二buttonを保持したresetでrelease→leaveが一度だけ届く。二重reset/late upを再生しない。
8. 別sourceの新captureを古いsourceのupで解除しない。unmapで元surfaceへupを返す。
9. capture中のchild destroyでCoreに押下・hover・captureが残らず、資源も回収する。

hostの実client検査とworkspace Clippyは成功した。
clientのkeyboard試験もfocusを取り直した後の古いtokenをSTALEとして拒否する。
QuestでもBuild71のnative fixtureと通常SDK同居・UI復帰が成功した。
このBuild71時点ではSDK listener経由の合成操作検査も残っていた。Build72で追加し、物理操作は最終受入れに残す。

```bash
scripts/check-host --suite input
scripts/check-host --suite all
EIDOLON_ARTIFACT_ROOT=/absolute/private/work/builds scripts/build-android 74
adb -s "$QUEST_SERIAL" install -r /absolute/private/work/builds/RUN/eidolon-debug.apk
scripts/quest-check --serial "$QUEST_SERIAL" --case pointer-wire --instrumentation
scripts/quest-check --serial "$QUEST_SERIAL" --case pointer-wire
scripts/quest-check --serial "$QUEST_SERIAL" --case keyboard-wire --instrumentation
scripts/quest-check --serial "$QUEST_SERIAL" --case keyboard
scripts/quest-check --serial "$QUEST_SERIAL" --case pointer
```

raw receiptと試行中のlogは、起動時に読む`codex-work-root`の
`eidolon-phase5-implementation/`に保存する。公開文書に端末の識別子やユーザーの入力列を入れない。

## Build71の受入範囲

| run | 検査 | 結果 |
| --- | --- | --- |
| `20261105T162133-588c358e` | pointer-wire / native instrumentation | 成功 |
| `20261105T162139-a682a593` | pointer-wire / 通常SDK同居・新runtime/UI復帰 | 成功 |
| `20261105T162145-176ff23a` | US/JIS keyboard-wire / 古focus token拒否 | 成功 |
| `20261105T162154-48cda8d7` | 通常Hostの固定keyboard入力・reset・再入力 | 成功 |
| `20261105T162204-3c6381bf` | ImageReader foot描画・停止回帰 | 成功 |
| `20261105T162217-cbc50e8d` | SDK foot描画・再生成・停止・UI復帰 | 成功 |
| `20261105T162226-34d01d47` | SDK ray四隅/中央とUV・listener回帰 | 成功 |

pointer fixtureは9 checkpoint、57 pointer event、resetまでに15 frameを実受信した。
Coreのbutton通知12、axis frame2、reset3、終了時の押下0・hover/captureなしを確認した。
client/object/surface/fixture FD/mappingも0へ戻った。
これは固定値をCoreへ渡すwire試験であり、SDKの物理controller入力を代用する結果ではない。

Rust全90test、Clippy/fmt、Python実APK47test・launcher16test、
debug/release/lint/package検査も成功した。
APK SHA256は`5bd187f733300116fa20602a05dec4674324c39ee2f2517a270718d847b8f4e0`、
source digestは`0e0173cad27c6fb60a9e8a56b2910cd159a44f82b31b262aec90960eed0a810b`。

## Build72の追加検査と取消修正

resetによるkeyup/button up/axis stopも`CLOCK_MONOTONIC`のu32 msを使う。
Androidのuptimeと同じ時刻domainを維持し、取得失敗時だけ最後の入力時刻へ戻す。
wire fixtureは通常入力も同じ時計を使い、u32 wrapを許容した時刻検査を行う。
active vertical axisを再開してからglobal resetし、明示stopだけでなくresetからもstopが届くことを確認する。
Build72のpointer wireは9 checkpoint、64 event、button12、axis frame4、reset3で、
最後に押下・hover・captureとclient/object/surface/FD/mappingが残らない。

接続開始直後はtransport ownerがまだSessionIdを割り当てていないことがある。
以前のHostは直前snapshotのSessionIdだけでdisconnectを作り、その間の取消を取りこぼせた。
`cancel_connect(connect_request)`を追加し、受付時に保持する元のrequestと一致する接続だけを取り消す。
Hostは開始と取消をworker上で順序付け、取消完了待ちとqueue BUSY時の再送を行い、
取り消したrequestに遅れて届くcredential要求へ鍵を渡さない。
native testはowner開始をchannelで止め、**SessionId割当前**の取消を決定論的に確認する。
その後の再接続で古い取消を拒否し、正しい取消でSSH/Tokio ownerを0へ戻す。
Quest側でも「開く→即閉じる→再接続」を通常Host経由で確認した。
この実機runでは最初のSessionIdは割当済みだったが、credential提供前に回収できた。

SDK合成pointer run `20261105T165839-f24dbe70` は12 stageすべて成功した。
専用source Entityを二つ作り、実`Scene.lineSegmentIntersect`でfootパネル上の
UV(.23,.31)/(.73,.61)を取得した。実測は(.230004,.3099989)/(.7300002,.6099966)、
world pointの最大誤差は約1e-5m（検査上限1e-4m）だった。
buttonは`Scene.onInput`、drag/scrollは公開`SceneObject.onDragMove/onPointerEvent`から通常listenerへ送り、
Host→JNI→Core→Waypipe→footの経路でcapture・focus・button/axisを確認した。
NoIntersectionから不足upが届き、別sourceがcaptureを奪わず、Shift+合成Menuで両押下が解消された。
旧heldは再生せず、新downで新しいfocus tokenを得た。runtime停止208ms、専用source破棄、
新runtime・通常UI復帰、pending cleanupなしを確認した。
合成MenuはOSのMenu画面を開く試験ではなく、実controller/Bluetooth/文字視認は最終受入れに残す。

Rust全91test・fmt/Clippy、Python実APK50test・launcher16test、debug/release・lint・package検査が成功した。
APK SHA256は`06670892f0da7b7a0cb3aa21f0063cd67f204a3d87b4b3c43b2ac6e7bc7ee3f2`、
source digestは`dbb33ad2d006f19a01bc181a78494acff496eeaf2a5b8685128404a757896b99`。

### ray回帰で分かった診断起動の問題

Build72のray回帰は `20261105T170003-c99d113d` と `20261105T170121-fbbe6004` でPC判定が失敗した。
実交差5点とpress/up/stopは得られたが、終了snapshotのXR状態はIDLEだったため、成功扱いしなかった。
logではNoDisplayのDebugCommandActivity経由でもMainActivityがpause/resumeし、
その後にOpenXRのSTOPPING callbackが届いていた。
`20261105T170334-96520579` ではMainActivityへの直接起動も試したが、同じpause/resumeと
STOPPINGを観測して失敗した。この起動script変更は戻した。
失敗後の通常snapshotでは実際にFOCUSEDへ復帰しており、ray側がその前に結果を保存する問題だった。
Build73で専用sourceを破棄した後、実可視状態と同targetの描画進行を待つ終了stageを追加した。
新しいtarget比較のJSON field順序への依存もreviewで修正し、Build74で実機確認した。
PC側の実可視性・座標・callback・回収の条件は緩めず、可視性を合成で上書きしない。

## Build74の最終回帰

| run | 検査 | 結果 |
| --- | --- | --- |
| `20261105T171044-7fff2d52` | ray校正 / 可視状態・描画の終了待ち | 成功 |
| `20261105T171051-dce7cb6f` | 通常SDKの合成pointer操作・取消/再接続・停止復帰 | 成功 |
| `20261105T171058-fa77e1ff` | pointer-wire / active axis reset・入力時刻 | 成功 |
| `20261105T171105-d2303b70` | US/JIS keyboard-wire / focus token | 成功 |
| `20261105T171110-36f884fe` | 通常Hostのkey入力・reset・再入力 | 成功 |
| `20261105T171119-5d89dc86` | SDK foot描画・再生成・停止復帰 | 成功 |
| `20261105T171127-17bccce3` | SDK再生成後のray再検査 | 成功 |

SDK pointerは12 stage、停止169ms、最初の取消でcredential提供0、専用source回収・pending cleanupなし。
Build72からRust sourceの変更はなく、全91testの結果を再利用し、
Build74のdebug/release・lint・packageと実APKのPython50testを再検証した。
APK SHA256は`a0efa5edd825a97e05c17ee0d9225533ab2a308e5a12bd3480b6871f415c7bcd`、
source digestは`7dcd3ebc41580223ce9b064556fa68bfe835f1c0c7f55da358c617ca7b354db9`。
実controller/Bluetooth/OS Menuの受入れはPhase 5の最後にまとめて行う。
