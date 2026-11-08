# NX11・NX18: 通常GUI起動の現状と残る実装計画

2026-11-08追記。ユーザーが挙げた「3. ランチャーからのFreedoom起動」と「5. 別Linuxマシンへの通常GUI接続」の詳細計画。
記録基準はmain `2eeff89`、導入済みdebug APKはBuild176/source `f09a3b4`。
検証版・digest・APK hashは[Build176の記録](build-176-integration-20261108.md)を正本とする。
実装・実機検証・goalは相談のため一時停止中。本追記は開発再開を意味しない。

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
| 通常接続設定 C1 | mainに専用保存モデル/Store/CASを実装。profile ID `remote-linux`、backend `linux_ssh`、revision/bindingとpinを分離 | 接続設定UI・Hostへの採用、device filesystemでの中断復旧、保存後の通常SSH接続 |
| process owner C2a | live pidfdでsetsid子孫を含む所有・回収・pipe EOFを扱う部品を実装・host検査 | 通常remote foot/GUIの接続完成。C2aという名称だけでterminal対応済みとはしない |
| 通常GTK C2 | 作業branch `43767a4`に通常demo/helper/builderと型付きprotocolを保存。Python13件・Rust8件・Cビルド・限定clippy成功 | main統合、実配備、SSH actor/wire loop/lease/journal/runtime/Host/UIへの接続、実描画・入力 |

