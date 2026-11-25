# Ubuntu systemd GUI セッションの実装と検証

2026-11-26。対象は [systemd GUI セッションの詳細設計](systemd-gui-session-design-20261126.md)。切替は Ubuntu の保存済み接続先単位。Quest の通常ランチャーから Ubuntu の Chrome と一般 GUI を起動し、Termux の GUI にも同じ待機時間を渡す。

## 実装とビルド

| 対象 | 確認 |
| --- | --- |
| Ubuntu Desktop | 既存の非 root の SSH 接続と固定した Waypipe を使い、ユーザーの transient systemd unit でアプリを管理する。 |
| 無応答の待機時間 | 1〜1440 分、既定60分。設定はアプリの起動時に確定する。 |
| Chrome | 接続先とアプリに紐付く永続プロファイル、セッション専用 tmpfs のキャッシュ。同じ Chrome に通常窓を追加する場合は既存の実行世代を使う。 |

Host と Android のビルド・検証は Ubuntu Desktop で実施した。Host 全体回帰は `9975d8c` で成功した。対象は Rust の model、theme、render、lifecycle、transport、compositor、spatial anchor。Python 本体は1,536件成功、9件スキップ。Termux の Python は124件成功した。開発 APK 157（`d163156`）のビルド、lint、JVM 単体テスト538件、配布物のライセンス検査が成功した。

## Ubuntu Desktop の GUI 試験

Host コンポジターへ、SSH と Waypipe を通して Chrome と GTK の Zenity を実際に表示した。各アプリで明示停止と実際の SSH 転送終了を試し、Chrome は各24プロセス、Zenity は各6プロセスを対象の cgroup 内に確認した。終了後に cgroup とセッションの runtime ディレクトリが消え、永続プロファイルが残った。Chrome の cache は tmpfs、Zenity の cache は永続ストレージだった。

Chrome の使い捨てプロファイルで、ローカルの試験ページから1 MiBのキャッシュ可能な応答を6個読み込んだ。HTTP キャッシュに6,787,072バイト、tmpfs のキャッシュ全体に13,086,720バイトの割当を確認した。Chrome が確定したページの保存データは、停止して tmpfs を回収したあと、新しい Chrome サービスのページから読み戻せた。ブラウザーが保存を確定する前に停止した試行では、同じデータの読み戻しに失敗した。

## Quest 3 の通常ランチャー

登録済み Quest 3 を Wi-Fi ADB 経由で照合し、開発 APK の署名と既存の設定を確認して更新した。起動や窓の終了には通常の UI を使い、Android の表示計画と SDK の採用、Ubuntu の unit、cgroup、タイマー、一時領域を別々に確認した。

| ケース | 観測 |
| --- | --- |
| Chrome の初回起動 | 実機に通常窓が表示され、ホストの実効タイマーは3,600秒。キャッシュは tmpfs。別の短い試行でアプリ起動直後のキャッシュ割当は6,471,680バイトだった。 |
| Chrome の追加窓と終了 | 2枚目の窓が同じ InvocationID に属し、片方を閉じると残りを維持した。最後の窓を閉じると Quest の枠と Ubuntu の unit、cgroup、タイマー、一時領域が消えた。 |
| 明示的な停止 | 「停止・切断」の通常操作から、約3.8秒で窓とホスト資源の回収を確認した。 |
| 共通設定と追加の Ubuntu アプリ | Chrome の稼働中に待機時間を7分へ保存し、その Chrome の3,600秒が維持された。追加起動した Alacritty は独立した実機パネル、cgroup、420秒のタイマーを持った。 |
| Ubuntu と Termux の同時利用 | 7分設定で Chrome、Alacritty、Termux の foot を別の実機パネルに同時表示した。Ubuntu の二つの unit に420秒のタイマー、Termux の別ワークスペースには420秒のヘルパー応答を確認した。Termux だけを閉じたとき Ubuntu の二つの unit は稼働を続けた。 |
| Termux を主接続にした場合 | 主接続を Quest 内の Termux に切り替え、ターミナルから foot を表示した。同じ7分設定に基づく420秒の応答を確認した。foot を維持したまま Ubuntu の Alacritty を追加で表示し、別の unit の420秒タイマーを確認した。 |
| SSH 転送を切断 | 対象 Chrome の転送ソケットを所有する SSH プロセス一件だけを終了した。約0.5秒でその cgroup と一時領域が消えた。Alacritty は同じ InvocationID で稼働を続けた。Quest の棚から同じ保存済みホストへ接続し、残ったタイマーを回収して、新しい世代の Chrome を起動できた。 |
| 1分設定での無応答と復帰 | Quest の Activity をバックグラウンドに移し、期限前に復帰すると、SSH 転送と Chrome の実行世代と窓が維持され、タイマーが更新された。再度バックグラウンドに移し、期限の約4秒前まで同じ SSH 転送とサービスが生存したことを確認。期限の約1.1秒後にホスト資源が回収され、Quest 復帰後も前のセッションの回収待ちはなかった。 |
| 既定60分での失効 | APK 157 で Chrome を通常起動し、稼働中の更新を確認してから Quest の Activity をバックグラウンドに移した。約2分ごとと期限直前に同じ SSH ソケットの inode、InvocationID、期限を照合した。期限の1.18秒後に cgroup、タイマー、一時領域の消失を確認した。Quest の同じアプリプロセスに戻り、先の GUI と回収待ちが消えていることを確認した。 |

旧方式の Chrome は保存済みの接続先で旧方式の回収を実施した。当該世代の終了済みプロセスと転送 EOF を照合し、深い一時キャッシュ一件を退避してから回収した。既存プロファイルと設定を保存した。

Termux の試験では、既存の回収待ち記録を保持した状態で別のワークスペースを使った。Quest の無応答試験は Activity がバックグラウンドに入り生存通知を停止する経路で実施した。装着検知による本体スリープは未検証。Chrome のキャッシュ測定は空ページと制御したローカル応答の範囲で、普段の利用時の容量上限は引き続き実使用から判断する。
