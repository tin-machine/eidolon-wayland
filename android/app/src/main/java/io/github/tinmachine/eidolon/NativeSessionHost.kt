package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.KeyEvent
import androidx.compose.runtime.mutableStateOf
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONObject
import org.json.JSONArray

/** Application scopeの所有者。Activity、SDK Entity、Surfaceを保管しない。 */
object NativeSessionHost {
    private val worker = Executors.newSingleThreadScheduledExecutor { task -> Thread(task, "eidolon-host").apply { isDaemon = true } }
    private val ui = Handler(Looper.getMainLooper())
    private val scenes = AtomicLong(0)
    private val requests = AtomicLong(0)
    @Volatile private var handle = 0L // 作成・停止はworker、短いenqueueは任意threadから。
    private val xrState = AtomicReference("IDLE")
    private val activityVisible = AtomicBoolean(false)
    private var store: DiagnosticsStore? = null
    private var paths: JSONObject? = null
    private var credentials: CredentialStore? = null
    private var identity: CredentialStore.Identity? = null
    private var profiles = JSONArray()
    private var lastConnectRequest: String? = null // connectとcancelをworker上の順序で束縛する。
    private var connectPending = false
    private var cancelledThrough = 0L
    private data class PendingDisconnect(val connectRequest: String?, val session: String?, var request: String? = null)
    private var pendingDisconnect: PendingDisconnect? = null
    private data class KeyboardFocus(val runtime: Long, val epoch: Long, val target: String, val token: String)
    @Volatile private var keyboardFocus: KeyboardFocus? = null
    data class InputTarget(val runtime: Long, val epoch: Long, val identity: String, val target: String)
    @Volatile private var inputTargets: Map<String, InputTarget> = emptyMap()
    private fun targetIdentity(target: JSONObject): String = listOf("session","window","scene","generation").joinToString(":") { target.getString(it) }
    val keyboardSummary = mutableStateOf("操作パネルへ入力")
    private var lastWrite = 0L
    private var state = "Starting"
    private var sshState = "未接続"
    private data class TerminalOpen(val nonce: String, val previousSession: String,
        var connectRequest: String? = null, var connectAccepted: Boolean = false,
        var startRequest: String? = null, val deadline: Long = SystemClock.elapsedRealtime()+30000)
    private var terminalOpen: TerminalOpen? = null // workerだけで操作し、二重clickをcoalesceする。
    private var terminalError: String? = null
    val summary = mutableStateOf("Starting")
    val credentialSummary = mutableStateOf("鍵を確認中")
    val connectionSummary = mutableStateOf("未接続")

    init { worker.scheduleAtFixedRate({ safely { tick() } }, 0, 16, TimeUnit.MILLISECONDS) }

