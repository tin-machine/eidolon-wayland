package io.github.tinmachine.eidolon

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.os.Process
import android.os.SystemClock
import java.io.File
import org.json.JSONArray
import org.json.JSONObject

/** Activity/Sceneを起動せず、同じAPK UIDとstandard JNIで固定native caseだけを試す。 */
class NativeCheckInstrumentation : Instrumentation() {
    private var name = ""
    private var nonce = ""
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        name = arguments?.getString("case") ?: ""
        nonce = arguments?.getString("nonce") ?: ""
        if (name !in setOf("forward", "remote-cleanup") || !nonce.matches(Regex("[0-9a-f]{32}")) ||
            arguments?.keySet()?.any { it != "case" && it != "nonce" } == true) {
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("result", "invalid_argument") })
            return
        }
        start()
    }
    override fun onStart() {
        val context = targetContext.applicationContext
        val store = DiagnosticsStore(context)
        val proof = JSONObject().put("mode", "instrumentation").put("case", name).put("nonce", nonce)
            .put("uid", Process.myUid()).put("pid", Process.myPid()).put("result", "running")
        var current = 0L
        var result = JSONObject().put("case", name).put("nonce", nonce).put("result", "running")
        var code = Activity.RESULT_CANCELED
        try {
            check(NativeBridge.status.ready)
            val credentials = CredentialStore(context)
            val identity = credentials.ensureIdentity()
            val profiles = JSONArray()
            credentials.profile(identity)?.let { profiles.put(it) }
            val configuration = JSONObject().put("schema", 1).put("installation", identity.installation).put("profiles", profiles)
                .put("paths", JSONObject().put("native_library_dir", context.applicationInfo.nativeLibraryDir).put("files_dir", context.filesDir.absolutePath)).toString()
            when (name) {
                "forward" -> ForwardCase.run(nonce, context, configuration, store)
                "remote-cleanup" -> RemoteCleanupCase.run(nonce, context, configuration, store)
            }
            result = JSONObject(File(context.noBackupFilesDir, "diagnostics/latest.json").readText()).getJSONObject("check")
            check(result.getString("nonce") == nonce && result.getString("result") == "pass")
            current = NativeBridge.nativeCreate(configuration)
            check(current > result.getString("last_handle").toLong())
            waitFor {
                val state = snapshot(current).getJSONObject("runtime")
                state.getString("state") == "Ready" && state.getJSONObject("transport").getBoolean("journal_ready")
            }
            val ready = snapshot(current)
            check(ready.getJSONObject("runtime").getJSONObject("transport").getJSONArray("pending_cleanup").length() == 0)
            proof.put("recovery_handle", current.toString()).put("ready", ready)
            val started = SystemClock.elapsedRealtime()
            check(NativeBridge.nativeRequestStop(current) == NativeBridge.ACCEPTED)
            proof.put("accept_ms", SystemClock.elapsedRealtime() - started)
            waitFor { snapshot(current).getJSONObject("runtime").getString("state") == "Stopped" }
            proof.put("stopped", snapshot(current))
            waitFor { NativeBridge.nativeDestroy(current) == NativeBridge.ACCEPTED }
            proof.put("stop_ms", SystemClock.elapsedRealtime() - started).put("after_destroy", snapshot(current))
            store.publishRuntime(snapshot(current))
            current = 0
            proof.put("result", "pass")
            code = Activity.RESULT_OK
        } catch (failure: Exception) {
            proof.put("result", "fail").put("failure_class", failure.javaClass.simpleName).put("failure_site", failure.stackTrace.firstOrNull()?.toString())
        } finally {
            if (current > 0) {
                NativeBridge.nativeRequestStop(current)
                try { waitFor { NativeBridge.nativeDestroy(current) != NativeBridge.BUSY } } catch (_: Exception) { proof.put("cleanup_failed", true) }
            }
            result.put("instrumentation", proof)
            store.publishCheck(result)
            finish(code, Bundle().apply { putString("case", name); putString("nonce", nonce); putString("result", proof.getString("result")); putInt("pid", Process.myPid()); putInt("uid", Process.myUid()) })
        }
    }
    private fun snapshot(handle: Long): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle))
    private fun waitFor(test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 10000
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(5) }
    }
}
