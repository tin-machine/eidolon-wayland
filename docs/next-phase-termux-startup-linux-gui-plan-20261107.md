# 次フェーズ案: Termuxの必要時起動とLinux GUIアプリ

作成日: 2026-11-07。状態: **相談・計画のみ。実装開始の指示待ち**。
調査基準は`5a515541296bee5227dfd09c87bb0104a66fbfec`、直近の実機候補はBuild133。
この文書の追加ではsource変更、パッケージ導入、APK更新、実機実験を行わない。
[既存の進捗](progress.md)と[Phase 6](design/phase-6.md)の未完了条件は維持する。
正式なPhase番号・tasks.jsonへの追加は、下記の小規模検証で方式が決まってから行う。

## 目指す体験と今回の出発点

1. Quest再起動後でも、Eidolonの「ターミナルを開く」から必要なTermuxサービスを起動し、接続できる。
2. Termux上の小さなゲームを、Eidolonの平面パネルに表示してキーボードで遊べる。
3. 成立した経路を、ゲーム以外のLinux GUIアプリを起動する仕組みへ広げる。

2026-11-07のユーザー確認では、スクロールすると「ターミナルを開く」が見つかり、
再起動後に停止していたTermux/sshdを開発側で起動するとターミナルが開いた。
ボタンが存在しない問題と、接続先が起動していない問題を混同しない。
初期画面で主操作を見つけやすくし、接続設定・詳細状態はその後に置く改善も候補とする。

現状のfoot自体がWaylandのGUIアプリであり、表示・入力の基礎は既にある。
一方で、任意のLinuxアプリに必要な描画方式やprotocolがすべて揃ったわけではない。

## 1. EidolonからTermuxを起動する

### 採用候補

| 方法 | 役割 | 確認する点 |
| --- | --- | --- |
| AndroidのIntentでTermuxのActivityを開く | 初回準備・復旧用の入口 | Quest上での画面切替とEidolonへの復帰。Termuxを開くだけでsshdが立つとは限らない |
| Termuxの`RUN_COMMAND`で固定helperを実行 | 通常の「ターミナルを開く」に組み込む第一候補 | EidolonのUIDから、Termux未起動でも必要なサービスを起動できるか |
| Termux側の起動時設定 | 補助案 | OS起動時の常駐は別要件。まずユーザーのボタン操作に応じた必要時起動を目指す |

