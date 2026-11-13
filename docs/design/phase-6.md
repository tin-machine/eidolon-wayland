---
id: initial-phases-phase-6
title: Initial phases shutdown recovery and acceptance design
type: detailed-design
design_status: proposed
feature: initial-phases
created: "2026-11-04"
last_reviewed: "2026-11-13"
baseline_commit: "443d5f740053efc0c55914ed4c556118e54d4220"
depends_on: [initial-phases-contracts]
related_sources:
  - native/core/src/session.rs
  - scripts/check-host
---
# Phase 6 — 停止・復旧・検証・配布

本書の時間・回数・予算は**採用する合格基準**であり、現時点の測定結果ではない。B03の診断基盤を各taskで拡張し、L01まで停止処理を先送りしない。

<a id="l01"></a>
## L01 — 正常停止と障害時の回収

変更: `native/core/src/session.rs`、`runtime.rs`、`native/transport/src/session.rs`、`native/render/src/surface.rs`、`NativeSessionHost.kt`。

`disconnect`は製品sessionだけを終了し、runtime/診断UIを残す。`nativeRequestStop`はsession終了後にruntime所有job/threadとregistryまで回収する。どちらも冪等で、受理直後に`Stopping`をsnapshotへ反映する。停止要求の受付からの目標は100 ms、通常の全回収は10秒以内とする。

| 順 | 担当・処理 | deadlineと失敗時 |
| --- | --- | --- |
| 1 | coreが新接続・panel・frameの受付を止め、入力解除、世代無効化 | 通常queueを待たない。100 ms以内の状態変化を試験 |
| 2 | transportがremote launcherへ終了を送り、forward受付を閉じる | 正常経路は3秒。TERM→3秒後KILL→waitの子回収を含め、全体deadlineで残件を報告 |
| 3 | coreがowned Waypipe helperを終了し、Wayland clientを切断、snapshot producerを閉じる | 他のTermux process、sshd、同UIDの無関係processをkillしない |
| 4 | GL ownerが描画を止め、texture→EGLSurface→NativeWindow参照の順に解放 | 2秒で応答監視。同期GLを別threadから強制解放しない |
| 5 | Kotlinがdetach済みtargetのlistenerとEntityを破棄し、適用ack | 2秒。既にscene消滅なら旧epochへのcommandを無害化してack |
| 6 | transportが残channelとSSHを閉じ、coreがowned socket/runtime directoryを回収 | 同一nonceの所有記録・path検査を通ったものだけ削除 |
| 7 | runtime停止時のみ専用ownerをjoin、共用GL executorのjob回収ackを確認、停止snapshotを保存、registry回収可能にする | UIでjoinしない。全体10秒超で未回収資源を記録 |

手順2〜4は所有関係を保って並行に進められる。正常な`Stopped`は全ownerの完了ack後だけ発行する。SSH断でremoteへ到達できない場合は、launcherのEOF/60秒leaseで回収を待つ。到達できないremote状態を「回収済み」と書かない。次回接続時に自分のnonceとlease記録を照合する。

GLが戻らない場合は`RENDER_STALLED`とし、そのruntimeを再利用しない。操作UIは状況を表示し、アプリ再起動での回復を案内する。stop timeoutを握り潰して新GL threadを繰り返し作る実装は禁止。R03/I05で再現した時点で設計gateへ戻す。

AndroidではSDK 0.15.0の[TID別GPU計測FD保持](../gpu-fd-investigation.md)を避けるため、process内にGL executorを1本だけ保持する。
同時に貸し出すjobは1件で、runtime停止時はtexture・context・Surface・NativeWindow・queue・画像・theme・runtime参照をすべて返す。
返却完了後にだけ`render.is_finished`を真にする。idleはchannel待機で、GL資源を保持せずtimerで起床しない。
`render.workers`はruntimeのGL job数、`render.executor`はprocess共用threadの実数・active_jobs・completed_jobs・TID・故障状態を記録する。
通常停止時はworkers=0/active_jobs=0/threads=1。再生成後も同じTIDを使い、process終了時にthreadも終了する。
これは全threadゼロを装う例外ではなく、SDK/UIと同様にprocess寿命の資源を明示して管理する設計変更である。
panicやstall後はGLが遅れて戻ってもexecutorを故障状態に固定し、自動で代替threadを作らない。
hostの通常backendは専用threadを維持し、共用経路は独立executorを渡すテストで再利用・参照回収・busy・故障を検査する。

