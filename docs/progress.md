# 製品実装の進捗

## 通常GUIの配線と視点姿勢（2026-11-08、Build175）

固定Freedoom用GUI worker/cookie/receiptとgroupのnative入力境界を統合し、debug/releaseのbuild・lint・packageと
JVM265/175件が成功した。新helperで通常footを起動でき、公開SDKのviewer poseを20回読んで有効な正面候補を計算できた。
通常IME検査のactive情報に参照違いを見つけ、修正を分離した。通常入口の成功とは扱わない。
catalog登録にもTermuxのAPI差があり、非上書き条件を保つ別APIを実測した。
詳細と未完了範囲は[Build175記録](build-175-integration-20261108.md)。

## 拡張の統合検証（2026-11-08、Build174）

通常footから明示的にIMEへ切り替える入口を共通controllerへ接続し、debug/releaseのbuild・lint・package検査、
JVM234/159件が成功した。共通検査経路でIMEのBOUNDと終了後の元targetへの直接入力復帰を確認した。
通常入口特有の初期owner経路、実かな漢字変換、物理Ctrl+Spaceは後続の受入れに残す。

Linux GTKの文字・削除・buttonと追加dialog、Termux GTKのinput/dialog/800×500→640×360 resizeが成功した。
全target退役、所有process/journal回収、通常SDK復帰も確認した。
resizeのPC観測形式誤りと検証wrapperの誤判定も含め、[Build174記録](build-174-integration-20261108.md)に残した。

窓別の移動・サイズ操作と最大9枚はBuild171で自動検証済み。
Freedoomの固定短demoはBuild172でSHM74commit・SDK更新・自然exit0・資源回収まで成功した。
通常GUIランチャー接続、グループ化のruntime/SDK/操作入口、別Linuxホストの通常GUI接続を継続中で、
それらの実装や物理受入れを完了扱いにはしない。
現在の拡張goalは[NX01〜18計画](next-phase-termux-startup-linux-gui-plan-20261107.md)であり、既存Phase5/6の受入れ条件も保持する。

## 手動確認後の修正（2026-11-07、Build142）

ユーザーの追加指定でリング線幅を6→30、切れ端を平らにした。中央のRSS回転は上限64MiBへ調整し、
小さいshellでも動きが分かるようにした。ランチャーを床上1.05mへ上げ、通常の左下側へ寄せた。
物理キーがActivityではなくSpatialの別Windowへ届いていたため、主操作・ランチャー双方の入口を接続した。
診断では文字を保存せず、入口・物理判定・enqueueの件数を分ける。

Build142をQuestへ導入。theme22件、debug build/lint/JUnit/APK検査、通常SDK keyboard8段階・theme-sdkが成功した。
両パネルへのADBキー到達と仮想device拒否、ランチャーからのfoot起動を確認した。
ユーザーが更新後の文字入力成功と、円の太さ・中央の回転の見やすさを確認した。この線幅と速度設定を採用する。
物理判定46件・enqueue46件・実配送46件も一致した。ランチャーはピンク色の「>_ ターミナル」と案内した。
詳細と残る個別受入れは[NX修正記録](extensions-nx01-nx08-20261107.md)。

## NX01〜NX08の実装（2026-11-07、Build141）

ユーザーが指定した直前一覧1〜5をNX01〜08へ対応させ、実装と自動検証を進めた。
線幅3倍、CPU/RSS別の回転、入力停止理由と窓別focus、固定主操作、電池/RAM/IP、
Termux必要時起動、左下のfootランチャー、日本語SKKの専用入口を追加した。
詳細と失敗を含む再現記録は[NX01〜08](extensions-nx01-nx08-20261107.md)。

- 通常APKの実UIDからTermux helper起動、保存済みpin/鍵で再接続、foot起動が成功。
- 編集終了からのfocus復帰、二窓切替、解除後の再選択、連打抑制、device metrics、元設定への復帰が成功。
- 通常SDKのテーマ・二窓・性能回帰が成功。負荷を観測しないときは回転を停止する新しい契約も検査した。
- 日本語はPCとQuestの専用PTYで変換・候補・確定・取消・英数復帰が成功。footでの候補視認と物理入力は最後に確認する。
- 権限拒否、連携設定無効、取消後の遅い結果を確認。途中の描画待ち・focus準備・SDK初期化の競合を修正した。

既存Phase 5の未受入れ、Phase 6の長時間・signed release検証、NX09以降の実装は別に残す。


## 最新のFD対策検証（2026-11-07、Build133）

