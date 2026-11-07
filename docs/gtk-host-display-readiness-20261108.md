# GTK host検証のXvfb起動確認

2026-11-08、全Python 263件のうちX11を使う四件が`display_unavailable`で失敗し、
同じmoduleの単独13件は成功した。後の診断付き全体266件でも成功し、discovery後・GTK起動前・各case前後で
環境変数の変更キー、実行file、cwd、signal handlerに差はなかった。元の一時失敗の原因を特定したとは扱わない。

確定した診断上の穴は、displayfdのEOF/空文字を`DISPLAY=:`として採用し、Xvfbの早期exitや実接続を検査していなかった点である。
host testの起動を、有限期限内のASCII表示番号・生きている子process・実際のGTK display初期化の確認へ変更した。
実接続には既にコンパイルしたproduction C fixtureを、private DISPLAYと`GDK_BACKEND=x11`で起動する。
`wayland_required`は`gtk_init_check`成功後、widget/map前の拒否なので、host displayのreadyを確認できる。
`display_unavailable`だけを同一期限内で待ち、ほかの結果や期限ちょうど/以降の成功は拒否する。

新しいX11 toolやctypesは使わない。接続probeの終了後も共有displayを維持するため、
導入済み`Xvfb -help`で確認した`-noreset`を指定する。失敗時は固定reason、exit code、stderr byte数だけを保持し、stderr本文は保存しない。
起動が失敗した場合も自分が起動したXvfbを回収し、既存四件をskipへ変更しない。
元の263件の失敗ログは保管し、この変更を原因が再現・修復された証拠へ読み替えない。

変更後はEOF/不正形式/早期exit/期限境界等の7件、既存GTK fixtureの13件、全Python 270件が成功した（既存skip7件）。
全体は一度だけ実行し、四つのX11検査を含む元の検証条件を維持した。

CI run `42414968688` ではhost jobが成功し、Androidの実APK Python検査だけで
`XVFB_GTK_PROBE_TIMEOUT` が発生した。Xvfbは生存していたが、表示番号取得までに共通5秒の大半を使い、
最初のGTK接続probeへ残り約0.035秒しか渡せなかった（attempt1/戻りcode未取得/stderr1174bytes）。
これは初回のローカル4件失敗の原因を断定するものではなく、新診断で分離できたCI側の別の具体的な停止点である。

表示番号取得とGTK実接続へ、それぞれ有限5秒の枠を与える。合計最大10秒、子の早期exit/EOF/
不正番号/異常reasonを拒否し、接続readyの条件は変更しない。
4.965秒で表示番号が来てもGTKへ5秒を渡す回帰を追加し、境界8件と実C/Xvfb13件が成功した。
