package io.github.tinmachine.eidolon

/** Product-worker state. Only fixed transport reasons are saved, scoped to the saved SSH host. */
internal class UbuntuShelfReconnect(
    private val read: (String) -> String?,
    private val write: (String, String?) -> Unit,
) {
    private var host = ""
    private var reason: String? = null
    private var saved: String? = null
    private var wanted = false
    private var next = 0L
    private var delayMs = 2000L
    private var refreshed = false

    fun bind(key: String) {
        if (host == key) return
        stop()
        host = key
        saved = if (key.isEmpty()) null else read(key)?.takeIf { it in explanations }
        reason = saved
    }

    fun stop() {
        wanted = false
        refreshed = false
        delayMs = 2000L
    }

    fun request(now: Long, automatic: Boolean = false) {
        if (!automatic) delayMs = 2000L
        if (refreshed) reason = saved
        wanted = true
        refreshed = false
        next = now + delayMs
        delayMs = (delayMs * 2).coerceAtMost(60_000L)
    }

    fun observe(code: String?, ready: Boolean, disconnected: Boolean = false) {
        if (host.isEmpty()) return
        if (disconnected && refreshed) { refreshed = false; reason = saved }
        if (code in explanations) {
            // Cleanup may follow a connection error. Preserve the more informative reason.
            if (code != "DESKTOP_CLEANUP_PENDING" || saved == null) reason = code
            refreshed = false
            if (saved != reason) {
                saved = reason
                write(host, reason)
            }
        } else if (ready && wanted) {
            refreshed = true
            delayMs = 2000L
            if (saved != null) {
                saved = null
                write(host, null)
            }
        }
    }

    fun retryDue(now: Long): Boolean = wanted && !refreshed && now >= next &&
        (reason == null || reason in transient || reason == "DESKTOP_CLEANUP_PENDING")

    fun notice(busy: Boolean, recovering: Boolean): String? {
        val previous = explanations[reason] ?: return null
        if (!wanted) return null
        val action = when {
            refreshed -> "再接続してアプリ一覧を更新しました。"
            recovering -> "終了を確認してから再接続します。"
            busy -> "再接続してアプリ一覧を取得しています。"
            reason in transient -> "自動で再接続を試みます。"
            else -> "接続設定とホストの状態を確認してください。"
        }
        return "前回の接続記録 · ${previous}。$action"
    }

    companion object {
        private val transient = setOf("CONNECT_TIMEOUT", "SSH_CONNECT_FAILED", "SSH_CONNECTION_REFUSED",
            "REMOTE_EXIT", "SSH_IO_FAILED", "AUTH_TIMEOUT")
        private val explanations = mapOf(
            "CONNECT_TIMEOUT" to "Ubuntu の応答待ちが時間切れになりました",
            "SSH_CONNECT_FAILED" to "Ubuntu に接続できませんでした",
            "SSH_CONNECTION_REFUSED" to "Ubuntu が SSH 接続を拒否しました",
            "REMOTE_EXIT" to "接続先の処理が終了しました",
            "SSH_IO_FAILED" to "SSH 通信が途切れました",
            "AUTH_TIMEOUT" to "SSH 認証が時間切れになりました",
            "AUTH_FAILED" to "SSH 認証に失敗しました",
            "HOST_KEY_MISMATCH" to "保存済みのホスト鍵と一致しませんでした",
            "HOST_KEY_UNKNOWN" to "ホスト鍵を確認できませんでした",
            "DESKTOP_CLEANUP_PENDING" to "前回の終了確認が残っていました",
        )
    }
}
