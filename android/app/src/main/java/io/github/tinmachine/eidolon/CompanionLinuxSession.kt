package io.github.tinmachine.eidolon

import android.os.SystemClock
import org.json.JSONObject

/** One independently recoverable Linux app on the same saved SSH profile. Host-worker only. */
internal class CompanionLinuxSession(
    val slot: Int,
    private val nextRequest: () -> String,
    private val provide: (Long, LinuxCredentialCapture, JSONObject) -> Boolean,
) {
    @Volatile var handle = 0L
        private set
    @Volatile var snapshot: JSONObject? = null
        private set
    var pending = false
        private set
    var message: String? = null
        private set
    private var selection: String? = null
    private var credentials: LinuxCredentialCapture? = null
    private var profile: ConnectionProfileV2.Value? = null
    private var capture = ""
    private data class Request(val id: String, val type: String)
    private var request: Request? = null
    private var lastRejection: String? = null
    private var connectRequest: String? = null
    private var initialConnectSent = false
    private var inspectSent = false
    private var deadline = 0L
    private var stopping = false
    private val recoveryRetry = DesktopRecoveryRetry()
    private var retired = false
    private var launchSent = false
    private var wasRunning = false
    private var onLaunch: ((Long, String, String) -> Unit)? = null

    val occupiesApp get() = handle != 0L && (!browsing || selection != null || launchSent || stopping)
    var browsing = false
        private set
    var shelf = LauncherUi(linux = true)
        private set
    private var shownCatalog: LinuxDesktopV3Catalog? = null

    fun browse(primary: Long, profile: ConnectionProfileV2.Value, capture: String, scene: Long, xr: String, visible: Boolean) {
        if (handle == 0L && !begin(primary, profile, capture, null, scene, xr, visible)) {
            shelf = LauncherUi(linux = true, firstFailure = "NOT_READY", message = message ?: "Ubuntu に接続できません")
            return
        }
        if (stopping || this.capture != capture) return
        browsing = true
        if (shownCatalog == null) shelf = LauncherUi(linux = true, busy = true, message = "Ubuntu に接続中")
        else if (!wasRunning && request == null && !launchSent) {
            snapshot?.optJSONObject("transport")?.optString("session")?.let {
                pending = true
                deadline = SystemClock.elapsedRealtime() + 30000
                command("linux_desktop_v3_inspect", JSONObject().put("session", it))
            }
        }
    }

    fun selectApp(expected: LauncherUi, id: String) {
        val catalog = shownCatalog ?: return
        if (!browsing || stopping || launchSent || request != null ||
            !catalog.accepts(handle, expected.session, expected.capture, expected.revision, id)) return
        selection = id
        pending = true
        deadline = SystemClock.elapsedRealtime() + 30000
        launch(handle, expected.session)
        shelf = shelf.copy(busy = true, message = "起動中")
    }

    fun begin(
        primary: Long, profile: ConnectionProfileV2.Value, capture: String, app: String?,
        scene: Long, xr: String, visible: Boolean, launched: ((Long, String, String) -> Unit)? = null,
    ): Boolean {
        if (handle != 0L) return false
        val created = NativeBridge.nativeCreateCompanion(primary, slot)
        if (created <= 0L) {
            message = if (created == -NativeBridge.BUSY.toLong()) "追加アプリの描画処理がまだ回収中です。"
                else "追加アプリのウィンドウの準備に失敗しました。接続と回収の状態を確認してください。"
            return false
        }
        handle = created
        this.profile = profile
        this.capture = capture
        selection = app
        onLaunch = launched
        pending = true
        lastRejection = null
        deadline = SystemClock.elapsedRealtime() + 30_000
        message = if (app == null) "追加アプリの回収を確認中" else "追加アプリを準備中"
        if (scene > 0) sceneCommand("scene_ready", "scene", scene.toString())
        sceneCommand("set_xr_state", "state", xr, scene)
        sceneCommand("set_activity_state", "visible", visible, scene)
        return true
    }

    fun sceneCommand(type: String, key: String, value: Any, scene: Long = 0L) {
        if (handle <= 0L) return
        val command = JSONObject().put(key, value)
        if (scene > 0) command.put("expected_scene", scene.toString())
        send(type, command, nextRequest())
    }

    fun stop() {
        if (handle == 0L) return
        selection = null
        onLaunch = null
        stopping = true
        browsing = false
    }

    fun retryRecovery() {
        recoveryRetry.reset()
        deadline = SystemClock.elapsedRealtime() + 30_000
    }

    private fun send(type: String, args: JSONObject = JSONObject(), id: String = nextRequest()): Boolean =
        NativeBridge.nativeCommand(handle, args.put("schema", 1).put("type", type).put("request_id", id).toString()) ==
            NativeBridge.ACCEPTED

    fun diagnostics(): JSONObject = JSONObject()
        .put("running", wasRunning && !stopping)
        .put("pending", pending)
        .put("transport", snapshot?.optJSONObject("transport")?.optString("state"))
        .put("request", request?.type)
        .put("last_rejection", lastRejection)

    fun tick() {
        val current = handle
        if (current == 0L) return
        val envelope = JSONObject(NativeBridge.nativePoll(current, 64))
        val events = envelope.optJSONArray("events")
        if (events != null) for (index in 0 until events.length()) {
            val event = events.getJSONObject(index)
            val id = event.optString("request_id")
            when (event.optString("type")) {
                "credential_required" -> credentials?.let {
                    val supplied = provide(current, it, event)
                    credentials?.close()
                    credentials = null
                    if (!supplied) fail("追加アプリの SSH 認証要求と保存済みの鍵を照合できませんでした。")
                }
                "command_complete" -> request?.takeIf { id == it.id }?.let { queued ->
                    request = null
                    val code = event.getString("code")
                    if (code != "ACCEPTED" && !stopping &&
                        snapshot?.optJSONObject("transport")?.optBoolean("desktop_pending") != true) {
                        lastRejection = "${queued.type}:$code"
                        fail(when (queued.type) {
                            "connect" -> "追加アプリの SSH 接続を開始できませんでした。"
                            "linux_desktop_v3_inspect" -> "追加アプリ一覧の取得を開始できませんでした。"
                            "linux_desktop_v3_launch" -> "追加アプリの起動を受け付けられませんでした。"
                            else -> "追加アプリの回収を確認できませんでした。"
                        })
                    }
                }
            }
        }
        val state = JSONObject(NativeBridge.nativeSnapshot(current))
        val runtime = state.optJSONObject("runtime") ?: return
        snapshot = runtime
        if (retired) {
            if (NativeBridge.nativeDestroy(current) == NativeBridge.ACCEPTED) {
                shownCatalog = null
                shelf = LauncherUi(linux = true, message = message ?: "Ubuntu は未接続です")
                handle = 0
                snapshot = null
                credentials?.close()
                credentials = null
                profile = null
                selection = null
                onLaunch = null
                capture = ""
                request = null
                connectRequest = null
                initialConnectSent = false
                inspectSent = false
                retired = false
                wasRunning = false
                stopping = false
                launchSent = false
                recoveryRetry.reset()
                pending = false
            }
            return
        }
        val transport = runtime.getJSONObject("transport")
        val session = transport.optString("session")
        val status = LinuxDesktopV3Status.observe(current, session, runtime)
        if (status.ready && !stopping) {
            if (!wasRunning) message = null
            if (browsing && !wasRunning) shelf = shelf.copy(busy = false, running = true, message = "起動しました")
            wasRunning = true
            pending = false
        }
        if (pending && SystemClock.elapsedRealtime() >= deadline) {
            request = null
            fail("追加アプリの起動を期限内に確認できませんでした。")
        }
        if (request != null || runtime.optString("state") == "Starting") return
        val phase = transport.optString("state")
        val reclamation = runtime.optJSONObject("reclamation")
        val owned = transport.optInt("owned_connections")
        val cleanup = transport.optBoolean("desktop_pending")
        if (cleanup && phase == "CleanupPending") {
            if (pending && !stopping) {
                val failure = LinuxDesktopV3Messages.reason(status.firstFailure)
                    ?: LinuxDesktopMessages.error(transport.optString("error"))
                if (failure != null) fail(failure)
            }
            stop()
            val now = SystemClock.elapsedRealtime()
            when {
                reclamation?.optBoolean("recovery_command_allowed") == true && recoveryRetry.admit(false, now) -> {
                    command("linux_desktop_v3_recover", JSONObject().put("session", session))
                }
                reclamation?.optBoolean("recovery_connect_allowed") == true && recoveryRetry.admit(true, now) -> connect()
                transport.optBoolean("desktop_reboot_needed") -> {
                    pending = false
                    message = "追加アプリの回収は Quest の再起動後に続きます。"
                }
            }
            return
        }
        if (launchSent && !status.ready && (owned == 0 || transport.optJSONObject("desktop_v3_transport")?.optBoolean("terminal_observed") == true)) stop()
        if (stopping || !browsing && selection == null && !launchSent) {
            if (stopping && owned == 1) {
                if (session.toLongOrNull() != null) command("disconnect", JSONObject().put("session", session))
                else connectRequest?.let { command("cancel_connect", JSONObject().put("connect_request", it)) }
            } else if (owned == 0 && phase in setOf("Idle", "Disconnected", "Failed")) {
                retired = NativeBridge.nativeRequestStop(current) == NativeBridge.ACCEPTED
            }
            return
        }
        if (launchSent) return
        when (phase) {
            "Idle", "Disconnected", "Failed" -> {
                if (initialConnectSent) fail(LinuxDesktopMessages.error(transport.optString("error"))
                    ?: "追加アプリの接続が終了しました。")
                else initialConnectSent = connect()
            }
            "ProbeReady" -> if (!inspectSent) {
                // ProbeReady persists while SSH discovery and the helper's inspection run.
                command("linux_desktop_v3_inspect", JSONObject().put("session", session))
                inspectSent = request != null
            }
            "DesktopReady" -> if (browsing && selection == null) {
                if (shownCatalog == null || pending) {
                    val value = readCatalog(current, session) ?: return
                    shownCatalog = value
                    pending = false
                    shelf = LauncherUi(runtime = current, session = session, capture = capture, linux = true, v3 = true,
                        revision = value.revision, entries = value.entries, total = value.entries.size, connected = true,
                        canStop = true, catalogComplete = value.installedAvailable, message = "Ubuntu のアプリ一覧を取得しました")
                }
            } else launch(current, session)
        }
    }

    private fun connect(): Boolean {
        val target = profile ?: return false
        val id = nextRequest()
        if (!send("connect", JSONObject().put("profile_id", LinuxConnectionProfile.ID), id)) return false
        credentials?.close()
        credentials = LinuxCredentialCapture(handle, id, target)
        connectRequest = id
        request = Request(id, "connect")
        return true
    }

    private fun command(type: String, args: JSONObject) {
        val id = nextRequest()
        if (send(type, args, id)) request = Request(id, type)
    }

    private fun readCatalog(runtime: Long, session: String): LinuxDesktopV3Catalog? {
        return runCatching {
            val envelope = LinuxStorageJson.parse(NativeBridge.nativeCatalogUi(runtime).toByteArray(), 528 * 1024) as Map<*, *>
            val catalog = envelope["catalog"] as Map<*, *>
            if (catalog["pending"] != false || catalog["session"] != session) return null
            val fixed = catalog["desktop_v3"] ?: return null
            LinuxDesktopV3Catalog.decode(runtime, session, capture, LinuxStorageJson.canonical(fixed),
                catalog["desktop_v3_installed"] as List<*>?, catalog["desktop_v3_editor_available"] == true)
        }.getOrElse { fail(); null }
    }

    private fun launch(runtime: Long, session: String) {
        val app = selection ?: return
        val value = readCatalog(runtime, session) ?: return
        if (!value.accepts(runtime, session, capture, value.revision, app)) {
            fail("追加アプリの接続で対象アプリの起動条件を確認できませんでした。")
            return
        }
        val id = nextRequest()
        if (send("linux_desktop_v3_launch", JSONObject().put("session", session).put("selection", value.selection(app)), id)) {
            launchSent = true
            selection = null
            request = Request(id, "linux_desktop_v3_launch")
            onLaunch?.invoke(runtime, session, id)
            onLaunch = null
        }
    }

    private fun fail(reason: String = "追加アプリの起動または回収を確認できません。") {
        if (!stopping) message = reason
        if (browsing) shelf = shelf.copy(busy = false, firstFailure = "NOT_READY", message = reason, menuRefreshFailed = true)
        pending = false
        stop()
    }
}
