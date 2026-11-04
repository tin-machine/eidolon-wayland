# 製品実装の進捗

ゴールはPhase 5（I07）まで。2026-11-04のユーザー指定により、HMD視認と物理入力の受入れは最終段階にまとめる。実装・host検証・ADBによる自動試験を先に進める。手動検証を含むcheck全体は、手動部分が残る間`not_run`とし、自動部分の成功を別に記録する。後続実装は自動部分の成立を根拠に進め、taskの`done`判定は省略しない。

基準source commit: `5692f54bcea0fdff8849260eb5e19b1c5125ac35`。
raw artifactは作業開始時に読み直した`codex-work-root`の`eidolon-phase5-implementation/`配下。公開する記録に端末識別子・室内画像・秘密鍵は含めない。

| Task | 状態 | 検証と残件 |
| --- | --- | --- |
| B01 | done | Rust 1.95.0固定、model/android workspace、ID/上限、決定的build info、JNI 0.22.4。Rust test 3件・fmt・clippy成功。JNIの実機確認はB02へ分離 |
| B02 | in_progress | assemble/lint・APK/ELF検査成功。Quest Build 3→4のnative/Kotlin版一致、scene/panel、XR FOCUSEDを自動確認。Rust版変更→復元→APK版変更の4 buildがすべて成功。HMD視認はnot_run |
| B03 | done | runtime/JNI/診断と固定caseを実装。ホスト11件・Python10件・debug/release検査、Quest Build 8の10cyclesと停止後late panic・製品復帰に成功 |
| C01 | in_progress | host dispatcher/Gradle checksum/GitHub Actionsを実装、ローカルhostとAndroid Build 9成功。対象commitのCI結果を待つ |
| H01 | done | 製品runtimeの専用I/O workerからAPK内PIEを実行。host・Questで10echo/明示終了/回収、起動中stop、releaseへのfixture不在を確認 |
| R01〜I07 | planned | 依存順に実装。独立probeの成功を製品統合の成功へ置き換えない |

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
