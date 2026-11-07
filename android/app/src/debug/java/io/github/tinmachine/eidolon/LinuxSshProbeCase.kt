package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Process
import android.os.SystemClock
import android.system.Os
import android.system.OsConstants
import java.io.File
import java.nio.file.Files
import java.nio.file.LinkOption
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONObject

/** sourceSet + DUMP境界。address/path/argv/JSONはintentで受け取らない。 */
class LinuxSshProbeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.extras?.keySet()?.any { it !in setOf("case", "nonce") } != false) return
        val name = intent.getStringExtra("case") ?: return
        val nonce = intent.getStringExtra("nonce")?.takeIf { it.matches(Regex("[0-9a-f]{32}")) } ?: return
        when(name) {
            "linux-ssh-probe" -> LinuxSshProbeCase.start(context.applicationContext, nonce)
            "linux-ssh-probe-stop" -> LinuxSshProbeCase.stop(nonce)
        }
    }
}

object LinuxSshProbeCase {
    private var active: Check? = null
    private val nonces = ArrayDeque<String>()
    @Synchronized fun start(context: Context, nonce: String) {
        if (active != null || nonce in nonces) return
        if (nonces.size == 64) nonces.removeFirst()
        nonces.addLast(nonce)
        val check = Check(context, nonce)
        active = check
        NativeSessionHost.initialize(context)
        NativeSessionHost.withStoppedRuntimeGuarded(check, { store -> check.run(store) }, {
            check.restored()
            synchronized(this) { if (active === check && check.safeToRestore) active = null }
        }, { check.safeToRestore })
    }
    @Synchronized fun stop(nonce: String) {
        val check = active?.takeIf { it.nonce == nonce } ?: return
        check.cancel.set(true)
        if (!check.safeToRestore && check.finished.compareAndSet(true,false)) {
            // 前回deadline後もowner handleを保持。明示stopだけで回収を再試行し接続は再実行しない。
            NativeSessionHost.withStoppedRuntimeGuarded(check, { store -> check.reap(store) }, {
                check.restored()
                synchronized(this) { if (active === check && check.safeToRestore) active = null }
            }, { check.safeToRestore })
        }
    }

