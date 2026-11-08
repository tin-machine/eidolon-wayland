package io.github.tinmachine.eidolon

import org.json.JSONObject

/** 起動前だけの終了確認。Launch後の全target退役には使用しない。 */
internal object NormalLinuxGtkAdmission {
    fun beforeLaunchClosed(runtime: JSONObject, session: String, connectRequest: String,
        connectReplyKnown: Boolean, launched: Boolean): Boolean {
        if (launched || !connectReplyKnown) return false
        val transport = runtime.getJSONObject("transport")
        val sameOwner = connectRequest.isEmpty() || session.isNotEmpty() &&
            transport.optString("session") == session && transport.optString("profile_id") == LinuxConnectionProfile.ID
        return sameOwner && ConnectionAdmission.canChange(runtime, false)
    }
}