    fun initialize(context: Context) {
        val application = context.applicationContext
        worker.execute { safely {
            if (store == null) store = DiagnosticsStore(application)
            if (paths == null) paths = JSONObject().put("native_library_dir", application.applicationInfo.nativeLibraryDir).put("files_dir", application.filesDir.absolutePath)
            if (handle == 0L && NativeBridge.status.ready) {
                if (credentials == null) credentials = CredentialStore(application)
                identity = checkNotNull(credentials).ensureIdentity()
                profiles = JSONArray()
                checkNotNull(credentials).profile(checkNotNull(identity))?.let { profiles.put(it) }
                ui.post { credentialSummary.value = "APK専用鍵を暗号化して保存済み" }
                startRuntime()
            }
        } }
    }
    fun sceneReady(): Long {
        val scene = scenes.incrementAndGet()
        send("scene_ready", "scene", scene.toString())
        return scene
    }
    fun setXrState(value: String) { if (value != "FOCUSED") { keyboardFocus = null; inputTargets = emptyMap() }; xrState.set(value); send("set_xr_state", "state", value) }
    fun setActivityVisible(value: Boolean) { if (!value) { keyboardFocus = null; inputTargets = emptyMap() }; activityVisible.set(value); send("set_activity_state", "visible", value) }
    private fun enqueueConnect(): String? {
        val request = requests.incrementAndGet().toString()
        val command = JSONObject().put("schema", 1).put("type", "connect").put("request_id", request).put("profile_id", "termux-local")
        if (NativeBridge.nativeCommand(handle, command.toString()) != NativeBridge.ACCEPTED) return null
        lastConnectRequest = request
        connectPending = true
        return request
    }
    fun connect() { worker.execute { safely {
        if (handle == 0L || pendingDisconnect != null || connectPending) return@safely
        val transport = snapshot().getJSONObject("runtime").getJSONObject("transport")
        if (transport.getString("state") in setOf("Idle", "Disconnected", "Failed")) enqueueConnect()
    } } }
    fun openTerminal() { worker.execute { safely {
        if (handle == 0L || terminalOpen != null || pendingDisconnect != null || connectPending) return@safely
        val runtime = snapshot().getJSONObject("runtime")
        val transport = runtime.getJSONObject("transport")
        val app = runtime.optJSONObject("app")
        if (app != null && app.optString("session") == transport.optString("session") && app.isNull("error") && app.optString("state") != "stopped") return@safely
        if (transport.getJSONArray("pending_cleanup").length() != 0) { terminalError = "前回の終了を確認してください"; return@safely }
        val bytes = ByteArray(8).also { java.security.SecureRandom().nextBytes(it) }
        val operation = TerminalOpen(bytes.joinToString("") { "%02x".format(it.toInt() and 255) }, transport.optString("session"))
        terminalError = null
        if (transport.getString("state") != "Ready") {
            if (transport.getString("state") !in setOf("Idle", "Disconnected", "Failed")) return@safely
            operation.connectRequest = enqueueConnect()
            if (operation.connectRequest == null) { terminalError = "接続を開始できません"; return@safely }
        }
        terminalOpen = operation
    } } }
    fun disconnect() { worker.execute { safely {
        terminalOpen = null; terminalError = null
        resetInput()
        if (handle == 0L || pendingDisconnect != null) return@safely
        val session = snapshot().getJSONObject("runtime").getJSONObject("transport").optString("session").takeIf { it.toLongOrNull() != null }
        val request = lastConnectRequest
        if (request != null) cancelledThrough = maxOf(cancelledThrough, request.toLong())
        if (request != null || session != null) {
            pendingDisconnect = PendingDisconnect(request, session)
            pumpDisconnect()
        }
    } } }
    private fun pumpDisconnect() {
        val operation = pendingDisconnect ?: return
        if (operation.request != null) return
        val request = requests.incrementAndGet().toString()
        val command = JSONObject().put("schema", 1).put("request_id", request)
        if (operation.connectRequest != null) command.put("type", "cancel_connect").put("connect_request", operation.connectRequest)
        else command.put("type", "disconnect").put("session", operation.session)
        when (val code = NativeBridge.nativeCommand(handle, command.toString())) {
            NativeBridge.ACCEPTED -> operation.request = request
            NativeBridge.BUSY -> Unit // queueが空いた次tickで同じ接続の取消だけを再送する。
            else -> { pendingDisconnect = null; terminalError = "切断を開始できません（$code）" }
        }
    }
    fun requestSnapshot() { worker.execute { safely { publish() } } }
    fun currentHandle(): Long = handle
    fun keyboardFocused(): Boolean = keyboardFocus != null
    fun publishCheck(value: JSONObject) { val copy = value.toString(); worker.execute { safely { store?.publishCheck(JSONObject(copy)) } } }
    fun publishAdapter(value: JSONObject) { val copy = value.toString(); worker.execute { safely { store?.publishAdapter(JSONObject(copy)) } } }
    fun detachSurface(target: JSONObject, runtime: Long = handle): Int {
        return NativeBridge.nativeCommand(runtime, JSONObject().put("schema", 1).put("request_id", requests.incrementAndGet().toString())
            .put("type", "surface_lost").put("target", target).toString())
    }
    fun ackPanel(plan: JSONObject, runtime: Long, success: Boolean = true): Int = NativeBridge.nativeCommand(runtime,
        JSONObject().put("schema", 1).put("request_id", requests.incrementAndGet().toString())
            .put("type", if (success) "adapter_applied" else "adapter_failed")
            .put("command_seq", plan.getString("command_seq")).put("target", plan.getJSONObject("target"))
            .put("action", plan.getString("action")).toString())
    fun resetInput(): Int {
        keyboardFocus = null
        inputTargets = emptyMap()
        if (handle == 0L) return NativeBridge.NOT_READY
        return NativeBridge.nativeCommand(handle, JSONObject().put("schema", 1)
            .put("request_id", requests.incrementAndGet().toString()).put("type", "reset_input").toString())
    }
    fun inputTarget(target: String): InputTarget? {
        if (!activityVisible.get() || xrState.get()!="FOCUSED") return null
        return inputTargets[targetIdentity(JSONObject(target))]?.takeIf { it.runtime==handle }
    }
    /** SDK callbackではimmutableなtargetと小DTOだけをenqueueする。座標をログへ出さない。 */
    fun sendPointer(target: InputTarget, source: Long, uv: List<Double>?, event: JSONObject): Boolean {
        if (source<=0 || inputTarget(target.target)!=target) return false
        val pointer=JSONObject().put("source",source.toString()).put("uv",uv?.let { JSONArray(it) } ?: JSONObject.NULL)
            .put("event",event).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
        val code=NativeBridge.nativeCommand(target.runtime,JSONObject().put("schema",1).put("type","pointer")
            .put("request_id",requests.incrementAndGet().toString()).put("target",JSONObject(target.target))
            .put("input_epoch",target.epoch).put("pointer",pointer).toString())
        if (code !in setOf(NativeBridge.ACCEPTED,NativeBridge.INVALID_ARGUMENT)) resetInput()
        return code!=NativeBridge.INVALID_ARGUMENT
    }
    fun focusTerminal() { worker.execute { safely {
        if (handle == 0L) return@safely
        val runtime = snapshot().getJSONObject("runtime")
        val plans = runtime.getJSONArray("panels")
        val plan = (0 until plans.length()).map { plans.getJSONObject(it) }
            .firstOrNull { it.getString("action") == "create" && it.getBoolean("applied") && it.isNull("error") } ?: return@safely
        NativeBridge.nativeCommand(handle, JSONObject().put("schema", 1).put("type", "focus_window")
            .put("request_id", requests.incrementAndGet().toString()).put("target", plan.getJSONObject("target"))
            .put("input_epoch", runtime.getLong("input_resets")).toString())
    } } }
    /** 入口の物理device判定はPhysicalKeyboard。ここは短いnative enqueueだけ。 */
    fun sendPhysicalKey(event: KeyEvent): Boolean {
        val focus = keyboardFocus ?: return false
        if (focus.runtime != handle || !activityVisible.get() || xrState.get() != "FOCUSED") return false
        val key = JSONObject().put("device", event.deviceId).put("scan_code", event.scanCode).put("key_code", event.keyCode)
            .put("action", if (event.action == KeyEvent.ACTION_DOWN) "pressed" else "released").put("repeat", event.repeatCount)
            .put("meta", event.metaState).put("time_ms", event.eventTime and 0xffffffffL)
        val code = NativeBridge.nativeCommand(focus.runtime, JSONObject().put("schema", 1).put("type", "key")
            .put("request_id", requests.incrementAndGet().toString()).put("target", JSONObject(focus.target))
            .put("input_epoch", focus.epoch).put("focus_token", focus.token).put("key", key).toString())
        if (code !in setOf(NativeBridge.ACCEPTED, NativeBridge.INVALID_ARGUMENT)) resetInput()
        return code != NativeBridge.INVALID_ARGUMENT
    }

