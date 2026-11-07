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
    private val probeRecovery = ProbeRuntimeRecovery()
    private val debugSdkLease = DebugSdkRuntimeLease()
    private val productWorker = DebugSdkProductExecutor(debugSdkLease, worker)
    private var debugSdkCapture: ProbeCredentialCapture? = null
    @Volatile private var debugSdkFailure: String? = null
    private var identity: CredentialStore.Identity? = null
    private var profiles = JSONArray()
    private var lastConnectRequest: String? = null // connectとcancelをworker上の順序で束縛する。
    private var connectPending = false
    private var cancelledThrough = 0L
    private data class PendingDisconnect(val connectRequest: String?, val session: String?, var request: String? = null)
    private var pendingDisconnect: PendingDisconnect? = null
    private data class KeyboardFocus(val runtime: Long, val epoch: Long, val target: String, val token: String)
    @Volatile private var keyboardFocus: KeyboardFocus? = null
    @Volatile private var imeConnection: ImeSessionConnection? = null // Methods belong to UI; worker only captures this identity.
    private val imeRawBlocked = AtomicBoolean(false)
    private var imeCaptureTicket = 0L // UI owner, invalidated by any external input intention.
    private var imeCaptureEpoch = 0L
    private var imeRawReturn: ImeRawFocusReturn? = null
    @Volatile private var imeInputMessage: InputStatus? = null
    private var imeFrameOrder = 0L // Worker-to-UI ordered packet sequence.
    private var imeAndroidReleased: (() -> Boolean)? = null // UI only; captures a WeakReference, never an Activity.
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
    private val inputIntents=InputIntentSerial()
    private val adapterSnapshot=AtomicReference<String?>(null)
    private data class PendingInputFocus(val target: String, val runtime: Long, val ticket: Long, val deadline: Long,
        var resetRequest: String? = null, var resetCompleted: Boolean = false, var focusRequest: String? = null,
        val waitForIme: Boolean = false, val controlsRevision: Long? = null)
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
    val launcherExpanded = mutableStateOf(false)
    val launcherUi = mutableStateOf(LauncherUi())
    val launcherFocusReset = mutableStateOf(0L)
    private var launcherState = LauncherUi()
    private data class CatalogPending(val session: String, val request: String, val path: String?, val kind: String,
        val deadline: Long = SystemClock.elapsedRealtime()+8000)
    private var catalogPending: CatalogPending? = null
    private val panelTextInputs = ConcurrentHashMap.newKeySet<String>()
    fun panelTextInput(key: String, active: Boolean) {
        if(active) { if(panelTextInputs.add(key)) resetInput() } else panelTextInputs.remove(key)
    }
    private fun releasePanelTextFocus() {
        if(panelTextInputs.isNotEmpty()) ui.post {launcherFocusReset.value=launcherFocusReset.value+1}
    }
    private fun launcherUpdate(value: LauncherUi) { launcherState=value;ui.post {launcherUi.value=value} }
    fun showLauncher() { launcherExpanded.value=true }
    fun reloadLauncher(path: String) { productWorker.execute { safely {
        requestCatalog(JSONObject().put("kind","reload").put("path",path),path)
    } } }
    fun launcherPage(offset: Int) { productWorker.execute { safely {
        requestCatalog(JSONObject().put("kind","page").put("revision",launcherState.revision).put("offset",offset))
    } } }
    fun saveLauncher(form: LauncherForm, path: String) { productWorker.execute { safely {
        if(!form.valid()) return@safely
        requestCatalog(JSONObject().put("kind","save").put("path",path).put("definition",form.definition())
            .put("expected_revision",if(path==launcherState.path && !launcherState.builtin) launcherState.revision else JSONObject.NULL),path)
    } } }
    private fun requestCatalog(operation: JSONObject, path: String? = null) {
        if(handle==0L || catalogPending!=null) return
        val runtime=snapshot().getJSONObject("runtime")
        val app=runtime.optJSONObject("app")
        val session=runtime.getJSONObject("transport").optString("session")
        if(app?.optString("state")!="running" || app.optString("mode")!="foot" || app.optString("session")!=session) {
            launcherUpdate(launcherState.copy(message="まずターミナルを開いて接続してください"));return
        }
        val request=requests.incrementAndGet().toString()
        val code=NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","catalog").put("request_id",request)
            .put("session",session).put("operation",operation).toString())
        if(code==NativeBridge.ACCEPTED) {
            catalogPending=CatalogPending(session,request,path,operation.getString("kind"))
            launcherUpdate(launcherState.copy(busy=true,message=if(operation.getString("kind")=="save") "登録を保存中" else "一覧を読み込み中"))
        } else launcherUpdate(launcherState.copy(message="操作を開始できません（$code）"))
    }
    private fun pumpCatalog(runtime: JSONObject) {
        val transport=runtime.getJSONObject("transport")
        val app=runtime.optJSONObject("app")
        val session=transport.optString("session").takeIf { transport.optString("state")=="Ready" &&
            app?.optString("state")=="running" && app.optString("mode")=="foot" && app.optString("session")==it } ?: ""
        if(session!=launcherState.session) {
            catalogPending=null
            launcherUpdate(LauncherUi(session=session,connected=session.isNotEmpty(),path=launcherState.path))
            if(session.isNotEmpty()) requestCatalog(JSONObject().put("kind","reload").put("path",launcherState.path),launcherState.path)
        }
        val pending=catalogPending ?: return
        if(pending.session!=session || SystemClock.elapsedRealtime()>pending.deadline) {
            catalogPending=null;launcherUpdate(launcherState.copy(busy=false,message="読込を確認できませんでした。再読込してください"));return
        }
        val envelope=JSONObject(NativeBridge.nativeCatalogUi(handle)) // UI専用。DiagnosticsStoreへ渡さない。
        if(envelope.optInt("code")!=NativeBridge.ACCEPTED) return
        val value=envelope.getJSONObject("catalog")
        if(value.optString("session")!=pending.session || value.optString("request_id")!=pending.request || value.optBoolean("pending")) return
        catalogPending=null
        val reply=value.optJSONObject("reply")
        if(reply==null) {launcherUpdate(launcherState.copy(busy=false,message=LauncherMessages.error(value.optString("error"))));return}
        val entries=reply.getJSONArray("entries")
        val error=reply.optString("error").takeIf { !reply.isNull("error") }
        val successfulPath=if(error==null) pending.path else null
        if(successfulPath!=null) application?.getSharedPreferences("launcher",Context.MODE_PRIVATE)?.edit()?.putString("path",successfulPath)?.apply()
        launcherUpdate(launcherState.copy(busy=false,revision=reply.getString("revision"),builtin=reply.getBoolean("builtin"),
            total=reply.getInt("total"),offset=reply.getInt("offset"),path=successfulPath ?: launcherState.path,
            entries=(0 until entries.length()).map {i -> entries.getJSONObject(i).let { e ->
                LauncherEntry(e.getString("id"),e.getString("label"),e.getString("icon"),e.getBoolean("available"),e.optString("error").takeIf { !e.isNull("error") })
            }},message=if(error=="PATH_MISSING" && reply.getBoolean("builtin")) "設定ファイルは未作成です。標準のアプリ一覧を表示します"
                else if(error!=null) LauncherMessages.error(error) else if(pending.kind=="save") "登録しました" else "アプリを選んで起動できます"))
    }
    fun launchRegistered(session: String, revision: String, id: String) { launchCatalogTerminal(session,
        JSONObject().put("kind","registered").put("revision",revision).put("id",id)) }
    fun launchFile(form: LauncherForm, requested: ((String, Int) -> Unit)? = null) {
        if(form.valid()) launchCatalogTerminal(launcherUi.value.session,JSONObject().put("kind","temporary").put("definition",form.definition()), requested)
    }
    private fun launchCatalogTerminal(expectedSession: String, target: JSONObject, requested: ((String, Int) -> Unit)? = null) {
        val payload=target.toString()
        productWorker.execute { safely {
            if(handle==0L || terminalAdding!=null || terminalOpen!=null || pendingDisconnect!=null) return@safely
            val runtime=snapshot().getJSONObject("runtime");val app=runtime.optJSONObject("app")
            if(app?.optString("session")!=expectedSession || runtime.getJSONObject("transport").optString("session")!=expectedSession ||
                app.optString("state")!="running" || app.optString("mode")!="foot") {
                launcherUpdate(launcherState.copy(message="接続後に一覧を再読込してください"));return@safely
            }
            val windows=app.getJSONArray("windows")
            if(windows.length()>=WindowBudget.MAX_WINDOWS) {launcherUpdate(launcherState.copy(message="ウィンドウ上限です。不要な窓を閉じてください"));return@safely}
            releasePanelTextFocus()
            val request=requests.incrementAndGet().toString()
            val code=NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","launch_terminal").put("request_id",request)
                .put("session",expectedSession).put("target",JSONObject(payload)).toString())
            if(code==NativeBridge.ACCEPTED) {
                terminalError=null
                terminalAdding=TerminalAdding(expectedSession,(0 until windows.length()).map {windows.getJSONObject(it).getString("window")}.toSet(),request)
                launcherUpdate(launcherState.copy(message="起動中"))
            } else launcherUpdate(launcherState.copy(message="起動できません（$code）"))
            requested?.invoke(request, code) // enqueue受理。起動完了/登録/描画のACKではない。
        } }
    }
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
        if (debugSdkLease.blocksProduct()) return false
        val transport=runtime.getJSONObject("transport")
        return runtime.getString("state")=="Ready" && !connectPending && pendingDisconnect==null && terminalOpen==null &&
            transport.getString("state") in setOf("Idle","Disconnected","Failed") && transport.getInt("owned_connections")==0 &&
            transport.getJSONArray("pending_cleanup").length()==0 && (runtime.optJSONObject("app")?.optString("state") ?: "stopped")=="stopped"
    }
    fun saveConnection(username:String,hostKey:String,expected:String,confirmed:Boolean) {
        productWorker.execute {
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
            if(handle==0L) launcherUpdate(launcherState.copy(path=application.getSharedPreferences("launcher",Context.MODE_PRIVATE)
                .getString("path",launcherState.path) ?: launcherState.path))
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
    fun sceneReady(): Long = inputIntents.serial {
        inputIntents.invalidate()
        val scene = scenes.incrementAndGet()
        send("scene_ready", "scene", scene.toString())
        scene
    }
    fun currentSceneEpoch(): Long = scenes.get()
    fun setXrState(value: String, expectedScene: Long? = null): Boolean = inputIntents.serial {
        if(expectedScene!=null && expectedScene!=scenes.get()) return@serial false
        if(value!="FOCUSED") {inputIntents.invalidate();keyboardFocus=null;inputTargets=emptyMap()}
        xrState.set(value);send("set_xr_state","state",value);true
    }
    fun setActivityVisible(value: Boolean, expectedScene: Long? = null): Boolean = inputIntents.serial {
        if(expectedScene!=null && expectedScene!=scenes.get()) return@serial false
        if(!value) {inputIntents.invalidate();keyboardFocus=null;inputTargets=emptyMap()}
        activityVisible.set(value);send("set_activity_state","visible",value);true
    }
    private fun enqueueConnect(): String? {
        val request = requests.incrementAndGet().toString()
        val command = JSONObject().put("schema", 1).put("type", "connect").put("request_id", request).put("profile_id", "termux-local")
        if (NativeBridge.nativeCommand(handle, command.toString()) != NativeBridge.ACCEPTED) return null
        lastConnectRequest = request
        connectPending = true
        return request
    }
    fun connect() { productWorker.execute { safely {
        if (handle == 0L || pendingDisconnect != null || connectPending) return@safely
        val transport = snapshot().getJSONObject("runtime").getJSONObject("transport")
        if (transport.getString("state") in setOf("Idle", "Disconnected", "Failed")) enqueueConnect()
    } } }
    fun openTerminal() { productWorker.execute { safely {
        releasePanelTextFocus()
        if (handle == 0L || terminalOpen != null || pendingDisconnect != null || connectPending) return@safely
        val runtime = snapshot().getJSONObject("runtime")
        val transport = runtime.getJSONObject("transport")
        val app = runtime.optJSONObject("app")
        if (app != null && app.optString("session") == transport.optString("session") && app.isNull("error") && app.optString("state") != "stopped") {
            val windows=app.getJSONArray("windows")
            if(app.optString("mode")!="foot" || app.optString("state")!="running" || windows.length()>=WindowBudget.MAX_WINDOWS || terminalAdding!=null) return@safely
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
    fun disconnect() { productWorker.execute { safely {
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
    private fun windowCommand(choice: WindowChoice, type: String, slot: Int?) { productWorker.execute { inputIntents.serial { safely {
        if(handle==0L) return@safely
        resetInput()
        val command=JSONObject().put("schema",1).put("request_id",requests.incrementAndGet().toString()).put("type",type)
            .put("session",choice.session).put("scene",choice.scene).put("window",choice.window)
        if(slot!=null) command.put("slot",slot)
        NativeBridge.nativeCommand(handle,command.toString())
    } } } }
    data class WindowControlTicket(val runtime: Long, val target: String, val intent: Long, val revision: Long)
    /** SDK button down owns one immutable target; reset also releases any Linux-held key. */
    fun beginWindowControl(target: JSONObject, runtime: Long, revision: Long): WindowControlTicket? {
        check(Looper.myLooper()==Looper.getMainLooper())
        val current=inputTarget(target.toString()) ?: return null
        if(current.runtime!=runtime || runtime!=handle || revision<0) return null
        resetInput()
        return WindowControlTicket(runtime,target.toString(),inputIntents.get(),revision)
    }
    fun windowControl(ticket: WindowControlTicket, action: String) { productWorker.execute { inputIntents.serial { safely {
        if(action !in setOf("focus","close") || handle!=ticket.runtime || inputIntents.get()!=ticket.intent || editing.get() ||
            !activityVisible.get() || xrState.get()!="FOCUSED") return@safely
        val target=JSONObject(ticket.target)
        val runtime=snapshot().getJSONObject("runtime")
        val plans=runtime.getJSONArray("panels")
        val valid=(0 until plans.length()).any { i -> plans.getJSONObject(i).let {p ->
            p.optString("action")=="create" && p.optBoolean("applied") && p.isNull("error") && targetIdentity(p.getJSONObject("target"))==targetIdentity(target)
        }}
        if(!valid || runtime.optString("scene")!=target.optString("scene") || runtime.getJSONObject("render").optLong("theme_revision",0)!=ticket.revision) return@safely
        if(action=="focus") {
            selectedWindow=target.getString("session") to target.getString("window")
            pendingInputFocus=PendingInputFocus(ticket.target,ticket.runtime,ticket.intent,SystemClock.elapsedRealtime()+10000,waitForIme=true,controlsRevision=ticket.revision)
        } else {
            val request=requests.incrementAndGet().toString()
            val code=NativeBridge.nativeCommand(ticket.runtime,JSONObject().put("schema",1).put("type","close_window_target")
                .put("request_id",request).put("target",target).put("controls_revision",ticket.revision).toString())
            BuildHooks.windowControlRequested(action,ticket.runtime,ticket.target,ticket.revision,request,code)
        }
    } } } }
    data class WindowMoveTicket(val control:WindowControlTicket,val source:Long)
    private val windowMove=AtomicReference<WindowMoveTicket?>(null)
    fun beginWindowMove(target:JSONObject,runtime:Long,revision:Long,source:Long):WindowMoveTicket? {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(windowMove.get()!=null || source==0L || panelTextInputs.isNotEmpty()) return null
        val control=beginWindowControl(target,runtime,revision) ?: return null
        return WindowMoveTicket(control,source).takeIf {windowMove.compareAndSet(null,it)}
    }
    fun windowMoveCurrent(ticket:WindowMoveTicket):Boolean=windowMove.get()===ticket && ticket.control.runtime==handle &&
        ticket.control.intent==inputIntents.get() && activityVisible.get() && xrState.get()=="FOCUSED" && !editing.get() && panelTextInputs.isEmpty()
    fun windowMoveSourceOwned(source:Long):Boolean=windowMove.get()?.source==source
    private fun movingTarget(target:String):Boolean=windowMove.get()?.control?.target?.let {targetIdentity(JSONObject(it))==targetIdentity(JSONObject(target))}==true
    fun finishWindowMove(ticket:WindowMoveTicket,restore:Boolean) {
        if(!windowMove.compareAndSet(ticket,null)) return
        if(restore && ticket.control.intent==inputIntents.get()) windowControl(ticket.control,"focus")
    }
    fun submitWindowMovePose(ticket:WindowMoveTicket,pose:JSONObject):String? {
        if(!windowMoveCurrent(ticket) || !PanelPoseCodec.valid(pose) || poseCompletions.size>=16) return null
        val request=requests.incrementAndGet().toString();poseCompletions[request]="pending"
        val code=NativeBridge.nativeCommand(ticket.control.runtime,JSONObject().put("schema",1).put("type","set_pose_control")
            .put("request_id",request).put("target",JSONObject(ticket.control.target)).put("pose",pose).put("controls_revision",ticket.control.revision).toString())
        BuildHooks.windowControlRequested("move",ticket.control.runtime,ticket.control.target,ticket.control.revision,request,code)
        if(code!=NativeBridge.ACCEPTED) {poseCompletions.remove(request);return null}
        return request
    }
    fun requestSnapshot() { worker.execute { safely { publish() } } }
    fun currentHandle(): Long = handle
    fun keyboardFocused(): Boolean = keyboardFocus != null
    fun keyboardFocusMatches(target: JSONObject, token: String): Boolean {
        val focus=keyboardFocus ?: return false
        return focus.runtime==handle && focus.token==token && targetIdentity(JSONObject(focus.target))==targetIdentity(target)
    }
    fun publishCheck(value: JSONObject) { val copy = value.toString(); worker.execute { safely { store?.publishCheck(JSONObject(copy)) } } }
    fun publishAdapter(value: JSONObject) { val copy = value.toString(); adapterSnapshot.set(copy); worker.execute { safely { store?.publishAdapter(JSONObject(copy)) } } }
    fun debugAdapterSnapshot():JSONObject? = if(BuildConfig.DEBUG) adapterSnapshot.get()?.let {JSONObject(it)} else null
    fun detachSurface(target: JSONObject, runtime: Long = handle): Int {
        return NativeBridge.nativeCommand(runtime, JSONObject().put("schema", 1).put("request_id", requests.incrementAndGet().toString())
            .put("type", "surface_lost").put("target", target).toString())
    }
    fun ackPanel(plan: JSONObject, runtime: Long, success: Boolean = true): Int = NativeBridge.nativeCommand(runtime,
        JSONObject().put("schema", 1).put("request_id", requests.incrementAndGet().toString())
            .put("type", if (success) "adapter_applied" else "adapter_failed")
            .put("command_seq", plan.getString("command_seq")).put("target", plan.getJSONObject("target"))
            .put("action", plan.getString("action")).toString())
    fun resetInput(): Int = inputIntents.serial { resetInputLocked() }
    fun resetInputForRetiringTarget(target:JSONObject,runtime:Long):Int = inputIntents.serial {
        if(runtime<=0 || runtime!=handle) NativeBridge.STALE else resetInputLocked(InputRetirement(runtime,targetIdentity(target)))
    }
    private fun resetInputLocked(retiring:InputRetirement?=null): Int {
        invalidateImeConnection()
        if(retiring==null) inputIntents.incrementAndGet() else inputIntents.retiring(retiring)
        if (BuildConfig.DEBUG) Log.i(TAG,"input_reset intent=${inputIntents.get()}")
        keyboardFocus = null
        inputTargets = emptyMap()
        if (handle == 0L) return NativeBridge.NOT_READY
        return enqueueInputReset(requests.incrementAndGet().toString())
    }
    private fun enqueueInputReset(request: String): Int = NativeBridge.nativeCommand(handle,
        JSONObject().put("schema",1).put("request_id",request).put("type","reset_input").toString())
    fun inputTarget(target: String): InputTarget? {
        if (debugSdkLease.blocksProduct() || editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED" || movingTarget(target)) return null
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
        if (debugSdkLease.blocksProduct()) return
        editing.set(value); resetInput(); ui.post { layoutEditing.value=value }
    }
    fun finishLayoutEditing() { productWorker.execute { inputIntents.serial { safely {
        if(handle==0L) return@safely
        val runtime=snapshot().getJSONObject("runtime")
        val target=focusTarget(runtime,requireApplied=false)
        editing.set(false);ui.post {layoutEditing.value=false}
        keyboardFocus=null;inputTargets=emptyMap()
        val ticket=inputIntents.incrementAndGet()
        pendingInputFocus=target?.let { PendingInputFocus(it.toString(),handle,ticket,SystemClock.elapsedRealtime()+2000) }
        if (BuildConfig.DEBUG) Log.i(TAG,"focus_begin editing target=${target?.let {targetIdentity(it)}} ticket=$ticket")
        if(target==null) resetInput()
    } } } }
    fun selectWindow(choice: WindowChoice) { productWorker.execute { safely {
        selectedWindow=choice.session to choice.window; resetInput()
    } } }
    fun setBackground(mode: String) { if(mode in setOf("passthrough","virtual")) productWorker.execute { send("set_background","background",mode) } }
    fun setKeyboardLayout(layout: String) { if(layout in setOf("us","jis")) productWorker.execute { resetInput();send("set_keyboard_layout","layout",layout) } }
    private fun themeCommand(command: JSONObject, done: (String) -> Unit = {}) {
        if (handle == 0L || themeRequests.size >= 8) { done("NOT_READY"); return }
        if(command.optString("type") in setOf("set_theme","begin_theme_import","apply_theme_import")) resetInput()
        val request = requests.incrementAndGet().toString()
        command.put("schema", 1).put("request_id", request)
        val code = NativeBridge.nativeCommand(handle, command.toString())
        if (code == NativeBridge.ACCEPTED) themeRequests[request] = done else done("REJECTED")
    }
    fun setWorkloadObservation(enabled: Boolean) {
        productWorker.execute { safely { themeCommand(JSONObject().put("type","set_workload_observation").put("enabled",enabled)) } }
    }
    fun setTheme(selection: String, reduced: Boolean = themeUi.value.reduced) {
        if (selection !in setOf("off", "builtin", "imported")) return
        productWorker.execute { safely {
            themeImportToken = null; themeImportMessage = null
            themeCommand(JSONObject().put("type", "set_theme").put("selection", selection).put("reduced_motion", reduced)) {
                if (it != "ACCEPTED") themeImportMessage = "テーマの変更を開始できませんでした"
            }
        } }
    }
    fun beginThemeImport(ready: (String) -> Unit) { productWorker.execute { safely {
        val token = java.util.UUID.randomUUID().toString().replace("-", "")
        themeImportToken = token; themeImportMessage = null
        themeCommand(JSONObject().put("type", "begin_theme_import").put("token", token)) { code ->
            if (themeImportToken == token) {
                if (code == "ACCEPTED") ui.post { ready(token) }
                else { themeImportToken = null; themeImportMessage = "テーマの読み込みを開始できませんでした" }
            }
        }
    } } }
    fun cancelThemeImport(token: String) { productWorker.execute { safely {
        if (themeImportToken == token) {
            themeImportToken = null; themeImportMessage = null
            themeCommand(JSONObject().put("type", "cancel_theme_import").put("token", token))
        }
    } } }
    fun readTheme(context: Context, uri: android.net.Uri, token: String) {
        val ticket = debugSdkLease.metadata().generation
        if (!debugSdkLease.permitsProduct(ticket)) return
        ThemeImporter.read(context, uri, token, { themeImportToken == token }) { file, error ->
            worker.execute { safely {
                if (!debugSdkLease.permitsProduct(ticket)) { file?.delete(); return@safely }
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
    fun nudgePose(x: Double=0.0,y: Double=0.0,z: Double=0.0,yaw: Double=0.0) { productWorker.execute { inputIntents.serial { safely {
        if(handle==0L) return@safely
        resetInputLocked()
        val runtime=snapshot().getJSONObject("runtime");val target=selectedTarget(runtime) ?: return@safely
        val windows=runtime.getJSONObject("workspace").getJSONArray("windows")
        val window=(0 until windows.length()).map {windows.getJSONObject(it)}.firstOrNull {it.getString("window")==target.getString("window") && it.getString("session")==target.getString("session")} ?: return@safely
        val pose=PanelPoseCodec.nudge(window.getJSONObject("placement").getJSONObject("pose"),x,y,z,yaw)
        if(PanelPoseCodec.valid(pose)) NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","set_pose").put("request_id",requests.incrementAndGet().toString()).put("target",target).put("pose",pose).toString())
    } } } }
    fun resizeTerminal(width: Int, height: Int) { productWorker.execute { inputIntents.serial { safely {
        if(handle==0L) return@safely
        resetInputLocked()
        val target=selectedTarget(snapshot().getJSONObject("runtime")) ?: return@safely
        NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","resize_logical")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target).put("width",width).put("height",height).toString())
    } } } }
    fun setPhysicalSize(width: Double, height: Double) { productWorker.execute { inputIntents.serial { safely {
        if(handle==0L) return@safely
        resetInputLocked()
        val target=selectedTarget(snapshot().getJSONObject("runtime")) ?: return@safely
        NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","set_physical_size")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target)
            .put("size",JSONObject().put("width_m",width).put("height_m",height)).toString())
    } } } }
    fun ackPhysicalSize(target: JSONObject, command: String?, size: JSONObject, runtime: Long): Int =
        NativeBridge.nativeCommand(runtime,JSONObject().put("schema",1).put("type","physical_size_applied")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target)
            .put("size_command",command ?: JSONObject.NULL).put("size",size).toString())
    /** SDK callbackではimmutableなtargetと小DTOだけをenqueueする。座標をログへ出さない。 */
    fun sendPointer(target: InputTarget, source: Long, uv: List<Double>?, event: JSONObject): Boolean = inputIntents.serial {
        sendPointerLocked(target,source,uv,event)
    }
    private fun sendPointerLocked(target: InputTarget, source: Long, uv: List<Double>?, event: JSONObject): Boolean {
        if (source<=0 || windowMoveSourceOwned(source) || inputTarget(target.target)!=target) return false
        if(event.optString("type")=="buttons" && event.optInt("state") and 1 != 0) {
            inputIntents.incrementAndGet();releasePanelTextFocus()
        }
        val pointer=JSONObject().put("source",source.toString()).put("uv",uv?.let { JSONArray(it) } ?: JSONObject.NULL)
            .put("event",event).put("time_ms",SystemClock.uptimeMillis() and 0xffffffffL)
        val code=NativeBridge.nativeCommand(target.runtime,JSONObject().put("schema",1).put("type","pointer")
            .put("request_id",requests.incrementAndGet().toString()).put("target",JSONObject(target.target))
            .put("input_epoch",target.epoch).put("pointer",pointer).toString())
        if (code !in setOf(NativeBridge.ACCEPTED,NativeBridge.INVALID_ARGUMENT)) resetInput()
        return code!=NativeBridge.INVALID_ARGUMENT
    }
    class DebugInputTrial internal constructor(internal val ticket:InputIntentSerial.Trial,val runtime:Long,val epoch:Long) {
        internal val targets=mutableSetOf<String>()
    }
    fun debugCaptureInput(target:InputTarget):DebugInputTrial? = inputIntents.serial {
        if(!BuildConfig.DEBUG || !debugImeCanRequestFocus() || inputTarget(target.target)!=target) null
        else DebugInputTrial(inputIntents.capture(),target.runtime,target.epoch).also {it.targets.add(target.identity)}
    }
    fun debugInputCurrent(trial:DebugInputTrial):Boolean = inputIntents.serial {
        val valid=BuildConfig.DEBUG && handle==trial.runtime && debugImeCanRequestFocus()
        if(!valid) inputIntents.invalidate()
        valid && inputIntents.current(trial.ticket)
    }
    fun debugInputDispatch(trial:DebugInputTrial,target:InputTarget,focusToken:String?=null,pointerDown:Boolean=false,enqueue:()->Int):Boolean = inputIntents.serial {
        val result=inputIntents.dispatch(trial.ticket,{
            debugInputCurrent(trial) && target.runtime==trial.runtime && target.epoch==trial.epoch && inputTarget(target.target)==target &&
                (focusToken==null || keyboardFocus?.let {it.runtime==trial.runtime && it.epoch==trial.epoch && targetIdentity(JSONObject(it.target))==target.identity && it.token==focusToken}==true)
        }) {
            trial.targets.add(target.identity)
            if(pointerDown) {inputIntents.ownedIncrement(trial.ticket);releasePanelTextFocus()}
            enqueue()
        }
        if(result!=NativeBridge.ACCEPTED) inputIntents.invalidate()
        result==NativeBridge.ACCEPTED
    }
    fun debugSealInput(trial:DebugInputTrial):Boolean=inputIntents.serial {debugInputCurrent(trial) && inputIntents.seal(trial.ticket)}
    fun debugExpectRetirement(trial:DebugInputTrial,target:JSONObject):Boolean=inputIntents.serial {
        debugInputCurrent(trial) && targetIdentity(target) in trial.targets && inputIntents.expectRetirement(trial.ticket,InputRetirement(trial.runtime,targetIdentity(target)))
    }
    fun debugCloseInputTarget(trial:DebugInputTrial,target:JSONObject,enqueue:()->Int):Boolean=inputIntents.serial {
        if(!debugInputCurrent(trial) || !inputIntents.isSealed(trial.ticket) || targetIdentity(target) !in trial.targets) false
        else {
            inputIntents.expectRetirement(trial.ticket,InputRetirement(trial.runtime,targetIdentity(target)))
            (enqueue()==NativeBridge.ACCEPTED).also {if(!it) inputIntents.invalidate()}
        }
    }
    fun debugFinishInput(trial:DebugInputTrial):Boolean=inputIntents.serial {
        val valid=debugInputCurrent(trial) && inputIntents.isSealed(trial.ticket)
        inputIntents.release(trial.ticket);valid
    }
    fun debugReleaseInput(trial:DebugInputTrial)=inputIntents.release(trial.ticket)
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
    data class DebugImeFocusReceipt(val runtime: Long, val target: ImeSdkTarget, val ticket: Long, val beforeEpoch: Long, val beforeToken: String?)
    fun focusTerminal(choice: WindowChoice? = null, requested: ((DebugImeFocusReceipt) -> Unit)? = null) { productWorker.execute { inputIntents.serial { safely {
        pendingInputFocus=null
        if (handle == 0L) return@safely
        if(editing.get()) return@safely
        val runtime = snapshot().getJSONObject("runtime")
        val target=focusTarget(runtime,choice,requireApplied=false) ?: return@safely
        releasePanelTextFocus()
        selectedWindow=target.getString("session") to target.getString("window")
        keyboardFocus=null;inputTargets=emptyMap()
        val ticket=inputIntents.incrementAndGet()
        pendingInputFocus=PendingInputFocus(target.toString(),handle,ticket,SystemClock.elapsedRealtime()+2000,waitForIme=true)
        if (BuildConfig.DEBUG && requested!=null) strictImeSdkTarget(target)?.let {
            requested(DebugImeFocusReceipt(handle,it,ticket,runtime.getLong("input_resets"),runtime.optJSONObject("app")?.optJSONObject("input")?.optString("focus_token")))
        }
        if (BuildConfig.DEBUG) Log.i(TAG,"focus_begin explicit target=${targetIdentity(target)} ticket=${inputIntents.get()}")
    } } } }
    /** Explicit debug-only endpoint. Normal UI never calls this or enables the Android backend. */
    fun debugCreateImeEditor(context: Context, preImeKey: (KeyEvent) -> Boolean = { false },
        windowFocusChanged: (Boolean) -> Unit = {}): ImeEditorView? {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!BuildConfig.DEBUG || imeRawBlocked.get()) return null
        val focus = keyboardFocus ?: return null
        if (focus.runtime != handle || editing.get() || panelTextInputs.isNotEmpty() || !activityVisible.get() || xrState.get() != "FOCUSED") return null
        val target = strictImeSdkTarget(JSONObject(focus.target)) ?: return null
        var editorReference = java.lang.ref.WeakReference<ImeEditorView>(null)
        lateinit var connection: ImeSessionConnection
        connection = ImeSessionConnection(focus.runtime, target, focus.epoch,
            requestId = { requests.incrementAndGet().toString() },
            enqueue = { request, command -> enqueueImeCommand(connection, request, command) },
            clock = SystemClock::elapsedRealtime,
            onClosed = { editorReference.get()?.closeEditor() })
        imeAndroidReleased = {
            val editor = editorReference.get()
            editor == null || (!editor.hasFocus() && editor.context.getSystemService(android.view.inputmethod.InputMethodManager::class.java)?.isAcceptingText != true)
        }
        imeConnection = connection
        imeRawBlocked.set(true)
        imeCaptureTicket = inputIntents.incrementAndGet() // Cancel pending automatic focus work, preserving the captured target.
        imeCaptureEpoch = focus.epoch
        imeRawReturn = null
        fun editorAccepts(): Boolean = editorReference.get()?.let { it.hasFocus() && it.hasWindowFocus() } == true
        val view = ImeEditorView(context,
            onTransaction = { editorAccepts() && connection.submit(it) },
            onKeyEvent = { event ->
                val generation = editorReference.get()?.metadata()?.generation
                val captured = generation?.let(connection::keyFocus)
                editorAccepts() && captured != null && sendImeNonTextKey(connection, captured, event)
            },
            onEditorAction = { false },
            onInvalidated = { generation, reason ->
                if (reason == "focus_lost") connection.close() else connection.editorInvalidated(generation)
            },
            onConnectionOpened = connection::openEditor,
            canEditGeneration = { editorAccepts() && connection.acceptsEditor(it) }, preImeKey = preImeKey, windowFocusChanged = windowFocusChanged)
        editorReference = java.lang.ref.WeakReference(view)
        // Backend enable requests urgent reset immediately: a prior Ctrl down must not
        // remain held in Linux while Android is still waiting to create its connection.
        connection.start()
        updateImeGate(connection)
        watchImeDeadline(connection)
        return view
    }

    fun debugCloseImeEditor(restoreRaw: Boolean = false) {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!BuildConfig.DEBUG) return
        val connection = imeConnection ?: return
        if (restoreRaw && imeRawReturn == null) {
            lateinit var returning: ImeRawFocusReturn
            returning = ImeRawFocusReturn(connection.runtime, connection.target, imeCaptureTicket, imeCaptureEpoch,
                keyboardFocus?.token, SystemClock::elapsedRealtime, { requests.incrementAndGet().toString() },
                { request, epoch -> enqueueImeRawFocus(connection, returning, request, epoch) })
            imeRawReturn = returning
        }
        connection.close()
        updateImeGate(connection)
    }
    fun debugImeRawReturnStatus(): ImeRawReturnMetadata? = if (BuildConfig.DEBUG) imeRawReturn?.metadata() else null
    fun debugImeReady(): Boolean = BuildConfig.DEBUG && !debugSdkLease.blocksProduct() && !imeRawBlocked.get() && keyboardFocus?.let {
        it.runtime == handle && !editing.get() && panelTextInputs.isEmpty() && activityVisible.get() && xrState.get() == "FOCUSED"
    } == true
    fun debugImeCanRequestFocus(): Boolean = BuildConfig.DEBUG && !debugSdkLease.blocksProduct() && handle!=0L && !imeRawBlocked.get() &&
        !editing.get() && panelTextInputs.isEmpty() && activityVisible.get() && xrState.get()=="FOCUSED"
    fun debugImeFocusObserved(request: DebugImeFocusReceipt): Boolean {
        if(!debugImeReady() || inputIntents.get()!=request.ticket) return false
        val focus=imeFocus(keyboardFocus) ?: return false
        return focus.runtime==request.runtime && focus.target==request.target && focus.epoch>request.beforeEpoch && focus.token!=request.beforeToken
    }
    fun debugImeFocusedTarget(): ImeSdkTarget? = if (debugImeReady()) keyboardFocus?.let { strictImeSdkTarget(JSONObject(it.target)) } else null
    /** Only an already captured, still live terminal may own an internal window handoff. */
    fun debugImeCaptureCurrent(): Boolean {
        if (!BuildConfig.DEBUG) return false
        val connection = imeConnection ?: return false
        return connection.runtime == handle && inputIntents.get() == imeCaptureTicket && activityVisible.get() &&
            xrState.get() == "FOCUSED" && !editing.get() && panelTextInputs.isEmpty() &&
            inputTargets.values.any { it.runtime == connection.runtime && strictImeSdkTarget(JSONObject(it.target)) == connection.target }
    }
    private fun enqueueImeRawFocus(connection: ImeSessionConnection, returning: ImeRawFocusReturn, request: String, epoch: Long): ImeEnqueueResult {
        if (imeConnection !== connection || imeRawReturn !== returning || !debugImeCaptureCurrent()) return ImeEnqueueResult.UNAVAILABLE
        val target = inputTargets.values.firstOrNull { it.runtime == returning.runtime && it.epoch == epoch &&
            strictImeSdkTarget(JSONObject(it.target)) == returning.target } ?: return ImeEnqueueResult.UNAVAILABLE
        return imeEnqueueCode(NativeBridge.nativeCommand(returning.runtime, JSONObject().put("schema",1)
            .put("type","focus_window").put("request_id",request).put("target",JSONObject(target.target)).put("input_epoch",epoch).toString()))
    }
    private fun updateImeGate(connection: ImeSessionConnection) {
        if (imeConnection !== connection) return
        val metadata = connection.metadata()
        val returning = imeRawReturn
        val blocked = metadata.rawBlocked || !debugImeAndroidReleased() || returning?.blocksRaw() == true
        imeRawBlocked.set(blocked)
        imeInputMessage = when {
            metadata.phase == ImeSessionPhase.BOUND -> InputStatus("IME_CONNECTED", "IME入力の検証中 · Ctrl+Spaceで直接入力へ")
            metadata.phase == ImeSessionPhase.CLOSED && blocked -> InputStatus("IME_RECOVERY", "入力停止 · IME終了または直接入力への復帰を確認中")
            metadata.phase == ImeSessionPhase.CLOSING -> InputStatus("IME_CLOSING", "IME終了処理中 · 直接入力は停止中")
            metadata.phase != ImeSessionPhase.CLOSED -> InputStatus("IME_CONNECTING", "IME入力を接続中 · 直接入力は停止中")
            else -> null
        }
    }

    /** Metadata only: no surrounding/preedit/commit, key events, or private payload strings. */
    fun debugImeStatus(): ImeSessionMetadata? {
        check(Looper.myLooper() == Looper.getMainLooper())
        return if (BuildConfig.DEBUG) imeConnection?.metadata() else null
    }

    /** Android focus/IMM exit is a separate observation from native delivery/cleanup ACKs. */
    fun debugImeAndroidReleased(): Boolean {
        check(Looper.myLooper() == Looper.getMainLooper())
        return imeAndroidReleased?.invoke() ?: true
    }

    private fun watchImeDeadline(connection: ImeSessionConnection) {
        ui.postDelayed({
            if (imeConnection === connection) {
                connection.expire()
                val metadata = connection.metadata()
                imeRawReturn?.expire()
                updateImeGate(connection)
                if (metadata.phase != ImeSessionPhase.CLOSED || imeRawBlocked.get()) watchImeDeadline(connection)
            }
        }, 250)
    }

    private fun invalidateImeConnection() {
        val connection = imeConnection ?: return
        ui.post {
            if (imeConnection === connection) {
                // stop cleanup may already have retired this owner before this UI task runs.
                imeRawBlocked.set(true)
                connection.close()
            }
        }
    }

    private fun strictImeSdkTarget(value: JSONObject): ImeSdkTarget? {
        fun id(key: String) = value.opt(key) as? String
        return ImeSdkTarget(id("session") ?: return null, id("window") ?: return null,
            id("scene") ?: return null, id("generation") ?: return null).takeIf { it.valid() }
    }
    private fun imeSdkJson(target: ImeSdkTarget) = JSONObject().put("session", target.session).put("window", target.window)
        .put("scene", target.scene).put("generation", target.generation)
    private fun imeFocus(value: KeyboardFocus?): ImeSessionFocus? = value?.let {
        val target = strictImeSdkTarget(JSONObject(it.target)) ?: return null
        ImeSessionFocus(it.runtime, it.epoch, target, it.token)
    }
    private fun imeEnqueueCode(code: Int) = when (code) {
        NativeBridge.ACCEPTED -> ImeEnqueueResult.ACCEPTED
        NativeBridge.BUSY -> ImeEnqueueResult.BUSY
        NativeBridge.INVALID_ARGUMENT -> ImeEnqueueResult.INVALID_ARGUMENT
        else -> ImeEnqueueResult.UNAVAILABLE
    }
    private fun imeDeliveryCode(code: String) = when (code) {
        "ACCEPTED" -> ImeDeliveryResult.APPLIED
        "STALE" -> ImeDeliveryResult.STALE
        "NOT_READY" -> ImeDeliveryResult.NOT_READY
        "INVALID_ARGUMENT" -> ImeDeliveryResult.INVALID_ARGUMENT
        else -> ImeDeliveryResult.FAILED
    }
    private fun enqueueImeCommand(connection: ImeSessionConnection, request: String, command: ImeSessionCommand): ImeEnqueueResult {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!BuildConfig.DEBUG || imeConnection !== connection || connection.runtime != handle || handle == 0L) return ImeEnqueueResult.UNAVAILABLE
        val envelope = JSONObject().put("schema", 1).put("request_id", request)
        when (command) {
            is ImeSessionCommand.Backend -> envelope.put("type", "set_ime_backend").put("session", connection.target.session).put("enabled", command.enabled)
            ImeSessionCommand.Reset -> envelope.put("type", "reset_input")
            is ImeSessionCommand.Focus -> {
                val target = inputTargets.values.firstOrNull { it.runtime == connection.runtime && it.epoch == command.epoch &&
                    strictImeSdkTarget(JSONObject(it.target)) == connection.target } ?: return ImeEnqueueResult.UNAVAILABLE
                envelope.put("type", "focus_window").put("target", JSONObject(target.target)).put("input_epoch", command.epoch)
            }
            is ImeSessionCommand.Transaction -> {
                if (!imeFocusCurrent(connection, command.focus)) return ImeEnqueueResult.UNAVAILABLE
                envelope.put("type", "ime").put("target", imeSdkJson(connection.target)).put("transaction", JSONObject(command.payload.toPayloadJson()))
            }
        }
        // This private envelope is consumed synchronously and never retained in diagnostics/ACK maps.
        return imeEnqueueCode(NativeBridge.nativeCommand(connection.runtime, envelope.toString()))
    }
    private fun imeFocusCurrent(connection: ImeSessionConnection, focus: ImeSessionFocus) =
        imeConnection === connection && connection.runtime == handle && focus.target == connection.target &&
            imeFocus(keyboardFocus) == focus && !editing.get() && panelTextInputs.isEmpty() && activityVisible.get() && xrState.get() == "FOCUSED"

    private fun sendImeNonTextKey(connection: ImeSessionConnection, focus: ImeSessionFocus, event: KeyEvent): Boolean {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!imeFocusCurrent(connection, focus) || event.deviceId < 0 || event.isCanceled || event.isSystem ||
            event.action !in setOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP)) return false
        // Text always comes from InputConnection. Only terminal controls/navigation cross this route.
        val controlLetter = event.isCtrlPressed && !event.isAltPressed && !event.isMetaPressed && event.keyCode in KeyEvent.KEYCODE_A..KeyEvent.KEYCODE_Z
        val nonText = event.keyCode in setOf(KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT,
            KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_MOVE_HOME, KeyEvent.KEYCODE_MOVE_END,
            KeyEvent.KEYCODE_PAGE_UP, KeyEvent.KEYCODE_PAGE_DOWN, KeyEvent.KEYCODE_INSERT, KeyEvent.KEYCODE_DEL,
            KeyEvent.KEYCODE_FORWARD_DEL, KeyEvent.KEYCODE_ESCAPE, KeyEvent.KEYCODE_TAB, KeyEvent.KEYCODE_ENTER,
            KeyEvent.KEYCODE_CTRL_LEFT, KeyEvent.KEYCODE_CTRL_RIGHT, KeyEvent.KEYCODE_SHIFT_LEFT, KeyEvent.KEYCODE_SHIFT_RIGHT,
            KeyEvent.KEYCODE_ALT_LEFT, KeyEvent.KEYCODE_ALT_RIGHT)
        // View permits an up only for its previously accepted down; Ctrl may already have lifted.
        val ownedControlUp = event.action == KeyEvent.ACTION_UP && event.keyCode in KeyEvent.KEYCODE_A..KeyEvent.KEYCODE_Z
        if (!nonText && !controlLetter && !ownedControlUp) return false
        val key = JSONObject().put("device", event.deviceId).put("scan_code", event.scanCode).put("key_code", event.keyCode)
            .put("action", if (event.action == KeyEvent.ACTION_DOWN) "pressed" else "released").put("repeat", event.repeatCount)
            .put("meta", event.metaState).put("time_ms", event.eventTime and 0xffffffffL)
        val code = NativeBridge.nativeCommand(focus.runtime, JSONObject().put("schema", 1).put("type", "key")
            .put("request_id", requests.incrementAndGet().toString()).put("target", imeSdkJson(focus.target))
            .put("input_epoch", focus.epoch).put("focus_token", focus.token).put("key", key).toString())
        if (code != NativeBridge.ACCEPTED) connection.close("key_rejected")
        return code == NativeBridge.ACCEPTED
    }

    private fun privateImeSnapshot(runtimeHandle: Long): ImeDecodedSnapshot = try {
        val text = NativeBridge.nativeImeUi(runtimeHandle)
        if (text.length > 65536) ImeDecodedSnapshot(false, null) else {
            val root = JSONObject(text)
            val selected = mutableMapOf<String, Any?>("schema" to root.opt("schema"), "code" to root.opt("code"))
            if (root.has("ime")) {
                val value = root.opt("ime")
                selected["ime"] = when (value) {
                    JSONObject.NULL -> null
                    is JSONObject -> {
                        val target = value.opt("target")
                        mapOf("edit_revision" to value.opt("edit_revision"), "last_sequence" to value.opt("last_sequence"),
                            "target" to if (target is JSONObject) listOf("session", "window", "surface", "focus_token", "input_epoch", "activation", "serial")
                                .associateWith { target.opt(it) } else target)
                    }
                    else -> value
                }
            }
            ImePrivateSnapshot.decode(selected)
        }
    } catch (_: Exception) { ImeDecodedSnapshot(false, null) }

    private fun pumpIme(runtime: JSONObject, completions: List<ImeSessionCompletion>) {
        val connection = imeConnection ?: return
        val focus = imeFocus(keyboardFocus)
        val available = inputTargets.values.any { it.runtime == connection.runtime && strictImeSdkTarget(JSONObject(it.target)) == connection.target }
        val frame = ImeSessionFrame(handle, runtime.getLong("input_resets"), available, focus, privateImeSnapshot(handle))
        val order = ++imeFrameOrder
        ui.post {
            if (imeConnection === connection) {
                connection.observe(order, frame, completions)
                val metadata = connection.metadata()
                imeRawReturn?.observe(ImeRawReturnFrame(frame.runtime, inputIntents.get(), frame.epoch, frame.targetAvailable,
                    frame.focus, metadata.phase == ImeSessionPhase.CLOSED && !metadata.rawBlocked, debugImeAndroidReleased()), completions)
                updateImeGate(connection)
            }
        }
    }

    /** 入口の物理device判定はPhysicalKeyboard。ここは短いnative enqueueだけ。 */
    fun sendPhysicalKey(event: KeyEvent): Boolean = inputIntents.serial {
        inputIntents.invalidate() // 外部キーはcounterの従来仕様を変えずdebug trialを失効させる。
        sendPhysicalKeyLocked(event)
    }
    private fun sendPhysicalKeyLocked(event: KeyEvent): Boolean {
        if (debugSdkLease.blocksProduct()) return false
        if (imeRawBlocked.get()) return false // Android IME owns physical ingress until reset/disable ACKs.
        if(BuildHooks.imeProbeActive()) {BuildHooks.imeProbePhysicalKey();return false}
        val focus = keyboardFocus ?: return false
        if (movingTarget(focus.target)) return false
        if (editing.get() || panelTextInputs.isNotEmpty() || focus.runtime != handle || !activityVisible.get() || xrState.get() != "FOCUSED") return false
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
        if (!probeRecovery.canStartNormal()) {
            ui.post { summary.value = "検証runtimeの停止を確認できません。検証を停止してください" }; return
        }
        handle = NativeBridge.nativeCreate(runtimeConfigJson())
        finishRuntimeStart()
    }
    private fun finishRuntimeStart() {
        check(handle > 0L) { "RUNTIME_CREATE_FAILED" }
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
    /** withStoppedRuntimeの同じworkerから同期呼出し。通常Host/設定への切替は行わない。 */
    fun provideDebugProbeCredential(runtime: Long, capture: ProbeCredentialCapture, event: JSONObject): Int {
        check(BuildConfig.DEBUG && handle == 0L && runtime == capture.runtime) { "PROBE_OWNER_INVALID" }
        return provideCapturedProbeCredential(runtime, capture, event)
    }
    private fun provideCapturedProbeCredential(runtime: Long, capture: ProbeCredentialCapture, event: JSONObject): Int {
        val envelope = JSONObject(NativeBridge.nativeCredentialScope(runtime))
        val fields = envelope.keys().asSequence().associateWith { key ->
            val value = envelope.get(key)
            if (value is JSONObject) value.keys().asSequence().associateWith { value.get(it) } else value
        }
        val supply = capture.claim(runtime, event.opt("session"), event.opt("request_id"), event.opt("profile_id"), fields)
        return checkNotNull(credentials).withPrivateKey { key ->
            NativeBridge.nativeProvideCredentialFor(runtime, supply.session, supply.request, supply.binding, key)
        }
    }

    /** private configはdebug sourceSetの固定準備処理からだけ渡し、通常config/prefsへ保存しない。 */
    class DebugSdkProbeConfig(val configuration: String, val profile: ConnectionProfileV2.Value) {
        override fun toString() = "DebugSdkProbeConfig(<private>)"
    }
    data class DebugSdkProbeReceipt(val runtime: Long, val connectRequest: String?, val phase: DebugSdkRuntimeLease.Phase, val error: String?)
    data class DebugSdkCommandReceipt(val runtime: Long, val request: String?, val code: Int)
    fun debugSdkProbeCommand(owner: Any, type: String, session: String, nonce: String?, completed: (DebugSdkCommandReceipt) -> Unit) {
        val captured = debugSdkLease.metadata()
        if (!BuildConfig.DEBUG || !debugSdkLease.owns(owner) || captured.phase != DebugSdkRuntimeLease.Phase.ACTIVE ||
            type !in setOf("linux_shm_sdk_probe", "disconnect") || !session.matches(Regex("[1-9][0-9]{0,18}")) ||
            session.toLongOrNull() == null || (type == "linux_shm_sdk_probe" && nonce?.matches(Regex("[0-9a-f]{16}")) != true) ||
            (type == "disconnect" && nonce != null)) {
            completed(DebugSdkCommandReceipt(captured.runtime,null,NativeBridge.STALE)); return
        }
        worker.execute {
            if (!debugSdkLease.owns(owner) || debugSdkLease.metadata().let { it.phase != DebugSdkRuntimeLease.Phase.ACTIVE || it.runtime != captured.runtime } || handle != captured.runtime) {
                ui.post { completed(DebugSdkCommandReceipt(captured.runtime,null,NativeBridge.STALE)) }; return@execute
            }
            val request = requests.incrementAndGet().toString()
            val command = JSONObject().put("schema",1).put("type",type).put("request_id",request).put("session",session)
            nonce?.let { command.put("nonce",it) }
            val code = NativeBridge.nativeCommand(handle,command.toString())
            ui.post { completed(DebugSdkCommandReceipt(captured.runtime,request,code)) }
        }
    }
    fun debugSdkProbeStatus(owner: Any): DebugSdkProbeReceipt? = if (BuildConfig.DEBUG && debugSdkLease.owns(owner))
        debugSdkLease.metadata().let { DebugSdkProbeReceipt(it.runtime, null, it.phase, debugSdkFailure) } else null
    private fun registryEmpty(): Boolean = try {
        JSONObject(NativeBridge.nativeSnapshot(0)).getJSONObject("registry").let { ProbeRuntimeRecovery.registryEmpty(it.opt("runtimes"), it.opt("workers")) }
    } catch (_: Exception) { false }
    fun debugBeginSdkProbe(owner: Any, prepare: (String) -> DebugSdkProbeConfig, completed: (DebugSdkProbeReceipt) -> Unit) {
        if (!BuildConfig.DEBUG) { completed(DebugSdkProbeReceipt(0,null,DebugSdkRuntimeLease.Phase.IDLE,"PROBE_DISABLED")); return }
        worker.execute {
            var request: String? = null
            var error: String? = null
            if (!probeRecovery.permits(owner) || !debugSdkLease.claim(owner, handle)) {
                ui.post { completed(DebugSdkProbeReceipt(0,null,DebugSdkRuntimeLease.Phase.IDLE,"PROBE_BUSY")) }; return@execute
            }
            probeRecovery.retain(owner)
            debugSdkFailure = null
            try {
                val previous = handle
                inputIntents.incrementAndGet(); pendingInputFocus = null
                stopRuntime()
                check(debugSdkLease.normalRetired(owner, previous, registryEmpty())) { "PROBE_NORMAL_NOT_RETIRED" }
                val prepared = prepare(runtimeConfigJson())
                check(prepared.profile.backend == ConnectionProfileV2.Backend.LINUX_SSH && prepared.profile.profileId == "linux-probe") { "PROBE_PROFILE_INVALID" }
                handle = NativeBridge.nativeCreate(prepared.configuration)
                if (handle == 0L) {
                    debugSdkLease.createFailed(owner, registryEmpty())
                    throw IllegalStateException("PROBE_CREATE_FAILED")
                }
                check(debugSdkLease.created(owner, handle)) { "PROBE_OWNER_INVALID" }
                request = requests.incrementAndGet().toString()
                debugSdkCapture = ProbeCredentialCapture(handle, checkNotNull(request), prepared.profile)
                finishRuntimeStart()
                check(snapshot().getJSONObject("runtime").getString("state") == "Ready") { "PROBE_START_FAILED" }
                check(NativeBridge.nativeCommand(handle, JSONObject().put("schema",1).put("type","connect_saved_probe")
                    .put("request_id",request).toString()) == NativeBridge.ACCEPTED) { "PROBE_CONNECT_REJECTED" }
            } catch (failure: Exception) {
                error = failure.message?.takeIf { it.matches(Regex("PROBE_[A-Z_]+")) } ?: "PROBE_START_FAILED"
                debugSdkFailure = error
                if (debugSdkLease.metadata().phase == DebugSdkRuntimeLease.Phase.CREATING && handle == 0L)
                    debugSdkLease.createFailed(owner, registryEmpty())
                if (debugSdkLease.metadata().phase != DebugSdkRuntimeLease.Phase.RESTORING) debugSdkLease.cleanupRequired(owner)
            }
            val metadata = debugSdkLease.metadata()
            val receipt = DebugSdkProbeReceipt(metadata.runtime, request, metadata.phase, error)
            ui.post { completed(receipt) }
        }
    }
    fun debugEndSdkProbe(owner: Any, verifyRestoration: () -> Boolean = {true}, completed: (DebugSdkProbeReceipt) -> Unit) {
        if (!BuildConfig.DEBUG) { completed(DebugSdkProbeReceipt(0,null,DebugSdkRuntimeLease.Phase.IDLE,"PROBE_DISABLED")); return }
        worker.execute {
            if (!debugSdkLease.owns(owner)) {
                ui.post { completed(DebugSdkProbeReceipt(0,null,DebugSdkRuntimeLease.Phase.IDLE,"PROBE_STALE")) }; return@execute
            }
            var error: String? = null
            try {
                debugSdkCapture?.close(); debugSdkCapture = null
                val before = debugSdkLease.metadata()
                if (before.phase != DebugSdkRuntimeLease.Phase.RESTORING || before.runtime != 0L) {
                    val retiring = checkNotNull(debugSdkLease.beginClose(owner))
                    check(handle == retiring) { "PROBE_OWNER_INVALID" }
                    stopRuntime()
                    check(debugSdkLease.retired(owner, retiring, registryEmpty())) { "PROBE_CLEANUP_UNCONFIRMED" }
                } else check(handle == 0L && registryEmpty()) { "PROBE_CLEANUP_UNCONFIRMED" }
                handle = NativeBridge.nativeCreate(runtimeConfigJson())
                check(debugSdkLease.restoreCreated(owner, handle)) { "PROBE_RESTORE_CREATE_FAILED" }
                finishRuntimeStart()
                val normal = snapshot().getJSONObject("runtime")
                check(verifyRestoration()) { "PROBE_CONFIGURATION_CHANGED" }
                check(normal.getString("state") == "Ready" && normal.getJSONObject("transport").getString("state") == "Idle") { "PROBE_RESTORE_NOT_READY" }
                // SDKの可視draw証拠をUIが捕捉するまでRESTORING/ownerを保持する。
            } catch (failure: Exception) {
                error = failure.message?.takeIf { it.matches(Regex("PROBE_[A-Z_]+")) } ?: "PROBE_CLEANUP_UNCONFIRMED"
                debugSdkFailure = error; debugSdkLease.cleanupRequired(owner)
            }
            val receipt = DebugSdkProbeReceipt(handle, null, debugSdkLease.metadata().phase, error)
            ui.post { completed(receipt) }
        }
    }
    fun debugFinalizeSdkProbe(owner: Any, runtime: Long, scene: String, target: JSONObject, completed: (DebugSdkProbeReceipt) -> Unit) {
        val capturedTarget = target.toString()
        if (!BuildConfig.DEBUG) { completed(DebugSdkProbeReceipt(0,null,DebugSdkRuntimeLease.Phase.IDLE,"PROBE_DISABLED")); return }
        worker.execute {
            var error: String? = null
            try {
                check(debugSdkLease.owns(owner) && debugSdkLease.metadata().let {it.phase==DebugSdkRuntimeLease.Phase.RESTORING && it.runtime==runtime} && handle==runtime) {"PROBE_STALE"}
                check(activityVisible.get() && xrState.get()=="FOCUSED" && scenes.get().toString()==scene) {"PROBE_RESTORE_NOT_VISIBLE"}
                val normal=snapshot().getJSONObject("runtime")
                val wanted=JSONObject(capturedTarget)
                val frames=normal.getJSONObject("render").getJSONArray("targets")
                check(normal.getString("state")=="Ready" && normal.getJSONObject("transport").getString("state")=="Idle" &&
                    normal.getBoolean("activity_visible") && normal.getString("xr_state")=="FOCUSED" && normal.optString("scene")==scene &&
                    wanted.getString("scene")==scene && frames.length()==1 && frames.getJSONObject(0).getString("role")=="diagnostic" &&
                    targetIdentity(frames.getJSONObject(0).getJSONObject("key"))==targetIdentity(wanted) && frames.getJSONObject(0).getLong("frames")>=3 &&
                    normal.getJSONObject("shutdown").getInt("sdk_bindings")==1) {"PROBE_RESTORE_NOT_READY"}
                check(debugSdkLease.restored(owner,runtime,true)) {"PROBE_STALE"}
                probeRecovery.release(owner);debugSdkFailure=null
            } catch(failure:Exception) {
                error=failure.message?.takeIf {it.matches(Regex("PROBE_[A-Z_]+"))} ?: "PROBE_RESTORE_FAILED"
                if(debugSdkLease.owns(owner)) {debugSdkFailure=error;debugSdkLease.cleanupRequired(owner)}
            }
            val receipt=DebugSdkProbeReceipt(handle,null,debugSdkLease.metadata().phase,error)
            ui.post {completed(receipt)}
        }
    }
    fun sdkBinding(target: JSONObject, runtime: Long, owned: Boolean): Int = NativeBridge.nativeCommand(runtime,
        JSONObject().put("schema",1).put("type",if(owned) "register_sdk_binding" else "release_sdk_binding")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target).toString())
    private fun stopRuntime() {
        if (handle == 0L) return
        invalidateImeConnection()
        keyboardFocus = null
        inputTargets = emptyMap()
        windowMove.set(null)
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
        val endedIme = imeConnection
        ui.post { if (imeConnection === endedIme) { endedIme?.close(); imeConnection = null; imeAndroidReleased = null; imeRawReturn = null; imeInputMessage = null; imeRawBlocked.set(false) } }
        catalogPending=null;launcherUpdate(LauncherUi(path=launcherState.path))
        poseCompletions.clear();selectedWindow=null;lastFocusedWindow=null;editing.set(false);ui.post { layoutEditing.value=false }
        clearTerminalOpen(); terminalAdding = null; terminalError = null
        lastConnectRequest = null; pendingDisconnect = null; connectPending = false
        Log.i(TAG, "runtime_stopped handle=$old duration_ms=${SystemClock.elapsedRealtime() - started}")
    }
    /** 診断の排他的実行。固定caseの実装自体はdebug sourceSetだけに置く。 */
    fun withStoppedRuntime(action: (DiagnosticsStore) -> Unit, completed: () -> Unit) =
        exclusiveRuntime(null, action, completed) { true }
    /** Debug固定caseのowner回収が確認されるまで、他case/通常runtime再作成を抑止する。 */
    fun withStoppedRuntimeGuarded(owner: Any, action: (DiagnosticsStore) -> Unit, completed: () -> Unit, canRestart: () -> Boolean) {
        check(BuildConfig.DEBUG)
        exclusiveRuntime(owner, action, completed, canRestart)
    }
    private fun exclusiveRuntime(owner: Any?, action: (DiagnosticsStore) -> Unit, completed: () -> Unit, canRestart: () -> Boolean) {
        worker.execute {
            if (!probeRecovery.permits(owner)) { ui.post(completed); return@execute }
            try {
                stopRuntime()
                action(checkNotNull(store))
            } catch (failure: Exception) {
                Log.e(TAG, "exclusive_failure class=${failure.javaClass.simpleName}")
            } finally {
                val recovered = runCatching(canRestart).getOrDefault(false)
                if (owner != null) { if (recovered) probeRecovery.release(owner) else probeRecovery.retain(owner) }
                safely { if (handle == 0L && recovered) startRuntime() }
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
        val imeCompletions = mutableListOf<ImeSessionCompletion>()
        for (index in 0 until values.length()) {
            val event = values.getJSONObject(index)
            if (event.optString("type") == "command_complete") {
                imeCompletions.add(ImeSessionCompletion(event.getString("request_id"), imeDeliveryCode(event.getString("code"))))
                catalogPending?.let { pending ->
                    if(event.getString("request_id")==pending.request && event.getString("code")!="ACCEPTED") {
                        catalogPending=null;launcherUpdate(launcherState.copy(busy=false,message="一覧を操作できません（${event.getString("code")}）"))
                    }
                }
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
                        launcherUpdate(launcherState.copy(message=checkNotNull(terminalError)))
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
            if (event.optString("type") == "credential_required" && debugSdkLease.blocksProduct()) {
                try {
                    val capture = checkNotNull(debugSdkCapture)
                    check(debugSdkLease.metadata().phase == DebugSdkRuntimeLease.Phase.ACTIVE && capture.runtime == handle)
                    check(provideCapturedProbeCredential(handle, capture, event) == NativeBridge.ACCEPTED)
                } catch (_: Exception) { debugSdkFailure = "PROBE_CREDENTIAL_REJECTED" }
            } else if (event.optString("type") == "credential_required" && event.optString("profile_id") == "termux-local" &&
                event.getString("request_id").toLong() > cancelledThrough) {
                val code = checkNotNull(credentials).provide(handle, event.getString("session").toLong(), event.getString("request_id").toLong())
                if (code != NativeBridge.ACCEPTED) Log.w(TAG, "credential_rejected code=$code")
            }
        }
        pumpDisconnect()
        val snapshot = snapshot()
        val runtime = snapshot.getJSONObject("runtime")
        pumpCatalog(runtime)
        inputIntents.serial {
        pendingInputFocus?.let { request ->
            val target=JSONObject(request.target)
            val plans=runtime.getJSONArray("panels")
            val current=(0 until plans.length()).map {plans.getJSONObject(it)}.firstOrNull {p ->
                p.optString("action")=="create" && p.isNull("error") && targetIdentity(p.getJSONObject("target"))==targetIdentity(target)
            }?.getJSONObject("target")
            if(SystemClock.elapsedRealtime()>request.deadline || editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED" ||
                request.runtime!=handle || request.ticket!=inputIntents.get() ||
                current==null || targetIdentity(current)!=targetIdentity(target) ||
                request.controlsRevision?.let {runtime.getJSONObject("render").optLong("theme_revision",0)!=it}==true) {
                if (BuildConfig.DEBUG) Log.i(TAG,"focus_cancel expired=${SystemClock.elapsedRealtime()>request.deadline} editing=${editing.get()} visible=${activityVisible.get()} xr=${xrState.get()} runtime=${request.runtime==handle} ticket=${request.ticket==inputIntents.get()} target=${current!=null}")
                pendingInputFocus=null
            }
            else if(request.waitForIme && imeRawBlocked.get()) {
                // A window control may dismiss IME ownership, but never races its reset/disable cleanup.
            } else if(request.resetRequest==null) {
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
                    val envelope=JSONObject().put("schema",1).put("type",if(request.controlsRevision==null) "focus_window" else "focus_window_control")
                        .put("request_id",focusRequest).put("target",target).put("input_epoch",runtime.getLong("input_resets"))
                    request.controlsRevision?.let {envelope.put("controls_revision",it)}
                    val code=NativeBridge.nativeCommand(handle,envelope.toString())
                    request.controlsRevision?.let {BuildHooks.windowControlRequested("focus",request.runtime,request.target,it,focusRequest,code)}
                    when(code) {
                        NativeBridge.ACCEPTED -> {request.focusRequest=focusRequest;if (BuildConfig.DEBUG) Log.i(TAG,"focus_enqueue request=$focusRequest epoch=${runtime.getLong("input_resets")}")}
                        NativeBridge.BUSY -> Unit
                        else -> pendingInputFocus=null
                    }
                }
            }
        }
        }
        val focusedWindow = runtime.optJSONObject("app")?.optJSONObject("input")?.optString("focus_window")?.takeIf { it.toLongOrNull()!=null }
        if(focusedWindow!=null && (focusedWindow!=lastFocusedWindow || selectedWindow?.first!=runtime.getJSONObject("app").getString("session"))) selectedWindow=runtime.getJSONObject("app").getString("session") to focusedWindow
        lastFocusedWindow=focusedWindow
        val plans = runtime.getJSONArray("panels")
        val renderTargets=runtime.getJSONObject("render").getJSONArray("targets")
        inputTargets=if (!debugSdkLease.blocksProduct() && !editing.get() && runtime.getString("state")=="Ready" && runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED")
            (0 until plans.length()).map { plans.getJSONObject(it) }.filter { plan ->
                plan.optString("action")=="create" && plan.optBoolean("applied") && plan.isNull("error") && !movingTarget(plan.getJSONObject("target").toString()) &&
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
        keyboardFocus = if (!debugSdkLease.blocksProduct() && !editing.get() && runtime.getString("state") == "Ready" && runtime.getBoolean("activity_visible") &&
            runtime.getString("xr_state") == "FOCUSED" && focused != null && !movingTarget(focused.getJSONObject("target").toString()))
            KeyboardFocus(handle, runtime.getLong("input_resets"), focused.getJSONObject("target").toString(), runtime.getJSONObject("app").getJSONObject("input").getString("focus_token")) else null
        pumpIme(runtime, imeCompletions)
        val keyboardLabel = if (keyboardFocus == null) "操作パネルへ入力" else "ターミナルへ入力"
        ui.post { keyboardSummary.value = keyboardLabel }
        val inputConditions=InputConditions(runtime.getBoolean("activity_visible"),runtime.getString("xr_state")=="FOCUSED",
            runtime.getString("state")=="Ready",if(terminalOpen?.startupWaiting==true) "StartingTermux" else runtime.getJSONObject("transport").getString("state"),editing.get(),keyboardCount.get(),
            (runtime.optJSONObject("app")?.optJSONArray("windows")?.length() ?: 0)>0,inputTargets.isNotEmpty(),
            focusedWindow.takeIf {keyboardFocus!=null},panelEditing=panelTextInputs.isNotEmpty(),imeProbe=BuildHooks.imeProbeActive())
        val status=if(debugSdkLease.blocksProduct()) InputStatus("FIXED_LINUX_PROBE","Linux描画の固定検査中 · 通常操作と文字入力を停止")
        else if(inputConditions.visible && inputConditions.xrFocused) {
            val move=windowMove.get()
            if(move!=null) InputStatus("WINDOW_MOVING","窓 ${JSONObject(move.control.target).getString("window")} を移動中 · その窓の文字入力を停止")
            else imeInputMessage ?: InputStatus.from(inputConditions)
        } else InputStatus.from(inputConditions)
        ui.post {inputStatus.value=status}
        store?.updateInterface(JSONObject().put("input_reason",status.reason).put("input_action",status.action.name)
            .put("editing",editing.get()).put("keyboard_count",keyboardCount.get() ?: JSONObject.NULL)
            .put("focus_window",focusedWindow ?: JSONObject.NULL).put("device",latestMetrics.get().json())
            .put("keyboard_ingress",KeyboardIngress.json())
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
            val receipt=runtime.getJSONObject("transport").optJSONObject("app_forward")?.optJSONObject("remote")?.optJSONObject("last_terminal")
            val owned=runtime.optJSONObject("window_launch")?.takeIf {it.optString("session")==operation.session && it.optString("request_id")==operation.request}
            if(receipt?.optString("request_id")==operation.request && !receipt.optBoolean("success")) {
                terminalError=LauncherMessages.error(receipt.optString("error"));terminalAdding=null
                launcherUpdate(launcherState.copy(message=checkNotNull(terminalError)))
            } else if(currentApp?.optString("session")!=operation.session) terminalAdding=null
            else if(owned?.optString("phase")=="matched" && owned.optString("window") !in operation.windows && choices.any {it.window==owned.optString("window")}) {
                terminalAdding=null;launcherUpdate(launcherState.copy(message="ウィンドウを開きました"))
            }
            else if(SystemClock.elapsedRealtime()>operation.deadline) {
                terminalError="ウィンドウの追加を確認できません。接続を閉じて再試行してください";terminalAdding=null
                launcherUpdate(launcherState.copy(message=checkNotNull(terminalError)))
            }
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
