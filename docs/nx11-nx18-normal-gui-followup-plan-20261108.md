# NX11・NX18: 通常GUI起動の現状と残る実装計画

2026-11-08追記。ユーザーが挙げた「3. ランチャーからのFreedoom起動」と「5. 別Linuxマシンへの通常GUI接続」の詳細計画。
記録基準はmain `2eeff89`、導入済みdebug APKはBuild176/source `f09a3b4`。
検証版・digest・APK hashは[Build176の記録](build-176-integration-20261108.md)を正本とする。
実装・実機検証・goalは相談のため一時停止中。本追記は開発再開を意味しない。
2026-11-08レビュー反映: 第2節をmain `ea068ba`と未統合C2 `43767a4`の静的レビューに基づき改訂した。
今回追加した修正・受入項目は計画であり、上記Build176や既存host検査へ新しい実装・実機成功を合算しない。

<a id="freedoom-normal"></a>
## 1. ランチャーからのFreedoom起動（NX10・NX11）

### 到達点と証拠の境界

| 項目 | 現状 | 証拠・制限 |
| --- | --- | --- |
| SDL/EGLからSHMへ出す表示経路 | 実機で成立 | SDL小probeとBuild172の固定Freedoom demo。software指定だけで成立すると推測した結果ではない |
| ゲームの描画と自然終了 | 固定demoで成立 | 128tic demo、SHM74commit、SDK最大68frame・5content更新、自然exit0、全資源退役・元foot維持。対話プレイは未確認 |
| engine/data/runtime | 固定版を配備済み | Chocolate Doom3.1.1、Freedoom0.13.0、SDL/Mesa等のhashとlicenseを個別固定。ゲーム用HOME/stateも分離 |
| 通常GUIの起動配線 | mainへ実装・PC検査済み | 型付きexecution profile、専用GUI cookie、mode付きreceipt、GUI workerとpidfd owner、通常helperの分岐を実装。通常ゲームの実機起動は未確認 |
| ランチャーへの登録 | 実機で成功 | 既定terminal/editorを維持し、固定ID `freedoom`を1件追加、計3件。登録時点でゲーム起動やcatalog reloadはしていない |
| 通常起動の自動検証入口 | mainへ実装・host8件成功 | `quest-run-freedoom-gui`のkeys/close。登録済み項目をreloadしてproduction `launchRegistered`へ渡す。実機caseは未実行 |
| GUIを最初の窓として開く | 下層APIとfixtureのみ | 隠れfootserver anchorは実装。Androidの初回接続UIはfoot先行で、最初からゲームを選ぶ導線は未接続 |
| 物理ランチャー・ゲーム操作 | 未受入れ | アイコンの見つけやすさ、実キー、HMD、ゲーム内移動・操作感は最後に確認 |

登録時のTermux差分は、`os.link`未提供→Bionic `linkat`のEACCES→専用fixtureで
`renameat2(RENAME_NOREPLACE)`の新規採用・既存target拒否を実証、の順で解決した。
他アプリを失う全設定置換や通常renameへのfallbackは行っていない。
現在のcatalog登録成功と、過去の失敗は[Build175](build-175-integration-20261108.md)・
[Build176](build-176-integration-20261108.md)と[登録手順](freedoom-normal-launcher-20261108.md)に分けて保持する。

表示条件には、TermuxのZink既定経路を避けて成立させた`LIBGL_KOPPER_DISABLE=1`も含める。
配備済みの固定runtime/profileから条件を引き継ぎ、SDLのsoftware指定だけへ簡略化しない。
版や環境条件を変える場合は小probeから再検証する。現行GUI sessionの回収期限12秒と従来経路8秒も区別し、
期限を延ばすだけで未回収を合格にしない。

### 今後の方針

最初は「通常footが一枚あるsessionから、登録済みFreedoomを追加する」経路を完成させる。
UIからも自動検証からも同じ製品起動処理を通し、固定demo専用経路の成功で代用しない。
対象は検証した固定Freedoom一種。任意GUI・自由env・任意WAD・音声・マウス取得・ゲームパッドは別段階とする。
通常のfootとGUIは同じ起動ordinal/窓予算を使うが、GUIのreceiptをPTY workloadやfootグループの根拠に流用しない。

