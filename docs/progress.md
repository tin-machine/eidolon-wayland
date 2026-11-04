# 製品実装の進捗

ゴールはPhase 5（I07）まで。2026-11-04のユーザー指定により、HMD視認と物理入力の受入れは最終段階にまとめる。実装・host検証・ADBによる自動試験を先に進める。手動検証を含むcheck全体は、手動部分が残る間`not_run`とし、自動部分の成功を別に記録する。後続実装は自動部分の成立を根拠に進め、taskの`done`判定は省略しない。

基準source commit: `5692f54bcea0fdff8849260eb5e19b1c5125ac35`。
raw artifactは作業開始時に読み直した`codex-work-root`の`eidolon-phase5-implementation/`配下。公開する記録に端末識別子・室内画像・秘密鍵は含めない。

| Task | 状態 | 検証と残件 |
| --- | --- | --- |
| B01 | done | Rust 1.95.0固定、model/android workspace、ID/上限、決定的build info、JNI 0.22.4。Rust test 3件・fmt・clippy成功。JNIの実機確認はB02へ分離 |
| B02 | in_progress | assemble/lint・APK/ELF検査成功。Quest Build 3→4のnative/Kotlin版一致、scene/panel、XR FOCUSEDを自動確認。Rust版変更→復元→APK版変更の4 buildがすべて成功。HMD視認はnot_run |
| B03 | done | runtime/JNI/診断と固定caseを実装。ホスト11件・Python10件・debug/release検査、Quest Build 8の10cyclesと停止後late panic・製品復帰に成功 |
| C01〜I07 | planned | 依存順に実装。独立probeの成功を製品統合の成功へ置き換えない |

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