    private fun send(type: String, key: String, value: Any) {
        safely {
            val current = handle
            if (current != 0L) {
                val command = JSONObject().put("schema", 1).put("request_id", requests.incrementAndGet().toString())
                    .put("type", type).put(key, value)
                val code = NativeBridge.nativeCommand(current, command.toString())
                if (code != NativeBridge.ACCEPTED) Log.w(TAG, "command_rejected type=$type code=$code")
            }
        }
    }
    private fun startRuntime() {
        handle = NativeBridge.nativeCreate(runtimeConfigJson())
        val deadline = SystemClock.elapsedRealtime() + 5000
        while (snapshot().getJSONObject("runtime").getString("state") == "Starting") {
            check(SystemClock.elapsedRealtime() < deadline) { "RUNTIME_START_TIMEOUT" }
            Thread.sleep(2)
        }
        if (scenes.get() > 0) send("scene_ready", "scene", scenes.get().toString())
        send("set_xr_state", "state", xrState.get())
        send("set_activity_state", "visible", activityVisible.get())
        tick()
    }
    fun runtimeConfigJson(): String = JSONObject().put("schema", 1).put("paths", checkNotNull(paths))
        .put("installation", checkNotNull(identity).installation).put("profiles", profiles).toString()
    private fun stopRuntime() {
        if (handle == 0L) return
        keyboardFocus = null
        inputTargets = emptyMap()
        val old = handle
        val started = SystemClock.elapsedRealtime()
        check(NativeBridge.nativeRequestStop(old) == NativeBridge.ACCEPTED)
        val deadline = SystemClock.elapsedRealtime() + 5000
        while (true) {
            val code = NativeBridge.nativeDestroy(old)
            if (code == NativeBridge.ACCEPTED) break
            check(code == NativeBridge.BUSY && SystemClock.elapsedRealtime() < deadline) { "RUNTIME_STOP_TIMEOUT" }
            Thread.sleep(2)
        }
        handle = 0
        terminalOpen = null; terminalError = null
        lastConnectRequest = null; pendingDisconnect = null; connectPending = false
        Log.i(TAG, "runtime_stopped handle=$old duration_ms=${SystemClock.elapsedRealtime() - started}")
    }
    /** 診断の排他的実行。固定caseの実装自体はdebug sourceSetだけに置く。 */
    fun withStoppedRuntime(action: (DiagnosticsStore) -> Unit, completed: () -> Unit) {
        worker.execute {
            try {
                stopRuntime()
                action(checkNotNull(store))
            } catch (failure: Exception) {
                Log.e(TAG, "exclusive_failure class=${failure.javaClass.simpleName}")
            } finally {
                safely { if (handle == 0L) startRuntime() }
                ui.post(completed)
            }
        }
    }
    private fun snapshot(): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle))
    private fun publish() { if (handle != 0L) store?.publishRuntime(snapshot()) }
    private fun tick() {
        if (handle == 0L) return
        val events = JSONObject(NativeBridge.nativePoll(handle, 64))
        check(events.getInt("code") == NativeBridge.ACCEPTED)
        val values = events.getJSONArray("events")
        for (index in 0 until values.length()) {
            val event = values.getJSONObject(index)
            if (event.optString("type") == "command_complete") {
                BuildHooks.commandComplete(event.getString("request_id"), event.getString("code"))
                if (event.getString("request_id") == lastConnectRequest) {
                    connectPending = false
                    if (event.getString("code") != "ACCEPTED") lastConnectRequest = null
                }
                pendingDisconnect?.let { operation ->
                    if (event.getString("request_id") == operation.request) {
                        if (event.getString("code") == "BUSY") operation.request = null
                        else {
                            pendingDisconnect = null
                            if (event.getString("code") !in setOf("ACCEPTED", "STALE")) terminalError = "切断できません（${event.getString("code")}）"
                        }
                    }
                }
                terminalOpen?.let { operation ->
                    val request = event.getString("request_id")
                    if (request == operation.connectRequest || request == operation.startRequest) {
                        if (event.getString("code") != "ACCEPTED") {
                            terminalError = "開始できません（${event.getString("code")}）"; terminalOpen = null
                        } else if (request == operation.connectRequest) operation.connectAccepted = true
                        else terminalOpen = null
                    }
                }
            }
            if (event.optString("type") == "credential_required" && event.optString("profile_id") == "termux-local" &&
                event.getString("request_id").toLong() > cancelledThrough) {
                val code = checkNotNull(credentials).provide(handle, event.getString("session").toLong(), event.getString("request_id").toLong())
                if (code != NativeBridge.ACCEPTED) Log.w(TAG, "credential_rejected code=$code")
            }
        }
        pumpDisconnect()
        val snapshot = snapshot()
        val runtime = snapshot.getJSONObject("runtime")
        val focusedWindow = runtime.optJSONObject("app")?.optJSONObject("input")?.optString("focus_window")
        val plans = runtime.getJSONArray("panels")
        val renderTargets=runtime.getJSONObject("render").getJSONArray("targets")
        inputTargets=if (runtime.getString("state")=="Ready" && runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED")
            (0 until plans.length()).map { plans.getJSONObject(it) }.filter { plan ->
                plan.optString("action")=="create" && plan.optBoolean("applied") && plan.isNull("error") &&
                    (0 until renderTargets.length()).any { index ->
                        val rendered=renderTargets.getJSONObject(index)
                        targetIdentity(rendered.getJSONObject("key"))==targetIdentity(plan.getJSONObject("target")) && rendered.getLong("frames")>0
                    }
            }.associate { plan ->
                val target=plan.getJSONObject("target"); val identity=targetIdentity(target)
                identity to InputTarget(handle,runtime.getLong("input_resets"),identity,target.toString())
            } else emptyMap()
        val focused = (0 until plans.length()).map { plans.getJSONObject(it) }.firstOrNull {
            it.optString("action") == "create" && it.optBoolean("applied") && it.isNull("error") &&
                it.getJSONObject("target").getString("window") == focusedWindow &&
                it.getJSONObject("target").optString("session") == runtime.optJSONObject("app")?.optString("session")
        }
        keyboardFocus = if (runtime.getString("state") == "Ready" && runtime.getBoolean("activity_visible") &&
            runtime.getString("xr_state") == "FOCUSED" && focused != null)
            KeyboardFocus(handle, runtime.getLong("input_resets"), focused.getJSONObject("target").toString(), runtime.getJSONObject("app").getJSONObject("input").getString("focus_token")) else null
        val keyboardLabel = if (keyboardFocus == null) "操作パネルへ入力" else "ターミナルへ入力"
        ui.post { keyboardSummary.value = keyboardLabel }
        val transport = snapshot.getJSONObject("runtime").getJSONObject("transport")
        terminalOpen?.let { operation ->
            val ready = transport.getString("state") == "Ready" && (operation.connectRequest == null ||
                operation.connectAccepted && transport.optString("session") != operation.previousSession)
            if (ready && operation.startRequest == null) {
                val request = requests.incrementAndGet().toString()
                val command = JSONObject().put("schema", 1).put("type", "start_app").put("request_id", request)
                    .put("session", transport.getString("session")).put("nonce", operation.nonce).put("mode", "foot")
                val code = NativeBridge.nativeCommand(handle, command.toString())
                if (code == NativeBridge.ACCEPTED) operation.startRequest = request
                else { terminalError = "ターミナルを開始できません（$code）"; terminalOpen = null }
            } else if (SystemClock.elapsedRealtime() >= operation.deadline ||
                transport.getString("state") == "Failed" && transport.optString("session") != operation.previousSession) {
                terminalError = "接続を開始できません"; terminalOpen = null
            }
        }
        val phase = when (val value = transport.getString("state")) {
            "Recovering" -> "前回の終了を確認中"
            "CleanupPending" -> "前回の終了を確認できません"
            else -> value
        }
        val pending = transport.optJSONArray("pending_cleanup")?.length() ?: 0
        val appError = snapshot.getJSONObject("runtime").optJSONObject("app")?.optString("error", "")?.takeIf { it.isNotEmpty() }
        val panelError = snapshot.getJSONObject("runtime").optJSONArray("panels")?.let { plans ->
            (0 until plans.length()).map { plans.getJSONObject(it) }.firstOrNull { !it.isNull("error") }?.getString("error")
        }
        val error = terminalError ?: panelError ?: appError
        val ssh = phase + (if (pending > 0) " · 未確認の回収 ${pending}件" else "") +
            (if (transport.isNull("error")) "" else " · ${transport.getString("error")}") + (error?.let { " · $it" } ?: "")
        if (ssh != sshState) { sshState = ssh; ui.post { connectionSummary.value = ssh } }
        val next = snapshot.getJSONObject("runtime").getString("state")
        val now = SystemClock.elapsedRealtime()
        if (next != state) {
            state = next
            Log.i(TAG, "runtime_state=$next")
            ui.post { summary.value = next }
        }
        if (now - lastWrite >= 1000) { store?.publishRuntime(snapshot); lastWrite = now }
    }
    private fun safely(action: () -> Unit) {
        try { action() }
        catch (failure: Exception) {
            Log.e(TAG, "host_failure class=${failure.javaClass.simpleName}")
            ui.post { summary.value = "Failed" }
            if (failure is CredentialStore.Failure) {
                Log.e(TAG, "credential_failure code=${failure.code}")
                ui.post { credentialSummary.value = "鍵の復旧・再pairingが必要（${failure.code}）" }
            }
        }
    }
    private const val TAG = "EidolonHost"
}
