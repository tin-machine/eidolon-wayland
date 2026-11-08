# Ubuntu等の別マシンからGUIを表示する拡張案

作成日: 2026-11-07。当初の調査基準: `871f496`。以下の当初案の制約表は、その時点のsourceを記録したもの。
2026-11-08更新: 固定SSH/SHMとBuild174のLinux GTK input/dialog・回収は成立。通常GUIの製品接続は未完了。
通常profileの保存APIはmain、通常GTK helper/protocolは未統合branch `43767a4`に保存済み。
2026-11-08レビュー反映: SSH・Waypipe・Eidolon compositorの方式を維持し、通常化の契約と実用アプリ一種の受入れを補強した。
現在の到達点・事前確認・L1〜L8の実装順と完了条件は[詳細追記](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-normal)を優先する。
実装・実機検証は相談のため一時停止中であり、文書更新だけで再開しない。
[次フェーズ計画](next-phase-termux-startup-linux-gui-plan-20261107.md)第10節の補足。
ユーザーはQuest内Termuxに加え、Ubuntu Desktop等へSSH接続し、GUIをQuestで表示・操作できるか相談した。

## レビューを反映した到達点と優先事項

レビュー基準はmain `ea068ba`と未統合C2 `43767a4`。コードと記録の静的レビューであり、今回の計画更新で
コード修正・main統合・APK配備・実機検証が完了したとは扱わない。Build174等の既存証拠はそのsourceと範囲を維持する。

最初に通常UIから自動終了しないGTK desktop demoを扱える状態を作り、次に**既存のLinux実用アプリ一種を固定登録し、
Questから起動・ファイルを開く／保存する・終了まで行えること**を確認する。この二つの到達点を分け、
任意execの全面開放やremote foot/PTYの完成を実用アプリ一種の前提にしない。

| 優先事項 | 計画へ追加した条件 |
| --- | --- |
| C2の非同期終了event | 自然終了通知とheartbeat/Stopが交差すると正当な通知を拒否する競合をL1/L3で修正する。ACKと非同期eventの相関規則、交差fixtureは[protocol契約](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-protocol)に記載 |
| 常駐GUIの寿命 | 起動・応答・停止の期限、更新可能なlease、総稼働時間を分ける。probeの90/120秒制限と累積出力量上限を一般アプリへ暗黙に持ち込まない。[寿命と出力](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-lifetime)を参照 |
| 実用アプリの受入れ | D-Bus、独立instance、portalのファイル選択画面、HOME/XDG設定保持、基本物理入力を通常demoの次に確認する。[L7aの条件](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-real-app)を参照 |
| 切断と所有者喪失 | 通信停滞、SSH断、Questのheartbeat停止、Linux helper死亡、host再起動を区別する。未保存作業の終了可能性とunknown ownerの解消手順を[障害・復旧契約](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-failure-recovery)へ追加 |
| 描画と性能 | Wayland backendとrendererを別に確認する。圧縮・遅延・CPUに加え、総pixel面積、buffer量、Waypipeを含むメモリを測る。[描画条件](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-rendering)を参照 |
| 認証鍵 | host key pinとAPKのclient identityを分け、鍵共有／host別鍵の選択と要求へのcaptureをL6前に確定する。[認証契約](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-credentials)を参照 |

## 技術的な成立性

**可能な拡張であり、既存のSSH・Waypipe・Wayland compositorを利用する方式が第一候補。**
Waypipeは別マシンのWaylandアプリを表示側へ転送するための仕組みである。
[作者による構成説明][waypipe-author]と[Ubuntu配布のマニュアル][waypipe-man]を確認した。
Eidolonの現在の転送経路を、Quest内の通信からLAN越しの通信へ一般化する。