`RUN_COMMAND`はTermuxの公式連携機能である。[公式仕様](https://github.com/termux/termux-app/wiki/RUN_COMMAND-Intent)に従い、
Eidolonのmanifestで`com.termux.permission.RUN_COMMAND`を宣言し、ユーザーに許可してもらう。
Termux側の`allow-external-apps=true`も必要になる。package/serviceの検索に必要な
`<queries>`を限定して追加する。現在のEidolonにはこれらの連携実装はない。

初回設定は対話が必要になるが、その後の毎回のターミナル起動では同じ確認を繰り返さない設計を目指す。
この権限はTermux内でのコマンド実行を可能にするため、製品から呼ぶ入口は固定helperに絞る。
画面上の任意文字列をshellとして実行するAPIにはしない。

### 起動処理の案

1. 保存済み接続先・pairing状態を確認する。対象がこのQuest内のTermuxである場合だけ起動処理を使う。
2. 通常のSSH接続を試みる。既に利用可能ならサービスを再起動せず、そのまま既存の起動処理へ進む。
3. 未起動が疑われる接続拒否の場合、許可・Termuxの存在・helperの導入状態を確認する。
4. `com.termux.app.RunCommandService`へ明示Intentを送り、background commandとしてhelperを一度起動する。
   helperは既存のservice managerを確認し、`eidolon-sshd`だけを冪等に起動する。
5. 結果通知と上限付き待機の後、既存のhost key pin・認証を通したSSH接続で準備完了を判定する。
   TCPポートが開いたことだけでは成功にしない。
6. Termuxを前面で開く必要がある環境では、理由と「Termuxを開く」を表示する。
   復帰後に接続を再試行できるようにする。

初期値案は一操作につき起動要求1回、準備待ち合計15秒、同時要求1件。
遅延時は期限付きの再確認を行い、連打をまとめる。実測で値を調整し、変更理由を残す。
起動中・接続中・設定が必要・起動失敗を区別し、取消後や古い世代の結果を新しい画面へ適用しない。
結果のPendingIntentは要求IDと世代を照合し、出力の長さを制限する。

host key不一致・認証失敗をサービス未起動として扱わない。自動でpairingや鍵を作り直さない。
既存の`sshd`/`ssh-agent`等の有効無効設定も維持する。
Eidolonの切断では所有するセッションと子プロセスを回収し、共有のTermux/sshdは停止しない。

### 先に行う小規模検証

製品と同じ権限条件のAPKから固定の無害なコマンドを実行し、結果を受け取る。
次に固定helperでsshdを起動して通常接続する。開発用ADBや`run-as`での成功を、
製品UIDからの成功に読み替えない。

Termux起動済み、OS再起動後の未起動、明示的な強制停止後を別ケースにする。
実際のTermux配布元・版・targetSdk、Quest OS/API、Eidolon版を記録する。
Androidの[background起動制約](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start)と
[package visibility](https://developer.android.com/training/package-visibility/use-cases)を確認し、
Quest上で常に無表示で起動できるとは事前に約束しない。
権限拒否、設定不足、未導入、helper失敗、timeout、連打、取消、画面復帰も確認する。

## 2. GUIアプリの表示で使えるもの・不足するもの

現在の経路は次のとおり。

```text
TermuxのWaylandアプリ → Waypipe/SSH → APK内Wayland compositor
                    → 所有済みRGBA画像 → Android GLES/SDK → Questのパネル
```

[Phase 3設計](design/phase-3.md)と
[compositor実装](../native/core/src/compositor/protocol.rs)を照合した範囲では、
公開globalは`wl_compositor`/`wl_subcompositor`/`wl_shm`/`wl_output`/`wl_seat`/
`xdg_wm_base`/`wl_data_device_manager`。クライアント画像はSHMから受け取る。
Waypipeは現在`--no-gpu`を用いる。APK側でGPU描画していることと、
LinuxクライアントのGPU bufferを取り込めることは別である。

| アプリ側の条件 | 現時点での判断 |
| --- | --- |
| native Wayland + 対応protocol + SHM画像 | 最初に狙う範囲。footで経路は成立済みだが、他アプリは個別検証 |
| SDL / GTK / Qt | toolkit名だけで互換性を断定できない。版、描画backend、要求globalを確認する |
| X11専用 | Xwayland等の追加経路が必要。最初の検証と分ける |
| GL/Vulkan・DMA-BUF依存 | 現在の入力経路では成立を保証できない。CPU側への出力経路、または描画方式の追加設計が必要 |
| 相対マウス・pointer lock | 現状の絶対位置入力とは別機能。最初のゲームはキーボードで操作する |
| 音声 | Waylandの画面転送には含まれない。Termux/Androidでの出力を別に検証する |

TermuxのARM64/Bionic向けパッケージを最初の対象とする。
Ubuntu等のglibc向けバイナリーをそのまま動かせるという意味ではない。
proot内のdistribution、Xwayland、GPU転送、ゲームパッド対応は後続候補に置く。

## 3. 最初のゲーム候補: Chocolate Doom + Freedoom

初代Doom系のエンジンを平面パネルに表示する案。VR版DoomやDoom 3への移植は対象外。
Termux公式repositoryに[Chocolate Doom 3.1.1のrecipe][doom-package]があり、
依存する[SDL2 2.32.10のrecipe][sdl-package]もWaylandを有効にしている。
`x11-repo`というrepository名だけでX11専用とは判断しない。
これらは調査時のパッケージ定義であり、Questに導入済み・動作済みという意味ではない。
実際に解決される依存が`sdl2`か`sdl2-compat`かも記録する。

ゲームデータは[Freedoom](https://freedoom.github.io/)を第一候補にする。
フリーなDoom互換データなので、有料版DoomのWADを用意しなくても検証を始められる。
エンジンとデータの版・取得元・hash・licenseを別々に記録し、commercial WADをrepositoryへ含めない。
将来ユーザー所有のDoom WADを指定する入口は別に設けられる。

### 最大の事前確認: ソフトウェア描画でもSHM出力とは限らない

Chocolate Doomはゲーム画面をCPUで生成するが、最後の表示にはSDL rendererを使う。
[Chocolate Doomの表示実装][doom-video]、[SDL2 Wayland backend][sdl-wayland]、
[SDL2のwindow framebuffer実装][sdl-video]を確認すると、SDLのsoftware renderer指定だけで
EGLへの依存をなくせるとは言えない。
`SDL_VIDEODRIVER=wayland`や`SDL_RENDER_DRIVER=software`を指定するだけで動く、とは扱わない。
Waypipeの`--no-gpu`も、任意のGPU描画を自動でSHMへ変換する保証にはならない。

まず同じSDLパッケージで、小さなウィンドウ・色の更新・キー入力・終了だけのprobeを作る。
利用driver、要求global、EGL初期化結果、実際のbuffer形式を測定する。
必要ならMesaのCPU描画経路を候補として調査するが、SHMへの出力を実測するまで採用決定しない。

probeが成立しない場合はSDL/backendの組合せを見直すか、SHM対応の小さなゲームを選び直す。
Doom専用のSHM出力を追加する案もあり得るが、それだけでは一般的なSDL/GTK/Qt対応の証明にはならない。
DMA-BUF、Xwayland等の大きな追加へ進む前に、必要な変更と検証結果をこの計画へ追記する。

### 最初のDoom検証条件

- 小さなウィンドウ（例: 640×400）、無音、マウス取得なし。US配列キーボードでメニュー・移動・終了を確認する。
- 最初はEidolonのfoot内から起動する。同じWaypipeセッションの`WAYLAND_DISPLAY`と
  `XDG_RUNTIME_DIR`を引き継げるか確認し、socket名を固定で埋め込まない。
  通常のTermux画面で起動するだけでEidolonに表示されるとは想定しない。
- 現在の二枚表示上限を守り、foot一枚とゲーム一枚で始める。第三windowの拒否を描画不良と混同しない。
- 短いプレイと再起動を先に試す。全画面、音、マウス、ゲームパッド、長時間性能は段階を分ける。
- パッケージを導入する前に、版・依存・取得元・変更対象を記録する。既存環境を一括更新しない。

probe成功はDoom成功ではなく、Doom成功もすべてのLinux GUIへの対応完了ではない。
起動コマンドは利用する実パッケージのオプションを確認してから、再現手順として確定する。

## 4. 実装を再開した後の順序と完了条件

| 段階 | 作業 | この段階の成功条件 |
| --- | --- | --- |
| 1 | Termux連携probe → 固定helper → 起動UI | 製品UIDから必要時起動・通常SSH認証。失敗理由と復帰方法が分かり、連打・取消で重複起動しない |
| 2 | SDL表示probe | 現行Waypipe/SHM経路で画像が更新され、キー入力と終了が成立。必要なbackend・依存が特定できる |
| 3 | Chocolate Doom + Freedoom | 通常SDKパネルでゲームが動き、focus切替・操作・終了・再起動が成立。footへ戻れる |
| 4 | 登録済みGUIアプリの起動 | 検証済みアプリを固定ID・実行ファイル・argv・環境設定で起動し、状態・終了・子windowを管理できる |
| 5 | 一般GUIへの拡張 | 小さなGTK/Qtアプリを一つ選び、描画・入力・dialog・resize等を個別確認。X11等は結果を見て別設計 |

段階1と2の調査は独立して進められる。段階4の対象は、段階2・3で動作条件が確定したものに限る。
現在の[Termux launcher](../scripts/termux/eidolon-session.py)はfoot等の固定modeを扱っており、
汎用GUIランチャーとして既に使えるわけではない。
拡張時はapp IDとsession/windowの対応、世代、所有するprocess group、終了コード、
起動後のtimeout、利用できる表示枠を管理する。未登録の任意shell文字列は入口にしない。
dialog・popupと独立toplevelを区別し、親を閉じた際の回収方針も定義する。

自動確認では、通常SDKでの描画更新、注入入力、focus喪失時の押下解除、取消、異常終了を先に進める。
短い反復は起動・終了10回を初期案とし、warmup後のFD、残存process、SHM・socket・panel資源を測る。
既存の[FD調査](gpu-fd-investigation.md)と停止契約を使い、増え続ける資源を固定費とみなさない。
ゲーム終了後は所有する資源を回収し、残すfootと共有サービスの資源は分けて集計する。
10回の成功をPhase 6の長時間受入れの代わりにはしない。

HMDでの読みやすさ・入力感覚・実キーボード操作は最後にユーザーへ依頼する。
これまでのUS101互換キーボード・Fキー検証免除の指定を引き継ぎ、Fキーを必須操作にしない。
実機未確認は`not_run`のまま残し、host testやbuildだけで完了にしない。

## 記録と保留事項

各実験ではsource revision、APK/hash、Termux・SDL・ゲーム・データの版、実行コマンド、
描画backend、エラー、仮説、条件変更、再試行結果、回収結果を残す。
鍵・token・ユーザー入力・室内画像を通常ログへ含めない。
再開用artifactは作業開始時に読み直した`codex-work-root`配下、再利用する判断は本書またはrunbookへ要約する。

今はゲームデータにFreedoom、入力にキーボード、表示に小さな平面パネル、音なしを仮置きする。
音・マウス・ゲームパッド、GTK/Qtの具体的な対象、X11/GPU対応は先行検証後に相談する。
ユーザーへの追加質問を待つ必要がある項目と、実装者が小規模検証で決められる項目を区別する。
**現在の依頼は計画作成であり、この文書を実装開始の承認として扱わない。**

## 調査元

Termuxパッケージ定義は調査時のcommit `d017404e330d5ae5d2595f3887ab654c78916e9b`に固定した。
実装再開時には現行定義と実機の導入候補を照合し、差を記録する。
SDL2の調査は`release-2.32.10`、Chocolate Doomは`chocolate-doom-3.1.1`のsourceを用いた。
Android/Termuxの運用条件は上記公式文書を再確認する。

[doom-package]: https://github.com/termux/termux-packages/blob/d017404e330d5ae5d2595f3887ab654c78916e9b/x11-packages/chocolate-doom/build.sh
[sdl-package]: https://github.com/termux/termux-packages/blob/d017404e330d5ae5d2595f3887ab654c78916e9b/x11-packages/sdl2/build.sh
[doom-video]: https://github.com/chocolate-doom/chocolate-doom/blob/chocolate-doom-3.1.1/src/i_video.c
[sdl-wayland]: https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/wayland/SDL_waylandvideo.c
[sdl-video]: https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/video/SDL_video.c
