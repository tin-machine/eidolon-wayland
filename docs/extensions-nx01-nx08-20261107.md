# NX01〜NX08 実装・検証記録

開始基準: `7943c5a`。2026-11-07、ユーザーが直前の一覧1〜5の実装と検証を承認。
この一覧は次フェーズ計画のNX01〜08に対応する。実機の装着・物理キーボードによる受入れは最後に行う。
Phase 5の未完了受入れやPhase 6全体を、この拡張の実装だけで完了にしない。

## 実装

| 項目 | 変更 | 検証範囲 |
| --- | --- | --- |
| NX01 | Cyan Orbitの線幅を2→6へ。三つのリングの直径は維持 | 素材・描画検査、HMDの見やすさは最後 |
| NX02 | 背景、準備、切断、編集、キーボード未検出、対象未準備、未選択を固定上部へ表示。窓別の入力先ボタン | 純粋な優先順位テストと通常SDK `extensions` case |
| NX03 | ターミナル起動と入力復帰を上部へ。接続・配置・テーマ等は「設定と詳細」 | コンパイル・lint・実UI確認 |
| NX04 | 電池/充電、RAM空き/総量、既定ネットワークのIP。可視時5秒間隔 | 通常APKのAPIとdiagnostics `interface.device`。ADB値だけで合格にしない |
| NX05 | `ttyskk`の固定版・固定辞書を専用入口に導入する最小probe | PCとQuest実UIDの専用PTY成功。foot/HMDは最後 |
| NX06 | CPUは外側、RSSは中央の独立した回転速度へ。欠測・省動作では停止 | 速度/位相/旧形式互換/実renderのテスト |
| NX07 | localhost SSH接続拒否時だけ固定Termux helperを一回実行。結果通知→SSH再接続 | pin/認証は従来のまま。正常・権限拒否・取消等を別に検証 |
| NX08 | 左下のピンク色のfoot起動ボタン。主画面と同じ連打抑制を使用 | カタログ・任意file実行・九枚表示は後続 |

## リングの契約

manifest `format_version=2`に`[[rotations]]`を追加。v1テーマは従来どおり読み込む。
CPUは既存の平滑値とbusy判定を使い、0〜4 coresを0〜60°/秒へ写す。
RSSは0〜1 GiBを0〜−40°/秒へ写す。範囲外はclampする。
250msの速度遷移を積分するため、負荷変化やfocus変更で角度が飛ばない。
窓ごとに位相を持ち、Surface再生成・サイズ変更では保持する。
別workloadへの対応変更では前の計測値を捨て、位相を保持する。

欠測では角度を保持して停止し、opacityを0.35倍にする。再開時に欠測時間の回転を追いかけない。
内側のDisk I/Oは未計測のため停止・低opacityとする。低負荷のCPU停止中でもRSSがあれば中央は回る。
既存CSSのtransform animation/transitionと同じpartへのrotationの競合は拒否する。
`rotations`のmetric、part、上限、重複、非有限値を読み込み時に検査する。

性能検査の既存ラベル`animated`は「動きを有効・観測停止」の条件を指す。
v2ではこの区間の継続描画は0が正しく、`animated_metrics`では実計測値による継続描画を確認する。

## Termux必要時起動

`com.termux.permission.RUN_COMMAND`と`allow-external-apps=true`を一度設定する。
起動fileは`$PREFIX/bin/eidolon-start`に固定し、UI文字列をshellへ渡さない。
helperは既存service managerと`eidolon-sshd`を使い、down fileや他serviceの設定、鍵を変更しない。
通常の切断では共有sshdを停止しない。

結果は非export receiverへの一回限りの明示PendingIntentで受ける。
要求tokenと現在の操作を照合し、取消・timeout・runtime停止後の結果は捨てる。
返却stdout/stderr/errmsgを通常診断へ保存しない。診断には開始・成功・失敗の件数だけを含める。
起動要求は一操作一回、helper待機15秒、接続を含む操作全体45秒。
`SSH_CONNECTION_REFUSED`と認証・pin不一致を区別し、後二者は起動再試行に使わない。
成功はhelper exitだけでは判定せず、保存済みpinと鍵によるSSHのReadyを待つ。

## 日本語probeの再現

方式は端末内SKK。一般的な連文節変換とは操作が異なり、先頭の大文字で変換の区切りを指定する。
本機能は一般GUI向けのAndroid/Wayland IMEではない。

- ttyskk 0.4.7: revision `9ada7618c69585bb00d201796381a3e96ebbfe09`、MIT OR Apache-2.0。
- SKK-JISYO.L: revision `0a164e6b990c5eb5b59eb7d8789f08865dc2f644`、GPL-2.0-or-later。
  SHA256 `c791f578d1b4040fce282db29bc22b2cc7ea46f83e269fab2e0fa779e2967e40`。
- NDK 27.3 / Android API34でARM64へcross build。辞書・license・hash manifestをAPK外の成果物へ保存する。

