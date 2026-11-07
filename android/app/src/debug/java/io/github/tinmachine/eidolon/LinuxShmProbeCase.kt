package io.github.tinmachine.eidolon

import android.content.Context
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

object LinuxShmProbeCase {
    private var active: Check? = null
    private val nonces = ArrayDeque<String>()
    @Synchronized fun start(context: Context, nonce: String, recoveryOnly: Boolean = false) {
        val operation = (if (recoveryOnly) "recover:" else "probe:") + nonce
        if (active != null || operation in nonces) return
        if (nonces.size == 64) nonces.removeFirst()
        nonces.addLast(operation)
        val check = Check(context, nonce, recoveryOnly)
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

    private class Check(val context: Context, val nonce: String, val recoveryOnly: Boolean) {
        private val helperNonce = nonce.take(16)
        val cancel = AtomicBoolean(false)
        val finished = AtomicBoolean(false)
        @Volatile var safeToRestore = true
        private var runtime = 0L
        private var capture: ProbeCredentialCapture? = null
        private val result = JSONObject().put("case", if (recoveryOnly) "linux-shm-recover" else "linux-shm-probe").put("nonce", nonce).put("result", "running")
            .put("gui_enabled", false).put("gpu_verified", false).put("physical_hmd", false).put("virtual_frame_clock", !recoveryOnly).put("apk_uid", Process.myUid())
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
                val pins = DebugLinuxShmPinStore(context).importConfirmed(profile)
                val privateRoot = File(context.noBackupFilesDir, "lx")
                if (!privateRoot.exists()) Os.mkdir(privateRoot.absolutePath, 448)
                val stat = Os.lstat(privateRoot.absolutePath)
                check(OsConstants.S_ISDIR(stat.st_mode) && stat.st_uid == Process.myUid() && stat.st_mode and 511 == 448) { "PROBE_DIRECTORY_INVALID" }
                config.getJSONObject("paths").put("files_dir", privateRoot.absolutePath)
                config.put("debug_probe_profile_v2", ConnectionProfileV2Codec.encode(profile).toString(Charsets.UTF_8))
                config.put("debug_linux_shm_pins", pins.nativeJson())
                config.remove("preferences_dir")
                // 設定を保持する通常profileを新runtimeへ渡さない。通常config自体は不変。
                config.put("profiles", org.json.JSONArray())
                safeToRestore = false
                runtime = NativeBridge.nativeCreate(config.toString())
                check(runtime > 0L) { "PROBE_CREATE_FAILED" }
                capture = ProbeCredentialCapture(runtime, "1", profile)
                deadline = SystemClock.elapsedRealtime() + 90000
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
                val session = checkNotNull(boundSession)
                send(if (recoveryOnly) "linux_shm_recover" else "linux_shm_probe", "2", session, helperNonce)
                var operationAccepted = false
                waitFor {
                    poll { event -> if (event.optString("type") == "command_complete" && event.optString("request_id") == "2") {
                        check(event.getString("code") == "ACCEPTED") { "PROBE_OPERATION_REJECTED" }; operationAccepted = true
                    } }
                    val value = state()
                    val transport = value.getJSONObject("transport")
                    check(transport.getString("session") == session && transport.getString("profile_id") == profile.profileId &&
                        transport.getString("profile_revision") == profile.revision) { "PROBE_TARGET_CHANGED" }
                    if (recoveryOnly) {
                        val recovery = transport.optJSONObject("linux_recovery")
                        if (!operationAccepted || recovery == null) false else {
                            check(recovery.getString("session") == session && recovery.getString("nonce") == helperNonce && recovery.getInt("uid") == result.getInt("remote_uid")) { "PROBE_TARGET_CHANGED" }
                            check(recovery.isNull("error") && !recovery.getBoolean("pending")) { "PROBE_REMOTE_CLEANUP_PENDING" }
                            val outcome = LinuxRecoveryOutcome.classify(recovery.getBoolean("queried"), recovery.getBoolean("pending"),
                                recovery.getBoolean("receipt_verified"), recovery.getBoolean("channel_closed"))
                            check(outcome != LinuxRecoveryOutcome.INVALID) { "PROBE_RECOVERY_REPORT_INVALID" }
                            result.put("recovery_queried", recovery.getBoolean("queried")).put("receipt_verified", recovery.getBoolean("receipt_verified"))
                                .put("origin_session", recovery.opt("origin_session")).put("remote_cleanup_confirmed", recovery.getBoolean("receipt_verified"))
                                .put("journal_cleared", true).put("recovery_not_required", outcome == LinuxRecoveryOutcome.NOT_REQUIRED)
                            true
                        }
                    } else {
                        val forward = transport.optJSONObject("linux_forward")
                        val app = value.optJSONObject("app")
                        check(transport.getString("state") !in setOf("Failed", "Disconnected", "Stopped") &&
                            (forward == null || forward.isNull("error")) && (app == null || app.isNull("error"))) { "PROBE_SHM_FAILED" }
                        if (!operationAccepted || forward?.optString("state") != "pass" || app?.optString("state") != "pass") false else {
                            check(forward.getString("session") == session && forward.getString("nonce") == helperNonce &&
                                forward.getInt("uid") == result.getInt("remote_uid") && app.getString("session") == session && app.getString("nonce") == helperNonce) { "PROBE_TARGET_CHANGED" }
                            check(transport.getString("state") == "ProbeReady" && forward.getBoolean("tools_verified") && forward.getBoolean("registered") &&
                                forward.getBoolean("forward_cancelled") && forward.getBoolean("remote_cleaned") && forward.getBoolean("channel_closed") &&
                                !forward.getBoolean("journal_pending") && forward.getBoolean("pattern_verified") && forward.getInt("frames") == 100) { "PROBE_REMOTE_REPORT_INVALID" }
                            check(app.getString("mode") == "pattern" && app.getString("callback_clock") == "virtual" &&
                                app.getInt("verified_hashes") == 100 && app.getLong("copies") == 100L && app.getLong("callbacks") == 100L &&
                                app.getLong("releases") == 100L && app.getInt("accepted") >= 2) { "PROBE_PIXEL_REPORT_INVALID" }
                            result.put("shm_verified_hashes", app.getInt("verified_hashes")).put("shm_copies", app.getLong("copies"))
                                .put("shm_callbacks", app.getLong("callbacks")).put("wayland_connections", app.getInt("accepted"))
                                .put("remote_cleanup_confirmed", true).put("journal_cleared", true).put("probe_capability_unchanged", true)
                            true
                        }
                    }
                }
                send("disconnect", "3", boundSession)
                var disconnected = false
                waitFor {
                    poll { event -> if (event.optString("type") == "command_complete" && event.optString("request_id") == "3") {
                        check(event.getString("code") == "ACCEPTED") { "PROBE_DISCONNECT_REJECTED" }; disconnected = true
                    } }
                    val transport = state().getJSONObject("transport")
                    disconnected && transport.getString("state") == "Disconnected" && transport.getInt("owned_connections") == 0 && (recoveryOnly || localCleaned(state()))
                }
                check(JSONObject(NativeBridge.nativeCredentialScope(runtime)).isNull("scope")) { "PROBE_SCOPE_RETAINED" }
                result.put("disconnected", true).put("scope_cleared", true).put("local_cleaned", recoveryOnly || localCleaned(state()))
                completed = true
            } catch (failure: Exception) {
                result.put("failure_class", failure.javaClass.simpleName).put("reason", failure.message?.takeIf { it.matches(Regex("PROBE_[A-Z_]+")) } ?: "PROBE_FAILED")
            } finally {
                capture?.close(); capture = null
                try {
                    val value = state(); val app = value.optJSONObject("app"); val forward = value.getJSONObject("transport").optJSONObject("linux_forward")
                    result.put("last_app_state", app?.optString("state")).put("last_app_error", app?.opt("error"))
                        .put("last_linux_state", forward?.optString("state")).put("last_linux_error", forward?.opt("error"))
                        .put("last_remote_phase", forward?.optJSONObject("remote")?.optString("phase"))
                        .put("journal_pending", forward?.optBoolean("journal_pending"))
                } catch (_: Exception) { }
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
            if (runtime == 0L) {
                // nativeCreateは失敗時に例外+0。例外伝播や0 fallbackでも実registryを確認する。
                safeToRestore = try {
                    val counts = JSONObject(NativeBridge.nativeSnapshot(0)).getJSONObject("registry")
                    ProbeRuntimeRecovery.registryEmpty(counts.opt("runtimes"), counts.opt("workers"))
                } catch (_: Exception) { false }
                result.put("destroyed", safeToRestore).put("create_had_no_owner", safeToRestore)
                return
            }
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
                completed && ready && recoveryOnly && result.optBoolean("recovery_not_required") -> "not_required"
                completed && ready -> "pass"
                cancel.get() && ready && result.optBoolean("termux_configuration_unchanged") && (runtime == 0L || result.optBoolean("destroyed")) -> "cancelled"
                else -> "fail"
            })
            NativeSessionHost.publishCheck(result)
            finished.set(true)
        }
        private fun send(type: String, request: String, session: String? = null, helperNonce: String? = null) {
            val command = JSONObject().put("schema",1).put("type",type).put("request_id",request)
            session?.let { command.put("session",it) }
            helperNonce?.let { command.put("nonce", it) }
            check(NativeBridge.nativeCommand(runtime,command.toString()) == NativeBridge.ACCEPTED) { "PROBE_ENQUEUE_REJECTED" }
        }
        private fun localCleaned(value: JSONObject): Boolean {
            val app = value.optJSONObject("app") ?: return false
            return app.optString("state") == "stopped" && app.optBoolean("directory_cleaned") &&
                listOf("clients", "objects", "surfaces", "pixel_bytes", "pool_bytes", "pool_count", "memfd_fds_after", "memfd_maps_after").all { !app.isNull(it) && app.getLong(it) == 0L } &&
                app.getJSONObject("local").getInt("owned_groups") == 0
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
