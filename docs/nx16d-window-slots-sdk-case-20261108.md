# NX16d: 九枠の固定SDKケース

2026-11-08。[九枠接続](nx16c-nine-window-integration-20261108.md)向けのdebug case `window-slots`、
fixture revisionは`window-slots-v3`。一回の新しいRuntime/foot serverで検査し、通常設定は既存の`SdkCaseEnvironment`で隔離・復元する。
Kotlin compilerとcached SDK 0.15.0/既存debug classによる新caseとdebug入口二ファイルの部分コンパイルは成功した。
既存internal classの参照にはfriend-pathを指定した。独立のread-only reviewでも新しい確定blockerは見つかっていない。
Android全体のcompile/lint/package、Quest実行と受入れはrootの後続gateであり、本checkpointでは未実行。

## 検査内容

1. nonce専用の設定でCyan Orbitを明示選択し、要求/採用/読み込み完了とnative/render revision一致を待つ。
   その後の通常起動はHost APIと既存の認証/helperを通し、foot一窓の中央slotを捕捉する。
2. 追加は固定requestを一件ずつ提出し、enqueue結果、`command_complete`、同requestの`window_launch.matched`、
   同じrequest originのBound leaseを別に確認する。三枚、六枚、九枚では二秒間のframe増加とSDK/poseを記録する。
3. 各窓の順序、実Runtime/session/scene/generation、panel slot/applied、workspace pose ACK、SDK実Transformを照合する。
   九窓すべての丸三ボタン、MODE objectと資源所有数、入力targetがそろうことも確認する。
4. 10枚目はnative commandのenqueue Acceptedと処理結果Busyを分け、500ms後も窓/lease/terminal数が変わらないことを確認する。
5. 左slotの捕捉target/revisionへcloseを一度送り、toplevel、render target、panel command、SDK binding、台帳の回収を待つ。
   回収前に再追加しない。新しいlease/tokenで左slotを再利用し、残る八窓のlease/target/poseを維持する。
6. 残る右窓を通常のfocus APIで選び、合成A/Backspaceのdown/up四件を配送し、pressedゼロを確認する。
   これは文字表示/物理キーボード/かな漢字変換の合格ではない。
7. Runtime停止・再生成で旧handleがStaleとなり、controls/MODEのactive ownerがゼロ、台帳ゼロ、cleanup待ちゼロとなることを確認する。
   さらにwrapperで通常UIへ復帰し、元の製品設定digestを照合する。

通常foot serverのWayland client数は1として検査・記録する。
九つの独立client、任意GUI/dialogの光学的非重複、HMDの可読性/快適さは別の受入れである。
Sceneのrayによる九窓すべてへのクリックを実測したcaseではない。既存の二枚controls/move SDKケースも別に維持する。

## 起動と結果

開始だけを既存の固定debug入口へ渡す。継続操作やstatus取得でActivityを再起動しない。
nonceは新しい32桁hexを毎回使い、同条件の自動retryは行わない。

```sh
nonce=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
adb -s "$EIDOLON_QUEST_SERIAL" shell am start \
  -n io.github.tinmachine.eidolon/.DebugCommandActivity \
  --es case window-slots --es nonce "$nonce"
adb -s "$EIDOLON_QUEST_SERIAL" exec-out run-as io.github.tinmachine.eidolon \
  cat no_backup/diagnostics/latest.json > window-slots-metadata.json
```

検査の成功は`check.case/nonce/fixture_revision`が一致し、`result=pass`だけでなく
`product_settings_untouched=true`、`environment_failure`なしまで待って判定する。
wrapper復元前の中間passだけで受け入れない。全体期限180秒、theme準備15秒、初回起動60秒、追加/回収は各15秒、停止30秒。

