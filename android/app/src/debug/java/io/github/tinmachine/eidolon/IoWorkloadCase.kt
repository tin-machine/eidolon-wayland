package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject

/** 専用の新規terminalだけを束縛し、固定I/O metadataを最大128sample記録する。 */
object IoWorkloadCase {
    private val ui = Handler(Looper.getMainLooper())
    private val consumed = ArrayDeque<String>()
    private var active: Run? = null
    fun command(name: String, nonce: String) {
        if (name == "io-workload-stop") { active?.takeIf { it.nonce == nonce }?.cancel(); return }
        if (active != null || nonce in consumed) return
        if (consumed.size == 64) consumed.removeFirst()
        consumed.addLast(nonce)
        val run = Run(nonce)
        active = run
        ui.post(run)
    }
    private fun list(array: JSONArray?): List<JSONObject> = if (array == null) emptyList() else (0 until array.length()).map { array.getJSONObject(it) }
    private class Run(val nonce: String) : Runnable {
        private val started = SystemClock.elapsedRealtime()
        private val deadline = started + 60000
        private val rows = JSONArray()
        private val result = JSONObject().put("case", "io-workload").put("nonce", nonce).put("result", "running")
            .put("physical_disk_guaranteed", false).put("hmd_verified", false).put("ring_motion_verified", false).put("fixture_file_bytes", 1048576)
            .put("fixture_cleanup_verified", false).put("owned_window_removed", false).put("observation_restored", false).put("samples", rows)
        private var runtime = 0L
        private var session = ""
        private var initialTerminals = emptySet<String>()
        private var initialWindows = emptySet<String>()
        private var requestId: String? = null
        private var launchRequested = false
        private var restoreSent = false
        private var terminal: String? = null
        private var window: NativeSessionHost.WindowChoice? = null
        private var renderKey: String? = null
        private var admission: IoWorkloadAdmission? = null
        private var partialFrame = -1L
        private var stage = "start"
        private var reason: String? = null
        private var metricPositive = false
        private var metricPartial = false
        private var metricZero = false
        private var positive = false
        private var partial = false
        private var zeroAfterPartial = false
        private var changedObservation = false
        private var closeSent = false
        private var accumulatedWrite = 0.0
        private var cleanupDeadline = 0L
        fun cancel() {
            reason = "operator_stop"
            if (!launchRequested) { finish("cancelled"); return }
            stage = "cleanup"; cleanupDeadline = SystemClock.elapsedRealtime() + 10000
        }
        override fun run() {
            if (active !== this) return
            try {
                val now = SystemClock.elapsedRealtime()
                if (now >= deadline && stage != "cleanup") { reason = "timeout"; stage = "cleanup"; cleanupDeadline = now + 10000 }
                val handle = NativeSessionHost.currentHandle()
                check(handle != 0L && (runtime == 0L || handle == runtime)) { "runtime_changed" }
                val state = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
                val app = checkNotNull(state.optJSONObject("app")) { "app_missing" }
                val remote = state.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote")
                val launches = list(remote.optJSONArray("terminal_launches"))
                if (stage == "start") {
                    check(app.optString("state") == "running" && app.optString("mode") == "foot") { "terminal_not_ready" }
                    check(state.getJSONObject("workspace").getBoolean("workload_observation")) { "observation_disabled" }
                    check(state.getJSONObject("render").optString("theme_id") == "cyan-orbit") { "theme_required" }
                    runtime = handle; session = app.getString("session")
                    check(NativeSessionHost.launcherUi.value.session == session) { "launcher_not_ready" }
                    initialTerminals = launches.map { it.getString("terminal_id") }.toSet()
                    initialWindows = list(app.optJSONArray("windows")).map { it.getString("window") }.toSet()
                    check(initialWindows.size == 1) { "single_terminal_required" }
                    launchRequested = true
                    NativeSessionHost.launchFile(LauncherForm(id="nx13-io-fixed", label="NX13 固定I/O検証",
                        executable="/data/data/com.termux/files/usr/bin/python3", args=listOf("-I", "-c", BOOTSTRAP))) { request, code ->
                        ui.post {
                            if (active === this) {
                                if (code == NativeBridge.ACCEPTED) requestId = request
                                else { reason = "enqueue_rejected"; finish("fail") }
                            }
                        }
                    }
                    stage = "observe"
                    publish()
                    ui.postDelayed(this, 250); return
                }
                check(app.getString("session") == session) { "session_changed" }
                val added = launches.filter { requestId != null && it.optString("request_id") == requestId && it.getString("terminal_id") !in initialTerminals }
                check(added.size <= 1) { "ambiguous_launch" }
                if (terminal == null) terminal = added.singleOrNull()?.getString("terminal_id")
                val workload = list(app.optJSONArray("workloads")).singleOrNull { it.optString("terminal_id") == terminal }
                if (window == null && workload != null) window = NativeSessionHost.windowChoices.value.singleOrNull {
                    it.session == session && it.window == workload.getString("window") && it.window !in initialWindows
                }
                val target = window?.let { owned -> list(state.getJSONObject("render").optJSONArray("targets")).singleOrNull {
                    val key = it.getJSONObject("key")
                    key.getString("session") == session && key.getString("window") == owned.window && key.getString("scene") == owned.scene
                } }
                if (target != null) {
                    val key = target.getJSONObject("key").toString()
                    check(renderKey == null || renderKey == key) { "render_target_changed" }
                    renderKey = key
                }
                if (stage == "observe") {
                    check(added.singleOrNull()?.opt("accepted") != false) { "launch_rejected" }
                    val telemetry = remote.optJSONObject("workload")
                    result.put("source_presence", JSONObject().put("telemetry", telemetry != null)
                        .put("window_workload", workload != null).put("render_target", target != null))
                    check(admission?.adopted != true || (telemetry != null && workload != null && target != null)) { "scope_changed" }
                    if (telemetry != null && workload != null && target != null) observe(telemetry, workload, target)
                    if (metricPositive && metricPartial && metricZero) {
                        changedObservation = true
                        NativeSessionHost.setWorkloadObservation(false)
                        stage = "off"
                    }
                } else if (stage == "off") {
                    val display = workload?.optJSONObject("display")?.optJSONObject("io")
                    val drawn = target?.optJSONObject("io_draw")
                    if (!state.getJSONObject("workspace").getBoolean("workload_observation") && display?.optString("state") == "stopped" &&
                        display.isNull("bytes_per_second")) {
                        result.put("stopped_null", true).put("draw_stopped_null", drawn != null && drawn.isNull("bytes_per_second"))
                        stage = "cleanup"; cleanupDeadline = now + 10000
                    }
                }
                if (stage == "cleanup") {
                    if (changedObservation && !restoreSent) { NativeSessionHost.setWorkloadObservation(true); restoreSent = true }
                    val owned = window
                    if (!closeSent && owned != null && NativeSessionHost.windowChoices.value.any { it.session == owned.session && it.scene == owned.scene && it.window == owned.window }) {
                        NativeSessionHost.closeWindow(owned); closeSent = true
                    }
                    val removed = owned != null && list(app.optJSONArray("windows")).none { it.getString("window") == owned.window }
                    if (removed && state.getJSONObject("workspace").getBoolean("workload_observation")) {
                        result.put("owned_window_removed", true).put("observation_restored", true)
                        result.put("verified_scope", if (positive && partial && zeroAfterPartial && result.optBoolean("draw_stopped_null")) "metrics_and_draw" else "metrics_only")
                        finish(if (reason == null) "observed" else if (reason == "operator_stop") "cancelled" else "fail"); return
                    }
                    if (now >= cleanupDeadline) { finish("cleanup_unconfirmed"); return }
                }
                publish()
                ui.postDelayed(this, 250)
            } catch (failure: Exception) {
                reason = failure.message?.takeIf { it in setOf("runtime_changed", "app_missing", "terminal_not_ready", "observation_disabled",
                    "theme_required", "launcher_not_ready", "single_terminal_required", "session_changed", "ambiguous_launch", "render_target_changed",
                    "launch_rejected", "epoch_changed", "sequence_invalid", "sample_limit", "scope_changed", "timeout") } ?: "metadata_unavailable"
                if (runtime == NativeSessionHost.currentHandle() && launchRequested && stage != "cleanup") {
                    stage = "cleanup"; cleanupDeadline = SystemClock.elapsedRealtime() + 10000; ui.postDelayed(this, 250)
                } else finish("fail")
            }
        }
        private fun observe(telemetry: JSONObject, workload: JSONObject, target: JSONObject) {
            val nextEpoch = telemetry.getString("epoch")
            val sequence = telemetry.getString("sequence").toLongOrNull() ?: error("sequence_invalid")
            val value = list(telemetry.optJSONArray("workloads")).singleOrNull { it.getString("terminal_id") == terminal }
            val io = value?.optJSONObject("io")
            result.put("raw_presence", JSONObject().put("terminal_workload", value != null).put("io", io != null))
            if (value == null || io == null) { check(admission?.adopted != true) { "scope_changed" }; return }
            val expectedIdentity = nextEpoch + ":" + telemetry.getString("boot") + ":" + value.getString("workload_id")
            val display = workload.getJSONObject("display").getJSONObject("io")
            val drawn = target.optJSONObject("io_draw")
            val frames = target.getLong("decoration_frames")
            val rate = if (io.getString("state") == "available") io.getDouble("read_bytes_per_second") + io.getDouble("write_bytes_per_second") else null
            val nativeMatched = display.optString("state") == io.optString("state") &&
                (if (rate == null) display.isNull("bytes_per_second") else display.opt("bytes_per_second") is Number && display.getDouble("bytes_per_second") == rate)
            val gate = admission ?: IoWorkloadAdmission(checkNotNull(window).window, deadline).also { admission = it }
            val input = IoWorkloadAdmission.Input(nextEpoch, sequence, expectedIdentity, workload.opt("identity") as? String,
                workload.opt("window") as? String, workload.opt("state") as? String, value.opt("scope") as? String, nativeMatched)
            val decision = gate.observe(input, SystemClock.elapsedRealtime())
            result.put("admission", JSONObject(gate.metadata(input, decision)))
            when (decision.kind) {
                IoWorkloadAdmission.Kind.WAIT -> return
                IoWorkloadAdmission.Kind.FAIL -> error(decision.reason.wire)
                IoWorkloadAdmission.Kind.ACCEPT -> Unit
            }
            val fresh = decision.fresh
            val matched = nativeMatched && drawn != null && drawn.optString("workload_identity") == expectedIdentity && drawn.opt("inner_rotation_degrees") is Number &&
                (if (rate == null) drawn.isNull("bytes_per_second") else drawn.opt("bytes_per_second") is Number && drawn.getDouble("bytes_per_second") == rate) &&
                (if (rate == null) display.isNull("bytes_per_second") else display.opt("bytes_per_second") is Number && display.getDouble("bytes_per_second") == rate)
            check(!fresh || rows.length() < 128) { "sample_limit" }
            val sample = JSONObject().put("sequence", sequence.toString()).put("epoch", nextEpoch).put("terminal_id", terminal)
                .put("window", window?.window).put("io", io).put("native_io", display).put("draw_io", drawn ?: JSONObject.NULL)
                .put("decoration_frames", frames).put("native_matched", true).put("matched", matched).put("elapsed_ms", SystemClock.elapsedRealtime()-started)
            if (fresh || rows.length() == 0) rows.put(sample) else rows.put(rows.length()-1, sample)
            if (fresh && rate != null) accumulatedWrite += io.getDouble("write_bytes_per_second") * io.getDouble("interval_seconds")
            val transition = io.optString("state") == "partial" && io.optString("reason") in setOf("zombie_pending", "scope_changed", "snapshot_changed")
            if (nativeMatched && rate != null && io.getDouble("write_bytes_per_second") > 0) metricPositive = true
            if (metricPositive && nativeMatched && transition) metricPartial = true
            if (metricPartial && nativeMatched && rate == 0.0) metricZero = true
            if (matched && rate != null && io.getDouble("write_bytes_per_second") > 0) positive = true
            if (positive && matched && transition) { partial = true; partialFrame = frames }
            if (partial && matched && rate == 0.0 && frames > partialFrame) zeroAfterPartial = true
        }
        private fun publish() {
            result.put("stage", stage).put("metric_positive", metricPositive).put("metric_partial", metricPartial).put("metric_zero_after_partial", metricZero)
                .put("positive", positive).put("partial", partial).put("zero_after_partial", zeroAfterPartial)
                .put("observed_write_integral", accumulatedWrite).put("reason", reason ?: JSONObject.NULL)
            NativeSessionHost.publishCheck(result)
        }
        private fun finish(status: String) {
            // metadata失効時も自分が変更した設定/窓だけをbest effortで回収。ACKとは扱わない。
            if (runtime != 0L && runtime == NativeSessionHost.currentHandle()) {
                if (changedObservation && !restoreSent) { NativeSessionHost.setWorkloadObservation(true); restoreSent = true }
                val owned = window
                if (!closeSent && owned != null && NativeSessionHost.windowChoices.value.any {
                    it.session == owned.session && it.scene == owned.scene && it.window == owned.window
                }) { NativeSessionHost.closeWindow(owned); closeSent = true }
            }
            result.put("observation_restore_requested", restoreSent).put("owned_close_requested", closeSent)
                .put("result", status)
            publish(); active = null
        }
    }
    const val SOURCE_SHA256 = "3d15dd121eb541e9e883272a37e8fe45901e22a07778613e038233530517d8cd"
    private val BOOTSTRAP = """import hashlib,os,stat,sys
fd=os.open('/data/data/com.termux/files/home',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for part in ('.local','state','eidolon','nx13-workload'):
  child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
  info=os.fstat(fd)
  assert info.st_uid==os.getuid() and not info.st_mode&0o022
 assert stat.S_IMODE(os.fstat(fd).st_mode)==0o700
 source=os.open('fixture.py',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
 try:
  info=os.fstat(source)
  assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1
  data=bytearray()
  while len(data)<=65536:
   block=os.read(source,min(4096,65537-len(data)))
   if not block:break
   data.extend(block)
  assert 0<len(data)<=65536 and hashlib.sha256(data).hexdigest()=='3d15dd121eb541e9e883272a37e8fe45901e22a07778613e038233530517d8cd'
 finally:os.close(source)
finally:os.close(fd)
path='/data/data/com.termux/files/home/.local/state/eidolon/nx13-workload/fixture.py'
sys.argv=[path]
exec(compile(bytes(data),path,'exec'),{'__name__':'__main__','__file__':path})
"""
}
