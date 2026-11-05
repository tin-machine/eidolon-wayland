# Native事前検証とQuest操作の記録

基準revision: `53baac4ab59f95e0b956b74438e460bb328e3e19`。2026-11-04開始。
本格実装前に、独立debug APK `io.github.tinmachine.eidolon.probe` で成立条件を調べる。
製品の31タスクの完了とは区別する。画像・端末識別子・生ログは公開しない。

## 実測結果

最終自動run: `20261104T232030-dee09de6`、Probe 3。
APK SHA-256: `cdf07f01785fd249e50ecd45c87a2125e8c55db06becb06cd53260147f7f8b9c`。

| 確認対象 | 結果と限界 |
| --- | --- |
| APK UIDでのhelper実行 | 10回のecho・終了・回収に成功。Waypipe自体の実行は別 |
| Rust EGL→SDK Surface | 1024×768の四隅readback、再生成10回に成功。Probe 1の4色表示はユーザーも視認 |
| ADBの撮影・タップ | 両眼画像を取得。検証パネルのボタン操作で再作成回数が1増加 |
| APK→Termux SSH | russh 0.65.0で公開鍵認証、host key不一致拒否、execが成功 |
| StreamLocal | remote Unix socket→SSH→APK内Unix echoで4接続×8 MiBが一致、forward取消成功 |
| 一時資源の回収 | 一時authorized_keys行とremote fixtureを回収したことをrunnerが再確認 |
| Smithay / xkbcommon | 0.7.0のDisplay・wl_shm global、1.8.1の自己完結keymapをAPK内で初期化成功 |
| SHM buffer・SIGBUS・Smithay GlesRenderer | 未実施。初期化成功で置き換えない |

probe/app両moduleのassemble/lint、Rust format、設計31タスクの参照検査、Python構文確認が通った。
ELFはAArch64。生成helperはPIEで、probeを含めNEEDEDは公開NDK libraryのみ。libxkbcommonはstatic linkされた。

## 保存先

再開時に `~/.config/homecluster-agents/codex-work-root` を読み直す。
その配下の `eidolon-native-preflight-20261104/` に、変更前設定、各試行、ビルドログ、画像、再開メモを保存する。
directoryは0700、記録は0600。以下のコマンドの `quest_serial` は `adb devices -l` で確認した対象を指定する。

## スリープと給電中の起動維持

実機はQuest 3、Android 14 / API 34。変更前の値を `power-before.json` に保存した。

| 項目 | 変更前 | 今回の操作 |
| --- | --- | --- |
| system `screen_off_timeout` | `86400000`（24時間、ミリ秒） | 変更なし |
| secure `sleep_timeout` | `-1` | 変更なし |
| global `stay_on_while_plugged_in` | `0` | `svc power stayon true` により `15` |

```bash
adb -s "$quest_serial" shell settings get system screen_off_timeout
adb -s "$quest_serial" shell settings get secure sleep_timeout
adb -s "$quest_serial" shell settings get global stay_on_while_plugged_in
adb -s "$quest_serial" shell dumpsys power
# 今回実行した変更
adb -s "$quest_serial" shell svc power stayon true
```

直後は `mWakefulness=Awake` / `mStayOn=true` になったが、後の観測では給電中でも `Asleep` に戻った。
**Androidの画面タイマーを長くすることと、Questの非装着時休止を抑止することは同じではない。**
今回は元から24時間だったため、タイマー延長は有効な修正にならない。

追加で開発用の装着検知通知を一度実行した。

```bash
adb -s "$quest_serial" shell am broadcast -a com.oculus.vrpowermanager.prox_close
```

