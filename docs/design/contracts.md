# 共通契約 v1

本書の型・関数・event名は**これから実装するEidolon独自API**。SDK/crateのAPIは[sources.md](sources.md)で区別する。数値上限は初期の設計値で、測定値ではない。

## ファイルと依存方向

```text
rust-toolchain.toml                  # 1.95.0、rustfmt/clippy、aarch64 Android
Cargo.toml / Cargo.lock              # workspace、default-membersはmodel
native/model/src/                   # Android/GL/Tokioを依存に持たない
  ids.rs state.rs commands.rs input.rs layout.rs limits.rs
native/core/src/                    # protocol/model owner、統合supervisor
  runtime.rs session.rs diagnostics.rs
  compositor/{mod,protocol,shm,snapshot}.rs
  helper.rs
native/render/src/                  # Android EGL/GL owner、入力は所有済みsnapshot
  surface.rs egl.rs pattern.rs compositor.rs
native/transport/src/               # russh/Tokio、UIとWaylandには依存しない
  session.rs host_key.rs exec.rs forward.rs launcher.rs
native/android/src/                 # cdylib libeidolon_native.so、JNI/Keystore adapter
  lib.rs registry.rs bridge.rs build_info.rs
native/fixtures/                    # helper probe、SHM client。debug専用
android/app/src/main/java/io/github/tinmachine/eidolon/
  NativeBridge.kt NativeSessionHost.kt SpatialPanelAdapter.kt
  InputAdapter.kt CredentialStore.kt DiagnosticsStore.kt
```

`model`←`core`、`model`←`render`、`model`←`transport`、`core`→`render`/`transport`（ownerの停止とcredential slot）、`android`→各crate。循環依存禁止。Android専用依存は`cfg(target_os = "android")`で囲み、host model testをSDKなしで実行する。必要になるtaskまで空のcrateを大量に追加しない。

## 識別子と所有者

| 値 | 所有・発行 | 規則 |
| --- | --- | --- |
| RuntimeHandle | JNI registry | 正のsigned 64 bit。pointerを公開しない。process内で再利用しない |
| SessionId | Rust I/O supervisor | 正のsigned 64 bit単調増加。新しい製品接続の開始前にjournalへ採番を保存し、runtime再生成・process再起動でも過去IDを再利用しない |
| WindowId / SurfaceId | protocol owner | session内単調増加。Wayland object IDやSDK Entity IDと同一視しない |
| SceneEpoch | Kotlin NativeSessionHost | SDK scene再生成ごとに増加 |
| SurfaceGeneration | Kotlin panel binding | window/scene内で新しいSurfaceごとに増加 |
| FrameSeq / CommandSeq | 各発行owner | 同一識別子内単調増加 |
| RemoteNonce | transport | ランダム16桁小文字hex、exclusive directory作成。認証鍵の代わりには使わない |

render targetは`(session, window, scene, generation)`、frame内の画像は`(session, surface, content_seq)`。古いtupleへの入力・attach/detach・ack・frameは`STALE`として無害に破棄する。JSONのIDは十進文字列にし、Kotlin/JavaScriptの浮動小数点変換による桁落ちを避ける。JNI引数のIDは`Long`、Rustでは範囲検査して整数型へ変換する。

## 四つの実行領域

| 領域 | 持つもの | してはいけないこと |
| --- | --- | --- |
| Kotlin SDK/UI | Activity、Entity、入力listener、SDK所有Surface、適用結果 | GL、SSH待ち、thread join、framebuffer copy |
| Rust protocol/model owner | Smithay、WindowManager、SHM保護下copy、Wayland FD、snapshot、supervisor状態 | EGL present、ネットワークのblocking wait |
| Rust GL owner | NativeWindow参照、EGL、GlesRenderer、texture | Smithay protocol object/JNI local refの保管 |
| Tokio runtime | russh、bridge、remote lease、child I/O | GL、modelの直接書換え |

MVPではTokio worker 2、protocol 1、GL 1。T02のI/O supervisor thread一件が接続ごとのTokio runtimeを所有し、blocking poolは最大一件。終了時のruntime dropをsupervisor上で行い、旧task/FD/worker回収後だけ次の接続を許す。`transport.workers`はsupervisor、`tokio_threads`はworker/blocking threadの実数を別に記録する。UIからのcallはenqueueと短い検証まで。GL ownerは作成threadで最後まで保持し、`unsafe impl Send/Sync`で制約を消さない。