固定GTKの詳細は[Build174](build-174-integration-20261108.md)。C1は[保存設計](normal-linux-profile-c1-20261108.md)、
C2の保存版は[通常Linux GUIのcheckpoint](https://github.com/tin-machine/eidolon-wayland/blob/43767a437b5deab94156f5550846c1a0456dbe73/docs/normal-linux-gui-c2-20261108.md)を参照する。
debugのB4/B6 profile・pins・journalを通常profileへ自動昇格させない。

### 採用方針と従来案からの変更

Quest APKから一台のLinuxへ直接SSH接続し、Linuxで実行する個別Wayland GUIをEidolonの窓へ表示する。
Termux中継は必須にしない。remoteの実行file/cwd/dataはremoteに残し、Questへコピーして実行しない。

従来の「固定SHM→remote foot→一般GUI」の案に対し、直近の再開順は
**固定GTKの成功を使い、通常GTK一種を接続してからremote foot/任意アプリへ広げる**。
足場は、自動入力や期限による自己終了を持たないGTK desktop demo（Entry、Dialog、Close）とする。
新たな端末PTY経路とGUIの寿命管理を同時に解放しない。remote footは削除せず後続の独立項目として残す。

接続先のprofileは「どこへ接続するか」、capabilityは「その接続で何を起動できるか」を表す別の契約とする。
SSH成功やprofile保存だけでcatalogやGUIReadyを公開しない。
初版はTermuxまたはLinux一台のどちらか一接続先、通常GTKは一instance。
Dialogなど追加toplevelも全体の9窓予算へ含め、別hostごとに予算を増やさない。

### 接続する実装単位と受入れ

以下の段階名は計画上の単位であり、実装済みenumや画面状態の名称を保証するものではない。

| 順 | 変更する層 | 実装・受入れ条件 |
| --- | --- | --- |
| L1 | branch reviewと配備 | C2をmain基準でreviewし、APK source digest入力に含む3sourceとbundle hashを照合。既存base/Waypipe/B6 bundleを変更せず、通常GTKの独立bundleを明示配備。未配備・改変・重複fileは拒否 |
| L2 | helper wire loop・lease | 既存の内部adapterに固定bootstrap/inspect/start/heartbeat/stopを接続。session/profile revision/binding/nonce/UIDとscopeを固定し、有限leaseと期限切れ時の所有回収を実装。`.eidolon-desktop`の専用receiptを使う |
| L3 | transport SSH actor | 一つの認証済みhandleとStreamLocal routerでprotocolを扱う。request/sequence/ACK、上限size、deadlineを照合。送信とremote ACKを分離し、応答不明を自動再実行しない。protocolの既存fixtureを実actorにも適用 |
| L4 | durable journal・復旧 | 通常Linux用の独立journalへ接続。process/pidfd/pipe、remote directory/socket/receipt、channel/routerの回収を別に確認。接続断後は同じprofile/binding/ownerの証拠で復旧し、別profileから見えないことを回収済み扱いしない |
| L5 | native・Host・SDK | AppMode/GUI receipt/cookie/slotを接続。実inspectとversion/capability照合後だけ一項目catalogを公開。Mapped→SDK初frameと更新→直接入力→Dialog→Close→全target退役を同じsessionで観測する |
| L6 | 接続設定UI・launcher | C1を通常workerへ接続し、接続先名と現在の起動可能項目を表示。profile/catalog revisionを要求へcapture。旧接続の入力ownerと資源を回収してから明示切替。起動ボタンが黙ってTermuxや別hostへ切り替わらない |
| L7 | 回帰・実ホスト・性能 | 固定B4/B6と通常Termux、通常GTKの切断/再接続/Stop/起動失敗/再起動、窓予算、設定保存復旧を確認。次に対象Ubuntu等のOS/arch/package/sshd実効値を確認して移植検証し、遅延・転送量・CPU/圧縮を測る |
| L8 | 利用範囲の拡張 | remote foot/PTY、構造化した任意アプリ定義・遠隔fileの一時実行/保存、Qt等を個別追加。物理/HMD/remoteかな漢字は最後。複数host同時・desktop全体共有・音・任意3Dは別設計 |

L2〜L5が揃うまで、通常GUI経路は利用可能と表示しない。
通信・helper待機は描画/入力threadで行わず、queueと保持frameを有界にする。
未知ownerや回収pendingを自動的にIdleへ戻さず、同じownerの明示回収を残す。

### 特に残る課題

- **常駐寿命:** B6の有限fixtureと異なり、通常アプリはユーザーが閉じるまで続く。heartbeat喪失、Quest停止、SSH断でも孤児を残さないleaseと復旧が必要。
- **認証と設定変更:** pin/endpoint/user/revisionを含むimmutable bindingを使う。credentialはcaptureした要求へ一度だけ渡し、遅い認証結果を新接続へ流さない。既存Termux鍵やpinを別hostへ流用しない。
- **DNS:** C1はDNS名を保存できるが現transportでは`DNS_UNSUPPORTED`。最初は検証可能なIP接続を使い、DNS対応は名前解決の取消/期限/宛先照合を設計して別実装する。
- **独立窓の対応:** app_idだけで起動成功や親子関係を決めない。追加Dialog、full targetの世代、同一instance、close時の入力解除、全世代のSDK退役を確認する。
- **通常UI:** 「設定保存」「認証成功」「起動可能catalog」「アプリ起動待ち」「表示」「回収待ち」を区別する。profile切替や再読込でアプリを自動再実行しない。
- **入力と計測:** GTK英数の合成検査を物理入力やremote IMEの成功へ広げない。remote workloadの未実装値はunknownとし、Quest全体やTermuxの値を代用しない。
- **LAN・他ホスト:** 隔離環境の成功を任意Ubuntuの成功にしない。Waypipe 0.11.3と実feature、SSH転送の許可、GTK/Wayland backendを対象ごとに確認する。圧縮は両端対応と実測後に選ぶ。

### 通常Linux GUIの完了判定

通常UIで接続先を明示選択し、そのhostのcatalogから一つ起動できることを最小到達点とする。
同じsource/job/profile/instanceで、描画進行・英数入力・追加Dialog・Close・停止・切断後の回収と通常復帰を記録する。
host fixture、helperの`complete`、catalogの表示、Mapped消失だけでは合格にしない。
物理キーとHMD、remote IME、任意アプリ、複数hostはそれぞれ独立した受入れを残す。

## 3. 記録と再開の約束

1. 再開指示後にmain/作業branch/CI/導入APK/実runtimeを照合する。Build176へ新sourceの検証結果を誤対応させない。
2. FreedoomのF1〜F4とLinuxのL1〜L4は、所有ファイルと実機ownerを分ければ並行準備できる。APK配備・ADBは一人のownerが直列化する。
3. runごとにsource/digest/APK/helper/bundle/profile revision、固定case、判定・失敗理由・回収結果・次actionを残す。秘密鍵・catalog本文・入力本文・室内画像は記録しない。
4. 既存goalのNX01〜18全体とPhase5/6の未受入れを保持し、この二項目の計画追記や部分成功で完了にしない。