実行後にAwakeへ復帰し、その後の観測でもAwakeだった。これは一回の実測であり、OS全版での持続や再起動後の維持を保証しない。
常駐アプリの導入や定期broadcastは行っていない。
[Meta公式device management資料](https://github.com/meta-quest/agentic-tools/blob/main/skills/metavr-cli/references/metavr-device-management.md)には、`metavr device proximity --disable --duration-ms 60000` 等の開発用操作がある。今回 `metavr` は未導入で、このコマンド自体は実行していない。

記録: `power-stayon-change.json`、`power-after-stayon.txt`、`power-observe-01.txt`、`proximity-wake-trial.json`、`power-after-proximity.txt`、`power-observe-02.txt`。
記録時点で `stay_on_while_plugged_in=15` を開発用に維持している。
今回の元の値へ戻すには次を使う。一般の端末では固定値0でなく、各端末で保存した変更前値を使う。

```bash
adb -s "$quest_serial" shell settings put global stay_on_while_plugged_in 0
adb -s "$quest_serial" shell settings get global stay_on_while_plugged_in
adb -s "$quest_serial" shell dumpsys power
```

これはAndroid設定だけの復元であり、装着検知の状態まで復元した証拠にはしない。
通常の装着・非装着によるsleep/復帰試験は別途行う。

## スクリーンショットと入力

実際に次のコマンドで4128×2208の両眼画像を取得できた。passthroughの室内と空間ウィンドウも含まれる。
[Android公式ADB資料](https://developer.android.com/tools/adb#screencap)でも `exec-out screencap -p` が案内されている。

```bash
umask 077
adb -s "$quest_serial" exec-out screencap -p > "$private_capture_path"
adb -s "$quest_serial" shell input
adb -s "$quest_serial" shell dumpsys window windows
```

この端末の `input` は引数なしでhelpを表示する。`input --help` は `Unknown command: --help` だった。
`input -d DISPLAY_ID tap X Y`、`swipe`、`keyevent`、`text` が利用できる。
ただし、両眼合成画像の座標、Androidの論理display座標、SDKのコントローラーrayは別物。
`dumpsys window windows` で目的のpackage・display・サイズを特定してから操作し、アプリ側のイベントで成功を確認する。
`screencap -d` のphysical display IDと、`input -d` のlogical display IDも混同しない。
コマンドがexit 0でも、ボタンが押された証拠にはしない。

今回、probeのComposeウィンドウはlogical display `23`、1080×315だった。
`adb -s "$quest_serial" shell input -d 23 tap 200 158` で「Recreate Rust panel」を押し、
同一nonceの `completed_cycles: 0→1` / `generation: 1→2` を確認した（`input-tap-trial.json`）。
display IDは起動ごとに変わるため、23を固定した自動化にはしない。

## 起動保留を調べた経緯

1. 最初のinstallは成功。`am start -W` が30秒でタイムアウトし、probeプロセスと結果ファイルは無かった。
2. activity dumpにGuardianがあったため境界確認待ちと推定したが、ユーザーの観測は「Termuxと設定だけ」。22:56の画像でもダイアログが無かった。最初の断定は訂正した。
3. `am start` を再試行するとprobeが起動した。APK UIDのhelper echoが10回成功し、Rust EGLで1024×768、四隅の赤・緑・青・白のreadbackとframe更新を確認した。
4. 再試行後の画像には「アプリで使用する境界線を作成」が実際に写った。内部描画成功とユーザーにパネルが見えることは別で、HMD視認を依頼した。
5. ユーザーが境界設定を完了し、4色のパネルが見えると確認した。黄色線の動きは個別の回答を得ていないため、確認済みに含めない。
6. `20261104T230222-399b1db8` でhelper 10回、パネル再生成10回、detach 10回、停止timeout 0回を確認した。rendererはAdreno 740、四隅のRGBAが期待値に一致した。これは短い再生成試験であり、FD・メモリーの長時間安定性の合格ではない。

当時はGuardian無効化や境界の自動承認を行わず、実際の周囲に合わせる設定を装着者へ依頼した。
runnerは `am start -W` の無期限に近い待ちに依存せず、起動要求とnonce付きreadinessの期限付き確認を分ける。

## ビルド中に判明した点

- `/opt/rustup` がroot所有で通常の `rustup target add` が失敗。既存管理方式に合わせ、`sudo -n env RUSTUP_HOME=/opt/rustup /opt/cargo/bin/rustup target add --toolchain stable aarch64-linux-android` で対象だけ追加した。
- `cargo install cargo-ndk --version 4.1.2 --locked` を実行。Rust 1.95.0、NDK 27.3.13750724、API 34、arm64で構築する。
- Android arm64の `c_char` は符号なし。FFIを固定i8から `std::ffi::c_char` に修正した。
- jni 0.22.4の `jboolean` はbool。整数との比較を削除した。
- SDKとEGLDisplayが共有される可能性があるため、probeは自分のcontext/surfaceを破棄するが `eglTerminate` は呼ばない。
- パネル再作成ボタンの連打が停止中のSurfaceを先に破棄しないよう、遷移中は次の再作成を受け付けない。
- SSH probe準備の `exec-out` + stdin + `cat` が終了待ちになった。転送先は専用fixtureで、既存authorized_keysへの追記前に失敗。`shell -T` へ変更すると6 byteのfixtureで書込み・EOF・終了を確認できた。残った自分のfixtureと空tmpを回収し、既存authorized_keysの95 byteは維持した。
- xkbcommonのKeycodeに `i32` からの変換はない。fixtureのliteralを `38u32` にして型を明示した。
- 生成metadataをassetsへ入れた時、Gradle lintがproducer taskとの依存不足を検出した。sourceSetのpath/TaskProviderだけでは解消せず、AGPの `variant.sources.assets.addGeneratedSourceDirectory` に切り替えた。
- 同APIは出力directoryを設定するため、build scriptへ渡すassets pathもtask実行時に取得する必要があった。最初は古いpathへ生成してAPKに含まれず、runnerがinstall前に拒否した。`doFirst` で確定pathを渡した後は、ZIP内metadata、APK/nativeの版・commit・digestが一致した。

## 再現と判定範囲

```bash
cd android
./gradlew --no-daemon --console=plain -PeidolonProbeVersion=3 :probe:assembleDebug :probe:lintDebug
cd ..
scripts/quest-native-probe --serial "$quest_serial" --cycles 10 --transport --timeout 180
```

helperはAPKのnativeLibraryDir内のPIEをAPK UIDで実行し、専用directoryのUnix socketでechoした後に子を回収する。
Rust描画はSDKから渡されたSurfaceのNativeWindow参照を追加取得し、専用threadで描画・停止・解放する。
rendererのreadback、同一nonce、APK/nativeのversion一致、パネル再生成回数を自動判定する。HMD視認は別結果。
描画/helperの判定だけで他の方式の成功とはしない。SSHとSmithay初期化の独立判定を以下に追加したが、Waypipe、SDK ray入力、長時間・sleep耐久は未実施。

`--transport` ではAPK内で一時Ed25519秘密鍵をメモリーに生成し、ADBでは公開鍵・Termux公開host key・ユーザー名だけを交換する。
PCの開発用秘密鍵をAPKへ渡さない。Termuxのauthorized_keysへ一時行を追加し、終了時はその行とexclusive作成したfixtureだけを回収する。
APKのrusshから127.0.0.1:8022へ接続し、異なるhost keyの拒否、公開鍵認証、exec、remote Unix socket→StreamLocal→APK内Unix echo socketの経路を確認する。
payloadは4接続で各8 MiB、各byteを比較する。WaypipeのFD転送や切断後復旧はこのechoだけでは判定しない。

Smithayは0.7.0、featureはwayland_frontend/desktopのみ。libxkbcommon 1.8.1をAPI34/arm64でstatic linkする。
source hash・license・Meson flagsは [deps.lock.json](../probes/native-deps/deps.lock.json)、構築入口は [build-probe-xkbcommon](../scripts/build-probe-xkbcommon)。
最小Display、wl_shm global、自己完結した一文字keymapをAPK内で初期化する。
SHM buffer読み込み・pool truncate時のSIGBUS/ART共存・Smithay GlesRenderer adapterは未実施で、W02/W03/R02の後続gateに残す。

## 2026-11-05の追跡復帰と次回のBoundary対応

装着後に案内が解消し、通常SDKのsurface・pattern・surface-cycle・layout検査が実行できた。
ユーザーからは作業を継続し、**次にBoundaryが検証を妨げた場合だけ**一時停止を試す許可を得た。
この許可前の記録を、現在の一律禁止条件として扱わない。

[Meta公式CLI手順](https://developers.meta.com/vr/essentials/metavr-devices-and-apps/)では、
`metavr device boundary`が状態照会、`--disable`/`--enable`が切替で、対象は`-d`で指定する。
`--status`の有無は実際の`--help`で確認する。調査時の環境では`metavr`はPATHになく、まだ導入・切替を行っていない。

再発時は前面ActivityとXR状態、元の境界状態を記録し、対象端末だけを一時停止して同じ検査を再試行する。
終了後に元の状態へ戻して照会する。代替候補の`debug.oculus.guardian_pause`も変更前後を記録する。
このOSでの代替propertyの有効性は未検証で、事前照会値は空だった。
passthroughに影響した場合はその条件を結果へ併記し、他の実装・検証を継続する。
境界を一時停止した成功を、通常境界でのpassthroughやHMD視認の証明へ読み替えない。
