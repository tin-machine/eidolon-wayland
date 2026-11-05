# popup・session内clipboardの検査

2026-11-05、I07実装中。hostの実Wayland client検査を先行し、Android/通常SDK/HMDの結果を分けて記録する。

## clipboard

同じDisplay/session内の`text/plain`、`text/plain;charset=utf-8`（charset大文字表記も可）を対象とする。
`text/plain`もUTF-8として検査する。画像、Android clipboard同期、drag-and-dropは対象外。
所有権変更はkeyboard focusと最近の入力serialを照合し、数値の大小比較に依存しない。
focusが変わると古いserial/offerを失効させる。

転送先FDへ直接中継せず、所有clientから一旦最大1 MiBを読み、EOFとUTF-8を検査してから渡す。
転送全体の期限は5秒。同時転送4本、offer64個、入力serial64個、MIMEは既知の3表記だけに制限する。
転送先はnonblockingにできるpipe/socketに限定する。
各transferは一tick最大64 KiBを処理し、protocol ownerで待機read/writeを行わない。
超過時は転送先を閉じ、残りは期限まで上限付きで読み捨てる。
offer破棄・focus変更・selection変更・所有client切断・session終了ではFDを閉じる。
本文はdiagnostics/report/logへ保存せず、件数と原因だけを記録する。

```bash
cargo test --locked -p eidolon-core clipboard_two_clients -- --nocapture
```

二つの独立Wayland接続を使い、固定UTF-8テキストと1 MiBちょうどの転送、超過、不正UTF-8、
5秒の送信停止と受信停止、古いoffer、focus/serial不一致、受信取消、転送中の所有client消失を検査する。
修正後のhost検査は5.43秒で成功した。通常アプリ全般との互換性やHMD表示の成功には数えない。

初回はresource集計の既存method名を誤りcompileに失敗し、`pixel_bytes()`へ修正した。
次にkeyboard focus通知の中からkeyboard状態を再取得して同じlockを待つ不具合を検出した。
自分の検査プロセスだけを終了し、通知引数からclientを保持する形へ変更した。
Smithay 0.7ではfocusをNoneへ変える際のcallbackがないため、入力reset側でも明示的にselection focusを解除する。

## popup

既存の親rectangle内clipと描画順を使用し、popup用の別SDK panelを増やさない。
明示grabはseat、実pointer pressのserial、親のfocus、親子順を照合し、最大8段とする。
keyboard focusは描画可能な最上位popupへ移し、windowの識別子は親のまま保持する。
外側を押した操作はpopupを閉じるために消費し、別windowへ同じ押下を流さない。
Esc、入力reset、親の破棄でもpopupを解除する。
`popup_done`と交差したcommitは再表示せず、切断の原因となる追加errorを返さない。

```bash
cargo test --locked -p eidolon-core popup_grab_clip -- --nocapture
```

固定clientでserial拒否、20×20 popupの12×12 clip、子popupの7×7 clip、
keyboard/pointer focus、別windowの親/serial拒否、外側クリックの消費、別windowへの後続クリック、親破棄を検査する。
popup検査はhostで成功した。fixture共用時の可視性指定の誤り、`Proxy`のimport不足によるcompile失敗を修正し、
Clippyの`get().is_none()`指摘も`contains_key()`へ直した。

Build90のdebug/lint/packageとhost全suite（Rust108件、Clippy/fmt、Python53件成功・実APK用6件skip、
launcher17件）が成功した。Build90は端末へ入れず、追加検査を含むBuild91で実機検証した。
受信側がpipeを読み出さない場合の5秒timeout、転送中の所有client切断、取消後のsource側BrokenPipe、
別windowのserialでpopupを作れないことを検査する。

追加検査も含むBuild91のhost全suite、debug/lint/packageが成功した。
取消ではsource側の書込みがBrokenPipeになることも確認し、所有clientは転送中に実接続を切断する。
受信側停止のtimeoutでは、UTF-8検査後にpipeへ渡した途中の正しいデータが残ることがある。
EOFだけを完全な転送の証拠とせず、client側でも必要なデータ長やアプリの転送結果を判定する。

```bash
EIDOLON_ARTIFACT_ROOT=/absolute/private/work/builds scripts/build-android 91
adb -s "$QUEST_SERIAL" install -r /absolute/private/work/builds/RUN/eidolon-debug.apk
scripts/quest-install-shm-fixture --serial "$QUEST_SERIAL"
scripts/quest-check --serial "$QUEST_SERIAL" --case popup-clipboard --instrumentation --timeout 180
```

instrumentationは通常SDK表示・HMDの受入れを含まない。
通常UI復帰までの検査は同じcaseから`--instrumentation`を外して行い、実XR FOCUSEDを要求する。

## Build91のQuest結果

`20261105T213301-6de081c9`でpopup/clipboardの両scenarioが成功した。
popupの親/serial拒否、12×12・7×7 clip、キー2event、`popup_done`5件、別window focusが成立した。
clipboardは10,559msで完走し、通常/1 MiB転送2件、超過1件、不正UTF-8 1件、送受信timeout各1件、
取消2件を記録した。終了時のselectionは空、active transferとclient/object/surface/window/画像・pool・fixture資源はすべて0。

APK SHA256は`7775780e6b6faba29e502b7acb4ab903a172ba141e68b91ef35756e5583c3256`、
source digestは`defc04c970514c4005751daff0406c64d5940f452597e3a7516559b08028a91d`。
同版のWayland state10scenario `20261105T213317-5eb01579`、pointer-wire `20261105T213325-1c9e1dbb`、
US/JIS wire `20261105T213330-f00b915a`、配置保存 `20261105T213338-4f287ae4`も成功した。
Rust108件、Clippy/fmt、launcher17件、debug/release・lint・package、実APKのPython59件が成功した。
同じAPKで二枚表示の73秒継続、Surface喪失中の入力・表示切替・個別closeとresize-nativeも回帰検査が成功した。
検査用foot/Waypipe/producerは残らず、作業前から存在したTermux bash/sshdの同一性を維持した。

このpopup/clipboard fixtureはAPK内の実Wayland clientを使用する。clipboard本文のWaypipe越しの転送や
任意GUI appの互換性をこの結果だけでは保証しない。
通常SDK/UI復帰はGuardianDialogActivityが前面のため`not_run`、HMD/物理操作も最終確認待ち。

実装時には同梱Smithay 0.7のsourceと、[Waylandのselection/focus/serialの仕様](https://wayland.freedesktop.org/docs/book/Protocol.html)、
[core protocolのdata-device仕様](https://wayland.freedesktop.org/docs/html/apa.html)を参照した。
Smithay標準のclient間転送へそのまま委譲するとMIME・byte数・期限をここで検査できないため、
source/manager基盤を使いつつofferとFD中継を製品側で管理している。
