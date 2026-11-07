# NX01〜NX18: 計画全体の継続記録

2026-11-07、ユーザーが既存計画を最後まで進めるよう指示した。
誤登録されたゴール`view`をユーザーが解除した後、[次フェーズ計画](next-phase-termux-startup-linux-gui-plan-20261107.md)の
NX01〜NX18を対象とするゴールを再設定した。開始基準は`4ea6986` / Build142。
実装・PC/Questの自動検証を先に進め、ユーザーによる装着・物理操作は最後にまとめる。
文書の更新や一部テストの成功だけで計画全体を完了にしない。

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
| NX09 | ユーザー指定で一旦保留 | Fcitx再利用の調査結果を保持。保留は完了ではなく、NX10以降を先行 |
| NX10 | カタログ・照会契約を実装中 | TOML読込・構造化argv・固定helper照会から開始。UI/登録保存/実foot起動・取消と回収は後続 |
| NX11 | 未着手 | SDL/SHM小probe、成立後Doom/Freedoomの個別実測 |
| NX12 | 未着手 | 二枚で窓別移動/close/状態、テーマ指定操作部、サイズ/正面復帰 |
| NX13 | 未着手 | 実UIDのDisk I/O probe、計測契約、内側回転 |
| NX14 | 未着手 | 自プロセスCPU/PSS、熱/容量等。全体CPU/GPUは取得経路ごとに判断 |
| NX15 | 未着手 | 小さな一般GUI一つの描画/入力/dialog/resize |
| NX16 | 未着手 | 二→三→六→九枚、指定順配置、資源予算、10枚目制限 |
| NX17 | 未着手 | 同種窓の集約、500ms展開、前面/取消/reduced motion |
| NX18 | 未着手 | Linux接続先profile/helper、小probe、ランチャー統合と切断回収 |

## NX09の開始調査（現在は保留）

`scripts/termux/eidolon-japanese`は固定ttyskk・共有辞書・専用設定と学習fileへの入口を既に持つ。
通常shellのrcは置き換えず、設定の初回作成と再導入時の保持は`quest-setup-extensions`にある。
次は辞書欠落・書込み不可・破損時の案内と英数への復帰、二つのwrapperでの学習保存、
tmux/nvim・PTY/終了・focus切替を順に確認する。ランチャー統合は任意で、NX10の汎用起動と重複する固定modeを増やさない。
上流ttyskkは前回と同じ固定revisionのcheckoutで調査する。現時点では追加修正や新しい検証の成功を主張しない。

### ユーザー指定によるFcitx優先への変更

調査中にユーザーがSKKの操作体系を避け、Termux内のFcitxパッケージ再利用を希望した。
現在の方針と成立条件は[日本語入力計画の更新](japanese-input-plan-20261107.md#更新-termuxのfcitx5を使う2026-11-07)を正とする。
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

### PC検証と試行錯誤

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

残る順序: (1) Android/Rust sessionへの一覧接続、(2) 一時定義をfootへ渡す起動・取消・回収、
(3) 設定fileへの登録/編集と外部変更検出、(4) 左下の複数アイコン・＋・再読込・位置保存、
(5) Quest自動検証、最後にユーザーの視認・物理操作。

## 記録

再開記録は、その時点の`codex-work-root`設定配下の`eidolon-nx09-nx18-20261107/`へ保存する。
各区切りでsource/APK/hash、実行条件、失敗と条件変更、回収結果を追記する。
構造preflightはeidolon repository未登録であるためsourceを直接確認する。Pi directも登録対象外のためCodexが実装する。
RAGの計画snapshotは更新前の内容だったため、`source_changed=true`を確認してローカル正本を採用した。
