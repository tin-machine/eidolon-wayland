# 実footからパネルへの接続と更新通知（D02）

2026-11-05、Quest 3/API34で、APKの認証済みSSH接続からTermuxのfootを起動し、
Waypipe→製品Wayland compositor→所有済みRGBA→Smithay/GLES→ImageReaderまでを検証した。
ImageReaderの検査は`scope=native-instrumentation`、`synthetic_visibility=true`である。
SDK Entityの表示、現実背景との同時表示、HMDの可読性、物理入力の受入とは区別する。

## 通常経路

通常UIの「ターミナルを開く」は、登録済み`termux-local`への接続と、
固定`foot`の`start_app`を順に行う。認証専用の`connect`は従来のまま残す。
連打は一つの開始操作へまとめ、同じrequestの完了を確認してから次へ進む。
接続・起動失敗は表示し、自動再接続はしない。既存profileやshell設定は変更しない。

`app_probe`はW05用のvirtual callback clock、`start_app`は通常のtarget-driven clockである。
一つのDisplayで二つのclockを混ぜない。通常経路では実際に表示した同じ`content_frame`、
Activity/XRの可視性、描画予算を確認したときだけ`wl_surface.frame`へdoneを返す。
描画先がまだ無いとき、非表示、unmap、停止時に架空の更新通知でclientを進めない。

Rendererの`diagnostic`/`windows`と各targetのroleは明示値である。
SessionIdの数値から用途を推測しない。windows modeで画像が無いときは四色へfallbackしない。
旧diagnostic targetの世代も保存し、通常パネルで同じtupleを再利用しない。
論理windowの履歴はunmap中も維持し、destroy後に回収する。

## SDKへの希望操作と適用完了

Coreの`runtime.panels`は、最大16件の再送可能なcommand表である。
各行は`command_seq/action/target/applied/error`を持つ。targetには
SessionId/WindowId/SceneEpoch/SurfaceGenerationの全tupleを含める。
同じwindowの旧Entityはdestroy ackまで残し、新世代の作成を先行させない。

SDK adapterはUI threadで表を照合し、1024×768、1.2×0.9m、位置(0,1.4,+1.5m)で一枚を作る。
実window用の`nativeAttachWindowSurface`へSurfaceを渡し、最新snapshotを描く。
native側は希望tuple・mapped状態・Scene・owned snapshotを検査する。
GLで最初のframeが描画されてから`adapter_applied`を送り、希望値と適用済みを区別する。

unmap/destroy/Scene変更ではパネルを隠し、入力を解除し、native detach ackを待ってEntityを破棄する。
Surface replacementも旧世代を破棄して新世代を要求する。
遅延ackは旧command_seqとtupleの両方で拒否する。
SDK操作失敗は`adapter_failed`として保持し、同じwindow/Sceneで自動作成を繰り返さない。
Scene変更またはunmap後の新しいmapで再評価する。

## 自動検査と再現

給電・ADB接続・[Termux tools](termux-wayland-tools.md)・APK専用鍵のpairingが前提である。
現在sourceとAPKのdigest、launcherのSHA、APK内WaypipeのSHAを照合する。

```bash
scripts/build-android 63
scripts/quest-install-launcher --serial QUEST_SERIAL
scripts/build-wayland-fixture
scripts/quest-install-shm-fixture --serial QUEST_SERIAL
adb -s QUEST_SERIAL install -r /absolute/path/to/eidolon-debug.apk
scripts/quest-check --serial QUEST_SERIAL --case foot --instrumentation
scripts/quest-check --serial QUEST_SERIAL --case foot
```

`foot_fixture`はdebug core/transportだけが起動できる固定modeで、foot内のPythonが
既知の行を50ms間隔で出力する。制御channelから任意shell/文字列は受け付けない。
ユーザーのfoot.iniは変更しない。通常`foot`は従来どおり固定bashを起動する。

検査は次の条件を要求する。

