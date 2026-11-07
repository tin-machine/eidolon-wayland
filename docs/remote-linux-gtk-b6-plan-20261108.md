# NX18 B6: Linux固定GTKの描画・合成入力

B4/B5の実機検査で、APK UIDから隔離Linux SSH接続、固定100frame SHM受信、SDK描画、
remote/local回収、通常SDK復帰まで確認した。この基盤の次として、固定GTK3 fixtureを同じ一時runtimeへ接続する。
通常Linux GUI/catalog/任意shell/普通のprofile選択UIはまだ有効化しない。
HMDの見え方と物理キーボードは最後の手動gateとして残す。

## 分割と所有境界

1. **B6a: Linux helperとGTK bundle**。既存pattern modeのwireと3file manifestを維持し、追加の固定
   `gtk-bundle.json`で共有C source、GTK fixture binary、既存base bundleを束縛する。
   起動は`gtk_input` / `gtk_dialog` / `gtk_resize`のみ。commandは事前captureしたcaseとnonceから生成し、
   control stdinへpath/argv/環境を渡す口を追加しない。独立SSH/SHM前のhost fixtureを先に確認する。
2. **B6b: transport/core**。GTK専用pinsとtyped counterを同認証済みSSH handle/Routerへ接続する。
   journalにはprobe種別とGTK bundle/source/binary pinも束縛し、既存SHM journalの自動移行やscope付替えをしない。
   GTKをPatternの100hash条件へ通さず、固定GUI用AppModeとreportを分ける。
   capabilityは`ProbeReady`のまま、debug featureが無いcoreは新config/commandを拒否する。
3. **B6c: APK SDK/input case**。B5のHost lease、v2 binding/revision、単一workerのcredential提供、
   private preferences、SDK復帰の二段階finalizeを再用する。
   既存GTK caseのcapture/合成入力/retirementの手順を共用できる部分だけ取り込み、通常footがある前提は持ち込まない。
   lease中の通常`inputTarget`拒否は維持する。所有caseだけの小dispatch APIで、runtime/scene/fulltarget/epoch、
   inputIntent、最新SDK適用状態を同じ直列化境界で検査する。単なるhandle交換や全体gateの解除はしない。
4. **B6d: 実機と再現CLI**。固定receiver/Activityのcase/nonceだけで起動し、専用結果をallowlist保存する。
   以下の三caseを別nonceで順に検査し、通常設定不変・回収・SDK復帰までを終端とする。

## Linux bundleと固定実行

既存private bundleの`bundle.json`はB4/B5の3file契約を保持する。
追加の`gtk-bundle.json`はschema/backend/revision、base bundle digest、
`gtk3-interaction-fixture.c`と`eidolon-gtk3-fixture`のdigest、ビルド時GTK versionを含む固定schemaとする。
ユーザー所有の0700 directory、0600 manifest/source、同UID・非group/world writableのregular executableを要求する。
root所有system Python/GTK librariesと、ユーザー所有の固定binary/stateを区別する。
ビルド時versionは実行時の全shared library同一性を証明する値として扱わない。

GTK CはTermux用と同じ修正版を使う。`gtk_widget_realize`だけではapp_id設定が保持されないため、
GTK初期化前のprogram nameとmap直後のsetterを使い、main/dialogの最終app_idとparent関係を実wireで確認する。
dialog初期のmain app_idから専用dialog IDへの遷移を、二つの別windowの成功として数えない。

helper nonceは既存の16hex。fixture run IDは`nonce + nonce`という固定32hexへ導出する。
main app_idは`io.github.tinmachine.eidolon.gtkfixture.r<run-id>`、dialogは`.dialog` suffix。
外部32hex nonceとhelper16hexの対応はB4/B5と同様に明示して保存する。

