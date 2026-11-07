package io.github.tinmachine.eidolon

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import android.view.MotionEvent
import android.view.InputDevice
import android.view.Window
import android.view.WindowInsets
import android.view.inputmethod.InputMethodManager
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.Scene
import com.meta.spatial.runtime.PanelDisplay
import com.meta.spatial.toolkit.DpPerMeterDisplayOptions
import com.meta.spatial.toolkit.Panel
import com.meta.spatial.toolkit.PanelRegistration
import com.meta.spatial.toolkit.PanelStyleOptions
import com.meta.spatial.toolkit.QuadShapeOptions
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.UIPanelSettings
import com.meta.spatial.toolkit.ViewPanelRegistration
import java.lang.ref.WeakReference
import java.util.concurrent.atomic.AtomicLong
import org.json.JSONArray
import org.json.JSONObject

/** 通常XR内の比較fixture。本文を診断へ渡さず、実IME/物理/HMD受入れを自動PASSにしない。 */
object ImeProbe {
    private val ui = Handler(Looper.getMainLooper())
    private val panelIds = listOf(View.generateViewId(), View.generateViewId())
    private var activity = WeakReference<Activity>(null)
    private var sceneAvailable = false
    @Volatile private var active: Run? = null

    fun activity(value: Activity) { activity = WeakReference(value) }
    fun isActive(): Boolean = active != null
    fun physicalKey() { active?.physicalKeys?.incrementAndGet() }
    fun sceneReady() { sceneAvailable = true }
    fun sceneClosing() {
        sceneAvailable = false
        active?.finish("blocked", "scene_closed")
    }
    fun stop(nonce: String): Boolean {
        val run = active ?: return false
        if (run.nonce != nonce) return false
        run.finish("completed", "operator_stop")
        return true
    }
    fun exercise(nonce: String): Boolean = active?.takeIf { it.nonce == nonce }?.exercise() ?: false
    fun sdkTap(nonce: String): Boolean = active?.takeIf { it.nonce == nonce }?.sdkTap() ?: false
    fun start(nonce: String, context: Context, scene: () -> Scene, done: () -> Unit) {
        (context as? Activity)?.let(::activity)
        check(active == null)
        Run(nonce, scene, done).also { active = it; it.start() }
    }
    fun registrations(): List<PanelRegistration> = panelIds.mapIndexed { index, id ->
        ViewPanelRegistration(
            id,
            dynamicViewCreator = { entity, context ->
                active?.takeIf { it.owns(entity) }?.createPanel(context, index) ?: TextView(context)
            },
            settingsCreator = {
                UIPanelSettings(
                    shape = QuadShapeOptions(width = 1.05f, height = .9f),
                    display = DpPerMeterDisplayOptions(),
                    style = PanelStyleOptions(themeResourceId = R.style.EidolonTheme),
                )
            },
            panelSetupWithRootView = { view, obj, entity ->
                val owner = active?.takeIf { it.owns(entity) }
                view.post {
                    if (owner != null && active === owner) owner.panelReady(index, view, obj.display)
                }
            },
        )
    }

    private class Run(val nonce: String, val scene: () -> Scene, val done: () -> Unit) : Runnable {
        val started = SystemClock.elapsedRealtime()
        val result = JSONObject().put("case", "ime-editor-probe").put("nonce", nonce).put("result", "running")
            .put("scope", "android_editor_fixture").put("linux_delivery_connected", false)
            .put("synthetic", JSONObject().put("result", "not_run"))
            .put("exercise", JSONObject().put("result", "not_run"))
            .put("sdk_tap", JSONObject().put("result", "not_run"))
            .put("acceptance", JSONObject().put("actual_ime", "not_verified").put("physical_input", "not_verified")
                .put("hmd", "not_verified").put("pointer_recovery", "not_verified"))
        val entities = mutableListOf<Entity>()
        val roots = mutableMapOf<Int, ImeProbeLayout>()
        val windows = mutableMapOf<Int, Window>()
        val displays = mutableMapOf<Int, PanelDisplay>()
        val editors = linkedMapOf<String, View>()
        val labels = mutableListOf<TextView>()
        val previews = mutableListOf<TextView>()
        val counts = linkedMapOf<String, Long>()
        val lengths = linkedMapOf<String, Int>()
        var target: View? = null
        var closed = false
        var created = false
        val physicalKeys = AtomicLong()
        var operation = "start"
        var operationReturned: Boolean? = null
        var syntheticRun = false
        private val exerciseSteps = listOf("A", "B", "C").flatMap { id ->
            listOf("focus", "show", "hide", "restart", "clear_focus").map { id to it }
        }
        private val exerciseTick = Runnable { advanceExercise() }
        private var exerciseStarted = 0L
        private var exerciseIndex = 0
        private var exercisePending = false
        private var exerciseCleaning = false
        private var exerciseCleanupFailed = false
        private var exerciseBaseline = emptyMap<String, Long>()
        private val tapSteps = listOf("A", "B", "C").flatMap { id ->
            listOf("prepare", "tap", "show_if_focused").map { id to it }
        }
        private val tapTick = Runnable { advanceTap() }
        private var tapStarted = 0L
        private var tapIndex = 0
        private var tapPending: JSONObject? = null
        private var tapCleaning = false
        private var tapCleanupFailed = false
        private var tapBaseline = emptyMap<String, Long>()
        private var tapTouchBaseline = emptyMap<String, Long>()

