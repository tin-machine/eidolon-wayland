# 通常Linux GUI L5〜L6: Host / native / launcher接続

対象は[通常GUI follow-up計画](nx11-nx18-normal-gui-followup-plan-20261108.md)のL5〜L6。
main `d47c24098855bb811f2ee00463ec4c0978c8579f`（L1〜L4のPR #1をmerge）を基点とする。
自動終了しない固定GTK demoを通常UIから起動し、既存のfull-target入力・Dialog・Close・SDK回収経路へ接続する。

実装の対象はLinux一台の `remote-linux` profile、IPv4、Ed25519 host pin、固定Waypipe 0.11.3 / GTK 3.24.x / SHM経路。
DNS/IPv6、任意exec、terminal、既存実用アプリ、remote IME、複数host、detach/resumeは追加していない。

## 通常操作と状態

| 操作・状態 | 成立すること | 次の操作 |
| --- | --- | --- |
| 設定で「別のLinux」を選択 | 次の明示Connect先を変更する。起動・再接続は行わない | IPv4、port、user、host pin、公開鍵登録、bundle pinsを確認 |
| Linuxへ接続 | immutable profileでSSH認証とnon-root UID probe。helperやGTKはまだ起動しない | アプリを確認 |
| 明示Inspect | 同じSSH actorでpinned helperを用意し、実version/source/capabilityを検証。通常leaseをheartbeatで保持 | `DesktopReady` の一項目catalogでGTKデモを起動 |
| 明示Launch | 同session/catalog scope/revisionを照合し、native側のWaypipe・cookie・slotを先に予約してStart | 入力、Dialog、Close |
| GTKのClose | 子の自然終了を処理し、helper・StreamLocal・SSHを終了。native/SDKの各targetを退役 | 全回収後に新しいConnect→Inspectで再起動可能 |
| Stop / 切断 | 接続先で起動した作業も終了する。local targetとremote ownerを別々に回収 | 全回収を確認して再接続または接続先変更 |
| 回収待ち | 起動・設定変更・別hostへの切替を止め、durable journalを保持 | 同じ設定へ接続し明示Recover。L1〜L4の復旧条件を適用 |

`ProbeReady` をアプリ起動可能と表示しない。通常Linuxの `DesktopReady` はTermuxの `Ready` と区別し、
Footの自動起動やTermux用catalog/任意execへのフォールバックを行わない。
helperは一回の起動を所有するため、自然Close後も同じselectionを再利用しない。
Inspect直後の待機には通常heartbeatがあり、GTK自体にはprobe用の短い終了タイマーを持たせない。

## 認証・設定の契約

`remote-linux` 専用client鍵を、Termux用とは別のAndroid Keystore alias / private directoryへ保存する。
UIで表示・コピーするのは公開鍵だけ。対象Linuxユーザーの公開鍵認証へ明示登録する。
初版は一つのLinux profileに一つのclient鍵で、endpoint変更に伴う自動鍵ローテーションはない。
秘密鍵の保存が失われた場合には既存の `KEY_LOST` 処理を維持し、無言で別鍵へ置き換えない。

profile保存はC1のstrict schema / CAS / atomic publish / fsync / readbackを使う。
host pinはclient identityと独立した接続先認証情報であり、別経路で指紋を照合して保存する。
通常HostはConnect要求時のruntime・request・profile・revision・bindingをcaptureし、
nativeのcredential scopeに含まれるsessionも照合する。scopeの不一致・遅延・二度目の供給を拒否し、秘密bufferはJNI受付後に消去する。

bundle pinsもC1のatomic storage境界を再利用し、確認したprofile bindingと一緒に保存する。
profileが変われば再確認が必要。接続中、起動待ち、以前のアプリのnative/SDK target残存、remote journal回収待ちの間はprofile/pinsを変更できない。
アプリ再起動後やTermux表示中にも通常Linux journalのbarrierを保持する。設定の見かけだけを変えて未回収ownerを消さない。

### Linux側の準備とpins出力

対象Linuxのnon-rootユーザーで、同じcheckoutの既存base bundleを先に用意する。
[通常GTK builder](../scripts/build-linux-desktop-bundle)はbase/B6 assetsを変更せず、通常demo/helperを追加する。
既に存在する通常bundleの上書きは拒否する。古いbundleが存在する場合、live ownerの回収と更新手順を先に判断する。

```sh
python3 scripts/build-linux-desktop-bundle \
  --bundle-directory "$HOME/.local/lib/eidolon-linux"

python3 scripts/build-linux-desktop-bundle \
  --bundle-directory "$HOME/.local/lib/eidolon-linux" --print-pins
```

