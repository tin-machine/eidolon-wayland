# Phase 3 — Android compositorとWaypipe

P3の大きな移植をW01〜W05へ分割する。最初にprotocolだけを成立させ、描画とfootを後から接続する。既存Waypipeを再利用し、転送protocolを作り直さない。

<a id="w01"></a>
## W01 — native依存のbuild gate

作成: `native/core/src/compositor/`、`native/deps.lock.json`、`scripts/build-native-deps`。

Smithay `=0.7.0`、`default-features=false`、最初は`wayland_frontend`/`desktop`だけ。DRM/GBM/udev/libinput/libseat/Xwayland/winitを有効にしない。crate sourceで`xkbcommon 0.8.0`依存がoptionalでないことを確認したため、native libxkbcommonを工程に含める。

[独立probe](../native-preflight-20261104.md)ではlibxkbcommon 1.8.1をstatic linkし、QuestのAPK内でDisplay・wl_shm global・自己完結keymapの初期化が通った。[固定sourceとbuild条件](../../probes/native-deps/deps.lock.json)を初期候補として再利用する。SHM buffer処理、SIGBUS/ART共存、SmithayのEGL adapterは未確認であり、この初期化結果だけでW01〜W03を完了にしない。

1. NDK 27.3/API 34/arm64用sysrootを唯一のtarget環境としてlibxkbcommonをcross buildする。hostのpkg-config pathをtargetへ流さない。
2. upstream source版、URL、SHA-256、license、patch、Meson options、NDK情報を`deps.lock.json`へ記録する。libxkbcommon等の未選定版を勝手にlatestへ追従させず、このgateの結果として固定する。
3. libxkbcommonはX11/Wayland tool/UIなど不要な付随機能を無効にし、必要なlibraryだけを生成する。設定optionは固定sourceのMeson定義で確認する。
4. runtimeがhostのxkeyboard-configを読む必要がないよう、US/JISのcompiled text keymapをビルド時に生成し、生成元版・layout・hashとともにAPK assetへ同梱する。runtimeは文字列からkeymapを作る。
5. `cargo tree -e features`と`llvm-readelf -d`で依存を確認する。Android/Bionicにないlibudev/GBM/host ELFが入ったら失敗。
6. APK UIDでevent loop、短いUnix socket、fd送受信、memfd/mmap、keymap生成を個別に試す。

このgateが移植不能ならW02/W04を完了扱いにしない。原版に必要なpatchは`native/patches/<dependency>/`へ最小diffと理由を残す。古いAndroid向けforkを丸ごと採用しない。

<a id="w02"></a>
## W02 — 最小protocolと同じUIDのテストclient

作成: `protocol.rs`、`native/fixtures/shm-client/`。rendererとSSHは使わない。

公開globalの初期上限はwl_compositor v4、wl_subcompositor v1、wl_shm v1、wl_output v3、wl_seat v5、xdg_wm_base v1、wl_data_device_manager v3。Smithayのstate/delegate/serial管理を使う。採用APIでcap指定できるかを確認し、実際のregistry dumpを保存する。生成済みhandlerが処理できないrequestや拡張を広告しない。

foot 1.28.0の`wayland.c`はdata_device_managerがないと起動を中止するため、これだけをI07まで延期しない。W02でSmithay DataDeviceStateを導入し、device/source生成・破棄・offer登録・set_actions等のv3 requestを処理する。初期selectionはnull、転送offerを他clientへ出さない。set_selection/start_dragはseat serial/focus/roleを検査し、未対応の転送を開始せずsource取消とFD回収へ収束させる。空selection/取消/破棄をfixtureで検証する。実データ転送・clipboard機能の合格はI07に残す。registryへ名前だけ広告してrequestを未処理にする実装は不可。

仮想outputは一つ、scale 1、初期mode 1024×768、refresh 60000 mHz。wl_output geometry/mode/scale/done、surface enter/leaveを送る。seatはこの段階では未実装capabilityを広告せず、keyboard/pointerはI02/I03で追加する。

最低限保持するstate:

```text
ClientState: client_id, owned_surface_ids, resource_counters
SurfaceState: surface_id, role, pending/current state, parent, buffer metadata
ToplevelState: window_id, title/app_id, lifecycle, configure serial, geometry
CompositorState: Display/DisplayHandle, globals, clients, surfaces, output, seat
```

title/app_idはUIへ渡す前に長さ上限(各512 UTF-8 bytes)を適用し、通常ログに全文を出さない。

