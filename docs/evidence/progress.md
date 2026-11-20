# 製品実装の進捗

## 通常IME・正面配置と中断点（2026-11-08、Build176）

debug/releaseのbuild・lint・package、JVM294/196件が成功した。
通常入口のIME ON/OFFと直接入力への復帰、単独窓の正面配置・入力復帰・資源回収は実機自動検証で成功した。
グループ化caseは初期段階の`GROUP_OWNER_CHANGED`で失敗し、個別の失効原因は未確定。設定と通常runtimeの復帰は確認した。
Freedoomは既存項目を保持して登録済みで、通常ゲームの起動・操作はまだ検証していない。

ユーザーの相談のため、開発を中断する。未統合のgroupメニュー・Linux GUI・複数窓recenter準備は作業ブランチへ保存する。
検証結果・失敗・保存範囲・再開順は[Build176の中断記録](integration/build-176-integration-20261108.md)を参照する。
NX01〜18と、既存Phase5/6の未受入れは残る。物理Ctrl+Space・かな漢字変換・HMDは未確認。

## 通常GUIの配線と視点姿勢（2026-11-08、Build175）

固定Freedoom用GUI worker/cookie/receiptとgroupのnative入力境界を統合し、debug/releaseのbuild・lint・packageと
JVM265/175件が成功した。新helperで通常footを起動でき、公開SDKのviewer poseを20回読んで有効な正面候補を計算できた。
通常IME検査のactive情報に参照違いを見つけ、修正を分離した。通常入口の成功とは扱わない。
catalog登録にもTermuxのAPI差があり、非上書き条件を保つ別APIを実測した。
詳細と未完了範囲は[Build175記録](integration/build-175-integration-20261108.md)。

## 拡張の統合検証（2026-11-08、Build174）

通常footから明示的にIMEへ切り替える入口を共通controllerへ接続し、debug/releaseのbuild・lint・package検査、
JVM234/159件が成功した。共通検査経路でIMEのBOUNDと終了後の元targetへの直接入力復帰を確認した。
通常入口特有の初期owner経路、実かな漢字変換、物理Ctrl+Spaceは後続の受入れに残す。

Linux GTKの文字・削除・buttonと追加dialog、Termux GTKのinput/dialog/800×500→640×360 resizeが成功した。
全target退役、所有process/journal回収、通常SDK復帰も確認した。
resizeのPC観測形式誤りと検証wrapperの誤判定も含め、[Build174記録](integration/build-174-integration-20261108.md)に残した。

窓別の移動・サイズ操作と最大9枚はBuild171で自動検証済み。
Freedoomの固定短demoはBuild172でSHM74commit・SDK更新・自然exit0・資源回収まで成功した。
通常GUIランチャー接続、グループ化のruntime/SDK/操作入口、別Linuxホストの通常GUI接続を継続中で、
それらの実装や物理受入れを完了扱いにはしない。
現在の拡張goalは[NX01〜18計画](../features/termux/next-phase-termux-startup-linux-gui-plan-20261107.md)であり、既存Phase5/6の受入れ条件も保持する。

## 手動確認後の修正（2026-11-07、Build142）

ユーザーの追加指定でリング線幅を6→30、切れ端を平らにした。中央のRSS回転は上限64MiBへ調整し、
小さいshellでも動きが分かるようにした。ランチャーを床上1.05mへ上げ、通常の左下側へ寄せた。
物理キーがActivityではなくSpatialの別Windowへ届いていたため、主操作・ランチャー双方の入口を接続した。
診断では文字を保存せず、入口・物理判定・enqueueの件数を分ける。

Build142をQuestへ導入。theme22件、debug build/lint/JUnit/APK検査、通常SDK keyboard8段階・theme-sdkが成功した。
両パネルへのADBキー到達と仮想device拒否、ランチャーからのfoot起動を確認した。
ユーザーが更新後の文字入力成功と、円の太さ・中央の回転の見やすさを確認した。この線幅と速度設定を採用する。
物理判定46件・enqueue46件・実配送46件も一致した。ランチャーはピンク色の「>_ ターミナル」と案内した。
詳細と残る個別受入れは[NX修正記録](integration/extensions-nx01-nx08-20261107.md)。

## NX01〜NX08の実装（2026-11-07、Build141）