```mermaid
flowchart LR
  subgraph Ubuntu[Ubuntu等の実行側]
    App[GUIアプリ] <--> Server[Waypipe server]
    Server <--> Sshd[sshdとremote Unix socket]
  end
  Sshd <-->|SSH暗号化通信・描画データと入力| Client[Eidolon内SSH・Waypipe client]
  subgraph Quest[Quest 3]
    Client <--> Compositor[Eidolon Wayland compositor]
    Compositor <--> Panel[空間内のパネル・キーボード・コントローラー]
  end
```

Ubuntu側でアプリを動かし、Quest側で表示と入力を扱う。x86_64のアプリをQuest向けに移植する方式ではない。
Ubuntu向けバイナリーはUbuntuに残り、ファイルの読み書きも原則としてそのマシン上で行う。
Quest APKからUbuntuのSSHへ直接接続する構成を目指し、この接続の中継としてTermuxを必須にしない。
WaylandではEidolonがcompositor/serverだが、Waypipeでは表示側をclient、アプリ実行側をserverと呼ぶ。

対象はQuest向けに起動する個別のGUIウィンドウ。Ubuntuで既に開いている窓の移送や
デスクトップ画面全体の共有は別の設計になる。単にfoot内から通常の`ssh`を実行するだけでは、
遠隔GUIをEidolonの窓へ接続する処理は追加されない。

## 当初調査時点の制約と拡張対象

次の表は当初の`871f496`時点の記録であり、C1保存APIやB4/B6まで未実装という意味ではない。
通常UIからの任意host接続は未完成で、IP変更だけでは成立しない。現在の層別状態は詳細追記を参照する。

| 現行の制約 | 調査した場所 | 必要な拡張 |
| --- | --- | --- |
| 接続先は`127.0.0.1:8022`に固定 | [session.rs](../native/transport/src/session.rs) | profileからhost/portを解決し、DNS・IPv4/IPv6・期限・取消を扱う |
| profileは最大1個、IDは`termux-local`のみ | [commands.rs](../native/model/src/commands.rs)、[host_key.rs](../native/transport/src/host_key.rs) | 接続先の保存・選択とschema移行。複数同時接続は別段階 |
| UIと保存はusername/host keyを中心としたTermux用 | [ConnectionProfile.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/ConnectionProfile.kt)、[CredentialStore.kt](../android/app/src/main/java/io/github/tinmachine/eidolon/CredentialStore.kt) | 表示名・host・port・実行環境の区分・認証設定。Linux usernameの入力規則も確認 |
| home、Python、helper、foot等のpathがTermux固定 | [launcher.rs](../native/transport/src/launcher.rs)、[eidolon-session.py](../scripts/termux/eidolon-session.py) | Linux用のpath/環境adapterと、version付きhelper契約 |
| Waypipeは0.11.3のC実装、SHM中心、圧縮なし | [deps.lock.json](../native/deps.lock.json)、[waypipe.rs](../native/core/src/waypipe.rs) | 対向の実装・版・featureを検証。LANで圧縮と転送量を評価 |

既存の鍵・host key pin照合、StreamLocal転送、lease、所有processの回収、停止時の入力解除は再利用候補。
launcherのSHA照合やJSON検査を外すことでLinuxへ対応する設計にはしない。
OSに依存しない監督処理と、Termux/Linuxのpath・環境設定を分離する。
LinuxへAndroid/Termux専用のexec環境変数を持ち込まず、Termux側の既存対策も維持する。

## 接続先の準備と小規模検証の条件

初回は同じLAN内のUbuntu一台、一つのアプリで検証する。
対象OS・CPU architecture・SSH認証・実際のpackage/版・表示backendに加え、Pythonのpidfd APIとLinuxのsubreaperを確認する。
接続先を決めた後の事前確認をL1より前へ置き、実ホスト固有の前提をUI統合後まで先送りしない。
今回、特定のUbuntuマシンにログインしたり、packageやsshd設定を変更したりはしていない。

- Ubuntu側にSSH server、対応Waypipe、Python helper、試験用アプリを用意する。
  通常ユーザーの専用session directoryへsocketを作り、既存desktopのsocketを置き換えない。
