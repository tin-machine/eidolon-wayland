# 次期アプリケーションランチャーの計画

作成日: 2026-11-07。調査基準: `727aad3`。**相談・計画のみ。実装開始待ち**。
[次フェーズ計画](next-phase-termux-startup-linux-gui-plan-20261107.md)第9節の詳細。
ユーザーは、左下の数個のアイコンからfoot以外も起動できるランチャーを希望した。
追加確認により、**実行ファイル・スクリプトの指定**と、**設定ファイルによる登録**の両方を要件とする。
追加相談の[remote Linux GUI拡張案](remote-linux-gui-plan-20261107.md)では、これらをUbuntu等の接続先profileにも結び付ける。
本書のTermux用path例は初期段階のもの。将来のremote起動では選択したマシン上のpathとして扱う。

## 使い方と初期配置

作業空間の左下に、小さな横長のランチャーを既定表示する。
初期案はアプリ3個程度と「＋」の入口。大きく丸いkawaii / sweet / popなボタンを使い、
短い名前とhover/押下状態で用途を見分けられるようにする。

```text
          作業中のウィンドウ

  左下:  [ ターミナル ] [ エディター ] [ ゲーム ]  [ ＋ ]
                                                    ├ ファイルから起動
                                                    ├ ランチャーへ登録・編集
                                                    └ 設定ファイルを指定・再読込
```

初期候補はfoot、foot内のnvim、GUI検証後のDoom。初期内容は導入済みpackageと
検証結果に合わせて確定する。未導入/未対応の項目は理由を表示して無効化でき、空のアイコンにしない。
ランチャー表示だけでpackageの導入やゲームデータの取得を行わない。
登録数が増えた場合は「すべてのアプリ」へ展開し、初期のアイコンを細かく縮め続けない。

「左下」は、初期の視線を基準にした作業空間の左下を初期案とする。
頭を動かすたびに追従するHUDにはせず、位置を保持し、「ランチャーを手元に戻す」入口を残す。
ユーザーによる移動・配置保存を可能にし、テーマの既定位置より保存配置を優先する。
九枚の初期配置、特に左下の窓・操作ボタンと重ならない距離と余白をHMDで確認する。
最初は自動で隠さず、ランチャーを見つけるためのhover操作を要求しない。

ランチャーはEidolonの共通UIとして扱い、**最大9枚のアプリウィンドウには数えない**。
描画面・texture・入力領域等の資源には計上する。既存のメイン設定画面にも起動・復帰の入口を残す。

## 二つの起動入口

### 実行ファイル・スクリプトを指定する

最初の対象は、接続済みTermux内のLinux実行ファイルとスクリプト。
「ファイルから起動」でpath、引数、作業directory、表示方式を指定して一度起動できる。
その場限りの起動に恒久登録を要求せず、同じ内容を「ランチャーに追加」で保存することもできる。
ファイル指定はpath入力を最低限とし、Termux内の一覧から選べるUIも段階的に追加する。

- **GUIとして起動:** 検証済みのWayland/Waypipe経路へ接続する。
- **ターミナル内で実行:** foot内でCLI/TUIやscriptを実行し、入力と出力を扱う。
- scriptは実行可能なshebang付きfile、または明示したinterpreterとscript pathの組で扱う。
  interpreter指定時はscriptを引数として渡し、script本文を起動要求へ埋め込まない。
- 引数は一つずつ入力・保持する。実行fileのpathに引数を連結したcommand文字列にしない。
  空白や日本語を含むpathも一つの引数として扱い、拡張子だけでGUI/CLIを決めない。
- terminal内のprogram終了後は、出力・終了状態を読めるよう残す初期案とする。
  閉じる操作でfoot窓を回収する。単なるターミナル起動は対話shellを継続する。

APKからTermuxのprivate fileを直接読む設計にはしない。pathの照会・実行は認証済みSSHと
Termux側helperで行う。file一覧はdirectory指定・件数/サイズ制限付きで取得し、再帰走査しない。
Androidの共有ストレージfileや`content://`を、Termuxの実行可能pathと同一視しない。
Android APKの起動、文書を関連アプリで開く機能、PC上の任意アプリ起動は今回の初期対象に含めない。