1. 実APK/Termuxの別UIDと一回の認証、Waypipeとfootの版照合、実SHM受信。
2. 描画先なしでcallback数0、1024×768のSurfaceで実画素の非単色・複数hash・継続更新。
3. 非表示中400msのcallback停止と表示再開後の更新。
4. 旧Surfaceの解放後、同じlogical windowへ800×600の新Surfaceをattach。
   これは描画先の再生成であり、Wayland logical resizeの成功ではない。
5. 実行中Stopの受付100ms未満、全停止10秒未満、GPU/SHM/子process/SSH/remote lease回収。
   さらに新Runtimeの作成・停止・registry回収を共通instrumentation検査で確認する。
6. 固定50ms/行の出力を4秒以上観察し、初期画面を超える行数と、画面下1/4の非単色・複数hashを照合する。

ImageReaderでは4pixel間隔でRGBAをサンプルし、SHA256と非単色数だけを記録する。
通常terminalの内容を出力しない。GPUのpresent seqとWayland snapshot seqも照合する。
PC側はnonce/版/UID、callback clock、SDK ack、世代、画素変化、停止証跡が欠けた成功を拒否する。

`--instrumentation`なしの`foot`は通常Host/SDKを使う別検査である。
可視性を偽装せず、実際のActivity可視・SDK VISIBLE/FOCUSED、footのpresent、SDK ack、
Entity再生成と旧Entity破棄、native停止後の新Runtimeと診断パネル復帰を照合する。
自動合格でも`hmd_verified=false`を維持する。

## 試行記録

- Build58: `20261105T123148-7fd0b240`がnative scopeでpass。
  初回Surfaceでcopies14/callbacks6/content_frame6/異なる画素hash4、
  再生成後でcopies23/callbacks15/content_frame15/異なる画素hash12。
  非表示中callback7→7、復帰後10。Stop受付0ms/全149ms、consumer thread回収成功。
- Build58はSDK希望command表の導入前の接続検証である。
  その後、SDK希望値と適用済みを分離し、旧diagnostic世代との衝突、
  unmap/remapでの履歴消失、SDK失敗後の無限再作成を防ぐ検査を追加した。
- Build59: native foot `20261105T124310-b04d4f76`、D01 readback回帰
  `20261105T124319-476c5ae5`がpass。Core指定generation1→3とSDK適用ackを確認。
  非表示中callback8→8、復帰後11、Stop受付0ms/全187ms。
- Build60: native foot `20261105T124652-2f849202`、readback回帰
  `20261105T124703-f2dfe0da`がpass。native footはgeneration1→3、画素hash5→15、
  非表示中callback9→9、復帰後12、Stop受付1ms/全179ms。
- Build60通常SDK `20261105T124709-073a63ed`が`scope=runtime-and-ui`でpass。
  実Activity可視/SDK FOCUSED、同じlogical windowのgeneration1→3、
  content_frame1→2、callback1→2、SDK作成数1→2・破棄数0→1を確認。
  旧Runtimeの破棄189ms、新Runtime/診断パネル/通常UIへの復帰が成功した。
  HMDの可読性・物理入力はこの結果に含めない。
- Build60のW05通常UI回帰`20261105T124911-d0a431df`はPC判定でfail。
  転送/hash/callback/releaseは各100、producer正常終了とremote回収は成功していたが、
  Kotlinがapp側`state=running`の行を保存した。transportのlive snapshotが先にpassへ進み、
  core所有app reportが次tickでpassへ追い付くまでの競合だった。
  `ForwardedShmCase`の待ち条件へapp側passも加え、PCの厳格な完了判定を維持した。
  この不完全な成功行を拒否する回帰fixtureも追加した。失敗後の通常RuntimeはReady/FOCUSED、
  pending cleanupは0で、権限追加やjournal削除は行っていない。