`--print-pins` は既存base/normal manifest・全file hash・現在のcheckoutにあるadapter/wire/process-owner/demo sourceを検証し、
通常設定へ貼り付けるJSONだけを出す。コンパイラやアプリを実行せず、bundleを変更しない。
成功時のキーは `schema`、`backend`、`base_bundle_sha256`、`desktop_bundle_sha256`、`waypipe_sha256`、
`shm_fixture_sha256`、`desktop_binary_sha256`。通常buildの結果にも同じobjectを `pins` として含める。
この出力を、APKの「転送用ファイルの確認情報」へ貼り付け、対象profileについて確認して保存する。
GTKを起動できるかの実検査は、その後の明示Inspectで行う。

## native / SDKの所有権

Launchのenqueueより先に、native側でsession・request・nonceとslotを捕捉する。
GTK mainがStarted ACKより早くMappedになっても、その窓を未要求の別slotへ採用しない。
同session/request/nonceのStartedとnonce由来app_idを照合し、一意なmainとその実際のxdg parentを持つDialogだけを表示・入力対象にする。
app_idだけで起動成功や親子関係を決めない。

Main/Dialogは独立したwindow/full target `(session, window, scene, generation)` を持ち、
既存のpointer/keyboard/resize/Close/panel/SDK処理へ渡す。許可が失われたwindowは入力・renderから除外するが、
「非表示」をWayland窓の破棄やslot解放の証拠にしない。raw windowの実在、native target、panel、SDK binding、
SDK disposal receiptをそれぞれ観測し、アプリの全世代の退役が終わるまで次の接続を許可しない。

### 初期診断パネルと接続の入口

通常起動直後の診断パネルは、接続後にwindow modeへ移ってから退役する。
このパネルが存在するだけで「以前のアプリが未回収」と判定すると、接続を開始できず、診断パネルも退役できない。
そのため、接続・設定変更・回収先選択の入口では初期診断の所有権とアプリの所有権を区別する。

native render targetの `role=diagnostic` と、SDK登録時に明示した診断用bindingだけを接続の妨げにしない。
SDKはSurfaceのattach前にも登録され、detach後にも残るため、その時点のrender targetの有無やSDK総数から役割を推定しない。
登録した完全なtarget tupleに役割を保持し、通常bindingの診断用への付け替えや、window mode移行後の新しい診断用登録は許可しない。
役割指定を省略した既存呼出しはアプリ所有として扱う。

snapshotの `app_sdk_bindings` は診断用を除く接続判定用の数で、`shutdown.sdk_bindings` は診断用も含む全所有数である。
設定保存に伴うruntime停止を含め、shutdownは引き続き全bindingの明示releaseを待つ。
アプリの稼働・起動待ち、通常render target、panel、古いsceneのアプリSDK binding、Boardの残存は従来どおり拒否条件になる。
remote journalのbarrierも維持し、診断パネルだけの再起動状態では保存済みの同じLinux設定へ選択・再接続してRecoverに進める。

SSH actorの終了とlocal Waypipe/SHM/compositor終了も別の条件で扱う。
helper喪失・ACK喪失・同一bootのunknown ownerはL1〜L4のjournalを残す。
ファイルの欠落やアプリ再起動を回収成功とみなさない。保存PIDを使うkillは追加しない。

local directory/listener/display/keyboard/workerの同期初期化が途中で失敗した場合も、生成途中のSessionを所有し続ける。
remote終了、生成物の明示cleanup、protocol/SHM/memfdの回収後にだけ `stopped` とする。
workerを生成していないことが確認できた場合は `startup_failed` / `startup_cleanup_complete` を使い、`local.waited` を偽造しない。
既存nonce directoryとの衝突や所有identityを捕捉できない失敗は回収未確認として保持し、そのpathを削除しない。
この失敗の回収を、mainの表示・Close成功として数えることはない。

## 実Questでの受入れ

hostテスト・catalog表示・Mapped・helper終了の単独成功を、実描画や入力の成功としない。
対象APK/source/bundle/profile revisionを固定した同一runで、次を記録する。

最初に通常起動からの入口を確認する。診断パネルを手動で消した状態やSDK所有が空のfixtureだけでは、この受入れを代替しない。

- 未設定の起動直後に診断パネルがある状態で、初回Termuxペアリングの保存、Linuxの選択・profile/pins保存に進める。
- 保存済みTermux設定でアプリを起動し、診断パネルを残したまま通常接続を開始できる。
- 保存済みLinux設定でアプリを起動し、診断パネルを残したままConnect→Inspectへ進める。
- durableなLinux回収待ちがある再起動では、設定の上書き・Termux接続を拒否したまま、同じLinuxへの選択・再接続・明示Recoverへ進める。

これらの入口を確認したうえで、GTKの一回の起動を次の手順で受け入れる。

