# 配置・背景の保存検証

2026-11-05、I06実装中。製品UI・SDKへの適用を接続し、Build89でAndroidの保存・復元が成功した。
通常SDKのgeometry・実Activity再生成と、HMD・物理操作の受入れは残っている。

## 保存する値

schema 1のJSONへ背景、US/JIS選択、app_idと表示枠に対応した配置・物理寸法・logical寸法を保存する。
session、WindowId、Surface世代、SDK Entity IDは保存DTOに含めない。
最大64 KiB・16項目とし、古いapp/slotの項目から置換する。
同じapp_idが複数ある場合は復元先を推測せず、表示枠の既定配置へ戻す。
その状態での移動は現sessionで使えるが、曖昧なapp_idの配置を上書き保存しない。

位置は有限値でx/z ±10m、y 0〜4m、回転は有限の単位quaternionとする。
SDKとの受渡しは`rotation_xyzw`と明記し、qと-qを同じ回転として扱う。
希望poseとSDK適用済みposeを分け、ackは全target tupleとpose commandに束縛する。
sceneやSurface世代が変わると適用済み状態を失効する。
背景のackもsceneとbackground commandに束縛し、背景切替でSSH sessionを作り直さない。

## ファイル保存と復旧

Coreの`preferences_dir`へAndroidのnoBackupFilesDirを渡す。
その下の`workspace/layout.json`を正本とし、directoryは0700・ファイルは0600。
別名の新規ファイルへ書込み・fsyncし、renameとdirectory fsyncで確定する。
途中で残ったtempファイルから復元せず、最後に確定したJSONを読む。
symlink・他UID・不適切なpermissionは拒否し、未知schema・破損・上限超過は既定値と原因codeを返す。
保存失敗で接続を停止せず、modelの希望値と保存結果を別に報告する。

ユーザー操作の確定値から保存を予約し、SDK ackだけを保存の契機にしない。
250msごとに最新設定へまとめ、停止時も未保存分を処理する。
モデルのMutexを解放した後にファイルI/Oを行い、JNIのsnapshot/enqueueをfsync待ちへ参加させない。
保存失敗は原因codeを残し、次の設定変更で再度保存する。

## 現在の検査

```bash
cargo test --locked -p eidolon-model
cargo test --locked -p eidolon-core
cargo clippy --locked --workspace --all-targets -- -D warnings
```

最新のhost全suiteはRust106件（model 29、render 9、lifecycle 41、transport 26、fixture 1）、Clippy/fmtが成功した。
保存DTOのschema/範囲/重複/上限、同app二枚の曖昧性、旧世代pose ack、背景の旧scene ack、
途中temp/未知schemaからの復旧、symlink先を変更しないこと、巨大JSONからの復旧を検査した。
最初のmodel検査は小数リテラル`.02`の記法誤りでcompileに失敗し、`0.02`へ修正して通した。

SDK 0.15.0の実artifactを調べ、Pose/Quaternion、Grabbable/GrabbableType、Scene.setBackfillColorを確認した。
public JVM methodだけでKotlin製品APIと決めず、Android buildで利用可否を検査した。
`isSystemPassthroughEnabled()`を背景の現在値getterとして使わない。
SDKの実geometryへの適用確認、grab中のWayland入力抑制、背景切替・保存/復元の実機検査は残件。

## SDK接続とBuild85の検査手順

製品HostからnoBackupFilesDirを`preferences_dir`へ渡し、背景/USJIS/配置選択と編集UIを接続した。
SDKのQuaternionコンストラクターはwxyzで、保存DTOのxyzwとは順序が異なることを実artifactで確認した。
GrabbableSystemは移動中に親を変更するため、絶対poseを読み書きする。
編集モードではWayland入力を止め、終了・非表示・破棄時には追跡したSDK sourceのgrabも解除する。
希望poseを毎tick上書きしてgrabを巻き戻さず、最新の移動eventへまとめてCoreへ返す。
SDKの実quadに対する二点ray/world/UV照合が通ってからpose ackを返す。
背景は単色backfillとpassthroughのAPI呼出し後にscene/command付きackを返し、光学的な確認とは区別する。

初回Kotlin API検査では、絶対poseの関数を拡張メソッドとして呼んでcompileに失敗した。
公開top-level関数として呼ぶ形へ修正し、Kotlin API検査が成功した。
最初のBuild85は新しい検査caseのhelperに閉じ括弧がなくcompileで失敗した。
helperをblock形式と明示的な戻り型へ直し、同じversionCodeの未導入版を再buildした。