        private fun exercising() = result.getJSONObject("exercise").optString("result") == "running"
        private fun tapping() = result.getJSONObject("sdk_tap").optString("result") == "running"
        private fun connectionCounts() = mapOf(
            "A" to (counts["A_create_connection"] ?: 0L),
            "B" to (counts["B_create_connection"] ?: 0L),
            "C" to (counts["C_metadata_openConnection"] ?: 0L),
        )
        fun exercise(): Boolean {
            val proof = result.getJSONObject("exercise")
            if (closed || tapping() || proof.optString("result") != "not_run" || windows.size != 2 ||
                editors.size != 3 || editors.values.any { !it.isAttachedToWindow } ||
                result.getJSONObject("synthetic").optString("result") != "pass") return false
            exerciseStarted = SystemClock.elapsedRealtime()
            exerciseBaseline = connectionCounts()
            proof.put("result", "running").put("sequence", "focus-show-hide-restart-clear-v1")
                .put("input_source", "framework_focus_requests").put("text_injected", false)
                .put("steps", JSONArray()).put("cleanup_requested", false).put("cleanup_failed", false)
            ui.post(exerciseTick)
            return true
        }
        private fun observation(since: Long, baseline: Map<String, Long>): JSONObject = JSONObject()
            .put("elapsed_ms", SystemClock.elapsedRealtime() - since)
            .put("editors", JSONArray(editors.map { (id, view) -> editorState(id, view) }))
            .put("framework_connection_callbacks", JSONObject(connectionCounts().mapValues { (id, count) ->
                count - (baseline[id] ?: 0L)
            }))
            .put("activity", activityState()).put("panels", panelStates())
        private fun exerciseObservation() = observation(exerciseStarted, exerciseBaseline)

        private fun clearEditors(): Boolean {
            var failed = false
            // clearFocusだけで別editorへfocusが移らないよう、両panelを先に抑制する。
            roots.values.forEach { it.descendantFocusability = android.view.ViewGroup.FOCUS_BLOCK_DESCENDANTS }
            editors.values.forEach { editor ->
                runCatching {
                    editor.context.getSystemService(InputMethodManager::class.java)
                        .hideSoftInputFromWindow(editor.windowToken, 0)
                }.onFailure { failed = true }
                runCatching { editor.clearFocus() }.onFailure { failed = true }
            }
            roots.values.forEach { root ->
                runCatching { root.requestFocus() }.onFailure { failed = true }
                root.descendantFocusability = android.view.ViewGroup.FOCUS_AFTER_DESCENDANTS
            }
            runCatching { NativeSessionHost.resetInput() }.onFailure { failed = true }
            return failed
        }
        private fun cleanExercise() {
            exerciseCleaning = true
            exerciseCleanupFailed = clearEditors()
            result.getJSONObject("exercise").put("cleanup_requested", true)
                .put("cleanup_failed", exerciseCleanupFailed)
        }
        private fun endExercise(status: String, reason: String) {
            ui.removeCallbacks(exerciseTick)
            if (!exerciseCleaning) cleanExercise()
            val proof = result.getJSONObject("exercise")
            proof.put("result", if (exerciseCleanupFailed) "fail" else status).put("reason", reason)
            runCatching { proof.put("cleanup_observation", exerciseObservation()) }.onFailure {
                proof.put("result", "fail").put("failure_class", it.javaClass.simpleName)
            }
        }
        private fun advanceExercise() {
            if (closed || !exercising()) return
            try {
                if (SystemClock.elapsedRealtime() - exerciseStarted >= 25_000) {
                    endExercise("blocked", "exercise_timeout"); publish(); return
                }
                if (exercisePending) {
                    val (id, name) = exerciseSteps[exerciseIndex]
                    result.getJSONObject("exercise").getJSONArray("steps").put(exerciseObservation()
                        .put("step", exerciseIndex + 1).put("editor", id).put("operation", name)
                        .put("operation_returned", operationReturned ?: JSONObject.NULL))
                    exercisePending = false
                    exerciseIndex += 1
                }
                if (exerciseIndex == exerciseSteps.size) {
                    if (exerciseCleaning) { endExercise("completed", "sequence_finished"); publish(); return }
                    cleanExercise()
                } else {
                    val (id, name) = exerciseSteps[exerciseIndex]
                    target = checkNotNull(editors[id])
                    operate(name, automatic = true)
                    exercisePending = true
                }
                publish()
                ui.postDelayed(exerciseTick, 750)
            } catch (failure: Exception) {
                result.getJSONObject("exercise").put("failure_class", failure.javaClass.simpleName)
                endExercise("fail", "exercise_exception")
                runCatching { publish() }
            }
        }