Waypipeはpin済み0.11.3、`--no-gpu --compress none --threads 1`、既存の同owner StreamLocal socketだけを使う。
GTK childはWayland/SHM用の固定環境とし、既存desktop/D-Bus/SSH-agent/Python/linker環境を継承しない。
HOME/config/cache/data/tempは自分のnonce directory内へ向け、既存account HOME設定を書き換えない。
生成したgroupが生存中はGTK stateを削除しない。boundedでnofollowな所有state回収が不成立ならreceiptを成功にしない。

## counterとprocess identity

child stdoutは本文を記録せず、固定Cのtyped JSON counterだけを最大256行/512KiBで取り込む。
重複key、不明field、host selftest、UID/case/run_id/app_id/PID/sequence不一致、counter逆行、
final後の出力、過大geometryを拒否する。stderrはbyte数だけで、Wayland/key/入力本文をそのまま保存しない。

Waypipe group leaderとGTK PIDは同一とは限らない。最初のlive reportでPID/UID/starttime/groupと
pinしたGTK executableのdevice/inodeを照合し、以後同一processとして束縛する。
終了後に観測できないPIDを推測で補わず、まだ生きている他processへ付け替えない。
helperの`gtk_verified`は固定counter・自然終了・回収に限る。実SHM・SDK・入力配送の証拠とは分離する。

## 三caseの受入れ

| case | 実clientの証拠 | SDK/native側の独立証拠 |
| --- | --- | --- |
| input | `a` down/up各1、Backspace down/up各1、変更2、固定button click1、他キー0、complete/final/exit0 | 同fulltargetをfocusし、entry/buttonの実geometryへ固定pointer、同epoch/tokenのkey配送、pressed/button0、SHM内容とdrawの前進 |
| dialog | opened/closed/response各1、parent成立、最終非表示、main/dialog draw | 専用app_idのdialogを同clientに束縛、parent main一致、Escapeまたは固定dialog操作後のdialog退役、main継続、その後main回収 |
| resize | 640×360→800×500→640×360、configure/draw前進、complete/final/exit0 | 同fulltargetのconfigure ACK→SHM commit→SDK成功drawを段階確認。要求値だけで適用済みにしない |

全caseで実受信contentがSHMであること、non-SHM/unknown contentを受け入れていないことを確認する。
GTKのcounter自己申告だけでは合格にしない。別window/sessionの古いframeや、role確定前の一時app_idを採用しない。
合成入力は物理入力と明確に区別し、kanji変換やIMEの成功も主張しない。

## 回収と復帰

一つのcaseは有限deadlineを持ち、通常成功は自然終了/exit0/最終reportを要求する。
取消、lease/最大時間切れ、EOF、bad pin、counter不整合では自分が生成したgroupだけをTERM→KILLで回収する。
保存PIDへの後日signal、別nonce停止、profile変更、journal自動削除をfallbackにしない。

成功終端は、main/dialogのnative render・Board・panel command・SDK Entity/listener/control/MODE資源の回収、
forward取消、channel EOF/exit、remote receipt、GTK専用journal clear、credential scope消去、local runtime破棄、
通常設定digest不変、通常SDK診断画面のfresh復帰、lease解放を全て満たす。
途中のdraw成功やlocal復帰だけをremote回収成功にしない。回収不能は同ownerを保持し、同nonce stopだけを再試行可能にする。

## 検証の順序

1. parser、pin、case固定、PID/group、stdout上限、bad input、取消/EOF/timeout、private stateのhost tests。
2. Linuxの修正版Cをビルドし、host compositor/WaypipeでSHMとapp_id/parentを確認。
3. feature/release境界とv2 scope/journal回帰、B4/B5の互換性を確認。
4. rootが一致APK/helper/bundleを配備し、SDK/input/dialog/resizeを実機で一項ずつ確認。
5. 最後にユーザーがHMDと物理操作を確認する。

この計画の追加だけではB6実装・実機受入れを完了扱いにしない。

## B6a checkpoint: helperのみ