ユーザーが指定した直前一覧1〜5をNX01〜08へ対応させ、実装と自動検証を進めた。
線幅3倍、CPU/RSS別の回転、入力停止理由と窓別focus、固定主操作、電池/RAM/IP、
Termux必要時起動、左下のfootランチャー、日本語SKKの専用入口を追加した。
詳細と失敗を含む再現記録は[NX01〜08](integration/extensions-nx01-nx08-20261107.md)。

- 通常APKの実UIDからTermux helper起動、保存済みpin/鍵で再接続、foot起動が成功。
- 編集終了からのfocus復帰、二窓切替、解除後の再選択、連打抑制、device metrics、元設定への復帰が成功。
- 通常SDKのテーマ・二窓・性能回帰が成功。負荷を観測しないときは回転を停止する新しい契約も検査した。
- 日本語はPCとQuestの専用PTYで変換・候補・確定・取消・英数復帰が成功。footでの候補視認と物理入力は最後に確認する。
- 権限拒否、連携設定無効、取消後の遅い結果を確認。途中の描画待ち・focus準備・SDK初期化の競合を修正した。

既存Phase 5の未受入れ、Phase 6の長時間・signed release検証、NX09以降の実装は別に残す。


## 最新のFD対策検証（2026-11-07、Build133）

