package io.github.tinmachine.eidolon

/** 状態表示は入力gateの観測値から作り、文字・キー列を保持しない。 */
data class InputConditions(
    val visible: Boolean = false,
    val xrFocused: Boolean = false,
    val runtimeReady: Boolean = false,
    val transport: String = "Idle",
    val editing: Boolean = false,
    val keyboards: Int? = null,
    val hasWindows: Boolean = false,
    val targetReady: Boolean = false,
    val focusedWindow: String? = null,
)

data class InputStatus(val reason: String, val message: String, val action: Action = Action.NONE) {
    enum class Action { NONE, OPEN, FOCUS, FINISH_EDITING }
    val actionLabel: String get() = when(action) {
        Action.NONE -> ""
        Action.OPEN -> "ターミナルを開く"
        Action.FOCUS -> "選んだ窓へ入力"
        Action.FINISH_EDITING -> "配置を確定して入力へ戻る"
    }
    companion object {
        fun from(c: InputConditions): InputStatus = when {
            !c.visible || !c.xrFocused -> InputStatus("BACKGROUND", "入力停止 · Eidolonへ戻ると入力できます")
            !c.runtimeReady -> InputStatus("PREPARING", "入力停止 · 画面を準備中")
            c.transport != "Ready" -> when(c.transport) {
                "Idle", "Disconnected", "Failed" -> InputStatus("DISCONNECTED", "入力停止 · 接続されていません", Action.OPEN)
                else -> InputStatus("CONNECTING", "入力停止 · 接続の準備・終了を確認中")
            }
            c.editing -> InputStatus("EDITING", "配置編集中 · 文字入力は停止", Action.FINISH_EDITING)
            c.keyboards == 0 -> InputStatus("NO_KEYBOARD", "キーボード未検出 · 接続後に入力先を選んでください")
            !c.hasWindows -> InputStatus("NO_WINDOW", "入力先のターミナルがありません", Action.OPEN)
            !c.targetReady -> InputStatus("TARGET_NOT_READY", "入力先の画面を準備中 · 表示する窓を選んでください")
            c.focusedWindow == null -> InputStatus("NO_FOCUS", "入力先が未選択 · 下の窓を選んでください", Action.FOCUS)
            c.keyboards == null -> InputStatus("DEVICE_UNKNOWN", "入力先: 窓 ${c.focusedWindow} · キーボードを確認中")
            else -> InputStatus("READY", "入力先: 窓 ${c.focusedWindow} · キーボード入力できます")
        }
    }
}
