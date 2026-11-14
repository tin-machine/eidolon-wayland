# PR #16: Stop後の回収とI4実SDK証拠の接続（2026-11-14）

調査基準は `39dee61588e1d171646c318757e2689796e2760d`。この記録は、その後の実装差分とpush前のローカル検証を扱う。過去HEADのCI成功をこの差分へ転用しない。新HEADのGitHub Actions結果は [PR #16](https://github.com/tin-machine/eidolon-wayland/pull/16) の本文で追跡する。

**コードの接続、ホストでの検証、Questでの受け入れを区別する。PRはDraftを維持し、マージしない。** I5の設定保存・ランチャー・cold restartへの回収authority移送はこの差分の対象外。

## 1. admission封鎖と回収観測

`Session::refresh_stats` はclosing中も、保持している同ownerのProtocolStateから完全なwindow censusを取得する。`WindowHistory::observe_retirements` はseal後に既存WindowIdのdestroy／消失を記録する。新規採用、role変更、destroy済みIDの復活は認めない。ProtocolState不在、foreign origin、重複sampleを空censusへ読み替えない。

`Session::start_v3` はpartial setupの前にContext・I/O generation・NativeOriginを保持する。listener、protocol、Waypipe、ディレクトリの途中失敗も、観測したlocal closeまで同じSessionに残す。mainの共有SHM poolが生存していても、独立したdialogの回収は進められる。origin全体の完了にはlocal子プロセス、client/object/surface/pixel/pool、memfd/map、listener/display/protocol/directoryの実回収を要求する。

B1がtimeout・startup中のlifecycle喪失・panel failureを`Ok(Stopping/Pending)`で返した場合も、native admission、SDK admission、remote ownerの停止へ接続する。core unwind後の共通drainでもSession dispatch、renderer fence、SDK receipt、I2 finalizerを並行して進め、最後のB1 snapshotを更新する。

## 2. native Createと実SDKの同一性

v3のpanel planにだけ`v3_evidence_required`を付ける。nativeはCreateをAndroidへ公開する前に、full TargetKeyと元Create commandを回収台帳へ予約する。SDK側はimmutable leaseを予約し、同期native registrationが受理されてからSDK副作用を開始する。

leaseのidentityはruntime、session、window、Scene epoch、surface generation、元Create command、adapterである。SDK側で現在のruntime／Sceneを検索し直して、古いcallbackを新ownerへ付け替えない。

`SdkResourceScope` はroot／decoration／controls／menu／badgeのEntity、SDK factory、SceneObject future、UI投稿、InputListener callbackを追跡する。producer／callbackのadmissionとsealは同じledger lockで直列化する。Entity IDはScene間で再利用されても旧Bindingを上書きできないようallocation単位で保持する。

current証拠には実Surfaceのvalidity、実SceneObject、listener、captured Scene、登録済みleaseを要求する。Create ACKはenqueueを証拠にせず、実際にnative panelへ適用された同じCreateだけをI4へ渡す。renderer証拠も同full TargetKey、Windows role、現在のcontent frame、非pauseを要求する。

入力は既定拒否を維持し、これらが揃ったtargetだけを許可する。mainのReadyをdialog全体へ流用しない。入力effect直前にもI2の同session/request、live Started、terminal/error不在と最新SDK／rendererを再照合する。Wayland frame callbackとproducer寸法もnative current Createのfull TargetKeyに限定し、旧Sceneや旧generationの同frameを流用しない。

SceneReadyはnative ingressでidentityを確定する。SceneClosingと`expected_scene`付きlifecycle更新は、旧Activity／旧Sceneの遅いFocus・visibility callbackが新Sceneを更新しないよう検査する。epoch 0はnative lifecycleを更新しない。

## 3. 全世代retirementからWindowHistory／B1 GCへ

native `Owner` は未完了Createを全世代分保持し、完了世代はcounterへ圧縮する。未完了義務をring evictionで捨てない。同じwindowが128世代交代する回帰でも、完了済み世代が上限を消費し続けない。

SDKの完了条件は次の実観測である。

1. 対象leaseのsealと、受理済みproducer／callbackのdrain。
2. 実Entity.destroy要求、owning DataModelのdelete通知、その後の同じSceneのlateExecute。
3. listener／Surface／補助リソースの解放。seal後にfutureが遅く成功した場合は、そのresultのcleanup後に新たな同Scene passを待つ。
4. 同leaseのretired receiptをnativeへ送り、exact renderer fenceが確認された後のACCEPTEDを受け取る。

native releaseをSDKのreadyToReleaseの前提にしないため、相互待ちは作らない。StopのHost worker待機中も、AdapterのUI Handler poll、SDK lateExecute、同期JNI evidenceは独立して進む。

renderer fenceは同Controllerの所有権に束縛し、targetのGPU／platform window参照、windowのlast image／player cache等が解放された後に完了する。Stopがqueueを破棄した場合は、同renderer ownerの実終了と全所有参照解放を待つ。

完了targetのmarkerはnative Destroy planを閉じるまで保持する。過去のDestroy ACK failureがあっても、実retirement proofで回収済みと確定したplanは終了できる。通常ACKの成功条件は緩めない。

同windowのnative protocol retirement、全target世代、window renderer fenceを確認したprivate `WindowRetirementProof`だけが、WindowHistoryとB1 role履歴をGCする。mainの固定identityはoriginの回収完了まで保持する。

## 4. 同captureのI2 finalizer

`OriginRetirementProof`は全local資源・全SDK世代・履歴GC完了後だけ発行する。`ReclamationAuthority::confirm`はContextとgenerationに束縛した`LocalRetirementGate`を要求する。診断JSONやtrue/falseの入力だけで完了能力を作らない。`Journal::clear`はtransport v3内部に限定する。

handoff、local proof、journal clear後のreceiptはcoreのcatch_unwind外に保持する。filesystem処理は専用workerで進める。publicationに失敗した場合は同receiptを再送し、journal clearをやり直さない。remote Complete、timeout、STALE、空snapshotだけではjournalを消さない。

Inspectのみで終わる場合は、実LaunchAdmissionのOPEN状態をatomicにsealし、local permitを一度も許可していない場合だけ`NoLocalAllocation`を発行する。reserved permitのDropや欠落Sessionは代用できない。

Runtimeが操作可能なまま旧runだけを閉じた場合、実local proofを保持していることを条件に明示Recoverを許す。I2が保持する元RunCaptureと新接続の`PreviousIoClosed`をContext／Pins／source hashesで照合したprivate bridgeだけが、元local generationの証拠を新recovery generationへ結び付ける。新Launch／Inspectの旧Owner barrierは維持する。

## 5. ローカル検証

Rust 1.95.0、固定Smithay、固定libxkbcommon／keymap、Waypipe 0.11.3を準備して検証した。以下の件数は重複を含むため加算しない。

| 検証 | 結果・範囲 |
| --- | --- |
| `cargo check --locked --workspace --all-targets` | PASS |
| `cargo fmt --all --check` | PASS |
| `cargo clippy --locked --workspace --all-targets -- -D warnings` | PASS。依存Smithayの既存warningは保持 |
| model unit | 135 PASS |
| renderer unit | 26 PASS |
| core unit（下記socket生成5件をローカル実行時だけfilter） | 149 PASS、0 failed、既存subprocess helper 1 ignored、5 filtered |
| coreの`v3`フィルタ | 40 PASS。実Wayland socketpairでmap／seal後destroy／parent／9→10を含む |
| native SDK入力・回収の統合unit | 1 PASS。全部のcurrent証拠が揃う正例、ACK不足、terminal、別request、旧Scene、content frame欠落、queue満杯中のretirement |
| transportのv3 unit | 44 PASS。no-allocation、live recovery bridge、journal保持を含む |
| transport session unit | 17 PASS。clear後publication失敗と同receipt再送を含む |
| Kotlin純粋JVM | 11 PASS。SDK evidence 9、Activity／Scene ownership 2 |
| Kotlin限定compile | 実main source 80本＋UI／生成stub 7本でPASS。実Meta SDK 0.15.0 core/toolkit/vr/compose AAR、Android API36、Kotlin 2.4.21、JVM target17 |
| docs resolver validation / check-design | PASS。受け入れ判定を変更しない |
| `git diff --check` | PASS |

この実行環境は`socket(AF_UNIX, SOCK_STREAM)`自体をEPERMで拒否する。socketpairは使用できる。core全体を無条件実行した際のnamed socket失敗を成功とは報告しない。ローカルfilterは次の5件に限り、test source、ignore属性、CI suiteは変更していない。

- `compositor::primitives::tests::product_dependencies_sync_fd_mapping_and_both_standalone_keymaps`
- `runtime::tests::native_dependency_command_uses_owned_worker_and_stops_after_reclaim`
- `session::tests::real_waypipe_server_producer_and_owned_compositor_verify_100_frames`
- `session::tests::synchronous_desktop_start_failure_retires_before_allowing_retry`
- `waypipe::tests::fixed_waypipe_client_restarts_and_cancel_reap_owned_groups`

transport全体でも同制限により13件が失敗したため、上表のv3／session実行を切り分けて確認した。完全なHost suiteとGradle／APK／lintは新HEADの既存CIで確認する。限定Kotlin compileでは`ApplicationLauncher`、`ConnectionSettings`、`ControlPanel`を除外し、Compose UI/stateと生成R/BuildConfigをstubにした。Meta SDK型・メソッドはstubにしていない。

### 検証中に見つけた既存clipboard fixtureの競合

切断後の固定10dispatch直後にpool accountingを採取しており、Smithayの別threadによるSHM dropより先に8192 bytesを観測してRESOURCE_LEAKとなることがあった。実client/object/surface/window/pixel/pool count/bytesの回収を最大3秒待つdrainへ変更した。期限到達時は引き続きRESOURCE_LEAKで失敗する。assertやCI条件は緩めていない。修正後、上表のcore149件が通過した。

## 6. 残る受け入れ・回復境界

- Quest上の実SDK delete通知順序、Stop中のlateExecute、Surface／listener寿命、Scene交換、keyboard／pointer／IME／resize／close、v2／diagnostic回帰は未実施。
- 実SSH＋C2＋Waypipe＋Mousepad 0.6.3の通常v3全経路T20は未実施。host unitやSDK signature compileをT20／HMD受け入れの代替にしない。
- Scene終了前に必要なdelete通知／後続passを取得できなかったleaseはpendingを維持する。新Sceneやtimeoutで補完しない。
- Runtime全体のStop後にremote cleanupがUnknownでhandoffが得られない場合、Ownerとjournalを保持してStop pendingが続く。今回のlive recovery bridgeは操作可能なRuntime内の旧runを対象とし、停止済みControllerからの自動再接続や次Runtimeへのauthority移送は実装していない。
- cold restartで旧local ownerの証拠が不明な場合もpendingを維持する。製品受け入れ表を一括PASSへ更新しない。

## 参照

- [I3詳細設計](linux-real-app-v3-i3-detailed-design.md)、[I4詳細設計](linux-real-app-v3-i4-detailed-design.md)、[統合計画](linux-real-app-v3-integration-plan-20261111.md)
- [直前HEADのHost点検記録](ci-pr16-host-review-20261114.md)
- [WindowHistory](../native/core/src/linux_desktop_v3.rs)、[Session](../native/core/src/session.rs)、[Runtime](../native/core/src/runtime.rs)、[回収Owner／finalizer](../native/core/src/v3_retirement.rs)
- [SDK scope／delete fence](../android/app/src/main/java/io/github/tinmachine/eidolon/SdkResourceScope.kt)、[SpatialPanelAdapter](../android/app/src/main/java/io/github/tinmachine/eidolon/SpatialPanelAdapter.kt)
- Meta Spatial SDK 0.15.0: [DataModel delete listener](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_core_datamodel/)、[SystemBaseWithLateExecute](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_core_systembasewithlateexecute/)、[VideoSurfacePanelRegistration](https://developers.meta.com/vr/reference/spatial-sdk/v0.15.0/com_meta_spatial_toolkit_videosurfacepanelregistration/)

DeleteListener APIのexperimental opt-inは実AAR照合に基づき、対応するSDK fenceクラスに局所化している。APIの存在確認は、実機でのcleanup順序の受け入れ証拠ではない。