```bash
EIDOLON_ARTIFACT_ROOT=/absolute/private/work/builds scripts/build-android 85
adb -s "$QUEST_SERIAL" install -r /absolute/private/work/builds/RUN/eidolon-debug.apk
scripts/quest-install-shm-fixture --serial "$QUEST_SERIAL"
scripts/quest-check --serial "$QUEST_SERIAL" --case layout --instrumentation --timeout 180
```

`layout`は専用nonceのnoBackup fixtureだけを使う。
実footでposeの希望/合成ack、サイズ、背景でsession維持、Shiftを保持してUS→JIS変更、
同app二枚で曖昧な配置を保存しないこと、runtime再生成での復元、新sceneで旧ack拒否、破損復旧を検査する。
pose/背景ackとscene通知は合成し、SDKの表示/Activity再生成/HMD確認はこのcaseの合格には含めない。
実機結果は以下のとおり。

## Build85の実機結果

`20261105T201700-6f9e8fc6`でnativeの10stageが成功した。
新しいSSH sessionへ接続し直して保存したpose・800×500 logical size・1.44×1.08m・virtual背景・JIS配列を復元した。
二回のfoot停止は171/175msで、破損検査の三つのruntimeも終了・回収できた。
未知schemaは`LAYOUT_SCHEMA`、範囲外poseとtruncated JSONは`LAYOUT_INVALID`で既定値へ戻った。
APK SHA256は`ec675243497dc390a5cffd98cd0e2b53b20158ce16e753128bdb069e5a3b0b8e`、
source digestは`3c094cc2b8d6a5ba59312361e2c84cd872b1860d2e4326fe483578ddf702b265`。

同版のresize-native `20261105T201720-e20b8c61`、二枚73秒継続 `20261105T202006-71b14a5b`、
pointer-wire `20261105T202125-ab71bc2d`、US/JIS keyboard-wire `20261105T202130-1a1289c0`も成功した。
Rust105件・Clippy/fmt・launcher17件、debug/release・lint・package、実APKのPython56件が成功した。

## 通常SDK検査と保存writerの中断

通常SDK用の`layout`も実装し、Build86のcompile・debug/lint/packageが成功した。
通常Hostを検査専用のnonce設定へ切り替え、一枚の移動/回転/拡大とSDK ray→key、二枚のfocus、
実Activity.recreate、新しいSDK sceneへの再適用、runtime再生成後の保存復元を検査する。
終了後は通常の製品設定へ戻し、設定ファイルが変わっていないことも照合する。
背景API適用と実quadのgeometry検査を行い、物理入力・背景の光学的な確認は別に報告する。

```bash
scripts/quest-check --serial "$QUEST_SERIAL" --case layout --timeout 180
```

SDKケースはまだ実機未実行。GuardianDialogActivityが前面のため、成功扱いにはしない。
Build87では、hostの保存writerをtemp fsync直後・rename直前で待機させ、親が自分のchildへSIGKILLを送り、
最後に確定した設定を読み戻す検査を追加した。専用child fixtureは通常suiteではignoreし、親の中断検査からだけ起動する。
中断後に新tempが残っても、確定済みの旧JSONを読み、原因不明の中間状態を採用しないことを確認した。
SDK側では、Activity.close時にもScene参照を外す前にgrabを解除し、最後の移動eventを送るよう改善した。

## Build89の結果とActivity通知の世代

Build87のnative保存・復元 `20261105T204746-fa62f7a8`、US/JIS wire `20261105T204803-e364d74e`が成功した。
Build88では、Activity再生成で同じdebug intentを再処理しないようnonceを記憶し、
同じapp_idが複数あるときの保存範囲を製品UIへ表示した。
Build89では、古いActivityの遅いXR/visibility/focus通知が新しいSceneの状態を変更しないようにした。
停止済みActivityのkeyboard device callbackも入力を解除しない。
通常SDK検査には、実Activity再生成後に旧Sceneの通知を拒否する検査を追加した。

Build89のnative保存・復元 `20261105T210421-5bc60188`、US/JIS wire `20261105T210439-3613be83`が成功した。
APK SHA256は`68fec87d3151412a39357f85d20289d6f969438c2eec3360342ac7daad754e0a`、
source digestは`5e06b46227d2ddc36dd5f998ca972bf98bdc9cbb778c69c11ec0ef36dc5f353a`。
debug/release・lint・packageと実APKのPython58件が成功した。
検査用foot/Waypipe/producerは残らず、既存Termux bash/sshdは維持できた。

通常SDK検査はGuardianDialogActivityが前面のため`not_run`を維持する。
native検査はImageReaderと合成Scene/pose ackを使い、通常SDK表示・grab・実Activity再生成の成功には数えない。
