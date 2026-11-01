# Eidolon Wayland — 実装計画・検証項目

Status: **Planning / 実装未着手**  
Architecture: [architecture.md](architecture.md)  
Target: **Quest 3単体 + Termux + localhost SSH + Waypipe + Spatial SDKアプリ内Wayland compositor**

## 成功の定義

最小MVP: Quest 3にインストールしたEidolon APKを起動し、**同じQuest上のTermuxで動くfoot**を、VR空間内の独立した長方形パネルとして表示する。Bluetoothキーボードでshell/Neovimに入力でき、コントローラーでクリック、画面の再描画、リサイズ、接続の再確立が行える。

- **ネットワークに依存しない**: アプリ実行と表示にはPC・Wi-Fi・クラウドは不要。localhost SSHのみ。
- **非特権**: Questのroot化、OSの置換、カーネルモジュールの導入なし。
- **既存のターミナルを再利用**: footをWayland clientとして動かし、ターミナル描画は再実装しない。
- **分離したXRパネル**: 最初は1画面、その後2枚以上の独立した<code>xdg_toplevel</code>へ。
- **PoC段階はCPUコピーを許容**: 低遅延/ゼロコピーはMVP合格要件にしない。

## 最初に潰すべき不確実性（スパイク）

| 優先度 | 検証 | 合格条件 | 不成立時 |
| --- | --- | --- | --- |
| P0 | 空のSpatial SDK APKをQuest実機でビルド・起動 | immersiveシーンを表示できる | SDK/Androidビルド環境を最小化 |
| P0 | <code>VideoSurfacePanelRegistration</code> + NDK EGL/GLES | 独自のテストパターンをXRパネルへ連続描画できる | ViewPanel/別Surface APIを比較 |
| P0 | Android/aarch64にWayland server/Smithayを組込む | <code>wl_shm</code>を扱う最小compositorがAPK内で起動 | libwayland-server C/C++を代案として検証 |
| **P0** | **Android側Waypipeのビルドとcompositorソケット接続** | **Bionic上でWaypipe display-sideが動作し、Wayland clientの接続要求を転送できる** | Android側は小さなcustom proxy、またはTermux compositor＋Binder bridgeへ変更 |
| P0 | APK内SSHクライアント → Termux <code>127.0.0.1:8022</code> | ホスト鍵検証＋公開鍵認証でコマンド実行可能 | libssh/NDK/exec方式を見直す |
| P0 | Waypipe両端 + Wayland client | 描画と入力が往復し<code>xdg_toplevel</code>が生成される | Waypipeのprotocol/依存差を解析 |
| P1 | Questのバックグラウンド制限 | XR表示中にTermuxのsshdとfootが維持される | 明示的な再接続とTermux保護手順を設計 |

> 最も危険なのは「SSHを使えばWaylandが自動的に届く」という思い込み。Waypipeの**両端**が動き、local display側に有効なWayland compositorが存在してはじめて表示できる。

## Phase 0 — レポジトリと開発環境

作業:
- [ ] Android Studio/Gradle/AGP/Kotlin/JDK/Spatial SDKのバージョンをサンプルから選び、固定・文書化。
- [ ] Android APK（Kotlin）+ native renderer（C/C++）+ compositor候補（Rust/Smithay）をモジュール分離。
- [ ] Android NDK aarch64、Rust android target、CMake/cargo-ndk等のtoolchain確認。
- [ ] <code>adb install</code>、起動、<code>logcat</code>、native crash stack、画面録画を再現可能にする。
- [ ] CIで少なくともGradle compile、Kotlin lint、Rust test（利用時）、Markdownリンク/フォーマット検証。
- [ ] SDKサンプルコードのライセンスと組込み可能範囲を確認。

成果物: 起動する空APK、手順書、build matrix、初期CI。

検証: adbでQuestを認識、APK起動/終了が安定。実機でのXR renderingをログ・録画で再現。

## Phase 1 — Spatial Surfaceの描画スパイク

作業:
- [ ] immersive Spatial SDKアプリに1枚のパネルを作成する。
- [ ] <code>VideoSurfacePanelRegistration</code>の<code>surfaceConsumer</code>を実装し、Surface取得・更新・破棄を追跡。
- [ ] <code>ANativeWindow_fromSurface</code>、EGL、OpenGL ESでcheckerboard/カラーバーとフレームカウンタを描画。
- [ ] pixel size、物理サイズ、DPI、視距離、render modeと文字の可読性を記録。
- [ ] パネル拡縮・位置移動、Surface再生成、アプリpause/resumeへの耐性を確認。
- [ ] CPU/メモリ/フレーム時間の測定点を設ける。

成果物: 入力で配置可能なテストパターンパネル。

合格: 画面のちらつきやクラッシュがなく、再生成後も再描画可能。

## Phase 2 — SSHとTermuxの安全な接続