| 順 | 実装・検証単位 | 完了条件と失敗時の扱い |
| --- | --- | --- |
| F1 | source・配備・通常footの準備 | APK入力digest、3file helper hash鎖、asset/engine/runtime、catalog revisionを照合。通常footを明示focusし、実行可能な窓枠を確認。不一致時に再登録・再配備を自動実行しない |
| F2 | 通常起動＋固定keys | reload→production起動のrequest ID/受理と、別にmode=gui receipt・cookie・Mapped full target・SDK更新を確認。Esc/Up/Enter/Yによる終了を、配送counterとcontent前進、子の自然exit0まで同じjobで照合 |
| F3 | 別起動でwindow close | 一回目closeのACK後に同targetが残りcontentが進む場合は終了確認中として扱う。二回目closeと自然終了を別記録。一回で退役した場合に別の窓へ二回目を送らない |
| F4 | Stop・取消・切断 | 起動中/表示後の取消と通信断を個別に検証。GUI worker→子孫→anchorの所有順で回収。通常Stopはfootも終了し得るため、元footを保つ自然終了とは別合格にする |
| F5 | 通常ランチャーUIの仕上げ | reload後のFreedoom項目、受付/起動待ち/稼働/失敗/回収待ちが分かること。連打は重複起動せず、pin変更・実行枠不足・未回収を区別。設定reload・再接続で自動再実行しない |
| F6 | 反復・回帰 | まず単発を成立させ、起動終了10回の短い反復へ進む。元footの維持、再起動、FD/process/pidfd/pipe/socket/SHM/SDK/Board/controls/MODEを確認。Phase6の長時間受入れとは別 |
| F7 | 初回GUI導線・手動受入れ | 下層のGUI-firstを実機確認してから初回接続UIへ接続。最後に物理ランチャー押下、focus往復、USキーでのメニュー・ゲーム内移動・終了、HMD可読性を確認。Fキーは必須にしない |

F2の固定キーは「メニューから終了できる」検証であり、ゲーム内の移動やプレイ感の検証にはならない。
移動の自動検証を加える場合は、固定版の状態・設定と観測可能な反応を先に定義する。
キー配送件数だけをゲーム動作の合格にせず、最終の物理確認と分ける。

### 起動・入力・終了の共通契約

- enqueueの受理、GUI receipt、Mapped、SDK初frame/更新、入力の実配送、ゲームの反応を別々に記録する。
  app_id一致や画面が一度出たことだけで完了にしない。
- 入力は同じruntime/session/scene/full target/epoch/focus tokenへ束縛する。
  外部focus喪失、別操作、世代変更後に遅い入力を返さない。close自身の既知resetだけを有限進捗として扱う。
- GUIの全世代targetを追い、native消失とSDK/Board/Entity/listener/controls/MODEの退役を個別に確認する。
  session全体のSHM copy増分をゲーム一窓のwire commit数へ読み替えない。
- 自然exit、明示Stop、期限切れ、signalによる終了を区別する。元のcaseが失敗した後に回収できても、操作成功へ変更しない。
- 未回収なら同じownerとreceiptを保持して新起動を止める。保存された数値PIDだけで後からkillしない。
  キー本文、ゲーム画面、raw Waylandを通常ログへ保存しない。

再現コマンドと固定caseの意味は[通常Freedoomの固定GUI検証](freedoom-normal-gui-probe-20261108.md)、
配備と型は[通常GUI接続](freedoom-typed-gui-connection-20261108.md)を参照する。
本計画の残件解消後も、任意SDL/GTK/Qtや9枚同時ゲームの動作保証へ拡張しない。

<a id="linux-normal"></a>
## 2. 別Linuxマシンへの通常GUI接続（NX18）

### 到達点と未接続部分

