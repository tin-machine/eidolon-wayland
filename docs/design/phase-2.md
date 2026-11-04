# Phase 2 — APKからTermuxへのSSH

前提はB03。[PC開発用SSH](../phase-0-reproduction.md)とは別の接続・鍵・試験を作る。製品endpointは`127.0.0.1:8022`に固定し、MVPに任意remote host設定を追加しない。

[独立probe](../native-preflight-20261104.md)で、APK内一時鍵・host key不一致拒否・exec・StreamLocalの4接続×8 MiB echoを実機確認した。成立性の証拠とコードを再利用できるが、永続credential管理、同時接続上限、切断復旧、remote launcherの製品実装は以下のタスクに残る。

<a id="t01"></a>
## T01 — アプリ専用鍵と開発時pairing

作成: `CredentialStore.kt`、`native/transport/src/host_key.rs`、`scripts/quest-pair`。

1. Rustでアプリ専用Ed25519鍵を初回生成する。PCの`quest_termux_ed25519`は再利用しない。
2. 秘密鍵はKotlinのAndroid Keystore AES-GCM鍵で暗号化してnoBackupFilesDirに保存する。Keystore aliasはアプリ専用。nonce再利用禁止、schema/version/AADとciphertext/tagを保存する。署名用の非exportable Ed25519鍵をrusshへ接続する方式はこの段階に持ち込まない。
3. 復号鍵は認証時だけRustへ渡し、debug command、logcat、snapshot、例外messageへ出さない。Kotlin/native側双方の一時byte bufferを可能な範囲で消去し、通常のJVMメモリーを完全消去できると保証しない。
4. 公開鍵のみdebug診断の専用fileへ出す。`quest-pair`は承認済みの同じQuestを識別し、APK公開鍵をTermuxのauthorized_keysへ重複なく追加する。
5. TermuxのSSH host公開鍵とuser名をADB `run-as`で取得し、APK profileへ保存する。private keyをPCへ抽出しない。profile更新はtemp＋rename、既存pin不一致時は失敗させる。
6. 正しいknown host keyでだけ接続する。未知鍵は`HOST_KEY_UNKNOWN`、不一致は`HOST_KEY_MISMATCH`。ネットワークで見えた鍵を無条件に承認しない。

実装した`quest-pair`は、Phase 0でPCに登録した公開known_hostsの端末alias（ADBの物理端末ID由来）とhost keyを両方照合してから更新する。PC秘密鍵は読み出さず、APKへ渡さない。`~/.ssh/eidolon_quest_known_hosts`が既定で、未登録端末・不一致pinは失敗させる。Termux Pythonも必要で、bootstrapの依存へ含めた。既存authorized_keysのbyte列とoptionを保ち、同じ鍵は重複追加しない。profileはAPKのinstallation IDへ束縛し、再起動後は新PIDとそのPIDが発行した同じbuildの診断を照合する。

debug pairingはGitHub debug版Termuxのrun-asを利用する限定経路。release向けには公開鍵を表示し、Termux側のhost fingerprintを人が照合・登録するUIが別途必要。T01でdebug成功しただけで配布版pairing完了とはしない。

検証: 鍵生成の冪等性、暗号文の改変拒否、Keystore鍵喪失時の再pair案内、known/unknown/mismatch、別端末混同拒否。fixtureは合成鍵。既存のPC認証・authorized_keysを上書きしない。

<a id="t02"></a>
## T02 — russh接続、認証、exec

作成: `native/transport/src/{session,exec}.rs`。russh `=0.65.0`、Tokio 1.xの解決版をCargo.lockへ固定する。最初はrussh標準featuresをAndroidでcompileし、native暗号backendに限る失敗なら原因を記録してring等を比較する。鍵検証を弱めてbuildを通さない。

状態順: TCP connect→server key完全一致→公開鍵認証→session channel→固定probe exec。connect 5秒、認証10秒、probe exec 10秒を初期deadlineにする。人のpin登録待ちは接続試行と分離する。errorは共通codeへ写像し、パスワード/keyboard-interactiveへのfallbackはしない。

probeは`id -u`と固定tokenのecho。stdout/stderrを別ringへdrainし、exit status/exit signal/EOFを分ける。PTYは要求しない。stderrが満杯でもchannel処理が止まらないことをhost fixtureで確認する。

QuestではAPKの診断から実行し、Termux UIDで応答したことを確認する。PC→ADB forwardの成功を代替証拠にしない。

製品実装は`connect(profile_id)`→I/O ownerのpin検証→coreの`credential_required`→Kotlin workerのKeystore復号→専用JNI slot→認証→固定exec。通常UIの接続ボタンも同じHost経路を使う。core/UIはSSH awaitへ入らない。接続ごとのTokio runtimeはI/O supervisorが所有し、切断時は全task/FDを回収してから再接続を許可する。Stop/DisconnectはCancellationTokenで割り込み、古いsessionのDisconnectで現在のcredential slotを消さない。

`ssh` caseは正常・pin不一致・別認証鍵・鍵提供前取消をstandard JNIで検査し、新runtime復帰もhandleで照合する。`ssh-product` caseは製品Host経由で認証し、GL更新中の接続維持・live停止・新runtimeの描画再開を確認する。両方を受入に用いる。stdout/stderrは64KiBの末尾ringへ保持し、超過後もEOFまで排出する。診断はUID/token一致/byte数/exit/EOFだけで、stream本文や鍵を含めない。

