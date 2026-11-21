package io.github.tinmachine.eidolon

import android.content.Context
import android.os.SystemClock
import org.json.JSONObject
import java.util.UUID

/** Worker-owned session. Browsing only opens an authenticated catalogue channel. */
internal class TermuxShelfSession(
    val slot: Int,
    private val nextId: () -> String,
    private val publish: (LauncherUi) -> Unit,
    private val dispatch: (() -> Unit) -> Unit,
) {
    @Volatile var handle = 0L
        private set
    var borrowed = false
        private set
    @Volatile var snapshot: JSONObject? = null
        private set
    private var view = LauncherUi()
    private var wantMenu = false
    private var connectSent: String? = null
    private var request: String? = null
    private var launchRequest: String? = null
    private var initialWindows = 0
    private var deadline = 0L
    private var stopping = false
    private var retiring = false
    private var helper: String? = null
    private var helperAttempted = false
    private var path = ""

    private fun update(value: LauncherUi) {
        if (view != value) { view = value; publish(value) }
    }
    private var queuedApp: Pair<String, String>? = null
    private var presentedApp = false
    private var stoppingApp = false
    val occupiesApp get() = handle != 0L && (slot > 0 || stoppingApp || presentedApp || launchRequest != null ||
        snapshot?.optJSONObject("app")?.optString("state") in setOf("running", "starting", "stopping"))
    fun accepts(expected: LauncherUi, id: String) = expected.runtime == handle && expected.session == view.session &&
        expected.revision == view.revision && view.connected && !view.busy && !view.menuRefreshFailed && view.entries.any { it.id == id && it.available }
    fun openApp(primary: Long, expected: LauncherUi, id: String, scene: Long, xr: String, visible: Boolean) {
        if (handle != 0L) return
        open(primary, false, expected.path, scene, xr, visible)
        if (handle != 0L) queuedApp = id to expected.revision
    }

    fun unavailable(reason: String) = update(LauncherUi(message = reason, firstFailure = "TERMUX_UNAVAILABLE"))
    private fun fail(reason: String) {
        if (slot > 0) stop()
        wantMenu = false
        request = null
        launchRequest = null
        update(view.copy(busy = false, menuRefreshFailed = true, message = reason, firstFailure = "TERMUX_UNAVAILABLE"))
    }
    fun open(primary: Long, usePrimary: Boolean, settingsPath: String, scene: Long, xr: String, visible: Boolean) {
        if (stopping || helper != null) return
        if (handle == 0L) {
            val newHandle = if (usePrimary) primary else NativeBridge.nativeCreateTermux(primary, slot)
            if (newHandle <= 0L) { unavailable("Termux のペアリング設定を確認してください"); return }
            borrowed = usePrimary
            handle = newHandle
            if (!borrowed) {
                if (scene > 0) sceneCommand("scene_ready", "scene", scene.toString())
                sceneCommand("set_xr_state", "state", xr, scene)
                sceneCommand("set_activity_state", "visible", visible, scene)
            }
        }
        if (request != null || wantMenu || launchRequest != null) return
        if (!view.connected) connectSent = null
        path = settingsPath
        wantMenu = true
        helperAttempted = false
        deadline = SystemClock.elapsedRealtime() + 25000
        update(view.copy(runtime = handle, busy = true, firstFailure = null, menuRefreshFailed = false, message = "Termux に接続中"))
    }
    private fun send(type: String, fields: JSONObject = JSONObject()): String? {
        val id = nextId()
        val cmd = fields.put("type", type).put("schema", 1).put("request_id", id)
        return id.takeIf { NativeBridge.nativeCommand(handle, cmd.toString()) == NativeBridge.ACCEPTED }
    }
    fun sceneCommand(type: String, key: String, value: Any, scene: Long = 0) {
        if (handle <= 0 || borrowed || retiring) return
        val args = JSONObject().put(key, value)
        if (scene > 0) args.put("expected_scene", scene.toString())
        send(type, args)
    }
    fun launch(expected: LauncherUi, id: String) {
        val state = snapshot ?: return
        if (expected.runtime != handle || expected.session != view.session || expected.revision != view.revision ||
            view.entries.none { it.id == id && it.available } || view.busy || !view.connected || view.menuRefreshFailed) return
        val app = state.optJSONObject("app")
        val running = app?.optString("state") == "running"
        val args = JSONObject().put("session", view.session)
        val target = JSONObject().put("revision", view.revision).put("id", id)
        val type = if (running) {
            args.put("target", target.put("kind", "registered"))
            "launch_terminal"
        } else {
            args.put("nonce", UUID.randomUUID().toString().replace("-", "").take(16)).put("mode", "foot")
                .put("selection", target.put("path", path).put("builtin", view.builtin))
            "start_app"
        }
        initialWindows = app?.optJSONArray("windows")?.length() ?: 0
        launchRequest = send(type, args)
        if (launchRequest == null) { fail("Termux で起動を受け付けられませんでした"); return }
        deadline = SystemClock.elapsedRealtime() + 45000
        update(view.copy(message = "起動中", busy = true))
    }
    fun stop() {
        if (handle == 0L) return
        helper?.let(TermuxStarter::cancel)
        helper = null
        stoppingApp = occupiesApp
        stopping = true
        wantMenu = false
        request = null
        launchRequest = null
    }
    fun commandComplete(id: String, code: String) {
        if (code != "ACCEPTED" && id in listOf(request, launchRequest, connectSent)) {
            if (id == connectSent) connectSent = null
            fail("Termux が要求を受け付けられませんでした（$code）")
        }
    }
    fun tick(context: Context, credentials: CredentialStore?, primarySnapshot: JSONObject? = null) {
        val owner = handle
        if (owner == 0L) return
        if (!borrowed) {
            val events = JSONObject(NativeBridge.nativePoll(owner, 64)).optJSONArray("events")
            if (events != null) for (i in 0 until events.length()) {
                val event = events.getJSONObject(i)
                val id = event.optString("request_id")
                when (event.optString("type")) {
                    "credential_required" -> if (id == connectSent && event.optString("profile_id") == "termux-local")
                        credentials?.provide(owner, event.getString("session").toLong(), id.toLong())
                    "command_complete" -> commandComplete(id, event.optString("code"))
                }
            }
        }
        val state = primarySnapshot ?: JSONObject(NativeBridge.nativeSnapshot(owner)).optJSONObject("runtime") ?: return
        snapshot = state
        if (retiring) {
            if (NativeBridge.nativeDestroy(owner) == NativeBridge.ACCEPTED) clear()
            return
        }
        val tx = state.getJSONObject("transport")
        val phase = tx.optString("state")
        val session = tx.optString("session")
        val running = state.optJSONObject("app")?.optString("state")
        if (presentedApp && (running in setOf(null, "stopped") || phase in setOf("Failed", "Disconnected"))) {
            presentedApp = false
            if (slot > 0) stop()
        }
        if (stopping) {
            if (borrowed) { clear(); return }
            if (tx.optInt("owned_connections") == 0 && phase in setOf("Idle", "Failed", "Disconnected", "CleanupPending")) {
                retiring = NativeBridge.nativeRequestStop(owner) == NativeBridge.ACCEPTED
            } else if (phase !in setOf("Disconnecting", "Recovering")) {
                if (session.toLongOrNull() != null) send("disconnect", JSONObject().put("session", session))
                else connectSent?.let { send("cancel_connect", JSONObject().put("connect_request", it)) }
            }
            return
        }
        if (state.optString("state") == "Starting") return
        if ((wantMenu || request != null || launchRequest != null) && SystemClock.elapsedRealtime() >= deadline) {
            helper?.let(TermuxStarter::cancel); helper = null
            fail("Termux の応答がありません。接続とアプリの状態を確認してください")
        }
        if (phase != "Ready") {
            if (view.connected) update(view.copy(connected = false, session = "", canStop = false, message = "Termux は未接続です"))
            if (wantMenu && helper == null && phase in setOf("Idle", "Disconnected", "Failed")) {
                if (connectSent == null) {
                    connectSent = send("connect", JSONObject().put("profile_id", "termux-local"))
                    if (connectSent == null) fail("Termux の SSH 接続を開始できません")
                } else if (phase == "Failed" && tx.optInt("owned_connections") == 0) {
                    if (tx.optString("error") == "SSH_CONNECTION_REFUSED" && !helperAttempted) {
                        helperAttempted = true
                        val token = UUID.randomUUID().toString()
                        helper = token
                        val failure = TermuxStarter.start(context, token) { error -> dispatch {
                            if (helper == token && owner == handle) {
                                helper = null; connectSent = null
                                if (error != null) fail(error)
                            }
                        } }
                        if (failure != null) { helper = null; fail(failure) }
                    } else { connectSent = null; fail("Termux の SSH 接続に失敗しました（${tx.optString("error")}）") }
                }
            }
            return
        }
        connectSent = null
        if (wantMenu) {
            wantMenu = false
            request = send("catalog", JSONObject().put("session", session).put("operation", JSONObject().put("kind", "shelf").put("path", path)))
            if (request == null) fail("Termux の一覧取得を開始できません")
        }
        if (request != null) {
            val reply = JSONObject(NativeBridge.nativeCatalogUi(owner)).optJSONObject("catalog")?.optJSONObject("termux_shelf")
            if (reply?.optString("request") == request) {
                request = null
                val data = reply?.optJSONObject("data")
                if (data == null) { fail("Termux のアプリ一覧を取得できません（${reply?.optString("error")}）"); return }
                val rows = data.getJSONArray("entries")
                val entries = List(rows.length()) {
                    val row = rows.getJSONObject(it)
                    LauncherEntry(row.getString("id"), row.getString("label"), row.getString("icon"), row.getBoolean("available"),
                        row.optString("error").takeUnless { row.isNull("error") }, row.optString("desktop_id").takeIf { it.isNotEmpty() })
                }
                update(LauncherUi(runtime = owner, session = session, path = path, connected = true, canStop = true,
                    entries = entries, total = entries.size, revision = data.getString("revision"), builtin = data.getBoolean("builtin"),
                    catalogComplete = true, message = "Termux のアプリ一覧を取得しました"))
            }
        }
        queuedApp?.takeIf { view.connected && !view.busy && !wantMenu && request == null }?.let { (id, revision) ->
            queuedApp = null
            if (revision != view.revision || !accepts(view, id)) fail("Termux の一覧が変わりました。更新してください")
            else launch(view, id)
        }
        if (launchRequest != null) {
            val app = state.optJSONObject("app")
            if ((app?.optJSONArray("windows")?.length() ?: 0) > initialWindows) {
                launchRequest = null; presentedApp = true; update(view.copy(busy = false, message = "起動しました"))
            } else if (app != null && !app.isNull("error")) fail("起動できません（${app.optString("error")}）")
        }
    }
    fun diagnostics(): JSONObject = JSONObject().apply {
        val state = snapshot ?: return@apply
        put("transport", state.optJSONObject("transport")?.optString("state"))
        put("scene", state.optLong("scene"))
        put("xr", state.optString("xr_state"))
        put("app_windows", state.optJSONObject("app")?.optJSONArray("windows")?.length() ?: 0)
        put("render_mode", state.optJSONObject("render")?.optString("mode"))
        put("panels", state.optJSONArray("panels")?.length() ?: 0)
        put("sdk_bindings", state.optInt("app_sdk_bindings"))
    }

    private fun clear() {
        queuedApp = null; presentedApp = false; launchRequest = null; wantMenu = false; request = null
        handle = 0; stoppingApp = false; borrowed = false; snapshot = null; retiring = false; stopping = false
        connectSent = null; update(LauncherUi(message = "Termux は未接続です"))
    }
}