- Build61のW05通常UI再検査は`20261105T125343-932dd327`でpass。
  続くnative-depsの通常UI検査`20261105T125353-b7aa5be6`は、FD回収auditでfailした。
  sync/delete_id、SCM_RIGHTS、mmap、keymap、socket回収は成功し、残ったのは
  `owned_fds_closed=false`だった。旧auditの`raw/dev/inode`では異なるepollを区別できない。
  hostでepollを閉じて開き直すと同じraw/dev/inodeになることを再現した。
  SDKと同じプロセスでは、この番号再利用を解放漏れと誤認する可能性がある。
- 修正したauditは、自分のepollだけへ非readyのeventfdをwatchし、
  `/proc/self/fdinfo`のtfd/dataから、そのwatchを保持するepollが残っていないか確認する。
  元の番号だけでなくdupされたepollも検出し、他ownerの新しいepollと区別する。
  eventfdはaudit終了まで保持し、最後にRAIIで閉じる。通常socket/memfdのidentity検査も維持する。
  hostでは、旧epollのdupが残る間はfail、dup解放後は別epollが生きていてもpassする検査に成功した。
  fdinfoの形式は[Linux kernelのproc文書](https://www.kernel.org/doc/html/v6.8/filesystems/proc.html#proc-pid-fdinfo-fd-information-about-opened-file)による。

Build62では全ての次のgateがpassした。上記FD audit修正後の通常SDK共存検査である。

| case | run（2026-11-05） | scope |
| --- | --- | --- |
| native-deps 10回 | 130310-f959ebb8 | runtime-and-ui |
| forwarded-shm 100frame+foot | 130318-aa8fb21f | runtime-and-ui |
| shm 9case | 130325-8e1010de | runtime-and-ui |
| shm-truncate | 130333-e0e97eec | runtime-and-ui |
| wayland-state 10case | 130339-c30729ee | runtime-and-ui |
| waypipe 3case | 130345-bd61c0e2 | runtime-and-ui |
| remote-cleanup 7case | 130354-13dd2b2e | runtime-and-ui |
| foot/ImageReader | 130504-3e94c7e1 | native-instrumentation |
| render/全36stage+pattern | 130513-6baf276b | native-instrumentation |
| foot/SDK再生成・UI復帰 | 130520-51674ff0 | runtime-and-ui |

T04/W01〜W05/D01の残っていた通常UI共通gateを満たしたため、これらをdoneとした。
D02のHMD視認は最終確認へ残し、Phase5全体の完了とはしない。
Build63ではdebug FootCaseに4秒以上の連続出力と画面下部のhash検査を追加し、次も全てpassした。

| case | run（2026-11-05） | 結果 |
| --- | --- | --- |
| native-deps/通常SDK共存10回 | 130949-213456e9 | pass |
| forwarded-shm/通常SDK共存 | 130958-88cdcba8 | pass |
| foot/ImageReader/連続出力 | 131006-27ff022d | 4,999ms観察、画面下部hash20、非単色22画像、callback30、Stop受付0ms/全164ms |
| render/全36stage+pattern | 131019-0fde07bf | pass |
| foot/SDK再生成・UI復帰 | 131026-2d655167 | pass、旧Runtime破棄198ms |

最終APKはBuild63、SHA256 `a0e107f18c90bc75d5d1c80d670621bad57cbdf417e90e2fad8f2bb5ce3d16f5`、
source digest `2d2b4ebed9c10e02b860ba42a1ccdeb6dcadf45a9f343b35e00bcfd3a53e1f16`。
Rust75test/Clippy/fmt、Python実APK40test、launcher16test、debug/release/lint/package検査が成功した。
全検査後のTermuxにfoot/Waypipeは残らず、作業前からのshellとsshdは保持された。
- 最初のfmt操作は存在しない`native/Cargo.toml`を指定して失敗した。
  workspace rootの`Cargo.toml`へ修正し、以後は`set -e`付きでfmt/Clippy/testを通す。

再開に必要な生ログとrunのsnapshotは、作業rootの
`eidolon-phase5-implementation/`配下に保存する。正式な受入範囲と失敗理由はこの文書へ追記する。