| 層 | 現状 | まだ証明していないこと |
| --- | --- | --- |
| SSH認証・StreamLocal・固定SHM | 実APKから隔離Linux検証環境へ接続、固定SHM100frameと通常SDK表示・復帰・lease解放が成立 | 任意のUbuntuホストや既存desktop全体への対応 |
| 固定GTK B6 | Build174のinput/dialogが成功。文字・削除・button、追加窓、SHM/SDK、全退役、remote journal/所有process回収、通常復帰を同jobで確認 | 通常ランチャー、終了しない通常アプリ、任意GTK/Qt、物理入力、remote IME |
| 通常接続設定 C1 | mainに専用保存モデル/Store/CASを実装。profile ID `remote-linux`、backend `linux_ssh`、revision/bindingとpinを分離。L5/L6で通常設定UI・Hostへ採用 | device filesystemでの中断復旧、保存後の実Questからの通常SSH接続 |
| process owner C2a | subreaperとlive pidfdでsetsid子孫を含む所有・回収を扱う部品を実装・host検査。pipe EOFはadapterで別判定 | 通常remote foot/GUIの接続完成、helper自身が失われた後の回収保証。C2aという名称だけでterminal対応済みとはしない |
| 通常GTK C2 | 保存版 `43767a4`の通常demo/helper/builderと型付きprotocolをL1〜L4でmainへ統合。自動終了しない固定GTKのみを扱う | 実配備・通常経路での実描画・入力 |
| L1〜L4 | [PR #1](https://github.com/tin-machine/eidolon-wayland/pull/1)をmain `d47c2409`へmerge。非同期終了競合・通常lease・SSH actor/StreamLocal・独立journal・helper喪失/boot変更時の明示復旧を接続。[実装と検証範囲](normal-linux-gui-l1-l4-20261108.md)を参照 | 対象host/Questへの実配備、L7a/L7bの受入れ |
| L5〜L6 | 通常Connect→明示Inspect→catalog→明示Launch、native cookie/slot/Started照合・SDK入力/Close経路、接続設定・個別client鍵・pins確認を接続。[実装・受入れ手順](normal-linux-gui-l5-l6-20261108.md)を参照 | 実Questで同じjobの描画・英数入力・Dialog・Close・全target退役を確認する受入れ。hostテストだけではL1〜L6統合成立としない |

固定GTKの詳細は[Build174](build-174-integration-20261108.md)。C1は[保存設計](normal-linux-profile-c1-20261108.md)、
C2の保存版は[通常Linux GUIのcheckpoint](https://github.com/tin-machine/eidolon-wayland/blob/43767a437b5deab94156f5550846c1a0456dbe73/docs/normal-linux-gui-c2-20261108.md)を参照する。
debugのB4/B6 profile・pins・journalを通常profileへ自動昇格させない。

### 採用方針と従来案からの変更

Quest APKから一台のLinuxへ直接SSH接続し、Linuxで実行する個別Wayland GUIをEidolonの窓へ表示する。
Termux中継は必須にしない。remoteの実行file/cwd/dataはremoteに残し、Questへコピーして実行しない。

従来の「固定SHM→remote foot→一般GUI」の案に対し、直近の再開順は
**対象hostを事前確認し、固定GTKの成功を使って通常GTK demoを接続した後、既存の実用アプリ一種を固定登録して確認する**。
足場は、自動入力や期限による自己終了を持たないGTK desktop demo（Entry、Dialog、Close）とする。
新たな端末PTY経路とGUIの寿命管理を同時に解放しない。remote footは削除せず後続の独立項目として残す。
実用アプリ一種の確認を、remote foot/PTYや任意exec・自由argv・任意catalogの全面開放の完了待ちにしない。

到達点は「通常経路の統合成立」と「限定した実用接続成立」の二段階に分ける。
前者は固定GTK demoを通常UI/launcherから操作できること、後者は対象Linuxにある既存アプリ一種をQuestから起動し、
remoteファイルを開く／保存する・設定を保持する・終了するところまで使えることとする。
固定demoの成功を後者へ読み替えず、詳細な[完了判定](#linux-acceptance)で記録する。

接続先のprofileは「どこへ接続するか」、capabilityは「その接続で何を起動できるか」を表す別の契約とする。
SSH成功やprofile保存だけでcatalogやGUIReadyを公開しない。
初版はTermuxまたはLinux一台のどちらか一接続先、通常GTKは一instance。
実用アプリ一種の検証でも同時instance数は増やさず、検証済みの固定entry/capabilityを明示的に追加する。
Dialogなど追加toplevelも全体の9窓予算へ含め、別hostごとに予算を増やさない。

### 接続する実装単位と受入れ

以下の段階名は計画上の単位であり、実装済みenumや画面状態の名称を保証するものではない。
従来のL1〜L8は維持し、L7を実用アプリ確認のL7aと、継続稼働・障害・性能のL7bへ分ける。

| 順 | 変更する層 | 実装・受入れ条件 |
| --- | --- | --- |
| 事前確認 | 対象Linuxの実行条件 | L1前に対象OS/arch、SSH認証・StreamLocalの実効許可、Waypipe実装/版/feature、Pythonのpidfd API・Linux subreaper、GTK/runtimeを確認する。実ホスト固有の前提をUI統合後まで先送りしない |
| L1 | branch reviewと配備 | C2の[非同期終了event競合](#linux-protocol)を修正する契約とfixtureを確定。APK source digest入力の3sourceとbundle hashを照合し、通常GTKの独立bundleを明示配備。既存base/Waypipe/B6 bundleと証拠を保持し、未配備・改変・重複fileは拒否 |
| L2 | helper wire loop・lease | 固定bootstrap/inspect/start/heartbeat/stopを接続。session/profile revision/binding/nonce/UIDとscopeを固定。[寿命と出力](#linux-lifetime)を分離し、更新可能な有限lease、所有回収、`.eidolon-desktop`専用receipt、helper喪失時の扱いを定義する |
| L3 | transport SSH actor | 一つの認証済みhandleとStreamLocal routerを使い、要求ACKと非同期終端eventの相関規則を分離する。上限size/deadline、event順序、bindingを検査。応答不明を自動再実行せず、自然終了・Stop・heartbeat・lease切れの交差fixtureを実actorにも適用 |
| L4 | durable journal・復旧 | 通常Linux用journalへ起動前にbinding/ownerとremote boot identityを記録。process/pidfd/pipe、directory/socket/receipt、channel/routerを別判定する。通信断・helper死亡・host再起動を区別し、unknown ownerの明示解消手順と新規起動条件を定義する |
| L5 | native・Host・SDK | AppMode/GUI receipt/cookie/slotを接続。実inspectとversion/capability照合後だけ一項目catalogを公開。Mapped→SDK初frameと更新→直接入力→Dialog→Close→全target退役を同じsessionで観測する |
| L6 | 接続設定UI・launcher | C1を通常workerへ接続し、host key pinとclient identityの[認証契約](#linux-credentials)を採用。profile/catalog revisionを要求へcaptureし、接続先・capability・切断時の作業終了条件を表示。旧owner回収後に明示切替し、起動操作で別hostへ暗黙に切り替えない |
| L7a | 既存実用アプリ一種 | [固定登録した実アプリ](#linux-real-app)の独立起動、専用busとfile chooser、remoteファイルの開く/保存、固定設定policy・可変設定保持、SDK初frameまでの有限期限、resize/focus/Close、基本物理入力を確認。portal利用有無を記録し、任意exec全面開放を前提にしない |
| L7b | 継続稼働・回帰・障害・性能 | 固定B4/B6・通常Termuxを回帰確認。通常GUIの継続稼働、少量継続出力、切断/スリープ/Stop、helper死亡/host再起動、保存復旧と窓予算、[成功receiptの受領・退役と上限超過の反復接続](#linux-receipt-retirement)を確認。圧縮・遅延・転送量・CPU・総pixel面積・buffer/メモリを測る |
| L8 | 利用範囲の拡張 | remote foot/PTY、構造化した任意アプリ定義・遠隔fileの一時実行/登録、Qt等を個別追加。HMD総合受入れ・remoteかな漢字は独立して残す。複数host同時・desktop全体共有・音・任意3Dは別設計 |

L2〜L5が揃うまで、通常GUI経路は利用可能と表示しない。
固定demoだけのcapabilityを一般アプリ対応として表示しない。L7aの基本物理確認は、HMD/IMEの総合受入れの代用にはしない。
通信・helper待機は描画/入力threadで行わず、queueと保持frameを有界にする。
未知ownerや回収pendingを自動的にIdleへ戻さず、同じownerの明示回収を残す。

<a id="linux-protocol"></a>
### 要求ACKと非同期終了eventの相関（L1・L3）

未統合C2の[protocol実装](https://github.com/tin-machine/eidolon-wayland/blob/43767a437b5deab94156f5550846c1a0456dbe73/native/transport/src/linux_desktop/protocol.rs#L194-L322)は、
`command()`でwire bytesを生成した時点でrequest sequenceを進め、全受信eventに最新送信番号との一致を要求する。
自然終了・lease失効・入力EOFの`Stopped`も同じ検査を受けるため、次の交差で正しい終了通知を拒否し得る。

1. 起動ACK後、双方のrequest sequenceが`2`になる。
2. Linuxが自然終了を観測し、`Stopped(ProcessExited, request_sequence=2)`を生成する。
3. Questがその通知を受け取る前にheartbeat `3`を生成する。
4. 正しい終了通知も`2 != 3`で`DESKTOP_PROTOCOL_SCOPE`となり、`Failed`へ移る。

これは保存版C2のコードから確認した静的指摘で、実機発生を意味しない。後続L1〜L4 PRではACKと非同期終端の因果範囲を分け、protocol/actual actorの交差fixtureを追加した。L3の通常actorへ配線する条件として、
Python/Rust共通のwire契約として、要求ACKの対応番号と非同期eventの最終処理request番号を分離する。

- ACKは対象requestと厳密に対応付ける。非同期終端eventではremoteが処理済みのrequestと未処理requestの関係を確認する。
  一律に最新送信番号との一致を要求せず、無条件に古い番号を受け入れる変更にもしない。
- session/profile revision/binding相当のscope/nonce/UID、event sequence、合法な状態遷移は厳密に検査する。
  自然終了や回収pendingが新しいsession・別instanceへ適用されないことを維持する。
- command生成、送信、remote ACKを別状態として扱い、送信/応答不明のstartを自動再送しない。
  終端と行き違ったACKの処理・破棄条件を定義し、終了済みアプリを復活させたり終了を二重計上したりしない。
- heartbeatと自然終了、Stopと自然終了、未ACK要求とlease失効/入力EOF/回収pendingを、双方の順序で検査する。
  未送信のcommand生成直後に終了eventが到着するcaseも含める。順番どおりACKを待つfixtureだけで合格にしない。

wireの意味やschema/revisionを変更した場合は、helperとRustの相互fixture、source/bundle pinsも同じ変更へ束縛する。

<a id="linux-lifetime"></a>
### 常駐寿命・期限・出力量の分離（L2・L3・L7b）

既存probeの[transport owner](https://github.com/tin-machine/eidolon-wayland/blob/ea068ba28051f441d28698730b4b18056ba59ae5/native/transport/src/linux_probe/owner.rs#L209-L245)は
GTK起動から90秒の総期限を持ち、heartbeatではその期限を更新しない。
[Linux probe helper](https://github.com/tin-machine/eidolon-wayland/blob/ea068ba28051f441d28698730b4b18056ba59ae5/scripts/linux/eidolon-linux-session.py#L480-L530)も
leaseと120秒の最大時間を分けている。これは有限probeの契約であり、通常GUIへ既に混入していると判断したものではない。
通常経路では次を別々に定義する。

| 制限 | 通常GUIでの契約 |
| --- | --- |
| 起動期限 | process生成・表示準備・最初のframeのどこで停止したかを識別して取消可能にする |
| request/ACK期限 | 個々の操作の応答待ちを有限にする。応答不明と未実行を同一視しない |
| heartbeat lease | 生存中の同ownerが更新する有限期限。更新間隔・猶予・停止条件を両端で一致させる |
| 停止期限 | 子孫回収、pipe EOF、remote state、native/SDK退役を別々に判定し、未証明ならpendingを残す |
| アプリ総稼働時間 | 通常用途にprobeの短い最大時間を引き継がない。テスト自体の実行上限は製品寿命と分離する |

C2の[Prepared契約](https://github.com/tin-machine/eidolon-wayland/blob/43767a437b5deab94156f5550846c1a0456dbe73/native/transport/src/linux_desktop/protocol.rs#L271-L315)は
`lease_ms=15000`を固定している。採用または改訂の際にheartbeat間隔と猶予を定め、既存probeの10秒間隔を無検討で流用しない。
描画・圧縮・SSH送信の待ちがheartbeatへ与える影響と、Questの実際の停止条件をL7bで測る。

C2時点の[adapterのdrain](https://github.com/tin-machine/eidolon-wayland/blob/43767a437b5deab94156f5550846c1a0456dbe73/scripts/linux/eidolon_linux_desktop.py#L145-L155)は
stdout/stderrの累積64KiB超を`OUTPUT_LIMIT`にしたが、現行の
[通常serveは `drain(enforce=False)` を使用する](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/linux/eidolon_linux_desktop_session.py#L311-L313)。
[64KiB超・複数lease期間の回帰](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/tests/test_linux_desktop_session.py#L451-L460)も存在する。
L7aではこの継続drainを維持し、次の分離と出力速度制限・制御処理の公平性を追加する。

- 制御protocolの1メッセージ上限と未知field/不正JSON拒否。
- 未処理queue、記録するログ、保持bufferの上限。
- アプリstdout/stderrの継続drainと、時間窓内の異常な出力速度への対処。本文の通常保存は行わない。
- 起動からの累積出力counter。観測値を暗黙のアプリ寿命にせず、少量のwarningが長時間続くだけで終了させない。

制御protocolをアプリのログと同じ読み捨て処理へ通さない。
L7bでは旧probeの90/120秒を十分超える継続稼働後の入力・Closeと、少量出力の累積が旧閾値を超えても
所定の資源上限内で操作できるcaseを確認する。この検査をPhase6の長時間受入れ全体の代用にしない。

<a id="linux-real-app"></a>
### 既存実用アプリ一種の受入れ（L7a）

2026-11-09に[実装単位まで分解した詳細設計](linux-real-app-l7a-design-20261109.md)を追加した。
第一候補はMousepadのGTK3版。専用bus、永続HOME/XDG、version付き固定catalog、
nonce markerを持たない実用アプリ用のnative origin/admission、旧版Recoverの維持、host検査を定義する。
同日の実装レビューを反映し、PR #4で[A/A0検査基盤とsettings module](linux-real-app-a0-foundation-20261109.md)をmainへ追加した。
以後の再起動/instance分離とGUI設定検査は[A0の次工程と引き継ぎ](linux-real-app-a0-next-steps-20261109.md)にまとめる。
対象packageのA0全項目、v3通常起動への組込み、実機受入れは未完了である。
read-only事前検査Aの後、実Mousepadと試作settings moduleを合成profileで検査するA0を置き、
対応版・窓の順序・設定policyを確定してからv3 bundle/transport/UIを実装する。

実装レビューでは、[既定の復元/autosave設定](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/org.xfce.mousepad.gschema.xml#L118-L135)、
[main前に出る復元dialog](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-dialogs.c#L626-L663)、
[opening-modeによる通常Openのmain生成](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-application.c#L1103-L1117)を確認した。
L7aは既定GSettings backendを利用するpackageに限定し、専用GIO moduleで `session-restore` / `autosave-timer` / `opening-mode` を
`never` / `0` / `tab` へ固定・変更不可にする。その他の検査済み設定はkeyfileへ永続化する。
独自backend buildや専用moduleの不成立は未対応として拒否し、host全体の設定を変更しない。
復元無効化に伴う[アプリ自身のautosave削除](https://github.com/xfce-mirror/mousepad/blob/mousepad-0.6.3/mousepad/mousepad-history.c#L827-L941)を避けるため、
復元用データがあるprofileはspawn前に `APP_STATE_RECOVERY_REQUIRED` として保持する。

起動成功はmain採用に加え、同じmain/full targetのSDK成功ACK・SDK所有・初renderer frameまで確認する。
現行の[Mappedと表示済み判定は別条件](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/android/app/src/main/java/io/github/tinmachine/eidolon/NativeSessionHost.kt#L395-L428)である。
nativeでmain待ち20秒、採用後のSDK表示待ち10秒を初期上限とし、世代交換で延長せず、失敗時は同ownerのStop/全世代回収へ進む。
v2 Recoverでは旧source bytes/hashを維持し、現在のMousepad package更新・削除を旧owner回収の拒否理由にしない。
各contract・根拠・gate・旧APKからの更新試験は[詳細設計](linux-real-app-l7a-design-20261109.md)に従う。

通常demoの次に、対象Ubuntu等にある軽量なエディタなどを一種選び、package/版、実行file、固定argv/cwd、
Wayland backend/renderer、独立instance条件をversion付きcapabilityへ結び付ける。
対象は事前確認で決め、未検証の具体的なアプリを対応済みと記載しない。自由shell/argv/任意catalogの解放はL8に残す。
既存アプリは固定demoの自己申告counterを持つと仮定せず、起動receipt・instance・Wayland client/target・実描画の対応を確認する。

**D-Bus/session bus:** [GApplicationの登録仕様](https://docs.gtk.org/gio/method.Application.register.html)では、
同一session bus上のapplication IDにより既存primary instanceへ要求が渡り得る。
対象アプリの独立起動方法、既存busを利用する範囲、専用busの必要性を定義する。
[dbus-run-session](https://dbus.freedesktop.org/doc/dbus-run-session.1.html)はSSH等から専用busを作る候補だが、
それだけでdesktop service一式が利用可能になるとはしない。

**Portalと追加画面:** [portalはsession busのサービス](https://flatpak.github.io/xdg-desktop-portal/docs/api-reference)であり、
[FileChooser backend](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.impl.portal.FileChooser.html)が
ファイル選択画面を表示する。仕様上、本体の`WAYLAND_DISPLAY`変更だけではその画面のQuest到達は保証できない。
対象アプリがportalを使うかを確認し、利用するbus/backend・表示先・所有/終了の範囲を固定する。
既存desktop serviceをhelperの子孫として回収せず、専用に起動するserviceの監督方法も定義する。
これは対象hostで誤表示を観測済みという意味ではない。

**HOMEと永続設定:** 検証用nonce directoryの一時HOME/XDG設定を、そのまま実用アプリの永続設定にしない。
対象hostの既存HOMEを使うか、アプリ用の永続HOME/XDG directoryを用意するかを決める。
sessionのsocket/temp/receiptと保存対象の設定・データを分け、通常回収で後者を削除しない。

L7aでは、同じsource/profile/app版・instanceを識別して次を確認する。

1. 同じユーザーの既存desktop instanceとは独立してQuestへ起動する。既存窓への転送や正常exitだけを成功にしない。
2. 開く/保存ダイアログがQuestで操作でき、remote側の検証用ファイルを実際に読み書きできる。
   portal未使用のアプリならその条件を記録し、portal対応済みへ広げない。
3. アプリ再起動後の可変設定保持と固定3キーの変更拒否が成立し、session資源だけの回収がHOME/XDG方針に従う。
   復元用データのあるfixtureは起動前に拒否され、内容を保持する。
4. resize、focus往復、基本の物理英数入力、Closeを確認し、生成した全targetと所有資源を回収する。
5. 取得できないremote CPU/RSS/Disk I/O等はunknownとし、Quest全体やTermuxの値で代用しない。
6. SDK callback欠落・失敗ACK・起動中の可視性喪失で有限時間内にStopへ進み、遅着frame/ACKで復活しない。
7. 旧APKのv2 pendingを新APKで旧source/pins/bundleにより回収できる。host fixtureと実機更新試験を区別する。

HMD内の可読性・長時間の使い勝手・remoteかな漢字変換・任意toolkitは、それぞれ別の受入れを残す。

<a id="linux-failure-recovery"></a>
### 通信・スリープ・helper喪失・host再起動（L2・L4・L6・L7b）

初版は、SSH切断後に同じGUIを復元せず、既存の所有資源回収を経て新しいsessionとして再接続する。
スリープ等でheartbeatがleaseを超えて停止する場合は、未保存のGUI作業も終了し得ることを通常UIへ表示する。
Questの脱着・スリープが実際にどの通信/workerを停止させるかは実機確認事項であり、必ず切断すると仮定しない。

| 障害・操作 | 初版の扱いと確認条件 |
| --- | --- |
| 同じSSH接続内の一時的な通信停滞 | lease内の回復と期限切れを区別する。期限後の遅いheartbeatで退役済みownerを復活させない |
| SSH接続断・Quest終了・heartbeat喪失 | remote helperが生存する場合のEOF/lease回収を検査する。入力を解除し、復帰時に無断再起動しない |
| 明示Stop・接続先切替 | 旧入力ownerと資源の回収を確認し、保存が必要な作業の終了を案内してから切り替える |
| 同一bootでのremote helper SIGKILL/OOM/crash | live pidfdを失ったunknown ownerとして扱う。保存PIDへの後日signalや、状態が見えないことによる完了解釈をしない |
| remote host再起動 | 同じ認証済host・実行環境のboot identityを起動前のjournalと照合する。旧processの不存続と、残るsocket/directory/receiptの所有・退役を別々に検証する |

[C2a owner契約](linux-terminal-process-owner-c2a-20261108.md)は生存する専用helperによるsubreaperとpidfdの管理である。
[subreaper](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)は生存している祖先へ孤児を引き取り、
[pidfd](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)は特定taskを参照するFDである。
helper死亡後までこの仕組みだけで回収が継続する保証はないため、通信断時の成功とhelper死亡時の成功を分ける。

L4ではunknown ownerの明示解消手順を実装前に定義する。最低限、同一binding/UID・実行環境・bootの確認、
live ownerがあればそのownerによる回収、owner喪失時の管理者による対象確認と解消結果、残る私有stateの照合、
新規起動を許す終端条件を記録する。取得不能・矛盾時はunknownを保持する。
boot identity変更だけでsocket/receiptまで自動回収済みにせず、別profileから不可視なことも解消の証拠にしない。

Ubuntu用adapterの発展案として、外側に[systemd user transient service](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html)と
cgroupによる監督を置く方式を比較できる。
[`KillMode=control-group`](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)はunit停止時の残存process回収に使えるが、
採用する場合はuser managerの可用性、helperとunitの寿命、unit identityとjournal、環境の引渡しを別途検証する。
固定GTK初版の必須依存にせず、helper喪失後まで保証する範囲を広げる際の選択肢とする。
スリープ後も同じGUI作業を保持する機能も、lease延長だけで達成したとせず、表示接続とアプリ寿命を分ける後続設計にする。

<a id="linux-receipt-retirement"></a>
### 成功receiptの受領・退役と反復接続（L7b必須・未実装）

現行の通常desktop helperは[成功receiptを自動GCしない](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/docs/normal-linux-gui-l1-l4-20261108.md#L109-L111)。
[rootの全entryを数える上限](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/linux/eidolon_linux_desktop_session.py#L111-L117)と
[終了後にもreceiptを残す処理](https://github.com/tin-machine/eidolon-wayland/blob/1a92b5c6864f38f7901419d1d93de3d004c9051d/scripts/linux/eidolon_linux_desktop_session.py#L172-L200)により、
正常な接続・終了の反復でも新規起動を拒否する状態へ到達する。
2026-11-09のレビューでは空の合成rootで現行Stateのprepare→finishを81回完了し、82回目が `STATE_LIMIT` になることを再現した。
これは既存制限の確認であり、実GUIの反復受入れや解消済みという記録ではない。

L7aではentry数・上限到達を事前検査し、成功receiptを保持する。日常利用へ進むL7bでは、以下の受領・退役契約を別途具体化する。

- localの全回収完了をdurableに確定し、対象receiptの完全なbindingとdigestを持つ受領記録を保存できたものだけを対象にする。
  local journalが存在しないことだけを受領の根拠にしない。
- 認証済みの同host/UIDで明示ACKし、remote側はversion・installation/storage・session/nonce/scope・boot・bundleへ束縛した
  成功receiptだけを検証して退役する。ACKの再送・応答喪失にも対応する。
- unknown/pending、未ACK、別profile、旧v2のreceiptを年齢や件数で一括削除しない。
  旧v2には新しいACKを無断適用せず、旧証拠の保管と容量の扱いも別途定義する。
- ACK前後のcrash・再送、v2/v3混在、80件を超える反復接続でも、未回収ownerの証拠を失わず新規利用を継続できることを受け入れる。

CLI/wire/schema、受領記録の保管上限、退役済みnonceの再送確認方法はL7bの設計事項とする。
L7aでこのGC/退役機能まで実装済み・受入れ済みとして扱わない。

<a id="linux-rendering"></a>
### Waypipe・renderer・性能の固定条件（事前確認・L7）

初回は[deps.lock.json](../native/deps.lock.json)のWaypipe C 0.11.3と実featureを揃え、SHMの相互接続を確認する。
通常の実行名や版番号だけでC/Rustやfeatureを同一と判断しない。
[作者のRust移植記録](https://mstoeckl.com/notes/code/waypipe_to_rust.html)には元のC実装との接続検査もあるため、
C/Rustが全面的に非互換とも、任意の組合せが互換とも断定しない。
[Ubuntu配布manpage](https://manpages.ubuntu.com/manpages/stonking/man1/waypipe.1.html)の版・実装は固定C版と異なり得るので、
新しいCLI optionを無検証で固定C版へ持ち込まず、選んだsource/buildのhelp・同梱文書と実featureを照合する。

`--no-gpu`はGPUアクセスを必要とするprotocolを遮断する指定であり、任意アプリの描画を必ずSHMへ変換する機能ではない。
アプリのWayland backendとrendererを別の条件として、検証したpackage/版・環境をcapabilityへ束縛する。

| 対象 | 初期の検証条件・境界 |
| --- | --- |
| GTK3標準widget demo | B6で成立した条件を利用する。GTK4やGtkGLAreaの動作保証へ広げない |
| GTK4 | [GTK公式の実行時設定](https://docs.gtk.org/gtk4/running.html)に従い、`GDK_BACKEND=wayland`と`GSK_RENDERER=cairo`等を別に確認する。環境指定は対象版の検証条件であり、全アプリ共通の保証にはしない |
| Qt Widgets | Wayland QPA backendとraster/SHM経路を対象アプリで確認する |
| Qt Quick | [公式scene graph設定](https://doc.qt.io/qt-6/qtquick-visualcanvas-adaptations.html)の`QT_QUICK_BACKEND=software`を候補とし、software側の機能制限も確認する |

L7bでは、静止画面・文字入力・scroll/resize等の更新を分け、none/lz4/zstdを両端の対応確認後に比較する。
解像度・窓数・更新条件・thread設定を揃えて、転送量、両端CPU、入力から描画までの遅延を記録する。
[Waypipe作者の説明](https://mstoeckl.com/notes/code/waypipe_to_rust.html)にあるbuffer複製・差分処理も考慮し、
総pixel面積、buffer数/bytes、Waypipeプロセスのメモリ、compositor copy、SDK側の保持量を測る。
取得できない内訳はunknownとして、既存の窓数/pixel予算・queue上限と合わせて総メモリ圧力を判断する。
PC側GPUの有無や一度のSHM表示だけを、動画・高解像度・高更新頻度の快適性の証拠にしない。

<a id="linux-credentials"></a>
### host key pinとclient identity（L6で採用する契約）

host key pinは接続先の認証、client identityはAPK側の公開鍵認証として区別する。
pin/endpoint/user/revisionを含むimmutable bindingを使い、別hostのpinを流用しない。
credentialはcaptureした要求へ一度だけ渡し、遅い認証結果を新接続へ流さない。

[CredentialStore](../android/app/src/main/java/io/github/tinmachine/eidolon/CredentialStore.kt)を別alias・別directoryで使い、
**`remote-linux` profile専用client鍵**を採用する。Termux用の既存identityは維持し、debug probeの鍵を移行しない。
初版の通常Linux profileは一つで、endpoint変更だけでclient鍵を自動交換する機能はない。公開鍵を対象Linuxユーザーへ明示登録する。
失われた鍵を黙って再生成せず、接続先/pin変更時にはprofile revisionを進め、bundle pinsも新bindingに対して再確認する。

通常HostはConnect時のruntime/request/profile/revision/bindingをcaptureし、nativeからの同じsessionを含むcredential scopeと照合して
一度だけ秘密bufferを供給する。遅いevent・再供給・古いbindingは拒否し、callback終了時にbufferを消去する。
回収未完了のjournalがあれば接続先変更を止め、古いownerを新しいprofile/identityへ付け替えない。

### その他の継続条件

- **DNS:** C1はDNS名を保存できるが、現[transport](../native/transport/src/connect_profile.rs)はIPv4接続のみで、名前を接続に使うと`DNS_NOT_IMPLEMENTED`となる。
  初回は検証可能なIPv4を使い、DNS/IPv6は期限・取消・宛先照合を含む別実装にする。
- **独立窓の対応:** app_idだけで起動成功や親子関係を決めない。追加Dialog、full target世代、同一instance、close時の入力解除、全世代SDK退役を確認する。
- **通常UI:** 設定保存、認証成功、起動可能catalog、起動待ち、表示、回収待ちを区別する。profile切替やreloadだけでアプリを自動再実行しない。
- **証拠の境界:** 合成英数入力を物理入力/remote IMEの成功へ広げない。隔離環境の成功を任意Ubuntuの成功にしない。

<a id="linux-acceptance"></a>
### 通常Linux GUIの完了判定

| 到達点 | 完了条件 | 残す境界 |
| --- | --- | --- |
| 通常経路の統合成立（L1〜L6） | 通常UIで接続先を明示選択し、固定GTK demoをcatalogから起動。同じsource/job/profile/instanceで描画進行・英数入力・追加Dialog・Close・停止・切断後回収・通常復帰を記録 | 既存実用アプリ・物理/HMD・remote IMEは未受入れ |
| 限定した実用接続成立（L7a・L7b） | 固定登録した既存アプリ一種で独立起動・remoteファイルの開く/保存・設定保持・基本物理入力・終了が成立。継続稼働、定義した障害/unknown-owner復旧、回帰と性能の条件を確認 | 対象host/app版/backend/rendererの範囲に限定。HMD総合・remote IME・任意アプリ・複数hostは別受入れ |

host fixture、helperの`complete`、catalogの表示、Mapped消失だけでは合格にしない。
「未確認」「失敗したが回収成功」「通常操作成功」を分け、未回収は同ownerのpendingを維持する。
実装・host/CI検証と実機受入れを分ける。L5/L6のcodeが揃っても、同jobの実機記録がない段階で上記の完了条件を達成済みにしない。

## 3. 記録と再開の約束

1. 再開指示後にmain/作業branch/CI/導入APK/実runtimeを照合する。Build176へ新sourceの検証結果を誤対応させない。
2. FreedoomのF1〜F4とLinuxのL1〜L4は、所有ファイルと実機ownerを分ければ並行準備できる。APK配備・ADBは一人のownerが直列化する。
3. runごとにsource/digest/APK/helper/bundle/profile revision、固定case、判定・失敗理由・回収結果・次actionを残す。秘密鍵・catalog本文・入力本文・室内画像は記録しない。
4. 既存goalのNX01〜18全体とPhase5/6の未受入れを保持し、この二項目の計画追記や部分成功で完了にしない。
