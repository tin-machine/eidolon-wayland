# Build 174: IME入口とGTK入力・サイズ変更の統合検証

2026-11-08。APKのsourceは`02e4b28`、native/Android入力digestは
`320d66501d3ff77495a7f3d9464a9b39aeeae00c9b2340ac6bbf9f99e7206b6a`。
通常foot向けの[IME入口](nx09-terminal-ime-entry-20261108.md)、
[Linux GTKの追加窓準備待ち](remote-linux-gtk-sdk-b6c-20261108.md)、
[サイズ変更のPC観測通知](gtk-resize-observation-handshake-20261108.md)を統合した。
実IMEのかな漢字変換、物理キー、HMDの受入れは別に残す。

## ビルドとhost検証

debug/releaseのassemble・lint・native package検査が成功した。
JVMはdebug234件、release159件、いずれも失敗・error・skipなし。
native coreのIMEを含む24件、debug feature無効時の境界1件、core all-target clippyが成功した。
Pythonは418件中411件実行・7件条件付きskip、別起動のTermux GUI owner14件も成功した。
shared Cargo targetでは検証owner lock内でworkspace entrypointのmtimeを更新し、
実際のtest名・件数を確認した。filter0件を合格にはしない。

| APK | SHA-256 |
| --- | --- |
| debug | `9921833fcce0c9b6d6e1d99b3ba5dfc5a792e1f9fad68db0682bdcfff5487122` |
| unsigned release | `8334d9cd04f6049134bc9042581cd03e1b93f376e91127988403e3cd5d0fcfe2` |

旧Build173はnative-depsの10cycles後、transport Idle・pending cleanupなし・Mapped窓0を確認してから更新した。
導入後もAPK versionとsource digestを照合した。unsigned releaseを実機導入した結果ではない。

## 共通IME経路

固定`ime-terminal`から、同じ通常controllerのON→BOUND→OFFを確認した。
ONではView/Window focus、InputConnection active、native raw blockがすべてtrue、pending ACK0だった。
OFFではcontroller STOPPED、Android解放、native raw block解除、元targetへのraw return COMPLETEを確認した。
入力本文・候補・周辺文脈は保存せず、文字注入も行っていない。

最初のwrapperはstart直後の自動focusを期待し、`external_focus_lost/WAITING_TARGET`で25秒timeoutした。
外部離脱後に自動で入力先を選ばない動作を維持し、既存の準備手順と同じ明示focusへ変更した。
次のwrapperはOFFの正常な`connection_failure="closed"`を失敗扱いした。
元のfailed結果を保持し、保存済みON/OFF metadataから上記の全条件を別評価してPASSとした。
同じ動作を成功表示のためだけに繰り返してはいない。

証拠は`ime-cycle174/4ca4ec55f4b8d0d171321207f8dbdd95/`の
`on-metadata.json`、`off-metadata.json`、`evaluation.json`。
通常入口特有のowner先行取得→Panel作成→View到着は、後続の固定通常入口検査と物理Ctrl+Spaceで確認する。

## Linux GTK

同じ保存済みv2 profile、pin、隔離sshd identityを再利用し、input/dialogを別nonceで実行した。
inputの固定文字・削除・button、dialogの追加窓描画・選択・Escape・親窓終了が両方PASSした。
dialogは3poll/108msの最初の準備待ちを実際に観測した。
両caseともSHM/SDK draw、全target退役、remote journal/所有process回収、通常runtime/SDK復帰、
lease解放、product設定不変がtrue。最後に所有sshdをreapした。

証拠は`linux-integration174/81623dae7edecc31/`。
input nonceは`5d35f219d1f1bc1b3c2eee68569206c1`、
dialog nonceは`89e751e6cb78130ab181d7c9f95b2df4`。
Build171〜173の失敗は以前の記録のまま保持し、新しい成功へ置き換えない。
通常Linux GUIランチャー、任意アプリ、GUIのIME、物理操作の合格には数えない。

## Termux GTKとPC観測通知

input `1d9e46c6e50c83c2b986a545e0c175b9`、
dialog `1043db7bfd2103bd8b8194f2b31a194e`も同sourceで成功した。

初回resize `adcde30f07e5212881e634222b92bc12`は800×500の同full target drawを保持できたが、
PCが`ResizeRequest`に存在しないwidth/heightを要求して通知せず、8秒で停止した。
実型はrequest_id/targetだけであり、host fixtureにも同じ誤った前提があった。
hostだけを`c3db831`で修正し、native/Android digestが変わらないことを確認した。
logical/requested/configured/committedの4sizeとrender content sizeの800×500照合は維持した。

別nonce `e2aaedbd2a7ecdbc7075712eb81fecaa`で再検証し、次を同jobで確認した。

- PC保存sequence1222で、同request・旧target・serial108・新full target・800×500・現在content drawを照合。
- `gtk-resize-return`は一回、Androidの観測状態はCOMPLETE/return_requests1。
- 640×360への復帰、C counter、SDK独立サイズ判定、全target/SDK/Board/control/MODE退役が成功。
- 元footを維持し、child/remote returncode0、TERM/KILLなし、remote cleanup確認true。

証拠は`gtk-interaction174/20261108T085321-757175-e2aaedbd2a7ecdbc7075712eb81fecaa/result.json`。
失敗回の回収成功と操作成功は分け、失敗artifactも保持した。

## 再現の入口と次の作業

成果物・source archive・JUnit・全試行ログは、設定済みwork rootの
`eidolon-ime-implementation-20261107/`へ保存する。directory0700、記録0600。
保存鍵・sshd stateは既存runtimeに保持し、この作業記録へ複製しない。

```sh
scripts/build-android 174
scripts/build-release 174
scripts/quest-ime-terminal start --serial "$EIDOLON_QUEST_SERIAL"
# 表示されたnonceを使い、focus → toggle → status → stop。
# ONのBOUNDとOFFのraw return COMPLETEは、それぞれmetadataで確認する。
scripts/quest-run-gtk-interaction --serial "$EIDOLON_QUEST_SERIAL" --case resize \
  --run-id "$EIDOLON_GTK_RUN_ID" --manifest-sha256 "$EIDOLON_GTK_MANIFEST_SHA256" \
  --binary-sha256 "$EIDOLON_GTK_BINARY_SHA256"
```

通常IME初期owner経路、viewer pose読取りと正面復帰、固定Freedoomの通常GUI起動、
native/SDK/メニューを通したグループ化を続ける。
この短い検証をPhase6長時間試験、signed release実測、最終の物理・装着受入れに代用しない。