Termuxの初期セットアップ例（ポートは実環境の設定を確認）:

~~~sh
pkg update
pkg install openssh waypipe
pkg install x11-repo
pkg install foot
# 公開鍵を $HOME/.ssh/authorized_keys に登録し、適切な権限へ
# まずはTermuxの別セッションで接続試験する
sshd
id -un
~~~

本番運用前にはSSHDのbind先を127.0.0.1のみに制限し、鍵認証だけにする（設定はTermuxのOpenSSHの実際のsshd_configで検証）。鍵生成と登録は初期は手動とし、自動配布やパスワード保存を実装しない。

作業:
- [ ] Termuxのsshdを<code>127.0.0.1:8022</code>に限定し、APKから接続可能か試す。
- [ ] Android側にSSH接続ライブラリ等を実装。<code>libssh</code>等は候補であり採用確定ではない。
- [ ] ホスト鍵フィンガープリント検証（初回登録方式と鍵ローテーションも設計）。
- [ ] 鍵をAndroid Keystore等で保護できるか、使用SSH実装との互換性も含めて検証。
- [ ] APK→SSH→<code>id</code>/<code>echo</code>のstdout/stderr/exit status取得と切断検出。
- [ ] sshdの待受、ユーザー、ポート、公開鍵認証の手順をセットアップドキュメント化。

合格: 物理LANにsshdを露出せず、Quest内のAPKからTermuxのコマンドを繰り返し実行できる。

## Phase 3 — Wayland serverとWaypipeのPoC（最重要）

作業:
- [ ] Androidプロセス内部にWayland serverを起動（Smithayを第一候補）。
- [ ] Wayland Unix socket、event loop、<code>wl_compositor</code>、<code>wl_shm</code>、<code>xdg_shell</code>、<code>wl_seat</code>の基礎を実装する。
- [ ] テスト用Wayland clientが<code>xdg_toplevel</code>を生成・commitできるようにする。
- [ ] Android側のWaypipeをNDK/Bionic向けにビルド/同梱できるか検証する。既存waypipeのspawn、Unix socket、外部依存、fd、APK実行制約を調査する。
- [ ] SSH双方向ストリームをwaypipe local ↔ remoteへ接続する。既存の<code>waypipe ssh</code> CLIの起動方法を参考にしつつ、APKへの単純なコピーで動くと仮定しない。
- [ ] TermuxのWaypipeと組み合わせ、<code>--no-gpu</code>相当のSHM経路で起動する。
- [ ] <code>WAYLAND_DEBUG=client</code>とcompositorログでプロトコルを追う。
- [ ] xdg configure/ack、frame callback、buffer release、resizeが止まらないか確認する。
- [ ] クライアントごとのsurface/toplevelを追跡し、window lifecycleイベントを出す。