短いstop20回帰では、同一GL TIDとjob回収を全20回で検査し、初期化の2回後から18回の同じ切断状態でFD幅2以下を要求する。
FDは所有資源の回収を確認してから一律1秒待ち、切断状態が維持されている時点で総数と種類別の内訳を採取する。
切断直後の値・本来の切断所要時間・初回分もログへ残し、最小FDの瞬間を選んだり待機時間を停止時間から差し引いたりしない。
この短い検査をL04の2時間・100回再接続の代替にはしない。

fake adapterで各ackを欠落・遅延・重複・順序逆転させる。Questでは一枚表示→正常切断を20回実施し、所有リソース数が基準へ戻ることを確認する。

<a id="l02"></a>
## L02 — 障害を分類して新セッションへ復旧

| 入力・障害 | 期待する動作 | 維持するもの |
| --- | --- | --- |
| Universal Menu、FOCUSED→VISIBLE | key/button/captureを解除。再focusまで入力しない | SSH、Wayland、表示可能なSurface |
| Surface loss/Activity再生成 | scene/generation更新、古いcallback破棄、最新snapshotを再attach後に描く | processが生存していれば製品session |
| SSH切断、remote exit、Termux終了 | 旧session停止。再接続ボタンで新SessionId/nonce、アプリを起動し直す | 保存したprofile/配置。旧shell作業内容の保持は保証しない |
| host key不一致/認証失敗 | 自動再試行しない。原因別に表示 | 信頼済みpinを自動上書きしない |
| APK process kill/Quest reboot | 再起動後Disconnected。Termux準備確認後にユーザーが接続 | 暗号化credential・明示保存設定のみ |
| Wi-Fi/ADBだけ切断 | 製品sessionの状態遷移を発生させない | APK→Termuxのloopback接続 |
| corrupt layout/設定schema不明 | 壊れた入力を保存し直さずdefault表示、原因を記録 | credentialを巻き込んで消去しない |

MVPでは無限auto-retryを実装しない。接続ボタンの多重押下は一つのrequestへ集約し、Connecting中は取消だけ可能にする。二回押下、接続中切断、旧sessionの遅延forward、切断後の旧入力をtestする。Termux force-stopやsshd停止による試験は、テスト用sessionに限定し、既存の開発SSH接続へ影響する操作は事前に影響を示す。

合格は障害注入20回で、二重runtime/remote子/forwardが残らず、pin検証を維持して新しいfootを起動できること。ネットワークを切る試験では事前に端末内診断を開始し、ADB再接続後に同一SessionId・期間中のframe/入力counterを回収する。ADB到達不能だけでは製品停止と判定しない。

<a id="l03"></a>
## L03 — sleep・給電・再起動の再現手順

試験開始時にAPK/native build情報、Quest API/OS build、Termux/Waypipe/foot版、接続方式、充電器・ケーブル条件、開始時のbattery status/level/temperatureを記録する。識別情報と部屋の画像はprivate artifactに置く。

1. Universal Menu表示/復帰20回。FOCUSED/表示/入力/SSHを別counterで照合。
2. HMDを外す、sleep/resume、Activity再生成を各10回。OSによるprocess終了の有無を残す。終了した場合はL02の新session復旧を検証し、旧session継続と区別する。
3. 給電＋Wi-Fiで一枚を30分連続使用。60秒ごとにbattery/温度/CPU/RSS/FD/queue/最終frame時刻を採取。
4. Quest reboot後にTermux起動・sshd・ADB・APK接続を手順どおり復旧する。以前のIPやADB待受を決め打ちしない。自動起動未対応なら手動手順を合格条件に明記。

`AC powered=true`だけで充電成功にしない。30分試験の合格はクラッシュ/ANR/資源超過なし、終了時残量が開始時以上、温度による停止なし。満たさない場合は充電条件・表示負荷・残量差を記録し、この運用gateを未合格のまま独立した機能開発は進める。OS全体の電源制限を根拠なく無効化しない。

<a id="l04"></a>
## L04 — 二枚・負荷・100回再接続の受入れ

`native/fixtures/`の既知patternと固定速度更新、foot静止/scrollの三条件を分ける。pixel/入力内容そのものは計測ログへ書かない。

