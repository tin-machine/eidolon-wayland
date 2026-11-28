package io.github.tinmachine.eidolon

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject

/** ADB requests to the running debug app. Only the named operation and its nonce are returned. */
class EidolonCtlReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!BuildConfig.DEBUG || !isOrderedBroadcast || intent.component?.packageName != context.packageName ||
            intent.component?.className != javaClass.name) return
        val nonce = intent.getStringExtra("nonce")?.takeIf { it.matches(Regex("[0-9a-f]{32}")) } ?: return
        val response = JSONObject().put("schema", 1).put("nonce", nonce)
        val outcome = runCatching {
            when (intent.action) {
                "${context.packageName}.CTL_WINDOW_LIST" -> {
                    if (intent.extras?.keySet() != setOf("nonce")) return
                    val windows = listWindows() ?: return@runCatching "runtime_unavailable"
                    response.put("windows", windows)
                    WindowGroupsCase.cliVisibilityObserved(windows)
                    "ok"
                }
                "${context.packageName}.CTL_WINDOW_SHOW" -> {
                    if (intent.extras?.keySet() != setOf("nonce", "window", "generation")) return
                    response.put("show", show(intent))
                    "ok"
                }
                else -> return
            }
        }.getOrDefault("invalid")
        response.put("status", outcome)
        resultCode = if (outcome == "ok") 0 else 1
        resultData = Base64.encodeToString(
            response.toString().toByteArray(Charsets.UTF_8),
            Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING
        )
    }

    private fun target(intent: Intent): InputWindowKey? {
        val window = intent.getStringExtra("window")?.split(':', limit = 5) ?: return null
        val generation = intent.getStringExtra("generation") ?: return null
        if (window.size != 4 || (window + generation).any {
                !it.matches(Regex("[1-9][0-9]{0,18}")) || it.toLongOrNull() == null
            }) return null
        return InputWindowKey(window[0].toLong(), window[1], window[2], window[3], generation)
    }

    private fun show(intent: Intent): String {
        val key = target(intent) ?: return "invalid"
        val fixture = WindowGroupsCase.acceptsCliShow(key)
        if (!fixture && !BuildHooks.imeProductProbeAllowed()) return "busy"
        val rows = listWindows()?.objects() ?: return "runtime_unavailable"
        val window = rows.singleOrNull {
            it.optString("id") == intent.getStringExtra("window") && it.optString("generation") == key.generation
        } ?: return "stale"
        if (window.optBoolean("visible")) return "already_visible"
        return NativeSessionHost.showCollapsedWindow(key) { request ->
            if (fixture) WindowGroupsCase.cliShowQueued(key, request)
        }
    }

    private fun listWindows(): JSONArray? {
        val primary = NativeSessionHost.currentHandle().takeIf { it > 0 } ?: return null
        val runtimes = (listOf(primary) + NativeSessionHost.otherWorkspaceRuntimes()).distinct()
        val windows = JSONArray()
        for (runtime in runtimes) {
            val snapshot = JSONObject(NativeBridge.nativeUiSnapshot(runtime, false))
            if (snapshot.optInt("code", -1) != 0) {
                if (runtime == primary) return null
                continue
            }
            val state = snapshot.getJSONObject("runtime")
            val app = state.optJSONObject("app") ?: continue
            val scene = number(state, "scene") ?: continue
            val session = number(app, "session") ?: continue
            val panels = state.getJSONArray("panels").objects().filter {
                it.optString("action") == "create" && it.isNull("error")
            }
            val render = state.getJSONObject("render")
            val targets = render.getJSONArray("targets").objects()
            for (window in app.getJSONArray("windows").objects().filter { it.optString("life") == "Mapped" }) {
                val id = number(window, "window") ?: continue
                val key = panels.map { it.getJSONObject("target") }.singleOrNull {
                    number(it, "session") == session && number(it, "window") == id && number(it, "scene") == scene
                }
                val generation = key?.let { number(it, "generation") }
                val target = targets.singleOrNull {
                    it.optString("role") == "windows" && it.getJSONObject("key").let { rendered ->
                        number(rendered, "session") == session && number(rendered, "window") == id &&
                            number(rendered, "scene") == scene && number(rendered, "generation") == generation
                    }
                }
                val visible = generation != null && target != null && target.optLong("frames") > 0 &&
                    !target.optBoolean("presentation_paused") && !render.optBoolean("paused") &&
                    state.optBoolean("activity_visible") && state.optString("xr_state") == "FOCUSED" &&
                    adapter?.windowVisible(InputWindowKey(runtime, session, id, scene, generation)) == true
                val logical = window.optJSONObject("logical_size")
                windows.put(JSONObject().put("id", listOf(runtime, session, id, scene).joinToString(":"))
                    .put("generation", generation ?: JSONObject.NULL)
                    .put("visible", visible)
                    .put("width", logical?.optInt("width") ?: JSONObject.NULL)
                    .put("height", logical?.optInt("height") ?: JSONObject.NULL))
            }
        }
        return windows.takeIf { NativeSessionHost.currentHandle() == primary }
    }

    private fun JSONArray.objects() = (0 until length()).map(::getJSONObject)
    private fun number(objectValue: JSONObject, key: String): String? =
        objectValue.optString(key).takeIf { (it.toLongOrNull() ?: 0) > 0 }

    companion object {
        internal var adapter: SpatialPanelAdapter? = null
    }
}
