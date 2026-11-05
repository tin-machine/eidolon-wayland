package io.github.tinmachine.eidolon

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONArray
import org.json.JSONObject

object DebugNativeBridge {
    @JvmStatic external fun nativePanic(handle: Long): Int
    @JvmStatic external fun nativeVerifyPin(pin: String?, candidate: String): String
}

/** ADBから受け取るのは固定caseとnonceのみ。任意JSON/path/shellは受け取らない。 */
class DebugCommandActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val name = intent.getStringExtra("case")
        val nonce = intent.getStringExtra("nonce")
        if (name == null || name !in setOf("foot", "runtime", "helper", "surface", "pattern", "surface-cycle", "credentials", "ssh", "ssh-product", "forward", "remote-cleanup", "native-deps", "shm", "shm-truncate", "wayland-state", "waypipe", "forwarded-shm") || nonce == null || !nonce.matches(Regex("[0-9a-f]{32}")) ||
            intent.extras?.keySet()?.any { it != "case" && it != "nonce" } == true || !running.compareAndSet(false, true)) {
            Log.w(TAG, "check_rejected")
            finish()
            return
        }
        val application = applicationContext
        NativeSessionHost.initialize(application)
        if (name in setOf("foot", "surface", "pattern", "surface-cycle", "ssh-product")) {
            application.startActivity(Intent(application, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra("case", name).putExtra("nonce", nonce))
            running.set(false)
            finish()
            return
        }
        NativeSessionHost.withStoppedRuntime({ store ->
            if (name == "runtime") RuntimeCase.run(nonce, store)
            else if (name == "helper") HelperCase.run(nonce, NativeSessionHost.runtimeConfigJson(), store)
            else if (name == "ssh") SshCase.run(nonce, application, NativeSessionHost.runtimeConfigJson(), store)
            else if (name == "forwarded-shm") ForwardedShmCase.run(nonce, application, NativeSessionHost.runtimeConfigJson(), store)
            else if (name == "forward") ForwardCase.run(nonce, application, NativeSessionHost.runtimeConfigJson(), store)
            else if (name == "remote-cleanup") RemoteCleanupCase.run(nonce, application, NativeSessionHost.runtimeConfigJson(), store)
            else if (name == "waypipe") WaypipeCase.run(nonce, NativeSessionHost.runtimeConfigJson(), store)
            else if (name == "native-deps") NativeDepsCase.run(nonce, NativeSessionHost.runtimeConfigJson(), store)
            else if (name in setOf("shm", "shm-truncate", "wayland-state")) CompositorCase.run(name, nonce, NativeSessionHost.runtimeConfigJson(), store)
            else CredentialCase.run(nonce, application, store)
        }, {
            running.set(false)
            application.startActivity(Intent(application, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        })
        // NoDisplay ActivityはonResumeまでに終了する必要がある。workerはApplication scope。
        finish()
    }

    companion object {
        private val running = AtomicBoolean(false)
        private const val TAG = "EidolonCheck"
    }
}

private object RuntimeCase {
    private const val TAG = "EidolonCheck"
    fun run(nonce: String, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "runtime").put("nonce", nonce).put("result", "running")
        val cycles = JSONArray()
        var active = 0L
        var previous = 0L
        val started = SystemClock.elapsedRealtime()
        store.publishCheck(result)
        try {
            var rejected = false
            try { NativeBridge.nativeCreate("{\"schema\":2}") } catch (_: IllegalArgumentException) { rejected = true }
            check(rejected)
            repeat(10) { cycle ->
                active = NativeBridge.nativeCreate("{\"schema\":1}")
                check(active > previous)
                check(NativeBridge.nativeDestroy(active) == NativeBridge.BUSY)
                waitFor { state(active) == "Ready" }
                check(JSONObject(NativeBridge.nativeSnapshot(active)).getJSONObject("registry").getInt("workers") == 1)
                for (invalid in listOf("{}", "{\"schema\":2,\"type\":\"reset_input\",\"request_id\":\"1\"}",
                    "{\"schema\":1,\"type\":\"shell\",\"request_id\":\"1\"}", "x".repeat(16385))) {
                    check(NativeBridge.nativeCommand(active, invalid) == NativeBridge.INVALID_ARGUMENT)
                }
                check(JSONObject(NativeBridge.nativePoll(active, 0)).getInt("code") == NativeBridge.INVALID_ARGUMENT)
                val command = "{\"schema\":1,\"type\":\"scene_ready\",\"request_id\":\"1\",\"scene\":\"1\"}"
                check(NativeBridge.nativeCommand(active, command) == NativeBridge.ACCEPTED)
                var completion = false
                waitFor {
                    val events = JSONObject(NativeBridge.nativePoll(active, 64)).getJSONArray("events")
                    for (i in 0 until events.length()) {
                        val event = events.getJSONObject(i)
                        if (event.optString("type") == "command_complete" && event.optString("request_id") == "1") {
                            check(event.getString("code") == "ACCEPTED")
                            completion = true
                        }
                    }
                    completion
                }
                if (cycle == 9) {
                    var panicConverted = false
                    try { DebugNativeBridge.nativePanic(active) } catch (_: RuntimeException) { panicConverted = true }
                    check(panicConverted && state(active) == "Failed")
                    check(NativeBridge.nativeCommand(active, command) == NativeBridge.FAILED)
                }
                check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
                check(NativeBridge.nativeRequestStop(active) == NativeBridge.ACCEPTED)
                waitFor { state(active) == "Stopped" }
                if (cycle == 9) {
                    var latePanicConverted = false
                    try { DebugNativeBridge.nativePanic(active) } catch (_: RuntimeException) { latePanicConverted = true }
                    check(latePanicConverted && state(active) == "Stopped")
                }
                var destroyed = false
                waitFor {
                    val code = NativeBridge.nativeDestroy(active)
                    check(code == NativeBridge.ACCEPTED || code == NativeBridge.BUSY)
                    destroyed = code == NativeBridge.ACCEPTED
                    destroyed
                }
                check(destroyed && NativeBridge.nativeDestroy(active) == NativeBridge.STALE)
                check(NativeBridge.nativeRequestStop(active) == NativeBridge.STALE)
                val stale = JSONObject(NativeBridge.nativeSnapshot(active))
                check(stale.getInt("code") == NativeBridge.STALE)
                check(stale.getJSONObject("registry").getInt("runtimes") == 0 && stale.getJSONObject("registry").getInt("workers") == 0)
                cycles.put(JSONObject().put("cycle", cycle + 1).put("handle", active.toString()).put("owned_workers", 0))
                previous = active
                active = 0
                result.put("cycles", cycles)
                store.publishCheck(result)
            }
            result.put("result", "pass").put("panic_converted", true).put("duration_ms", SystemClock.elapsedRealtime() - started)
        } catch (failure: Exception) {
            result.put("result", "fail").put("failure_class", failure.javaClass.simpleName)
        } finally {
            if (active != 0L) {
                NativeBridge.nativeRequestStop(active)
                try { waitFor { NativeBridge.nativeDestroy(active) != NativeBridge.BUSY } } catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            store.publishCheck(result)
            Log.i(TAG, "check_finished case=runtime result=${result.getString("result")} cycles=${cycles.length()}")
        }
    }
    private fun state(handle: Long): String = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime").getString("state")
    private fun waitFor(predicate: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 5000
        while (!predicate()) { check(SystemClock.elapsedRealtime() < deadline) { "RUNTIME_TIMEOUT" }; Thread.sleep(2) }
    }
}
