# NX01〜NX18: 計画全体の継続記録

2026-11-07、ユーザーが既存計画を最後まで進めるよう指示した。
誤登録されたゴール`view`をユーザーが解除した後、[次フェーズ計画](next-phase-termux-startup-linux-gui-plan-20261107.md)の
NX01〜NX18を対象とするゴールを再設定した。開始基準は`4ea6986` / Build142。
実装・PC/Questの自動検証を先に進め、ユーザーによる装着・物理操作は最後にまとめる。
文書の更新や一部テストの成功だけで計画全体を完了にしない。

**追加レビュー後のユーザー指定で、日本語入力の実装を再開し優先して進める。**
[Android IME先行の新計画](android-ime-wayland-plan-20261107.md)に従い案Aの小さなSpatial入力probeを先行し、固有の制約で成立しなければ案BのTermux Fcitx5へ進む。
方式選定に必要なHMD/物理確認は採用ゲートとする。標準欄・独自editorの試作と自動検査は先に準備し、装着確認をまとめる。
以下のFcitx/ttyskk調査は履歴として保持する。A1/A2限定試作とC1を実装し、Build145でnative wire v2、Build147でSDKクリック経由の実Android入力接続を確認した。
実IMEによる日本語変換は未確認。版ごとの証拠と不具合修正は[日本語入力の実装記録](android-ime-implementation-20261107.md)に記す。

## 引き継ぐ証拠と未完了条件

NX01〜08の実装・失敗・自動検証は[前回記録](extensions-nx01-nx08-20261107.md)。
ユーザーはBuild142のリング線幅30を採用し、中央の回転と物理文字入力の成功を確認した。
物理判定・enqueue・実配送の各46件も一致した。入力本文は記録していない。
Backspace個別、かな漢字変換の候補視認、ランチャーの物理押下を今回の回答だけで合格にはしない。
[Phase 5の手動項目](phase5-manual-acceptance.md)、[Phase 6](design/phase-6.md)の残件は別途保持する。

| ID | 継続開始時点 | 次に確認・実装する範囲 |
| --- | --- | --- |
| NX01 | 太さはユーザー採用、素材/SDK検査成功 | 後続のテーマ変更・多窓でも維持 |
| NX02 | 固定上部の状態・復帰、自動検証済み | 窓横表示をNX12へ接続し、物理状態遷移の受入れを保持 |
| NX03 | 主操作を固定上部へ移動済み | ランチャー拡張後も主操作の可視性を維持 |
| NX04 | 電池/充電・RAM・IPを製品UIDで取得済み | 残るメトリクスはNX14へ |
| NX05 | 固定ttyskk・辞書、PC/Quest専用PTY成功 | 実footでの描画・入力と最終の物理かな漢字変換 |
| NX06 | CPU/RSS独立回転、自動検証済み | 中央回転はユーザー採用。停止/欠測・多窓回帰を維持 |
| NX07 | 固定helper、権限/取消等の自動検証済み | 再起動後を含む実利用の起動・復帰を継続確認 |
| NX08 | 左下footボタン、ADB起動成功 | ユーザーへ「>_ ターミナル」と案内済み。NX10へ拡張 |
| NX09 | ユーザー指定で優先再開 | Android IMEのSpatial probeと共通text-input-v3を分担して準備。実footへ段階化し、案A固有の不成立ならTermux Fcitx5 |
| NX10 | カタログUI・保存・稼働中footへの任意起動を接続 | Build143のPC検査。初回任意起動・個別取消・配置保存とQuest実機確認は残件 |
| NX11 | SDL/SHM小probeを実装、隔離host成功 | Questで公式SDL2/Mesa経路を検証し、成立後Doom/Freedoomへ |
| NX12 | focus/closeに加え[move/窓横MODE](nx12b-window-move-mode-20261108.md)もBuild161のSDK固定case成功 | サイズ/正面復帰、HMD/物理受入れ |
| NX13 | Build159でI/O値とdrawを照合し、Build163で専用root/子のidentity退役と一時data残存0を外部確認 | thread/共有tmux、多窓、HMD。APK内の回収未検証flagは外部証拠と区別 |
| NX14 | CPU148、熱/容量152、PSS154、gateway164を製品UID・停止復帰確認 | HMD表示受入れ、追加値。全体CPU/GPUは取得経路ごとに判断 |
| NX15 | [固定GTK](gtk-first-frame-probe-20261108.md)のSHM/SDK初描画・回収をBuild160で確認 | 入力/dialog/resize、製品起動・Stop、HMD |
| NX16 | [九枠台帳・起動相関・回収](nx16a-slot-reservations-20261108.md)を実装、Build164の二窓で確認 | 表示は現時点2枚。三→六→九枚、指定順配置、資源予算、10枚目制限 |
| NX17 | 未着手 | 同種窓の集約、500ms展開、前面/取消/reduced motion |
| NX18 | v2接続設定・endpoint別認証を実装し、[Build162の実APK SSH probe](remote-apk-probe-b2-20261108.md)成功。通常接続はv1を維持 | Linux helper SHM/実APK描画、ランチャー統合と切断回収 |