fixtureはPNGやfontを使わず、memfd上の色パターンとframe番号をwl_shmで送る。registry bind→xdg surface/toplevel→bufferなしcommit→configure受信→ack→buffer attach/commitの順をコード化する。SDKパネル生成を待ってconfigureを遅延させない。

結果はframe番号、bufferの検証用hash、configure/ack/commit/release/callback回数。100回の更新とclient切断後のFD/objects回収を確認する。host実行とQuest APK UID内での実行を両方行う。

<a id="shm"></a>
## SHMのcopy・所有権契約

新規`shm.rs`だけに共有メモリー読取りのunsafeを隔離する。

1. `with_buffer_contents`のcallback内でformat、幅/高さ、offset、strideを検証する。全算術はchecked arithmetic。
2. `row_bytes = width*4`、`stride >= row_bytes`、`offset + (height-1)*stride + row_bytes <= pool_len`、共通上限を満たすこと。負値をusizeへ変換する前に拒否する。
3. 共有mappingから`&[u8]`を作らない。callback外へpointerを持ち出さない。保護下のraw read/copyで所有済みallocationに各行をcopyし、format/行方向を正規化する。
4. `ARGB8888`はlittle-endianでB,G,R,A、`XRGB8888`はB,G,R,Xを読みA=255とする。ARGBはpremultiplied alphaを維持し、もう一度alphaを掛けない。
5. poolの参照は読取り終了まで保持する。owned copyが完了し、同じbufferを使う他surfaceを含む全consumerが参照を終えた時だけreleaseする。
6. 予算確保に失敗したbufferや壊れたpoolは診断と対象clientのエラーへ収束させる。全APKのcrash・巨大allocationを許さない。

Smithay 0.7のSHM保護はSIGBUS handlerを使う。ART/SDK側handlerと共存するかをQuestで必ず確認する。pool truncateの悪性fixtureを独立試験として実行し、error→対象client終了→APK継続を判定する。handler競合があれば`needs_design`で止め、保護を外して継続しない。

<a id="w03"></a>
## W03 — lifecycleとsnapshot

作成: `snapshot.rs`、modelのwindow state。W02のfixtureへ異常・再生成caseを追加する。

- 初回null commit→configure/ack→map、null bufferによるunmap→新しい初期commit/configure→remap、destroy、client disconnectを別遷移にする。
- configure serialはSmithayに発行/追跡させる。存在しないserialのackを独自に受け入れない。新サイズの適用は対応commitまで待つ。
- subsurfaceの同期commitと重なり順を保持し、一つの整合したsurface treeをsnapshotへ変換する。
- popupは親のtreeへ取り込み、positioner/configureを処理する。初期は親のlogical rectangleでclip。I07でgrab/clipboardを追加し、親外canvas拡張は後続とする。
- scaleと8種類のbuffer transformを明示処理する。viewporter/fractional-scaleなど未実装の拡張は広告しない。

snapshotの形:

```text
WindowSnapshot { session, window, frame_seq, geometry, logical_size,
  layers: [ { surface_id, content_seq, offset_logical, clip_logical,
              transform, scale, size_px, pixels: Arc<OwnedRgba> } ] }
```

coreは最新のowned画像を保持し、surfaceがdamageなしで再利用される場合も現在のtreeを再描画できるようにする。全snapshot/textureを毎frame蓄積しない。destroyでcacheを解放する。

frame callbackは最大60 Hzのschedulerで、可視/target使用可能/予算ありのwindowに対して進める。protocol単体testだけは明示した仮想可視clockを使う。Surfaceがない製品windowでは抑制し、復帰時に再開する。buffer releaseとframe callbackとXR presentは異なる指標。

検査: bad stride/offset/overflow、異常format、巨大resize、truncate、複数surfaceが同じbufferを参照、遅いrenderer、unmap/remap、nested popup/subsurface、scale/transform、同一windowの連続configure。モデル/画像hashとprotocol回数をassertする。

<a id="w04"></a>
## W04 — APK向けWaypipe buildと配置