`check.enqueue_codes`はJNI enqueue code、`check.completions`はnative処理結果。
`highest_ready_windows`はSDK/pose/resourceまでそろった最大枚数、`expected_count/requested_count`は検査中の段階を示す。
三/六/九枚、10枚目拒否、穴回収、再利用、残窓入力のcompact checkpointを保存する。
本文、terminal出力、部屋画像、全trace、全runtime snapshotをstageごとに累積しない。
case metadataは256KiB・checkpoint12件以内に制限し、既存latest.jsonの1MiB予算を無制限化しない。
32msのsnapshot/SDK照合と診断のコストを含むため、通常利用時の性能保証としてCPU値を読むcaseではない。

## 予算拒否と失敗

既存の同client SHM pool8件/64MiB、総SHM/pixel128MiB等は変更しない。
pool/byte拒否やSDK失敗時は`result=fail`として、そのstage、到達枚数、期待窓、実窓、pool_count/bytes、
pool_rejections、最後の拒否bytes、native error、controls/MODE状態を保存し、wrapperで回収・復元する。
拒否で既存の窓が失われた場合も実窓一覧に残し、「九枠を試した」ことを合格へ読み替えない。
`pool_budget_rejection_observed`はnativeのpool拒否カウンターを見た場合だけtrueにする。

失敗後の同条件再試行は自動化しない。証拠を基に寸法や限定予算案を検討し、新たなnonceで別試行として記録する。
metadata生成自体が失敗した場合にも設定復元へ進む。新しい外部receiver、任意JSON/exec/path入力、追加Surfaceは導入しない。

## 初回実機失敗と待機条件の診断

Build 165のv1は、最初の通常footがMappedへ到達した後、`first`の期限切れで失敗した。
`highest_ready_windows=0`で、三枚・六枚・九枚の受入れには到達していない。通常設定のdigest復元は成功した。
v1は期限の検査をsnapshot取得より先に行っており、そのtickのローカル変数が空のまま失敗したため、
最後のSDK/pose状態を保存できなかった。これをSDK生成の循環待ちやSHM予算拒否と断定しない。

v2は取得済みの最新compact snapshotを一件だけ保持し、新しいsnapshotを取得できた場合は期限判定より先に保存する。
期限切れ・STALE等で新しい観測がない場合にも前回の観測を`failure_snapshot`へ残す。
`failure_observation`のstage、観測時刻、ageは、その証拠がいつの状態かを示す。
`failure_readiness`はglobalと各窓の固定bool・固定reasonを含み、panel作成/ACK、native frame、SDK binding、
丸ボタン、MODE、workspaceの希望pose/ACK、SDK実Transform、入力target、資源所有数を分ける。
最初のfalseだけで後続条件の評価を止めない。希望Transformの一致だけをpose ACKへ読み替えず、既存の入力・Android/SDK gateを維持する。

初期poseの依存方向も静的に確認した。native workspaceは同session/scene/window/slotのcreate panelから
希望poseを作り、panel.applied・SDK binding・pose ACKを要求しない。SDKはその希望poseでEntityを作り、
frameとlistener確認後にpanelをACKし、続いてpose/physical geometryを確認する。入力targetはこの後の条件であり、Entity作成の前提ではない。
core回帰では九枚すべての未ACK panelから有効な初期poseが得られることと、旧sceneのpanelではslotを作れないことを確認した。
通常設定に保存された手動poseは維持する。検証nonceの空の設定に適用する九枠の既定poseへ、通常設定を上書きしない。

Build167のv2を実行中のmetadataでは、希望/適用pose・SDK三ボタンまで成立し、
themeだけが`requested/selection=off`・render.theme_id=nullであることを確認した。
検証用の空の設定はOffが既定なのに、v1/v2はbuiltinを要求せずcyan-orbitの成立だけを待っていた。
v3は上記のsetup stageを加えて原因を修正する。テーマ条件・pose ACK・入力targetの検査は維持する。
製品設定の選択は変更せず、同じwrapperのnonce別設定とdigest復元を使う。