固定CLIは `prepare-gtk-input` / `prepare-gtk-dialog` / `prepare-gtk-resize` と16hex nonce。
開始controlのmodeもそれぞれ `gtk_input` / `gtk_dialog` / `gtk_resize` へ一致させる。
stdinからcaseを変更できない。従来の`prepare`/`pattern`/`cleanup`と3file base manifestは維持する。
新modeのnative/transport/Host配線はB6b以降であり、このcheckpointだけではAPKから起動できない。

`tools`は追加bundle/source/binary pinとbuild時GTK version、`gtk_report`は固定typed counterを返す。
`finished.gtk_verified`は最終counter条件・exit0・未強制終了・自group全滅・pipe EOF・state回収のAND。
`pattern_verified`はfalseのまま。`gtk_report_error`は固定tokenのみで不正入力本文を保存しない。
レコードに含まれるfixture PID/UID/counterは実SHM・SDK成功証拠と別に扱う。

GTK stateの回収は`g/`の下だけで、6階層・256entry・16MiBの上限、同UID、regular file/nlink1、
directory nofollowを要求する。symlink・所有違い・上限超過はowner recordを保持してpendingにする。
この制約は通常GUIの永続HOME用途ではない。既存accountの設定を保存・移行する機能も追加しない。

固定CのLinuxビルドは、正しいbase bundleが既に配備されたLinux上のcheckoutで行う。

```sh
scripts/build-linux-gtk-bundle \
  --bundle-directory "$HOME/.local/lib/eidolon-linux"
```

system `/usr/bin/cc` と `/usr/bin/pkg-config`、GTK3.24.x development packageが必要。
ビルド対象はこのcheckoutの固定Cだけで、任意source/flags/argvを受けない。
source・binary・manifest・`gtk-build/`が既にあれば上書きせず拒否する。
成功時もbinaryは実行せず、source/binary/base/manifestのdigestとcompiler出力量だけを返す。
失敗時の部分生成物は自動削除・再試行しない。新しい正規bundleを準備する手順へ戻る。
`gtk-build/build.json`はビルド証拠であり、remote sessionのreceiptではない。
今回helper本文が変わるため、base bundle/helper pin・APK source digestも次版で同時に更新する。

検証コマンド（PC上、ADB/Quest操作なし）:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p 'test_linux*helper.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_linux_gtk_build.py -v
```

既存helper 10件、GTK parser/所有/PID/子group/EOF・stop・lease/回収17件、build境界4件を検査する。
小Cのprocess fixtureはWaypipe相当leaderとreport出力childを別PIDにし、同group/executable照合を実際に通す。
これはGTK GUIではない。固定GTK Cの実compileは確認するが、今回新helper経由のLinux SSH/Waypipe/SHM、
SDK/input/dialog/resizeはまだ実行していない。KWin上の共有Cのrole検査は
[既存GTK記録](gtk-interaction-fixture-20261108.md)を参照し、B6全経路の成功へ合算しない。

環境変数の意味は[GTK3実行時設定](https://docs.gtk.org/gtk3/running)と
[Wayland backend](https://docs.gtk.org/gtk3/wayland.html)を参照した。
`GIO_USE_VFS=local`は[公式GIOの説明](https://gnome.pages.gitlab.gnome.org/gtk/gio/overview.html)に基づく。
private環境の指定だけで全libraryを隔離したことやSHM受信成功を保証しない。

### 並行build時の回収検査

最終host検証を並行build中に再実行すると、既存SHM helperの回収testも失敗した。
529 processがあるPCで自分の終了済みgroupだけを5回検査し、従来50msの全`/proc` scanが
4回`GROUP_SCAN_LIMIT`（50〜53ms）、1回29msで終了した。group生存を確認した結果ではなく、
時間上限による「回収不明」であり、これを成功へ読み替えなかった。

worker上のscan上限だけを250msへ調整した。16,384entry、同UID/PID/starttime/group、
leaderのWNOWAIT保持、finish全体2秒、曖昧時pendingは維持した。
回帰では60msのscheduler待ちを許容し、300msの待ちは上限として拒否することを検査した。
変更後はhelper27件＋build4件の31件がPASS。実APKの回収結果とは区別する。
