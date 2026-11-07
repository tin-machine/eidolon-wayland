# NX18 B3b: Linux固定SHM用transport owner

[B3a](remote-linux-helper-b3a-20261108.md)のLinux helperを、認証済みrussh接続と既存StreamLocal Routerへ接続する独立moduleを追加した。今回のcheckpointはtransport内のAPIと回収契約まで。Controller、runtime、JNI、APK caseにはまだ配線していない。既存Termux Launcher/Journalと通常接続は変更しない。

## APIとcapability

`eidolon_transport::linux_probe` は `debug-fixtures` featureまたはunit testだけでcompileする。通常releaseにはmoduleを含めない。後続core/APK配線も、B2と同じ実feature/sourceSet境界でconfig/commandを拒否する必要がある。

- `Pins::parse()` は4096bytes以内のstrict JSON。schema 1とbundle、Waypipe、fixtureのSHA-256を受け、型違い・重複key・未知field・hex形式違いを拒否する。通常snapshotへserializeしない。Debugは値を伏せる。
- helper hashはAPKと同じsource bytesからcompile時に取り込む。自己申告のhelper hashを初回pinとして採用しない。
- `Request` はimmutableなsession、v2 profile binding/revision、認証後のUID、nonce、private files directory、local Waypipe socket、pinsを受ける。endpoint/path/argvをADB入力から選ぶAPIではない。callerは必ず同じ接続でcaptureしたidentityから作る。
- `run(client, router, request, cancel, publish)` は一つの固定pattern sessionだけを扱う。通常GUI capabilityは変更しない。Linux接続は引き続き `ProbeReady` とし、catalog/foot/start_appを許可しない。
- `Report` はphase、UID、件数、終了/回収bool、固定error tokenだけ。remote HOME/socket、profile binding、bundle pins、子のstdout/stderr本文は保存しない。

## 起動と終了の順序

1. nonce、UID、binding、十進revision、local同UID Unix socketを確認する。取消済みならremote execもjournal作成もしない。
2. SSH session channelを確保した後、exec直前にLinux専用journalをdurable化する。ここから先のexec ACK不明は「未起動」に読み替えない。
3. 固定 `/usr/bin/python3 -I -S -c <固定bootstrap>` を使う。account HOMEをpwdから取得し、private directory/file所有・mode・nofollow・link数・サイズ・bundle hash・helper hashを確認してからhelper sourceを実行する。helperのbundle再読込にも同じ期待hashを束縛し、toolsの実行前とstart直前に全pinを再照合する。shellに挿入する変数は検査済みhashとnonceだけで、remote応答や環境HOMEをexec pathへ展開しない。
4. preparedのbackend/revision/nonce/UID/helper hashとsocketを照合する。socketは同nonceの `.eidolon-linux/s-<nonce>/p` に限り、絶対path、103bytes以下、空成分/`.`/`..`/制御文字なし。Termux用pathは拒否する。
5. toolsの全hashと固定versionを確認する。local Routerをarmし、StreamLocal登録ACKを受けてからpatternをstartする。登録要求済みと登録成功を別に持ち、ACK timeout後も取消を試みる。
6. bounded JSON行をphase順に検査する。行16KiB、helper stdout合計256KiB、stderr合計128KiB、起動/検査は各10秒、patternは30秒。heartbeatは10秒間隔、停止待ちは8秒。stderr本文を保持しない。
7. finished receiptだけでは成功にしない。child identity、固定100 frame/callback/release、子exit 0、group終了、pipe回収に加え、SSH EOF・exit 0・channel終了を確認する。正常時は転送drainを最大2秒待ち、双方向の実byte転送も必要とする。
8. remote停止を先に行い、forward取消、Router task終了、channel回収を確認する。全ownerを回収できたときだけ自分のjournalを消す。取消は通常成功と区別し、固定pattern成功のboolを立てない。

## Linux専用journal

`<private files>/linux-probe-state/pending.json` 一件に限定する。directory0700/file0600、same UID、nofollow、regular file、link数1、4096bytes以内。schema/backend/profile binding/revision/session/UID/nonce/helper hash/bundle hash/Waypipe hash/fixture hashを保存する。

新しいtmpにwrite/fsyncし、exclusive hardlinkでpublish、tmp除去、directory fsync、読戻しを行う。既存pendingは、別profileの場合も同profileの場合も置換しない。clearはcaptureしたbytesとdevice/inodeが全て一致する場合だけ行う。途中で書換えられた記録を削除しない。旧 `transport-state/transport-pending.json` をLinux cleanupへ渡さない。

process crashやACK不明で残ったpendingは自動reconcileしない。通常の次回startは `LINUX_CLEANUP_PENDING` として停止する。別API `recover(client, Recovery, cancel)` は、明示指定された同じbinding/revision/UID/pins/nonceだけにreceipt照会を許可する。origin sessionを保持し、新しいSSH sessionへ接続してもscopeを付け替えない。別scope・壊れた記録ではremote execを行わない。

固定cleanup bootstrapは保存PIDへsignalしない。helperがsession不在と同nonceの回収receiptを返し、receipt内部のidentity/group/pipe/child回収、SSH EOF/exit 0、channel終了を確認した後、captureしたinode/bytesのjournalだけを消す。receipt欠落、active/pending、返答型不正、期限切れ、cancelでは記録を保持する。journalが元から無い場合は照会せず `pending=false, queried=false, receipt_verified=false` を返し、remote回収を検証したとは扱わない。APIはこの段階ではController/runtimeから未接続。

## 検証の範囲

host testは以下を分離する。

- 純粋な型/phase検査: pin/UID/nonce/revision/socketの取り違え、起動順違い、重複key、100フレーム以外、receiptだけの早期成功、EOF/exit不足、取消後の成功誤認を拒否する。
- file system検査: 別profileのpendingを保持、旧Termux記録を保持、途中で変わったownerを削除しない、権限異常を拒否する。
- actual SSH + Linux helper: prepare→stop→EOF/exit→session消失/receiptを確認する。patternを実行していないのでpattern成功とはしない。
- 合成SSH/StreamLocal lifecycle: private test injectionで固定childとtoolsを使い、64KiBの双方向転送、通常終了、転送中取消、誤pin時に転送ゼロ、既存pending時にexecゼロ、forward/task/channel/journal回収を確認する。このchildのpattern counterは合成値であり、SHM描画の証拠ではない。

実SHM100フレームとpixel hashの検証はB3aの独立host wireにあり、今回のnative owner経由の実SHM受信は後続B4で既存Pattern AppSessionに接続して確認する。さらにSDK/GPU表示と物理HMDの受入れは別gateとする。transportの `pattern_verified=true` だけでGUIや実描画を合格にしない。

検査は既存host targetを共有し、共通flockを取得して自tree workspace entrypointのmtimeを更新、Cargo実行、固定test名/件数照合まで同じlockで行う。他treeの新しいbinaryを誤って採用しない。新targetの複製や共有cache全削除は行わない。

2026-11-08のcheckpoint検証: 新規11件PASS、既存を含むtransport全48件PASS、feature付きall-targets clippy `-D warnings` PASS、featureなしlibrary check PASS。bootstrapは実行せずPython ASTとしてもparseする。実account HOMEでのbootstrap・APK native owner経由のSHMはまだ未検証である。

同日の明示回収追加後は新規分13件PASS、feature付きclippy PASS。SSHでのreceipt照会、別binding/revision/UID/nonce/各pinでexecゼロ、欠落receipt保持、origin session保持、同scopeの回収後だけjournal消失を追加確認した。fixtureのreceiptは固定合成値で、PIDを発見してsignalする検査ではない。