- 二枚1024×768、各30更新/秒のfixtureを2時間。描画不能な時間を含めてframe_seq・drop数・copy/upload bytes・swap時間p50/p95/p99・UI command latencyを集計。
- 100回の正常接続/切断と20回の障害切断。warmup5回を除き、各切断後のowned target/texture/channel/helperは0、Wayland clientは0、SDK診断パネル分だけ残る。
- FDはwarmup後基準＋4以内へ毎回復帰、tracked pixelは契約上限内、各queueは上限内。PSSはwarmup後から最終10回中央値の増分が16 MiB以内。超過時は内訳を調査し、基準を黙って緩めない。
- 表示可能な30 Hz fixtureで更新遅延p95が100 ms以下、入力command→protocol送信p95が50 ms以下を初期目標とする。これはHMDのmotion-to-photon測定ではない。達成しなければcopy/SSH/swapのどこに時間を使ったかを示し、性能gateを未合格とする。
- 片方のSurface喪失/非表示で他方のprotocol/inputが継続することを確認。単一GL ownerのswap待ちが他方の描画を停止させる場合は、per-target GL owner等の設計変更を別taskにし、無条件にthread追加しない。

最後にユーザーがHMDで文字の可読性、入力、二枚のfocus、背景切替を確認する。自動counterとHMDの視認は別欄。capture画像だけで奥行き・可読性・快適性を合格にしない。

<a id="c01"></a>
## C01 — host/Android CI

作成: `.github/workflows/check.yml`、`scripts/check-host`。B02で作った`scripts/check-native-package --apk PATH`をCIでも実行する。B03の時点では存在するcrateだけを対象にし、追加taskでtest suiteを増やす。

- host job: `python3 scripts/check-design`、Rust fmt/clippy/model/coreのhost test、Python launcher test。network/SSH fixtureは専用の一時鍵・専用port・所有processだけを用いる。Questや通常Termuxへ接続しない。
- Android job: Java25、compileSDK36、buildtools35、NDK27.3.13750724、Rust1.95.0/cargo-ndk4.1.2を明示。wrapperからassembleDebug/lint、native ELF/ABI/NEEDED/APK同梱を検証。
- wrapper/distributionとnative archiveのchecksumを検証。actionsは実装時に確認したcommit SHAへ固定する。Gradle/Cargo/native cacheはlock・ABI・NDK・profileをkeyへ入れ、生成済みsoをrepoへcommitしない。
- CIのAPK artifactはdebugだけ、短い保持期間。signing key、SSH secret、端末artifactをuploadしない。Quest testは接続済み開発PCで明示実行し、CIのAndroid成功を実機passに変換しない。

`scripts/check-host`は`--suite model|transport|compositor|input|lifecycle|all`を持つ。suite未実装はnot_run exit 2、空のtest集合をpassと扱わない。C01後は後続taskのhost test追加時にsuite登録も更新する。

<a id="c02"></a>
## C02 — release構成と利用手順

release前にdebug用exported Activity/receiver、ADB pairing入口、固定fixture、詳細入力観測を除外し、merged manifestとAPK zipを検査する。INTERNET等の必要permissionだけを根拠付きで残し、passthrough表示のためにraw camera permissionを追加しない。

開発ADBがなくても初期設定できるよう、T01の鍵形式を保った接続設定UIとTermux用`eidolon-pair`手順を作る。APKは自身の**公開鍵**を表示/コピー可能にする。Termuxのhelperは公開鍵を引数で受け、既存authorized_keysを保持して追加し、sshdの公開host keyとusernameを表示する。ユーザーはAPKに公開host key/usernameを入力してfingerprintを照合し保存する。秘密鍵を表示・clipboard転送しない。既存pinの変更は接続停止中の明示操作とする。入力はbase64鍵形式/長さ/usernameを検証し、shell文字列へ連結しない。QR/deep linkは必須にしない。

`THIRD_PARTY_NOTICES.md`と`native/deps.lock.json`、Cargo.lockを対応させる。Waypipe/依存libraryのLICENSE、必要な配布元・patch・source提供条件を確認し、APK内と配布説明へ含める。署名用keystoreの作成/保管はrepo外とし、秘密値をCIログへ出さない。

debugとreleaseでversion、API34/arm64、Surface/SSH/Waypipe/入力の最低一回の実機smokeを行う。新規profile→二枚→切断→再起動→接続の手順、依存導入、復旧と既知制限を`docs/usage.md`へ書く。release APK作成までが本taskの範囲で、ストア公開・外部配信は別作業。

<a id="checks"></a>
## 検証コマンドとcase契約

実行scriptの実装状況は[進捗記録](../progress.md)を確認する。B02で`build-native`・`check-native-package`とnative版を検査する`quest-smoke`、B03で`quest-check --case runtime`を追加した。その他caseと`check-host`（C01）は対応taskで作成する。作成taskより前には実行せず`not_run`を残す。正確なcommand、作成task、判定対象は[tasks.json](tasks.json)の`checks`が正本。

