# 根拠・固定版・未確定事項

確認日: 2026-11-04。API/配布sourceの確認は、その組合せがQuestで動いた証拠ではない。実機済みの基準は[Phase 0記録](../phase-0-troubleshooting-20261104.md)。今回の設計調査ではAPK更新、端末設定変更、Rust実装を行っていない。

## APIと採用判断

| 一次資料・確認対象 | 確認できたこと | 残る実装gate |
| --- | --- | --- |
| [jni 0.22.4](https://docs.rs/jni/0.22.4/jni/)と配布crate `src/lib.rs` | `EnvUnowned`/`with_env`、ErrorPolicyが0.22の入口 | B01/B03で例外・panic・thread境界を実装して試験 |
| [cargo-ndk 4.1.2 source](https://static.crates.io/crates/cargo-ndk/cargo-ndk-4.1.2.crate)のCLI定義 | target/platform/outputを指定してGradle生成先へ出力可能 | B02でtask入力・APK版・so同梱を照合 |
| [Meta公式ExoVideoEntity](https://github.com/meta-quest/Meta-Spatial-SDK-Samples/blob/822f4d1ec967d4975ae8db386502b60cda5218c0/PremiumMediaSample/app/src/main/java/com/meta/spatial/samples/premiummediasample/entities/ExoVideoEntity.kt)とローカルSDK 0.15.0 AAR | `VideoSurfacePanelRegistration`、Entity/Surface callback、MediaPanelSettings。AARからInputListener/HitInfo/SessionStateも確認 | R01でcallback寿命/thread、I01でUV/buttonsを実測。sampleの`unregisterPanel`はsample内extensionで、SDK本体のAPIと扱わない |
| [Android 10の実行権限制約](https://developer.android.com/about/versions/10/behavior-changes-10#execute-permission) | target 29以降は書込み可能なapp homeからのexecを前提にできない | H01でAPKに同梱・抽出したPIEをAPK UIDから実行。ADB/run-as成功で代替しない |
| [russh 0.65.0 source](https://static.crates.io/crates/russh/russh-0.65.0.crate)の`src/client/mod.rs`・`Cargo.toml` | MSRV 1.89、標準にaws-lc-rs。StreamLocal成功応答、forwarded callback、reply dropでreject。default handlerは無条件acceptなので上書きが必要 | T02/T03でAndroid暗号backend、明示path検査、reply/task所有権、half-close |
| [Smithay 0.7.0 SHM契約](https://docs.rs/smithay/0.7.0/smithay/wayland/shm/fn.with_buffer_contents.html)と[配布source](https://static.crates.io/crates/smithay/smithay-0.7.0.crate) | raw pointerはcallback内のみ。SHM可変、SIGBUS保護あり。`ImportMem`は所有byte列を取る。xkbcommon依存はdefault-features=falseだけでは消えない | W01〜W03でnative依存・ARTとSIGBUS handler共存、D01でAndroid EGL adapterとRGBA形式 |
| [Wayland公式xdg-shell source](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/1.47/stable/xdg-shell/xdg-shell.xml) | ローカル配布XMLからinitial configure/ack/map/unmapの順序を確認 | W02/W03でclient fixture。ackだけでpixel更新完了にしない |
| [Termux Waypipe recipe](https://github.com/termux/termux-packages/blob/6c7082a257f4bd15dbca71bdfe1ecab9e3e5bab2/packages/waypipe/build.sh) | 0.11.3 C版、libandroid-spawn/lz4/zstd/libwayland、GPU/videoなしの構成 | W04でTermuxのprefix/runtime前提をAPK向けbuildから除去。recipeをそのままAPK移植済みと扱わない |
| [Waypipe 0.11.3 source](https://gitlab.freedesktop.org/mstoeckl/waypipe/-/archive/v0.11.3/waypipe-v0.11.3.tar.bz2)の`waypipe-c/waypipe.c`・`waypipe.scd` | global optionはmodeより前。client側転送socketとserver側displayを分離。`--no-gpu --compress none --threads 1`を使える | W04/W05でAPK/Termux両端を固定し、実引数・fd/SHM転送・複数clientを確認 |
| [Termux foot recipe](https://github.com/termux/termux-packages/blob/6c7082a257f4bd15dbca71bdfe1ecab9e3e5bab2/x11-packages/foot/build.sh)と[foot 1.28.0 source](https://codeberg.org/dnkl/foot/archive/1.28.0.tar.gz)の`wayland.c` | 必須はcompositor/subcompositor/shm/xdg shell/data_device_manager/seat/output。clipboard manager v3欠落で起動中止。viewporterはこの初期判定では必須でない | W02でdata deviceの最小処理を前倒し。I07で実転送を拡張。導入版が違えば再照合 |
| [Codex Models](https://learn.chatgpt.com/docs/models) | UIのモデル選択、CLI `/model`、利用可能なモデルから選ぶ手順 | ユーザー指定Solへ切替。特定世代やaccountでの可用性は仮定しない |

## 配布物の照合値

今回取得した公式配布物のSHA-256。実装時の依存取得で同じ値を照合する。これは将来生成するAPK/native成果物のhashではない。

```text
cargo-ndk-4.1.2.crate  903cc87cda6ab7a2ff82a74065e1c2ae5baa869546b32eb4aacabcc6ed5a670f
jni-0.22.4.crate       5efd9a482cf3a427f00d6b35f14332adc7902ce91efb778580e180ff90fa3498
russh-0.65.0.crate     3bb77a4b259312b2abbc541bca1c13cb4e9b61c560a7fd894068c3b92e0c9dc3
smithay-0.7.0.crate    740cea6927892bc182d5bf70c8f79806c8bc9f68f2fb96e55a30be171b63af98
waypipe-v0.11.3.tar.bz2 2d4093903d444fda39c9b7db0a0b826f450a64d4302a03c9c9e3dbe480e8bb03
foot-1.28.0.tar.gz     4296be402b5684d049534598e69db92b918f92beac9dab76b585207045f0b037
```

JNI/russh/Smithay/cargo-ndkは`https://static.crates.io/crates/<name>/<name>-<version>.crate`から取得。Waypipe archiveは上のリンクから取得し、Termux recipeのchecksumとも一致した。

ローカルSDKの`classes.jar`を`javap`で確認したAAR:

```text
meta-spatial-sdk-0.15.0.aar         f409e9b9dfda689df2fa6a574e6857b41e2f1aff7c1a56c7ba80c6d60370a873
meta-spatial-sdk-toolkit-0.15.0.aar b570d70b3a536de0b9198dedc3aedeec2148b4b2f1a87ea2674c2e4fb1ac736f
meta-spatial-sdk-vr-0.15.0.aar      896a02aea7defed24510e107af0afd07bde304d7a8eadc9cc1ea5bcf5c36ce0d
```

ローカル`wayland-protocols` packageは`1.47-1`、`stable/xdg-shell/xdg-shell.xml`のSHA-256は`5084e76386f6c3959bee957a784c57de204be0de3f57533ce07b4be0617b171a`。オンラインのXML取得に失敗したため、導入済み公式packageのXMLを確認した。

## 設計調査の試行錯誤と再開情報

1. 既存phase文書をHybrid RAGから読み、現行code/Gradle/scriptと照合した。構造preflightは`eidolon-wayland`が登録対象外と返したため、sourceを直接確認した。
2. russh docs.rs等の一部取得が失敗したため、公式crate archiveをchecksum付きで保存し、実装とfeature定義を調べた。API名を記憶で補わず、`ChannelOpenHandle`やxkbcommonの実依存を設計へ反映した。
3. Meta sampleだけでは0.15.0のsignatureを確定できないため、実際にビルドで使用したAARを検査した。sample extensionとSDK APIを区別し、Surface破棄・callback threadは実機gateとして残した。
4. Waypipeのマニュアル名を`waypipe.1.scd`と想定した検索は不一致だった。archive内の実ファイル`waypipe.scd`とC parserを読み、socket/display/option順を確定した。
5. 当初の引継ぎモデル名はユーザー訂正でSolへ変更した。CLIの設定や実装modelを自動変更していない。
6. 旧設計の単一compositor/render owner案を、protocol/modelとGLの分離に変更した。GL待ちから制御を切り離す代わりに、immutable CPU copy・memory予算・世代・release契約を追加した。性能の改善を測定済みとは扱わない。
7. 最終レビューでfootの必須globalを疑い、Termux recipeとchecksum一致の1.28.0 sourceを確認した。clipboardをすべてI07へ延期する案ではfootが起動しないため、空selectionとrequest処理をW02へ移した。viewporterの有無とは別条件だった。

private作業記録のtask相対directoryは`eidolon-sol-design-20261104/`。rootは毎回`~/.config/homecluster-agents/codex-work-root`から読み直す。`resume.md`、`sdk-signatures.txt`、`sdk-input-lifecycle-signatures.txt`、`sdk-session-signatures.txt`、`upstream/`に詳細と配布sourceを保存した。rawログの絶対pathや端末識別情報を実装要件にしない。

## まだ固定しないもの

- W01のlibxkbcommon/libwaylandなどnative依存の正確な版とAndroid build patch。先行compile gateでsource/版/hash/license/optionを`native/deps.lock.json`へ固定する。
- SDK Surface callbackの通知順、panel再生成、ray UVとbutton maskの実機挙動。
- AndroidのSIGBUS保護とSmithayの共存、GlesRendererのAndroid EGL接続、APK helperの実行可否。
- 二枚表示時のlayer予算、swap待ち、充電継続、性能。

これらは「実装者が適当に選ぶ余白」ではなく、対応taskの観測・合格条件を満たしてから次へ進む項目である。失敗して方式を変える場合は、根拠・影響task・契約の変更をこの設計へ反映する。