    private class Check(val context: Context, val nonce: String) {
        val cancel = AtomicBoolean(false)
        val finished = AtomicBoolean(false)
        @Volatile var safeToRestore = true
        private var runtime = 0L
        private var capture: ProbeCredentialCapture? = null
        private val result = JSONObject().put("case", "linux-ssh-probe").put("nonce", nonce).put("result", "running")
            .put("gui_enabled", false).put("apk_uid", Process.myUid())
        private var deadline = 0L
        private var completed = false
        fun run(store: DiagnosticsStore) {
            val base = NativeSessionHost.runtimeConfigJson()
            val config = JSONObject(base)
            val originalFiles = listOf(File(context.noBackupFilesDir, "credentials/profile.json"),
                File(config.getJSONObject("paths").getString("files_dir"), "transport-state/transport-pending.json"))
            var original: List<ByteArray?>? = null
            try {
                original = originalFiles.map(::digest)
                check(!cancel.get()) { "PROBE_CANCELLED" }
                val profile = DebugProbeProfileStore(context).importConfirmed(config.getString("installation"))
                val privateRoot = File(context.noBackupFilesDir, "remote-probe/runtime")
                if (!privateRoot.exists()) Os.mkdir(privateRoot.absolutePath, 448)
                val stat = Os.lstat(privateRoot.absolutePath)
                check(OsConstants.S_ISDIR(stat.st_mode) && stat.st_uid == Process.myUid() && stat.st_mode and 511 == 448) { "PROBE_DIRECTORY_INVALID" }
                config.getJSONObject("paths").put("files_dir", privateRoot.absolutePath)
                config.put("debug_probe_profile_v2", ConnectionProfileV2Codec.encode(profile).toString(Charsets.UTF_8))
                // 設定を保持する通常profileを新runtimeへ渡さない。通常config自体は不変。
                config.put("profiles", org.json.JSONArray())
                runtime = NativeBridge.nativeCreate(config.toString()); safeToRestore = false
                capture = ProbeCredentialCapture(runtime, "1", profile)
                deadline = SystemClock.elapsedRealtime() + 30000
                waitFor { state().getString("state") == "Ready" }
                send("connect_saved_probe", "1")
                var accepted = false
                var supplied = false
                var boundSession: String? = null
                waitFor {
                    poll { event ->
                        when(event.optString("type")) {
                            "command_complete" -> if (event.optString("request_id") == "1") {
                                check(event.getString("code") == "ACCEPTED") { "PROBE_CONNECT_REJECTED" }; accepted = true
                            }
                            "credential_required" -> {
                                check(!supplied) { "PROBE_DUPLICATE_CREDENTIAL" }
                                val code = NativeSessionHost.provideDebugProbeCredential(runtime, checkNotNull(capture), event)
                                check(code == NativeBridge.ACCEPTED) { "PROBE_CREDENTIAL_REJECTED" }
                                supplied = true; boundSession = event.getString("session")
                            }
                        }
                    }
                    val transport = state().getJSONObject("transport")
                    check(transport.getString("state") !in setOf("Failed", "CleanupPending", "Disconnected", "Stopped")) { "PROBE_TRANSPORT_FAILED" }
                    if (transport.getString("state") != "ProbeReady") false else {
                        check(accepted && supplied && transport.getString("session") == boundSession && transport.getString("profile_id") == profile.profileId &&
                            transport.getString("profile_revision") == profile.revision && transport.getInt("credential_requests") == 1) { "PROBE_TARGET_CHANGED" }
                        val probe = transport.getJSONObject("probe")
                        check(probe.getBoolean("token_matched") && probe.getBoolean("eof") && probe.getInt("exit_status") == 0 && !probe.getBoolean("exit_signal")) { "PROBE_REPORT_INVALID" }
                        result.put("credential_bound", true).put("probe_ready", true).put("remote_uid", probe.getInt("uid"))
                        true
                    }
                }
                send("disconnect", "2", boundSession)
                var disconnected = false
                waitFor {
                    poll { event -> if (event.optString("type") == "command_complete" && event.optString("request_id") == "2") {
                        check(event.getString("code") == "ACCEPTED") { "PROBE_DISCONNECT_REJECTED" }; disconnected = true
                    } }
                    val transport = state().getJSONObject("transport")
                    disconnected && transport.getString("state") == "Disconnected" && transport.getInt("owned_connections") == 0
                }
                check(JSONObject(NativeBridge.nativeCredentialScope(runtime)).isNull("scope")) { "PROBE_SCOPE_RETAINED" }
                result.put("disconnected", true).put("scope_cleared", true)
                completed = true
            } catch (failure: Exception) {
                result.put("failure_class", failure.javaClass.simpleName).put("reason", failure.message?.takeIf { it.matches(Regex("PROBE_[A-Z_]+")) } ?: "PROBE_FAILED")
            } finally {
                capture?.close(); capture = null
                cleanupOwner()
                val unchanged = try { original != null && originalFiles.map(::digest).zip(original).all { (a,b) -> if (a == null || b == null) a == null && b == null else a.contentEquals(b) } &&
                    NativeSessionHost.runtimeConfigJson() == base } catch (_: Exception) { false }
                result.put("termux_configuration_unchanged", unchanged)
                completed = completed && unchanged && result.optBoolean("destroyed")
                result.put("result", if (!safeToRestore) "cleanup_unconfirmed" else "restoring")
                store.publishCheck(result)
            }
        }
        private fun cleanupOwner() {
            if (runtime == 0L) return
            result.put("probe_runtime", runtime.toString())
            try {
                NativeBridge.nativeRequestStop(runtime)
                val stopDeadline = SystemClock.elapsedRealtime() + 10000
                while (SystemClock.elapsedRealtime() < stopDeadline) {
                    val code = NativeBridge.nativeDestroy(runtime)
                    if (code == NativeBridge.ACCEPTED || code == NativeBridge.STALE) {
                        val retired = JSONObject(NativeBridge.nativeSnapshot(runtime))
                        safeToRestore = retired.getInt("code") == NativeBridge.STALE &&
                            retired.getJSONObject("registry").getInt("runtimes") == 0 && retired.getJSONObject("registry").getInt("workers") == 0
                        break
                    }
                    Thread.sleep(5)
                }
            } catch (_: Exception) { safeToRestore = false }
            result.put("destroyed", safeToRestore)
        }
        fun reap(store: DiagnosticsStore) { cleanupOwner(); store.publishCheck(result.put("result",if(safeToRestore) "restoring" else "cleanup_unconfirmed")) }
        fun restored() {
            val normal = NativeSessionHost.currentHandle()
            val ready = normal > 0 && normal != runtime && JSONObject(NativeBridge.nativeSnapshot(normal)).optJSONObject("runtime")?.let {
                it.optString("state") == "Ready" && it.getJSONObject("transport").getString("state") == "Idle"
            } == true
            result.put("normal_runtime_restored", ready).put("result", when {
                !safeToRestore -> "cleanup_unconfirmed"
                completed && ready -> "pass"
                cancel.get() && ready && result.optBoolean("termux_configuration_unchanged") && (runtime == 0L || result.optBoolean("destroyed")) -> "cancelled"
                else -> "fail"
            })
            NativeSessionHost.publishCheck(result)
            finished.set(true)
        }
        private fun send(type: String, request: String, session: String? = null) {
            val command = JSONObject().put("schema",1).put("type",type).put("request_id",request)
            session?.let { command.put("session",it) }
            check(NativeBridge.nativeCommand(runtime,command.toString()) == NativeBridge.ACCEPTED) { "PROBE_ENQUEUE_REJECTED" }
        }
        private fun state() = JSONObject(NativeBridge.nativeSnapshot(runtime)).getJSONObject("runtime")
        private fun poll(consume: (JSONObject) -> Unit) {
            val envelope = JSONObject(NativeBridge.nativePoll(runtime,64))
            check(envelope.getInt("code") == NativeBridge.ACCEPTED) { "PROBE_POLL_FAILED" }
            val events = envelope.getJSONArray("events")
            repeat(events.length()) { consume(events.getJSONObject(it)) }
        }
        private fun waitFor(ready: () -> Boolean) {
            while (true) {
                check(!cancel.get()) { "PROBE_CANCELLED" }
                check(SystemClock.elapsedRealtime() < deadline) { "PROBE_TIMEOUT" }
                if (ready()) return
                Thread.sleep(5)
            }
        }
        private fun digest(file: File): ByteArray? {
            if (!Files.exists(file.toPath(), LinkOption.NOFOLLOW_LINKS)) return null
            check(Files.isRegularFile(file.toPath(),LinkOption.NOFOLLOW_LINKS) && file.length() in 1..16384) { "PROBE_OLD_STATE_INVALID" }
            return MessageDigest.getInstance("SHA-256").digest(file.readBytes())
        }
    }
}