### 設定ファイルで登録する

TOML形式のランチャー設定を候補とする。既定pathは**Termux側**の
`$HOME/.config/eidolon/launcher.toml`とし、別fileを指定して読み込む操作も用意する。
設定はアイコンと起動内容の一覧であり、読み込み自体はアプリ起動を伴わない。
保存済みのfileを明示再読込する方式で始め、常時watchは必須にしない。

登録する情報は、安定したID、表示名、アイコン、GUI/terminalの区分、実行file、引数配列、
任意のinterpreter、作業directoryとする。schema versionを持たせ、項目の並びをアイコン順に使う。
相対的な実行file・作業directoryはTermux home基準、相対アイコンpathは設定fileのdirectory基準と
明記して正規化する。shellの`~`、変数置換、globを暗黙に評価しない。
environmentの指定が必要なGUIは、検証済みの描画backend設定と合わせて追加設計する。

次は**提案schemaの例であり、現在のEidolonが読める設定ではない**。
通常のTermux pathを例示しているため、実装時は接続profileで確認した実際のhome/prefixと照合する。

```toml
schema = 1

[[apps]]
id = "terminal"
label = "ターミナル"
icon = "builtin:terminal"
mode = "terminal"
executable = "/data/data/com.termux/files/usr/bin/bash"
args = ["--noprofile", "--norc", "-i"]
cwd = "/data/data/com.termux/files/home"

[[apps]]
id = "my-script"
label = "マイスクリプト"
icon = "builtin:script"
mode = "terminal"
executable = "scripts/start-tool.sh"
interpreter = "/data/data/com.termux/files/usr/bin/bash"
args = ["--profile", "my workspace"]
cwd = "/data/data/com.termux/files/home"
```

UIでの登録も同じschemaへ変換する。UIからfileを書き換える場合は、読込時のrevisionを照合し、
外部編集があれば再読込へ案内して上書きしない。新fileを検証してから原子的に置き換える。
TOML全体の構文不正・ID重複・未知schema・予算超過は新設定を採用せず、最後の正常設定を維持する。
実行fileが一つ消えた場合はその項目だけ起動不可にし、他のアイコンを使える状態にする。
初回で正常設定がなければ組込みのターミナルと設定入口を残す。

初期の予算案は設定256 KiB、64項目まで。最初に表示する件数とは分けて扱う。
アイコンは組込み素材またはサイズ上限を設けた対応画像とし、既存のSVG subset等の制約を引き継ぐ。
画像不正・未対応・欠落は標準アイコンへ戻し、ラベルと起動操作を残す。
設定に含まれるURLから画像や実行fileを自動取得する仕組みは追加しない。

## テーマとの役割分担

テーマはランチャーのanchor、offset、方向、間隔、色・形・大きさ、hover/pressed状態を指定する。
左下・横並びは初期テーマの既定であり、共通実装へ固定しない。
窓別ボタンのanchorが本文基準であるのに対し、ランチャーは作業空間の基準を使う。
これは次期テーマschemaの項目であり、既存v1で指定済みとはしない。

**起動するprogram・引数はランチャー設定が所有し、テーマには持たせない。**
テーマを交換しても登録内容は維持する。装飾OFF・旧テーマ・不正テーマでも標準ランチャーを使える。
最小hit寸法、色の判別、ラベル、見た目とhit領域の一致、押下途中のreload取消を窓別操作と共有する。
配置変更・theme reloadでアプリを勝手に起動しない。クリックは背後のLinux窓へ流さない。

## 現行実装から必要になる拡張

[Rust launcher](../native/transport/src/launcher.rs)は固定modeとnonceでSSH commandを構築し、
[Termux helper](../scripts/termux/eidolon-session.py)はfoot server/client等の固定起動を扱う。
現行のscript hash照合・mode/JSON形状検査を外して任意commandを通す変更にはしない。
以下を新しいversion/capability付きの起動契約として設計する。

