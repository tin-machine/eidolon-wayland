package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

/** Product-worker snapshot. A browsing runtime owns its workspace without consuming an app. */
internal data class LauncherCapacity(
    val used: Int = 0,
    val nextWorkspace: Int? = null,
    private val ubuntuNeedsWorkspace: Boolean = false,
    private val termuxNeedsWorkspace: Boolean = false,
) {
    enum class Workspace { FREE, BROWSING, APP }

    fun refusal(source: LauncherSource? = null): String? {
        if (used >= MAX_APPS) return "同時起動は${MAX_APPS}アプリまでです。アプリを閉じてください。"
        val needsWorkspace = when (source) {
            LauncherSource.UBUNTU -> ubuntuNeedsWorkspace
            LauncherSource.TERMUX -> termuxNeedsWorkspace
            null -> false // Add a window to the existing primary runtime.
        }
        return if (needsWorkspace && nextWorkspace == null) "追加アプリの起動枠が空くまでお待ちください。" else null
    }

    companion object {
        const val MAX_APPS = 20
        val workspaces = 2..(MAX_APPS + 1)

        fun appOccupiesRuntime(snapshot: JSONObject?): Boolean {
            val app = snapshot?.optJSONObject("app") ?: return false
            val state = app.optString("state")
            val reclamation = snapshot.optJSONObject("reclamation")
            return state.isNotEmpty() && state != "stopped" ||
                reclamation?.optBoolean("local_cleanup_pending") == true ||
                reclamation?.optBoolean("remote_cleanup_pending") == true
        }

        fun linuxApps(primary: JSONObject, launching: Boolean): Int {
            val transport = primary.optJSONObject("transport")
            val cleanup = primary.optBoolean("desktop_v3_finalizer_pending") ||
                transport?.optBoolean("desktop_pending") == true || transport?.optString("state") == "CleanupPending"
            return if (launching || cleanup || appOccupiesRuntime(primary)) 1 else 0
        }

        fun terminalApps(windows: JSONArray?, primaryOccupied: Boolean, beforeAdd: Set<String>?): Int {
            var roots = 0
            var newRoot = false
            if (windows != null) for (i in 0 until windows.length()) {
                val window = windows.getJSONObject(i)
                if (window.isNull("parent")) {
                    roots++
                    if (beforeAdd != null && window.optString("window") !in beforeAdd) newRoot = true
                }
            }
            val pending = beforeAdd != null && !newRoot
            return maxOf(roots, if (primaryOccupied) 1 else 0) + if (pending) 1 else 0
        }

        fun observe(primaryApps: Int, independentTermuxApp: Boolean, ubuntuPrimary: Boolean,
                    ubuntuNeedsWorkspace: Boolean, termuxNeedsWorkspace: Boolean,
                    workspace: (Int) -> Workspace): LauncherCapacity {
            var used = primaryApps + if (independentTermuxApp) 1 else 0
            var next: Int? = null
            for (slot in workspaces) {
                when (workspace(slot)) {
                    Workspace.APP -> used++
                    Workspace.BROWSING -> Unit
                    Workspace.FREE -> if (next == null && (slot != 2 || ubuntuPrimary)) next = slot
                }
            }
            return LauncherCapacity(used, next.takeIf { used < MAX_APPS }, ubuntuNeedsWorkspace, termuxNeedsWorkspace)
        }
    }
}
