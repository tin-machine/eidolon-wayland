# キーボード・seatの検証記録

2026-11-05。I02の実装中の記録。Bluetooth実機操作とHMDの受入れは最終段階に残す。
固定入力の自動試験を、物理キーボードを接続した操作成功として扱わない。

## 入力の経路

Android `dispatchKeyEvent` → `PhysicalKeyboard` →短いJNI enqueue → protocol owner →
Smithay seat → APKのWaypipe → SSH → TermuxのWaypipe → Wayland client。
文字列/Unicodeをkeycodeの代わりに送らない。

Android入口は物理device、`SOURCE_KEYBOARD`、down/up、system keyを確認する。
操作パネルに焦点がある間は通常のAndroid/Compose配送を使う。
「ターミナルへキー入力」で表示済みパネルを選び、「操作パネルへ戻る」で解除する。
controller clickによるwindow選択はI03で接続する。

AndroidはLinuxの入力keycodeをscanCodeとして公開するが、公開APIはdeviceごとの差異も警告している。
検証済みevdev領域だけを採用し、未知/0は明示したAndroid keyCode表へfallbackする。
JISの変換/無変換/半角全角/かな/円/ろも位置として扱い、暗黙のShiftや文字列変換をしない。
[AOSP keyboard devices](https://source.android.com/docs/core/interaction/input/keyboard-devices)、
[KeyEvent](https://developer.android.com/reference/android/view/KeyEvent)、
[InputDevice](https://developer.android.com/reference/android/view/InputDevice)を参照した。
定数は実際のcompileSdk 36のandroid.jarとも照合した。

Smithay 0.7の`KeyboardHandle.input`へ渡す直前だけevdevへ8を足す。
SmithayのWayland frontendがwire送信時に8を引く。
seatからkeymap FD、repeat rate25/delay600ms、enter、modifiersを通知し、
Androidのrepeat down/重複down/未知upは二重配送しない。
同じkeyを二つのdeviceで押した場合は、両方のupを受けるまでglobalなupを出さない。

焦点はhoverとは分離する。表示・Scene・Surface世代・applied ackが一致するwindowだけを選べる。
urgent resetは通常queueと別のatomicを使い、epochを進める。
古いepochの待機入力は破棄し、押下解除→modifiers→leaveの順で失われたupを補う。
Activity/XR/window focus喪失、device変更・切断、Surface喪失、disconnect/Stop、
入力queue満杯から解除する。再フォーカスで古いdownを復活させない。
通常診断にユーザーの文字やkey列は記録せず、状態と回数だけを出す。

## 同梱compiled keymapと上流patch

systemのXKB rulesへ依存しない。US/JISをassetから読み、include path数0を実測する。
既存のRMLVO初期化を経由すると`/eidolon/no-system-xkb`に依存して失敗するため、
固定Smithayへcompiled keymapから直接seatを作るAPIを追加した。

wireを読む独立clientの試験で、seat v5向け`KeymapFile.with_fd`が末尾NULを送っていないことも分かった。
[公式Wayland protocol](https://cgit.freedesktop.org/wayland/wayland/tree/protocol/wayland.xml)は
XKB v1をNUL終端文字列と定めている。受信側の検査を緩和せず、送信fileとsizeにNULを含めた。
Androidには一般の`/tmp`やprocessの`XDG_RUNTIME_DIR`がないため、旧client向けのコピーは
匿名のunsealed memfdで渡す。process全体の環境変数やTermuxの保存先を変更しない。
新しいclient向けのsealed FDと、非Androidの一時file経路は維持する。
全patchをlockのSHAで固定し、生成sourceの直接編集は拒否する。

## 試行と失敗

1. 最初のAndroid定数表では`KEYCODE_KATAKANA_HIRAGANA`のevdev値を95としていた。
   android.jarとLinux headerを照合して93へ訂正し、モデル検査に追加した。
2. Build67のfoot/ImageReader検査は描画・keymap初期化・遠隔回収を通ったが、停止時の
   `app.error=RENDER_BUDGET`をPCが拒否した。Stopとsubmitの競合を調査し、
   rendererの明示停止中に限り`NotReady`を正常な終了競合として扱う修正を加えた。
   稼働中の`NotReady`や予算超過は引き続き失敗とする。通常SDK foot67は成功した。
3. 新wire fixtureの初回compileで空のVec比較の型が曖昧になった。`Vec<u32>`を明示した。
4. keymap FDをstream readすると共有open-file offsetへ依存するため、位置0の`pread`へ変更した。
   それでもNUL検査が失敗し、上記の上流送信処理を特定した。修正後、US/JIS双方のwire試験が成功した。
5. queue満杯の単体検査で256件の通常完了eventも一気に発生させ、別のUI event上限128を超えた。
   検査を「満杯時のurgent reset要求と、失われたkeyがenqueueされないこと」に絞り、
   実際のrelease/leaveは独立Wayland clientで確認する形へ分離した。製品の上限は緩和していない。

## 再現

```bash
scripts/check-host --suite input
scripts/check-host --suite all
EIDOLON_ARTIFACT_ROOT=/absolute/private/work/builds scripts/build-android 68
adb -s "$QUEST_SERIAL" install -r /absolute/private/work/builds/RUN/eidolon-debug.apk
scripts/quest-check --serial "$QUEST_SERIAL" --case keyboard-wire --instrumentation
scripts/quest-check --serial "$QUEST_SERIAL" --case keyboard-wire
```

`keyboard-wire`はAPK UIDの実Display/seatと独立Wayland clientを使う。
US/JISのkeymap FD/hash、evdev wire値、Shift+2のkeysym、repeat設定、二deviceの押下、
reset時の12イベント、empty enter、unmapによるleave、client/FD/SHM回収を照合する。
実Bluetooth、Androidの物理配送、SSHを越えたfootの入力成功は別の受入条件である。
debug fixtureの文字列・codeをrelease APKへ含めない検査も行う。

## Questでの自動検査

| Build / run | 結果 |
| --- | --- |
| 67 / `20261105T144131-6b4a115b` | ImageReaderの描画後、停止時のRENDER_BUDGETでPCが拒否 |
| 67 / `20261105T144524-b516aec8` | 通常SDKのfoot表示・再生成・停止・UI復帰が成功 |
| 68 / `20261105T150105-86ee1347` | APK UIDのkeyboard wire検査、US/JISが成功 |
| 68 / `20261105T150110-656457da` | 通常SDKと同居するwire検査、US/JISと新runtime/UI復帰が成功 |
| 68 / `20261105T150117-2874d972` | ImageReader footの停止回帰が成功 |
| 68 / `20261105T150131-9dd87dd0` | 通常SDK footの描画・再生成・停止・UI復帰が成功 |
| 69 / `20261105T150805-df0f3aa5` | 通常Host→Core→実footセッションで固定合成入力と解除・再入力、停止197ms・UI復帰が成功 |

68の実clientが受信したkeymap本文はUS 71,196 bytes / JIS 71,708 bytes。
いずれも同梱assetおよびseatのhashと一致し、別途NULも検査した。
USのShift+2は`@`、JISは`"`、raw wireはevdevのまま。12 key通知、2 empty enter、2 leave、
rate25/delay600ms、modifier解除、すべての所有client/SHM/fixture FD回収が成功した。

`--case keyboard`は通常Hostのfocus/key入口へ固定KeyEventを渡す。
69では実SDK/FOCUSEDのまま、A/repeat/up/Backspaceで配送数4、Shift+A保持で6/押下2、
resetで8/押下0/focusなし、古epochのlate upをSTALEとして拒否して8を維持、
明示的な再フォーカス後Space/down/upで10/押下0となった。epochは4→5へ進んだ。
入力中にfoot由来のSHM更新も継続した。
これはAndroidの物理device filterや文字内容の正しさを実測した試験ではない。
`synthetic_input=true`、`synthetic_visibility=false`、`physical_entry_verified=false`、
`physical_input_verified=false`、`text_verified=false`を記録し、PCもその範囲を照合する。
独立wire試験と、通常セッションでの入力処理・描画進行の試験を合わせて使う。

```bash
scripts/quest-check --serial "$QUEST_SERIAL" --case keyboard
```

| APK | SHA256 |
| --- | --- |
| 67 | `f10ad73ad7b438ba9ca619922f99d4f66d103e42aee17ea27eab7d10f63280c3` |
| 68 | `e96c58e321efddfc0442cb716e7428139f3a2d31d16d4709c7c7f5b9986fe958` |
| 69 | `12a0e9f7621a1ee596ed181599a6c7f3793b8ee210e682011921b89f935b0092` |

hostの全Rust85test、Clippy/fmt、PC拒否検査とdebug/release/lint/package検査を実施した。
Android実Bluetooth、shell/tmux/Neovimの文字・修飾キー操作、HMDでの視認は最終受入れで確認する。
raw receiptは起動時に読み直す`codex-work-root`の`eidolon-phase5-implementation/`以下へ保存する。

## Build100の物理USキーボード確認（2026-11-06）

Questへ接続した物理USキーボードで、ユーザーが以下を確認した。

- footでの文字入力とBackspace。
- Ctrl+Lによるclear。
- controllerで別のfootを選ぶと、そのwindowだけへ入力が切り替わる。
- Shift+英字の大文字と、Shiftを離した後の小文字。
- 長押しrepeatと、離した後の停止。

最初はTermuxがOS topResumed、EidolonはVISIBLEで、footへのkey/button配送がなかった。
実際のAndroid `am start`でEidolonを前面へ戻すとSDK FOCUSEDへ遷移し、物理入力が通った。
入力本文を採取せず、window/focus・配送件数・押下残留だけを記録した。
この復帰はADBを使用したため、controllerだけでTermuxからEidolonへ戻る経路は未確認として残す。
FOCUSEDを合成したり、VISIBLE時の入力を無条件に許可したりはしていない。
残りの記号・行編集・Tab/Esc/Fkey・tmux/Neovim・Menu/sleepを確認してI02全体を判定する。

追加で、US記号、Ctrl+A/Eの行頭末移動、Alt+B/Fの単語移動、Tabのshell補完もユーザーが確認した。

### Neovimの実行環境差

tmuxの起動・pane分割は物理操作で成功した。Neovimはheadlessだけでは問題が見えず、通常TUIがすぐ終了した。
footのSSH環境で継承されたtermux-execにより、Neovimの自己実行パスが`linker64`になり、`--embed`起動に失敗していた。
同じ環境を専用PTYで再現し、modeをdisableにすると実行パスがNeovim本体へ戻り、TUIが起動・終了できた。
[Termux上流のADB entrypoint問題](https://github.com/termux/termux-exec-package/issues/40)と
[公式exec modeの説明](https://github.com/termux/termux-exec-package/blob/master/site/pages/en/projects/docs/usage/index.md#termux_exec__system_linker_exec__mode)も照合した。

ユーザーが`TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE=disable nvim --clean`で起動・文字入力・終了を確認した。
これは一時設定付きの成功であり、plain `nvim`の修正完了には数えない。
launcherへ、実contextで二段の固定execを成功させた場合だけmodeを選ぶ処理をローカルに用意した。
確認は1秒上限・launcher内一回、失敗時は元の動作を維持し、明示force/disableやLD_PRELOADも維持する。
このlauncher変更と負例はまだ未検証・未配備で、現在のAPK100/remote launcher83には含まれない。