```sh
# 設定済みwork root配下へsource/ビルド記録/artifactsを作成する。
python3 scripts/build-japanese-probe
python3 scripts/quest-setup-extensions --serial "$EIDOLON_QUEST_SERIAL" \
  --japanese-artifacts /absolute/work-root/eidolon-japanese-probe/artifacts \
  --grant-run-command
```

導入先はTermux内`~/.local/share/eidolon/japanese/`。
専用config、学習辞書`user-jisyo`を使用し、通常shellのrcや既存IME設定を書き換えない。
configは初回のみ作成し、再導入時は利用者の設定・学習を保持する。
APKの「ターミナルを開く」で開いたfoot内で`eidolon-japanese`を実行する。

1. `Ctrl+J`でかなモード。カーソル横の「あ」と未確定表示を確認する。
2. `Kanji`→Spaceで「漢字」、さらにSpaceで「幹事」、`x`で前候補へ戻る。
3. Enterまたは`Ctrl+J`で確定。確定のEnterだけではshellのコマンドを実行しない。
4. `Ctrl+G`で候補を読みへ戻し、もう一度で取り消す。`l`で英数へ戻る。
5. `exit`で専用shellを終了して通常shellへ戻る。

`scripts/termux/probe-japanese.py --binary PATH --dictionary PATH`は専用PTYと一時的な学習fileで
固定テキストだけを入力し、候補、確定、取消、Backspace、英数、矢印、resize signal、終了を検査する。
通常端末の入力を採取せず、物理キー・foot描画の合格とも区別する。
tmux/nvimとの全組合せ、窓間の未確定文字、GUIのIMEはNX09以降。