1. カタログ読取・対象file照会・起動要求を、固定helperへの構造化messageとして追加する。
   起動要求は登録IDと読込revision、または明示した一時定義を参照する。
   theme由来のデータを実行要求として扱わず、path/argvをSSH command文字列へ挿入しない。
2. GUI/terminalのどちらも、Termux側で正規化したpath、file種別、interpreter、実行条件、cwdを検査する。
   `Popen`等へ引数配列で渡し、`sh -c`へ利用者入力を連結しない。
   symlinkは解決先と実行対象を確認し、欠落・directory・権限不足は理由を分ける。
3. 起動要求ID、設定revision、接続/session世代、起動instanceとwindowの対応を管理する。
   アイコンの登録ID、Waylandのapp ID、個々のWindowIdは同一と仮定しない。
   入力のpress/releaseを一回の要求にし、同じ起動の準備中は連打をまとめる。
   起動後の追加クリックでは新しいinstanceを開く初期仕様とし、将来の既存窓選択とは分ける。
4. 必要なら既存計画のTermux必要時起動・通常SSH認証を先に行い、ready後に一度だけ起動する。
   切断・取消・旧世代の遅延応答で再実行しない。読み込み済みカタログの再表示も再実行にしない。
5. 新窓は現行runtimeの上限で予約する。九枚対応後は第7節の上限と中央→右→左…の順を適用する。
   登録できるアイコン数は窓数と別。追加toplevel・dialogにも同じ上限を適用し、失敗時は予約を解放する。
6. lease、所有process group、終了通知、stdout/stderrの上限、取消・停止・再接続の回収契約を引き継ぐ。
   shared foot serverの寿命と各terminalの作業、GUI instanceを区別し、他のアプリを停止しない。
   既存のTermux exec環境・Neovim自己再実行対策を保ち、無条件に環境を消さない。

起動状態は「準備中 / 起動中 / 起動済み / 起動できない」を表示し、退出コードと原因を確認できるようにする。
process生成だけでGUIの表示成功にしない。一定時間窓が現れない場合も、生成した所有資源を追跡して取消可能にする。
任意fileの指定ができることと、任意のGUI/backendが現在の描画経路で動くことは別に扱う。
通常診断には要求ID・登録ID・状態を残し、引数全文やscript出力を恒常的に保存しない。

## 実装再開後の順序と確認

全体の着手順は[次フェーズ計画の概算コスト順](next-phase-termux-startup-linux-gui-plan-20261107.md#implementation-order)に従う。
NX08では左下の「ターミナル」アイコンから既存foot起動を呼ぶ最小構成を先行する。
これは任意file・設定file・複数アイコンの要求全体の完成ではない。未実装の「＋」やGUI起動を使えるように見せない。
以下1〜3はNX10、GUI登録はNX11の表示検証後に進める。窓操作のテーマ契約が未完成でも標準外観で検証できる。

1. **カタログ・入力モデル:** schema、予算、path/argv、重複ID、不正file、reload原子性をhostで確認する。
   空白・日本語・shell記号を含むpath/引数を、分割やcommand解釈せず渡せることを確認する。
2. **基本のランチャー:** 左下のアイコン、組込みfoot起動、＋、設定読込、再読込、位置保存を接続する。
   旧テーマ・装飾OFF・設定不正でも起動と復帰の入口を保つ。
3. **ファイル指定:** 製品と同じ権限のSSH/helper経路でscriptとnvim等のCLIをfoot内に起動する。
   一時起動、登録、interpreter指定、終了後の出力保持、削除済みfile、取消、連打、再接続を確認する。
4. **GUI登録:** SDL/ゲームの表示条件が確定してからDoom等を追加し、表示・入力・終了と資源回収を確認する。
   ランチャーとfile指定の基礎は、このGUI検証と独立して進めてよい。
5. **複数窓・最終視認:** 上限到達、起動失敗の枠解放、他窓の存続、group機能との対応、停止後のFD等を確認する。
   HMDで左下の見つけやすさ、九枚配置との干渉、ボタンの押しやすさを最後に確認してもらう。

この追記ではコード・APK・Quest設定・既存tasks.jsonの状態を変更しない。