## NX09の開始調査（当時の保留までの履歴）

`scripts/termux/eidolon-japanese`は固定ttyskk・共有辞書・専用設定と学習fileへの入口を既に持つ。
通常shellのrcは置き換えず、設定の初回作成と再導入時の保持は`quest-setup-extensions`にある。
次は辞書欠落・書込み不可・破損時の案内と英数への復帰、二つのwrapperでの学習保存、
tmux/nvim・PTY/終了・focus切替を順に確認する。ランチャー統合は任意で、NX10の汎用起動と重複する固定modeを増やさない。
上流ttyskkは前回と同じ固定revisionのcheckoutで調査する。現時点では追加修正や新しい検証の成功を主張しない。

### ユーザー指定によるFcitx優先への変更

調査中にユーザーがSKKの操作体系を避け、Termux内のFcitxパッケージ再利用を希望した。
当時の方針と成立条件は[日本語入力計画の更新](japanese-input-plan-20261107.md#更新-termuxのfcitx5を使う2026-11-07)に記録した。
現行の順序は[Android IME先行の新計画](android-ime-wayland-plan-20261107.md)に従い、この調査を案Bで再利用する。
Termux公式fcitx5は存在するがWayland無効。Mozcのrecipeも同revisionにないため、そのままの導入だけでは足りない。
APKへのエンジン同梱へ直行せず、Termux側の有効化・追加packageと、EidolonのIME protocol接続を分けて検証する。

方針変更前にはttyskkの学習とimportを24同時writerで試し、共通一時fileのrename失敗を再現した。
上流の既存テストは順次保存であり、同時保存を検証していなかった。修正はまだ適用・配備していない。
この不合格fixtureとログは作業rootに保持し、製品の通常testへ未修正のSKK fixtureを追加しない。
既存の学習・設定fileは削除しない。実機apt照会はADB offlineで未完了、PC側のsource調査は継続可能。

### 日本語変換を保留してNX10へ移る

ユーザーが「日本語変換の実装はスキップして次の課題を開始」と指定したため、
Fcitxの移植・IME protocol実装を一旦止め、ランチャー拡張を先行する。
この保留はゴール全体の停止・NX09の完了ではない。Termuxの既存データ・Build142は維持する。

## NX10: カタログと照会契約

`eidolon-session.py`の固定helper内にTOML schema 1の読込・検査を追加した。
最大256KiB/64項目、一覧応答は8項目ずつ。全件検査後に採用し、失敗時は直前の正常な一覧・revisionを保持する。
初回は組込みterminal/editor定義を持ち、未導入の実行fileは理由付きで無効にする。
任意iconはまだ読まず標準素材へ戻す。GUIはNX11の検証まで`GUI_NOT_VERIFIED`とする。

認証済みSSHとhelper hash照合後、同じlease上の`catalog_v1`で`reload/page/inspect`を扱う。
request IDは単調増加とし、nonce/UID/IDが一致しない応答をRust側で拒否する。
設定の間違いは構造化errorとして返し、既存terminalやsessionの終了理由にしない。
不正なwire envelopeは従来どおり停止・回収する。入力は最大16KiB、従来commandは引き続き1KiBに制限する。
path/argvをSSH commandへ埋め込まず、照会はprocessを起動しない。通常Report/receiptにcatalog本文を含めない。

選択する実行fileはHOME基準のpathを解決し、通常file・実行/interpreter権限・cwdを確認する。
登録IDとrevisionから起動内容を取得する際は、外部編集・削除を再確認する。
`~`・変数・globの暗黙展開はしない。argv配列を直接渡すため、空白・日本語・空引数・shell記号を保持する。
ここでのargv検証とPC上のprocess実行は、製品foot内への起動・UI完成の合格ではない。
設定fileは同UID所有でgroup/other書込み不可の通常fileのみ読み込む。symlink/FIFO/deviceは拒否する。
TOML parserはPython 3.11以降の`tomllib`を使い、未導入なら`CATALOG_UNAVAILABLE`を返す。
その場合も既存terminalの起動・停止を壊さず、組込み一覧を保持する。

### カタログ基盤のPC検証と試行錯誤（前回2b6a380）

- Termux helperのPython testは45件成功（今回のcatalog 12件を含む）。
  実fileの読込、64項目のページ分割、重複ID、未知schema、過大・深い不正TOML、削除・権限・FIFO、
  interpreter、通常の`Popen(argv)`、制御pipe経由の照会と終了回収を確認した。
- `cargo test --locked -p eidolon-transport`は31件成功。実OpenSSH経由のcatalog読込・エラー後の継続、
  旧revision・応答scope拒否、既存の認証/heartbeat/EOF/lease/停止・回収を含む。
- 最初の実SSH testはfixtureの設定fileをホストのumask `0002`のまま作り、group書込み可で読込を拒否された。
  fixtureを明示0600へ直し、成功した。製品の検査は緩めていない。
- `cargo clippy --locked -p eidolon-transport --all-targets -- -D warnings`、fmt、diff検査、設計検査も成功。
- ADBのdevice一覧は空。APK/helperは実機へまだ配備しておらず、Build142の表示・入力は変更していない。
  script hash照合があるため、実機検証時は同じsourceから作ったAPKとhelperを組で更新する。

再現コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/termux/tests -v
cargo test --locked -p eidolon-transport
cargo clippy --locked -p eidolon-transport --all-targets -- -D warnings
cargo fmt --all --check
python3 scripts/check-design
git diff --check
```

### Build143: 画面・保存・起動を接続

ユーザーの明示指示で担当を分け、rootがKotlin/Rustと統合、別agentがTermux保存/起動、
別agentがNX11、別agentがAndroid IME計画のレビューを進めた。
作業中のリモート追加`b0b28ec`はfast-forwardで取り込み、利用者の計画変更を維持した。

- 左下をターミナル・カタログの2項目まで・「＋ アプリ」の横並びへ拡張した。
  メイン操作パネルの「アプリ」からも同じ一覧を開き、設定pathの指定/再読込、8件ずつの一覧を扱う。
  同じ接続でfootがrunningになったとき、一度だけ保存済みpathを読み込む。読込では起動しない。
- 「ファイルから起動・登録」は実行path、interpreter、cwd、引数を一項目ずつ保持する。
  「今回だけ起動」と「設定へ保存」を分離し、同ID保存は置換する。GUIの起動はまだ無効。
- `catalog_v1/save`は同helperのwriterをflockで排他し、読込revisionと交換直前の内容を照合する。
  新規fileは作成競合でも上書きせず、既存fileは同じdirectory内で原子的に交換する。
  lockを守らない外部editorが最終検査とreplaceの間に書く極短時間の競合まで、厳密なCASと主張しない。
- `open_terminal_v2`は登録ID/revisionまたは一時定義を受け、既存foot serverへ追加する。
  owned session内の0600定義を固定`terminal-app` wrapperが検査・消費し、cwd/argvでexecする。
  `footclient --hold`で終了後の画面を残す。footclientの成功はアプリ成功ではなく、窓のmapを別に確認する。
  起動失敗で共有serverを停止せず、一時file・短命childは失敗/全接続停止時に回収する。
- カタログのUI応答は専用JNIへ分離し、通常Snapshot/receiptへ表示名・引数を含めない。
  session/requestの一致、queue上限と古い応答拒否を維持した。
- 設定欄にfocusがある間はLinuxへの物理キー転送を止め、「入力先: ランチャーの設定欄」を表示する。
  担当間レビューで、起動後やLinux窓選択後にもCompose focusが残り得る点を見つけた。
  起動/窓選択から実際のCompose focus解除を要求し、focus変更通知でgateを開けるよう修正した。

検証はmodel 39件、transport 32件、lifecycle 56件、Termux/SDLのPython 65件。
実SSHで新規保存・追記・古いrevision拒否、実PTYで空白/日本語/空引数/shell記号、
wrapperからexec後の同じPIDと終了コード7、起動失敗・回収と無関係processの存続を確認した。
Build143のassembleDebug・lintDebug・JUnit 14件・APK整合検査は成功した。
最終artifactは`20261107T220727-595639/eidolon-debug.apk`、SHA256は
`7bc83fcc43a08034956e93508e828d246203539eb8c419b1760ea37a2bfa0bea`。
build時の基準は`b0b28ec`＋未commit差分、source digestは
`a392d34163c6395b987e6ef9f35d45b5b42b025e64164db822cc79ce0c6e1711`。
helperは86,676 bytes、SHA256
`8bc260278f3e8556cade44c4dd7d6c0b2fe01913432c502be1ae0ae9bbf155cf`。
後続の文書・独立SDL probe変更はこのAPKの実装を変更しない。
統合commit `8b0fcb4`の[GitHub Actions run 42376143943](https://github.com/tin-machine/eidolon-wayland/actions/runs/42376143943)も
host/Androidとも成功した。CIのAPKはversionCode 1であり、ローカルBuild143と区別する。
Wi-Fi ADBは`No route to host`で、実機へ配備していない。Build142の実機確認をBuild143の合格へ流用しない。

再現時は上記Python/Rust検査に加え、`scripts/check-host --suite lifecycle`、`scripts/build-android 143`を使う。
APKとTermux helperを同じsourceから更新し、最初のfoot→設定path再読込→登録保存→
空白を含む引数で一時起動→Linux窓を選択して入力→停止・回収の順に実機確認する。

残る作業は、任意アプリを最初の一窓として起動する入口、起動準備中の個別取消、
既存定義を編集フォームへ読み戻す機能、ランチャーの移動/位置保存、Quest自動検証、最後のHMD/物理操作。
現段階は普通のターミナルで接続してからカタログ/任意起動を使う。NX10全体は完了ではない。

## NX11と日本語入力計画の並行作業

[SDL/SHM検証記録](sdl-shm-probe-20261107.md)の小C clientと観測runnerを追加した。
隔離Weston/pixmanとMesa CPU経路で10回ともtoplevelのSHM更新を確認し、warmup後のWeston FDは13→13。
`SDL_FRAMEBUFFER_ACCELERATION=0`ではrenderer作成に失敗したため、一律のEGL無効化を前提にしない。
Quest・Eidolon compositor・Doom/Freedoomは未確認で、次は公式SDL2/Mesaを実機で試す。

[Android IME計画](android-ime-wayland-plan-20261107.md)のレビューでは、表示display/Window focus、
footのsurrounding text非対応、固定protocol版、transaction検査、InputConnection失効と本文ログを具体化した。
特に固定Smithayはserial確認より前に文字イベントを送るため、不一致`done`を取消として使わず、
transaction全体を先に検査するgateを追加した。静的読解の懸念を実機再現済みの不具合とは扱わない。
日本語変換は引き続きユーザー指定で保留し、このレビューだけで再開しない。

### 日本語入力計画の追加独立レビュー

ユーザーの追加依頼で`8b0fcb4`を基準に、Quest/SDK、protocol、実装費用・順序を三担当で再確認した。
大筋のAndroid IME再利用→共通text-input→foot、案A固有の不成立時にTermux Fcitxを検証する方針は維持した。
実装や実機操作を再開せず、次の改善を計画へ反映した。

- SDK 0.15.0にはIMMの文字編集状態によりパネルのMotionEvent配送前にreturnする処理がある。
  鍵盤の非表示と入力接続の終了を区別し、入力後のcontroller/hand操作と再開までprobeへ追加した。
  固定AARのbytecodeで確認した分岐であり、Questでの操作不能を再現したわけではない。
- 初回のSmithay先送出・foot pending残留の指摘は別担当も確認した。
  後続の新commitでは残留が上書きされるため、preedit-only/空doneを使う反例に具体化した。
  複数text-input object・active object破棄・enable前pendingの初期化も最初の小fixtureへ含めた。
- 標準bridgeとbridge API patch/小さな自前dispatchを、必要修正量・所有範囲・停止契約で比較する。
  周辺テキストが読めることを任意カーソル移動や再変換の対応済み根拠にしない。
- A1/A2の試作準備と方式採用を分離し、同じdebug APKで比較して装着機会をまとめる。
  端末のC2-TをNX09、GUI固有のC2-GをNX15へ分け、全体計画の古い先行ゲート表現もそろえた。

再現調査・固定source/hash・各担当の報告は、作業rootの`eidolon-ime-extra-review-20261107/`に保存した。
文書検査は実装・IME互換性・実機成功の証拠ではない。NX09の保留と最後の手動受入れは維持する。

## 記録

再開記録は、その時点の`codex-work-root`設定配下の`eidolon-nx09-nx18-20261107/`へ保存する。
各区切りでsource/APK/hash、実行条件、失敗と条件変更、回収結果を追記する。
構造preflightはeidolon repository未登録であるためsourceを直接確認する。Pi directも登録対象外のためCodexが実装する。
RAGの計画snapshotは更新前の内容だったため、`source_changed=true`を確認してローカル正本を採用した。