`quest-check`はB03で作る。引数は`--serial DEVICE --case NAME --timeout SECONDS`。対象同定を既存quest-smokeと共有し、private作業directoryへrun nonce、build SHA、case、開始/終了、結果JSONと連続logcatを保存する。stdoutには秘密や端末識別子のない要約だけを出す。caseは下表の固定enum、未登録/未実装はexit 2。case名から任意shellを組み立てない。

| case | 実装task | 自動観測 / 別途手動で確認 |
| --- | --- | --- |
| runtime | B03 | create/queue/stop/destroy、二重停止、古いhandle拒否 |
| helper | H01 | APK UID exec、echo、終了・reap10回 |
| surface | R01 | callback順序/thread/世代、Menu/Activityの手動操作 |
| pattern | R02 | attach/描画counter、HMDの色・上下左右・背景 |
| surface-cycle | R03 | 世代/資源/停止、sleep等の手動操作 |
| credentials | T01 | 暗号化保存・pin mismatch・公開情報だけのpairing |
| ssh | T02 | 認証/exec/両出力/timeout。PC SSHとは別 |
| forward | T03 | 同時channel、byte一致、EOF、path拒否 |
| remote-cleanup | T04 | lease/子回収/切断、無関係processの存続 |
| native-deps | W01 | Androidロード、host ELF混入なし |
| shm | W02 | APK UID fixture100frame、configure、buffer検査 |
| wayland-state | W03 | map/unmap/remap、subsurface、制限、SIGBUS probe |
| waypipe | W04 | APK helper版/依存/起動・終了 |
| forwarded-shm | W05 | Termux fixture→Waypipe→APKのcommit/pixel digest |
| render | D01 | 既知pixel、alpha/transform/scale、GL readback |
| foot | D02 | foot実client/frame、HMD内の文字と背景 |
| ray | I01 | UV/corner/拒否、HMD ray位置を手動照合 |
| keyboard | I02 | keymap/scanCode/修飾、物理keyboardを手動確認 |
| pointer | I03 | focus/capture/scroll/reset、controller実入力 |
| resize | I04 | configure/ack/commit/timeout/寸法区別 |
| two-windows | I05 | 独立target/入力、HMD二枚と第三window扱い |
| layout | I06 | 配置/背景保存、scene再生成、破損設定 |
| popup-clipboard | I07 | popup/grabとsession内の既知text転送 |
| stop | L01 | 正常/欠落ack停止、FD/所有資源20回 |
| reconnect | L02 | 障害行列、新session、旧event拒否 |
| power-lifecycle | L03 | 給電30分/Menu/sleep/reboot。手動操作あり |
| soak | L04 | 二枚2時間、100回再接続、性能・資源 |

C02のreleaseには上記debug入口を含めない。release smokeは通常UIと外部のprocess/manifest/logcat観測、HMD確認で行い、debugのcaseを偽装して実行しない。

## 結果と試行錯誤の記録

実装者は`docs/progress.md`へtask ID、source commit/content digest、check ID、結果、private artifact相対path、次のactionを記す。公開可能な再利用知見はrepo文書/適切なHugo記事へ、homecluster固有の非公開知見はrunbookへ残す。rawログは毎回設定を読み直した作業rootのtask directory、0700/0600を基本とする。

結果値は`pass / fail / not_run / needs_design`。task statusは`planned / in_progress / blocked / done`。必要なcheckがすべてpassになるまでdoneにしない。HMD確認未実施なら自動部分passでも該当check全体はnot_runとし、内訳を残す。

```json
{"schema":1,"task":"R02","check":"quest-pattern","result":"not_run",
 "source_commit":"FULL_SHA","source_digest":"SHA256","dirty":false,
 "apk_version":3,"native_build_matches":true,"run_nonce":"UNIQUE",
 "automatic":{"result":"pass","artifact":"runs/RUN/pattern.json"},
 "manual":{"result":"not_run","reason":"HMDでの上下左右を未確認"},
 "hypothesis":"","change":"","next_action":"HMDで四隅と背景を確認"}
```

失敗は実行command（秘密を除去）、exit/timeout、期待値と実測、仮説、修正、再試験の順で残す。状態遷移/回数だけでは判別できないcrashはtombstoneと同一soのsymbolをprivate artifactへ保存する。入力文字・clipboard本文・SSH秘密鍵・普段の画面を通常診断へ記録しない。synthetic fixtureの期待pixel digestだけはtest evidenceに使ってよい。

設計チェックは参照と依存関係を検査する。host testはmodel/protocolの性質、Android buildはABI/リンク、Quest testはOS/SDK/実行制約、HMD確認は表示・物理入力を判定する。この四層を一つの「テスト成功」にまとめない。
