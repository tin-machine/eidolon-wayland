# Phase 0残作業・Phase 1 — build、JNI、Surface

前提は[共通契約](contracts.md)。この文書のファイルは既存と明記したもの以外は新規作成予定。現行`MainActivity.kt`のCompose表示とpassthroughを回帰確認できる状態で増やす。

<a id="b01"></a>
## B01 — Rust workspaceと版情報の契約

作成: root `Cargo.toml`、`Cargo.lock`、`rust-toolchain.toml`、`native/model/`、`native/android/`、`docs/progress.md`。

1. toolchainは1.95.0、edition 2024、Android targetは`aarch64-linux-android`。必要なtargetとrustfmt/clippyを明示して導入する。
2. `eidolon-model`は通常library、`eidolon-android`は`cdylib`、lib名`eidolon_native`。JNI依存は`=0.22.4`。生成済みCargo.lockをcommitする。
3. `build_info.rs`は`schema=1`、source commit、dirty含むsource digest、APK versionCode、crate版、target、build profileをJSONで返す。
4. `build.rs`へ`rerun-if-env-changed`を設定し、APK版だけ変えてもnative版情報が更新されるようにする。git情報がない配布sourceでも明示入力で構築できる設計にする。
5. `nativeBuildInfo`を[公式0.22.4のEnvUnowned方式](https://docs.rs/jni/0.22.4/jni/)で実装する。0.21のJNIEnv例を混ぜない。Kotlin classの完全名は`io.github.tinmachine.eidolon.NativeBridge`。
6. `.gitignore`へ`/target/`、生成JNI、native build/cacheを追加。Cargo.lockはignoreしない。

host確認: `cargo fmt --all --check`、`cargo test --locked -p eidolon-model`。版情報の必須field・型・同じ入力で同じJSONを返すことを検査する。まだAndroid/JNI実機成功とはしない。

<a id="b02"></a>
## B02 — GradleからCargoを呼び、古いsoを混入させない

変更: 既存`android/app/build.gradle.kts`、`scripts/build-android`。作成: `scripts/build-native`、`scripts/check-native-package`、`NativeBridge.kt`。既存`quest-smoke`へnative_ready/版一致の検査を追加する。

採用する入口:

```bash
rustup target add --toolchain 1.95.0 aarch64-linux-android
cargo install cargo-ndk --version 4.1.2 --locked
export ANDROID_NDK_HOME="$ANDROID_HOME/ndk/27.3.13750724"
# scripts/build-native内部のAndroidビルド。repo rootから実行する。
cargo ndk -t arm64-v8a --platform 34 \
  -o android/app/build/generated/jniLibs/debug \
  build --locked -p eidolon-android
```

Gradleのdebug sourceSetは`app/build/generated/jniLibs/debug`を参照し、`mergeDebugJniLibFolders`をnative生成taskへ依存させる。releaseは別task・別出力directory・`--release`とし、debug成果物を流用しない。AGPのtask graphとAPK内容で依存を確認する。`src/main/jniLibs`へ手作業copyして最新扱いにしない。

native taskのinputs: Cargo.lock、全workspace source、toolchain、NDK版/API/ABI/profile、source digest、APK versionCode。outputs: 対象ABIのsoとmetadata。入力digestは対象ファイル内容から作り、Git dirtyの有無だけで同一視しない。BuildConfigの版情報も同じ入力から生成する。

検証順:

1. `scripts/build-android 3`でassemble/lint。APK zip内に`lib/arm64-v8a/libeidolon_native.so`が一つだけあることを確認。
2. NDKの`llvm-readelf -h -d`でAArch64・必要libraryと公開NDK依存を確認。host ELFを混入させない。
3. Rustの版文字列だけ変更→同じビルド入口→APK内so hashが変わることを確認。
4. APK versionCodeだけ変更→KotlinとRustの両方が新番号を返すことを確認。
5. 最小APKをQuestへ入れ、ComposeパネルにKotlin/Rust両版を表示。`native_ready`とユーザー視認を別々に記録。

`System.loadLibrary("eidolon_native")`失敗は診断Composeへ表示し、nativeReadyをtrueにしない。元のbuild/lint/USB-Wi-Fi操作を壊した状態で後続へ進まない。

<a id="b03"></a>
## B03 — RuntimeHandle、非同期停止、診断の土台

作成: `native/android/src/{registry,bridge}.rs`、`native/core/src/{runtime,diagnostics}.rs`、`NativeSessionHost.kt`、`DiagnosticsStore.kt`、debug専用診断入口。

共通契約のcreate/command/poll/snapshot/stop/destroyを実装する。`NativeSessionHost`はApplication scopeでruntimeを一つ保持し、Activity参照をnative registryへ保持しない。Activity再生成時はUI adapterだけを付け直す。`Application.onTerminate`を実機のcleanup契機にしない。

診断snapshotは`noBackupFilesDir/diagnostics/latest.json`へtemp＋atomic renameで出力。UIから明示した状態取得、試験command完了時、1秒以内の周期で更新できる。1 MiB上限、通常logcatは状態変化と集計値だけ。秘密鍵・入力文字・buffer内容は含めない。

debug専用Activity `DebugCommandActivity`を`src/debug/`へ追加し、固定case IDとランダムrun nonceだけを受け取る。任意shell、path、secret読取りは実装しない。release manifestには存在しないことをB03で検査し、C01のCIとC02の配布検査へ組み込む。ADB入口が一時的にXR focusを変える可能性があるため、物理入力試験の代用にしない。

`scripts/quest-check --serial ... --case ... --timeout ...`を新設し、既存smokeの対象照合・連続ログ・artifact処理を再利用する。run nonceとAPK版が一致するsnapshotを待ち、古い`latest.json`を合格にしない。実行可能なcase一覧は[Phase 6](phase-6.md#checks)が正本。

host: stale handle、二重stop、destroy-before-stop、queue満杯、未知JSON/schema、panic変換を検査。Quest: load→create→snapshot→stop→destroyを10回、通常パネルへ戻れることを確認する。

<a id="h01"></a>
## H01 — APK UIDでnative helperを起動する先行gate

作成: `native/fixtures/helper-probe/`、`native/core/src/helper.rs`。B02にdebug helperの生成・packagingを追加する。

1. probeは`--version`、Unix socket echo、明示終了だけを持つ小さいPIE executable。APKと同じAPI 34/arm64でbuildする。
2. 生成時に`libeidolon_probe_bin.so`という名前でnative library directoryへ同梱する。`useLegacyPackaging=true`で抽出し、`applicationInfo.nativeLibraryDir`から絶対pathを得る。アプリの書込み可能filesDirへcopyしてexecしない。
3. 起動はAPK内のRust `std::process::Command`へargvを個別に渡す。shellを挟まない。実UID、SELinux context、版、ELF依存、exit codeを診断へ返す。
4. runtimeは`filesDir/rt/<nonce>/`、0700、socket pathはUTF-8で103 byte以下。既存directoryを上書きせずexclusive作成する。
5. stdout/stderrを同時にdrainし、socket echo→終了→wait/reap→自分のdirectoryだけcleanup→再起動を10回確認する。

[Androidの実行制約](https://developer.android.com/about/versions/10/behavior-changes-10#execute-permission)と、[Wawonaの配置例](../phase-3-rust-compositor-waypipe.md#apk側helperの実行確認をphase-01へ前倒しする)を区別する。[独立probe](../native-preflight-20261104.md)ではAPK内のCommandから同じUIDで起動し、echo/終了を10回確認した。製品runtimeへの統合とWaypipe binary自体は後続で検証する。`run-as`やADB shellからの起動だけでは合格にしない。失敗時はloader、exec制約、ABI、依存不足を分け、Waypipe統合を待つ。

<a id="r01"></a>
## R01 — SDK Surfaceと入力callbackの契約を実機で確定

作成: `SpatialPanelAdapter.kt`。既存Compose診断を残し、別の一枚を`VideoSurfacePanelRegistration`で作る。

SDK 0.15.0 AARで確認できた入口:

```text
VideoSurfacePanelRegistration(
  id: Int,
  surfaceConsumer: (Entity, Surface) -> Unit,
  settingsCreator: (Entity) -> MediaPanelSettings,
  panelSetup: (PanelSceneObject, Entity) -> Unit
)
VrActivity.onSessionStateChanged(SessionState)
SceneObject.addInputListener(InputListener): Long
SceneObject.removeInputListener(Long)
HitInfo.textureCoordinate: Vector2
```

public signatureの確認までで、callback thread・順序・UV方向・destroy通知を確認済みとはしない。constructorのKotlin named argumentは実際のcompileでも検査する。

初期はActivityごとに**一つのnative panel registrationを作り、windowのEntityごとにbinding**を管理する。R01ではwindow一枚。多枚数では同じregistrationを再利用し、`Entity.id → WindowKey`を照合する。Entity.create直後にcallbackが来てもよいよう、作成中のbindingへ一時保留して照合後にnative attachする。保留件数は表示上限、timeout 5秒。未知Entity/旧sceneのcallbackは適用しない。SDKの公開APIにない`unregisterPanel`や`onSurfaceDestroyed`を存在すると仮定しない。

採取: callback名、thread ID、scene/window/generation、Surface取得回数、SDK Entity生成/破棄、XR state、Activity event。Java objectのhashを永続IDにしない。

操作: 作成→表示→アプリ自身によるpanel破棄→再作成10回、Universal Menu開閉、Activity背景化/復帰、sleep/wake。SDKのSurface再取得通知と`isValid`の変化、描画結果を比較する。

アプリ自身の破棄ではnative detach要求→`surface_detached` ack→input listener解除→Entity.destroy。SDK側から先に失われるケースはR03でEGL errorとして収束させる。SDK所有のJava Surfaceへreleaseしない。scene全体の終了でOSが先にSurfaceを破棄する可能性を許容する。

独立probeでは、この順序でアプリ自身による再生成10回を確認した。再生成要求の連打も遷移中は抑止する。EGLDisplayはSDKと共有され得るため、自分のEGLSurface/contextを破棄しても、表示所有権の根拠なしに `eglTerminate` しない。実際の非装着sleep、SDK主導のsurface喪失、ray入力の通知は別途確認する。

合格証拠は`docs/decisions/sdk-surface-lifecycle.md`へcallback順序とthread制約として残す。ここが未解決ならR02は限定描画実験までで、再生成対応済みとしない。

<a id="r02"></a>
## R02 — Rust patternを一枚に描く

作成: `native/render/src/{surface,egl,pattern}.rs`。依存候補`ndk =0.9.0`、`khronos-egl =6.0.0`、glowは導入時の解決版をlockする。

GL owner上でNativeWindow→EGLDisplay初期化→GLES3 context/config→window surface→current→viewport→checkerboard→swapの順。Androidの`libEGL.so`/`libGLESv3.so`を使い、Linuxの`.so.1`名を固定しない。native window geometryを1024×768へ設定し、EGL_WIDTH/HEIGHTの実測を記録する。

patternは左上赤・右上緑・左下青・右下白、中央に進行が分かる矩形。alpha=1。60 Hz以下の期限付き更新、非可視時はpause。GL vendor/renderer/version、config、frame count、swap時間を記録する。camera permissionや別OpenXR sessionを追加しない。

hostで計算/状態を検査し、Questで`pattern` caseとHMD視認。`eglSwapBuffers`成功、Android capture取得、HMD表示を別結果にする。現在のpassthrough背景が同時に見えることも確認する。

<a id="r03"></a>
## R03 — Surface再生成と描画停止

GL threadへ`Attach`、`RenderLatest`、`Detach`を渡す。世代が異なるdetachは現在のtargetへ触れない。同じtupleのattachは重複として処理する。EGL_BAD_SURFACEはそのtargetをLostへ、context lossは全GL resourceを無効化して最新snapshotから再構築する。

解放順: 新しいframeを止める→currentなcontextでGL resource回収→currentを外す→EGLSurface→NativeWindow追加参照。context全体の再作成は診断に回数を残す。別threadから詰まったGL所有物を破棄しない。

検証: 20回のpanel再生成、old-generation frame/late detach、Menu開閉中のkey reset準備、描画pause中もcore snapshot取得とStopが応答すること。停止遅延・FD/native memoryの推移を保存する。同期swapが停止不能なら`RENDER_STALLED`として記録し、後続の通常運用合格を保留する。
