package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.KeyEvent
import androidx.compose.runtime.mutableStateOf
import java.util.concurrent.ConcurrentHashMap
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
    private var application: Context? = null
    private var store: DiagnosticsStore? = null
    private var paths: JSONObject? = null
    private var preferencesDir: String? = null
    private var selectedWindow: Pair<String,String>? = null
    private var lastFocusedWindow: String? = null
    private val editing = AtomicBoolean(false)
    private val poseCompletions = ConcurrentHashMap<String,String>()
    private val lastStop = AtomicReference<String?>(null)
    fun stopReceipt(): JSONObject? = lastStop.get()?.let { JSONObject(it) }
    val layoutEditing = mutableStateOf(false)
    val workloadObservation = mutableStateOf(true)
    val workspaceSummary = mutableStateOf("配置設定を確認中")
    data class ThemeUi(val selection: String = "off", val reduced: Boolean = false, val imported: Boolean = false,
        val loading: Boolean = false, val message: String = "装飾なし")
    val themeUi = mutableStateOf(ThemeUi())
    private val themeRequests = linkedMapOf<String, (String) -> Unit>()
    @Volatile private var themeImportToken: String? = null
    private var themeImportMessage: String? = null
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
    val inputStatus = mutableStateOf(InputStatus("PREPARING","画面を準備中"))
    private val keyboardCount = AtomicReference<Int?>(null)
    val deviceMetrics = mutableStateOf(DeviceMetricsSnapshot())
    private val latestMetrics = AtomicReference(DeviceMetricsSnapshot())
    fun setKeyboardCount(value: Int?) { keyboardCount.set(value) }
    fun publishDeviceMetrics(value: DeviceMetricsSnapshot) { latestMetrics.set(value);deviceMetrics.value=value }
    private val inputIntents=AtomicLong(0)
    private data class PendingInputFocus(val target: String, val runtime: Long, val ticket: Long, val deadline: Long,
        var resetRequest: String? = null, var resetCompleted: Boolean = false, var focusRequest: String? = null)
    private var pendingInputFocus: PendingInputFocus? = null
    val layoutSummary = mutableStateOf("ターミナルを開くとサイズを変更できます")
    val layoutAvailable = mutableStateOf(false)
    data class WindowChoice(val session: String, val scene: String, val window: String, val slot: Int?, val ambiguous: Boolean=false)
    val windowChoices = mutableStateOf<List<WindowChoice>>(emptyList())
    private data class TerminalAdding(val session: String, val windows: Set<String>, val request: String,
        val deadline: Long = SystemClock.elapsedRealtime()+8000)
    private var terminalAdding: TerminalAdding? = null
    private var lastWrite = 0L
    private var state = "Starting"
    private var sshState = "未接続"
    private data class TerminalOpen(val nonce: String, var previousSession: String,
        var connectRequest: String? = null, var connectAccepted: Boolean = false,
        var startRequest: String? = null, val deadline: Long = SystemClock.elapsedRealtime()+45000,
        var startupAttempted: Boolean = false, var startupWaiting: Boolean = false,
        var startupDeadline: Long = 0, var retryReady: Boolean = false)
    private var terminalOpen: TerminalOpen? = null // workerだけで操作し、二重clickをcoalesceする。
    private fun clearTerminalOpen() { terminalOpen?.let {TermuxStarter.cancel(it.nonce)};terminalOpen=null }
    val termuxHelp = mutableStateOf(false)
    val terminalBusy = mutableStateOf(false)
    private var termuxRequests=0L
    private var termuxSuccesses=0L
    private var termuxFailures=0L
    private var terminalError: String? = null
    val summary = mutableStateOf("Starting")
    val credentialSummary = mutableStateOf("鍵を確認中")
    val connectionSummary = mutableStateOf("未接続")
    data class ConnectionUi(val publicKey:String="",val username:String="",val hostKey:String="",val savedToken:String="",
        val canSave:Boolean=false,val message:String="設定を読み込み中")
    val connectionUi=mutableStateOf(ConnectionUi())
    private var connectionEditable=false

    private fun refreshConnection(message:String) {
        val profile=profiles.optJSONObject(0)?.let {ConnectionProfile.parse(it.getString("username"),it.getString("host_key"))}
        val publicKey=identity?.publicKey ?: ""
        val editable=connectionEditable
        ui.post {connectionUi.value=ConnectionUi(publicKey,profile?.username ?: "",profile?.hostKey ?: "",profile?.token ?: "",editable,message)}
    }
    private fun canChangeConnection(runtime:JSONObject):Boolean {
        val transport=runtime.getJSONObject("transport")
        return runtime.getString("state")=="Ready" && !connectPending && pendingDisconnect==null && terminalOpen==null &&
            transport.getString("state") in setOf("Idle","Disconnected","Failed") && transport.getInt("owned_connections")==0 &&
            transport.getJSONArray("pending_cleanup").length()==0 && (runtime.optJSONObject("app")?.optString("state") ?: "stopped")=="stopped"
    }
    fun saveConnection(username:String,hostKey:String,expected:String,confirmed:Boolean) {
        worker.execute {
            try {
                check(handle!=0L && canChangeConnection(snapshot().getJSONObject("runtime"))) {"CONNECTION_BUSY"}
                val previous=profiles.optJSONObject(0)?.let {ConnectionProfile.parse(it.getString("username"),it.getString("host_key"))}
                ConnectionProfile.replacement(previous,expected,username,hostKey,confirmed)
                stopRuntime() // SDKの回収を完了してからpinを変更。停止失敗なら保存しない。
                val saved=checkNotNull(credentials).saveProfile(checkNotNull(identity),username,hostKey,expected,confirmed)
                profiles=JSONArray().put(saved)
                refreshConnection("保存しました。Termuxでsshdを起動し「ターミナルを開く」を押してください。")
            } catch(_:Exception) {
                refreshConnection("保存できませんでした。切断状態と公開鍵を確認し、設定を開き直してください。")
            } finally {safely {if(handle==0L) startRuntime()}}
        }
    }

    init { worker.scheduleAtFixedRate({ safely { tick() } }, 0, 16, TimeUnit.MILLISECONDS) }

    fun initialize(context: Context) {
        val application = context.applicationContext
        worker.execute { safely {
            this.application=application
            if (store == null) store = DiagnosticsStore(application)
            if (preferencesDir == null) preferencesDir = application.noBackupFilesDir.absolutePath
            if (paths == null) paths = JSONObject().put("native_library_dir", application.applicationInfo.nativeLibraryDir).put("files_dir", application.filesDir.absolutePath)
            if (handle == 0L && NativeBridge.status.ready) {
                if (credentials == null) credentials = CredentialStore(application)
                identity = checkNotNull(credentials).ensureIdentity()
                profiles = JSONArray()
                checkNotNull(credentials).profile(checkNotNull(identity))?.let { profiles.put(it) }
                refreshConnection(if(profiles.length()==0) "初回は公開鍵のペアリングを行ってください。" else "保存済みの接続設定を読み込みました。")
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
    fun currentSceneEpoch(): Long = scenes.get()
    fun setXrState(value: String, expectedScene: Long? = null): Boolean {
        if(expectedScene!=null && expectedScene!=scenes.get()) return false
        if(value!="FOCUSED") {keyboardFocus=null;inputTargets=emptyMap()}
        xrState.set(value);send("set_xr_state","state",value);return true
    }
    fun setActivityVisible(value: Boolean, expectedScene: Long? = null): Boolean {
        if(expectedScene!=null && expectedScene!=scenes.get()) return false
        if(!value) {keyboardFocus=null;inputTargets=emptyMap()}
        activityVisible.set(value);send("set_activity_state","visible",value);return true
    }
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
        if (app != null && app.optString("session") == transport.optString("session") && app.isNull("error") && app.optString("state") != "stopped") {
            val windows=app.getJSONArray("windows")
            if(app.optString("mode")!="foot" || app.optString("state")!="running" || windows.length()>=8 || terminalAdding!=null) return@safely
            val request=requests.incrementAndGet().toString()
            val code=NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","open_terminal").put("request_id",request).put("session",app.getString("session")).toString())
            if(code==NativeBridge.ACCEPTED) {
                terminalError=null
                terminalAdding=TerminalAdding(app.getString("session"),(0 until windows.length()).map { windows.getJSONObject(it).getString("window") }.toSet(),request)
            } else terminalError="追加できません（$code）"
            return@safely
        }
        // Idle等で残るjournalは、新しいSSH接続のauditで照合する。
        // 未確認entryの存在だけで接続を拒むと、正常終了済みのreceiptも確認できなくなる。
        if (transport.getJSONArray("pending_cleanup").length()!=0 && transport.getString("state")=="Ready") {
            terminalError="前回の終了を確認してください";return@safely
        }
        val bytes = ByteArray(8).also { java.security.SecureRandom().nextBytes(it) }
        val operation = TerminalOpen(bytes.joinToString("") { "%02x".format(it.toInt() and 255) }, transport.optString("session"))
        terminalError = null
        ui.post {termuxHelp.value=false}
        if (transport.getString("state") != "Ready") {
            if (transport.getString("state") !in setOf("Idle", "Disconnected", "Failed")) return@safely
            operation.connectRequest = enqueueConnect()
            if (operation.connectRequest == null) { terminalError = "接続を開始できません"; return@safely }
        }
        terminalOpen = operation
    } } }
    fun disconnect() { worker.execute { safely {
        pendingInputFocus=null
        clearTerminalOpen(); terminalAdding=null; terminalError = null
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
    fun showWindow(choice: WindowChoice, slot: Int) { windowCommand(choice,"show_window",slot) }
    fun closeWindow(choice: WindowChoice) { windowCommand(choice,"close_window",null) }
    private fun windowCommand(choice: WindowChoice, type: String, slot: Int?) { worker.execute { safely {
        if(handle==0L) return@safely
        resetInput()
        val command=JSONObject().put("schema",1).put("request_id",requests.incrementAndGet().toString()).put("type",type)
            .put("session",choice.session).put("scene",choice.scene).put("window",choice.window)
        if(slot!=null) command.put("slot",slot)
        NativeBridge.nativeCommand(handle,command.toString())
    } } }
    fun requestSnapshot() { worker.execute { safely { publish() } } }
    fun currentHandle(): Long = handle
    fun keyboardFocused(): Boolean = keyboardFocus != null
    fun keyboardFocusMatches(target: JSONObject, token: String): Boolean {
        val focus=keyboardFocus ?: return false
        return focus.runtime==handle && focus.token==token && targetIdentity(JSONObject(focus.target))==targetIdentity(target)
    }
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
        inputIntents.incrementAndGet()
        if (BuildConfig.DEBUG) Log.i(TAG,"input_reset intent=${inputIntents.get()}")
        keyboardFocus = null
        inputTargets = emptyMap()
        if (handle == 0L) return NativeBridge.NOT_READY
        return enqueueInputReset(requests.incrementAndGet().toString())
    }
    private fun enqueueInputReset(request: String): Int = NativeBridge.nativeCommand(handle,
        JSONObject().put("schema",1).put("request_id",request).put("type","reset_input").toString())
    fun inputTarget(target: String): InputTarget? {
        if (editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED") return null
        return inputTargets[targetIdentity(JSONObject(target))]?.takeIf { it.runtime==handle }
    }
    private fun selectedTarget(runtime: JSONObject): JSONObject? {
        val plans=runtime.getJSONArray("panels")
        val focused=runtime.optJSONObject("app")?.optJSONObject("input")?.optString("focus_window")
        val live=(0 until plans.length()).map { plans.getJSONObject(it) }.filter { it.optString("action")=="create" && it.optBoolean("applied") && it.isNull("error") }
        return (live.firstOrNull { selectedWindow == (it.getJSONObject("target").optString("session") to it.getJSONObject("target").optString("window")) } ?: live.firstOrNull { it.getJSONObject("target").optString("window")==focused } ?: live.firstOrNull())?.getJSONObject("target")
    }
    fun isLayoutEditing(): Boolean = editing.get()
    fun setLayoutEditing(value: Boolean) {
        editing.set(value); resetInput(); ui.post { layoutEditing.value=value }
    }
    fun finishLayoutEditing() { worker.execute { safely {
        if(handle==0L) return@safely
        val runtime=snapshot().getJSONObject("runtime")
        val target=focusTarget(runtime,requireApplied=false)
        editing.set(false);ui.post {layoutEditing.value=false}
        keyboardFocus=null;inputTargets=emptyMap()
        val ticket=inputIntents.incrementAndGet()
        pendingInputFocus=target?.let { PendingInputFocus(it.toString(),handle,ticket,SystemClock.elapsedRealtime()+2000) }
        if (BuildConfig.DEBUG) Log.i(TAG,"focus_begin editing target=${target?.let {targetIdentity(it)}} ticket=$ticket")
        if(target==null) resetInput()
    } } }
    fun selectWindow(choice: WindowChoice) { worker.execute { safely {
        selectedWindow=choice.session to choice.window; resetInput()
    } } }
    fun setBackground(mode: String) { if(mode in setOf("passthrough","virtual")) worker.execute { send("set_background","background",mode) } }
    fun setKeyboardLayout(layout: String) { if(layout in setOf("us","jis")) worker.execute { resetInput();send("set_keyboard_layout","layout",layout) } }
    private fun themeCommand(command: JSONObject, done: (String) -> Unit = {}) {
        if (handle == 0L || themeRequests.size >= 8) { done("NOT_READY"); return }
        val request = requests.incrementAndGet().toString()
        command.put("schema", 1).put("request_id", request)
        val code = NativeBridge.nativeCommand(handle, command.toString())
        if (code == NativeBridge.ACCEPTED) themeRequests[request] = done else done("REJECTED")
    }
    fun setWorkloadObservation(enabled: Boolean) {
        worker.execute { safely { themeCommand(JSONObject().put("type","set_workload_observation").put("enabled",enabled)) } }
    }
    fun setTheme(selection: String, reduced: Boolean = themeUi.value.reduced) {
        if (selection !in setOf("off", "builtin", "imported")) return
        worker.execute { safely {
            themeImportToken = null; themeImportMessage = null
            themeCommand(JSONObject().put("type", "set_theme").put("selection", selection).put("reduced_motion", reduced)) {
                if (it != "ACCEPTED") themeImportMessage = "テーマの変更を開始できませんでした"
            }
        } }
    }
    fun beginThemeImport(ready: (String) -> Unit) { worker.execute { safely {
        val token = java.util.UUID.randomUUID().toString().replace("-", "")
        themeImportToken = token; themeImportMessage = null
        themeCommand(JSONObject().put("type", "begin_theme_import").put("token", token)) { code ->
            if (themeImportToken == token) {
                if (code == "ACCEPTED") ui.post { ready(token) }
                else { themeImportToken = null; themeImportMessage = "テーマの読み込みを開始できませんでした" }
            }
        }
    } } }
    fun cancelThemeImport(token: String) { worker.execute { safely {
        if (themeImportToken == token) {
            themeImportToken = null; themeImportMessage = null
            themeCommand(JSONObject().put("type", "cancel_theme_import").put("token", token))
        }
    } } }
    fun readTheme(context: Context, uri: android.net.Uri, token: String) {
        ThemeImporter.read(context, uri, token, { themeImportToken == token }) { file, error ->
            worker.execute { safely {
                if (themeImportToken != token || handle == 0L) { file?.delete(); return@safely }
                if (file == null) {
                    themeImportToken = null; themeImportMessage = error
                    themeCommand(JSONObject().put("type", "cancel_theme_import").put("token", token))
                } else {
                    themeCommand(JSONObject().put("type", "apply_theme_import").put("token", token)) { code ->
                        if (code != "ACCEPTED") {
                            file.delete()
                            if (themeImportToken == token) { themeImportToken = null; themeImportMessage = "読み込みを適用できませんでした" }
                        }
                    }
                }
            } }
        }
    }
    fun submitPose(target: JSONObject, pose: JSONObject, runtime: Long): String? {
        if(runtime!=handle || poseCompletions.size>=16 || !PanelPoseCodec.valid(pose)) return null
        val request=requests.incrementAndGet().toString();poseCompletions[request]="pending"
        val code=NativeBridge.nativeCommand(runtime,JSONObject().put("schema",1).put("type","set_pose").put("request_id",request).put("target",target).put("pose",pose).toString())
        if(code!=NativeBridge.ACCEPTED) {poseCompletions.remove(request);return null}
        return request
    }
    fun poseCompletion(request: String): String? {
        val code=poseCompletions[request] ?: return null
        if(code!="pending") poseCompletions.remove(request)
        return code
    }
    fun forgetPose(request: String) { poseCompletions.remove(request) }
    fun ackPose(target: JSONObject, command: String?, pose: JSONObject, runtime: Long): Int = NativeBridge.nativeCommand(runtime,
        JSONObject().put("schema",1).put("type","pose_applied").put("request_id",requests.incrementAndGet().toString()).put("target",target)
            .put("pose_command",command ?: JSONObject.NULL).put("pose",pose).toString())
    fun ackBackground(scene: Long, command: String?, background: String, runtime: Long): Int = NativeBridge.nativeCommand(runtime,
        JSONObject().put("schema",1).put("type","background_applied").put("request_id",requests.incrementAndGet().toString()).put("scene",scene.toString())
            .put("background_command",command ?: JSONObject.NULL).put("background",background).toString())
    fun nudgePose(x: Double=0.0,y: Double=0.0,z: Double=0.0,yaw: Double=0.0) { worker.execute { safely {
        if(handle==0L) return@safely
        val runtime=snapshot().getJSONObject("runtime");val target=selectedTarget(runtime) ?: return@safely
        val windows=runtime.getJSONObject("workspace").getJSONArray("windows")
        val window=(0 until windows.length()).map {windows.getJSONObject(it)}.firstOrNull {it.getString("window")==target.getString("window") && it.getString("session")==target.getString("session")} ?: return@safely
        val pose=PanelPoseCodec.nudge(window.getJSONObject("placement").getJSONObject("pose"),x,y,z,yaw)
        if(PanelPoseCodec.valid(pose)) NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","set_pose").put("request_id",requests.incrementAndGet().toString()).put("target",target).put("pose",pose).toString())
    } } }
    fun resizeTerminal(width: Int, height: Int) { worker.execute { safely {
        if(handle==0L) return@safely
        val target=selectedTarget(snapshot().getJSONObject("runtime")) ?: return@safely
        NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","resize_logical")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target).put("width",width).put("height",height).toString())
    } } }
    fun setPhysicalSize(width: Double, height: Double) { worker.execute { safely {
        if(handle==0L) return@safely
        val target=selectedTarget(snapshot().getJSONObject("runtime")) ?: return@safely
        NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","set_physical_size")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target)
            .put("size",JSONObject().put("width_m",width).put("height_m",height)).toString())
    } } }
    fun ackPhysicalSize(target: JSONObject, command: String?, size: JSONObject, runtime: Long): Int =
        NativeBridge.nativeCommand(runtime,JSONObject().put("schema",1).put("type","physical_size_applied")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target)
            .put("size_command",command ?: JSONObject.NULL).put("size",size).toString())
    /** SDK callbackではimmutableなtargetと小DTOだけをenqueueする。座標をログへ出さない。 */
    fun sendPointer(target: InputTarget, source: Long, uv: List<Double>?, event: JSONObject): Boolean {
        if (source<=0 || inputTarget(target.target)!=target) return false
        if(event.optString("type")=="buttons" && event.optInt("state") and 1 != 0) inputIntents.incrementAndGet()
        val pointer=JSONObject().put("source",source.toString()).put("uv",uv?.let { JSONArray(it) } ?: JSONObject.NULL)
            .put("event",event).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
        val code=NativeBridge.nativeCommand(target.runtime,JSONObject().put("schema",1).put("type","pointer")
            .put("request_id",requests.incrementAndGet().toString()).put("target",JSONObject(target.target))
            .put("input_epoch",target.epoch).put("pointer",pointer).toString())
        if (code !in setOf(NativeBridge.ACCEPTED,NativeBridge.INVALID_ARGUMENT)) resetInput()
        return code!=NativeBridge.INVALID_ARGUMENT
    }
    private fun focusTarget(runtime: JSONObject, choice: WindowChoice? = null, requireApplied: Boolean = true): JSONObject? {
        val plans=runtime.getJSONArray("panels")
        val live=(0 until plans.length()).map {plans.getJSONObject(it)}
            .filter {it.optString("action")=="create" && (!requireApplied || it.optBoolean("applied")) && it.isNull("error")}
            .map {it.getJSONObject("target")}
        val wanted=choice?.let {it.session to it.window} ?: selectedWindow
        if(wanted!=null) {
            val match=live.firstOrNull {(it.getString("session") to it.getString("window"))==wanted && (choice==null || it.getString("scene")==choice.scene)}
            if(match!=null || choice!=null) return match
        }
        return live.singleOrNull()
    }
    fun focusTerminal(choice: WindowChoice? = null) { worker.execute { safely {
        pendingInputFocus=null
        if (handle == 0L) return@safely
        if(editing.get()) return@safely
        val runtime = snapshot().getJSONObject("runtime")
        val target=focusTarget(runtime,choice,requireApplied=false) ?: return@safely
        selectedWindow=target.getString("session") to target.getString("window")
        keyboardFocus=null;inputTargets=emptyMap()
        pendingInputFocus=PendingInputFocus(target.toString(),handle,inputIntents.incrementAndGet(),SystemClock.elapsedRealtime()+2000)
        if (BuildConfig.DEBUG) Log.i(TAG,"focus_begin explicit target=${targetIdentity(target)} ticket=${inputIntents.get()}")
    } } }
    /** 入口の物理device判定はPhysicalKeyboard。ここは短いnative enqueueだけ。 */
    fun sendPhysicalKey(event: KeyEvent): Boolean {
        val focus = keyboardFocus ?: return false
        if (editing.get() || focus.runtime != handle || !activityVisible.get() || xrState.get() != "FOCUSED") return false
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
    fun runtimeConfigJson(): String = BuildHooks.runtimeConfiguration(JSONObject().put("schema", 1).put("paths", checkNotNull(paths))
        .put("installation", checkNotNull(identity).installation).put("profiles", profiles).put("preferences_dir",checkNotNull(preferencesDir)).toString())
    fun sdkBinding(target: JSONObject, runtime: Long, owned: Boolean): Int = NativeBridge.nativeCommand(runtime,
        JSONObject().put("schema",1).put("type",if(owned) "register_sdk_binding" else "release_sdk_binding")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target).toString())
    private fun stopRuntime() {
        if (handle == 0L) return
        keyboardFocus = null
        inputTargets = emptyMap()
        val old = handle
        themeImportToken = null; themeRequests.clear(); themeImportMessage = null
        val started = SystemClock.elapsedRealtime()
        lastStop.set(null)
        check(NativeBridge.nativeRequestStop(old) == NativeBridge.ACCEPTED)
        val accepted = SystemClock.elapsedRealtime() - started
        val deadline = started + 10000
        while (true) {
            val stopped = JSONObject(NativeBridge.nativeSnapshot(old))
            val done = stopped.getJSONObject("runtime").getJSONObject("shutdown").getBoolean("complete")
            val code = if(done) NativeBridge.nativeDestroy(old) else NativeBridge.BUSY
            if (code == NativeBridge.ACCEPTED) {
                lastStop.set(JSONObject().put("handle",old.toString()).put("accept_ms",accepted)
                    .put("stop_ms",SystemClock.elapsedRealtime()-started).put("snapshot",stopped).toString())
                break
            }
            if (code != NativeBridge.BUSY || SystemClock.elapsedRealtime() >= deadline) {
                // 回収できなかったownerを残したまま新runtimeへ置き換えない。
                store?.publishRuntime(JSONObject(NativeBridge.nativeSnapshot(old)))
                ui.post { summary.value = "停止を確認できません。アプリを再起動してください" }
                error("RUNTIME_STOP_TIMEOUT")
            }
            Thread.sleep(2)
        }
        handle = 0
        poseCompletions.clear();selectedWindow=null;lastFocusedWindow=null;editing.set(false);ui.post { layoutEditing.value=false }
        clearTerminalOpen(); terminalAdding = null; terminalError = null
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
                pendingInputFocus?.let { pending ->
                    if (BuildConfig.DEBUG && event.getString("request_id") in listOf(pending.resetRequest,pending.focusRequest)) Log.i(TAG,"focus_complete request=${event.getString("request_id")} code=${event.getString("code")}")
                    if(event.getString("request_id")==pending.focusRequest) {
                        when(event.getString("code")) {
                            "STALE", "NOT_READY", "BUSY" -> pending.focusRequest=null
                            else -> pendingInputFocus=null
                        }
                    }
                    if(event.getString("request_id")==pending.resetRequest) {
                        when(event.getString("code")) {
                            "ACCEPTED" -> pending.resetCompleted=true
                            "BUSY" -> pending.resetRequest=null
                            else -> pendingInputFocus=null
                        }
                    }
                }
                themeRequests.remove(event.getString("request_id"))?.invoke(event.getString("code"))
                BuildHooks.commandComplete(event.getString("request_id"), event.getString("code"))
                poseCompletions.computeIfPresent(event.getString("request_id")) { _, _ -> event.getString("code") }
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
                terminalAdding?.let { operation ->
                    if(event.getString("request_id")==operation.request && event.getString("code")!="ACCEPTED") {
                        terminalError="追加できません（${event.getString("code")}）"; terminalAdding=null
                    }
                }
                terminalOpen?.let { operation ->
                    val request = event.getString("request_id")
                    if (request == operation.connectRequest || request == operation.startRequest) {
                        if (event.getString("code") != "ACCEPTED") {
                            terminalError = "開始できません（${event.getString("code")}）"; clearTerminalOpen()
                        } else if (request == operation.connectRequest) operation.connectAccepted = true
                        else clearTerminalOpen()
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
        pendingInputFocus?.let { request ->
            val target=JSONObject(request.target)
            val plans=runtime.getJSONArray("panels")
            val current=(0 until plans.length()).map {plans.getJSONObject(it)}.firstOrNull {p ->
                p.optString("action")=="create" && p.isNull("error") && targetIdentity(p.getJSONObject("target"))==targetIdentity(target)
            }?.getJSONObject("target")
            if(SystemClock.elapsedRealtime()>request.deadline || editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED" ||
                request.runtime!=handle || request.ticket!=inputIntents.get() ||
                current==null || targetIdentity(current)!=targetIdentity(target)) {
                if (BuildConfig.DEBUG) Log.i(TAG,"focus_cancel expired=${SystemClock.elapsedRealtime()>request.deadline} editing=${editing.get()} visible=${activityVisible.get()} xr=${xrState.get()} runtime=${request.runtime==handle} ticket=${request.ticket==inputIntents.get()} target=${current!=null}")
                pendingInputFocus=null
            }
            else if(request.resetRequest==null) {
                val reset=requests.incrementAndGet().toString()
                when(enqueueInputReset(reset)) {
                    NativeBridge.ACCEPTED -> {request.resetRequest=reset;if (BuildConfig.DEBUG) Log.i(TAG,"focus_reset request=$reset")}
                    NativeBridge.BUSY -> Unit
                    else -> pendingInputFocus=null
                }
            } else if(request.resetCompleted && request.focusRequest==null) {
                val panels=runtime.getJSONArray("panels")
                val frames=runtime.getJSONObject("render").getJSONArray("targets")
                val ready=(0 until panels.length()).any {i -> panels.getJSONObject(i).let {p ->
                    targetIdentity(p.getJSONObject("target"))==targetIdentity(target) && p.optBoolean("applied") && p.isNull("error")
                }} && (0 until frames.length()).any {i -> frames.getJSONObject(i).let {f ->
                    targetIdentity(f.getJSONObject("key"))==targetIdentity(target) && f.getLong("frames")>0
                }}
                if(ready) {
                    val focusRequest=requests.incrementAndGet().toString()
                    val code=NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","focus_window")
                        .put("request_id",focusRequest).put("target",target).put("input_epoch",runtime.getLong("input_resets")).toString())
                    when(code) {
                        NativeBridge.ACCEPTED -> {request.focusRequest=focusRequest;if (BuildConfig.DEBUG) Log.i(TAG,"focus_enqueue request=$focusRequest epoch=${runtime.getLong("input_resets")}")}
                        NativeBridge.BUSY -> Unit
                        else -> pendingInputFocus=null
                    }
                }
            }
        }
        val focusedWindow = runtime.optJSONObject("app")?.optJSONObject("input")?.optString("focus_window")?.takeIf { it.toLongOrNull()!=null }
        if(focusedWindow!=null && (focusedWindow!=lastFocusedWindow || selectedWindow?.first!=runtime.getJSONObject("app").getString("session"))) selectedWindow=runtime.getJSONObject("app").getString("session") to focusedWindow
        lastFocusedWindow=focusedWindow
        val plans = runtime.getJSONArray("panels")
        val renderTargets=runtime.getJSONObject("render").getJSONArray("targets")
        inputTargets=if (!editing.get() && runtime.getString("state")=="Ready" && runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED")
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
        keyboardFocus = if (!editing.get() && runtime.getString("state") == "Ready" && runtime.getBoolean("activity_visible") &&
            runtime.getString("xr_state") == "FOCUSED" && focused != null)
            KeyboardFocus(handle, runtime.getLong("input_resets"), focused.getJSONObject("target").toString(), runtime.getJSONObject("app").getJSONObject("input").getString("focus_token")) else null
        val keyboardLabel = if (keyboardFocus == null) "操作パネルへ入力" else "ターミナルへ入力"
        ui.post { keyboardSummary.value = keyboardLabel }
        val inputConditions=InputConditions(runtime.getBoolean("activity_visible"),runtime.getString("xr_state")=="FOCUSED",
            runtime.getString("state")=="Ready",if(terminalOpen?.startupWaiting==true) "StartingTermux" else runtime.getJSONObject("transport").getString("state"),editing.get(),keyboardCount.get(),
            (runtime.optJSONObject("app")?.optJSONArray("windows")?.length() ?: 0)>0,inputTargets.isNotEmpty(),
            focusedWindow.takeIf {keyboardFocus!=null})
        val status=InputStatus.from(inputConditions)
        ui.post {inputStatus.value=status}
        store?.updateInterface(JSONObject().put("input_reason",status.reason).put("input_action",status.action.name)
            .put("editing",editing.get()).put("keyboard_count",keyboardCount.get() ?: JSONObject.NULL)
            .put("focus_window",focusedWindow ?: JSONObject.NULL).put("device",latestMetrics.get().json())
            .put("termux_start",JSONObject().put("requests",termuxRequests).put("successes",termuxSuccesses).put("failures",termuxFailures)))
        val workspace=runtime.getJSONObject("workspace");val storage=workspace.getJSONObject("storage")
        val observing=workspace.getBoolean("workload_observation")
        ui.post { workloadObservation.value=observing }
        runtime.optJSONObject("theme")?.let { theme ->
            val label = when (theme.getString("selection")) { "builtin" -> "Cyan Orbit"; "imported" -> "読み込んだテーマ"; else -> "装飾なし" }
            val loading = theme.getBoolean("loading")
            val message = themeImportMessage ?: when {
                !theme.isNull("error") -> "テーマを適用できませんでした。現在の表示を維持しています。"
                !theme.isNull("fallback_reason") -> "装飾を表示できなかったため、装飾なしに戻しました"
                loading -> "テーマを読み込み中…"
                !theme.getJSONObject("storage").isNull("error") -> "$label · 設定を保存できませんでした"
                theme.getJSONObject("storage").getBoolean("pending") -> "$label · 保存中"
                else -> label
            }
            val value = ThemeUi(if (loading) theme.getString("requested") else theme.getString("selection"),
                theme.getBoolean("reduced_motion"), theme.getBoolean("imported_available"), loading, message)
            ui.post { themeUi.value = value }
        }
        val background=if(workspace.getString("background")=="passthrough") "現実" else "単色"
        val saveStatus=when { !storage.isNull("error") -> "保存できません（${storage.getString("error")}）";workspace.getBoolean("pending_save")->"保存中";else->"保存済み" }
        ui.post { workspaceSummary.value="背景: $background · キー配列: ${workspace.getString("keyboard_layout").uppercase()} · $saveStatus" }
        val selected=selectedTarget(runtime)
        val appWindows=runtime.optJSONObject("app")?.optJSONArray("windows")
        val selectedWindow=appWindows?.let { windows -> (0 until windows.length()).map { windows.getJSONObject(it) }.firstOrNull { it.optString("window")==selected?.optString("window") } }
        val currentApp=runtime.optJSONObject("app")
        val choices=appWindows?.let { windows -> (0 until windows.length()).map { windows.getJSONObject(it) }.filter { it.optString("life")=="Mapped" }.map { w ->
            val plan=(0 until plans.length()).map { plans.getJSONObject(it) }.firstOrNull { it.optString("action")=="create" && it.getJSONObject("target").optString("window")==w.getString("window") }
            val placements=workspace.getJSONArray("windows")
            val ambiguous=(0 until placements.length()).map {placements.getJSONObject(it)}.firstOrNull {it.getString("window")==w.getString("window")}?.optBoolean("ambiguous")==true
            WindowChoice(checkNotNull(currentApp).getString("session"),runtime.optString("scene"),w.getString("window"),plan?.optInt("slot"),ambiguous)
        } } ?: emptyList()
        ui.post { windowChoices.value=choices }
        terminalAdding?.let { operation ->
            if(currentApp?.optString("session")!=operation.session || choices.any { it.window !in operation.windows }) terminalAdding=null
            else if(SystemClock.elapsedRealtime()>operation.deadline) { terminalError="ウィンドウの追加を確認できません。接続を閉じて再試行してください";terminalAdding=null }
        }
        val logical=selectedWindow?.optJSONObject("logical_size")
        val resize=selectedWindow?.optJSONObject("resize")
        val label=if(logical==null) "ターミナルを開くとサイズを変更できます" else {
            val status=when(resize?.optString("phase")) { "timed_out"->" · サイズ変更に応答がありません"; "adjusted"->" · アプリ側でサイズ調整"; "queued","awaiting_ack","awaiting_commit"->" · 変更中";else->"" }
            "ウィンドウ ${selected?.optString("window")}: ${logical.getInt("width")}×${logical.getInt("height")}$status"
        }
        ui.post { layoutAvailable.value=logical!=null; layoutSummary.value=label }
        val transport = snapshot.getJSONObject("runtime").getJSONObject("transport")
        terminalOpen?.let { operation ->
            val now=SystemClock.elapsedRealtime()
            val failed=transport.getString("state")=="Failed" && operation.connectAccepted &&
                transport.optString("session")!=operation.previousSession
            if(now>=operation.deadline || operation.startupWaiting && now>=operation.startupDeadline) {
                terminalError="起動待ちが時間切れになりました。Termuxを開いて再試行してください"
                ui.post {termuxHelp.value=true};clearTerminalOpen()
            } else if(operation.retryReady && !connectPending && transport.getInt("owned_connections")==0) {
                operation.previousSession=transport.optString("session")
                operation.connectAccepted=false
                operation.connectRequest=enqueueConnect()
                if(operation.connectRequest!=null) operation.retryReady=false
            } else if(!operation.startupWaiting && !operation.retryReady && failed) {
                if(!operation.startupAttempted && transport.optString("error")=="SSH_CONNECTION_REFUSED" &&
                    transport.getInt("owned_connections")==0 && activityVisible.get()) {
                    operation.startupAttempted=true;operation.startupWaiting=true;operation.startupDeadline=now+15000
                    termuxRequests++
                    val error=TermuxStarter.start(checkNotNull(application),operation.nonce) {error ->
                        worker.execute {
                            if(terminalOpen===operation && operation.startupWaiting && SystemClock.elapsedRealtime()<operation.startupDeadline) {
                                operation.startupWaiting=false
                                if(error==null) {operation.retryReady=true;termuxSuccesses++}
                                else {termuxFailures++;terminalError=error;ui.post {termuxHelp.value=true};clearTerminalOpen()}
                            }
                        }
                    }
                    if(error!=null) {termuxFailures++;terminalError=error;ui.post {termuxHelp.value=true};clearTerminalOpen()}
                } else if(transport.getInt("owned_connections")==0) {
                    terminalError="接続を開始できません。接続状態と設定を確認してください";clearTerminalOpen()
                }
            } else {
                val ready=transport.getString("state")=="Ready" && !operation.startupWaiting && !operation.retryReady &&
                    (operation.connectRequest==null || operation.connectAccepted && transport.optString("session")!=operation.previousSession)
                if(ready && operation.startRequest==null) {
                    val request=requests.incrementAndGet().toString()
                    val command=JSONObject().put("schema",1).put("type","start_app").put("request_id",request)
                        .put("session",transport.getString("session")).put("nonce",operation.nonce).put("mode","foot")
                    val code=NativeBridge.nativeCommand(handle,command.toString())
                    if(code==NativeBridge.ACCEPTED) operation.startRequest=request
                    else if(code!=NativeBridge.BUSY) {terminalError="ターミナルを開始できません（$code）";clearTerminalOpen()}
                }
            }
        }
        val phase = if(terminalOpen?.startupWaiting==true) "Termuxを起動中（最大15秒）" else when (val value = transport.getString("state")) {
            "Idle", "Disconnected" -> "未接続"
            "Connecting", "AwaitingCredential", "Authenticating" -> "接続中（切断ボタンで取消）"
            "Ready" -> "接続済み"
            "Failed" -> "接続が終了しました"
            "Disconnecting" -> "終了を確認中"
            "Recovering" -> "前回の終了を確認中"
            "CleanupPending" -> "前回の終了を確認できません"
            else -> value
        }
        val pending = transport.optJSONArray("pending_cleanup")?.length() ?: 0
        val appError = snapshot.getJSONObject("runtime").optJSONObject("app")?.takeUnless {it.isNull("error")}?.optString("error")?.takeIf {it.isNotEmpty()}
        val panelError = snapshot.getJSONObject("runtime").optJSONArray("panels")?.let { plans ->
            (0 until plans.length()).map { plans.getJSONObject(it) }.firstOrNull { !it.isNull("error") }?.getString("error")
        }
        val error = terminalError ?: panelError ?: appError
        val connectionError=when(transport.optString("error")) {
            "HOST_KEY_UNKNOWN" -> "初回の接続設定が必要です"
            "HOST_KEY_MISMATCH" -> "Termuxのホスト鍵が保存済みの鍵と違います。接続設定で指紋を照合してください"
            "AUTH_FAILED" -> "公開鍵認証に失敗しました。TermuxへAPK公開鍵を登録してください"
            "SSH_CONNECTION_REFUSED", "AUTH_TIMEOUT", "SSH_CONNECT_FAILED", "CONNECT_TIMEOUT" -> "Termuxでsshdを起動し、ターミナルを開き直してください"
            "REMOTE_EXIT", "SSH_IO_FAILED" -> "接続先が終了しました。Termuxを確認し、ターミナルを開き直してください"
            else -> transport.optString("error").takeUnless {it.isEmpty() || it=="null"}
        }
        val ssh = phase + (if (pending > 0 && transport.getString("state")!="Ready") " · 未確認の回収 ${pending}件" else "") +
            ((error ?: connectionError)?.let {" · $it"} ?: "")
        if (ssh != sshState) { sshState = ssh; ui.post { connectionSummary.value = ssh } }
        val busy=terminalOpen!=null || terminalAdding!=null || connectPending || pendingDisconnect!=null
        ui.post {terminalBusy.value=busy}
        val next = snapshot.getJSONObject("runtime").getString("state")
        val editable=canChangeConnection(snapshot.getJSONObject("runtime"))
        if(editable!=connectionEditable) {connectionEditable=editable;ui.post {connectionUi.value=connectionUi.value.copy(canSave=editable)}}
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