H01とW03の成功が前提。Waypipe **0.11.3 C版**を両端の基準にする。[固定Termux recipe](https://github.com/termux/termux-packages/blob/6c7082a257f4bd15dbca71bdfe1ecab9e3e5bab2/packages/waypipe/build.sh)と上流archiveを照合済み。

archive SHA-256: `2d4093903d444fda39c9b7db0a0b826f450a64d4302a03c9c9e3dbe480e8bb03`。

APK buildは`build_c=true, build_rs=false, with_dmabuf=disabled, with_video=disabled, with_vaapi=disabled, with_lz4=enabled, with_zstd=enabled`を起点にする。libwayland/liblz4/zstd/spawn互換の実依存もdeps.lockへ固定する。Termux prefixへのpatchをAPKへそのまま使わず、socket/runtime pathは明示引数・環境へ移す。`libandroid-spawn`が必要かはtarget側link/execで確かめる。

[W04の実装調査](../waypipe-build-checks.md)では、C版のlibwayland/scanner依存はsecurity-contextだけだった。
今回は`with_secctx=disabled`を追加し、その拡張をcompositorで広告しない。liblz4 1.10.0/Zstd 1.5.7をstatic linkし、
API34のlibcのspawnを使う。必須librt探索だけをAndroid用の固定patchで外す。APK側clientの実行・停止を実機で検査し、
remote側の版/features照合と実転送はW05で行う。

出力は`libwaypipe_bin.so`としてH01と同じ方式で同梱する。TermuxのELFをcopyしない。`--version`、features、SHA、DT_NEEDED、licenseをartifactへ保存する。依存soが必要ならnativeLibraryDirで解決し、Termuxのprivate library pathへ依存させない。

APK側argv/環境の契約（実装はAPK内のRust std::process::Command、下は説明用）:

```bash
XDG_RUNTIME_DIR="$app_runtime_dir" WAYLAND_DISPLAY=w \
  "$packaged_waypipe" --no-gpu --compress none --threads 1 \
  --socket "$app_runtime_dir/p" client
```

Waypipe C 0.11.3のhelp/parserで上記optionとmodeを確認した。`w`はcompositorのWayland socket、`p`はWaypipe転送socket。socketのlisten確認はpathの存在だけで終わらず、W05の実通信で判定する。`--oneshot`は複数clientの妨げになるため既定にしない。

Termux側のインストール済み版が0.11.3と違えば、黙って混在させず両端の版/featuresを揃えるtaskへ戻る。圧縮noneで成立させてから後続性能試験でlz4/zstdを比較する。

<a id="w05"></a>
## W05 — echo経路をWaypipeへ置換

footの基準候補は固定Termux recipeで確認した1.28.0。導入版・package revision・font/terminfo依存を記録し、異なる版を使う場合はその配布sourceの必須global判定を読み直す。既知の起動必須globalはW02で用意する。fixture成功だけでfoot起動成功と推定しない。

[Build49の実機検査](../waypipe-forwarded-shm-checks.md)では、固定起動引数へ
`--override=tweak.max-shm-pool-size-mb=0`を追加した。foot既定の512MiB仮想SHM pool最適化を無効にし、
compositorの64MiB/client上限は維持する。利用者の設定fileは書き換えない。
100frame/hashと実footのbuffer受信・所有資源の停止をnative scopeで確認した。
通常UI復帰、SDK表示と物理入力は未受入である。

T02の認証済み接続を再利用し、journalで採番されたSessionIdへcompositorを束縛する。
認証前に仮のSessionIdを発行せず、次の依存順で開始する。

1. T02のpin検証・SSH認証がReadyに到達する。
2. そのSessionIdでcore compositorが`w`をlistenする。
3. APK Waypipe clientが`p`をlistenし、実接続と生存を確認する。
4. remote directoryを準備し、両端の版/featuresを照合してT03のforward ACKを確認する。
5. remote launcherがTermux側Waypipe serverと固定appを起動する。最初はSHM pattern fixture、次にfootを選ぶ。

同じSessionIdでDisplayを再作成してwindow/surface IDを再利用しない。
[Termuxの固定版・font・producerの準備記録](../termux-wayland-tools.md)を使い、forward成功前にremote appを開始しない。

remote launcherが使うargv:

```text
waypipe --no-gpu --compress none --threads 1 --socket <remote-session>/p
  --display <remote-session>/w server -- <allowlisted-app-and-args>
```

`--display`はserver側が作るWayland display。APKのcompositor socketを指定するoptionではない。launcherはTermuxのPATH/必要なfontconfig環境を持ち、footは通常のTerminalアプリとして起動する。実行するアプリはmodeから固定argvへ写像し、ユーザー入力をshellへ埋め込まない。

確認: patternのframe/hash/configure/ack/releaseが直結fixtureと一致、続いてfootのtoplevel/buffer/継続更新。fontやlocaleの失敗はtransport障害と分ける。表示はD02の合格条件に残す。

停止はremote child→forward/bridge→local Waypipe→Wayland clients→socket cleanup。PIDだけで所有を判定しない。固定nonce/session markerを持ち、TERM→期限→KILL→waitを記録する。Waypipeの終了が隣のTermuxアプリを巻き込まないことを検査する。