- OpenSSHのremote Unix socket転送を許可する必要がある。
  [`AllowStreamLocalForwarding`][sshd-config]、転送禁止設定、鍵ごとの制約等の実効値を確認する。
  SSHログイン成功だけを、転送可能の証拠にしない。
- APK専用鍵の公開部分を対象ユーザーへ登録し、対象hostのfingerprintを検証してprofileへ保存する。
  host key変更を自動受理せず、Termux向けpinを他のマシンへ流用しない。
  接続先・ユーザー・pin・profile revisionと回収journalの対応を維持する。
  client認証鍵の共有／host別管理はhost key pinとは別の選択として、[認証契約](nx11-nx18-normal-gui-followup-plan-20261108.md#linux-credentials)で確定する。
- 最初はAPKと揃えたWaypipe 0.11.3のC実装・必要featureを基準にする。
  Ubuntu標準packageが同じ版・実装とは限らない。版番号だけで相互接続を合格にせず、SHM転送を検証する。
  別版やRust実装へ広げる場合は独立した互換性確認にする。
  [作者のRust移植記録](https://mstoeckl.com/notes/code/waypipe_to_rust.html)にはC実装との接続検査もあるため、
  C/Rustの全面的な非互換を前提にしない。配布manpageの版・実装と、固定C版のCLI/featureは区別する。
- アプリのWayland backendと、Eidolonが扱えるSHM経路を確認する。
  Ubuntu側にGUIログイン済みであることを一律の前提にしないが、D-Bus、font、locale、
  toolkitの初期化等の必要条件はアプリごとに調べる。
- 同一ユーザーの既存アプリへ起動要求を転送するsingle-instance/D-Bus動作に注意する。
  processが正常終了しても、既存Ubuntu画面に窓が出ただけならQuest表示の成功にしない。
  アプリの独立instance起動方法等を検証し、無関係なdesktop processは停止しない。
  実用アプリ一種では専用／既存session busの選択、portal backendの表示先、永続設定の保存先まで定義する。
  同process内のGTK Dialog成功を、外部サービスのファイル選択画面の成功へ読み替えない。

## ランチャー・ファイル指定・負荷表示との統合

[ランチャー計画](application-launcher-plan-20261107.md)の登録内容を、接続先profileへ対応付ける。
「Quest内 / Ubuntu」等の短い接続先表示を付け、同名アプリでも実行場所を判別できるようにする。
最初は選択中の一接続先のカタログを扱い、別hostの窓を同時に混在させる方式は後段とする。

- ファイル指定、実行file、interpreter、cwd、アイコンの取得元、カタログのpathは、その接続先のnamespaceで解決する。
  Ubuntuの`/home/...`をQuest/Termuxのpathとして処理しない。遠隔ファイルをAPKへコピーして実行しない。
- 既存のTermux用定義は`termux-local`として移行する。未知のprofile IDや切断中のhostを、
  自動的にQuest内Termuxへ置き換えて実行しない。設定reloadだけで接続・起動を繰り返さない。
- カタログの登録IDは接続先IDと組にして区別する。profileとそのカタログの対応はAPK側で管理し、
  ランチャーTOMLへ秘密鍵や任意の接続先への自動切替を埋め込む必要はない。
  具体的なprofile/schemaの移行は実装前に確定する。
- 窓のCPU/RSS/Disk I/Oリングは、Ubuntu側の対象process群から取得する。
  Quest全体のメトリクスと混ぜず、取得不可の場合はunknownとして扱う。
  groupキーにも接続先を含め、同名のfootをhostをまたいで自動的にまとめない初期案とする。

複数接続先の同時表示は、session/window/scene世代、入力focus、journal、
一接続だけ失敗した場合の回収を分離した後に検討する。
その場合も最大9枚は**全接続先の合計**とし、hostごとに9枚の予算を増やさない。
一接続先ずつの初期段階では、切替時の既存窓の終了と必要な保存をUIで案内し、起動操作だけで黙って切断しない。

## LAN越しに確認する制約

端末内通信と比べ、往復遅延・Wi-Fi変動・転送量が操作感へ影響する。
現在の`--compress none`を固定の最適解とせず、両端の対応を確認してnone/lz4/zstdを比較する。
採用時はAPK側とLinux側の起動設定を合わせ、転送量・CPU負荷・入力から描画までの遅延を測る。
結果を待つ間も描画/入力threadを通信待ちで止めず、queueとframeの保持数に上限を設ける。
窓数と総pixel面積を記録し、Waypipeのbuffer複製、compositorのcopy、SDK側の保持量を含めたメモリ圧力も評価する。

現在のAPK WaypipeはDMA-BUF/video/VAAPIを無効にしている。
`--no-gpu`はGPUアクセスを必要とするprotocolを遮断する指定であり、任意アプリを自動的にSHMへ変換する機能ではない。
GTK/QtのWayland backend指定とrenderer指定を分け、検証したアプリ・版・renderer条件を登録内容へ結び付ける。
PC側に強いGPUがあっても、任意の3Dアプリや動画を現行SHM経路で快適に表示できる保証にはならない。
まず小さな静的GUI・テキスト中心のアプリから始め、高更新頻度・高解像度は別に測る。
X11アプリ、音声、clipboard/file転送、切断後の同一GUIセッション復元は、個別に設計・検証する後続項目。
初期の切断時は既存のlease/所有資源回収契約を守り、再接続時は新しいsessionとして扱う。
スリープ等でheartbeatがleaseを超えて停止する場合は、未保存のGUI作業も終了し得る仕様をUIへ示す。
実際のQuestのスリープ挙動・heartbeat停止は実機確認事項であり、脱着だけで必ず切断すると仮定しない。
「GUIが表示された」をこれらの機能まで成立した証拠にしない。

## 実装を再開した後の順序

当初のfoot先行案を、既存B6の成功を利用する通常GTK先行へ変更し、実用アプリ一種の確認を前倒しする。
profile保存と通常SSH actorの採用、pidfd ownerとremote lease/journal、固定probeと常駐アプリを区別する。
更新後の順序と個別受入れは[詳細追記](nx11-nx18-normal-gui-followup-plan-20261108.md)に従う。

1. **対象hostの事前確認:** OS/arch、SSH転送許可、Waypipe実装・feature、Python/pidfd/subreaper、GTKを確認する。
2. **L1〜L4:** C2の非同期終了event競合を修正し、通常用の寿命・出力契約、SSH actor、lease/journal、owner喪失時の復旧を接続する。
3. **L5〜L6:** 通常UI/launcherから固定GTK demoを起動し、描画・英数入力・Dialog・Close・全資源回収を確認する。
4. **L7a:** 既存の実用アプリ一種を固定登録し、独立起動・ファイルを開く／保存する・設定保持・基本物理入力を確認する。
5. **L7b:** 継続稼働、通信断・スリープ条件、helper死亡・host再起動、失敗後復旧、圧縮・遅延・メモリを評価する。
6. **L8:** remote foot/PTY、Qt等の別toolkit、構造化した任意アプリ定義・遠隔fileの一時実行/登録へ広げる。
   HMDの総合受入れ・remote IME・複数host等は個別に残し、L7aの基本確認で代用しない。

全体の着手順は[次フェーズ計画の概算コスト順](next-phase-termux-startup-linux-gui-plan-20261107.md#implementation-order)のNX18へ改訂した。
小さなUI・入力・Termux内の拡張を先行し、本書の一接続先probeは九枚/group完成を必須条件にしない。
この調査を実装開始やUbuntu実機への変更承認として扱わない。

[waypipe-author]: https://mstoeckl.com/notes/gsoc/blog.html
[waypipe-man]: https://manpages.ubuntu.com/manpages/stonking/man1/waypipe.1.html
[sshd-config]: https://man.openbsd.org/sshd_config#AllowStreamLocalForwarding