参照: [ttyskk](https://github.com/barewalker/ttyskk/tree/9ada7618c69585bb00d201796381a3e96ebbfe09)、
[SKK辞書](https://github.com/skk-dev/dict/tree/0a164e6b990c5eb5b59eb7d8789f08865dc2f644)、
[Termux RUN_COMMAND](https://github.com/termux/termux-app/wiki/RUN_COMMAND-Intent)。

## 試行錯誤と確認状況

- Kotlinの`IntArray.mapNotNull`がコンパイル不可だったため、device IDをListへ変換してから判定した。
- 初回Rust検査は生成済みSmithayがなく失敗。既存`build-native-deps --target host`で固定sourceから生成した。
- リングの旧テストは無計測でも常時回転する前提だった。v1互換テストを残し、v2は計測値を渡して検査した。
- `clippy`のnested if指摘を修正。lint初回にはindentation指摘があり、括弧と整形を修正して再実行した。
- 配置選択と編集終了が連続するとresetが複数積まれる。reset回数の推測をやめ、要求IDの完了通知を待つよう修正。
  その後の別操作・device変更・窓の世代変更では自動focusを取り消す。
- ttyskkの初回cross linkはhost linkerを使って失敗。固定NDKのAndroid clangを明示して成功。
- 辞書repositoryの`COPYING`取得は404。辞書自身のGPL-2.0-or-later表記を確認し、GPL-2本文を成果物へ同梱した。
- PC上の専用PTYでは変換・候補・確定・取消・英数復帰・Backspace・矢印・resize signal・正常終了が成功。
- Wi-Fi ADBは接続後にofflineへ移る症状があり、targetの再接続・PC側server再起動を実施。
  一時deviceへ戻っても継続せず、PC側の実装/検証を継続した。原因をスリープと断定していない。

- Questを電源ボタンで起こした後、Wi-Fi ADBが復帰。既存のスリープ・給電設定は変更していない。
- 通常APKのUIDから電池・充電・RAM・既定ネットワークのIPを取得した。
- 専用sshdを一時停止した状態からRUN_COMMANDが1回成功し、保存済みpin/鍵で接続、footを起動した。
  検証後に専用serviceを起動状態へ戻した。
- RUN_COMMAND権限拒否とallow-external-apps=falseの各条件は、案内を表示して接続を停止した。
  検査後に権限と設定を復元した。許可ダイアログの手動受入れとは区別する。
- QuestのTermux実UIDでも専用PTYの日本語probeが成功。物理入力とfoot上の候補視認は未確認。
- Build134のSDK検査は静止画面に3frameを要求してtimeoutした。最新内容の2frameは描画済みだったため、
  「1frame以上かつ論理窓の最新frameと一致」へ修正した。
- release134は空き容量不足で失敗。新SDK検査fileの書き込みも失敗したため元内容を復元した。
  同一SHAの作業APKをhardlinkで共有し、検査済みhostの生成cacheだけを削除して容量を確保した。
  release135のassemble/lint/JUnit/静的APK検査は成功した。
- Build135は二窓目のfocus、Build136は編集終了後のfocusでtimeoutした。
  一回限りのenqueueを、対象世代・入力意図・可視状態を照合してresetとfocusの完了まで待つ処理へ改修し、
  Build137で取消理由と完了codeを追加して、編集終了時のtarget=nullを特定した。
  描画開始からSDK appliedまでの間にも対象を保持し、Build138では編集復帰・二窓切替・解除後の復帰が成功した。
- Build138の実操作は成功したが、検証開始がAPK初期化と競合し、検証環境のoriginal_handleが0になったため
  外側の判定は正しく拒否した。SDK検証環境が初期runtimeのReadyを待つよう修正し、Build139で再確認した。
- 自動起動の取消テストでは、遅れて返るhelper成功を無視し、追加接続・窓作成を行わないことを確認する。
  初回の外側判定は接続拒否後の資源を持たないFailed状態を許可せず失敗した。
  取消後は元の失敗sessionを維持できるため、session不変・所有接続0・成功通知の不採用を検査するよう直した。

- Build139のextensions検査は初期runtimeの待機を含めてpass。既存keyboard検査にも静止画面の3frame待ちが残っており、
  最新frame一致へ修正した。手動のfocus回復が内部でresetを行うため、最初の未選択記録だけはfocus後より古いepochを許す。
  確定したfocusからキー入力までのepoch一致、解除後のepoch更新、古いキーの拒否条件は維持した。

## 検証結果

- `scripts/check-host --suite all`: Rust165件、Termux33件、Python70件成功（APKなしの7件はskip）。fmt/clippy成功。
- 最終の判定コードでBuild140の実APKを指定したPython78件成功。静止frameの受理/拒否を含む。
- Build138の通常SDK `theme-sdk`: run `20261107T184120-9906cded`、5cyclesと二窓・性能比較・製品設定復帰がpass。
- Build139の通常SDK `extensions`: run `20261107T184602-6deed2d1`、編集復帰、窓指定、解除、連打抑制、電池/RAM/IP、設定復帰がpass。
  専用sshd停止から製品UIDで起動し、RUN_COMMANDはrequests1/successes1/failures0。
- 日本語のPC/Quest専用PTY、Termux権限拒否・連携設定無効・取消後の遅い通知破棄がpass。
- debug140のassemble/lint/JUnit/静的APK検査、release138のassemble/lint/JUnit/静的APK検査がpass。
  Build141では実UIで見つかった不要なnull表示と、稼働中の終了記録を未回収警告に見せる表示を修正した。
- unsigned release138 SHA256: `c55dd5f3e46e2bfac1cf89fed8ecf0ad4d9d754586c3c9c852873d2fc58c0019`。
  配布用署名・release実機受入れは含まない。

入力回帰を検証したdebug140 SHA256は`cd2a0034a2a5bacd59fbbe4b7d49cf6dc836b26054c78e4837fa3543335cff92`。
作業基準`7943c5a` + dirtyから作成した。commit後に同じ版番号で作り直したAPKとは区別する。
通常SDK keyboardの8段階はrun `20261107T184951-2aa90813`でpass（固定合成キー、物理入力未検証）。

導入済みdebug141 SHA256: `baffcaf130680e416e96e62110f4f7c02b5e351a5e1988b5697888e175c0b992`。
source digest: `c4d901b2a52b0c8a719aafbda655d4d133943b9106ceeca164b2d14b7484b5b8`。
Build141の通常主画面で二窓を開き、窓2→窓1のボタンから実focusの変化を確認した。
不要なnull表示が消え、主操作とmetricsが画面内に収まることも確認した。
通常主画面の二窓ボタンと詳細scrollをADBで操作・撮影した。画像と端末固有の生ログはprivate作業先へ保存する。
Cyan Orbitを選択し、動きを有効、負荷観測を有効にして手動確認へ渡す。
検証のため変更したGuardian/proximityは元の設定へ復元済み。専用sshdもrunへ戻した。


最終release141のassemble/lint/JUnit/静的APK検査もpass。
unsigned APK SHA256は`8e3e5b5a6b8c1a19321f9ce1b7911f2e7ffc02535573e0214e50c36c62d56765`。
実機に導入したのはdebug141であり、配布署名・release実機受入れを主張しない。

## 最後の手動確認

1. 主画面のBuild番号と入力状態、電池/RAM/IP、左下のピンク色ランチャーを確認する。
2. 「窓1へ入力」「窓2へ入力」で実キーボードの入力先を切り替え、配置編集を終了して入力へ戻れることを確認する。
3. 三つの太いリングとCPU/RSSに応じた外側/中央の動きを確認する。内側は停止する。
4. footで`eidolon-japanese`を実行し、上記SKK手順で「漢字」の候補表示・確定・英数復帰を確認する。

Guardianを一時停止したSDK検査・合成キー・専用PTYの成功を、HMD視認や物理キーの合格には置き換えない。
後続NX09以降のGUI IME、任意fileランチャー、Doom、九枚・グループ化、Disk I/Oは今回の対象外。

