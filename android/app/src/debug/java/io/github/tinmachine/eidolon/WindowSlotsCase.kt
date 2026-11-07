package io.github.tinmachine.eidolon

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import com.meta.spatial.toolkit.getAbsoluteTransform
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/** One isolated foot-server owner. SDK readiness, native ACK, slot drains and product restore stay separate. */
object WindowSlotsCase {
    @Volatile private var active: Check? = null
    fun start(nonce: String, adapter: SpatialPanelAdapter, complete: (JSONObject) -> Unit) {
        check(active == null)
        Check(nonce, adapter, complete).also { active = it; it.start() }
    }
    fun commandComplete(request: String, code: String) { active?.let { if (request in it.requests) it.completions[request] = code } }
    private fun list(value: JSONArray?): List<JSONObject> = if (value == null) emptyList() else (0 until value.length()).map { value.getJSONObject(it) }
    private fun same(a: JSONObject, b: JSONObject): Boolean = listOf("session", "window", "scene", "generation").all { a.optString(it) == b.optString(it) }
    private class Check(val nonce: String, val adapter: SpatialPanelAdapter, val complete: (JSONObject) -> Unit) : Runnable {
        val requests = ConcurrentHashMap.newKeySet<String>()
        val completions = ConcurrentHashMap<String, String>()
        val enqueues = linkedMapOf<String, Int>()
        val ui = Handler(Looper.getMainLooper())
        val rows = JSONArray()
        val order = listOf("center", "right", "left", "up", "down", "upper_left", "upper_right", "lower_left", "lower_right")
        val expected = linkedMapOf<String, Int>()
        val leases = linkedMapOf<String, String>()
        val targets = linkedMapOf<String, JSONObject>()
        val result = JSONObject().put("case", "window-slots").put("fixture_revision", "window-slots-v4-four-controls").put("nonce", nonce).put("result", "running")
            .put("normal_foot_server", true).put("independent_clients_verified", false).put("physical_input_verified", false).put("hmd_verified", false)
            .put("synthetic_input", true).put("synthetic_visibility", false).put("auto_retry", false).put("checkpoints", rows)
        var handle = 0L
        var session = ""
        var scene = ""
        var stage = "ready"
        var next = 9101600L
        var pending = ""
        var deadline = SystemClock.elapsedRealtime() + 60000
        val overall = SystemClock.elapsedRealtime() + 180000
        var settle = 0L
        var frameStart = emptyMap<String, Long>()
        var hole = ""
        var removed = JSONObject()
        var busyBefore = ""
        var terminalCountBefore = 0
        var keyDelivered = 0L
        var highestReady = 0
        var lastSnapshot: JSONObject? = null
        var lastReadiness: JSONObject? = null
        var lastObservedAt = 0L
        var lastObservedStage = ""
        fun start() { publish(); ui.post(this) }
        fun publish() {
            result.put("stage", stage).put("request_ids", JSONArray(requests.toList().sortedBy {it.toLong()})).put("enqueue_codes", JSONObject(enqueues.toMap())).put("completions", JSONObject(completions.toMap())).put("owned_runtime", handle.toString()).put("highest_ready_windows", highestReady).put("expected_count", expected.size)
                .put("requested_count", if(stage=="adding") expected.size+1 else expected.size)
            check(result.toString().toByteArray(Charsets.UTF_8).size <= 262144) { "CASE_METADATA_BOUND" }
            NativeSessionHost.publishCheck(result)
        }
        fun windows(s: JSONObject) = list(s.optJSONObject("app")?.optJSONArray("windows"))
        fun entries(s: JSONObject) = list(s.getJSONObject("window_slots").getJSONArray("entries"))
        fun bound(s: JSONObject) = entries(s).filter { it.getJSONObject("state").optString("phase") == "bound" }
        fun plans(s: JSONObject) = list(s.getJSONArray("panels"))
        fun rendered(s: JSONObject) = list(s.getJSONObject("render").getJSONArray("targets"))
        fun sdk() = list(adapter.metrics().getJSONArray("targets")).filter { it.optString("role") == "windows" }
        fun leaseId(value: JSONObject): String = value.getJSONObject("lease").let { "${it.getString("runtime")}/${it.getString("session")}/${it.getString("token")}/${it.getString("position")}" }
        fun choice(target: JSONObject) = NativeSessionHost.WindowChoice(target.getString("session"), target.getString("scene"), target.getString("window"), expected[target.getString("window")])
        fun summary(s: JSONObject): JSONObject {
            val app = s.optJSONObject("app")
            val metrics = adapter.metrics()
            val snapshot = JSONObject().put("state", s.optString("state")).put("scene", s.opt("scene")).put("activity_visible", s.optBoolean("activity_visible"))
                .put("xr_state", s.optString("xr_state")).put("input_resets", s.optLong("input_resets")).put("error", s.opt("error"))
                .put("window_slots", s.optJSONObject("window_slots")).put("window_launch", s.optJSONObject("window_launch")).put("shutdown", s.optJSONObject("shutdown"))
                .put("theme", s.optJSONObject("theme")).put("render_theme_id", s.getJSONObject("render").opt("theme_id")).put("render_theme_revision", s.getJSONObject("render").opt("theme_revision"))
                .put("panels", JSONArray(plans(s).map { JSONObject().put("target", it.getJSONObject("target")).put("slot", it.getInt("slot"))
                    .put("action", it.getString("action")).put("applied", it.getBoolean("applied")).put("error", it.opt("error")) }))
                .put("render", JSONArray(rendered(s).map { JSONObject().put("key", it.getJSONObject("key")).put("frames", it.getLong("frames"))
                    .put("frame_timing", it.optJSONObject("frame_timing")).put("width", it.optInt("width")).put("height", it.optInt("height")) }))
                .put("workspace", s.getJSONObject("workspace").getJSONArray("windows"))
                .put("adapter", JSONObject().put("scene", metrics.opt("scene")).put("bindings", metrics.getInt("bindings")).put("pending", metrics.getInt("pending"))
                    .put("window_controls", metrics.getJSONObject("window_controls")).put("window_mode_badges", metrics.getJSONObject("window_mode_badges")).put("window_menus",metrics.getJSONObject("window_menus"))
                    .put("targets", JSONArray(sdk().map { JSONObject().put("target", it.getJSONObject("target")).put("mode_badge", it.optJSONObject("mode_badge"))
                        .put("controls", it.optJSONObject("controls")).put("actual_pose", adapter.windowEntity(it.getJSONObject("target").getString("window"))?.let { entity -> PanelPoseCodec.fromSdk(getAbsoluteTransform(entity)) }) })))
            if (app != null) snapshot.put("app", JSONObject().put("state", app.optString("state")).put("session", app.optString("session"))
                .put("error", app.opt("error")).put("clients", app.optInt("clients")).put("surfaces", app.optInt("surfaces"))
                .put("pool_count", app.optInt("pool_count")).put("pool_bytes", app.optLong("pool_bytes")).put("pixel_bytes", app.optLong("pixel_bytes"))
                .put("pool_rejections", app.optLong("pool_rejections")).put("last_rejected_pool_bytes", app.opt("last_rejected_pool_bytes"))
                .put("surface_rejections", app.optLong("surface_rejections"))
                .put("toplevel_rejections", app.optLong("toplevel_rejections")).put("client_rejections", app.optLong("client_rejections"))
                .put("windows", JSONArray(windows(s).map { JSONObject().put("window", it.getString("window")).put("life", it.getString("life"))
                    .put("app_id", it.opt("app_id")).put("logical_size", it.opt("logical_size")).put("sizing", it.optJSONObject("sizing")) })))
            return snapshot
        }
        fun record(label: String, s: JSONObject) {
            check(rows.length() < 12) { "CHECKPOINT_BOUND" }
            rows.put(JSONObject().put("stage", label).put("elapsed_ms", SystemClock.elapsedRealtime() - (overall - 180000)).put("snapshot", summary(s)))
            publish()
        }
        fun submit(s: JSONObject, type: String, target: JSONObject? = null): String {
            check(s.getJSONObject("app").getString("session") == session && NativeSessionHost.currentHandle() == handle)
            val id = (++next).toString()
            val command = JSONObject().put("schema", 1).put("type", type).put("request_id", id)
            if (target == null) command.put("session", session)
            else command.put("target", JSONObject(target.toString())).put("controls_revision", s.getJSONObject("render").getLong("theme_revision"))
            requests.add(id)
            val code = NativeBridge.nativeCommand(handle, command.toString()); enqueues[id] = code
            check(code == NativeBridge.ACCEPTED) { "COMMAND_ENQUEUE_REJECTED" }
            deadline = SystemClock.elapsedRealtime() + 15000
            return id
        }
        /** Evaluate every fixed gate, including gates beyond the first missing SDK object. */
        fun readiness(s: JSONObject, count: Int): JSONObject {
            val gates = JSONObject()
            val gateOrder = mutableListOf<String>()
            fun gate(name: String, value: Boolean) { gateOrder.add(name); gates.put(name, value) }
            val w = windows(s); val app = s.optJSONObject("app"); val render = s.getJSONObject("render")
            val slots = s.getJSONObject("window_slots")
            val p = plans(s); val r = rendered(s); val a = sdk(); val placements = list(s.getJSONObject("workspace").getJSONArray("windows"))
            gate("runtime_ready", s.optString("state") == "Ready")
            gate("runtime_owner", handle == 0L || NativeSessionHost.currentHandle() == handle)
            gate("scene_owner", scene.isEmpty() || s.optString("scene") == scene)
            gate("session_owner", session.isEmpty() || app?.optString("session") == session)
            gate("activity_visible", s.optBoolean("activity_visible"))
            gate("xr_focused", s.optString("xr_state") == "FOCUSED")
            gate("window_count", w.size == count)
            gate("all_mapped", w.size == count && w.all { it.optString("life") == "Mapped" })
            gate("expected_count", expected.size == count)
            gate("single_foot_client", app?.optInt("clients", -1) == 1)
            gate("theme_selected", render.optString("theme_id") == "cyan-orbit")
            gate("theme_loaded", !s.getJSONObject("theme").optBoolean("loading"))
            gate("terminal_markers", app != null && w.all { it.optString("app_id").startsWith("io.github.tinmachine.eidolon.foot.t-${app.getString("nonce")}-") })
            gate("slot_count", slots.optInt("occupied", -1) == count)
            gate("display_limit", slots.optInt("display_limit", -1) == 9)
            gate("slot_cleanup_clear", !slots.optBoolean("cleanup_required"))
            gate("bound_count", bound(s).size == count)
            gate("panel_count", p.size == count)
            gate("render_count", r.size == count)
            gate("sdk_count", a.size == count)
            val windowGates = JSONArray()
            for ((window, slot) in expected) {
                val checks = JSONObject()
                val checkOrder = mutableListOf<String>()
                fun checkGate(name: String, value: Boolean) { checkOrder.add(name); checks.put(name, value) }
                val entry = bound(s).singleOrNull { it.getJSONObject("state").getString("window") == window }
                val lease = entry?.optJSONObject("lease")
                val plan = p.singleOrNull { it.getJSONObject("target").optString("window") == window }
                val target = plan?.optJSONObject("target")
                val row = a.singleOrNull { target != null && same(it.getJSONObject("target"), target) }
                val controls = row?.optJSONObject("controls"); val badge = row?.optJSONObject("mode_badge")
                val placement = placements.singleOrNull { it.optString("session") == session && it.optString("window") == window && !it.isNull("slot") && it.optInt("slot", -1) == slot }?.optJSONObject("placement")
                val applied = placement?.optJSONObject("applied_pose"); val pose = placement?.optJSONObject("pose")
                val grid = arrayOf(0 to 0, 1 to 0, -1 to 0, 0 to 1, 0 to -1, -1 to 1, 1 to 1, -1 to -1, 1 to -1)[slot]
                val wanted = JSONObject().put("position_m", JSONArray(listOf(grid.first * 1.8, 2.0 + grid.second * 1.8, 1.5))).put("rotation_xyzw", JSONArray(listOf(0.0, 0.0, 0.0, 1.0)))
                val entity = adapter.windowEntity(window)
                val actual = entity?.let { PanelPoseCodec.fromSdk(getAbsoluteTransform(it)) }
                checkGate("lease_present", entry != null)
                checkGate("lease_owner_position", lease?.optString("runtime") == handle.toString() && lease.optString("session") == session && lease.optString("position") == order[slot])
                checkGate("lease_stable", entry != null && (leases[window] == null || leases[window] == leaseId(entry)))
                checkGate("plan_present", plan != null)
                checkGate("target_owner_slot", target?.optString("session") == session && target.optString("scene") == scene && plan.optInt("slot", -1) == slot)
                checkGate("target_stable", target != null && (targets[window] == null || same(checkNotNull(targets[window]), target)))
                checkGate("panel_create", plan?.optString("action") == "create")
                checkGate("panel_applied", plan?.optBoolean("applied") == true)
                checkGate("panel_error_clear", plan != null && plan.isNull("error"))
                checkGate("native_frames", target != null && r.any { same(it.getJSONObject("key"), target) && it.getLong("frames") > 0 })
                checkGate("sdk_binding", row != null)
                checkGate("controls_present", controls != null)
                checkGate("controls_enabled", controls?.optBoolean("enabled") == true)
                checkGate("controls_revision", controls?.optLong("revision", -1) == render.getLong("theme_revision"))
                checkGate("controls_no_failure", controls?.optLong("failures", -1) == 0L)
                checkGate("control_entities", controls?.optInt("entities", -1) == 12)
                checkGate("buttons_ready_target", controls != null && target != null && list(controls.optJSONArray("buttons")).let { buttons -> buttons.size == 4 && buttons.all { it.optBoolean("ready") && same(it.getJSONObject("target"), target) } })
                checkGate("mode_present", badge != null)
                checkGate("mode_object_ready", badge?.optBoolean("object_ready") == true)
                checkGate("mode_no_failure", badge?.optLong("failures", -1) == 0L)
                checkGate("workspace_present", placement != null)
                checkGate("pose_applied", applied != null)
                checkGate("applied_target", target != null && applied?.optJSONObject("target")?.let { same(it, target) } == true)
                checkGate("requested_pose", pose != null && PanelPoseCodec.near(pose, wanted, .001))
                checkGate("applied_pose", applied?.optJSONObject("pose")?.let { PanelPoseCodec.near(it, wanted, .001) } == true)
                checkGate("sdk_entity", entity != null)
                checkGate("sdk_pose", actual != null && PanelPoseCodec.near(actual, wanted, .001))
                checkGate("input_target", target != null && NativeSessionHost.inputTarget(target.toString()) != null)
                windowGates.put(JSONObject().put("window", window).put("slot", slot).put("gates", checks)
                    .put("reason", checkOrder.firstOrNull { !checks.getBoolean(it) } ?: "ready"))
            }
            val controls = adapter.metrics().getJSONObject("window_controls")
            val badges = adapter.metrics().getJSONObject("window_mode_badges")
            gate("control_entity_owners", controls.getLong("entity_owners") == count * 12L)
            gate("control_listener_owners", controls.getLong("listener_owners") == count * 4L)
            for (name in listOf("entity_owners", "object_owners", "mesh_owners", "material_owners", "texture_owners", "pixel_slots_in_use")) gate("mode_$name", badges.getLong(name) == count.toLong())
            gate("mode_cleanup_clear", badges.getLong("cleanup_pending_owners") == 0L)
            val menus=adapter.metrics().getJSONObject("window_menus")
            for(name in listOf("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners")) gate("menu_$name",menus.getLong(name)==0L)
            val failure = gateOrder.firstOrNull { !gates.getBoolean(it) } ?: list(windowGates).firstNotNullOfOrNull { row -> row.getString("reason").takeIf { it != "ready" }?.let { "window.$it" } }
            return JSONObject().put("expected_count", count).put("ready", failure == null).put("reason", failure ?: "ready").put("gates", gates).put("windows", windowGates)
        }
        fun ready(s: JSONObject, count: Int): Boolean {
            val report = readiness(s, count); lastReadiness = report
            val ready = report.getBoolean("ready")
            check(report.getJSONObject("gates").getBoolean("terminal_markers")) { "UNOWNED_TERMINAL_WINDOW" }
            for ((window, _) in expected) {
                val entry = bound(s).singleOrNull { it.getJSONObject("state").getString("window") == window }
                if (entry != null && leases[window]?.let { it != leaseId(entry) } == true) error("LEASE_CHANGED")
                val target = plans(s).singleOrNull { it.getJSONObject("target").optString("window") == window }?.getJSONObject("target")
                if (target != null && targets[window]?.let { !same(it, target) } == true) error("TARGET_CHANGED")
                if (ready) { leases.putIfAbsent(window, leaseId(checkNotNull(entry))); targets.putIfAbsent(window, JSONObject(checkNotNull(target).toString())) }
            }
            if (ready) highestReady = maxOf(highestReady, count)
            return ready
        }
        fun nextLaunch(s: JSONObject) { pending = submit(s, "open_terminal"); stage = "adding"; publish() }
        fun settled(s: JSONObject): Boolean {
            if (settle == 0L) { settle = SystemClock.elapsedRealtime() + 2000; frameStart = rendered(s).associate { it.getJSONObject("key").getString("window") to it.getLong("frames") }; return false }
            return SystemClock.elapsedRealtime() >= settle && rendered(s).all { it.getLong("frames") > checkNotNull(frameStart[it.getJSONObject("key").getString("window")]) }
        }
        fun finish() {
            if (active === this) active = null
            try { publish() } catch (_: Exception) {
                result.put("result", "fail").put("metadata_failure", true).remove("checkpoints")
                NativeSessionHost.publishCheck(result)
            } finally { complete(result) }
        }
        fun fail(s: JSONObject?, failure: Exception) {
            result.put("result", "fail").put("failed_stage", stage).put("reached_windows", highestReady).put("failure_class", failure.javaClass.simpleName)
                .put("failure_site", failure.stackTrace.firstOrNull { it.className.startsWith("io.github.tinmachine.eidolon.") }?.toString())
            result.put("expected_windows", JSONObject(expected.toMap()))
            if (s != null) {
                result.put("failure_snapshot", runCatching { summary(s) }.getOrElse {
                    JSONObject().put("state",s.optString("state")).put("app",s.optJSONObject("app")?.let { app ->
                        JSONObject().put("state",app.optString("state")).put("error",app.opt("error")).put("clients",app.optInt("clients"))
                            .put("pool_count",app.optInt("pool_count")).put("pool_bytes",app.optLong("pool_bytes")).put("pool_rejections",app.optLong("pool_rejections"))
                    })
                })
                result.put("pool_budget_rejection_observed", (s.optJSONObject("app")?.optLong("pool_rejections") ?: 0L)>0L)
            }
            // Keep the last bounded observation even when the deadline/STALE path had no new snapshot.
            if (!result.has("failure_snapshot")) lastSnapshot?.let { result.put("failure_snapshot", it) }
            lastReadiness?.let { result.put("failure_readiness", it) }
            result.put("failure_observation", JSONObject().put("stage", lastObservedStage).put("elapsed_realtime_ms", lastObservedAt)
                .put("age_ms", if (lastObservedAt > 0) SystemClock.elapsedRealtime() - lastObservedAt else JSONObject.NULL)
                .put("snapshot_available", result.has("failure_snapshot")))
            if (!result.has("pool_budget_rejection_observed")) result.put("pool_budget_rejection_observed", (lastSnapshot?.optJSONObject("app")?.optLong("pool_rejections") ?: 0L) > 0L)
            result.put("surface_budget_rejection_observed", (lastSnapshot?.optJSONObject("app")?.optLong("surface_rejections") ?: 0L) > 0L)
            finish()
        }
        override fun run() {
            if (active !== this) return
            var observed: JSONObject? = null
            try {
                val current = NativeSessionHost.currentHandle()
                if (current > 0) {
                    val envelope = JSONObject(NativeBridge.nativeSnapshot(current)); val s = envelope.optJSONObject("runtime")
                    if (s == null) { check(SystemClock.elapsedRealtime() < deadline && SystemClock.elapsedRealtime() < overall) { "WINDOW_SLOTS_TIMEOUT" }; check(stage == "stopped" && envelope.optInt("code") == NativeBridge.STALE); ui.postDelayed(this, 32); return }
                    observed = s
                    lastSnapshot = summary(s); lastObservedAt = SystemClock.elapsedRealtime(); lastObservedStage = stage
                    lastReadiness = readiness(s, if (stage == "first") 1 else expected.size)
                    check(SystemClock.elapsedRealtime() < deadline && SystemClock.elapsedRealtime() < overall) { "WINDOW_SLOTS_TIMEOUT" }
                    check(s.optString("state") != "Failed") { "RUNTIME_FAILED" }
                    s.optJSONObject("app")?.let { check(it.isNull("error")) { "APP_FAILED" } }
                    if (handle > 0 && stage != "stopped") {
                        check(current == handle && s.optString("scene") == scene) { "OWNER_CHANGED" }
                        check(s.getBoolean("activity_visible") && s.getString("xr_state") == "FOCUSED") { "EXTERNAL_VISIBILITY_LOSS" }
                        s.optJSONObject("app")?.optString("session")?.takeIf { it.isNotEmpty() }?.let { check(session.isEmpty() || it == session) { "SESSION_CHANGED" } }
                    }
                    when (stage) {
                        "ready" -> if (s.optString("state") == "Ready" && s.optBoolean("activity_visible") && s.optString("xr_state") == "FOCUSED") {
                            check(s.getJSONObject("transport").getString("state") == "Idle"); handle = current; scene = s.getString("scene")
                            // Nonce-isolated preferences default to Off. Select the required theme explicitly,
                            // then wait for its actual native/render revision before opening the first foot.
                            NativeSessionHost.setTheme("builtin", reduced = false)
                            stage = "theme_ready"; deadline = SystemClock.elapsedRealtime() + 15000; publish()
                        }
                        "theme_ready" -> {
                            val theme = s.getJSONObject("theme"); val render = s.getJSONObject("render")
                            check(theme.isNull("error") && theme.isNull("fallback_reason")) { "THEME_SETUP_FAILED" }
                            if (theme.optString("requested") == "builtin" && theme.optString("selection") == "builtin" && !theme.optBoolean("loading") &&
                                theme.optLong("applied_revision", -1) == render.optLong("theme_revision", -2) && render.optString("theme_id") == "cyan-orbit") {
                                result.put("setup_theme_revision", render.getLong("theme_revision"))
                                record("builtin_theme_loaded_before_first_foot", s)
                                NativeSessionHost.openTerminal(); stage = "first"; deadline = SystemClock.elapsedRealtime() + 60000; publish()
                            }
                        }
                        "first" -> if (windows(s).size == 1 && windows(s).single().optString("life") == "Mapped") {
                            session = s.getJSONObject("app").getString("session"); expected[windows(s).single().getString("window")] = 0
                            if (ready(s, 1)) { result.put("wayland_clients", s.getJSONObject("app").getInt("clients")); check(s.getJSONObject("app").getInt("clients") == 1); nextLaunch(s) }
                        }
                        "adding" -> {
                            completions[pending]?.let { check(it == "ACCEPTED") { "ADD_COMMAND_REJECTED" } }
                            val matched = s.optJSONObject("window_launch")?.takeIf { it.optString("session") == session && it.optString("request_id") == pending && it.optString("phase") == "matched" }
                            if (completions[pending] == "ACCEPTED" && matched != null) {
                                val window = matched.getString("window")
                                val entry = bound(s).singleOrNull {it.getJSONObject("state").optString("window") == window}
                                if(entry != null) {
                                    check(entry.getJSONObject("lease").getJSONObject("origin").optString("launch") == pending) { "LAUNCH_LEASE_MISMATCH" }
                                    check(window !in expected) { "LAUNCH_REUSED_WINDOW" }
                                    expected[window] = if (hole.isEmpty()) expected.size else 2
                                    stage = if (hole.isEmpty()) "ready_count" else "replacement"; deadline = SystemClock.elapsedRealtime() + 15000
                                }
                            }
                        }
                        "ready_count" -> if (ready(s, expected.size)) {
                            check(s.getJSONObject("app").getInt("clients") == 1) { "FOOT_CLIENT_TOPOLOGY_CHANGED" }
                            if (expected.size in setOf(3, 6, 9)) {
                                if (settled(s)) { record("sdk_${expected.size}_frames_and_pose", s); settle = 0
                                    if (expected.size == 9) {
                                        busyBefore = expected.keys.joinToString()
                                        terminalCountBefore = s.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote").getInt("terminal_count")
                                        pending = submit(s, "open_terminal"); stage = "tenth"
                                    } else nextLaunch(s)
                                }
                            } else nextLaunch(s)
                        }
                        "tenth" -> if (completions[pending] != null && ready(s, 9)) {
                            check(completions[pending] == "BUSY" && expected.keys.joinToString() == busyBefore) { "TENTH_NOT_REFUSED" }
                            settle = SystemClock.elapsedRealtime() + 500; stage = "tenth_settled"
                        }
                        "tenth_settled" -> if (SystemClock.elapsedRealtime() >= settle && ready(s, 9)) {
                            check(expected.keys.joinToString() == busyBefore && s.getJSONObject("transport").getJSONObject("app_forward").getJSONObject("remote").getInt("terminal_count") == terminalCountBefore) { "BUSY_LAUNCHED_LATE" }
                            result.put("tenth_busy", true); record("tenth_refused_without_retiring_existing", s)
                            hole = expected.entries.single { it.value == 2 }.key; removed = JSONObject(checkNotNull(targets[hole]).toString())
                            pending = submit(s, "close_window_target", removed); stage = "hole"
                        }
                        "hole" -> if (completions[pending] == "ACCEPTED" && windows(s).none { it.getString("window") == hole } &&
                            s.getJSONObject("window_slots").getInt("occupied") == 8 && entries(s).none { it.optJSONObject("state")?.optString("window") == hole } &&
                            plans(s).none { it.getJSONObject("target").getString("window") == hole } && rendered(s).none { it.getJSONObject("key").getString("window") == hole } && sdk().none { it.getJSONObject("target").getString("window") == hole } && s.getJSONObject("shutdown").getInt("sdk_bindings") == 8) {
                            expected.remove(hole); check(ready(s, 8)) { "SURVIVORS_CHANGED_AFTER_DRAIN" }; result.put("hole_drained_before_reuse", true)
                            record("native_sdk_panel_drain_then_hole", s); nextLaunch(s)
                        }
                        "replacement" -> if (ready(s, 9)) {
                            check(expected.keys.none { it == hole } && bound(s).single { it.getJSONObject("state").getString("window") == expected.entries.single { it.value == 2 }.key }.getJSONObject("lease").getString("token") != leases[hole]?.split('/')?.getOrNull(2))
                            result.put("hole_reused_with_fresh_lease", true); record("hole_reused_without_moving_survivors", s)
                            val survivor = expected.entries.single { it.value == 1 }.key; NativeSessionHost.focusTerminal(choice(checkNotNull(targets[survivor]))); stage = "focus_survivor"
                        }
                        "focus_survivor" -> {
                            val target = checkNotNull(targets[expected.entries.single { it.value == 1 }.key]); val input = s.getJSONObject("app").getJSONObject("input")
                            if (input.optString("focus_window") == target.getString("window") && !input.isNull("focus_token") && NativeSessionHost.keyboardFocusMatches(target, input.getString("focus_token"))) {
                                keyDelivered = input.getLong("delivered"); val now = SystemClock.uptimeMillis()
                                for ((code, scan) in listOf(KeyEvent.KEYCODE_A to 30, KeyEvent.KEYCODE_DEL to 14)) for (action in listOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP))
                                    check(NativeSessionHost.sendPhysicalKey(KeyEvent(now, now, action, code, 0, 0, Int.MAX_VALUE - 16, scan, 0, InputDevice.SOURCE_KEYBOARD)))
                                stage = "keys"
                            }
                        }
                        "keys" -> if (ready(s, 9) && s.getJSONObject("app").getJSONObject("input").let { it.getLong("delivered") == keyDelivered + 4 && it.getInt("pressed") == 0 }) {
                            result.put("survivor_synthetic_keys_delivered", true); record("survivor_keys_after_capacity_and_hole", s)
                            stage = "stopped"; deadline = SystemClock.elapsedRealtime() + 30000; NativeSessionHost.withStoppedRuntime({}, {})
                        }
                        "stopped" -> if (current > handle && s.optString("state") == "Ready" && s.getJSONObject("transport").getString("state") == "Idle" &&
                            rendered(s).let { it.size == 1 && it.single().optString("role") == "diagnostic" && it.single().getLong("frames") >= 3 }) {
                            check(JSONObject(NativeBridge.nativeSnapshot(handle)).optInt("code") == NativeBridge.STALE)
                            check(s.getJSONObject("transport").getJSONArray("pending_cleanup").length() == 0 && s.getJSONObject("window_slots").getInt("occupied") == 0)
                            val c = adapter.metrics().getJSONObject("window_controls"); check(c.getLong("entity_owners") == 0L && c.getLong("listener_owners") == 0L)
                            val b = adapter.metrics().getJSONObject("window_mode_badges"); check(listOf("entity_owners", "object_owners", "mesh_owners", "material_owners", "texture_owners", "pixel_slots_in_use", "cleanup_pending_owners").all { b.getLong(it) == 0L })
                            val menus=adapter.metrics().getJSONObject("window_menus")
                            check(listOf("owners","cleanup_pending_owners","entity_owners","listener_owners","object_owners","mesh_owners","material_owners","texture_owners").all {menus.getLong(it)==0L})
                            result.put("old_runtime_destroyed", true).put("control_mode_owners_zero", true).put("menu_owners_zero",true)
                                .put("stopped_controls", c).put("stopped_modes", b).put("stopped_menus",menus).put("result", "pass")
                            finish(); return
                        }
                    }
                } else check(SystemClock.elapsedRealtime() < deadline && SystemClock.elapsedRealtime() < overall) { "WINDOW_SLOTS_TIMEOUT" }
                ui.postDelayed(this, 32)
            } catch (failure: Exception) { fail(observed, failure) }
        }
    }
}
