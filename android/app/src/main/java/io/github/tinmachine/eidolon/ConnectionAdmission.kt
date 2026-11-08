package io.github.tinmachine.eidolon

import org.json.JSONObject

/** 通常接続の入口。初期診断の所有権と、回収を待つアプリの所有権を分ける。 */
internal object ConnectionAdmission {
    private val disconnected = setOf("Idle", "Disconnected", "Failed")

    fun localTargetsRetired(runtime: JSONObject): Boolean {
        val targets = runtime.getJSONObject("render").getJSONArray("targets")
        val board = runtime.getJSONObject("window_slots")
        return (runtime.optJSONObject("app")?.optString("state") ?: "stopped") == "stopped" &&
            // Native が登録時の役割と完全な所有tupleを保持する。SDK総数やsession/window番号では分類しない。
            runtime.opt("app_sdk_bindings") == 0 && runtime.getJSONArray("panels").length() == 0 &&
            (0 until targets.length()).all { targets.getJSONObject(it).optString("role") == "diagnostic" } &&
            board.getInt("occupied") == 0 && !board.getBoolean("cleanup_required") &&
            board.getJSONArray("entries").length() == 0 && board.getJSONArray("unassigned").length() == 0
    }

    fun canChange(runtime: JSONObject, busy: Boolean): Boolean {
        if (busy) return false
        val transport = runtime.getJSONObject("transport")
        return runtime.getString("state") == "Ready" &&
            transport.getString("state") in disconnected && transport.getInt("owned_connections") == 0 &&
            transport.getJSONArray("pending_cleanup").length() == 0 && !transport.optBoolean("desktop_pending") &&
            localTargetsRetired(runtime)
    }

    fun canSelectRecovery(runtime: JSONObject, busy: Boolean): Boolean {
        if (busy) return false
        val transport = runtime.getJSONObject("transport")
        return runtime.getString("state") == "Ready" && transport.optBoolean("desktop_pending") &&
            transport.getInt("owned_connections") == 0 && transport.getString("state") in disconnected + "CleanupPending" &&
            transport.getJSONArray("pending_cleanup").length() == 0 && localTargetsRetired(runtime)
    }

    fun canConnectLinux(runtime: JSONObject, busy: Boolean): Boolean {
        if (busy) return false
        val transport = runtime.getJSONObject("transport")
        return localTargetsRetired(runtime) && transport.getInt("owned_connections") == 0 &&
            transport.getString("state") in disconnected + "CleanupPending"
    }
}