<a id="t03"></a>
## T03 — StreamLocalをWaypipeなしで検証

作成: `native/transport/src/forward.rs`、host SSH fixture、`scripts/termux/eidolon-session.py`のecho mode。

forward方向を固定する:

```text
Termux echo client → Termux remote socket → sshd → forwarded-streamlocal channel
  → APK russh → APK Unix echo listener
```

remote socketはTermux専用session directoryの`p`、local socketはAPK専用session directoryの`p`。異なるUID・pathであり、Wayland socket `w`とも異なる。

実装順:

1. APKのUnix listenerをlisten済みにする。
2. SSH execでremote launcherのprepareを実行し、0700の新directoryをexclusive作成する。
3. `streamlocal_forward(remote_path).await`の成功応答を待つ。0.65.0では`Result<(), Error>`で拒否を検出できる。
4. `server_channel_open_forwarded_streamlocal`でsessionとpathを完全一致検査、channel数上限を確保する。
5. 所有済みchannel/replyを短いtaskへ渡す。local Unix connectを2秒以内に済ませてから`reply.accept().await`、失敗ならreject。callback内で転送終了までawaitしない。
6. `Channel::into_stream()`とUnixStreamを双方向copyし、一方のEOFでは反対方向の残りをdrainする。cancelは別tokenで両方向を終了する。
7. 最後にforward cancel、task join、owned socketとdirectoryの回収を行う。

`ChannelOpenHandle`の破棄は自動rejectなので、単にcallbackをreturnしてaccept済みと思わない。default handlerの無条件acceptを製品へコピーしない。[固定crate source](sources.md)でこの仕様を確認した。

host: 1 byte/64 KiB/8 MiBの決定的データhash一致、同時4channel、half-close後の応答、slow reader、cancel、wrong path、古いsession、forward拒否。使用buffer上限とtask数を測る。Questで同じecho caseをAPK UIDから実行する。

実装では接続ごとのRouterがSessionIdとremote pathを保持し、最大8channelのpermitをlocal connect前に予約する。受付と停止を短いgateで直列化し、取消後にtaskがjoin対象から漏れないようにする。copy bufferは方向ごと32KiB、合計上限512KiB。これはSSH library内部queue・kernel buffer・固定echoのbufferを含む総メモリー値ではない。SSH受信windowは256KiB、channel message queueは128件とする。defaultの2MiB windowではhost大容量試験が断続停止し、この設定で再検証した。無制限queueへは変更していない。

大容量host試験は製品と同じOpenSSHを、合成鍵・専用port・一時directory・所有processだけで起動する。`openssh-server`が必要。拒否/上限/取消はrussh server fixtureでも検査する。launcherの配備は`scripts/quest-install-launcher --serial DEVICE`。ADBで固定配布物のSHA256を照合し、prepare応答のhashをAPKに組み込んだ配布物とも照合する。CLIはprepare/echo/cleanupと16桁hex nonceだけで、既存sessionの上書きや任意shellを許可しない。prepare成功ackがないnonceをcleanupで削除しない。応答消失時の残件照合、heartbeat/leaseとremote子回収はT04で扱う。

<a id="t04"></a>
## T04 — remote launcher、lease、障害収束

remote launcherはTermuxのPython 3標準libraryだけを使う。Termux側に必要なpackageと導入版を記録する。ファイルはユーザーprivate homeの管理対象directoryへ配置し、配布物hashを確認する。

許可modeは`prepare`、`echo`、`pattern`、`foot`、`cleanup`。任意shell commandを受け取らない。引数は固定modeと`[0-9a-f]{16}`のnonceのみ。SSH execは固定launcher pathと検証したnonceから`shlex.quote`相当で構築し、pathやアプリ名の生文字列連結をしない。

prepareはremote root所有権・symlinkでないことを検査し、session directoryをexclusive作成する。絶対socket pathを1行のJSONで返す。APKはprofile由来のTermux home配下であること・長さ・nonceを検査する。既存socketをunlinkして上書きしない。

実行modeは子を新process groupで起動し、stdout/stderrをdrain、exit statusを返す。SSH execのstdinで10秒ごとにheartbeatを受ける。EOFまたは60秒lease失効で、そのlauncherが起動したprocess groupだけへTERM、3秒後KILL、wait/reapを行う。任意PID、他のfoot、Termux全体をkillしない。pid再利用を避け、保持中の子handleとgroupを対象にする。

process groupを離れた全子孫まで回収できるとは仮定しない。Waypipe/footの終了に伴うWayland切断・PTY hangupも観測し、fixtureで意図したhelper/footが残っていないことを確認する。利用者がshell内で明示的に切り離したdaemon/tmux sessionの一括終了は対象外。group番号だけを後日のcleanup用PIDとして再利用せず、launcher生存中の子handleで終了する。launcher自身がOSにkillされた場合の残件は次回検査で記録し、他processを名前検索して一括killしない。

停止時にSSHが生存していればforward cancelとlauncher cleanupを実行する。SSH断ならleaseによるremote回収を待ち、次回接続で所有markerとnonceを照合して残件を検査する。cleanup失敗をログに残し、正常停止としない。

検証: 接続直後停止、auth失敗、exec開始前cancel、forward後のremote失敗、片方向EOF、大量stderr、SSH切断、lease失効、二重cleanup。各段階でtask/FD/child/socketが残らないこと。再接続は新SessionId・nonce・childを発行し、作業内容を復元したとは表示しない。
