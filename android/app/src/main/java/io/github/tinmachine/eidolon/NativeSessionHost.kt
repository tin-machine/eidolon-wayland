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
    @Volatile private var debugSdkMode = "pattern"
    @Volatile private var debugSdkRemoteStarted=false
    @Volatile private var debugSdkInputScope: DebugSdkInputScope? = null
    private var identity: CredentialStore.Identity? = null
    private var profiles = JSONArray()
    private var selectedProfileId = "termux-local" // 保存された選択だけ。接続/起動時の暗黙切替は禁止。
    private var linuxCredentials: CredentialStore? = null
    private var linuxIdentity: CredentialStore.Identity? = null
    private var linuxProfileStore: LinuxProfileStore? = null
    private var linuxPinsStore: LinuxDesktopPinsStore? = null
    private var linuxProfile: ConnectionProfileV2.Value? = null
    private var linuxSavedPins: LinuxDesktopPinsRepository.Saved? = null
    private var linuxCredentialCapture: LinuxCredentialCapture? = null
    private var linuxCatalog: LinuxDesktopCatalog? = null
    private data class LinuxAction(val kind: String, val session: String, val request: String,
        val deadline: Long = SystemClock.elapsedRealtime()+30000)
    private var linuxAction: LinuxAction? = null
    private var linuxMessage: String? = null
    val selectedConnection = mutableStateOf("termux-local")
    val connectionTarget = mutableStateOf("このQuestのTermux")
    val canSelectLinuxRecovery = mutableStateOf(false)
    private var lastConnectRequest: String? = null // connectとcancelをworker上の順序で束縛する。
    private var connectPending = false
    private var cancelledThrough = 0L
    private data class PendingDisconnect(val connectRequest: String?, val session: String?, var request: String? = null)
    private var pendingDisconnect: PendingDisconnect? = null
    private data class KeyboardFocus(val runtime: Long, val epoch: Long, val target: String, val token: String)
    @Volatile private var keyboardFocus: KeyboardFocus? = null
    @Volatile private var imeConnection: ImeSessionConnection? = null // Methods belong to UI; worker only captures this identity.
    class ImeEditorOwner internal constructor(internal val connection:ImeSessionConnection,internal val ticket:Long,internal val token:String) {
        val runtime:Long get()=connection.runtime
        val target:ImeSdkTarget get()=connection.target
        internal var attached=false
        internal var view=java.lang.ref.WeakReference<ImeEditorView>(null)
    }
    private var imeOwner:ImeEditorOwner?=null
    val imeAvailable=mutableStateOf(false)
    private val imeRawBlocked = AtomicBoolean(false)
    private var imeCaptureTicket = 0L // UI owner, invalidated by any external input intention.
    private var imeCaptureEpoch = 0L
    private var imeRawReturn: ImeRawFocusReturn? = null
    @Volatile private var imeInputMessage: InputStatus? = null
    private var imeFrameOrder = 0L // Worker-to-UI ordered packet sequence.
    private var imeAndroidReleased: (() -> Boolean)? = null // UI only; captures a WeakReference, never an Activity.
    data class InputTarget(val runtime: Long, val epoch: Long, val identity: String, val target: String)
    @Volatile private var inputTargets: Map<String, InputTarget> = emptyMap()
    private val groupInputGate=AtomicReference(WindowGroupInputGate.unavailable())
    private val groupGeometryCompletions=ConcurrentHashMap<String,String>()
    private fun targetIdentity(target: JSONObject): String = listOf("session","window","scene","generation").joinToString(":") { target.getString(it) }
    val keyboardSummary = mutableStateOf("操作パネルへ入力")
    val inputStatus = mutableStateOf(InputStatus("PREPARING","画面を準備中"))
    private val keyboardCount = AtomicReference<Int?>(null)
    val deviceMetrics = mutableStateOf(DeviceMetricsSnapshot())
    private val latestMetrics = AtomicReference(DeviceMetricsSnapshot())
    fun setKeyboardCount(value: Int?) { keyboardCount.set(value) }
    fun publishDeviceMetrics(value: DeviceMetricsSnapshot) { latestMetrics.set(value);deviceMetrics.value=value }
    private val inputIntents=InputIntentSerial(SystemClock::elapsedRealtime)
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
        if(handle==0L || catalogPending!=null || selectedProfileId!="termux-local") return
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
        if(selectedProfileId==LinuxConnectionProfile.ID) { pumpLinuxCatalog(runtime); return }
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
    fun launchRegistered(session: String, revision: String, id: String, requested: ((String, Int) -> Unit)? = null) {
        if(launcherUi.value.linux) launchLinuxDesktop(session,revision,id,requested)
        else launchCatalogTerminal(session,JSONObject().put("kind","registered").put("revision",revision).put("id",id),requested)
    }
    fun launchFile(form: LauncherForm, requested: ((String, Int) -> Unit)? = null) {
        if(form.valid()) launchCatalogTerminal(launcherUi.value.session,JSONObject().put("kind","temporary").put("definition",form.definition()), requested)
    }
    private fun launchCatalogTerminal(expectedSession: String, target: JSONObject, requested: ((String, Int) -> Unit)? = null) {
        val payload=target.toString()
        productWorker.execute { safely {
            if(handle==0L || selectedProfileId!="termux-local" || terminalAdding!=null || terminalOpen!=null || pendingDisconnect!=null) return@safely
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
    data class LinuxConnectionUi(val publicKey:String="",val displayName:String="Linux desktop",val host:String="",val port:String="22",
        val username:String="",val hostKey:String="",val savedBinding:String="",val pinsJson:String="",val pinsToken:String="",
        val pinsReady:Boolean=false,val canSave:Boolean=false,val message:String="設定を読み込み中") {
        override fun toString() = "LinuxConnectionUi(<private>)"
    }
    val linuxConnectionUi=mutableStateOf(LinuxConnectionUi())
    private var connectionEditable=false

    private fun refreshConnection(message:String) {
        val profile=profiles.optJSONObject(0)?.let {ConnectionProfile.parse(it.getString("username"),it.getString("host_key"))}
        val publicKey=identity?.publicKey ?: ""
        val editable=connectionEditable
        ui.post {connectionUi.value=ConnectionUi(publicKey,profile?.username ?: "",profile?.hostKey ?: "",profile?.token ?: "",editable,message)}
    }
    private fun canChangeConnection(runtime:JSONObject):Boolean = ConnectionAdmission.canChange(runtime,
        debugSdkLease.blocksProduct() || connectPending || pendingDisconnect!=null || terminalOpen!=null || linuxAction!=null)
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

    private fun linuxPins(): LinuxDesktopPins? = linuxSavedPins?.forProfile(linuxProfile)
    private fun canSelectRecovery(runtime:JSONObject):Boolean {
        return selectedProfileId!=LinuxConnectionProfile.ID && linuxProfile!=null && linuxPins()!=null &&
            ConnectionAdmission.canSelectRecovery(runtime,
                debugSdkLease.blocksProduct() || connectPending || pendingDisconnect!=null || terminalOpen!=null)
    }
    private fun refreshLinuxConnection(message:String) {
        val profile=linuxProfile
        val value=LinuxConnectionUi(linuxIdentity?.publicKey ?: "",profile?.displayName ?: "Linux desktop",profile?.host ?: "",
            profile?.port?.toString() ?: "22",profile?.username ?: "",profile?.hostKey ?: "",profile?.binding() ?: "",
            linuxSavedPins?.pins?.json() ?: "",linuxSavedPins?.token ?: "",linuxPins()!=null,connectionEditable,message)
        val target=if(selectedProfileId==LinuxConnectionProfile.ID) profile?.let {"${it.displayName} · ${it.host}:${it.port}"} ?: "別のLinux（未設定）" else "このQuestのTermux"
        val selected=selectedProfileId
        ui.post {linuxConnectionUi.value=value;selectedConnection.value=selected;connectionTarget.value=target}
    }
    fun selectConnection(profileId:String) { productWorker.execute { safely {
        if(profileId !in setOf("termux-local",LinuxConnectionProfile.ID) || profileId==selectedProfileId) return@safely
        val runtime=if(handle!=0L) snapshot().getJSONObject("runtime") else null
        if(runtime==null || !(canChangeConnection(runtime) || profileId==LinuxConnectionProfile.ID && canSelectRecovery(runtime))) {
            refreshLinuxConnection("前の接続とウィンドウの終了を確認してから切り替えてください。");return@safely
        }
        // 保存・起動・再接続は行わず、次の明示操作で使う接続先だけを変更する。
        check(checkNotNull(application).getSharedPreferences("connections",Context.MODE_PRIVATE).edit().putString("selected",profileId).commit())
        selectedProfileId=profileId;linuxCatalog=null;linuxMessage=null;catalogPending=null
        launcherUpdate(LauncherUi(path=launcherState.path,linux=profileId==LinuxConnectionProfile.ID))
        refreshLinuxConnection("接続先を選択しました。設定を確認して接続してください。")
    } } }
    fun saveLinuxConnection(draft:LinuxConnectionDraft,expectedBinding:String,confirmed:Boolean) { productWorker.execute {
        try {
            check(handle!=0L && canChangeConnection(snapshot().getJSONObject("runtime"))) {"CONNECTION_BUSY"}
            require(linuxProfile?.binding()==expectedBinding.ifEmpty {null}) {"PROFILE_CHANGED"}
            LinuxConnectionProfile.propose(draft,checkNotNull(identity).installation,linuxProfile)
            require(confirmed) {"PIN_CONFIRMATION_REQUIRED"}
            stopRuntime()
            linuxProfile=checkNotNull(linuxProfileStore).save(draft,expectedBinding.ifEmpty {null},confirmed)
            linuxCatalog=null
            refreshLinuxConnection("接続先を保存しました。転送用ファイルの確認情報も、この接続先に対して確認・保存してください。")
        } catch(_:Exception) {
            // publish後のreadback失敗も古いbindingに戻さず、保存済み状態を読み直す。
            linuxProfile=null;runCatching {linuxProfile=checkNotNull(linuxProfileStore).load()}
            refreshLinuxConnection("保存できませんでした。終了状態と接続先・ホスト鍵を確認し、設定を開き直してください。")
        } finally {safely {if(handle==0L) startRuntime()}}
    } }
    fun saveLinuxPins(raw:String,expectedBinding:String,expectedToken:String,confirmed:Boolean) { productWorker.execute {
        try {
            check(handle!=0L && canChangeConnection(snapshot().getJSONObject("runtime"))) {"CONNECTION_BUSY"}
            val profile=checkNotNull(linuxProfile)
            require(profile.binding()==expectedBinding) {"PROFILE_CHANGED"}
            LinuxDesktopPins.parse(raw);require(confirmed) {"PIN_CONFIRMATION_REQUIRED"}
            stopRuntime()
            linuxSavedPins=checkNotNull(linuxPinsStore).save(profile,raw,expectedToken.ifEmpty {null},confirmed)
            linuxCatalog=null
            refreshLinuxConnection("転送用ファイルの確認情報を保存しました。明示的に接続して、利用できるアプリを確認できます。")
        } catch(_:Exception) {
            linuxSavedPins=null;runCatching {linuxSavedPins=checkNotNull(linuxPinsStore).load()}
            refreshLinuxConnection("保存できませんでした。接続先と、固定builderから取得した確認情報を確認してください。")
        } finally {safely {if(handle==0L) startRuntime()}}
    } }
    fun inspectLinuxDesktop(requested:((String,Int)->Unit)?=null) { linuxDesktopAction("inspect",requested) }
    fun recoverLinuxDesktop() { linuxDesktopAction("recover") }
    private fun linuxDesktopAction(kind:String,requested:((String,Int)->Unit)?=null) { productWorker.execute { safely {
        if(handle==0L || selectedProfileId!=LinuxConnectionProfile.ID || linuxAction!=null || pendingDisconnect!=null) {
            requested?.invoke("",NativeBridge.NOT_READY);return@safely
        }
        val transport=snapshot().getJSONObject("runtime").getJSONObject("transport")
        if(transport.optString("profile_id")!=LinuxConnectionProfile.ID || transport.optInt("owned_connections")!=1) {
            requested?.invoke("",NativeBridge.NOT_READY);return@safely
        }
        val permitted=when(kind) {
            "inspect" -> transport.optString("state")=="ProbeReady" && !transport.optBoolean("desktop_pending")
            "recover" -> transport.optString("state")=="CleanupPending" && transport.optBoolean("desktop_pending") && transport.isNull("desktop")
            else -> false
        }
        if(!permitted) {requested?.invoke("",NativeBridge.NOT_READY);return@safely}
        val session=transport.getString("session");val request=requests.incrementAndGet().toString()
        val code=NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","linux_desktop_$kind")
            .put("request_id",request).put("session",session).toString())
        if(code==NativeBridge.ACCEPTED) {
            linuxAction=LinuxAction(kind,session,request);linuxCatalog=null;linuxMessage=null
        } else linuxMessage="操作を開始できません。接続状態を確認してください。"
        requested?.invoke(request,code)
    } } }
    private fun launchLinuxDesktop(session:String,revision:String,id:String,requested:((String,Int)->Unit)?) { productWorker.execute { safely {
        if(handle==0L || selectedProfileId!=LinuxConnectionProfile.ID || linuxAction!=null || pendingDisconnect!=null || connectPending) {
            requested?.invoke("",NativeBridge.NOT_READY);return@safely
        }
        val catalog=linuxCatalog
        val runtime=snapshot().getJSONObject("runtime");val transport=runtime.getJSONObject("transport")
        if(catalog?.accepts(session,revision,id)!=true || transport.optString("session")!=session ||
            transport.optString("profile_id")!=LinuxConnectionProfile.ID || transport.optString("state")!="DesktopReady" ||
            (runtime.optJSONObject("app")?.optString("state") ?: "stopped")!="stopped") {
            linuxMessage="接続先とアプリ一覧を確認してから起動してください。";requested?.invoke("",NativeBridge.STALE);return@safely
        }
        releasePanelTextFocus()
        val request=requests.incrementAndGet().toString()
        val code=NativeBridge.nativeCommand(handle,JSONObject().put("schema",1).put("type","linux_desktop_launch")
            .put("request_id",request).put("session",session).put("selection",catalog.selection()).toString())
        if(code==NativeBridge.ACCEPTED) {linuxAction=LinuxAction("launch",session,request);linuxMessage=null}
        else linuxMessage="起動を開始できません。接続と前回の終了状態を確認してください。"
        requested?.invoke(request,code)
    } } }
    private fun pumpLinuxCatalog(runtime:JSONObject) {
        val transport=runtime.getJSONObject("transport");val phase=transport.optString("state")
        val session=transport.optString("session").takeIf {transport.optString("profile_id")==LinuxConnectionProfile.ID} ?: ""
        val owned=transport.optInt("owned_connections");val pending=transport.optBoolean("desktop_pending")
        val app=runtime.optJSONObject("app")?.takeIf {it.optString("mode")=="linux_desktop" && it.optString("session")==session}
        val running=app?.optString("state")=="running"
        val appActive=app!=null && app.optString("state")!="stopped"
        if(linuxCatalog?.session!=session || phase!="DesktopReady") linuxCatalog=null
        val envelope=JSONObject(NativeBridge.nativeCatalogUi(handle)) // 設定/UIのみ。通常snapshotへ本文を入れない。
        val catalog=envelope.optJSONObject("catalog")?.takeIf {envelope.optInt("code")==NativeBridge.ACCEPTED && it.optString("session")==session}
        linuxCatalog=if(phase=="DesktopReady" && catalog!=null && !catalog.optBoolean("pending") && catalog.isNull("error"))
            catalog.optJSONObject("desktop")?.let {runCatching {LinuxDesktopCatalog.decode(session,it)}.getOrNull()} else null
        linuxAction?.let { action ->
            val completed=when(action.kind) {
                "inspect" -> linuxCatalog!=null
                "launch" -> running
                "recover" -> !pending && phase=="ProbeReady"
                else -> false
            }
            val catalogFailed=action.kind=="inspect" && catalog?.optString("request_id")==action.request &&
                !catalog.optBoolean("pending") && !catalog.isNull("error")
            if(completed) {linuxAction=null;linuxMessage=null}
            else if(action.session!=session || phase in setOf("Disconnected","Failed") || catalogFailed || SystemClock.elapsedRealtime()>action.deadline) {
                linuxAction=null
                linuxMessage=if(pending) "終了を確認できていません。同じ接続先へ接続し、前回の回収を実行してください。"
                    else "操作の完了を確認できません。接続状態を確認して明示的にやり直してください。"
            }
        }
        val busy=connectPending || pendingDisconnect!=null || linuxAction!=null
        val configured=linuxIdentity!=null && linuxProfile?.let {LinuxConnectionProfile.endpointSupport(it)==LinuxConnectionProfile.EndpointSupport.IPV4}==true && linuxPins()!=null
        val canConnect=configured && ConnectionAdmission.canConnectLinux(runtime,busy)
        val canInspect=!busy && owned==1 && phase=="ProbeReady" && !pending
        val canRecover=!busy && owned==1 && phase=="CleanupPending" && pending && !appActive && transport.isNull("desktop")
        val canStop=pendingDisconnect==null && (owned>0 || connectPending || appActive)
        val shown=running && app?.optBoolean("remote_gui_verified")==true && runtime.getJSONArray("panels").let {plans -> (0 until plans.length()).any {i ->
            val plan=plans.getJSONObject(i)
            plan.optString("action")=="create" && plan.optBoolean("applied") && plan.isNull("error") &&
                plan.getJSONObject("target").optString("session")==session && runtime.getJSONObject("render").getJSONArray("targets").let {targets ->
                    (0 until targets.length()).any {j -> val target=targets.getJSONObject(j)
                        targetIdentity(target.getJSONObject("key"))==targetIdentity(plan.getJSONObject("target")) && target.optLong("frames")>0 }
                }
        } }
        val message=linuxMessage ?: when {
            linuxAction?.kind=="recover" -> "前回のLinuxアプリと転送処理の終了を確認中"
            linuxAction?.kind=="inspect" -> "転送用ファイルと固定GTKデモを接続先で確認中"
            linuxAction?.kind=="launch" || appActive && !shown -> "GTKデモの画面を準備中"
            shown -> "GTKデモを表示中 · 英数入力・Dialog・Closeを使えます"
            pending && phase=="CleanupPending" -> "前回の終了が未確認です。同じ接続先へ接続して回収してください。"
            linuxCatalog!=null -> "確認済み: 固定GTKデモ（Wayland・1アプリ）· 選択すると起動します"
            phase=="ProbeReady" -> "SSH認証済み · 「アプリを確認」で利用できる機能を照合してください"
            !configured -> "接続設定でIPv4接続先・ホスト鍵・転送用ファイルを確認してください"
            pending && owned==0 -> "前回の終了が未確認です。同じ接続先へ接続して回収してください。"
            phase in setOf("Connecting","AwaitingCredential","Authenticating") -> "Linuxへ接続中"
            else -> "接続してアプリを確認してください。起動や再接続は自動では行いません。"
        }
        val selection=linuxCatalog
        val value=LauncherUi(session=session,connected=phase=="DesktopReady" && selection!=null,busy=busy,
            revision=selection?.revision ?: "",builtin=true,total=if(selection!=null) 1 else 0,path=launcherState.path,
            entries=if(selection!=null) listOf(LauncherEntry(LinuxDesktopCatalog.APP_ID,"GTKデモ","builtin:script",!busy && !appActive,null)) else emptyList(),
            message=message,linux=true,canConnect=canConnect,canInspect=canInspect,canRecover=canRecover,canStop=canStop,running=running)
        if(value!=launcherState) launcherUpdate(value)
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
                val linuxLoaded=runCatching {
                    if(linuxCredentials==null) linuxCredentials=CredentialStore(application,"eidolon.identity.remote-linux.aes.v1",
                        java.io.File(application.noBackupFilesDir,"credentials-remote-linux"))
                    linuxIdentity=null;linuxProfile=null;linuxSavedPins=null
                    linuxIdentity=checkNotNull(linuxCredentials).ensureIdentity()
                    if(linuxProfileStore==null) linuxProfileStore=LinuxProfileStore(application,checkNotNull(identity).installation)
                    if(linuxPinsStore==null) linuxPinsStore=LinuxDesktopPinsStore(application)
                    linuxProfile=checkNotNull(linuxProfileStore).load();linuxSavedPins=checkNotNull(linuxPinsStore).load()
                }.isSuccess
                selectedProfileId=application.getSharedPreferences("connections",Context.MODE_PRIVATE).getString("selected","termux-local") ?: "termux-local"
                require(selectedProfileId in setOf("termux-local",LinuxConnectionProfile.ID)) {"PROFILE_SELECTION_INVALID"}
                refreshConnection(if(profiles.length()==0) "初回は公開鍵のペアリングを行ってください。" else "保存済みの接続設定を読み込みました。")
                refreshLinuxConnection(if(linuxLoaded) "保存済みの設定を読み込みました。接続先を選択して接続してください。"
                    else "Linux用の鍵または設定を読み込めません。保存データを確認してください。")
                val credentialMessage=if(linuxIdentity!=null) "Termuxと別Linux用の公開鍵認証を個別に保存済み" else "Termux用の公開鍵認証を保存済み · Linux用の鍵を確認できません"
                ui.post { credentialSummary.value = credentialMessage }
                startRuntime()
            }
        } }
    }
    fun sceneReady(): Long = inputIntents.serial {
        inputIntents.invalidate();keyboardFocus=null;inputTargets=emptyMap()
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
    private fun enqueueConnect(requested:((String,Int)->Unit)?=null): String? {
        if(selectedProfileId==LinuxConnectionProfile.ID && (linuxIdentity==null || linuxProfile==null || linuxPins()==null ||
                LinuxConnectionProfile.endpointSupport(checkNotNull(linuxProfile))!=LinuxConnectionProfile.EndpointSupport.IPV4)) {
            linuxMessage="IPv4接続先・ホスト鍵・転送用ファイルを設定で確認してください。";requested?.invoke("",NativeBridge.NOT_READY);return null
        }
        val request = requests.incrementAndGet().toString()
        val command = JSONObject().put("schema", 1).put("type", "connect").put("request_id", request).put("profile_id", selectedProfileId)
        val code=NativeBridge.nativeCommand(handle,command.toString())
        requested?.invoke(request,code)
        if (code != NativeBridge.ACCEPTED) return null
        linuxCredentialCapture?.close()
        linuxCredentialCapture=if(selectedProfileId==LinuxConnectionProfile.ID) LinuxCredentialCapture(handle,request,checkNotNull(linuxProfile)) else null
        linuxCatalog=null;linuxMessage=null
        lastConnectRequest = request
        connectPending = true
        return request
    }
    fun connect(requested:((String,Int)->Unit)?=null) { productWorker.execute { safely {
        if (handle == 0L || pendingDisconnect != null || connectPending || linuxAction!=null) {requested?.invoke("",NativeBridge.NOT_READY);return@safely}
        val transport = snapshot().getJSONObject("runtime").getJSONObject("transport")
        if (transport.getString("state") in setOf("Idle", "Disconnected", "Failed") ||
            selectedProfileId==LinuxConnectionProfile.ID && transport.getString("state")=="CleanupPending" && transport.optInt("owned_connections")==0) enqueueConnect(requested)
        else requested?.invoke("",NativeBridge.NOT_READY)
    } } }
    fun openTerminal() { productWorker.execute { safely {
        releasePanelTextFocus()
        if (handle == 0L || selectedProfileId!="termux-local" || terminalOpen != null || pendingDisconnect != null || connectPending) return@safely
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
    fun disconnect() = disconnectExpected(null,null)
    fun debugDisconnectSession(runtime:Long,session:String) {
        if(BuildConfig.DEBUG && runtime>0 && session.matches(Regex("[1-9][0-9]{0,18}"))) disconnectExpected(runtime,session)
    }
    fun debugCancelConnect(runtime:Long,request:String) {
        if(BuildConfig.DEBUG && runtime>0 && request.matches(Regex("[1-9][0-9]{0,18}"))) disconnectExpected(runtime,null,request)
    }
    private fun disconnectExpected(expectedRuntime:Long?,expectedSession:String?,expectedConnectRequest:String?=null) { productWorker.execute { safely {
        if(expectedRuntime!=null && (handle!=expectedRuntime || expectedSession!=null && snapshot().getJSONObject("runtime").getJSONObject("transport").optString("session")!=expectedSession)) return@safely
        if(expectedConnectRequest!=null && lastConnectRequest!=expectedConnectRequest) return@safely
        pendingInputFocus=null
        clearTerminalOpen(); terminalAdding=null; terminalError = null
        linuxCredentialCapture?.close();linuxCredentialCapture=null;linuxAction=null;linuxCatalog=null;linuxMessage=null
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
    data class WindowControlTicket(val runtime: Long, val target: String, val intent: Long, val revision: Long) {
        internal var groupMenuPermit:WindowGroupMenuPermit?=null;private set
        internal constructor(runtime:Long,target:String,intent:Long,revision:Long,permit:WindowGroupMenuPermit?):this(runtime,target,intent,revision) {groupMenuPermit=permit}
    }
    /** SDK button down owns one immutable target; reset also releases any Linux-held key. */
    fun beginWindowControl(target: JSONObject, runtime: Long, revision: Long,forMenu:Boolean=false): WindowControlTicket? = inputIntents.serial {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(runtime!=handle || revision<0 || editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED" || debugSdkLease.blocksProduct()) return@serial null
        val permit=if(forMenu) runCatching {
            val state=JSONObject(NativeBridge.nativeSnapshot(runtime)).getJSONObject("runtime")
            WindowGroupMenuWire.capture(runtime,target,state)?.takeIf {it.controlsRevision==revision}
        }.getOrNull() else null
        // The explicit menu permit never unlocks Focus/Key/Pointer/IME or another control action.
        val current=inputTarget(target.toString())
        if(current?.runtime!=runtime && permit?.group==null) return@serial null
        resetInput()
        WindowControlTicket(runtime,target.toString(),inputIntents.get(),revision,permit)
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
    class WindowOperationTicket internal constructor(val control:WindowControlTicket,val source:Long,val kind:String) {
        @Volatile var phase:String=kind;internal set
    }
    data class WindowMoveTicket(val operation:WindowOperationTicket) {
        val control get()=operation.control
        val source get()=operation.source
    }
    private val windowOperation=AtomicReference<WindowOperationTicket?>(null)
    private val windowOperationCompletions=ConcurrentHashMap<String,String>()
    fun beginWindowMove(target:JSONObject,runtime:Long,revision:Long,source:Long):WindowMoveTicket? = inputIntents.serial {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(windowOperation.get()!=null || source==0L || panelTextInputs.isNotEmpty()) return@serial null
        val control=beginWindowControl(target,runtime,revision) ?: return@serial null
        val owner=WindowOperationTicket(control,source,"move")
        if(windowOperation.compareAndSet(null,owner)) WindowMoveTicket(owner) else null
    }
    fun windowOperationCurrent(ticket:WindowOperationTicket):Boolean = inputIntents.serial {
        windowOperation.get()===ticket && ticket.control.runtime==handle && ticket.control.intent==inputIntents.get() &&
            activityVisible.get() && xrState.get()=="FOCUSED" && !editing.get() && panelTextInputs.isEmpty() && !debugSdkLease.blocksProduct()
    }
    fun windowMoveCurrent(ticket:WindowMoveTicket):Boolean=windowOperationCurrent(ticket.operation)
    fun windowMoveSourceOwned(source:Long):Boolean=windowOperation.get()?.let {it.kind=="move" && it.source==source}==true
    private fun movingTarget(target:String):Boolean=windowOperation.get()?.takeIf {it.phase!="returning"}?.control?.target?.let {targetIdentity(JSONObject(it))==targetIdentity(JSONObject(target))}==true
    fun finishWindowMove(ticket:WindowMoveTicket,restore:Boolean) {
        if(!windowOperation.compareAndSet(ticket.operation,null)) return
        if(restore && ticket.control.intent==inputIntents.get()) windowControl(ticket.control,"focus")
    }
    fun submitWindowMovePose(ticket:WindowMoveTicket,pose:JSONObject):String? = inputIntents.serial {
        if(!windowMoveCurrent(ticket) || !PanelPoseCodec.valid(pose) || poseCompletions.size>=16) return@serial null
        val request=requests.incrementAndGet().toString();poseCompletions[request]="pending"
        val code=NativeBridge.nativeCommand(ticket.control.runtime,JSONObject().put("schema",1).put("type","set_pose_control")
            .put("request_id",request).put("target",JSONObject(ticket.control.target)).put("pose",pose).put("controls_revision",ticket.control.revision).toString())
        BuildHooks.windowControlRequested("move",ticket.control.runtime,ticket.control.target,ticket.control.revision,request,code)
        if(code!=NativeBridge.ACCEPTED) {poseCompletions.remove(request);return@serial null}
        request
    }
    fun beginWindowMenu(control:WindowControlTicket):WindowOperationTicket? = inputIntents.serial {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(windowOperation.get()!=null || control.runtime!=handle || control.intent!=inputIntents.get() || panelTextInputs.isNotEmpty() ||
            editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED" || debugSdkLease.blocksProduct()) return@serial null
        WindowOperationTicket(control,0,"menu").takeIf {windowOperation.compareAndSet(null,it)}
    }
    fun windowOperationAndroidReleased(ticket:WindowOperationTicket)=windowOperationCurrent(ticket) && !imeRawBlocked.get()
    fun windowOperationPhase(ticket:WindowOperationTicket,phase:String):Boolean = inputIntents.serial {
        if(!windowOperationCurrent(ticket) || phase !in setOf("menu","resizing","recentering","returning","cleanup")) return@serial false
        ticket.phase=phase;true
    }
    fun finishWindowOperation(ticket:WindowOperationTicket) {windowOperation.compareAndSet(ticket,null)}
    fun windowOperationCompletion(request:String):String? {
        val code=windowOperationCompletions[request] ?: return null
        if(code!="pending") windowOperationCompletions.remove(request)
        return code
    }
    fun forgetWindowOperation(request:String) {windowOperationCompletions.remove(request)}
    fun resetWindowOperation(ticket:WindowOperationTicket):String? = inputIntents.serial {
        if(!windowOperationCurrent(ticket) || windowOperationCompletions.size>=8) return@serial null
        val request=requests.incrementAndGet().toString();windowOperationCompletions[request]="pending"
        if(enqueueInputReset(request)!=NativeBridge.ACCEPTED) {windowOperationCompletions.remove(request);return@serial null}
        request
    }
    fun submitWindowSize(ticket:WindowOperationTicket,expectedCommand:String?,expected:WindowPhysicalSize,larger:Boolean):String? = inputIntents.serial {
        if(!windowOperationCurrent(ticket) || !windowOperationAndroidReleased(ticket) || expected.step(larger)==null || windowOperationCompletions.size>=8) return@serial null
        val request=requests.incrementAndGet().toString();windowOperationCompletions[request]="pending"
        val code=NativeBridge.nativeCommand(ticket.control.runtime,JSONObject().put("schema",1).put("type","set_physical_size_control")
            .put("request_id",request).put("target",JSONObject(ticket.control.target)).put("controls_revision",ticket.control.revision)
            .put("expected_size_command",expectedCommand ?: JSONObject.NULL).put("expected_size",JSONObject().put("width_m",expected.width.toDouble()).put("height_m",expected.height.toDouble()))
            .put("step",if(larger) "larger" else "smaller").toString())
        BuildHooks.windowControlRequested("physical-size",ticket.control.runtime,ticket.control.target,ticket.control.revision,request,code)
        if(code!=NativeBridge.ACCEPTED) {windowOperationCompletions.remove(request);return@serial null}
        request
    }
    /** Same menu owner, one captured placement. Product selection is never consulted after capture. */
    fun submitWindowRecenter(ticket:WindowOperationTicket,capture:WindowRecenterCapture):String? = inputIntents.serial {
        if(!WindowRecenterPolicy.ENABLED || !windowOperationCurrent(ticket) || !windowOperationAndroidReleased(ticket) ||
            ticket.kind!="menu" || capture.identity!=WindowSizeIdentity(ticket.control.runtime,ticket.control.target,ticket.control.revision,ticket.control.intent) ||
            windowOperationCompletions.size>=8) return@serial null
        val target=JSONObject(ticket.control.target)
        val state=runCatching {JSONObject(NativeBridge.nativeSnapshot(ticket.control.runtime)).getJSONObject("runtime")}.getOrNull() ?: return@serial null
        if(state.optString("state")!="Ready" || !state.optBoolean("activity_visible") || state.optString("xr_state")!="FOCUSED" ||
            state.optString("scene")!=target.optString("scene") || state.optJSONObject("render")?.optLong("theme_revision",-1)!=ticket.control.revision ||
            !windowGroupInputAllowed(ticket.control.runtime,target)) return@serial null
        val geometry=WindowRecenterWire.read(state,target) ?: return@serial null
        if(capture.scene?.matches(state,target)!=true) return@serial null
        if(!capture.readyToSubmit(capture.identity,SystemClock.elapsedRealtime(),geometry,state.getLong("input_resets"))) return@serial null
        val request=requests.incrementAndGet().toString();windowOperationCompletions[request]="pending"
        val code=NativeBridge.nativeCommand(ticket.control.runtime,JSONObject().put("schema",1).put("type","set_pose_control")
            .put("request_id",request).put("target",target).put("controls_revision",ticket.control.revision)
            .put("pose",WindowMoveController.json(capture.desired)).toString())
        BuildHooks.windowControlRequested("recenter",ticket.control.runtime,ticket.control.target,ticket.control.revision,request,code)
        if(code!=NativeBridge.ACCEPTED) {windowOperationCompletions.remove(request);return@serial null}
        request
    }
    fun returnWindowOperation(ticket:WindowOperationTicket):Boolean = inputIntents.serial {
        if(!windowOperationAndroidReleased(ticket) || !windowOperationPhase(ticket,"returning")) return@serial false
        windowControl(ticket.control,"focus");true
    }
    internal fun windowGroupMenuPermit(ticket:WindowOperationTicket,state:JSONObject):WindowGroupMenuPermit?=inputIntents.serial {
        if(ticket.kind!="menu" || !windowOperationCurrent(ticket)) return@serial null
        val captured=ticket.control.groupMenuPermit ?: return@serial null
        WindowGroupMenuWire.capture(ticket.control.runtime,JSONObject(ticket.control.target),state)?.takeIf {captured.same(it)}
    }
    /** Register the one request before native enqueue while ordinary input intent changes share this monitor. */
    internal fun submitWindowGroupMenu(ticket:WindowOperationTicket,capture:WindowGroupMenuCapture):String?=inputIntents.serial {
        if(!windowOperationCurrent(ticket) || !windowOperationAndroidReleased(ticket) || ticket.kind!="menu" ||
            capture.phase!=WindowGroupMenuCapture.Phase.SUBMITTING || windowOperationCompletions.size>=8) return@serial null
        val control=ticket.control;val target=JSONObject(control.target)
        val identity=WindowGroupMenuIdentity(WindowGroupWire.key(control.runtime,target),control.revision,control.intent)
        val state=runCatching {JSONObject(NativeBridge.nativeSnapshot(control.runtime)).getJSONObject("runtime")}.getOrNull() ?: return@serial null
        val permit=windowGroupMenuPermit(ticket,state) ?: return@serial null
        val now=SystemClock.elapsedRealtime()
        if(capture.identity!=identity || !capture.current(identity,now,permit) || capture.action !in permit.actions) return@serial null
        val request=requests.incrementAndGet().toString()
        if(!capture.submitted(identity,now,permit,request)) return@serial null
        windowOperationCompletions[request]="pending"
        val code=NativeBridge.nativeCommand(control.runtime,JSONObject().put("schema",1).put("type","window_group")
            .put("request_id",request).put("target",target).put("controls_revision",control.revision).put("action",capture.action)
            .put("expected_group",permit.group ?: JSONObject.NULL).put("expected_revision",permit.revision ?: JSONObject.NULL).toString())
        BuildHooks.windowControlRequested("group-${capture.action}",control.runtime,control.target,control.revision,request,code)
        if(code!=NativeBridge.ACCEPTED) {windowOperationCompletions.remove(request);capture.cancel("enqueue_rejected");return@serial null}
        request
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
    /** workerで捕捉した群metadataだけを見る。最終native gateはenqueue後にも照合する。 */
    fun windowGroupInputAllowed(runtime:Long,target:JSONObject):Boolean = runCatching {
        runtime==handle && groupInputGate.get().allows(WindowGroupWire.key(runtime,target))
    }.getOrDefault(false)
    internal fun ackWindowGroupGeometry(owner:WindowGroupPresentation.Owned,actual:JSONObject,frame:String):String?=inputIntents.serial {
        if(owner.runtime!=handle || owner.key.scene!=scenes.get().toString() || !owner.settling ||
            !activityVisible.get() || xrState.get()!="FOCUSED" || debugSdkLease.blocksProduct() || groupGeometryCompletions.size>=9 ||
            !PanelPoseCodec.valid(actual) || frame.toLongOrNull()?.let {it>0}!=true) return@serial null
        val target=JSONObject().put("session",owner.key.session).put("window",owner.key.window).put("scene",owner.key.scene).put("generation",owner.key.generation)
        val request=requests.incrementAndGet().toString();groupGeometryCompletions[request]="pending"
        val code=NativeBridge.nativeCommand(owner.runtime,JSONObject().put("schema",1).put("type","window_group_applied")
            .put("request_id",request).put("target",target).put("group",owner.group).put("revision",owner.revision)
            .put("pose",actual).put("physical_size",owner.size).put("content_frame",frame).toString())
        if(code!=NativeBridge.ACCEPTED) {groupGeometryCompletions.remove(request);null} else request
    }
    internal fun windowGroupGeometryCompletion(request:String):String? {
        val result=groupGeometryCompletions[request] ?: return null
        if(result!="pending") groupGeometryCompletions.remove(request)
        return result
    }
    internal fun forgetWindowGroupGeometry(request:String) {groupGeometryCompletions.remove(request)}
    internal fun windowGroupGeometryPending()=groupGeometryCompletions.size
    fun inputTarget(target: String): InputTarget? {
        if (debugSdkLease.blocksProduct() || editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED" || movingTarget(target)) return null
        val key=JSONObject(target)
        if(!windowGroupInputAllowed(handle,key)) return null
        return inputTargets[targetIdentity(key)]?.takeIf { it.runtime==handle }
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
    class DebugInputTrial internal constructor(internal val ticket:InputIntentSerial.Trial,val runtime:Long,epoch:Long,internal val sdkScope:DebugSdkInputScope?=null) {
        var epoch:Long=epoch;internal set
        internal val targets=mutableSetOf<String>()
        internal var resize:DebugPendingResize?=null
        internal val sdkContinuity=sdkScope?.let {DebugSdkInputContinuity(runtime,epoch,it.session,it.scene.toString())}
    }
    internal data class DebugPendingResize(val token:InputResizeReplacement,val theme:Long,val frame:Long,val delivered:Long,
        val buttons:Long,val lease:String,val appId:String,val physical:String,val placement:String,val focus:String,var readiness:JSONObject?=null)
    private var debugInputActive:DebugInputTrial?=null
    class DebugSdkInputPreparation internal constructor(internal val scope:DebugSdkInputScope,internal val key:String,
        internal val ticket:InputIntentSerial.Trial,internal val readiness:DebugSdkInputReadiness,
        internal val geometry:DebugSdkInputContinuity.Geometry)
    class DebugSdkInputPoll(val trial:DebugInputTrial?,val metadata:JSONObject)
    private var debugSdkInputPreparation:DebugSdkInputPreparation?=null
    data class DebugInputResizeReceipt(val request:String,val oldTarget:String,val target:InputTarget,val configuredSerial:Long,
        val acknowledgedSerial:Long,val committedSerial:Long,val width:Int,val height:Int,val replaced:Boolean,val delivered:Long)
    fun debugCaptureInput(target:InputTarget):DebugInputTrial? = inputIntents.serial {
        if(!BuildConfig.DEBUG || !debugImeCanRequestFocus() || inputTarget(target.target)!=target ||
            JSONObject(target.target).optString("scene")!=scenes.get().toString()) null
        else DebugInputTrial(inputIntents.capture(),target.runtime,target.epoch).also {it.targets.add(target.identity);debugInputActive=it}
    }
    /** 通常targetを公開せず、固定GTK leaseにだけInputIntentSerial上の能力を渡す。 */
    fun debugPrepareSdkInput(owner:Any,target:JSONObject,expectedEpoch:Long,expectedKeys:Long,expectedButtons:Long):DebugSdkInputPreparation? = inputIntents.serial {
        if(debugSdkInputPreparation!=null || debugInputActive!=null) return@serial null
        val scope=debugSdkInputScope?.takeIf {it.owner===owner} ?: return@serial null
        val observed=debugSdkInputObservation(scope,target.toString()) ?: return@serial null
        if(!observed.unpressed || observed.stamp.epoch!=expectedEpoch || observed.stamp.keys!=expectedKeys || observed.stamp.buttons!=expectedButtons) return@serial null
        // provisional Trialはinvalidate()だけの物理入力も検知する。scopeの一回claimはREADYまで保留。
        val ticket=inputIntents.capture()
        DebugSdkInputPreparation(scope,target.toString(),ticket,DebugSdkInputReadiness(observed.stamp.copy(intent=ticket.expected),SystemClock.elapsedRealtime()),observed.proof.geometry)
            .also {debugSdkInputPreparation=it}
    }
    fun debugPollSdkInput(prepared:DebugSdkInputPreparation):DebugSdkInputPoll = inputIntents.serial {
        val gate=prepared.readiness
        if(gate.state==DebugSdkInputReadiness.State.CAPTURED || gate.state==DebugSdkInputReadiness.State.CANCELLED)
            return@serial DebugSdkInputPoll(null,JSONObject(gate.metadata()))
        val owned=debugSdkInputPreparation===prepared && debugSdkInputScope===prepared.scope
        val observed=if(owned) debugSdkInputObservation(prepared.scope,prepared.key) else null
        if(observed!=null && observed.proof.geometry!=prepared.geometry) gate.cancel("PROBE_INPUT_TARGET_CHANGED")
        val state=gate.observe(SystemClock.elapsedRealtime(),observed?.stamp,owned && observed!=null,
            inputIntents.current(prepared.ticket),observed?.unpressed==true,
            observed?.adapterReady==true && observed.proof.geometryApplied && observed.proof.interactive,observed?.frameReady==true)
        var trial:DebugInputTrial?=null
        if(state==DebugSdkInputReadiness.State.READY) {
            if(prepared.scope.claimTrial() && gate.capture()) {
                val target=checkNotNull(observed).target
                trial=DebugInputTrial(prepared.ticket,target.runtime,target.epoch,prepared.scope)
                    .also {check(checkNotNull(it.sdkContinuity).accept(observed.proof));it.targets.add(target.identity);debugInputActive=it}
            } else gate.cancel("PROBE_INPUT_ALREADY_CLAIMED")
        }
        if(gate.state==DebugSdkInputReadiness.State.CANCELLED) inputIntents.release(prepared.ticket)
        DebugSdkInputPoll(trial,JSONObject(gate.metadata()))
    }
    private fun debugSdkInputState(scope:DebugSdkInputScope):JSONObject? = try {
        check(BuildConfig.DEBUG && debugSdkInputScope===scope && debugSdkLease.owns(scope.owner) &&
            scope.current(scope.owner,debugSdkLease.metadata(),handle,scenes.get()) &&
            !editing.get() && !imeRawBlocked.get() && activityVisible.get() && xrState.get()=="FOCUSED")
        val envelope=JSONObject(NativeBridge.nativeSnapshot(scope.runtime));check(envelope.getInt("code")==NativeBridge.ACCEPTED)
        val value=envelope.getJSONObject("runtime");val app=value.getJSONObject("app");val transport=value.getJSONObject("transport")
        check(value.getString("state")=="Ready" && value.getBoolean("activity_visible") && value.getString("xr_state")=="FOCUSED" &&
            value.getString("scene")==scope.scene.toString() && app.getString("session")==scope.session && app.getString("nonce")==scope.nonce &&
            app.getString("mode")==scope.mode && app.getString("state") in setOf("running","pass") && app.isNull("error") &&
            transport.getString("session")==scope.session && transport.getString("state")=="ProbeReady")
        val forward=transport.getJSONObject("linux_forward");val gtk=forward.getJSONObject("gtk")
        check(forward.getString("session")==scope.session && forward.getString("nonce")==scope.nonce && forward.getInt("uid")>0 &&
            forward.getString("state") in setOf("running","pass") && forward.isNull("error") && forward.getBoolean("tools_verified") &&
            gtk.getString("case")==scope.case && gtk.getBoolean("process_bound") && gtk.isNull("report_error"))
        value
    } catch(_:Exception) {null}
    private class DebugSdkInputObservation(val target:InputTarget,val stamp:DebugSdkInputReadiness.Stamp,
        val unpressed:Boolean,val adapterReady:Boolean,val frameReady:Boolean,val proof:DebugSdkInputContinuity.Proof)
    private fun debugSdkInputObservation(scope:DebugSdkInputScope,target:String):DebugSdkInputObservation? { return try {
        val key=JSONObject(target);val value=debugSdkInputState(scope) ?: return null
        val identity=targetIdentity(key)
        check(key.keys().asSequence().toSet()==setOf("session","window","scene","generation") && key.getString("session")==scope.session && key.getString("scene")==scope.scene.toString())
        fun rows(a:JSONArray)=(0 until a.length()).map {a.getJSONObject(it)}
        val row=rows(value.getJSONObject("app").getJSONArray("windows")).single {it.getString("window")==key.getString("window") && it.getString("life")=="Mapped"}
        check(scope.permitsApp(row.getString("app_id")))
        check(rows(value.getJSONArray("panels")).any {it.optString("action")=="create" && it.optBoolean("applied") && it.isNull("error") && targetIdentity(it.getJSONObject("target"))==identity})
        val drawn=rows(value.getJSONObject("render").getJSONArray("targets")).single {targetIdentity(it.getJSONObject("key"))==identity}
        val frameReady=drawn.getLong("frames")>0 && drawn.optString("content_frame").toLongOrNull()?.let {frame->frame>=row.getString("frame").toLong()}==true
        val logical=row.getJSONObject("logical_size");val sizing=row.getJSONObject("sizing");val physical=sizing.getJSONObject("physical_size_m")
        val placement=rows(value.getJSONObject("workspace").getJSONArray("windows")).single {
            it.getString("session")==scope.session && it.getString("window")==key.getString("window")}.getJSONObject("placement")
        val pose=placement.getJSONObject("pose");check(PanelPoseCodec.valid(pose))
        // TargetReportのGpuInfoはserde(flatten)。buffer寸法もgeometryへ含める。
        val geometry=DebugSdkInputContinuity.Geometry(logical.getInt("width"),logical.getInt("height"),drawn.getInt("width"),drawn.getInt("height"),
            physical.getDouble("width_m"),physical.getDouble("height_m"),
            (0..2).map {pose.getJSONArray("position_m").getDouble(it)}+(0..3).map {pose.getJSONArray("rotation_xyzw").getDouble(it)},value.getJSONObject("render").getLong("theme_revision"))
        val appliedPose=placement.optJSONObject("applied_pose");val appliedSize=sizing.optJSONObject("applied_physical_size_m")
        val geometryApplied=appliedPose?.optJSONObject("target")?.let {targetIdentity(it)==identity}==true &&
            appliedPose.optJSONObject("pose")?.let {PanelPoseCodec.near(it,pose)}==true &&
            appliedSize?.optDouble("width_m")==geometry.physicalWidth && appliedSize.optDouble("height_m")==geometry.physicalHeight
        val adapter=debugAdapterSnapshot()
        val sdk=adapter?.takeIf {it.optString("scene")==scope.scene.toString()}?.getJSONArray("targets")?.let(::rows)?.singleOrNull {
            targetIdentity(it.getJSONObject("target"))==identity && it.optString("runtime")==scope.runtime.toString()}
        // 同targetの退役は単なるcache未公開として待たない。
        check(sdk==null || (!sdk.optBoolean("retiring") && !sdk.optBoolean("sdk_disposed") && !sdk.optBoolean("disposal_failed")))
        val input=value.getJSONObject("app").getJSONObject("input");val pointer=input.getJSONObject("pointer")
        val captured=InputTarget(scope.runtime,value.getLong("input_resets"),identity,key.toString())
        val stamp=DebugSdkInputReadiness.Stamp(scope.runtime,identity,captured.epoch,inputIntents.get(),input.getLong("delivered"),pointer.getLong("buttons"),
            if(input.isNull("focus_window")) null else input.getString("focus_window"),if(input.isNull("focus_token")) null else input.getString("focus_token"))
        val sdkOwned=sdk!=null && sdk.optBoolean("listener_owned") && sdk.optBoolean("input_owned")
        DebugSdkInputObservation(captured,stamp,input.getInt("pressed")==0 && pointer.getInt("pressed")==0,sdkOwned,frameReady,
            DebugSdkInputContinuity.Proof(debugResizeKey(scope.runtime,key),captured.epoch,geometry,drawn.getLong("frames")>0 && !drawn.optBoolean("presentation_paused"),geometryApplied,sdkOwned,frameReady))
    } catch(_:Exception) {null} }
    private fun debugSdkInputTarget(trial:DebugInputTrial,target:String):InputTarget? {
        val observed=debugSdkInputObservation(checkNotNull(trial.sdkScope),target) ?: return null
        return observed.target.takeIf {checkNotNull(trial.sdkContinuity).accept(observed.proof)}
    }
    fun debugInputContinuityStatus(trial:DebugInputTrial):JSONObject?=inputIntents.serial {
        trial.sdkContinuity?.let {JSONObject(it.metadata())}
    }
    fun debugInputTarget(trial:DebugInputTrial,target:JSONObject):InputTarget? = inputIntents.serial {
        if(!debugInputCurrent(trial) || target.optString("scene")!=scenes.get().toString()) null
        else if(trial.sdkScope!=null) debugSdkInputTarget(trial,target.toString())
        else inputTarget(target.toString())?.takeIf {it.runtime==trial.runtime}
    }
    fun debugInputFocusMatches(trial:DebugInputTrial,target:JSONObject,token:String):Boolean = inputIntents.serial {
        if(!debugInputCurrent(trial)) false else if(trial.sdkScope==null) keyboardFocus?.let {it.runtime==trial.runtime && it.epoch==trial.epoch && targetIdentity(JSONObject(it.target))==targetIdentity(target) && it.token==token}==true
        else debugSdkInputState(checkNotNull(trial.sdkScope))?.let {value->
            val input=value.getJSONObject("app").getJSONObject("input")
            value.getLong("input_resets")==trial.epoch && target.getString("session")==trial.sdkScope.session && input.optString("focus_window")==target.getString("window") && input.optString("focus_token")==token
        }==true
    }
    fun debugInputCurrent(trial:DebugInputTrial):Boolean = inputIntents.serial {
        val valid=BuildConfig.DEBUG && debugInputActive===trial && handle==trial.runtime && (trial.sdkScope?.let {debugSdkInputState(it)!=null} ?: debugImeCanRequestFocus())
        if(!valid && debugInputActive===trial) inputIntents.invalidate()
        valid && inputIntents.current(trial.ticket)
    }
    fun debugInputDispatch(trial:DebugInputTrial,target:InputTarget,focusToken:String?=null,pointerDown:Boolean=false,enqueue:()->Int):Boolean = inputIntents.serial {
        val result=inputIntents.dispatch(trial.ticket,{
            debugInputCurrent(trial) && target.runtime==trial.runtime && target.epoch==trial.epoch && debugInputTarget(trial,JSONObject(target.target))==target &&
                (focusToken==null || debugInputFocusMatches(trial,JSONObject(target.target),focusToken))
        }) {
            trial.targets.add(target.identity)
            if(pointerDown) {inputIntents.ownedIncrement(trial.ticket);releasePanelTextFocus()}
            enqueue()
        }
        if(result!=NativeBridge.ACCEPTED && debugInputActive===trial) inputIntents.invalidate()
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
        debugReleaseInput(trial);valid
    }
    fun debugReleaseInput(trial:DebugInputTrial)=inputIntents.serial {
        inputIntents.release(trial.ticket)
        if(debugInputActive===trial) {trial.resize=null;debugInputActive=null}
    }
    private fun debugResizeKey(runtime:Long,target:JSONObject):InputWindowKey {
        val values=listOf("session","window","scene","generation").map {key->target.getString(key).also {
            check(it.matches(Regex("[1-9][0-9]{0,18}")) && it.toLongOrNull()!=null)
        }}
        check(target.keys().asSequence().toSet()==setOf("session","window","scene","generation"))
        return InputWindowKey(runtime,values[0],values[1],values[2],values[3])
    }
    private fun debugResizeRows(value:JSONArray):List<JSONObject> = (0 until value.length()).map {value.getJSONObject(it)}
    private fun debugResizeWindow(runtime:JSONObject,key:InputWindowKey):JSONObject? =
        debugResizeRows(runtime.getJSONObject("app").getJSONArray("windows")).singleOrNull {it.optString("window")==key.window && it.optString("life")=="Mapped"}
    private fun debugResizePlacement(runtime:JSONObject,key:InputWindowKey):JSONObject? =
        debugResizeRows(runtime.getJSONObject("workspace").getJSONArray("windows")).singleOrNull {
            it.optString("session")==key.session && it.optString("window")==key.window
        }?.optJSONObject("placement")
    private fun debugResizeLease(runtime:JSONObject):String {
        val app=runtime.getJSONObject("app");val transport=runtime.getJSONObject("transport")
        val forward=transport.getJSONObject("app_forward");val remote=forward.getJSONObject("remote");val child=remote.getJSONObject("child")
        check(app.getString("state")=="running" && app.isNull("error") && transport.getString("state")=="Ready" &&
            forward.getString("state")=="running" && remote.getString("phase")=="running" && remote.isNull("error"))
        return listOf(app.getString("session"),app.getString("nonce"),transport.getString("session"),forward.getString("nonce"),
            child.getInt("pid").toString(),child.getString("start"),child.getInt("group").toString()).joinToString(":")
    }
    private fun debugResizeFocus(input:JSONObject):String=JSONObject().put("window",input.opt("focus_window") ?: JSONObject.NULL)
        .put("token",input.opt("focus_token") ?: JSONObject.NULL).toString()
    /** 一つの要求だけを登録してからenqueueする。BUSYを同条件で再送しない。 */
    fun debugBeginInputResize(trial:DebugInputTrial,target:InputTarget,request:String,width:Int,height:Int):Boolean=inputIntents.serial {
        if(!debugInputCurrent(trial) || trial.resize!=null || target.epoch!=trial.epoch ||
            debugInputTarget(trial,JSONObject(target.target))!=target || width !in 320..2048 || height !in 200..2048) return@serial false
        try {
            val key=debugResizeKey(trial.runtime,JSONObject(target.target));val runtime=snapshot().getJSONObject("runtime")
            check(runtime.getString("scene")==key.scene && runtime.getJSONObject("app").getString("session")==key.session &&
                runtime.getLong("input_resets")==trial.epoch && key.scene==scenes.get().toString())
            val window=checkNotNull(debugResizeWindow(runtime,key));val placement=checkNotNull(debugResizePlacement(runtime,key))
            val input=runtime.getJSONObject("app").getJSONObject("input");val pointer=input.getJSONObject("pointer")
            check(input.getInt("pressed")==0 && pointer.getInt("pressed")==0)
            val replacement=InputResizeReplacement(request.toLong(),key,trial.epoch,width,height,SystemClock.elapsedRealtime()+10000)
            val pending=DebugPendingResize(replacement,runtime.getJSONObject("render").getLong("theme_revision"),window.getString("frame").toLong(),
                input.getLong("delivered"),pointer.getLong("buttons"),debugResizeLease(runtime),window.getString("app_id"),
                window.getJSONObject("sizing").getJSONObject("physical_size_m").toString(),placement.getJSONObject("pose").toString(),debugResizeFocus(input))
            if(!inputIntents.beginReplacement(trial.ticket,replacement)) return@serial false
            trial.resize=pending
            val code=NativeBridge.nativeCommand(trial.runtime,JSONObject().put("schema",1).put("type","resize_logical")
                .put("request_id",request).put("target",JSONObject(target.target)).put("width",width).put("height",height).toString())
            if(code!=NativeBridge.ACCEPTED) {inputIntents.invalidate();false} else true
        } catch(_:Exception) {inputIntents.invalidate();false}
    }
    /** 各gateは最新native snapshotと既存SDK/input gateから確認する。raw入力を有効化し直さない。 */
    fun debugAdoptInputResize(trial:DebugInputTrial):DebugInputResizeReceipt?=inputIntents.serial {
        if(!debugInputCurrent(trial)) return@serial null
        val pending=trial.resize ?: return@serial null;val token=pending.token;val old=token.old
        try {
            val runtime=snapshot().getJSONObject("runtime")
            check(runtime.getString("state")=="Ready" && runtime.getBoolean("activity_visible") && runtime.getString("xr_state")=="FOCUSED" &&
                runtime.getString("scene")==old.scene && old.scene==scenes.get().toString() && runtime.getJSONObject("app").getString("session")==old.session &&
                runtime.getJSONObject("render").getLong("theme_revision")==pending.theme && debugResizeLease(runtime)==pending.lease)
            val window=checkNotNull(debugResizeWindow(runtime,old));val resize=window.getJSONObject("resize")
            check(window.getString("app_id")==pending.appId && window.getJSONObject("sizing").getJSONObject("physical_size_m").toString()==pending.physical)
            val placement=checkNotNull(debugResizePlacement(runtime,old));check(placement.getJSONObject("pose").toString()==pending.placement)
            val request=resize.optJSONObject("request")
            val matches=request?.optString("request_id")==token.request.toString() && request.optJSONObject("target")?.let {debugResizeKey(trial.runtime,it)==old}==true
            val plans=debugResizeRows(runtime.getJSONArray("panels"))
            val plan=plans.filter {it.optString("action")=="create" && it.isNull("error")}.singleOrNull {
                val key=debugResizeKey(trial.runtime,it.getJSONObject("target"));old.sameWindow(key)
            }
            val target=plan?.getJSONObject("target");val next=target?.let {debugResizeKey(trial.runtime,it)} ?: old
            val input=runtime.getJSONObject("app").getJSONObject("input");val pointer=input.getJSONObject("pointer")
            val frame=window.getString("frame").toLong();val epoch=runtime.getLong("input_resets")
            fun size(value:JSONObject?):Boolean=value?.optInt("width")==token.width && value.optInt("height")==token.height
            fun serial(name:String):Long?=(resize.opt(name) as? Number)?.toLong()?.takeIf {it in 0..0xffffffffL}
            val serials=listOf("configured_serial","acknowledged_serial","committed_serial").map(::serial)
            val applied=resize.optString("phase")=="applied" && resize.isNull("error") && size(window.optJSONObject("logical_size")) &&
                size(resize.optJSONObject("requested_logical_size")) && size(resize.optJSONObject("configured_logical_size")) && size(resize.optJSONObject("committed_size"))
            val frames=debugResizeRows(runtime.getJSONObject("render").getJSONArray("targets"))
            val rendered=frames.singleOrNull {debugResizeKey(trial.runtime,it.getJSONObject("key"))==next}
            val adapter=debugAdapterSnapshot();val sdk=adapter?.optJSONArray("targets")?.let(::debugResizeRows) ?: emptyList()
            val bound=sdk.singleOrNull {debugResizeKey(trial.runtime,it.getJSONObject("target"))==next}
            val receipts=adapter?.optJSONArray("retired")?.let(::debugResizeRows) ?: emptyList()
            val oldReleased=next==old || (plans.none {debugResizeKey(trial.runtime,it.getJSONObject("target"))==old} &&
                frames.none {debugResizeKey(trial.runtime,it.getJSONObject("key"))==old} && sdk.none {debugResizeKey(trial.runtime,it.getJSONObject("target"))==old} &&
                receipts.any {it.optString("runtime")==trial.runtime.toString() && debugResizeKey(trial.runtime,it.getJSONObject("target"))==old &&
                    it.optBoolean("sdk_disposed") && it.optBoolean("sdk_binding_released") && !it.optBoolean("disposal_failed")})
            val sizing=window.getJSONObject("sizing");val physical=sizing.optJSONObject("applied_physical_size_m")?.toString()==pending.physical
            val pose=placement.optJSONObject("applied_pose")
            val poseReady=pose?.optJSONObject("target")?.let {debugResizeKey(trial.runtime,it)==next}==true &&
                pose.optJSONObject("pose")?.let {PanelPoseCodec.near(it,placement.getJSONObject("pose"))}==true
            val ticket=target?.let {debugInputTarget(trial,it)}
            val controls=bound?.optJSONObject("controls");val controlButtons=controls?.optJSONArray("buttons")?.let(::debugResizeRows) ?: emptyList()
            val buttonsReady=controlButtons.map {it.optString("action")}.containsAll(listOf("move","focus","close")) &&
                controlButtons.all {it.optBoolean("ready") && debugResizeKey(trial.runtime,it.getJSONObject("target"))==next}
            val focusOriginal=debugResizeFocus(input)==pending.focus
            val focusCleared=input.isNull("focus_window") && input.isNull("focus_token")
            val focusAllowed=focusOriginal || (focusCleared && epoch-token.epoch in 1L..2L)
            val focusReady=if(next==old) focusOriginal else focusCleared
            val ready=focusReady && plan?.optBoolean("applied")==true && rendered?.getLong("frames")?.let {it>0}==true && size(rendered.optJSONObject("content_size")) &&
                rendered.optString("content_frame").toLongOrNull()?.let {it>=frame}==true && adapter?.optString("scene")==old.scene &&
                bound!=null && bound.optString("runtime")==trial.runtime.toString() && !bound.optBoolean("retiring") && !bound.optBoolean("sdk_disposed") && !bound.optBoolean("disposal_failed") && bound.optBoolean("listener_owned") &&
                controls?.optLong("revision")==pending.theme && controls.optBoolean("enabled") && buttonsReady && oldReleased && physical && poseReady &&
                ticket?.runtime==trial.runtime && ticket.epoch==epoch && runtime.getJSONObject("shutdown").getInt("sdk_bindings")==sdk.size
            val unchanged=input.getLong("delivered")==pending.delivered && pointer.getLong("buttons")==pending.buttons && input.getInt("pressed")==0 && pointer.getInt("pressed")==0 && focusAllowed
            pending.readiness=JSONObject().put("receipt_matches",matches).put("command_ack",token.acknowledged).put("sizes_applied",applied)
                .put("serials_present",serials.all {it!=null}).put("producer_advanced",frame>pending.frame).put("input_unchanged",unchanged)
                .put("old_owner_released",oldReleased).put("focus_unchanged_or_owned_reset",focusAllowed).put("focus_ready",focusReady).put("physical_applied",physical).put("pose_applied",poseReady).put("sdk_and_input_ready",ready)
                .put("epoch",epoch).put("candidate_generation",next.generation)
            val proof=InputResizeReplacement.Proof(next,epoch,matches,applied,serials.all {it!=null},frame>pending.frame,unchanged,ready)
            if(!inputIntents.completeReplacement(trial.ticket,token,proof)) return@serial null
            val adopted=checkNotNull(ticket);trial.epoch=epoch;trial.targets.add(adopted.identity);trial.resize=null
            DebugInputResizeReceipt(token.request.toString(),old.identity,adopted,checkNotNull(serials[0]),checkNotNull(serials[1]),checkNotNull(serials[2]),token.width,token.height,next!=old,pending.delivered)
        } catch(_:Exception) {inputIntents.invalidate();null}
    }
    fun debugInputResizeStatus(trial:DebugInputTrial):JSONObject=inputIntents.serial {
        val token=trial.resize?.token
        JSONObject().put("request_id",token?.request?.toString() ?: JSONObject.NULL).put("pending",token!=null)
            .put("readiness",trial.resize?.readiness?.let {JSONObject(it.toString())} ?: JSONObject.NULL)
            .put("acknowledged",token?.acknowledged ?: false).put("old_retirement_seen",token?.retirementSeen ?: false)
            .put("reason",token?.failure ?: if(token==null) "RESIZE_IDLE" else if(!debugInputCurrent(trial)) "RESIZE_INTENT_LOST" else "RESIZE_WAITING")
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
    /** 明示切替で先にraw排他/urgent resetを取得。Panelの生成待ち中も先行Ctrlを残さない。 */
    fun beginImeOwner():ImeEditorOwner? = inputIntents.serial {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!imeReady()) return@serial null
        val focus = keyboardFocus ?: return@serial null
        val target = strictImeSdkTarget(JSONObject(focus.target)) ?: return@serial null
        val fresh=runCatching {snapshot().getJSONObject("runtime")}.getOrNull() ?: return@serial null
        if(fresh.optLong("input_resets",-1)!=focus.epoch || !ImeTerminalEligibility.allows(fresh,target) ||
            !WindowGroupWire.decode(focus.runtime,fresh.optJSONObject("window_groups")).allows(WindowGroupWire.key(focus.runtime,imeSdkJson(target)))) return@serial null
        val actual=fresh.getJSONObject("app").getJSONObject("input")
        if(actual.optString("focus_window")!=target.window || actual.optString("focus_token")!=focus.token) return@serial null
        lateinit var owner:ImeEditorOwner
        lateinit var connection: ImeSessionConnection
        connection = ImeSessionConnection(focus.runtime, target, focus.epoch,
            requestId = { requests.incrementAndGet().toString() },
            enqueue = { request, command -> enqueueImeCommand(connection, request, command) },
            clock = SystemClock::elapsedRealtime,
            onClosed = { runCatching { owner.view.get()?.closeEditor() } })
        val ticket=inputIntents.incrementAndGet()
        owner=ImeEditorOwner(connection,ticket,focus.token)
        imeOwner=owner
        imeAndroidReleased = {
            val editor = owner.view.get()
            runCatching { editor == null || (!editor.hasFocus() && editor.context.getSystemService(android.view.inputmethod.InputMethodManager::class.java)?.isAcceptingText != true) }.getOrDefault(false)
        }
        imeConnection = connection
        imeRawBlocked.set(true)
        imeCaptureTicket = ticket // Cancel pending automatic focus work, preserving the captured target.
        imeCaptureEpoch = focus.epoch
        imeRawReturn = null
        connection.start()
        updateImeGate(connection)
        watchImeDeadline(connection)
        owner
    }
    private fun ownsIme(owner:ImeEditorOwner)=imeOwner===owner && imeConnection===owner.connection && owner.runtime==handle
    /** 同ownerに正しいPresentation Contextを一度だけ付ける。古いViewの到着で新接続を作らない。 */
    fun attachImeEditor(owner:ImeEditorOwner,context:Context,preImeKey:(KeyEvent)->Boolean={false},
        windowFocusChanged:(Boolean)->Unit={}):ImeEditorView? = inputIntents.serial {
        check(Looper.myLooper()==Looper.getMainLooper())
        val connection=owner.connection
        if(!ownsIme(owner) || owner.attached || !imeCaptureCurrent() || connection.metadata().phase in setOf(ImeSessionPhase.CLOSING,ImeSessionPhase.CLOSED)) return@serial null
        owner.attached=true
        fun editorAccepts():Boolean=ownsIme(owner) && inputIntents.get()==owner.ticket && owner.view.get()?.let {it.hasFocus() && it.hasWindowFocus()}==true
        val view=try { ImeEditorView(context,
            onTransaction = { editorAccepts() && connection.submit(it) },
            onKeyEvent = { event ->
                val generation = owner.view.get()?.metadata()?.generation
                val captured = generation?.let(connection::keyFocus)
                editorAccepts() && captured != null && sendImeNonTextKey(connection, captured, event)
            },
            onEditorAction = { false },
            onInvalidated = { generation, reason ->
                if (reason == "focus_lost") connection.close() else connection.editorInvalidated(generation)
            },
            onConnectionOpened = {if(ownsIme(owner) && inputIntents.get()==owner.ticket) connection.openEditor(it)},
            canEditGeneration = { editorAccepts() && connection.acceptsEditor(it) }, preImeKey = preImeKey, windowFocusChanged = windowFocusChanged)
        } catch(_:Exception) {connection.close();return@serial null}
        owner.view=java.lang.ref.WeakReference(view)
        view
    }
    fun debugCreateImeEditor(context:Context,preImeKey:(KeyEvent)->Boolean={false},windowFocusChanged:(Boolean)->Unit={}):ImeEditorView? {
        if(!BuildConfig.DEBUG) return null
        val owner=beginImeOwner() ?: return null
        return attachImeEditor(owner,context,preImeKey,windowFocusChanged).also {if(it==null) closeImeOwner(owner,false)}
    }

    fun closeImeOwner(owner:ImeEditorOwner,restoreRaw:Boolean=false) = inputIntents.serial {
        check(Looper.myLooper() == Looper.getMainLooper())
        if(!ownsIme(owner)) return@serial
        val connection = owner.connection
        if (restoreRaw && imeRawReturn == null) {
            lateinit var returning: ImeRawFocusReturn
            returning = ImeRawFocusReturn(connection.runtime, connection.target, imeCaptureTicket, imeCaptureEpoch,
                owner.token, SystemClock::elapsedRealtime, { requests.incrementAndGet().toString() },
                { request, epoch -> enqueueImeRawFocus(connection, returning, request, epoch) })
            imeRawReturn = returning
        }
        connection.close()
        updateImeGate(connection)
    }
    fun debugCloseImeEditor(restoreRaw:Boolean=false) {if(BuildConfig.DEBUG) imeOwner?.let {closeImeOwner(it,restoreRaw)}}
    fun imeRawReturnStatus():ImeRawReturnMetadata?=imeRawReturn?.metadata()
    fun debugImeRawReturnStatus(): ImeRawReturnMetadata? = if (BuildConfig.DEBUG) imeRawReturn?.metadata() else null
    fun imeReady(): Boolean = !debugSdkLease.blocksProduct() && !imeRawBlocked.get() && !BuildHooks.imeProbeActive() && keyboardFocus?.let {
        it.runtime == handle && !movingTarget(it.target) && windowGroupInputAllowed(it.runtime,JSONObject(it.target)) && !editing.get() && panelTextInputs.isEmpty() && activityVisible.get() && xrState.get() == "FOCUSED"
    } == true
    fun debugImeReady():Boolean=BuildConfig.DEBUG && imeReady()
    fun debugImeCanRequestFocus(): Boolean = BuildConfig.DEBUG && !debugSdkLease.blocksProduct() && handle!=0L && !imeRawBlocked.get() &&
        !editing.get() && panelTextInputs.isEmpty() && activityVisible.get() && xrState.get()=="FOCUSED"
    fun debugImeFocusObserved(request: DebugImeFocusReceipt): Boolean {
        if(!debugImeReady() || inputIntents.get()!=request.ticket) return false
        val focus=imeFocus(keyboardFocus) ?: return false
        return focus.runtime==request.runtime && focus.target==request.target && focus.epoch>request.beforeEpoch && focus.token!=request.beforeToken
    }
    fun debugImeFocusedTarget(): ImeSdkTarget? = if (debugImeReady()) keyboardFocus?.let { strictImeSdkTarget(JSONObject(it.target)) } else null
    /** Only an already captured, still live terminal may own an internal window handoff. */
    fun imeCaptureCurrent(): Boolean {
        val connection = imeConnection ?: return false
        return !debugSdkLease.blocksProduct() && !movingTarget(imeSdkJson(connection.target).toString()) && windowGroupInputAllowed(connection.runtime,imeSdkJson(connection.target)) &&
            connection.runtime == handle && inputIntents.get() == imeCaptureTicket && activityVisible.get() &&
            xrState.get() == "FOCUSED" && !editing.get() && panelTextInputs.isEmpty() &&
            inputTargets.values.any { it.runtime == connection.runtime && strictImeSdkTarget(JSONObject(it.target)) == connection.target }
    }
    fun debugImeCaptureCurrent():Boolean=BuildConfig.DEBUG && imeCaptureCurrent()
    private fun enqueueImeRawFocus(connection: ImeSessionConnection, returning: ImeRawFocusReturn, request: String, epoch: Long): ImeEnqueueResult = inputIntents.serial {
        if (imeConnection !== connection || imeRawReturn !== returning || !imeCaptureCurrent()) return@serial ImeEnqueueResult.UNAVAILABLE
        val target = inputTargets.values.firstOrNull { it.runtime == returning.runtime && it.epoch == epoch &&
            strictImeSdkTarget(JSONObject(it.target)) == returning.target } ?: return@serial ImeEnqueueResult.UNAVAILABLE
        imeEnqueueCode(NativeBridge.nativeCommand(returning.runtime, JSONObject().put("schema",1)
            .put("type","focus_window").put("request_id",request).put("target",JSONObject(target.target)).put("input_epoch",epoch).toString()))
    }
    private fun updateImeGate(connection: ImeSessionConnection) {
        if (imeConnection !== connection) return
        val metadata = connection.metadata()
        val returning = imeRawReturn
        val blocked = metadata.rawBlocked || !debugImeAndroidReleased() || returning?.blocksRaw() == true
        imeRawBlocked.set(blocked)
        imeInputMessage = when {
            metadata.phase == ImeSessionPhase.BOUND -> InputStatus("IME_CONNECTED", "IME入力 · Ctrl+Spaceで直接入力へ")
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
    fun imeStatus(owner:ImeEditorOwner):ImeSessionMetadata? {
        check(Looper.myLooper()==Looper.getMainLooper())
        return if(imeOwner===owner) owner.connection.metadata() else null
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
    private fun enqueueImeCommand(connection: ImeSessionConnection, request: String, command: ImeSessionCommand): ImeEnqueueResult = inputIntents.serial {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (imeConnection !== connection || connection.runtime != handle || handle == 0L) return@serial ImeEnqueueResult.UNAVAILABLE
        if(command is ImeSessionCommand.Focus || command is ImeSessionCommand.Transaction || command is ImeSessionCommand.Backend && command.enabled) {
            val owner=imeOwner ?: return@serial ImeEnqueueResult.UNAVAILABLE
            if(owner.connection!==connection || inputIntents.get()!=owner.ticket || debugSdkLease.blocksProduct() || movingTarget(imeSdkJson(connection.target).toString()) || !windowGroupInputAllowed(connection.runtime,imeSdkJson(connection.target)) ||
                editing.get() || panelTextInputs.isNotEmpty() || !activityVisible.get() || xrState.get()!="FOCUSED") return@serial ImeEnqueueResult.UNAVAILABLE
        }
        val envelope = JSONObject().put("schema", 1).put("request_id", request)
        when (command) {
            is ImeSessionCommand.Backend -> envelope.put("type", "set_ime_backend").put("session", connection.target.session).put("enabled", command.enabled)
            ImeSessionCommand.Reset -> envelope.put("type", "reset_input")
            is ImeSessionCommand.Focus -> {
                val target = inputTargets.values.firstOrNull { it.runtime == connection.runtime && it.epoch == command.epoch &&
                    strictImeSdkTarget(JSONObject(it.target)) == connection.target } ?: return@serial ImeEnqueueResult.UNAVAILABLE
                envelope.put("type", "focus_window").put("target", JSONObject(target.target)).put("input_epoch", command.epoch)
            }
            is ImeSessionCommand.Transaction -> {
                if (!imeFocusCurrent(connection, command.focus)) return@serial ImeEnqueueResult.UNAVAILABLE
                envelope.put("type", "ime").put("target", imeSdkJson(connection.target)).put("transaction", JSONObject(command.payload.toPayloadJson()))
            }
        }
        // This private envelope is consumed synchronously and never retained in diagnostics/ACK maps.
        imeEnqueueCode(NativeBridge.nativeCommand(connection.runtime, envelope.toString()))
    }
    private fun imeFocusCurrent(connection: ImeSessionConnection, focus: ImeSessionFocus) =
        imeConnection === connection && imeOwner?.let {it.connection===connection && it.ticket==inputIntents.get()}==true &&
            !debugSdkLease.blocksProduct() && !movingTarget(imeSdkJson(connection.target).toString()) && windowGroupInputAllowed(connection.runtime,imeSdkJson(connection.target)) && connection.runtime == handle && focus.target == connection.target &&
            imeFocus(keyboardFocus) == focus && !editing.get() && panelTextInputs.isEmpty() && activityVisible.get() && xrState.get() == "FOCUSED"

    private fun sendImeNonTextKey(connection: ImeSessionConnection, focus: ImeSessionFocus, event: KeyEvent): Boolean = inputIntents.serial {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!imeFocusCurrent(connection, focus) || event.deviceId < 0 || event.isCanceled || event.isSystem ||
            event.action !in setOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP)) return@serial false
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
        if (!nonText && !controlLetter && !ownedControlUp) return@serial false
        val key = JSONObject().put("device", event.deviceId).put("scan_code", event.scanCode).put("key_code", event.keyCode)
            .put("action", if (event.action == KeyEvent.ACTION_DOWN) "pressed" else "released").put("repeat", event.repeatCount)
            .put("meta", event.metaState).put("time_ms", event.eventTime and 0xffffffffL)
        val code = NativeBridge.nativeCommand(focus.runtime, JSONObject().put("schema", 1).put("type", "key")
            .put("request_id", requests.incrementAndGet().toString()).put("target", imeSdkJson(focus.target))
            .put("input_epoch", focus.epoch).put("focus_token", focus.token).put("key", key).toString())
        if (code != NativeBridge.ACCEPTED) connection.close("key_rejected")
        code == NativeBridge.ACCEPTED
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
        if (movingTarget(focus.target) || !windowGroupInputAllowed(focus.runtime,JSONObject(focus.target))) return false
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
    fun runtimeConfigJson(): String {
        val config=JSONObject().put("schema",1).put("paths",checkNotNull(paths)).put("installation",checkNotNull(identity).installation)
            .put("profiles",profiles).put("preferences_dir",checkNotNull(preferencesDir))
        linuxPins()?.let {pins ->
            config.put("linux_profile_v2",ConnectionProfileV2Codec.encode(checkNotNull(linuxProfile)).toString(Charsets.UTF_8))
                .put("linux_desktop_pins",pins.json())
        }
        return BuildHooks.runtimeConfiguration(config.toString())
    }
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
    class DebugSdkProbeConfig(val configuration: String, val profile: ConnectionProfileV2.Value, val mode:String="pattern") {
        override fun toString() = "DebugSdkProbeConfig(<private>)"
    }
    data class DebugSdkProbeReceipt(val runtime: Long, val connectRequest: String?, val phase: DebugSdkRuntimeLease.Phase, val error: String?)
    data class DebugSdkCommandReceipt(val runtime: Long, val request: String?, val code: Int)
    fun debugSdkProbeCommand(owner: Any, type: String, session: String, nonce: String?, gtkCase:String?=null, completed: (DebugSdkCommandReceipt) -> Unit) {
        val captured = debugSdkLease.metadata()
        if (!BuildConfig.DEBUG || !debugSdkLease.owns(owner) || captured.phase != DebugSdkRuntimeLease.Phase.ACTIVE ||
            type !in setOf("linux_shm_sdk_probe", "linux_gtk_probe", "linux_gtk_recover", "disconnect") || !session.matches(Regex("[1-9][0-9]{0,18}")) ||
            session.toLongOrNull() == null || (type != "disconnect" && nonce?.matches(Regex("[0-9a-f]{16}")) != true) ||
            (type == "linux_shm_sdk_probe" && (debugSdkMode!="pattern" || gtkCase!=null)) ||
            (type in setOf("linux_gtk_probe","linux_gtk_recover") && (gtkCase !in setOf("input","dialog","resize") || debugSdkMode!="linux_gtk_$gtkCase" || debugSdkRemoteStarted)) ||
            (type == "disconnect" && (nonce != null || gtkCase!=null))) {
            completed(DebugSdkCommandReceipt(captured.runtime,null,NativeBridge.STALE)); return
        }
        worker.execute {
            if (!debugSdkLease.owns(owner) || debugSdkLease.metadata().let { it.phase != DebugSdkRuntimeLease.Phase.ACTIVE || it.runtime != captured.runtime } || handle != captured.runtime) {
                ui.post { completed(DebugSdkCommandReceipt(captured.runtime,null,NativeBridge.STALE)) }; return@execute
            }
            val request = requests.incrementAndGet().toString()
            val command = JSONObject().put("schema",1).put("type",type).put("request_id",request).put("session",session)
            nonce?.let { command.put("nonce",it) };gtkCase?.let {command.put("case",it)}
            val code = inputIntents.serial {
                if(type in setOf("linux_gtk_probe","linux_gtk_recover") && debugSdkRemoteStarted) NativeBridge.STALE
                else NativeBridge.nativeCommand(handle,command.toString()).also {code->
                    if(code==NativeBridge.ACCEPTED && type in setOf("linux_gtk_probe","linux_gtk_recover")) {
                        debugSdkRemoteStarted=true
                        if(type=="linux_gtk_probe") debugSdkInputScope=DebugSdkInputScope(owner,handle,session,checkNotNull(nonce),checkNotNull(gtkCase),scenes.get())
                    }
                }
            }
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
                check(prepared.profile.backend == ConnectionProfileV2.Backend.LINUX_SSH && prepared.profile.profileId == "linux-probe" && prepared.mode in setOf("pattern","linux_gtk_input","linux_gtk_dialog","linux_gtk_resize")) { "PROBE_PROFILE_INVALID" }
                debugSdkMode=prepared.mode;debugSdkInputScope=null;debugSdkRemoteStarted=false
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
                inputIntents.serial {
                    debugSdkInputPreparation?.let {it.readiness.cancel("PROBE_INPUT_OWNER_CHANGED");inputIntents.release(it.ticket)}
                    debugSdkInputPreparation=null;debugSdkInputScope=null;inputIntents.invalidate()
                }
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
    fun sdkBinding(target: JSONObject, runtime: Long, owned: Boolean, diagnostic: Boolean = false): Int {
        val command=JSONObject().put("schema",1).put("type",if(owned) "register_sdk_binding" else "release_sdk_binding")
            .put("request_id",requests.incrementAndGet().toString()).put("target",target)
        if(owned) command.put("diagnostic",diagnostic)
        return NativeBridge.nativeCommand(runtime,command.toString())
    }
    private fun stopRuntime() {
        if (handle == 0L) return
        invalidateImeConnection()
        keyboardFocus = null
        inputTargets = emptyMap()
        groupInputGate.set(WindowGroupInputGate.unavailable())
        windowOperation.set(null);windowOperationCompletions.clear()
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
        ui.post { if (imeConnection === endedIme) { endedIme?.close(); imeConnection = null; imeOwner = null; imeAndroidReleased = null; imeRawReturn = null; imeInputMessage = null; imeRawBlocked.set(false) } }
        catalogPending=null;launcherUpdate(LauncherUi(path=launcherState.path))
        linuxCredentialCapture?.close();linuxCredentialCapture=null;linuxAction=null;linuxCatalog=null;linuxMessage=null
        poseCompletions.clear();groupGeometryCompletions.clear();selectedWindow=null;lastFocusedWindow=null;editing.set(false);ui.post { layoutEditing.value=false }
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
                inputIntents.serial {
                    debugInputActive?.takeIf {it.runtime==handle}?.resize?.token?.takeIf {it.request.toString()==event.getString("request_id")}
                        ?.acknowledge(event.getString("code")=="ACCEPTED",SystemClock.elapsedRealtime())
                }
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
                groupGeometryCompletions.computeIfPresent(event.getString("request_id")) { _, _ -> event.getString("code") }
                poseCompletions.computeIfPresent(event.getString("request_id")) { _, _ -> event.getString("code") }
                windowOperationCompletions.computeIfPresent(event.getString("request_id")) { _, _ -> event.getString("code") }
                if (event.getString("request_id") == lastConnectRequest) {
                    connectPending = false
                    if (event.getString("code") != "ACCEPTED") {
                        lastConnectRequest = null;linuxCredentialCapture?.close();linuxCredentialCapture=null
                    }
                }
                linuxAction?.let {operation ->
                    if(event.getString("request_id")==operation.request && event.getString("code")!="ACCEPTED") {
                        linuxAction=null;linuxMessage="操作を受け付けられませんでした。接続と回収の状態を確認してください。"
                    }
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
            } else if(event.optString("type")=="credential_required" && event.optString("profile_id")==LinuxConnectionProfile.ID) {
                try {
                    require(event.getString("request_id").toLong()>cancelledThrough)
                    val envelope=JSONObject(NativeBridge.nativeCredentialScope(handle))
                    val fields=envelope.keys().asSequence().associateWith {key -> val value=envelope.get(key)
                        if(value is JSONObject) value.keys().asSequence().associateWith {value.get(it)} else value }
                    val supply=checkNotNull(linuxCredentialCapture).claim(handle,event.opt("session"),event.opt("request_id"),event.opt("profile_id"),fields)
                    val code=checkNotNull(linuxCredentials).withPrivateKey {key ->
                        NativeBridge.nativeProvideCredentialFor(handle,supply.session,supply.request,supply.binding,key)
                    }
                    check(code==NativeBridge.ACCEPTED)
                } catch(_:Exception) {linuxMessage="接続要求と認証鍵の対応を確認できません。接続を閉じて再試行してください。"}
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
        groupInputGate.set(WindowGroupWire.decode(handle,runtime.optJSONObject("window_groups")))
        pendingInputFocus?.let { request ->
            val target=JSONObject(request.target)
            val plans=runtime.getJSONArray("panels")
            val current=(0 until plans.length()).map {plans.getJSONObject(it)}.firstOrNull {p ->
                p.optString("action")=="create" && p.isNull("error") && targetIdentity(p.getJSONObject("target"))==targetIdentity(target)
            }?.getJSONObject("target")
            if(SystemClock.elapsedRealtime()>request.deadline || editing.get() || !activityVisible.get() || xrState.get()!="FOCUSED" ||
                request.runtime!=handle || request.ticket!=inputIntents.get() ||
                current==null || targetIdentity(current)!=targetIdentity(target) || !windowGroupInputAllowed(request.runtime,target) ||
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
                plan.optString("action")=="create" && plan.optBoolean("applied") && plan.isNull("error") && !movingTarget(plan.getJSONObject("target").toString()) && windowGroupInputAllowed(handle,plan.getJSONObject("target")) &&
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
            runtime.getString("xr_state") == "FOCUSED" && focused != null && !movingTarget(focused.getJSONObject("target").toString()) && windowGroupInputAllowed(handle,focused.getJSONObject("target")))
            KeyboardFocus(handle, runtime.getLong("input_resets"), focused.getJSONObject("target").toString(), runtime.getJSONObject("app").getJSONObject("input").getString("focus_token")) else null
        val eligibleIme=keyboardFocus?.let {f->strictImeSdkTarget(JSONObject(f.target))?.let {ImeTerminalEligibility.allows(runtime,it)}}==true &&
            !debugSdkLease.blocksProduct() && !imeRawBlocked.get() && !editing.get() && panelTextInputs.isEmpty() && !BuildHooks.imeProbeActive()
        ui.post {imeAvailable.value=eligibleIme}
        pumpIme(runtime, imeCompletions)
        val keyboardLabel = if (keyboardFocus == null) "操作パネルへ入力" else if(selectedProfileId==LinuxConnectionProfile.ID) "Linuxアプリへ入力" else "ターミナルへ入力"
        ui.post { keyboardSummary.value = keyboardLabel }
        val inputConditions=InputConditions(runtime.getBoolean("activity_visible"),runtime.getString("xr_state")=="FOCUSED",
            runtime.getString("state")=="Ready",if(terminalOpen?.startupWaiting==true) "StartingTermux" else runtime.getJSONObject("transport").getString("state"),editing.get(),keyboardCount.get(),
            (runtime.optJSONObject("app")?.optJSONArray("windows")?.length() ?: 0)>0,inputTargets.isNotEmpty(),
            focusedWindow.takeIf {keyboardFocus!=null},panelEditing=panelTextInputs.isNotEmpty(),imeProbe=BuildHooks.imeProbeActive(),
            linuxDesktop=selectedProfileId==LinuxConnectionProfile.ID)
        val status=if(debugSdkLease.blocksProduct()) InputStatus("FIXED_LINUX_PROBE","Linux描画の固定検査中 · 通常操作と文字入力を停止")
        else if(inputConditions.visible && inputConditions.xrFocused) {
            val move=windowOperation.get()
            if(move!=null) {
                val name=JSONObject(move.control.target).getString("window")
                when(move.phase) {
                    "menu" -> InputStatus("WINDOW_MENU","窓 $name の操作メニュー · その窓の文字入力を停止")
                    "resizing" -> InputStatus("WINDOW_RESIZING","窓 $name の表示サイズを確認中 · その窓の文字入力を停止")
                    "recentering" -> InputStatus("WINDOW_RECENTERING","窓 $name の正面配置を確認中 · その窓の文字入力を停止")
                    "cleanup" -> InputStatus("WINDOW_OPERATION_CLEANUP","窓 $name の操作終了を確認中")
                    "returning" -> InputStatus("WINDOW_RETURNING","窓 $name への入力復帰を確認中")
                    else -> InputStatus("WINDOW_MOVING","窓 $name を移動中 · その窓の文字入力を停止")
                }
            }
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
            "ProbeReady" -> "SSH認証済み · アプリ未確認"
            "DesktopReady" -> "Linuxアプリ接続済み"
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
        val connectionError=if(selectedProfileId==LinuxConnectionProfile.ID) LinuxDesktopMessages.error(transport.optString("error")) else when(transport.optString("error")) {
            "HOST_KEY_UNKNOWN" -> "初回の接続設定が必要です"
            "HOST_KEY_MISMATCH" -> "Termuxのホスト鍵が保存済みの鍵と違います。接続設定で指紋を照合してください"
            "AUTH_FAILED" -> "公開鍵認証に失敗しました。TermuxへAPK公開鍵を登録してください"
            "SSH_CONNECTION_REFUSED", "AUTH_TIMEOUT", "SSH_CONNECT_FAILED", "CONNECT_TIMEOUT" -> "Termuxでsshdを起動し、ターミナルを開き直してください"
            "REMOTE_EXIT", "SSH_IO_FAILED" -> "接続先が終了しました。Termuxを確認し、ターミナルを開き直してください"
            else -> transport.optString("error").takeUnless {it.isEmpty() || it=="null"}
        }
        val ssh = phase + (if (pending > 0 && transport.getString("state")!="Ready") " · 未確認の回収 ${pending}件" else "") +
            (if(transport.optBoolean("desktop_pending") && transport.getString("state") in setOf("Idle","Disconnected","Failed","CleanupPending")) " · 前回のLinuxアプリの終了が未確認" else "") +
            ((if(selectedProfileId==LinuxConnectionProfile.ID) linuxMessage ?: connectionError ?: error?.let {"アプリまたは画面の処理を完了できませんでした"} else error ?: connectionError)?.let {" · $it"} ?: "")
        if (ssh != sshState) { sshState = ssh; ui.post { connectionSummary.value = ssh } }
        val busy=terminalOpen!=null || terminalAdding!=null || connectPending || pendingDisconnect!=null || linuxAction!=null
        ui.post {terminalBusy.value=busy}
        val next = snapshot.getJSONObject("runtime").getString("state")
        val editable=canChangeConnection(snapshot.getJSONObject("runtime"))
        val recoverySelection=canSelectRecovery(snapshot.getJSONObject("runtime"))
        if(canSelectLinuxRecovery.value!=recoverySelection) ui.post {canSelectLinuxRecovery.value=recoverySelection}
        if(editable!=connectionEditable) {connectionEditable=editable;ui.post {
            connectionUi.value=connectionUi.value.copy(canSave=editable);linuxConnectionUi.value=linuxConnectionUi.value.copy(canSave=editable)
        }}
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