        fun sdkTap(): Boolean {
            val proof = result.getJSONObject("sdk_tap")
            if (closed || exercising() || proof.optString("result") != "not_run" || displays.size != 2 ||
                windows.size != 2 || editors.size != 3 || editors.values.any { !it.isAttachedToWindow } ||
                result.getJSONObject("synthetic").optString("result") != "pass") return false
            tapStarted = SystemClock.elapsedRealtime()
            tapBaseline = connectionCounts()
            tapTouchBaseline = touchCounts()
            proof.put("result", "running").put("sequence", "prepare-sdk-tap-focused-show-v1")
                .put("input_source", "sdk_panel_dispatch_event").put("text_injected", false)
                .put("steps", JSONArray()).put("cleanup_requested", false).put("cleanup_failed", false)
            ui.post(tapTick)
            return true
        }
        private fun touchCounts() = (0..1).flatMap { panel -> listOf("down", "up").map { action ->
            "panel_${panel}_touch_$action" to (counts["panel_${panel}_touch_$action"] ?: 0L)
        } }.toMap()
        private fun tapObservation() = observation(tapStarted, tapBaseline)
            .put("panel_touch_callbacks", JSONObject(touchCounts().mapValues { (key, count) ->
                count - (tapTouchBaseline[key] ?: 0L)
            }))
        private fun cleanTap() {
            tapCleaning = true
            tapCleanupFailed = clearEditors() || tapCleanupFailed
            result.getJSONObject("sdk_tap").put("cleanup_requested", true).put("cleanup_failed", tapCleanupFailed)
        }
        private fun endTap(status: String, reason: String) {
            ui.removeCallbacks(tapTick)
            if (!tapCleaning) cleanTap()
            val proof = result.getJSONObject("sdk_tap")
            proof.put("result", if (tapCleanupFailed) "fail" else status).put("reason", reason)
            runCatching { proof.put("cleanup_observation", tapObservation()) }.onFailure {
                proof.put("result", "fail").put("failure_class", it.javaClass.simpleName)
            }
        }
        private fun submitTap(id: String, editor: View, step: JSONObject) {
            val panel = if (id == "C") 1 else 0
            val display = checkNotNull(displays[panel])
            val visible = Rect()
            val location = IntArray(2)
            editor.getLocationOnScreen(location)
            val displayId = editor.display?.displayId ?: -1
            val x = location[0] + editor.width / 2
            val y = location[1] + editor.height / 2
            val valid = editor.isAttachedToWindow && editor.isShown && editor.width > 0 && editor.height > 0 &&
                editor.getLocalVisibleRect(visible) && visible.contains(editor.width / 2, editor.height / 2) &&
                displayId >= 0 && displayId == display.rootView?.display?.displayId &&
                editor.windowToken == display.rootView?.windowToken &&
                x >= 0 && y >= 0 && x < display.widthInPx && y < display.heightInPx
            if (!valid) {
                step.put("outcome", "bounds_unavailable")
                return
            }
            step.put("geometry", JSONObject().put("panel", panel).put("display", displayId)
                .put("display_width", display.widthInPx).put("display_height", display.heightInPx)
                .put("left", location[0]).put("top", location[1]).put("width", editor.width).put("height", editor.height)
                .put("center_x", x).put("center_y", y))
            val now = SystemClock.uptimeMillis()
            fun dispatch(action: Int, key: String) {
                val event = MotionEvent.obtain(now, SystemClock.uptimeMillis(), action, x.toFloat(), y.toFloat(), 0)
                try {
                    // SDK PanelInputListenerと同じsource。SDK内の注入Booleanは公開APIから観測できない。
                    event.source = InputDevice.SOURCE_CLASS_POINTER
                    display.dispatchEvent(event, false)
                    step.put(key, true)
                } finally { event.recycle() }
            }
            try { dispatch(MotionEvent.ACTION_DOWN, "down_submitted") }
            finally { dispatch(MotionEvent.ACTION_UP, "up_submitted") }
            step.put("outcome", "dispatch_submitted")
        }
        private fun advanceTap() {
            if (closed || !tapping()) return
            try {
                if (SystemClock.elapsedRealtime() - tapStarted >= 25_000) {
                    endTap("blocked", "sdk_tap_timeout"); publish(); return
                }
                tapPending?.let { step ->
                    step.put("after", tapObservation())
                    result.getJSONObject("sdk_tap").getJSONArray("steps").put(step)
                    tapPending = null
                    if (step.has("failure_class") || step.optString("outcome") == "bounds_unavailable") {
                        endTap("fail", "sdk_tap_step_failed"); publish(); return
                    }
                    tapIndex += 1
                }
                if (tapIndex == tapSteps.size) {
                    if (tapCleaning) { endTap("completed", "sequence_finished"); publish(); return }
                    cleanTap()
                } else {
                    val (id, name) = tapSteps[tapIndex]
                    val editor = checkNotNull(editors[id])
                    val step = JSONObject().put("step", tapIndex + 1).put("editor", id).put("operation", name)
                        .put("before", tapObservation()).put("route", if (name == "tap") "sdk_panel_dispatch_event" else "framework")
                        .put("down_submitted", false).put("up_submitted", false).put("show_returned", JSONObject.NULL)
                    tapPending = step
                    try {
                        when (name) {
                            "prepare" -> {
                                check(!clearEditors())
                                step.put("outcome", "cleanup_requested")
                            }
                            "tap" -> submitTap(id, editor, step)
                            "show_if_focused" -> {
                                if (editor.hasWindowFocus() && editor.hasFocus()) {
                                    step.put("outcome", "show_requested")
                                    step.put("show_returned", editor.context.getSystemService(InputMethodManager::class.java)
                                        .showSoftInput(editor, 0))
                                } else step.put("outcome", "show_skipped_no_focus")
                            }
                        }
                    } catch (failure: Exception) {
                        step.put("outcome", "operation_exception").put("failure_class", failure.javaClass.simpleName)
                    }
                }
                publish()
                ui.postDelayed(tapTick, 750)
            } catch (failure: Exception) {
                result.getJSONObject("sdk_tap").put("failure_class", failure.javaClass.simpleName)
                endTap("fail", "sdk_tap_exception")
                runCatching { publish() }
            }
        }