AndroidのGL threadをprocess内で1本再利用し、runtime停止時にはjobの全資源と参照を返す方式へ変更した。
SDK 0.15.0のTID別GPU計測cacheを直接変更するものではない。停止契約は[Phase 6設計](design/phase-6.md#l01)、
失敗を含む切り分けは[GPU FD調査](gpu-fd-investigation.md)に記録した。

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
表のBuild99は通常SDK全ケースの検証基準。後続Buildの自動検査は末尾の履歴、ユーザー回答とFキー免除は[最終実機確認](phase5-manual-acceptance.md)を参照する。

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

## B01/B02: ビルドと実機の再現

```bash
rustup toolchain install 1.95.0 --profile minimal \
  --component rustfmt --component clippy --target aarch64-linux-android
cargo install cargo-ndk --version 4.1.2 --locked
cargo fmt --all --check
cargo test --locked --workspace
cargo clippy --locked --workspace --all-targets -- -D warnings
python3 scripts/check-design
scripts/build-android 4
scripts/check-native-package --apk "$EIDOLON_APK"
EIDOLON_TEST_APK="$EIDOLON_APK" python3 -m unittest discover -s scripts/tests -v
scripts/quest-smoke --serial "$EIDOLON_QUEST_SERIAL" --apk "$EIDOLON_APK"
```

`EIDOLON_APK`はbuild scriptが出した保存済みAPKを指定する。`quest-smoke`はAPKのpackage/版とQuest機種/APIを照合し、指定端末だけへ更新する。HMD目視はこのscriptの成功に含めない。

版情報は`schema/source_commit/source_digest/source_dirty/apk_version_code/crate_version/target/profile`。時刻と絶対pathは含めない。GradleのBuildConfigとRustへ同じ入力契約を適用し、APKのasset・埋込みJSON・so hash・JNI symbol・ELF noteの実API/NDK build・公開NDK依存を照合する。

Gitのない配布sourceでは、`EIDOLON_SOURCE_COMMIT`（40桁hex）、`EIDOLON_SOURCE_DIGEST`（64桁hex）、`EIDOLON_SOURCE_DIRTY`（`true`/`false`）を明示できる。通常は実ファイル内容からdigestを生成する。dirtyであるかどうかだけで変更を同一視しない。debug/releaseの生成先とtaskは分離する。

### 試行錯誤

1. **固定toolchainと権限:** `/opt/rustup`には1.95.0のstable aliasだけがあり、固定名を初めて参照したcargoはroot所有tmpへの書込みで失敗。管理者権限で固定版・components・Android targetを追加した。`sudo rustup`ではPATHに見つからず、絶対pathと既存RUSTUP_HOMEを指定した。導入後は通常ユーザーのcargoで成功。
2. **別アプリのログを誤選択:** `pm list packages io.github.tinmachine.eidolon`は独立probeも返す。最初のsmokeはprobeのUIDを選び、実際には成功していた製品のnative/sceneを観測できずtimeoutした。package完全一致と一意性検査を共通関数へ切り出し、prefix衝突の回帰testを追加。修正後run `20261104T235014-1607483`はnative_ready・版一致・scene/panel・XR FOCUSEDに成功。HMDは未確認。
3. **Cargoの版往復で古いcdylib:** workspace版0.1.0→0.1.1ではsoが更新されたが、0.1.0へ戻すとCargoがfresh判定し、共通の`debug/libeidolon_native.so`と`deps/libeidolon_native.so`に0.1.1が残った。assemble/lintは成功したがAPK検査が埋込みJSON不一致で拒否した。CARGO_TARGET_DIRをNDK版・workspace版で分け、生成直後にも期待JSONを照合するよう修正した。
4. **cache分離だけではcopyが古い:** その修正後も復元試験で生成先に0.1.1が残った。ただし版別cache本体には正しい0.1.0が保存されていた。[cargo-ndk 4.1.2の上流実装](https://github.com/bbqsrc/cargo-ndk/blob/v4.1.2/src/cli/mod.rs#L699)を確認し、`-o`のcopyが`source mtime <= destination mtime`なら省略されることが分かった。古いcacheへ戻す場合にこの条件へ入る。`-o`を使わず、期待target/profileのcache本体の版情報を検査してから、生成先へ無条件copyするよう修正。再び全往復試験を実施し、基準と復元のsoだけでなくAPK全体のSHA256も一致した。APK版変更の結果は下の受入記録へ記す。
5. **破損APKの試験fixture:** ZipInfoを別archiveのwritestrへ直接再利用するとheader offsetが書き換わり、fixture生成自体がBadZipFileになった。copy.copyして修正。製品APKのso破損・重複・API不一致の3つを意図的に作り、検査がすべて拒否することを確認した。

hostのidentity testは、dirtyのまま内容変更した時のdigest変化、cache生成物の除外、APK版/profile差、Gitなし明示入力と不正入力の拒否を確認する。実APKを指定したPython testは7件成功。Rust testはmodel 2件とAndroid bridgeのbuild info 1件が成功した。

### 版往復試験の結果（2026-11-05）

全行でassemble/lint・APK静的検査に成功した。基準と復元のAPK SHA256は両方`bdd6761389c7b0547d8cb2b72d75e2ce52e96b5f6e145a9a48e7a13220e6a3d9`。表のso hashは先頭16桁で、完全値はartifact JSONへ保存する。

| crate版 | APK版 | so SHA256先頭 | 判定 |
| --- | --- | --- | --- |
| 0.1.0（基準） | 3 | 769424010be32f2f | pass |
| 0.1.1（Rust版のみ変更） | 3 | ad4b6030ddd88c0f | hash更新、pass |
| 0.1.0（元に戻す） | 3 | 769424010be32f2f | so/APKとも元どおり、pass |
| 0.1.0（APK版のみ変更） | 4 | edb34a03075838d5 | source digest維持・so更新、pass |

Build 4のAPK SHA256は`a27ba122aae80b0dad3ad88628583bca9f7041d4c5f19eea010e3fe6622c186d`、source digestは`67c615a92fccde467ade6c36d4f848f589191780cddae8dbf97ffd226777cc4f`。いずれも基準commitからのdirty buildであり、後のcommitで作り直したAPKと混同しない。

実機run `20261105T000249-1633511`はBuild 4のnative_ready・Kotlin/native版とdigest一致・scene/panel・XR FOCUSEDに成功した。HMD確認はユーザー指定どおり最終受入れへ残す。

releaseの`assembleRelease/lintRelease`とunsigned APKの静的検査も成功し、`profile=release`・別出力先のsoを確認した。これはB02の生成経路の検証であり、C02の配布用署名・通常UI・release実機受入れは未実施。

主要artifact:

- `host-b01.log`、`python-b02.log`
- `builds/20261104T234607-1598875/`: 最初のBuild 3 assemble/lint/package成功
- `smoke/20261104T234832-1605138/`: UID誤選択の失敗とログ
- `smoke/20261104T235014-1607483/`: Build 3自動実機成功
- `smoke/20261105T000249-1633511/`: Build 4自動実機成功
- `restored-build3.log`: 修正前の古いso拒否
- `rebuild-fixed/`: cache分離だけではcopyが古かった再試行
- `check-rebuild.py`、`rebuild-final/`: cache分離と無条件copy後の4 build往復、JSONとログ
- `release-build4.log`、`release-package4.json`: release生成経路とunsigned APK静的検査

次はC01のCIとH01の製品runtime内helperへ進む。B02のHMD受入れは最終段階にまとめる。

## B03: runtime・非同期停止・診断（2026-11-05）

`eidolon-core`を追加し、model ownerを専用threadに置いた。JNI registryが発行するhandleは正のsigned 64 bitで、同一process内では再利用しない。通常commandは256件、UI eventは128件。停止は別atomic flag、入力解除も通常queueと別に伝え、failure/stopped各1件のevent枠を予約する。pollは64件/64 KiBまでで待機しない。

`nativeCreate/Command/Poll/Snapshot/RequestStop/Destroy`を実装した。enqueue成功とcommand完了は区別し、後者にrequest_idと結果を返す。停止前destroyはBUSY、二重stopは安全、破棄後の古いhandleはSTALE。destroyはowner threadの終了を確認してregistryから回収し、呼出threadでjoinしない。現在のruntime所有threadはcoreだけで、SSH/GL/Wayland資源の回収成功をこの試験で主張しない。

`NativeSessionHost`はApplication scopeでapplicationContextだけを保持する。Activity、Entity、JNI local reference、SDK Surfaceをnative registryへ保存しない。初期化と停止待ちはworkerで行い、通常commandのenqueueはJNIへ直接渡す。Java側の無制限なRunnable queueに入力をためない。SDKのXR状態、Activity可視性、scene epochを独立に記録し、runtime再生成後は最新値を再送する。

診断は`noBackupFilesDir/diagnostics/latest.json`へtemp＋fd.sync＋atomic moveで保存し、最大1 MiB。通常は1秒ごと、UIの「診断を保存」と試験完了時にも保存する。固定caseの結果は後続の周期snapshotにも保持する。状態・版・世代・件数だけを保存し、秘密鍵・入力文字・clipboard・pixel内容は含めない。

```bash
scripts/build-android 8
scripts/quest-smoke --serial "$EIDOLON_QUEST_SERIAL" --apk "$EIDOLON_APK"
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case runtime --timeout 120
```

`quest-check`は導入済みbase APKを取得して静的検査し、現在のsource digestとの一致を確認する。固定case名とランダム32桁hex nonceだけをdebug Activityへ渡し、診断のnonce・版全field・10世代の単調handleとworker回収・panic変換を検査する。さらに試験後に新しい製品runtimeがReadyになり、Activity可視状態・SDKのXR VISIBLE/FOCUSED・MainActivity復帰が揃うまで成功にしない。古いsnapshot、別版、資源残留、未復帰の拒否をhost testでも検査した。未実装caseはexit 2。

### 確認済み結果と境界

- Rust test: model 3、core 5、Android bridge/registry 3の計11件。fmtとclippy `-D warnings`も成功。
- 実APK指定のPython test: 10件すべて成功。schema、nonce、版違い、worker残留、破損APK等を含む。
- Build 5の初回実機case `20261105T002222-fe0044d3`: 10回の生成・停止・破棄、実JNI panicからの例外変換、新製品runtimeと通常Activityへの復帰に成功。
- 改訂後Build 7のcase `20261105T002919-ad749778`: Activity可視性を含む復帰判定に成功。
- 旧APKの拒否 `20261105T002902-2eb071e0`: caseを動かす前にsource digest不一致を検出して終了。期待した拒否であり、アプリの実行失敗ではない。
- debug専用Activity/classとpanic fixtureはreleaseへ含めない。APK検査はmanifest、DEX、native JNI symbolを直接調べる。Build 7のrelease assemble/lint・不在検査は成功。配布署名・release実機受入れはC02で別途行う。
- HMD視認・物理入力は未実施。ユーザー指定どおり最終段階へ残す。

### 試行錯誤とreview修正

1. JNI 0.22.4の初回cross compileで、`throw_new`のclass/messageがJNIStrを要求することと、JStringのCharSequence viewが`as_char_sequence()`であることを確認。`jni_str!`と実APIへ修正し、Build 5で実行した。Java UTF-16長とUTF-8 byte長の両方を検査し、巨大commandをcopy前にも拒否する。
2. host向けbuild.rsの監視をGit revision、Android source、native生成scriptへ拡張。最初のclippyでnested ifが検出され、Git出力helperとOption chainへ整理した。
3. reviewで、`onPause`からXR状態をIDLEへ上書きするとSDKの状態とActivity可視性が混ざる点を修正。`set_activity_state`を別commandにし、同じXR状態を保ったままActivityを背景化できるようにした。
4. failure/stoppedを通常eventより先に返すと、Stopped通知後に古いruntime_readyが残るため、通常event→failure→stoppedの順へ修正。満杯時もterminal用の予約枠は維持し、host testで順序を検査する。
5. 停止済みownerへの遅いJNI panicがStoppedをFailedへ戻すとdestroy不能になるため、Stoppedを終端として維持する。host回帰testと、実機最終cycleの停止前・停止後の両方にpanicを注入する試験を追加した。

主なartifactは`host-b03-accepted.log`、`clippy-b03-accepted.log`、`python-b03-accepted.log`、`builds/`、`checks/`、`release-build8.log`と`release-package8.json`。初回compile失敗と中間Build 5〜7のログも保持する。

最終受入れ: Build 8、run `20261105T003421-7e2d88d2`で10cycles・停止前/停止後の実JNI panic変換・所有worker回収・新runtime/Activity復帰がpass。release Build 8もassemble/lint・debug入口不在検査がpass。

Build 8は基準commit `c3954aa3ff7d27aae3242ddbfbb1b6f360c77cbb`からのdirty build、source digest `ea0720e809e4e8c2c30238ddb66f38846770ec761c8adb2336bf638de7b47213`、APK SHA256 `a118735a344de558491a3893bf2360c8c21dc227ac146b3bb539cf6bb2799dea`。

復帰条件を強化した再試験 `20261105T003803-5fdb7ea7` もpass。Activity再開だけではXR再表示より早く合格するため、実ログの約1秒遅いXR Session再開を根拠にVISIBLE/FOCUSEDも待つようにした。最終snapshotのXR状態は`FOCUSED`。

## C01: host/Android CI（2026-11-05）

`scripts/check-host --suite all`はdesign、Gradle checksum、fmt、workspace clippy、登録済みRust crateとPython testを実行する。現時点ではmodel/lifecycleが実装済みで、transport/compositor/input/launcherは`not_run`を明示する。未実装suiteを直接指定するとexit 2。Rustのcompile成功や0 testを成功とせず、新しいworkspace crateにもsuite登録を要求する。T03以降のlauncher testは専用一時fixtureで実行し、通常Termuxへ接続しない。

Gradle 9.4.1のwrapper JARとdistribution SHA256を[公式wrapper checksum](https://services.gradle.org/distributions/gradle-9.4.1-wrapper.jar.sha256)・[公式distribution checksum](https://services.gradle.org/distributions/gradle-9.4.1-bin.zip.sha256)と照合して`ci/toolchains.lock.json`へ固定した。`build-android`はJVM起動前にwrapperを検証する。破損JAR・配布checksum差し替えの拒否をfixtureで確認した。現在のnative依存はCargo.lockのchecksumと`--locked`で検証し、追加のnative archiveはW01でchecksum付きlockへ追加する。

Actionsはcheckout v7 / setup-java v6 / cache v5 / upload-artifact v7の公式tagを実SHAへ解決して固定し、node24であることも確認した。host/Androidを別jobとし、AndroidはTemurin Java25、API36/build-tools35、NDK27.3.13750724、Rust1.95.0/cargo-ndk4.1.2を指定する。cacheは依存downloadだけで、OS・lock・ABI/API・NDK・profileをkeyへ含める。target/生成済みsoはcacheしない。CI artifactはdebug APKだけを3日間保存し、署名鍵や端末ログはuploadしない。

GitHub連携のworkflow一覧取得はPR専用のため、pushでの検証結果は[commit status API](https://docs.github.com/en/rest/commits/statuses)にも`eidolon/host`と`eidolon/android`として記録する。report jobだけに`statuses:write`を付け、他jobは`contents:read`、fork PRではreportしない。両jobの結果とrun URLを対象SHAへ結び、ローカル成功をGitHub CI成功へ置き換えない。

ローカルではhost Rust 11件、Python 11件成功・実APK用3件skipを明示し、Android Build 9のassemble/lint/APK検査が成功した。wrapper・suite gateを加えたPython testは全14件。Build 9のAPK SHA256は`b879fc0ab6f03e662bb91eeb12e51f512e7d771d81afec138cf92c23f90ba49c`。端末へは未導入で、Questは受入済みBuild 8のまま。

C01初回pushは`9a4c58a27bc727635f7095017488cf64a2a9282d`。このSHAのGitHub commit statusはまだ取得できていないため、CI checkは`not_run`、taskは`in_progress`を維持する。ローカル成功で代用せず、独立したH01以降を進める。

## H01: 製品runtimeによるAPK UID helper（2026-11-05）

独立probeで確認した方式を製品coreへ組み込んだ。`RuntimeConfig.paths`にはKotlinのApplicationから得たnativeLibraryDir/filesDirだけを渡す。`helper_probe` commandは16桁小文字hex nonceを受け、固定の`libeidolon_probe_bin.so`と固定argvだけを実行する。shellや任意実行pathは受け取らない。debug APKだけにAPI34/arm64のPIEを同梱し、`useLegacyPackaging=true`でOSに抽出させる。書込み可能filesDirへ実行ファイルをcopyしない。

Core ownerはhelper I/O workerを一つだけ所有する。Childのstdout/stderrは同時に読み、各64 KiB上限後もEOFまで読み捨てる。Unix socketの既知frameをechoし、0長frameによる明示終了→exit確認→wait/reapの順で10回繰り返す。通常coreのsnapshot/command処理はhelper I/Oから分離する。停止flagはhelperへ共有し、子の終了・pipe reader回収・directory削除・worker終了を確認してからruntimeをStoppedにする。

runtime directoryは`filesDir/rt/<nonce>/`を0700でexclusive作成し、socket pathはUTF-8 byte長103以下。削除時は作成時のdevice/inodeを照合する。既存nonceとの衝突では上書き・削除しない。通常完走、途中cancel、別ownerのsentinel保持、runtime停止時のhelper回収をhostの実fixtureで確認した。

```bash
scripts/build-android 10
scripts/quest-smoke --serial "$EIDOLON_QUEST_SERIAL" --apk "$EIDOLON_APK"
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case helper --timeout 120
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case runtime --timeout 120
```

受入結果:

- Rust testはmodel 3・core 6・Android 3・helper fixture統合3の計15件。clippy/fmt成功。fixture crateもlifecycle suiteへ登録した。
- Build 10のassemble/lint/APK静的検査が成功。helperのAArch64 PIE、`/system/bin/linker64`、実API34/NDK note、公開NDK依存、APK内hashを検査した。
- Quest helper run `20261105T011101-8cdeb79c`がpass。APK UIDと子UID、SELinux contextが一致し、10echo・exit code 0・所有child 0・directory削除を確認した。別nonceで起動中に停止し、23 msで子・worker・directoryを回収した。これは単発のH01計測であり、L01の停止時間保証ではない。
- 同じAPKのruntime回帰case `20261105T011104-ac161880`もpass。両caseとも新製品runtime/Activity/XR FOCUSEDへ復帰した。
- release Build 10のassemble/lint・静的検査が成功し、helper fixture、debug Activity/class/JNIは含まれない。Waypipe本体の起動はW04で別に判定する。

Build 10は基準`9a4c58a27bc727635f7095017488cf64a2a9282d`からのdirty build、source digest `2423bc4d3752dae65881e3162258ae8f59703c507a25feea8cc9b198257cdc2e`、APK SHA256 `1d41ced681d830c3f17a924eed8fbc469004c4ca429fae246ca734eaa733a0b9`。artifactは`builds/20261105T010720-1744298/`、上記`checks/`、`host-h01-first.log`、`python-h01-first.log`、`release-build10.log`、`release-package10.json`へ保存した。

H01の診断改善として、exec時のerrnoを保持し、ENOENT（ファイル未存在）/EACCES（実行権限不足）を実fixtureで区別してcleanupも確認した。helper失敗時は破棄前のnative snapshotをdebug結果へ残す。stdout上限後の読み捨ても、retained長だけでなく入力がEOFまで消費されたことを検査する。

この改善後のBuild 11はassemble/lint・debug/release APK検査、helper case `20261105T012453-63ae0c50`とruntime回帰caseでpass。正確なrun directoryはprivate artifactのnonce suffixとresult.jsonを参照。host Rust 16件と実APK付きPython 14件が成功。APK SHA256は`d358b639ef6cb79bac8008cb07833dd967d61f52ed3b940cafc6bf4f9c58e333`、source digestは`756a1b40dcbe95869414116d278588c3766cb3d920ad74b0c2f59f5beb485c47`、基準`72ef1e112ba52b52942107b907ae6ee0ce0a7c01`のdirty build。

## C01: Actionsの許可設定復旧（2026-11-05）

ユーザーの確認でrepositoryのGitHub Actionsが無効だったと判明した。repository設定で`Allow tin-machine, and select non-tin-machine, actions and reusable workflows`と`Allow actions created by GitHub`へ変更済み。workflowが参照するActionはすべて`actions/*`の公式Actionで、SHA固定。復旧の記録をpushし、新しいpush eventのhost/Android結果を観測する。まだ成功したと判断せず、両方の同一SHA成功を確認するまでC01は`in_progress`を維持する。

復旧後の`06cd62c`のrun `41870384317`はpush eventを受信したが、開始直後failureでjobは0件だった。追加調査で、jobレベルの`env`に置いた`${{ runner.temp }}`が[許可context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)の範囲外と分かった。これをstepの`env`へ移した。YAMLとして読めるだけでは検出できないため、host jobへ`actionlint v1.7.12`による構文・context検査も追加した。ローカルでは公式releaseのSHA256を照合した同版でpass。新しいGitHub connectorの汎用`github_fetch`は`actions/runs?head_sha=...`を取得でき、PR専用wrapperの制限を回避してpush runを直接観測できた。

次の`6331c88`/run `41871665707`ではhostが成功し、AndroidはSDK導入の`FileNotFoundError: sdkmanager`で失敗した。runnerのPATHを仮定していたため、`ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager`を明示するよう修正。report jobは成功し、同一SHAのhost success / Android failureを記録できた。実行されなかったAPK buildを成功に数えない。

C01は`9c83853ba054a32743499f97aad107cd3bfabe26`の[run 41872241492](https://github.com/tin-machine/eidolon-wayland/actions/runs/41872241492)でhost/Android/reportがすべて成功した。Androidのassemble/lint/同梱検査、実APKの拒否検査、artifact uploadまで実行済み。同一SHAの成功を根拠にC01をdoneへ変更した。今後のsource commitについてもCI結果を確認する。

## R01–R03: 製品Surface・EGL描画・停止（2026-11-05）

`SpatialPanelAdapter`と`native/render`を製品runtimeへ統合した。Compose診断パネルに加え、一枚のSDK VideoSurfaceへRustがopaqueな四隅patternと動く矩形を描く。1024×768、RGBA readback、GL vendor/renderer/version、実config、frame/max swap時間を保存する。60Hz以下、非可視時pause。Activity/SDK SurfaceはKotlin、NativeWindow追加参照/EGLはGL ownerに分け、coreがGL owner終了まで停止完了を保留する。`registry.workers`はcore worker数、`runtime.render.workers`はGL owner数を別々に表す。

全tupleの世代比較、重複attach、遅延detach、context/Surface loss、停止をbounded queueとsnapshotへ実装した。`surface_lost`の受付と実際の`surface_detached`を分け、ack後にlistener/Entityを回収する。EGLDisplayはSDKと共有し得るためterminateしない。driver callの2秒待ちを`RENDER_STALLED`として記録し、他threadからGPU資源を破棄しない。SDK callbackの順序・thread・未確認項目は[実測記録](decisions/sdk-surface-lifecycle.md)を参照する。

試行錯誤とreviewで得た修正:

- 初回のBuild12で製品Surface/pattern/10回と20回再生成が成功。Build14から各cycleのFD/native heapを記録した。短時間のheap増加も隠さず保存し、長期リークなしとは判定していない。
- host helper fixtureが断続的に失敗。単体再実行だけでは原因不明だったが、4並列×30回のcopy/execでerrno26 `ETXTBSY`を再現。tmpfs上の実行直前copyをやめ、正常fixtureはimmutable build artifactへのsymlinkを使う。EACCES試験用copyは分離し、artifact自体のpermissionを変更しない。12回の並列suite・48tests成功。製品execの失敗をretryで隠す変更はしていない。
- queue満杯時の非表示通知がBUSYとなり、古い可視commandでGLが再開する経路を発見。再現testを先に失敗させ、最新SDK可視性を専用mailboxで保持して通常queue外でpauseする方式へ修正。BUSYを返したcommandへ完了eventを偽造せず、安全効果だけ適用する。
- EGLのContextLostに加え、GLESの`GL_CONTEXT_LOST`も同じ再構築経路へ分類。readback mismatchとdriver context lossを取り違えない。
- host fault injectionでdriver待ち中もsnapshot/Stop要求が100ms未満で戻り、所有threadが終了するまで回収成功を報告しないことを検査した。これは実機でdriver stallを起こした結果ではない。

Build15では実際に描画中だった製品runtimeの停止・回収が10msで終了した。別helper case開始時は7ms。実Menu/sleep・HMD・ray/UVと、後続W02の外部snapshot旧世代検査を保留してR01/R02/R03はin_progressを維持する。

最終candidate Build17でhostのRust22件・fmt/clippy/design、実APK付きPython15件、debug/release assemble/lint/静的検査がすべて成功。Quest smoke `20261105T020851-1832786`、surface10 `020905-d5f1c6fe`、pattern `020912-6fbff134`、surface-cycle20 `020917-1363bb93`、runtime `020924-e2bf1297`、helper `020928-8023a126`が成功した（check directoryのprefixはすべて`20261105T`）。cycle20のFDは287〜288、native heapは45,450,392→46,601,672 bytes。描画activeな製品runtimeの停止・回収8ms、helper試験開始時7ms。自動部分の受入とHMD未確認を区別している。

Build17は`9c83853ba054a32743499f97aad107cd3bfabe26`からのdirty build、source digest `4457124e4d91c39699169afb55f3bf2dde2fadec584e9bd10d25e61fb776184b`、APK SHA256 `32eeb98fe45fe3b832208c2fcd8cbf519387175656b4fb8069fd7f9c89112d8f`。`builds/20261105T020613-1829190/`、`host-render17.log`、`python-render17.log`、`release17.log`、`release-package17.json`、上記checksへ保存。commit後のclean buildと取り違えない。

R統合commit `87c673e5e42074af8a5995be1fa432a749bcc4cf`もGitHub run `41875258934`のhost/Android/reportがすべて成功した。

## T01: APK専用identityと公開情報pairing

RustでEd25519を生成し、Kotlinは平文をByteArrayで短時間だけ扱い、Android Keystore AES256-GCMで`noBackupFilesDir/credentials/identity.json`へ暗号化する。schemaとinstallation IDをAADへ含め、nonceはproviderに生成させる。通常診断・command JSON・file/Stringへ平文秘密鍵を通さない。一時ByteArrayとRustの対応bufferを可能な範囲で消去する。JVM全体の完全消去は保証しない。既存保存の消失・AES鍵喪失では黙って新identityへ置換せず、UIに復旧/再pairingの必要を表示する。

公開exportは`public.json`だけ。`quest-pair`はPCに既登録の物理端末aliasと公開host pinをADBで照合し、公開APK鍵だけをTermuxへ追加する。既存authorized_keysとoptionを維持し、二重追加しない。PC秘密鍵をAPKへ再利用しない。秘密提供JNIはschema JSONと別で、待機中session/requestと期限を検査するslotを一度だけ使用する。slotは失敗・Stop・期限切れで消去。実SSH認証はT02の受入で別に確認する。

試行と修正:

- 初回Build18はJNI旧APIの配列長i32/usize差でcompile失敗。`JByteArray.len(env)`へ修正した。Kotlinの修飾子keywordと同じ局所名も変更した。
- Build19はassemble/lint/APK検査、host Rust26件・実APK Python19件で成功。Quest credentials caseは冪等性、公開情報だけのexport、0600、非exportable AES、正しい/未知/不一致pin、未要求/停止済みruntimeへのcredential拒否、暗号文改変、fixture AES鍵喪失、黙った再生成なし、production保存不変の全項目でpass。fixtureはproductionのalias/暗号文を消していない。
- 初回pairingは端末/host pin照合に成功し、Termux側Python未導入で公開鍵更新前に失敗。`.ssh`700・authorized_keys600は正しく、権限を緩める修正は不要だった。既存のPC開発用SSHで`pkg install -y python`を実行してPython3.14.8を導入し、bootstrap依存にも追加した。このPC接続をAPKのSSH認証の証拠にはしない。
- pairの再起動確認で古い診断fileを誤認しないよう、診断にPIDを追加し、新PID/同一build/Ready/Activity可視性を照合する。PIDが古い・別process・別buildのfixtureは拒否する。現在この変更を含むBuild20を検証中。

最終reviewでは、暗号文/public両fileが消えてKeystore aliasだけが残るケースもKEY_LOSTとするよう補強した。初回保存の途中失敗も自動で別identityへ置換せず、復旧を要求する。専用fixtureで両fileを削除しても新file/aliasを作り直さないことを確認し、productionは変更しない。ADB exec-outはremote exec失敗でも終了code0となる場合があったため、pairの変更操作はshellの終了codeとJSON resultの双方を照合する。

Build21はhost Rust26件、実APK Python20件、debug/release assemble/lint/APK検査がすべて成功。Quest credentials `20261105T025641-d988fa91`、surface-cycle20 `025648-1af55e47`、runtime10 `025656-198550b4`、helper `025701-daeec2af`がpass（省略したprefixは`20261105T`）。pairingは初回の公開鍵追加、2回目の追加不要、既存行保持、profile保存、installation不変、新PID/同一buildのruntime復帰を確認した。Build21のpair再実行も追加不要で成功。これはAPKのSSH接続成功を意味せず、実認証はT02で検査する。

基準`87c673e5e42074af8a5995be1fa432a749bcc4cf`のdirty build、source digest `e949c8e8ecec8dcbb55322a812f09bbe231b0e18fa5c83ff646a03ff3287f572`、APK SHA256 `489a657776ff43f8008bc7236de383ec5a404195adbce117e30606758d0f404d`。artifactは`builds/20261105T025330-1881380/`、上記checks、`pairing/`、`host-t01-final20.log`、`python-t01-21.log`、`release21.log`、`release-package21.json`。失敗したBuild18と初回pair、Python導入logも削除していない。

```bash
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case credentials
scripts/quest-pair --serial "$EIDOLON_QUEST_SERIAL"
scripts/quest-pair --serial "$EIDOLON_QUEST_SERIAL"
```

公開pinを含むprofileや公開鍵自体もstdoutへ表示せず、結果は識別・保存・保持のbooleanと安定error codeだけを記録する。releaseにはCredentialCase・debug pin JNI・fixture alias/directory名がないことをAPKから検査した。

## T02: 製品SSH・認証・固定exec（2026-11-05）

公開profileをinstallationへ束縛してNativeCreate前に検証する。接続先はloopback8022固定。coreがSessionIdを発行し、I/O ownerのserver key完全一致後だけcredential_requiredを発行する。Kotlin workerがKeystoreを復号し、既存の単発slotを経て認証する。パスワード/keyboard-interactiveへfallbackしない。通常の接続ボタンも同じHost経路を使う。

I/O supervisorが接続ごとのTokio runtime（worker2、blocking最大1）を所有する。cancelでawaitを中断し、supervisorでruntimeをdropして、未完SSH task/FD/workerを回収後だけconnection0/再接続可を報告する。core/GL/UIからjoinしない。coreのStoppedはhelper・GL・SSH所有者の終了後。

connect5秒・auth10秒（鍵提供待ち込み）・exec10秒。stdout/stderrは別64KiB ring、上限後もEOFまで消費し、本文は診断に出さない。UID/token/bytes/exit status/exit signal/EOFを別に保存する。

試行と補強:

- host fixtureで2.5MiB stderrを最後まで排出し、cap後も終了を受信。誤pin時のcredential要求ゼロ、別認証鍵でAUTH_FAILED・password試行ゼロを検査。connect/auth/exec timeout、exit17、TERM signal、SSH bannerを返さないpeerのStopを区別して回収した。
- Core統合の初回compileでIDのchecked_nextがOptionなのにResult扱いしていた箇所を修正。古いsessionのDisconnectが新しいauth slotを消さないdeterministic testも追加した。
- Build22で初めて製品APK→Termuxの4scenarioがpass。試験後の古いsnapshotを誤認しないよう、SSH/credential caseは停止済みhandleより新しい製品runtimeを必須にした。
- Build24のssh-productは製品Hostの鍵提供経路で成功。SSH接続を保ってGL frameが更新され、両ownerが動くruntimeの停止・回収は8ms。その後、新runtimeでIdle/一枚の描画/readbackへ戻った。手動HMD視認の証拠にはしていない。

最終Build24: host Rust34件、実APK Python22件、fmt/clippy/design、debug/release assemble/lint/APK検査が成功。Quest ssh-product `20261105T034725-58b0326e`、ssh `034729-50a9a04e`、credentials `034735-cc977a91`、surface-cycle20 `034740-bc98a5a9`、runtime10 `034748-acb59fbf`、helper `034754-155501ad`がすべてpass（省略prefixは20261105T）。pin不一致は要求/鍵提供0/0、認証失敗1/1、取消1/0、正常1/1。全scenarioで停止後worker/Tokio/connectionが0、公開profileは不変。UIDはADBで取得したcom.termux UIDと一致した。

基準`0c6812c282ff23139269e2b6a0491ffe7c30e2c3`のdirty build、source digest `5ad4aa6fb6867e172548af8c481d2beee94d4ee036fc2f117995e8532697efe1`、APK SHA256 `255698ef0de4212d1201e2c54cec512af1ed0a41853fc5c83a0a8e82dd7ac15f`。artifactはbuilds/20261105T034412-1910887、上記checks、host-t02-final24.log、python-t02-24.log、release24.log、release-package24.json。

```bash
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case ssh --timeout 180
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case ssh-product --timeout 180
```

T01 commit0c6812cのCI run41882737833もhost/Android/report全成功を確認した。T02受入時点ではT03のStreamLocal echoとT04のremote leaseは未実装で、SSH成功だけでforward/Waypipe成功には数えていない。T02確定commit c26ab47のCI run41892112868も全成功。

## T03: StreamLocal echo（2026-11-05）

APK内のUnix listener→remoteのexclusive prepare→StreamLocal成功応答→固定echoの順で実行する。forward callbackはsession/path完全一致と最大8channelのpermit予約だけを行い、local connect（2秒）とaccept/copyは所有taskへ渡す。任意のserver起点session/TCP/agent/X11/direct socketは拒否する。方向ごと32KiBのcopyは片側EOF後も反対側をdrainし、取消後に新taskがjoin対象から漏れないよう、受付と停止を短いgateで直列化する。正常終了ではforward取消→転送task join→owned local/remote socketとdirectory回収を確認する。

Termux launcherはPython標準libraryだけ。prepare/echo/cleanupと16桁hex nonceに限定し、0700 directory/0600 owner markerを検査する。既存session/socketを上書きしない。APKはprepareのpathとlauncher SHA256を自身の配布物と完全一致検査する。配備scriptも転送前後のSHA256を検査する。APKのlocal directory/socketは作成時のdevice/inodeと照合して回収する。prepare成功ackがないnonceをcleanupで削除しない。応答消失・SSH断時のremote残件照合、heartbeat/lease/子process groupはT04へ残す。

試行錯誤と判断:

- sha2 0.11のdigest ArrayはLowerHexを実装しないため、byte列からの明示hex変換へ修正した。OpenSSH用鍵のLineEndingもrusshのre-exportを仮定せず引数型のDefaultを使った。compile失敗logを保存した。
- russh両側の初期fixtureはsmallサイズ・単独8MiBに成功し、同時4本で途中停止した。server event queueを10→256へ増やすだけでは改善しなかった。callbackで転送終了までawaitする実装や無制限queueへは変更していない。
- 製品と同じOpenSSHで、最初のQuest Build25は同時4本に成功。hostにも合成鍵/専用port/所有processだけのOpenSSH fixtureを作った。一時directoryの祖先/tmpをStrictModesが拒否したため、0700/0600の合成fixtureだけStrictModes noとする。通常host/Questのsshd設定は変更していない。host OpenSSH10.2p1ではAllowTcpForwarding noの状態でStreamLocalもport forwarding refusedになり、fixtureでyesにすると成立した。これはその環境での観測で、一般的な仕様とは断定しない。
- OpenSSH fixture単独成功後も、全host suiteで大容量転送の途中停止が再発した。受信windowをdefault2MiB→256KiB、channel queueを128件へ固定し、製品とfixtureへ同じ設定を適用した。この構成でtransport13件を10回連続、さらに全host suiteを実行して成功。queue/flow controlの相互待ちを疑って調査したが、library内部の根本原因を確定したとは記録しない。後続の長時間/障害検査でも再発を監視する。
- 最終Build27でQuest echoを3回実行し、全回で1byte/64KiB/同時4×8MiBのSHA256とbyte数が一致した。送信33,619,969byte、EOF後trailer込み応答33,620,023byte、peak4、終了後active0。forward取消・local/remote回収、新runtimeの描画復帰も成功。echo後のruntime停止は7/7/8ms。手動HMD確認とは別の自動結果である。

最終検証: Rust38件・fmt/clippy/design・actionlint、実APK Python23件、launcher Python4件、debug/release assemble/lint/静的検査が成功。copy buffer上限512KiBは8本×2方向×32KiBであり、SSH内部queue・kernel・固定echo bufferを含む総メモリー量ではない。hostのwrong path/旧session/forward拒否/local接続失敗/8本上限/取消を検査し、host大容量caseは半閉鎖後の応答と遅い読み手も含む。

Quest checksは20261105T042357-9b42d75d、042404-63924aa3、042411-09cf4a45（forward3回）、042418-4e23f05f（ssh-product）、042424-cdbefccc（ssh）、042430-15f2617f（credentials）、042436-fe9a80fd（surface-cycle20）、042445-4b7b57a2（runtime10）、042451-bfb03b48（helper10）が全pass。省略prefixはすべて20261105T。

Build27はc26ab47d1718a6bd82c10e505b4db55d5d8dc29cのdirty build、source digest `04de595764bd2e3d4b5e810019ebafec8656929c03263bd8175f62b30173d8d7`、APK SHA256 `c59da58df796a7fb3ac8e1efb27604d91557a4992d850954b43b6a9f0f306ac4`。artifactはbuilds/20261105T042220-1938466、上記checks、forward-window-repro1.log、host-forward-final27.log、python-forward27.log、launcher-host1.log、release27.log、release-package27.json。失敗した初期fixtureとcompile/設定のlogも保存している。

```bash
# hostの専用fixtureにOpenSSH serverが必要（既存sshdの設定変更は不要）
sudo apt-get install openssh-server
scripts/check-host --suite all
scripts/quest-install-launcher --serial "$EIDOLON_QUEST_SERIAL"
scripts/build-android 27
scripts/quest-smoke --serial "$EIDOLON_QUEST_SERIAL" --apk "$EIDOLON_APK" --require-focus
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case forward --timeout 180
```

接続先は製品内loopback8022固定。上記serialはADBの対象指定だけで、製品のSSH宛先には渡さない。SSH認証鍵・保存済みprofileと既存Termux processを維持して検査した。Phase5全体とT04のlease受入はまだ完了していない。

## T04: lease owner・永続回収記録（UI受入待ち、2026-11-05）

prepareを長寿命ownerに変更した。prepared JSON lineの後、stdinからnonce付きの固定heartbeat/start/stopだけを受け、子を新process groupで起動する。10秒heartbeat/60秒失効、EOF/StopでTERM→3秒後KILL→wait。PIDを保存recordから拾ってsignalせず、保有Popen handleと、そのleaderをWNOWAITでreap前に保持したgroupだけを対象にする。stdout/stderr各64KiB tailを保持し、超過後もEOFまで排出する。診断は件数・exit・固定echoのJSONだけで、任意の子出力本文を公開しない。

prepared/child_startedの出力時にSSH stdoutが先に閉じても、ownerはIOFailedとして回収を最後まで進める。回収済みreceiptはnonce/UID/process starttimeを含み、最新64件に制限する。receiptのあるnonceを再利用しない。後日のcleanup CLIは確認とreceipt照合を行い、未知のdirectoryの所有権を取得したり保存PIDへsignalしたりしない。削除はexclusive prepareを実行したownerが、子とpipeを回収した後に行う。

Rust側はscript SHA256/UID/nonce/pathとline上限を検査し、channelのEOF/exitとremoteの回収ackを別に扱う。ackがない場合はcleanup_pendingのままにする。hostの実OpenSSH試験でStop、heartbeat維持、失効、stdout/stderr洪水を確認した。短縮heartbeat fixtureで次回送信が固定10秒へ戻っていた箇所をtestが検出し、指定intervalを維持するよう修正した。Python fixtureではTERM無視の子へのKILL、応答消失、PID再利用の照合、孤児の非kill、無関係processの生存も検査した。

Build28は新owner経由で従来の6channel echoに成功。Quest上でもBionic/PythonのWNOWAIT・process group・pipe回収が成立し、launcherのfinished/EOF/exit0と所有子のidentityを照合できた。受入scriptもsummary booleanだけでなく、childのstarttime一致・UID・EOF・pipe終了・cleanup_pending=falseを必須に補強した。自動echo/SSH/credential/surface-cycle20/runtime10/helper10の回帰、host Rust42件・launcher Python11件、実APK Python23件、debug/release assemble/lint/静的検査が成功した。

Build28はa3b58b8e2ac28eb5228b3dc217ab82b809ecf0e4のdirty build、source digest `c7e0e222e7083de2bc4273f4fafa528ff38692730481e85b42085a8755dbd6ff`、APK SHA256 `ef09a983d83d4df2559ddc873cbdaf7a8d9dd949fd52775b5b9fc7262b061d3a`。artifactはbuilds/20261105T045211-1956275、forward28.log/forward28-ack.log、launcher-t04-host5.log、python-lease28.log、release28.log、release-package28.jsonなど。

後日のauditが過去directoryを削除できないようcleanupを保守的に変更し、未確認資源のjournalを追加した。専用0700 directoryの0600 JSONへ、最大8件のnonce/session/UID/接続identityのhash/子のprocess identityをatomic保存する。exec前にpendingを保存し、回収ackまたは新接続のreadonly receipt照合後だけ消す。runtime再生成でも残件を読み込み、回収未確認なら新しいremote起動を許可しない。SessionIdの採番も永続化し、新runtimeで過去のIDを再利用しない。

追加実装と試行錯誤（まだ受入途中）:

- Build29ではAndroidのfilesDirが0771であるためjournal初期化が失敗。hostで同じmodeを再現し、親の権限を変更せず、その下の専用0700 directoryを使うよう修正した。
- Build30の正常forwardは成功したが、故障caseのexec前取消後、Stop要求直後のsnapshotが一瞬Readyのままで失敗。要求受付時に同期でStoppingを反映し、protocol ownerを意図的に停滞させても即座にsnapshotが変わるhost回帰検査を追加した。
- Build31はdebug/release assemble・lint・同梱検査、実APK付きPython24件、host suiteとlauncher Python11件に成功。Quest上のアプリ起動検査はPIDが作られる前に保留され、撮影すると「ルームで位置を見つける」画面が表示されていた。アプリの回収失敗と混同せず、追跡回復後の実機再検証を残す。
- 起動runnerは`am start -W`の30秒待ちに依存せず、起動要求後に同一buildのnative/scene/XR readinessを期限内で照合するよう修正した。境界画面の自動承認や無効化はしていない。
- exec要求失敗時のchannel closeにも1秒の上限を追加。Build32はhost Rust51件・launcher Python11件、実APK Python24件、debug/release build・lint・同梱検査に成功し、APKを再導入した。Questの7故障caseはまだ未開始で、host成功を実機の代わりに数えていない。

起動保留中もnativeを検証できるよう、debug APKだけに`NativeCheckInstrumentation`を追加した。
[Android標準のInstrumentation](https://developer.android.com/reference/android/app/Instrumentation)のthreadから、
Activity/Sceneを開かずに製品と同じUID・Keystore・profile・standard JNIで既存caseを実行する。
最後に別runtimeを生成し、Ready→停止→destroyとregistry0を確認する。
任意path/commandを受ける入口は追加せず、case名とnonceだけを受け取る。

```bash
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case forward --instrumentation --timeout 180
scripts/quest-check --serial "$EIDOLON_QUEST_SERIAL" --case remote-cleanup --instrumentation --timeout 300
```

Build34でforward `20261105T064553-22d6e27d`、remote-cleanup `064616-ad785cb5`が成功した。
7種類（exec前取消、prepare後EOF、稼働中Stop、制御EOF、SSH断後再接続、SSH断後runtime再生成、
実60秒lease失効）すべてに成功。leaseの実測は60,346msで、各停止は32/16/107/11/9/11/8msだった。
SSH断時の回収未確認をpendingとして残し、新epochから同じnonce/UID/子identityのreceiptを照合し、
runtime再生成を跨いでjournalを解消できた。全case後の新runtimeも停止・destroyして所有registryは0。

この経路の結果は`scope=native-instrumentation`、`normal_ui_returned=false`として保存する。
従来の画面復帰gateも維持し、instrumentationのpassを渡してもUI合格にはならないことをtestした。
T04は通常のQuest caseでの製品UI復帰が未確認のためin_progressを維持する。
描画やHMD・実入力の受入は後続も含めて残す。

追加reviewでは、receiptがdangling symlinkの時に`exists()`だけでnonceの再利用を見逃す穴を確認した。
先行testで再現後、symlinkの存在も検査して、既存記録を保持したままprepare前に拒否するよう修正した。
この修正を含むBuild36でもnativeのforwardと7故障を再実行して成功した。
最終remote-cleanup runは`20261105T065454-054aebd6`、実leaseは60,363ms。
各停止は31/14/102/13/12/10/10ms、受付はすべて0〜1ms。
最後の別runtimeの停止・destroyも14msで終了し、registryのruntime/workerは0だった。
いずれもUI復帰は未検証として保存している。

最終検証はhost Rust51件・launcher Python12件・実APK付きPython24件、
debug/release assemble/lint/ELF/版/同梱検査が成功。
Build36はa3b58b8e2ac28eb5228b3dc217ab82b809ecf0e4からのdirty build、
source digest `69604489c38526735bf64673afe272e5852dff1f099a5dfe1ceb5cbcfadcb0e8`、
APK SHA256 `fdf3d23dbd9b64a4ce80a9cf3dfb78b47f06e58279cce83ae1f7638072cb0b62`。
artifactは`builds/20261105T065121-2058405/`、`t04-host-final36.log`、`apk-tests36.log`、
`forward-headless36.log`、`remote-cleanup-headless36.log`、`release36.log`、`release-package36.json`。
失敗した起動試行・nonce再利用の先行red test・以前の検査logも保持している。

pattern/footの実binary接続はW04で行い、現時点はDEPENDENCY_NOT_READYで成功扱いしない。
T03確定commit a3b58b8のCI run41897106364はhost/Android/report全成功。
Phase5 goalは継続中で、手動HMD・物理入力は最終段階へ残す。

## W01: 固定native依存とAPK UID基本検査（UI復帰待ち）

Smithay0.7.0の最小frontendと、固定sourceから構築したlibxkbcommon1.8.1を製品へ接続した。
data2.48からUS/JIS keymapを自己完結textへ展開し、hash/byte数/licenseをAPKへ同梱する。
hostとNDK/API34のbuildを分離し、system Wayland/DRM/GBM/udev backendを有効にしない。
source cacheの改変・追加file、古い依存build marker、壊れたAPK keymapも拒否する。
詳細・再現command・初期の失敗は[native依存の固定build](native-dependencies-build.md)を参照する。

QuestのAPK UIDから、短いUnix socket上のWayland sync応答、SCM_RIGHTSでのFD送受信、
memfd/mmapの4096byte共有、US/JISのinclude無しparseと意味検査を10回実行した。
Build39の`20261105T071657-e46f6b0b`は全回pass、各28〜44ms、FD数70で一定、
各回8FD回収・残存mapping0。最後のruntime停止は13msだった。
同sourceのforward `071648-73752891`と7故障 `071702-564a0fa4`もpass。
前buildの失敗で残ったjournal1件も、認証接続のreadonly receipt照合で検証・解消した。

統合時にはKotlin条件式とfixtureのjournal保持契約を修正した。
再接続検査ではJNIのenqueue受付をdispatch完了と取り違えて旧Failedを読むraceも検出し、
対象request_idのcommand_completeを待つよう修正した。終了失敗時の最終snapshotもrunnerで保存する。
host Rust54件・launcher Python12件、実APK Python27件、debug/release build/lint/packageが成功した。

W01は通常caseの製品UI復帰が未確認のためin_progressを維持する。
SHM buffer、SIGBUS/ART共存、一般Wayland client、Waypipe/footは後続の検証・実装へ残す。
Phase5全体の完了ではなく、次のW02へ進むnative基盤の証拠として記録する。

## W02: 最小Wayland protocolと安全なSHM copy（native検証済み）

GitHub Actionsの許可変更後、W01 commit `fc4b0af`のrun `41925351431`はhost/Android/report全successとなった。
W02では実global handlerを残して広告versionを制限し、configure/ack、仮想output、
入力capabilityなしのseat、空selectionと取消を処理するdata-device v3を実装した。
共有mappingの読取りを保護callback内に隔離し、premultiplied RGBA8の所有済み画像へcopyする。

実mappingの遅延解放まで予算を保持するSmithayの最小patchと、原本+patchの全file検証を追加した。
さらに、別rootの同期childが同bufferを未適用commitに保持している場合の早期releaseを
固定clientで再現し、transactionと共に保持する使用tokenで修正した。

Build43のQuest APK UIDでshm 9case、独立truncate、依存10cycle、SSH forward、remote回収7故障が全pass。
各100frameのpixel hash、同期cacheの2consumerとrelease一度、SIGBUS後の同Display回復、
切断後FD/mapping/object/pool/pixel予算0を照合した。全SHM caseでFD数70が一定だった。
host Rust60/Python25/launcher12、実APK Python29、debug/release build/lint/packageも成功。
詳細なrun ID・hash・再現command・失敗と修正は[Wayland/SHM検証記録](wayland-shm-checks.md)にまとめた。

自動結果は`scope=native-instrumentation`、`normal_ui_returned=false`。
通常UI復帰とScene稼働時SDK handlerとの共存は未確認なのでW02はin_progressを維持する。
Phase5/I07全体を完了扱いにせず、次はW03のlifecycle、scale/transform、popupとimmutable snapshotへ進む。

## W03: Window寿命・owned snapshot・tree（native検証済み）

map/unmap/remap、非再利用ID、configure ACKと画像commitの分離、subsurfaceの同期/非同期とstack、
popupの位置・親clip、scale/8transform、最新のimmutable snapshotを実装した。
所有済みpixelだけを渡し、可視/target/render予算でframe callbackを最大60Hzに制限する。
検査は明示的な仮想clockを使い、実HMDのfpsとは区別する。

Build45のQuest APK UIDで`wayland-state`10caseが全pass（run `20261105T092341-ede857b2`）。
旧snapshot/旧treeの不変性、ACKだけではresizeしないこと、未ACK旧serialのunmap後拒否、
nested popupの2×2/4×4 clip、popup_done 3回、異常client後の同Display回復を確認した。
各case 58〜93ms、FD数70で一定、終了後FD/mapping/client/object/window/pool/pixel予算は0、停止6ms。
shm9・独立truncate・native依存10・forward・remote回収7故障も同sourceで全pass。
host Rust64/Python27/launcher12、実APK Python31、debug/release build/lint/packageが成功した。

Rust借用のcompile失敗、positioner Dispatch未登録、独自cache時のrole reset、検証器のcleanup迂回を修正し、
[再現手順・run/hash・試行錯誤](wayland-window-state-checks.md)へまとめた。
W03は通常UI復帰とSDKパネルへの接続が未受入のためin_progressを維持する。
次はW04のAPK向けWaypipe build/起動へ進む。Phase5/I07全体のgoalは継続する。

## W04: APK向けWaypipeの固定build・起動停止（native検証済み）

Waypipe 0.11.3 C版、LZ4 1.10.0/Zstd 1.5.7をchecksum付きsourceからbuildし、
`libwaypipe_bin.so`をAPKのnativeLibraryDirへ同梱した。WaypipeのDT_NEEDEDはlibc.soだけ。
最初はAndroidにないlibrtを要求して失敗したため、Androidだけlibcを使う最小patchを固定した。
security-contextを無効にし、そのためだけのlibwayland/scanner依存も外した。
API34ではNDK libcのspawnを使い、TermuxのELF/private prefixはcopyしない。

Build46のQuest case `20261105T093854-0e4e328b`は3probe×3回の起動停止と実行中取消でpass。
実子processのUIDをAPKと照合し、compositor接続・転送socket接続・waitを各3回/probe確認した。
各停止最大5ms、FD70一定、実行中Stopの受付0ms・全回収14ms、残client/object/group/socketは0。
新runtimeの生成・停止・registry0も成功した。これは画像転送の合格ではない。
window-state10・shm9・独立truncate・native依存10・forward・remote7故障の回帰も同sourceで全pass。
実lease失効60341ms。全scopeはnative-instrumentation、通常UI復帰は未確認。

host Rust65/Python28/launcher12、source cache改変拒否1、実APK Python34、debug/release build/lint/packageが成功。
[再現手順・固定依存・hash・失敗と修正](waypipe-build-checks.md)へ集約した。
W04は共通の通常UI復帰確認を残すためin_progress。続くW05でTermux側toolsとSHM実転送を検証した。
Phase5/I07 goalは継続中。

## W05: SSH経由の100frame/hashと実foot受信（native検証済み）

同じ認証済みSSH SessionIdへDisplay/Waypipe/remote ownerを束縛し、tools照合とforward成功ACKの後に
固定producerとfootを順に起動した。Build49のrun `20261105T105352-410c6b8d`は100frameすべてのhashと
release/callback一致、footの非一様な9buffer、WindowId1→2の非再利用、STALE session拒否に成功した。
実行中Stopは受付0ms・全回収206ms、所有worker/group/socket/SHM/pool/pixelとpending journalは0。
新native runtimeの生成・停止・destroyも成功した。通常UI/XRパネルへの表示は未受入である。

footの512MiB仮想SHM最適化とcompositor予算、停止時に残る`w` socket、backend列挙の再入deadlockを
調べて修正した。Build48の残件は原本記録と対象identityを照合して手動復旧し、自動回復の成功と区別した。
[再現手順・実測値・hash・失敗と復旧](waypipe-forwarded-shm-checks.md)に記録を集約した。
W05は共通UI確認を残してin_progress。次はD01/D02でowned snapshotをSDKパネルへ描く。

## D01: owned画像→Android EGL/Smithayと実GPU readback（native検証済み）

Androidの公開EGL handleとNativeWindowをSmithayへ渡すadapterと、GL owner一つのcontextを実装した。
owned RGBA以外をrendererへ渡さず、texture cache/update、letterbox、clip、transform、premultiplied alphaを扱う。
既存の四色診断もSmithay経由へ移し、raw GLの描画stateを混在させない。

Build55のImageReader実機caseは四色と36stageを通過した。8transform×scale1/2、透過/重なり/clip、
同寸法更新とSurface再生成、readback496sample、Stop受付0ms・全16ms、全owned資源0を確認した。
host Rust69/実APK Python38/launcher16、debug/release assemble/lint/packageと既存実機8系統の回帰も全pass。
最初の色不一致を段階診断で切り分け、EGL出力だけの上下補正と元error保持で修正した。
[再現・run/hash・失敗と修正](owned-image-render-checks.md)へ記録した。

これはImageReaderとsynthetic visibilityでのGPU検査で、SDKパネルへの実foot表示やHMDの合格ではない。
D01は通常UI共通scopeを残してin_progress、次はD02のmap/SDK binding/frame callbackを接続する。

## D02 — 実footと通常SDKパネルの接続（Build63）

SSH→Waypipe→owned RGBA→Smithay/Android EGL→実SDKパネルを接続した。
Coreの希望create/destroyとSDK適用済みをcommand_seq・全世代tupleで分け、
旧Entityの解放を待って再生成する。通常UIの「ターミナルを開く」は認証後に固定footを起動する。
画像のない通常targetを診断四色で代用せず、同じframeの実presentと可視性を確認したときだけ
Waylandへ更新通知を返す。

Build62でT04/W01〜W05の通常SDK共存・UI復帰が全成功し、D01のGPU readback回帰と
通常foot描画も成功した。これらの共通gateをdoneへ更新した。
Build63では画面下部を含む連続出力の画素変化、pause/resume、Surface再生成、停止・通常UI復帰を確認した。
D02はHMD視認を最終段階へ残してin_progress、物理入力はまだ未受入である。

通常SDKで初めて表面化した検査上の問題も修正した。
W05検査がcompositor側完了を待たない競合と、W01 auditがSDKの新しいepollを
自分のFDの解放漏れと誤認する問題である。後者は元epollのdup漏れも検出するmarker監査に変更した。
失敗run、切り分け、kernel一次資料、再現command、最終runとAPK hashは
[foot描画検査](foot-render-checks.md)へ集約した。

Rust75test/Clippy/fmt、Python実APK40test、launcher16test、debug/release/lint/packageが成功。
次はI01のSDK ray/共通座標変換から入力・focus・二枚のwindowへ進む。
Phase5/I07 goalは継続し、HMD/実入力の確認は最終段階にまとめる。

## I02 — keyboardと押下解除（Build69）

Android物理keyの入口、evdev正規化、同梱compiled US/JISのseat、
window focus、XDG Activated、device別押下追跡、urgent reset/epochを実装した。
Questでは独立したWayland clientが実際のkeymap FD/evdev/modifiers/repeat/enter/leaveを受信し、
両layoutと資源回収が成功した。通常SDKの実footセッションでも、
固定合成入力のrepeat重複抑止、Shift保持中のreset、古epoch拒否、再フォーカス後の入力が成功した。
旧runtimeの破棄197msと通常UI復帰も確認した。

検査によりSmithayのlegacy keymap FDのNUL不足を検出し、
Androidの一般一時directoryに依存しない匿名FD経路と合わせて固定patchへ反映した。
Build67で生じたStopとframe送信の競合も修正し、68のfoot回帰が成功した。
[失敗・切り分け・検査範囲・再現command](keyboard-checks.md)を記録した。
Rust85test/Clippy/fmtが成功。物理Bluetooth/文字視認は未受入として残し、I03以降へ進む。

## 通常SDKの連続受入検査（Build99）

装着による追跡復帰後、下記11ケースが同じAPKで連続成功した。各caseのnonce付き正本receiptをPCが照合し、通常UI復帰まで確認した。

| case | 検証artifact |
| --- | --- |
| surface | `20261105T235159-42baff94` |
| pattern | `20261105T235209-143b0d8b` |
| surface-cycle | `20261105T235216-622eaf48` |
| layout | `20261105T235226-94c7d967` |
| two-windows | `20261105T235243-e7ab9a89` |
| resize | `20261105T235302-e853dce0` |
| pointer | `20261105T235314-2fbaa7b4` |
| keyboard | `20261105T235326-a60b10c0` |
| foot | `20261105T235335-2977520f` |
| ray | `20261105T235342-53a00470` |
| popup-clipboard | `20261105T235349-b985d67a` |

APK SHA256: `b50a5adffd2faebfcbda7831de276fc428bd9d7dc17fb77e84068b7189a136ec`。
source digest: `0a2c4030cdafae239ac703a991b0c7a96d0f63e758c13da507cd00f44dd6ea4c`。
二枚時間窓のアプリ由来LCntはlayout/two-windowsとも観測済み。SDKの実geometryと画像・入力・世代の独立性を確認し、物理操作とは区別する。
debug/release・lint・package、実APKのPython61件が成功した。Rustは前回成功の108件からsource変更なし。
終了後に検証用foot/Waypipe/producerが残らないこと、元のTermux bash/sshdを維持したことを確認した。
設定隔離を使うcaseは、製品設定digest不変と新しい製品runtimeへの復帰もPCで照合した。

自動検証で見つけた早期stage記録、旧SceneのEntity寿命、pending journal時の再接続、診断quadとUIの重なり、中央rayの条件は各検査文書へ記録した。失敗した旧runを成功へ書き換えていない。
Boundary回避は今回使っていない。次回実際に妨げた時にだけ試す条件と、変更・復元の記録方針は[事前検証記録](native-preflight-20261104.md#2026-11-05の追跡復帰と次回のboundary対応)を参照。

**Phase 5全体はまだ未完了**。残りは[最終実機確認](phase5-manual-acceptance.md)のHMD視認・物理keyboard/controller・Menu/sleep・背景/grabである。task/check全体の状態は、その回答を受けて更新する。

## 手動確認の開始とpassthrough修正（Build99/100）

ユーザーがBuild99で二枚のfoot表示と文字の可読性を確認した。
一枚目の上下黒帯は1280×720画像を1024×768 producerへletterbox表示した結果で、1024×768選択で除ける。
背景はAPI上passthroughでもHMDでは青黒い単色だったため、Build100でpassthrough用backfillを透明にした。
通常起動後、ユーザーが現実の部屋の表示を確認した。Boundary変更無し。
この修正はユーザー指定のquick試行で、追加suite・release・CI・pushは未実施。Build99の全自動成功と区別する。
残りの背景往復/grab/配置保存・物理入力/Menu/sleepを確認してからPhase5の完了判定を行う。

## Build101の配備と後続機能（2026-11-06）

通常のNeovim起動を直すため、launcherで実contextの二段execを一度probeし、直接execできるときだけ
Termuxのsystem-linker modeをその子環境内で無効にする。LD_PRELOADと明示的なforce設定は維持する。
launcher20件、Rust transport26件とClippy、debug/lint/package、release assemble、実APKのPC61件が成功した。
Build101（SHA256 `1658f6cabbd2637fec54ce018d7434d9345d3c3daec791ffb5f9f681219649b0`）と
同一sourceのlauncher・producerを配備済み。source digestは`60115f9773bb9d32d0163a6ee5e097186afc17a140a53b382d3a1daa495c49ed`。

配備後の通常SDK layout検査は`20261106T015736-2a67dde3`でready待ちtimeout。
snapshotはnative ReadyだがActivity非表示、XR IDLE、Scene無しだった。layout実動作の不合格とは切り分け、成功にも数えない。
無線ADBも一時的にofflineになった。新launcher環境でのNeovim実機再検査は通信復帰後に続ける。
電池切れ前のsession216 journalは保持し、正常な再接続の監査に任せる。古いPIDを新bootで同一所有者と扱わない。

ユーザーの依頼により、テーマ・三重リング・workload連動をPhase 6全耐久/release完了より先に追加する。
追加機能の実装・自動検証を先行し、必要なユーザーHMD/物理操作は最後にまとめる。既存Phase 5の未確認条件は維持する。

## テーマのhost基礎（TH00/TH01、2026-11-06）

`native/theme`に制限付きTOML/CSS、resvgによるpremultiplied画像化、bundle検証、
共通pixel予算と装飾32MiB予算、window単位の独立時計・補間・再生状態を追加した。
builtinの三重リングは12/18/8秒・逆方向を持ち、focus/負荷通知・同一定義reloadで回転をresetしない。
動きを減らす設定、非表示pause、欠測とhold-last、workload切替での値破棄をhostで検査した。
`ContentViewport`はapp論理サイズを維持し、装飾canvasと描画/入力座標を共有する入口となる。

全host suite、workspace Clippy/fmt、設計参照検査が成功。theme13件、model31件を含むRust検査と
PC55件・launcher20件を実行した。PCの実APKを要する6件はこのtheme checkoutでは未実行で、Build101の61件成功とは別に扱う。
GLES/SDK/通常UIへの接続とQuest上のリング表示はまだ未実装・未確認。
後続のTH00〜TH05は[追加設計と残件](design/theme-animation.md)に記録する。Phase 5全体の完了判定は変更しない。

## TH00/TH01/TH02: hostの装飾合成・時計・reload worker

2026-11-06。`native/render`へ任意角度のpremultiplied装飾を追加。背景/app/前景の3passで
装飾Linearと端末Nearestを分け、透明外周・popup clip・無効化後の通常表示を実GPUで検証した。
Render ownerのwindow別時計はSurface寿命と分離し、二枚の静止appで装飾だけを更新する。
動きを抑える設定/無効化では静止画像のpresentを止め、appのFrameSeqは変更しない。
`native/theme::loader`は単一worker/最新要求/世代検査でreloadを準備する。
詳細と未接続部分は[テーマ設計](design/theme-animation.md)。SDK alpha/ray、runtime import/設定、
負荷collectorとHMD受入れは未完了。テーマは既定OFFで、通常UIからはまだ有効化できない。

Build102のdebug assemble/lint/APK検査成功。host全suiteはRust130件、PC55件（APKが必要な7件は別実行）、
launcher20件。Build102を使ったPC62件は全成功し、テーマlicense改変の拒否を含む。
実GPUは15+21点のreadback成功。QuestはWi-Fi ADB接続不可で未配備、release/実機受入れはこの結果に含めない。

## TH00: SDK Surface/ray部分検証の準備

Build103へdebug専用`theme-surface`を追加。mesh/layerの固定色producer readback、
実SDK ray、NoCollision後の背後到達を独立して記録する。SDK alpha/HMD未確認のflagを維持する。
debug/release assemble・lint・APK検査とPC64件が成功し、releaseのDEXにprobe classが無いことを検査した。
Questへ導入してcaseを実行したが、Activity非表示・XR IDLEでready待ちがtimeoutした。
entity作成前のため透過/rayは未判定。これは透過の実機合格やTH02完了ではない。

## TH03/TH04: PTY対応付け・負荷観測・tmux adapter

2026-11-06。TerminalId付き起動、実PTY credential登録、版付きtelemetry、同UIDのCPU/RSS collector、
tmuxの登録socket/共有session対応を実装。起動receipt・登録・mapを別eventとしてjoinし、
欠測を0にせず、busy/EMA/staleとworkload切替時のhold-last破棄をRender ownerへ接続した。
control/leaseを待たせない最新一件の送信と、観測PIDを終了権限へ追加しない境界を検査した。
host全suite Rust139・PC57（実APK用7件は別）・launcher32成功。実PTY/SSH、host実tmux二client/二pane成功。
Build104のdebug/release assemble・lint・APK検査、実APK PC64成功。同版のAPK/launcher/producerを配布した。
Questの二footを別PTYへ対応付け、73秒・七段階のresize/表示切替/close/Stopがnative instrumentationで成功。
専用PTY二つからtmux 3.8の同sessionへattachし、二paneの負荷を重複なく報告、固定CPU負荷で約0.95 coreを確認した。
probe所有資源の回収も成功。APK window内のtmux切替とHMD表示は未確認で、テーマはまだ通常UIから有効化できない。
実装契約と上限は[テーマ設計](design/theme-animation.md#terminalidcpursstmuxの接続)。

Quest再接続時、Termuxとservice supervisorが停止しておりSSH接続できなかった。
既存Termux Activityと`service-daemon start`で復旧し、設定/host keyは変更していない。
同UID proc読み取りとtmux/footclient機能を確認した。既存launcherのNeovim直接exec修正も、
専用PTYで起動・終了成功、headless成功、LD_PRELOAD保持を確認した。実foot内の通常起動・物理キー受入れとは区別する。

## Build105/106: テーマの取り込み・保存・SDK接続（2026-11-06）

通常UIから装飾なし/Cyan Orbit/取り込んだテーマ/ZIP/動きの抑制を選択できるようにした。
CPU compile、GPU候補の採用、設定のatomic保存を別ownerへ分け、取消・不正ZIP・GPU予算不足で最後の正常themeを保持する。
size省略SVGの基準寸法と、SDK panel作成失敗後に装飾を無効化して復旧する経路も追加した。
表示面はpremultiplied/NoCollision、入力面はcontentだけの透明meshへ接続した。実SDK/HMDの成立は未確認。

Build106はdebug/release assemble・lint・APK境界検査・ZIP JUnit 2件、host Rust 150件・PC 58件（APKなしの7件skip）・launcher 32件、
実APK指定PC 65件が成功。Questのtheme-runtimeは7段階の実EGL upload、不正reload時の保持、動き抑制、
無効化でGPU装飾0byte、runtime再生成後の設定/import復元、Stop/worker/GL回収に成功した。
これは専用private領域を使うnative検証であり、製品設定・SDK描画・物理入力の合格ではない。
Build105のSDK probeはActivity非表示・XR IDLEでready期限となり、entity作成前で未判定。

試行錯誤:

- SDK Kotlin compileのnullable Future/SceneObject型/Surface property、Rust Clippyのstyle指摘を修正して再build。初回ログは保持した。
- 前回mainのCI run 42093209538はclipboard fixtureの独立Cargo.lockがtheme依存に追従せず、locked buildで失敗。
  rootと同じ依存版/checksumへ更新し、実Waypipe clipboard検査を再実行して成功。失敗ログの末尾をCIにも出すようにした。
  Waypipe中継全体のtimeout成立は引き続き別問題である。
- Build106のhost全体検査でpopupの共有メモリ解放が一度遅延し、後続の共有test lockもpoisonした。
  単独・全体再実行は成功。同梱SmithayのShm dropping threadによる非同期解放を確認したため、
  検査側に期限付きの後処理待ちを追加する。

再現入口と現在の制限は[theme-animation.md](design/theme-animation.md)。
厳密なsource/APK hashと失敗を含むrawログは作業rootのeidolon-theme-reviewに保存する。

## Build107〜110: 負荷計測の切替・装飾二窓・自動復旧

負荷計測の停止/再開を通常UIと保存設定へ追加した。停止時は/proc全走査とtmux照会を止める。
PTY登録と同じSSH接続を保持し、再開時のCPU差分だけを取り直す。観測対象のshell/tmuxへsignalを送らない。
同じcreate要求への装飾panel作成失敗が確認できた場合、装飾を無効化して通常canvasへ戻す。
import元を保持し、切り戻した理由をUIへ表示する。旧世代の通知は復旧の引き金にしない。

再現用`theme-windows --instrumentation`は実foot二枚と装飾の独立更新・resize・三枚目への切替・close、
73秒の継続、四条件の性能比較、合成したadapter失敗からの復旧、Stop/資源回収を検査する。
draw+swapの時間分布はCPU側のwall timeであり、HMDのpresent latencyやGPU timestamp測定ではない。

試行錯誤と修正:

- Build107はresizeの確定前にfixtureが旧寸法のSurfaceを再作成し、後から退役した世代のackが拒否された。
  検査をlogical寸法の確定→旧Surface回収→新Surface接続の順にした。製品側の旧世代拒否は維持する。
- Build108の初回はpackage検査のstderrに出たCargo lock待ちがJSONへ混入。標準出力と標準エラーの保存先を分けた。
- Build108の再試行は装飾二窓の七段階/73秒を通過後、観測停止時にprotocol拒否。
  collectorのepochを停止/再開のたびに変えたことが原因だった。同じSSH streamのepochを維持し、内部の設定世代とCPU基準だけを更新した。
  受信側のsession/nonce/epoch検査を緩めず、同じ接続でunavailable→再開を検証する回帰を加えた。
- Build109はSurface接続の一回目が不受理で検査が停止。正確な戻り値は未記録だったため原因を断定しない。
  製品adapterと同様、同じcreateが有効な間だけSTALE/BUSY/NOT_READYを期限付きで再試行し、未知statusと退役したplanは拒否する。
- host popup fixtureは非同期のSHM解放を固定10回dispatchで待っていた。資源条件を満たすまで最大3秒待つ方式へ修正。
  launcherの正常終了fixtureも製品と同じ3秒graceへ揃え、SIGTERMを無視する故障fixtureの短い期限は維持した。

失敗したrunも削除せず、source/APK識別子とともに作業rootの`theme-validation107.json`以降へ記録する。

Build110はdebug/release assemble・lint・package・ZIP JUnit2件、実APK PC66件が成功。
実機の保存結果では七段階/73秒、観測停止・再開、四条件、合成adapter失敗→Off/通常二窓、Stop185msと再生成後の回収まで完走した。
ただし静止リングの測定区間が31,864msへ延び、ADB読出しとforce-stopがtimeoutしたため、**host総合判定は失敗**。
後から回収した端末のpassだけで昇格しない。他の区間は約6秒、静止二条件は新規draw0回、回転二条件は両窓とも244〜253回、
回転中の装飾texture uploadは増えていなかった。これらは参考観測であり、揃った性能比較やHMD性能の合格ではない。

休止を切り分けるBuild111では、性能caseだけに最大5分のCPU wake lockとfinally解放を追加。
releaseの権限検査を加え、元の失敗とcleanup失敗を両方保存するようPC runnerも修正した。
一時装着通知によるSDK再検査も準備したが、最初の電源状態取得がtimeoutし、装着通知とcase起動には到達しなかった。
Guardian/永続設定は変更していない。SDK透過・ray・HMD受入れは未判定を維持する。
Build111のdebug/release assemble・lint・APK検査、ZIP JUnit2件、実APK PC66件は成功。
releaseにWAKE_LOCK権限がないことも確認した。端末との再接続後に配備・性能比較・SDK検査を再開する。

## Build111の実機比較成功とnative検査の休止条件

継続時に同一端末の応答が復帰し、Build111を導入した。`theme-windows`は七段階/73秒、四条件各6秒、
観測停止/再開、合成adapter失敗から同じsessionの二窓へ復旧、Stop193msと資源回収までhost判定も成功。
CPU wake lockの獲得・終了時保持・解放も確認した。静止条件は再描画0回、回転中もapp contentは不変で装飾uploadは増えなかった。
約42回/秒、draw+swapのp95上限4msというnative結果を[測定表](design/theme-animation.md#観測の停止とnative性能比較)へ保存した。
通常SDK/HMDの表示頻度や性能保証とは区別する。

続く`theme-runtime`はreducedまでの四段階を採取後、ADB読出しと後処理がtimeoutし、未受入れ。
元のerrorとcleanup-errorが別々に保存される修正は機能した。Build112ではnative instrumentation全体を
最大5分のCPU wake lockで保護し、finallyで解放する。通常Activity/SDK・実sleep/復帰へは適用しない。
装着を模擬する単発prox_closeと対のprox_farもtimeoutしたため、当初は受理と復元を未観測と記録した。
接続復帰後のBuild112試験でprox_farのACKとGET_PROPERTY前後一致を確認し、復元済みになった。
Guardianや永続propertyは変更していない。

### Build112: native検査のCPU休止対策とSDK実交差の切り分け

debug/release build・lint・package・ZIP JUnit2件、実APK指定PC66件が成功し、Questへ配備した。
`theme-runtime`の7段階、不正reload時の保持、無効化、import/設定復元、Stopと資源回収が成功。
CPU wake lockの獲得・終了時保持・finally解放も確認した。

SDK `theme-surface`初回は新nonceが記録されずtimeoutした。明示的なActivity起動後は実際の
Ready/visible/FOCUSEDに到達し、producerのRGBA/GL資源回収は成功したが、中央rayのfront判定で失敗した。
通常起動の再試行でも再現したため、非表示待ちと区別する。
既存の[ray校正記録](ray-coordinate-checks.md)には垂直segmentがnullになり、同じ交点を通る斜めsegmentが成功する実測がある。
Build113のprobeはこの方式を採用し、collision準備を期限内で待ち、実hitを失敗ログにも残す。
contentのUVに加えてworld交点もPCで照合する。中央の検証や座標の許容幅は省略しない。

### Build113〜114: probe配置の重なりとSDK Mesh URI

斜めsegmentだけでは不十分で、Build113は中央がback、余白が別の操作UIへ当たった。
操作UI中心x=-1.45m/幅1.2mとprobe右余白x=-1.85mが重なっていた。
Build114でprobeを右側へ移すと、中央/透明余白ともfrontに当たり、NoCollision後は両方ともbackへ到達した。
元の中央がbackだった内部理由は断定しない。余白を透明clearするだけでは入力を透過しないことが実測できた。

続くcontent入力mesh生成は`MeshManager.kt`の`Scheme not implemented yet`でクラッシュした。
実SDK 0.15.0 AARのMeshManagerは登録済みcreatorを`mesh` schemeで解決する。
独自の`eidolon://`ではその分岐へ入らないため、製品とprobe共通のURIを
`mesh://eidolon-theme-content-hit`へ修正した。Build115で実機再検証する。
Build114までのdebug/release・lint・package、実APK PC66件は成功していたため、コンパイル成功とは区別して記録する。
クラッシュ前の`running` snapshotを成功とせず、後から採取したPID限定logcatと照合した。

### Build115〜117: 非描画の入力形状を実SDKで比較

Build115でschemeによるクラッシュは解消したが、content入力meshへの交差は成立しなかった。
Build116でLineTest_IgnoreVisibleへ変更しても、中央と中央以外の既知点がbackへ当たった。
可視性だけで解決したとは扱わず、Build117のdebug経路で形状を比較した。

| 入力候補 | 実機での結果 |
| --- | --- |
| 厚み0の自作mesh、depth test ALWAYS | 中央・既知点ともbackへ到達 |
| 厚み1mmの自作二面mesh | 入力Entityへ到達するが交点のzが期待から1mmずれる |
| SDK標準box、厚み1mm | 中央・既知点が入力Entityへ到達、余白はback。UVとworld交点が既存許容幅内で一致 |
| SDK標準panel、非表示・IgnoreVisible | 入力Entityへ到達するがUVが期待値とずれる |

比較probeの総合結果は、製品baselineが成立していないため失敗のまま保存した。
Build118ではSDK標準boxを採用し、color/depth書込みを両方無効にして非描画の入力面とする。
SDK内部のBVH原因は断定せず、平面と厚みありの実測差を採用根拠にする。
probe全体のmesh/layer回帰と、通常SDKで装飾付き二枚を検査する`theme-sdk`を追加した。
このケースは隔離した設定内でテーマを有効にし、resize、実ray/key、片側喪失、表示切替、close、
実Activity再生成、設定復元を行う。終了時は通常の配置設定とテーマ保存fileが不変であることも照合する。

## SDKの入力面と表示面の実機修正（Build118〜120）

Build118でSDK標準の薄いboxによるcontent中心・UV・world交点・余白の背後到達・削除後の非交差が成功した。
layer比較はSDKの内部正規化を検査側が考慮しておらず失敗。SDK 0.15.0のALPHA_BLEND layerは
表示meshをTRANSLUCENT_HOLE_PUNCHへ変更するため、mesh/layer別にalphaとblend factorを照合した。
Build119の`theme-surface`は両方式で成功した。SDK合成後の透明な見え方やHMD確認は含めない。

通常SDKの`theme-sdk`では一枚のpose/resize/key入力が通る一方、二枚目の物理サイズ・pose ackが成立せず停止した。
失敗直前のsnapshot保存を追加し、実際のFOCUSED状態で二枚とも描画中と確認した。
AARのPanelとHittableはともに`R.id.mesh_hittable`属性を使うため、Hittable(NoCollision)の後に
既定Panelを追加するとLineTestで上書きする。Build120では`Panel(id, NoCollision)`で生成し、
adapterの実属性も検査する。非表示への移行時は入力面をNoCollisionへ戻してからSurface解放を待つ。

PCの空き容量不足でBuild119のrelease APKコピーと検査前のAPK取得が失敗した。
同じSHA256のAPKコピー27個をhardlinkで共有し、約2.3GBを回収。試行ログ・APKの内容・参照pathは保持した。
共有した成果物をin-placeで書き換えない。復旧後のrelease package検査は成功した。

Build120の通常SDK回帰は5段階＋二窓10段階すべて成功した。一枚のresize/pose/key、二枚での入力切替、
片側Surface喪失中の他方の入力継続、復旧と旧世代拒否、三枚目の表示切替とShift解放、close、
実Activity再生成、再接続とテーマ/配置復元、検査前の製品設定への復帰を確認した。
PC67件、debug/releaseのbuild・lint・package、JUnit2件が成功。
最初の起動では新しいruntime snapshotが得られず、保存済み旧版のReadyを合格とせずtimeoutした。
同APKの次の起動で実Ready/FOCUSEDを確認し成功した。HMD光学・物理入力の合格とは分ける。

## 通常SDKの四条件比較を含む回帰（Build122）

Build121では装飾なし・静止・回転・回転＋計測を各6秒、通常SDK上で測定できたが、後段の合成clickが
入力ticket公開前に送られる可能性と、旧Entity上の合成hoverの残存を検査が考慮していなかった。
Build122で同runtime/input epochのticketを待ち、測定前に専用sourceを解除・回収した。
`theme-surface`のmesh/layer両方と、四条件比較を含む`theme-sdk`の5＋10段階が合格した。

回転時は二枚とも約49.48 draw/秒、p95上限は2〜3ms。app画像と固定素材のupload回数は各区間で不変。
装飾なし/静止時の追加drawは0。通常SDKを含むprocess CPUは約1.14→1.32 core、RSSは約333→348MiBで、
検査処理の負荷を含む一回の比較である。draw+swap wall timeをHMDのrefresh rateや遅延として報告しない。
詳しい値と再現CLIは[テーマ設計](design/theme-animation.md)に記録した。

Build122のdebug/release build・lint・package検査、JUnit2件、実APK指定PC68件が成功。
物理操作とHMDの見え方は最後の受入れとして残し、Phase 5/TH05を自動完了にしない。

## 装飾なしの静止panel回帰も完了（Build123）

Build122の装飾なし検査は、最新画像と実geometryを適用済みでも累積presentが2回で止まり、旧検査の3回待ちで失敗した。
静止中の追加drawを止める製品動作と矛盾するため、plainの準備判定を「最新contentのpresentが1回以上」へ修正した。
回転themeの複数frame条件は維持する。第三windowを閉じた後の残ったwindowも、実際のSDK ray/keyを送って
focus・キー到達・画像更新を検証し、入力のない静止panelへ無意味な再描画を要求しない。

Build123では`theme-surface`両mode、四条件性能比較を含む`theme-sdk`、装飾なし`two-windows`がすべて合格。
後二つはそれぞれ5＋10段階、製品設定hash不変、通常UI復帰まで確認した。
debug/release assemble・lint・package、JUnit2件、実APK PC68件も成功。APK123をQuestへ導入済み。
一時装着通知は最後にprox_farで戻し、永続設定・Guardianは変更していない。

## 停止・接続設定・観測基盤（Build124〜128）

Phase 5/TH05のHMD確認を最後に残し、Phase 6の独立部分を先行実装した。
L01/L02/C02とL03/L04の測定基盤は実装済みの範囲があるが、各taskは`in_progress`を維持する。

- 停止: SDK Entity/listenerを正しい世代で記帳し、native/SDKの回収後にだけ停止完了とする。
  10秒timeout時は未回収ownerを残し、新runtimeを追加しない。古い/重複ack、queue飽和、遅いackをhostで検査。
- 接続障害: host専用SSH peerを20回終了させ、所有thread/forward回収、明示再接続、旧SessionId拒否、pin維持を確認。
  Questの障害行列・物理Menu/sleep/rebootの受入れとは区別する。
- 初回設定: 公開鍵コピー、username/host key入力、指紋確認、保存前停止、古いformの上書き拒否を追加。
  Termuxの`eidolon-pair`は既存authorized_keysを保持する。表示名は`Eidolon`。
- 配布: `build-release`でunsigned APKと同buildのTermux toolsを作る。署名・release実機受入れは残る。
- 観測: `quest-observe`でbattery/温度/PSS/FD/CPUと資源counterを記録する。欠測・更新停止・process変更を識別し、
  採取完了を長時間試験の合格にしない。使い方は[起動・接続手順](usage.md)を参照。

### 失敗から修正まで

Build125の通常SDK停止は、GL終了後もSDKが個別detach ackを待つためtimeoutした。
描画threadの終了とnative参照ゼロの両方を確認した場合にもSDKを解放するよう修正し、Build126でstop20が成功した。
ただし切断後FDは293→351へ増加。keyboard単独のfixtureでは再現せず、data deviceを作ってreleaseなしで切断すると
keymap FDが0→1となった。独自clipboardとSmithay 0.7のdevice登録の二重管理による強参照循環を修正した。
同じhost fixtureとBuild128の全停止反復で、対象memfd FD/mapがゼロへ戻ることを確認した。

### Build128の検査結果

| 検査 | 結果 |
| --- | --- |
| Rust全suite / fmt / clippy | 156件成功、警告なし |
| 実APK指定PC検査 / Termux launcher | 73件 / 33件成功、skipなし |
| debug・release assemble / lint / package | 成功。JUnit各4件成功 |
| 通常SDK stop20 | 成功。受付最大3ms、全停止191ms、切断336ms |
| FD追加採取付きstop20 | 成功。受付最大3ms、全停止196ms、切断349ms |
| 実Keystore credentials | 保存・0600・pin確認/旧form拒否・製品設定不変が成功 |
| theme-sdk / two-windows / popup-clipboard | 成功（最後はinstrumentation） |
| 観測器60秒smoke（Build126） | 13/13採取、同process・診断更新を確認。長時間受入れではない |

Build128のsource digestは`1af62ad95dbe59bb6c121de0588ffad7eca8ff0fa0c6527ed38b026f9aa15fa6`。
debug APKのSHA256は`0766d7114ecde896082a8b8dcaf583db1ce06c53852a43c51013c08e07c89ba4`、
unsigned releaseは`295b88341b1f776efd02982c1c51ab9e090b7866624242da55468bace4751f20`。
Build128をQuestへ導入済み。通常のGuardian案内で可視性が得られず、事前承認された一時pauseで実SDK検査を行った。
各runのfinallyで元値（空）とprox_farへ復帰。HMD/パススルーの光学的受入れは含まない。

### 残る資源増加

キーマップ修正後も切断後の全FDは294→312、別runでは293→313へ増えた。
1秒間隔の内訳採取で、`/sys/devices/virtual/kgsl/kgsl/thread/<tid>/active_time`が増えることを確認。
反復後には44個中41個のTIDが当該processのtask一覧になかった。30秒待機後も全FDは約318で維持された。
当初の「終了済みthread」という分類は、別processのTIDを区別していなかったため訂正する。
このpath文字列とGPU計測symbolは同梱`libMetaSpatialSDK.so`にあるためSDK内部計測を疑うが、呼出し元stackは未確定。
製品側は既にGL owner上でcontext破棄と`eglReleaseThread`を実行している。
他ownerのFDを強制closeせず、SDK側の切り分けとL04のFD基準達成を残件とする。

再現ログは設定済み作業rootの`eidolon-phase6-implementation/`に保持。
通常SDKのrunは`20261106T094817-cb089347`、認証は`20261106T094926-d6e8ef3f`、
テーマは`20261106T094933-9583da3e`、二窓は`20261106T095025-e05d445f`、
clipboardは`20261106T095041-e3d66753`、FD再採取は`20261106T095403-546d4a7e`。
残りはSDK計測FDの調査、Questの障害行列、実時間の給電/soak、署名release検査、最後のHMD/物理操作。
キーボード検証の中断とFキー免除は維持する。

## GPU計測FDを製品処理から分離する準備（Build130）

SDK 0.15.0のGPU計測にはTIDごとのfile cacheがあり、終了したthreadは計測loopから除外される一方、
既存cacheからの消滅TIDの回収が見当たらなかった。これは前回観測を説明する有力な原因だが、実機での因果確認は別に必要。
[独立比較](gpu-fd-investigation.md)として、SDKなしinstrumentationとSDKだけの空Sceneへ、同じEGL Pbuffer処理を追加した。
製品native/SSH/Waylandをloadせず、空thread・別GL thread・同一GL thread再利用を比較し、30秒後の作成TIDのFDを数える。

Build130のdebug/release assemble・lint・JUnit・同梱/入口検査、実APK指定PC76件が成功。
新しい比較入口はdebugだけに含まれる。接続中の製品を停止しないpreflightもhostで確認した。
この時点ではWi-Fi ADBがofflineとなり、対象接続とPC側serverの再起動でも直ちには戻らず、二条件比較は未実施だった。
SDKのFDを外からcloseする処置、releaseの変更、L04受入れ完了は行っていない。

## GPU計測FDの独立再現（Build131）

接続復帰後、collectorの空JSON読取待ちと検証用ActivityのMAIN/VR・singleTask宣言を修正した。
Guardian前面による可視性失敗を記録した後、許可済み一時pauseで二条件とも完走した。
SDKなしでは作成TIDのGPU計測FDが0、SDKありでは別GL thread 10本で10個、同じthreadの10反復で1個残った。
各threadはjoin済み、EGL解放成功、30秒待機後の結果。空threadは両条件で0だった。
製品native/SSH/Waylandを使わずSDK条件だけで再現した。詳細と限界は[調査記録](gpu-fd-investigation.md)。

Build131のdebug/release build・lint・JUnit・同梱検査、実APK指定PC77件は成功。比較入口はreleaseに含まれない。
debug APK SHA256は`e678cbef5b52875d75c3c0f840ba05476697a3e20fd5fd459d4c38f6927ca9d5`、
source digestは`58933ec71f291e130e70ab41a7e154160bf6d8a4ee079c6023b659825e4f442f`。
実測は設定済み作業rootの`eidolon-sdk-gpu-fd-20261106/checks/20261106T110132-7f8141df/`に保存。
Guardianを元値へ戻し、通常UI Ready・SSH Idle・Termux sshd稼働を確認した。
原因切り分けは完了したが、SDK cacheの修正、release実測、全FD基準達成と長時間受入れは残る。
