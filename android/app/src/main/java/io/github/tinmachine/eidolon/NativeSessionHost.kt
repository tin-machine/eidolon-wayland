package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.compose.runtime.mutableStateOf
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONObject

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
    private var lastWrite = 0L
    private var state = "Starting"
    val summary = mutableStateOf("Starting")

    init { worker.scheduleAtFixedRate({ safely { tick() } }, 0, 16, TimeUnit.MILLISECONDS) }

    fun initialize(context: Context) {
        val application = context.applicationContext
        worker.execute { safely {
            if (store == null) store = DiagnosticsStore(application)
            if (paths == null) paths = JSONObject().put("native_library_dir", application.applicationInfo.nativeLibraryDir).put("files_dir", application.filesDir.absolutePath)
            if (handle == 0L && NativeBridge.status.ready) startRuntime()
        } }
    }
    fun sceneReady(): Long {
        val scene = scenes.incrementAndGet()
        send("scene_ready", "scene", scene.toString())
        return scene
    }
    fun setXrState(value: String) { xrState.set(value); send("set_xr_state", "state", value) }
    fun setActivityVisible(value: Boolean) { activityVisible.set(value); send("set_activity_state", "visible", value) }
    fun requestSnapshot() { worker.execute { safely { publish() } } }

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
    fun runtimeConfigJson(): String = JSONObject().put("schema", 1).put("paths", checkNotNull(paths)).toString()
    private fun stopRuntime() {
        if (handle == 0L) return
        val old = handle
        check(NativeBridge.nativeRequestStop(old) == NativeBridge.ACCEPTED)
        val deadline = SystemClock.elapsedRealtime() + 5000
        while (true) {
            val code = NativeBridge.nativeDestroy(old)
            if (code == NativeBridge.ACCEPTED) break
            check(code == NativeBridge.BUSY && SystemClock.elapsedRealtime() < deadline) { "RUNTIME_STOP_TIMEOUT" }
            Thread.sleep(2)
        }
        handle = 0
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
        val snapshot = snapshot()
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
        }
    }
    private const val TAG = "EidolonHost"
}