参考: [Waypipe manpage](https://man.archlinux.org/man/waypipe.1.en)、[Smithay Wayland helpers](https://docs.rs/smithay/latest/smithay/wayland/)、[Termux waypipe recipe](https://github.com/termux/termux-packages/blob/master/packages/waypipe/build.sh)。

合格: 1つのWaylandアプリがTermuxで動き、APK側compositorが確実にそのsurfaceとbufferを受け取れる。

**Gate:** Android側Waypipeが構築できない場合は、何が障害か（libwayland、liblz4、Zstd、socket、spawn、API制限等）を記録する。主設計の目的を維持しつつ、custom protocol proxy、別UID FD bridge、Termux側compositorへの構成変更を技術的に比較してから判断する。

## Phase 4 — Wayland windowをXRパネルに描画（MVP）

作業:
- [ ] compositorで受信した<code>wl_shm</code>画像をEGL/GLES rendererへ引き渡す。
- [ ] ARGB8888/XRGB8888、stride、damage、buffer lifetime、同期を正しく処理する。
- [ ] 受信フレームをSurfaceへ描画する。最初はCPU→GPUアップロードを許容。
- [ ] 1つの<code>xdg_toplevel</code>に1つのSpatialパネルを紐付ける。
- [ ] 物理パネルサイズとWayland logical buffer sizeを分離する。
- [ ] まず静止クライアント、次にfoot（shell実行）で確認。
- [ ] クラッシュ/切断でbufferが解放されることを確認。

合格: Quest上のTermuxで動くfootがXRパネルに表示され、文字とカーソルが更新される。

## Phase 5 — XR入力と複数ウィンドウ

作業:
- [ ] Spatial panel hit-test → Wayland pointer座標変換、button、scroll、enter/leave。
- [ ] Android KeyEvent → Wayland keymap/modifiers、keyboard focus、キー繰返し。
- [ ] Bluetoothキーボードで<code>Esc</code>/<code>Ctrl</code>/<code>Alt</code>/<code>Tab</code>/function keys、US/JIS記号を検証。
- [ ] foot内のfish/tmux/nvim操作（特にcopy-modeとmodifier）を確認。
- [ ] 複数<code>xdg_toplevel</code>それぞれにXRパネルを割り当て、生成・閉鎖・フォーカス変更を実装。
- [ ] popup、subsurface、親子座標、clipboard、IME/日本語変換の仕様と実装を段階的に追加。
- [ ] 物理サイズの拡縮、ピクセルリサイズ、配置保存、パススルーの切替を実装。

合格: 2つの独立したアプリウィンドウを空間に並べ、入力フォーカスを切り替えられる。

## Phase 6 — 運用性、性能、耐障害性

作業:
- [ ] lifecycle state machine（Disconnected/Connecting/Ready/Reconnecting/Stopping）を実装。
- [ ] Termuxの停止・Androidバックグラウンド制限・Questスリープ復帰に対応。
- [ ] ホスト鍵変更、秘密鍵不整合、ssh失敗、Waypipe失敗の診断を分けて出力。
- [ ] フレーム到着頻度、描画時間、Waypipe byte数、CPU/メモリを観測。
- [ ] 30分/2時間の長時間実験、再接続100回などの回帰確認。
- [ ] 必要ならGPU importを試し、コピー回数や同期を計測して最適化。
- [ ] 外部LinuxへのSSH転送は、Quest単体MVP完了後の追加機能として扱う。

## 予定ディレクトリ（実装時に調整可）

~~~text
eidolon-wayland/
├── README.md
├── docs/
│   ├── architecture.md
│   └── implementation-plan.md
├── android/                # Kotlin / Meta Spatial SDK アプリ（案）
├── native/                 # Android NDK renderer / JNI bridge（案）
├── compositor/             # Smithay / Wayland server（案）
├── transport/              # SSH + Waypipe adapter（案）
└── scripts/                # Quest/Termux setup・診断（案）
~~~

コードの分割やプログラミング言語は実験結果に応じてCodex/実装担当が変更してよい。現時点ではこのレイアウトを事実上の制約にしない。

## テスト戦略・確認項目

| 観点 | Test | 成功の目安 |
| --- | --- | --- |
| Build | Gradle、NDK、Rust target、aarch64 linker | 再現可能なAPK生成 |
| Surface | EGL draw、pause/resume、resize | 描画継続、leakなし |
| Protocol | configure/ack、buffer lifecycle、disconnect | デッドロック・プロトコルエラーなし |
| Transport | localhost SSH再接続、Waypipe転送 | 1ウィンドウで描画可能 |
| Security | sshdのlisten address、host key、鍵管理 | LAN露出なし、不正鍵拒否 |
| Input | Ctrl/Alt/Esc/US/JIS、pointer/scroll | CLI・GUI操作可能 |
| UX | サイズ調整、フォーカス、パススルー | 文字が読めて移動可能 |
| Reliability | スリープ復帰、Termux再起動 | 正常復帰・リソース回収 |
| Performance | render latency、CPU、熱、メモリ | まず計測とベースライン確立 |

## 設計判断の記録（初期ADR）

### ADR-001: compositorをAndroid APK内に置く

**採用。** 別UIDのTermux内でcompositorを動かして表示を別アプリへ取り出すより、空間ウィンドウ管理と描画パイプラインを一体化させる。Android向けの描画バックエンド実装は必要。

### ADR-002: Termuxとの境界はlocalhost SSH + Waypipe

**採用（実現性検証を前提とする）。** 通信先を127.0.0.1に限定し、Waypipeを通じてWaylandプロトコルとSHM bufferを転送する。SSHだけでWaylandのFDが透過的に渡るわけではない。Android側Waypipeの移植は未検証。

### ADR-003: terminal emulatorをSpatial SDKで再実装しない

**採用。** footなどのWaylandアプリを直接表示する。Android側はWayland server・描画・入力・空間管理を担当。

### ADR-004: 最初はSHM + CPU copy

**採用。** GPUのbuffer import/zero-copyは初期MVPをブロックさせない。性能目標を数値で決めるのは動作測定後。

## 実装担当（Codex等）への引き継ぎ

このドキュメントは仕様のベースラインであり、**実装者の設計裁量を不必要に制限しない**。採用技術の変更が合理的なら、根拠・代替案・検証結果を文書に残して前進すること。先にすべてを完成させるのではなく、重要な不確実性から短いスパイクで解消する。

最初に推奨する作業:
1. Meta Spatial SDK公式サンプルからQuest実機で描画する最小APKを起こす。
2. Android側Waypipe/Smithayのbuild feasibilityを**別々に**実証する。
3. Termuxのlocalhost SSH接続・鍵認証を確認する。
4. Wayland clientの表示と入力の往復ができたところでMVPを定義し直す。

次の条件を満たすまで、README/ドキュメントで「Quest単体のWayland XRデスクトップが動作する」とは書かないこと。