        fun owns(entity: Entity): Boolean = !closed && entities.any { it.id == entity.id }

        fun start() {
            NativeSessionHost.resetInput()
            NativeSessionHost.publishCheck(result)
            ui.post(this)
        }
        fun count(name: String, length: Int? = null) {
            if (closed) return
            counts[name] = (counts[name] ?: 0) + 1
            if (length != null) lengths[name] = length.coerceAtLeast(0)
        }
        private fun label(context: Context, value: String) = TextView(context).apply {
            text = value; textSize = 17f; setTextColor(Color.WHITE)
        }
        fun createPanel(context: Context, index: Int): View {
            val root = ImeProbeLayout(context) { event -> count("panel_${index}_$event") }.apply {
                orientation = LinearLayout.VERTICAL
                setPadding(20, 16, 20, 16)
                setBackgroundColor(Color.rgb(30, 34, 42))
                isFocusableInTouchMode = true
                descendantFocusability = android.view.ViewGroup.FOCUS_AFTER_DESCENDANTS
            }
            roots[index] = root
            root.addView(label(context, if (index == 0) "IME比較 A1: 標準入力欄 A / B" else "IME比較 A2: 独自editor C"))
            root.addView(label(context, "固定語で確認。本文は保存しません。実IME・物理・HMDの合格は別途確認。"))
            if (index == 0) {
                addEditor(root, "A", ImeProbeEditText(context) { name, length -> count("A_$name", length) })
                addEditor(root, "B", ImeProbeEditText(context) { name, length -> count("B_$name", length) })
            } else {
                val committed = label(context, "直前の確定: （未入力・この画面内だけに表示）").also { previews.add(it) }
                addEditor(root, "C", ImeEditorView(context,
                    onTransaction = { transaction ->
                        count("C_transaction")
                        transaction.commit?.let { value ->
                            count("C_commit", value.length)
                            committed.text = "直前の確定: $value"
                        }
                        transaction.preedit?.let { count("C_preedit", it.text.length) }
                        true
                    },
                    onMetadata = { metadata -> count("C_metadata_${metadata.lastOperation}", metadata.utf16Length) },
                    onKeyEvent = { count("C_returned_key"); true },
                    onEditorAction = { count("C_editor_action"); true },
                ))
                root.addView(committed)
            }
            root.addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                addView(button(context, "表示") { operate("show") })
                addView(button(context, "非表示") { operate("hide") })
                addView(button(context, "focus解除") { operate("clear_focus") })
            })
            root.addView(LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                addView(button(context, "reset") { operate("reset") })
                addView(button(context, "restart") { operate("restart") })
                addView(button(context, "無害ボタン") { count("panel_${index}_button"); operation = "button" })
            })
            root.addView(button(context, "比較を終了（合格判定は行わない）") { finish("completed", "panel_stop") })
            root.addView(label(context, "準備中").also { labels.add(it) })
            return root
        }
        private fun addEditor(root: LinearLayout, id: String, view: View) {
            view.id = View.generateViewId()
            editors[id] = view
            root.addView(label(root.context, "入力欄 $id"))
            root.addView(view, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 104))
            view.onFocusChangeListener = View.OnFocusChangeListener { _, focused ->
                count("${id}_focus_${if (focused) "gain" else "loss"}")
                if (focused) target = view
            }
        }
        private fun button(context: Context, title: String, action: () -> Unit) = Button(context).apply {
            text = title; textSize = 14f
            isFocusable = false
            setOnClickListener { if (!closed) runCatching(action).onFailure { count("operation_failed") } }
        }
        fun panelReady(index: Int, view: View, display: PanelDisplay?) {
            if (closed || roots[index] !== view) return
            val window = display?.presentation?.window
            if (display != null) displays[index] = display
            if (window != null) windows[index] = window
            count("panel_${index}_attached")
        }
        private fun operate(name: String, automatic: Boolean = false) {
            if ((exercising() || tapping()) && !automatic) return
            val editor = target ?: editors.values.firstOrNull() ?: return
            val imm = editor.context.getSystemService(InputMethodManager::class.java)
            operation = name; operationReturned = null; count("operation_$name")
            when (name) {
                "focus" -> operationReturned = editor.requestFocus()
                "show" -> {
                    editor.requestFocus()
                    editor.post { if (!closed && editor.isAttachedToWindow) operationReturned = imm.showSoftInput(editor, 0) }
                }
                "hide" -> operationReturned = imm.hideSoftInputFromWindow(editor.windowToken, 0)
                "clear_focus" -> {
                    editor.clearFocus()
                    roots.values.firstOrNull { it.findViewById<View>(editor.id) === editor }?.apply {
                        descendantFocusability = android.view.ViewGroup.FOCUS_BLOCK_DESCENDANTS
                        requestFocus()
                        descendantFocusability = android.view.ViewGroup.FOCUS_AFTER_DESCENDANTS
                    }
                }
                "reset" -> {
                    if (editor is ImeEditorView) editor.resetEditor()
                    else if (editor is EditText) { editor.text.clear(); imm.restartInput(editor) }
                    NativeSessionHost.resetInput()
                }
                "restart" -> imm.restartInput(editor)
            }
        }
        private fun activityState(): JSONObject {
            val app = activity.get()
            return JSONObject().put("display", app?.display?.displayId ?: -1)
                .put("window_focus", app?.hasWindowFocus() ?: false)
                .put("imm_accepting_text", app?.getSystemService(InputMethodManager::class.java)?.isAcceptingText ?: JSONObject.NULL)
                .put("window_flags", app?.window?.attributes?.flags ?: 0)
        }
        private fun panelStates() = JSONArray(windows.map { (id, window) -> JSONObject().put("panel", id)
            .put("window_flags", window.attributes.flags).put("window_focus", window.decorView.hasWindowFocus()) })
        private fun editorState(id: String, editor: View): JSONObject {
            val imm = editor.context.getSystemService(InputMethodManager::class.java)
            val state = JSONObject().put("editor", id).put("display", editor.display?.displayId ?: -1)
                .put("attached", editor.isAttachedToWindow).put("window_token", editor.windowToken != null)
                .put("view_focus", editor.hasFocus()).put("window_focus", editor.hasWindowFocus())
                .put("imm_active_view", imm.isActive(editor)).put("imm_accepting_text", imm.isAcceptingText)
                .put("ime_insets_visible", editor.rootWindowInsets?.isVisible(WindowInsets.Type.ime()) ?: JSONObject.NULL)
            if (editor is EditText) state.put("utf16_length", editor.length())
                .put("input_type", editor.inputType).put("ime_options", editor.imeOptions)
                .put("selection_start", editor.selectionStart).put("selection_end", editor.selectionEnd)
                .put("composing_start", BaseInputConnection.getComposingSpanStart(editor.text))
                .put("composing_end", BaseInputConnection.getComposingSpanEnd(editor.text))
            if (editor is ImeEditorView) {
                val metadata = editor.metadata()
                state.put("generation", metadata.generation).put("sequence", metadata.sequence)
                    .put("connection_active", metadata.active).put("utf16_length", metadata.utf16Length)
                    .put("utf8_bytes", metadata.utf8Bytes).put("selection_start", metadata.selectionStart)
                    .put("selection_end", metadata.selectionEnd).put("composing_start", metadata.composingStart)
                    .put("composing_end", metadata.composingEnd).put("batch_depth", metadata.batchDepth)
                    .put("accepted_transactions", metadata.acceptedTransactions).put("rejected_operations", metadata.rejectedOperations)
                    .put("stale_operations", metadata.staleOperations).put("last_operation", metadata.lastOperation)
                    .put("last_failure", metadata.lastFailure ?: JSONObject.NULL)
            }
            return state
        }
        /** 非attachの別Viewへ固定文字だけを送る。実IME経由のcallback件数へ混ぜない。 */
        private fun synthetic(context: Context) {
            syntheticRun = true
            val proof = JSONObject().put("result", "running").put("input_source", "direct_input_connection_fixture")
                .put("actual_ime", false).put("physical_keyboard", false)
            result.put("synthetic", proof)
            val stock = ImeProbeEditText(context) { _, _ -> }
            val commits = mutableListOf<String>()
            var preeditSeen = false
            var preeditCleared = false
            val custom = ImeEditorView(context, onTransaction = { transaction ->
                transaction.commit?.let { commits.add(it) }
                transaction.preedit?.let { if (it.text == "にほん") preeditSeen = true; if (it.text.isEmpty()) preeditCleared = true }
                true
            })
            try {
                val a = checkNotNull(stock.onCreateInputConnection(EditorInfo()))
                val c = checkNotNull(custom.onCreateInputConnection(EditorInfo()))
                listOf(a, c).forEach { connection ->
                    check(connection.setComposingText("にほん", 1))
                    check(connection.commitText("日本語", 1))
                    check(connection.finishComposingText())
                }
                check(stock.text.toString() == "日本語")
                check(commits == listOf("日本語") && preeditSeen && preeditCleared)
                val metadata = custom.metadata()
                check(metadata.utf16Length == 0 && metadata.composingStart == -1)
                custom.resetEditor(restartInput = false)
                val count = commits.size
                check(!c.commitText("古い入力", 1) && commits.size == count)
                a.closeConnection(); c.closeConnection()
                proof.put("result", "pass").put("stock_fixed_text_match", true)
                    .put("custom_commit_once", true).put("preedit_observed_and_cleared", true).put("stale_connection_rejected", true)
            } catch (failure: Exception) {
                proof.put("result", "fail").put("failure_class", failure.javaClass.simpleName)
            } finally {
                custom.closeEditor(); stock.text.clear(); commits.clear()
            }
        }
        private fun publish() {
            val app = activity.get()
            val appImm = app?.getSystemService(InputMethodManager::class.java)
            val states = JSONArray(editors.map { (id, view) -> editorState(id, view) })
            result.put("stage", if (windows.size == 2) "awaiting_manual" else "panels_pending")
                .put("elapsed_ms", SystemClock.elapsedRealtime() - started).put("editors", states)
                .put("counts", JSONObject(counts as Map<*, *>)).put("last_lengths_utf16", JSONObject(lengths as Map<*, *>))
                .put("physical_key_events_observed", physicalKeys.get()).put("operation", operation)
                .put("operation_returned", operationReturned ?: JSONObject.NULL)
                .put("activity", activityState()).put("panels", panelStates())
            app?.let {
                result.put("selected_ime", Settings.Secure.getString(it.contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD) ?: "unknown")
            }
            @Suppress("DEPRECATION")
            val subtype = appImm?.currentInputMethodSubtype
            result.put("ime_subtype", subtype?.let { JSONObject().put("hash_code", it.hashCode())
                .put("language_tag", it.languageTag).put("mode", it.mode) } ?: JSONObject.NULL)
            val summary = editors.map { (id, view) -> "$id: D${view.display?.displayId ?: -1} V${view.hasFocus()} W${view.hasWindowFocus()}" }.joinToString(" / ")
            labels.forEach { it.text = "状態: ${result.optString("stage")} / $operation\n$summary\n物理イベント: ${physicalKeys.get()} / 合成fixture: ${result.getJSONObject("synthetic").optString("result")}\n実IME・候補・HMD・pointer復帰: 未判定\n10分で終了。ADB nonce終了も利用可。" }
            NativeSessionHost.publishCheck(result)
        }
        override fun run() {
            if (closed) return
            try {
                val elapsed = SystemClock.elapsedRealtime() - started
                if (elapsed >= 600_000) { finish("blocked", "probe_timeout"); return }
                if (!created && sceneAvailable) {
                    scene() // sceneが利用可能な通常XR sessionだけに生成する。
                    panelIds.forEachIndexed { index, id ->
                        val entity = Entity.create(listOf(Transform(Pose(Vector3(1.05f + index * 1.15f, 1.5f, 1.5f)))))
                        entities.add(entity)
                        // owner登録後にPanelを付け、旧runの遅延View生成を新runへ取り込まない。
                        entity.setComponent(Panel(id))
                    }
                    created = true
                }
                if (windows.size < 2 && elapsed >= 20_000) { finish("blocked", "panel_attach_timeout"); return }
                if (windows.size == 2 && !syntheticRun) synthetic(checkNotNull(editors["A"]).context)
                publish()
                ui.postDelayed(this, 1000)
            } catch (failure: Exception) {
                result.put("failure_class", failure.javaClass.simpleName)
                finish("fail", "probe_exception")
            }
        }
        fun finish(status: String, reason: String) {
            if (closed) return
            if (exercising()) endExercise("blocked", "probe_stopped")
            if (tapping()) endTap("blocked", "probe_stopped")
            closed = true; ui.removeCallbacks(this)
            ui.removeCallbacks(exerciseTick)
            ui.removeCallbacks(tapTick)
            runCatching { publish() }.onFailure { result.put("final_snapshot_unavailable", true) }
            var cleanupFailed = false
            editors.values.forEach { editor ->
                runCatching {
                    editor.context.getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(editor.windowToken, 0)
                    if (editor is ImeEditorView) editor.closeEditor()
                    editor.clearFocus()
                    if (editor is EditText) editor.text.clear()
                    editor.onFocusChangeListener = null
                }.onFailure { cleanupFailed = true }
            }
            entities.forEach { runCatching { it.destroy() }.onFailure { cleanupFailed = true } }
            val detached = roots.values.none { it.isAttachedToWindow }
            previews.forEach { it.text = "" }; previews.clear()
            entities.clear(); editors.clear(); windows.clear(); displays.clear(); roots.clear(); labels.clear(); target = null
            NativeSessionHost.resetInput()
            result.put("result", if (cleanupFailed) "fail" else status).put("reason", reason)
                .put("cleanup_failed", cleanupFailed).put("panels_destroy_requested", !cleanupFailed)
                .put("views_detached_observed", detached)
            NativeSessionHost.publishCheck(result)
            if (active === this) active = null
            done()
        }
    }
}