AndroidのGL threadをprocess内で1本再利用し、runtime停止時にはjobの全資源と参照を返す方式へ変更した。
SDK 0.15.0のTID別GPU計測cacheを直接変更するものではない。停止契約は[Phase 6設計](../design/phase-6.md#l01)、
失敗を含む切り分けは[GPU FD調査](experiments/gpu-fd-investigation.md)に記録した。

- 通常SDKのstop20回はpass。同一GL TID・GPU計測FD33個を維持。初期化2回後の全FDは304〜306で幅2以下。
- 切断後1秒の固定待機を設け、即時値も保持。変動はsync_fileの58〜60だけで、他のFD分類は一定だった。
- 二窓表示とテーマの通常SDK回帰は各5cycles pass。popup/clipboardはnative instrumentationの2cycles pass。
- Rust160件、Termux33件、実APK指定Python77件が成功。fmt/clippy、debug/release assemble・lint・JUnit・APK検査も成功。
- Build132の即時測定は幅3でfailed。Termux未起動・古いPIDの診断・Wi-Fi ADB途絶も、復旧手順とともに記録した。

debug APK SHA256は`82aaea01870719451c96d9744b31f848ef766844266e8a8923d196515a4e8c2f`、
sourceはlocal checkpoint `ae16af0` + dirty、digestは`702662e47df96c1fb92ab6c86740b7d1bc9783591cc948e4f893dd386d6307b5`。
artifactは設定済みwork rootの`eidolon-gpu-fd-mitigation-20261106/`。
stop runは`20261107T000754-518b7dfd`、二窓`20261107T001000-ec566702`、
テーマ`20261107T001022-bdf62b76`、popup/clipboard`20261107T001115-c41a2b71`。
今回の短い回帰はL04の長時間受入れ、signed release実測、HMD視認・物理操作の代わりにはしない。

初回CI `42240859040`は通常host suite成功後、独立したWaypipe clipboard検査でlock不整合を検出した。
GL threadのTID取得に追加したlibcを製品のCargo.lockへ反映した一方、
`scripts/fixtures/clipboard-waypipe/Cargo.lock`の同じpath依存の更新を漏らしていた。
ローカルでも同じ`--locked --offline`失敗を再現し、既存libcへの依存1行だけを更新した。
PC検証では`check-host --suite all`に加えてCIの別stepである`check-waypipe-clipboard`も実行する必要がある。

## 既存タスクと受入れ範囲

既存Phase 5（I07）のゴールと、追加されたNX01〜18の拡張goalを区別する。2026-11-04以降のユーザー指定により、HMD視認と物理入力の受入れは最終段階にまとめる。実装・host検証・ADBによる自動試験を先に進める。手動検証を含むcheck全体は、手動部分が残る間`not_run`とし、自動部分の成功を別に記録する。後続実装は自動部分の成立を根拠に進め、taskの`done`判定は省略しない。

各検証のsource commit・source digest・APK SHA256はartifactと下記のBuild別記録を基準とする。
raw artifactは作業開始時に読み直した`codex-work-root`の`eidolon-phase5-implementation/`と、テーマ導入後の`eidolon-theme-review/`配下。公開する記録に端末識別子・室内画像・秘密鍵は含めない。
表のBuild99は通常SDK全ケースの検証基準。後続Buildの自動検査は末尾の履歴、ユーザー回答とFキー免除は[最終実機確認](../features/initial-phases/phase5-manual-acceptance.md)を参照する。

| Task | 状態 | 検証と残件 |
| --- | --- | --- |
| B01 | done | Rust 1.95.0固定、model/android workspace、ID/上限、決定的build info、JNI 0.22.4。Rust test 3件・fmt・clippy成功。JNIの実機確認はB02へ分離 |
| B02 | in_progress | Build99のRust/Kotlin/APK版・ELF検査と通常SDK稼働が成功。Build99/100の二枚表示・可読性・passthroughをユーザー確認済み。最新候補のHMD確認は残件 |
| B03 | done | runtime/JNI/診断と固定caseを実装。ホスト11件・Python10件・debug/release検査、Quest Build 8の10cyclesと停止後late panic・製品復帰に成功 |
| C01 | done | acc284bのGitHub Actions run 42110160287でhost・Android・report全成功。Rust151件・launcher33件。Actions許可、context、依存lock等の切り分けを履歴へ記録 |
| H01 | done | 製品runtimeの専用I/O workerからAPK内PIEを実行。host・Questで10echo/明示終了/回収、起動中stop、releaseへのfixture不在を確認 |
| R01〜R03 | in_progress | Build99の通常SDK surface10回・pattern・surface-cycle20回が連続成功。HMD色/方向・物理Menu/sleepは最終確認待ち |
| T01 | done | APK専用Ed25519・Keystore AES-GCM・pin・公開情報pairing。Build21の実機保護検査、pairing冪等性、debug/release検査成功 |
| T02 | done | APK→Termux認証/exec、pin/別鍵/取消、stderr/timeout/exit、製品Host+GL同時動作とlive停止・復帰を検証 |
| T03 | done | 1byte/64KiB/8MiB×4、hash/EOF/上限/取消、host反復・Quest3回、配布物hashと所有socket回収 |
| T04 | done | 永続journal・再接続audit・lease・7故障caseと通常SDK/UI復帰がBuild62で成功 |
| W01〜W04 | done | native依存・SHM・window model・APK向けWaypipeを実機検証済み。Build62で通常SDKとの共存・UI復帰も成功 |
| W05 | done | SSH経由の100frame/hash・実foot受信・Stop回収と通常SDK/UI復帰がBuild62で成功 |
| D01 | done | owned画像→Smithay/Android EGLの実GPU readbackと、通常SDK上のfoot描画・UI復帰に成功 |
| D02 | in_progress | Build99の実foot通常SDK描画・Surface再生成・停止後UI復帰が成功。HMD可読性をユーザー確認済み。最新候補での通常nvim起動と残る物理操作を確認する |
| I01 | in_progress | Build99で実quad四隅/中央のUV・交点・固定ray入力と回収が成功。診断quadのUI重なりを解消。物理controller確認は残件 |
| I02 | in_progress | 通常SDK/wire成功。Build100でUS配列の文字・記号・repeat・修飾キー・Tab・左右矢印・tmuxをユーザー確認済み。Fキーは免除。恒久修正後の通常nvimとMenu/sleepは残件。キーボード検証の一旦中断指定を維持 |
| I03 | in_progress | Build99の通常SDK pointer操作・focus・解除・UI復帰が成功。実controllerで二枚をクリックして入力先が切り替わることは確認済み。drag/scroll・OS前面復帰・Menuは残件 |
| I04 | in_progress | Build99の通常SDK logical resize・実画像・SDKメートル計測・拡大後入力が成功。設定隔離と入力後の描画待ちを追加。HMD操作は最終確認待ち |
| I05 | in_progress | Build99でSDK二枚の片側喪失・他方入力/描画・新世代・第三window・表示切替/解除・close・実Activity再生成が連続成功。HMD二枚の可読性と入力先切替も確認済み。最新候補の残るHMD操作を確認する |
| I06 | in_progress | Build99で実SDK pose/寸法・背景API・USJIS・実Activity再生成・保存復元・製品設定不変が成功。Build100で部屋の視認も確認済み。背景往復・grab・配置保存の物理確認は残件 |
| I07 | in_progress | Build99の通常SDK共存・popup/限定UTF8 clipboard・通常UI復帰が成功。Coreの5秒FD制限とWaypipe中継の制限を区別。Phase 5共通の手動確認は残件 |

## 関連資料

[初期フェーズの実装・検証履歴](initial-phases-implementation-history.md)