1. 通常設定から明示Connect→Inspect→GTKデモのLaunch。一度のclickで一つのmainが開き、待機中に自動終了しない。
2. mainのMapped、Started/cookie照合、SDK初frameとcontent進行を別々に確認する。
3. mainの入力欄へ英数字を入力し、表示内容を操作担当者が確認する。native delivered counterとcontent進行だけで文字内容を推定しない。
4. 「ダイアログを開く」で別windowが生まれ、実parentが同じmainであることと独立したSDK full targetを確認する。
5. Dialogを閉じ、mainが残ることとDialogの全target退役を確認する。再度Dialogを開き、mainのCloseによる両window終了も確認する。
6. Close後、remote journal、process/pidfd/pipe、StreamLocal/channel/router、local Waypipe、SHM、native/panel/SDK/Boardを照合する。
7. 別runで通常Stopを使い、切断による作業終了と同じ回収条件を確認する。回収後にだけTermuxへ戻し、既存通常経路を確認する。

debug APKには通常Host操作を使う固定caseの観測入口を用意する。設定や任意argvをbroadcastから渡さず、
入力内容・画像を保存せず、physical/HMD/IMEの成功を自動認定しない。
異常終了時は同じruntime/sessionを指定した通常Stopを送り、回収の失敗・未確認を元の失敗と別に残す。

設定画面でLinuxを選択・保存し、未接続かつ全window回収済みのdebug APKを前面に出してから、次のoperatorを使う。
これは通常HostのConnect→Inspect→Launchを一度ずつ呼ぶ。UIボタンそのものの手動確認は上記手順で別途行う。

```sh
python3 scripts/quest-observe-normal-linux-gui --serial "$QUEST_SERIAL" \
  --timeout-seconds 240 --artifact-root /absolute/path/to/acceptance
```

起動後の入力・Dialog・Closeは操作担当者が行う。operatorはsource digest/APK process/runtimeを固定し、
同じsession/nonce/requestの本文なしcheckとnative/SDK資源を採取する。
終了時にはAndroidの完了表示とは独立に、protocol終端・EOF・exit status、forward取消、journal、local resources、
main/Dialogの全世代SDK disposal receiptを照合する。古いrunの完了表示を別sessionの回収へ結び付けない。
採取期限はアプリのTTLではなくoperatorの期限で、期限切れ・中断・検証失敗時には同じrunへのStopを要求する。
`status=observed` はこの観測が揃ったことを示し、`visual_input` / `physical_input` は `not_verified` のままにする。
通常Stop・lease失効・通信EOFやerrorは、回収できてもGUI観測の成功に数えない。
`process_exited` は子が終了した事実であり、GTKがClose操作で正常終了したことまでは証明しないため、`close_action` も `not_verified` とする。
文字内容、実際のClose操作と終了原因、HMD上の見え方は操作担当者の別の受入れ記録が必要である。

## 実装箇所と検証境界

- [通常transport Controller](../native/transport/src/session.rs): 明示操作、catalog、単一起動、pending barrier。
- [通常actor](../native/transport/src/linux_desktop/runtime.rs): Inspect待機とheartbeat/Launch mailbox。
- [共有GUIモデル](../native/model/src/linux_desktop.rs): selection、Started、main/Dialogの対応。
- [native runtime](../native/core/src/runtime.rs) / [Session](../native/core/src/session.rs) / [slot](../native/core/src/window_slots.rs): 予約、admission、full-target退役。
- [通常Host](../android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt) / [接続設定](../android/app/src/main/java/io/github/tinmachine/eidolon/ConnectionSettings.kt): worker上の明示操作、個別鍵、scope capture、UI。
- [通常起動の接続回帰](../native/core/tests/initial_diagnostic_connection.rs): 実Runtimeに診断target/SDK所有を残したままTermux/LinuxのConnect受理と接続先の記録を確認する。SSH接続・認証の成功や実機描画の検査ではない。
- [Androidの接続判定回帰](../android/app/src/test/java/io/github/tinmachine/eidolon/ConnectionAdmissionTest.kt): 診断パネルを伴う初期snapshotから設定・接続・回収先選択を確認し、旧アプリSDK・通常target・panel・Board・journalの拒否を維持する。
- [pins exporterとテスト](../scripts/tests/test_linux_desktop_build.py): 検証済みpinsの読取専用出力、source/hash/manifest不一致の拒否。
- [通常GUI観測operator](../scripts/quest-observe-normal-linux-gui) / [独立判定](../scripts/normal_linux_evidence.py) / [回帰テスト](../scripts/tests/test_normal_linux_gui.py): 別sessionの回収、古いtarget世代、不正終端、SDK漏れ、偽の物理入力成功を受け入れない。

回帰テストとPR CIの結果はPR本文に記録する。この変更を作成した環境ではQuest/対象Linuxへの実配備を行っていない。
したがってL5/L6の実機受入れと、L7a/L7bの既存アプリ・継続稼働・障害・物理/HMD/IME受入れは未確認として残す。
