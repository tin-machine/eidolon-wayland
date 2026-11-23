# Android IME / Wayland text-input — D6 feature index

The [Android IME → Wayland plan](android-ime-wayland-plan-20261107.md) is registered as **proposed architecture**. The [implementation log](android-ime-implementation-20261107.md) and [NX09 entry](nx09-terminal-ime-entry-20261108.md) are **historical** records. The [A1/A2 comparison probe](android-ime-probe-20261107.md), [terminal debug probe](android-ime-terminal-probe-20261108.md), and [NX09 normal owner probe](nx09-normal-ime-probe-20261108.md) are runbooks for bounded/debug tests, not evidence of a successful Japanese IME or keyboard acceptance.

```sh
python3 scripts/docs.py context --feature android-ime --task A1
python3 scripts/docs.py context --feature android-ime --task NX09
python3 scripts/docs.py context --feature android-ime --task NORMAL-PROBE
python3 scripts/docs.py affected --path native/core/src/compositor/ime.rs
```

**Safety boundary:** do not promote synthetic input injection, a debug-only case, or an observed focus transition to proof of real kana-kanji conversion, normal release UI behavior, or HMD acceptance. Each historical report belongs to its pinned build/device/OS circumstances, and the present source/Quest state must be re-checked. The task keys index reading sets; they do not replace the original command preconditions.

## ランチャーから開いた窓

Shift+Space は選択中の窓のランタイムで IME を開始し、同じランタイムの応答とフォーカスを確認して直接入力に戻ります。Termux の foot と、Wayland の `text-input-v3` が有効な Ubuntu アプリが対象です。IME パネルを開いた後の Ctrl+Space は、Stealth Keyboard など Quest で選択した IME の入力モード切替に使えます。日本語変換と候補表示はその IME が担当します。

## 開発版の実機ログ

開発版の `EidolonIme` タグで、IME接続・フォーカス、入力パネルを通ったキー押下、未確定・確定の通知、Waylandへの投入と完了を件数で確認できます。文字を含むログは既定で無効です。再現に使う文字だけを入力し、検証後に無効へ戻します。以下の `QUEST_ADB_SERIAL` には `adb devices` で確認した対象端末を指定します。

```sh
adb -s "${QUEST_ADB_SERIAL}" shell setprop log.tag.EidolonImeText VERBOSE
adb -s "${QUEST_ADB_SERIAL}" logcat -s EidolonIme:I EidolonImeText:V
adb -s "${QUEST_ADB_SERIAL}" shell setprop log.tag.EidolonImeText SILENT
```

IMEパネルに届いた文字キー、IMEが送った未確定・確定文字を、1件80文字まで端末のlogcatに記録します。文字を含むlogcatにはテスト入力が残るため、記録の共有前に内容を確認してください。
