# 通常グループメニューと複数窓の正面復帰

PR #2の修正版を基点に、保存済みのグループ操作メニューをレビューして統合した。
「まとめる」「展開する」「前面を切替」は捕捉したtarget・group revision・入力intentへ束縛し、
メニュー資源を回収した後に一度だけ送信する。nativeの受付と500msの実geometry完了は別判定にする。
サイズ変更と正面復帰の既存操作は維持する。

正面復帰は全Mapped窓のfull target・SDK適用済みpose/物理サイズ・pixel寸法を捕捉する。
一度のviewer取得から最大9個の近傍奥行き候補を比較し、他の窓のbody boxと交差しない候補を選ぶ。
選択前後およびHost送信直前に全窓の捕捉を再照合し、窓追加・世代交換・移動・サイズ変更・未適用geometryなら取消する。
候補がなければ移動しない。他窓の位置を変更せず、viewerの再取得や自動再送も行わない。
グループoverlayの実geometryと永続base poseは異なるため、groupが存在する間の正面復帰は拒否する。

これは同一平面の衝突を避ける候補選択であり、全窓が視界で重ならず見える配置を保証するものではない。
送信後に生成される新しい窓の配置まで固定しない。最終のSDK適用確認・入力復帰は既存captureを通す。
室内座標やviewer姿勢は診断へ追加しない。

## 検証

Androidの実SDK classpathでcompileDebugKotlinとtestDebugUnitTestを実行する。
新たな回帰は、二枚の適用済み窓での候補変更、他窓不変、世代・pose・サイズ・窓追加・group発生での捕捉失効。
既存のgroup menu capture/wire、recenter候補・期限・入力復帰・window menuの検査も維持する。

実機の受入れ結果は下記へ記録した。HMD視認、物理メニュー操作、複数窓recenterの実geometry、
通常メニュー経由の取消・reduced motion・Close等の残る組合せは別の受入れに残す。
hostテストやcommand ACKだけで実機成功としない。

## 実機で見つかったgroup候補の拒否

初回の単一窓recenterはSDK・回収・通常復帰まで成功したが、二枚のgroupケースはcollapseでNOT_READYとなった。
記録を追加して確認すると、実行中foot自身のdurable journal entryを、候補抽出が未回収の別ownerとして拒否していた。
`remote.cleanup_pending`の真偽だけでなく、`forward.pending_cleanup`にも稼働中の自分の回収責務が残る。

修正ではsession/nonce/UIDと子のpid/group/startが認証済みの稼働ownerに一致するentryだけを許容する。
別owner・子identityの不一致・不明child・終端stateは引き続き拒否する。journalの削除や回収済み化はしない。
host fixtureにも実機と同じ稼働ownerのjournalを持たせ、矛盾したjournalを拒否する負例を追加した。
初回の失敗と修正後の受入れは別のnonceで記録する。マージ判定は修正後のCIと実機結果を確認して行う。

## 修正後の検証とマージ（2026-11-09）

`ae0129c6a1963b620757f706acb9b144a7f86cba` の[CI run 42470921028](https://github.com/tin-machine/eidolon-wayland/actions/runs/42470921028)で
AndroidのAPK/native/lint/同梱・拒否検査が成功した。host初回はGTK fixtureのXvfb起動で
`XVFB_DISPLAYFD_TIMEOUT`（stderr長0、probe開始前）となったが、host jobだけの再実行で
suite全体とWaypipe clipboard/IME検査まで成功した。初回失敗も保持し、無条件の成功として扱わない。
ローカルの実SDK compile/JUnitは356件、Rust group/owner回帰は28件が成功している。

同runのAPKを既存debug署名と照合してQuestへ更新し、source digestを照合した。
固定groupケースは二枚のfootでcollapse→途中geometry→前面切替→expand→全回収までpass。
背面描画時計の停止、前面の進行、保持中Shiftの解除、元poseへの展開、SDK/Board/操作部の回収を確認した。
同じ修正版APKで固定recenterケースもpassし、SDK実ray/menu hit、一度のpose要求、ACKと適用pose、
寸法不変、入力復帰、窓とmenu/操作部の回収を確認した。両ケースとも製品設定不変・通常復帰を確認している。

groupケースは固定native操作であり、通常groupメニューのコントローラー操作の代替ではない。
recenter実機ケースは単一窓。複数窓は候補モデルと全窓scene捕捉の回帰で検査し、実機geometryは未確認とする。
HMD・物理入力の成功も自動認定していない。本文なしの結果をここに記録し、室内座標を含むraw記録は非公開の作業領域へ保持する。

PR #2は `18fe49e72caa58b6f74dda1052dda3986da18783`、PR #3は
`3bcd6cfb44dd21e8c9979a106f93434b52fa396c` としてmainへマージ済み。