SHMはprotocol ownerが保護されたcallback内で所有済みbyte列へcopyし、Rustの`Arc`等でimmutable snapshotをGLへ渡す。共有pointer、`WlBuffer`、EGL objectはqueueに入れない。詳細は[SHM契約](phase-3.md#shm)。

## JNI入口

`NativeBridge`はKotlin `object`と`@JvmStatic external`で固定する。次のsignatureをB01/B03で導入し、実装前の全native宣言を一度に呼ばない。

```kotlin
external fun nativeBuildInfo(): String
external fun nativeCreate(configJson: String): Long
external fun nativeCommand(handle: Long, commandJson: String): Int
external fun nativeAttachSurface(
    handle: Long, session: Long, window: Long, scene: Long, generation: Long,
    surface: android.view.Surface, widthPx: Int, heightPx: Int
): Int
external fun nativePoll(handle: Long, maxEvents: Int): String
external fun nativeSnapshot(handle: Long): String
external fun nativeRequestStop(handle: Long): Int
external fun nativeDestroy(handle: Long): Int
```

- `nativeCreate`はconfig検査後にregistryへ登録しthreadを起動、handleを返す。初期化完了は別event。失敗時は例外と0、半端なhandleを残さない。
- `nativeCommand`はschema v1の小さいJSON。command全体16 KiB、未知field/tag、無効ID、NaN/Infinity、過大寸法を拒否する。
- `nativeAttachSurface`内で`ANativeWindow`参照を取得する。enqueue失敗時はその参照を直ちに解放する。Java local refとJNI環境を保存しない。
- `nativePoll`は1回最大64件/64 KiB。UIが16 msを目安にpollするが、実際のSDK操作threadはR01で確認する。pollは待機しない。
- `nativeRequestStop`はatomic停止flagを立てて戻る。`nativeDestroy`はStopped後のregistry回収、停止中はBUSY。二重destroyは安全な結果を返す。
- 初期化を含む各入口は`jni 0.22.4`の`EnvUnowned`→`with_env`とErrorPolicyを使う。panicをFFI境界の外へunwindさせない。通常の失敗は安定code、unexpected panicはruntimeをFailedへ移し再利用しない。

戻り値: `0 ACCEPTED`、`1 STALE`、`2 BUSY`、`3 INVALID_ARGUMENT`、`4 NOT_READY`、`5 FAILED`。ACCEPTEDはenqueue成功であって処理完了ではない。完了eventに同じ`request_id`を入れる。実装後はKotlin定数とRust enumが一致する契約testを持つ。

command envelope:

```json
{"schema":1,"request_id":"17","session":"1","type":"resize_logical","window":"2","width":1024,"height":768}
```

主要tag: `connect`、`disconnect`、`reset_input`、`set_xr_state`、`scene_ready`、`surface_lost`、`resize_logical`、`set_pose`、`set_physical_size`、`set_background`、`close_window`、`pointer`、`key`。`connect`は登録済みprofile IDだけを指定し、任意shell commandや秘密鍵本文を受け取らない。

B03では`scene_ready`・`set_xr_state`・`reset_input`と、Activity可視性を独立に伝える`set_activity_state(visible: bool)`を実装する。H01でdebug用`helper_probe(nonce)`を追加し、releaseではNOT_READYとする。R01–R03では`nativeAttachSurface`と`surface_lost(target)`を追加し、targetは全tupleを含むstrict objectとする。detach受付と実際の`surface_detached {target}`を区別する。未実装tagは拒否する。`onPause`でSDKのXR状態を上書きしない。runtime再作成後はhostが最新のscene/XR/Activity状態を再送する。

runtime configはschema 1と、省略可能な`paths: {native_library_dir, files_dir}`。T02で`installation`と`profiles`（最大一件）を追加した。profileのfieldはschema/profile_id/installation/username/host_keyだけで、NativeCreate前にinstallation束縛とEd25519 pinを検証する。接続先は127.0.0.1:8022固定、通常commandは登録済み`profile_id`だけを指定する。pathはApplicationのContext由来だけを渡し、command/ADB intentから指定しない。native側でも絶対path・NUL・長さを検査する。H01の子processとpipe readerは専用I/O workerが所有し、coreの停止完了はworker回収後とする。

通常command 256件・UI event 128件と別に、停止flag・入力解除flag、failure/stopped各1件の予約枠を持つ。入力解除の安全効果は通常queueがBUSYでも伝えるが、BUSYを返したcommandの完了eventは発行しない。eventは既発行の通常event→failure→stoppedの順でpollし、Stopped通知後に古いruntime_ready等を返さない。

Rust→Kotlin: `runtime_ready`、`create_panel`、`destroy_panel`、`set_panel_pose`、`set_panel_size`、`set_panel_title`、`set_background`、`surface_detached`、`session_state`、`failure`、`stopped`。window commandには全世代tupleとcommand_seqを含める。Kotlinは`adapter_applied`/`adapter_failed`を返し、Rustの希望値とSDK適用値を区別する。

T01で追加するcredential専用JNIは通常command/診断と分離する:

```kotlin
external fun nativeGenerateIdentity(): ByteArray
external fun nativePublicKey(privateKey: ByteArray): String
external fun nativeProvideCredential(
    handle: Long, session: Long, requestId: Long, privateKey: ByteArray
): Int
```

生成結果は暗号化前のOpenSSH形式Ed25519秘密鍵。Kotlinはworkerで生成/暗号化し、平文をfileやStringへ保存しない。公開鍵導出後も一時ByteArrayをfinallyで消去する。接続ではRustがhost pin照合後に`credential_required(session, request_id, profile_id)`を発行し、Kotlinが復号して`nativeProvideCredential`へ渡す。native側は長さ上限16 KiBと待機中requestを検査し、一度だけ受け取る専用slotへ所有copyする。終了/失敗/取消時はslotと鍵objectを破棄し、可能なbufferをzeroizeする。JNI受理後にKotlin側bufferを消去する。期限切れ/旧sessionはSTALE。公開鍵以外をnativeSnapshot/nativePoll/logcatへ混ぜない。これらもこれから実装する独自APIである。

## queueと上限

| 資源 | 初期上限・処理 |
| --- | --- |
| UI→core通常command | 256件。満杯はBUSY。pointer motion/poseだけ最新値へcoalesce可能 |
| 停止・focus喪失・入力解除 | 通常queueとは別のatomic flags/mailbox。満杯で失わない |
| Rust→UI command | 128件。pose/size/titleは同一targetへcoalesce。create/destroyが入らなければ新規割当を停止しFailedを通知 |
| render snapshot | windowごとに描画中1＋最新待機1。古い待機画像を所有権ごと解放 |
| 論理toplevel / 表示panel | 8 / 最初1、I05後2。診断UIとpassthroughは別に消費を観測 |
| client / surface | 4 / 合計64。超過は対象clientへprotocolエラー/切断 |
| buffer | width/height 1〜2048、最大16 MiB。stride/offset込みの実mappingも検査 |
| SHM mapping | client 64 MiB、全体128 MiB、pool 8/client |
| 所有済みpixel | 全体128 MiB。cache・pending・描画中の重複参照は同じallocationとして計上 |
| SSH forward channel | 8。同時copyを無制限にspawnしない |
| exec診断出力 | stdout/stderr各64 KiB ring。上限後も読み捨てを続ける |

モデル/制御threadは10 ms以内の周期を目標に停止flagを確認する。上限は性能保証ではない。予算不足は明示エラーにし、clientのbufferをまだ参照中にreleaseして空きを作らない。snapshotを描かずに捨てても、protocolのcommit/release処理は完了させる。

## 座標・画像・resize

- `LogicalSize`はWayland単位、`BufferSizePx`はpixel、`PhysicalSizeM`はmeter。暗黙変換しない。
- MVPの初期logical sizeは1024×768、buffer_scale=1、normal transform、panelは1.2×0.9 m。既存診断Compose panelの1.2×0.8 mとは別target。
- coreのUV規約は左上(0,0)、右下(1,1)。SDKのUVがこの規約かをI01で実測し、Kotlin側で一度だけ補正する。
- pixelはpacked RGBA8、premultiplied alpha、上から下の行順へ正規化してthread間で渡す。Wayland ARGB8888/XRGB8888のlittle-endian byte orderを直接RGBAと解釈しない。
- popup/subsurfaceのoffset、window geometry、letterboxを一つの変換関数へ集約し、描画とhit testで同じ変換値を使う。
- 物理拡大はpanelサイズのみ。logical resizeはconfigure/ack/新commit、producer buffer寸法は別操作。ackだけで新しい画像寸法に切り替えない。

## lifecycleとerror

Runtime: `Starting → Ready → Stopping → Stopped`、回復不能なら`Failed`。
製品session: `Disconnected → Connecting → Authenticating → Forwarding → StartingRemote → Running → Stopping → Disconnected/Failed`。
window: `Created → AwaitingInitialCommit → Configured → Mapped ↔ Unmapped → Destroyed`。
Surface target: `Absent → Attaching → Active → Detaching → Absent`、loss時は`Lost`。

XR `FOCUSED`は入力可能、`VISIBLE`は表示可能だが入力不可。それ以外は描画抑制・入力解除。scene/Surfaceの有無、Activity再生成、transportの状態と独立させる。

安定error code: `CONFIG_INVALID`、`CREDENTIAL_INVALID`、`KEY_LOST`、`CREDENTIAL_IO`、`JNI_FAILURE`、`SURFACE_LOST`、`EGL_INIT_FAILED`、`RENDER_STALLED`、`HOST_KEY_UNKNOWN`、`HOST_KEY_MISMATCH`、`AUTH_FAILED`、`CONNECT_TIMEOUT`、`AUTH_TIMEOUT`、`EXEC_TIMEOUT`、`SSH_CONNECT_FAILED`、`SSH_IO_FAILED`、`FORWARD_DENIED`、`REMOTE_EXIT`、`HELPER_EXEC_FAILED`、`PROTOCOL_ERROR`、`RESOURCE_LIMIT`、`CLEANUP_FAILED`。補足messageは秘密情報・入力文字・pixelを含めない。再試行可能かと責任領域も返す。

停止の順番とdeadlineは[Phase 6](phase-6.md#l01)が正本。同期GL callをtimeoutで安全に強制終了できるとは仮定しない。GL thread停止が詰まった場合はruntime再利用を拒否し、旧NativeWindowを別threadから解放しない。
