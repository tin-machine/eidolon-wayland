package io.github.tinmachine.eidolon

import android.content.SharedPreferences
import androidx.compose.runtime.mutableStateMapOf
import org.json.JSONArray
import org.json.JSONObject

/** A catalog selection outlives its card, catalog revision, and runtime slot. */
internal fun launcherAppKey(source: LauncherSource, state: LauncherUi, entry: LauncherEntry): String {
    val host = launcherSourceKey(source)
    val identity = if (entry.desktopId != null) listOf(host, "desktop", entry.desktopId)
        else listOf(host, if (source.linux) "registered" else state.path, entry.id)
    return LinuxStorageIdentity.sha256(identity.joinToString("\u0000").toByteArray())
}

internal data class AppFailure(val reason: String, val log: String, val event: String? = null) {
    fun encode(): String = JSONObject().put("reason", reason).put("log", log).put("event", event).toString()

    companion object {
        fun decode(value: String): AppFailure? = runCatching {
            val record = JSONObject(value)
            AppFailure(record.getString("reason"), record.getString("log"), record.opt("event") as? String)
        }.getOrNull()
    }
}

internal class LauncherFailures {
    class Attempt internal constructor(val key: String) { internal var event: String? = null }
    private val newest = mutableMapOf<String, Attempt>()
    private val reasons = mutableStateMapOf<String, AppFailure>()
    private var preferences: SharedPreferences? = null

    @Synchronized fun restore(storage: SharedPreferences) {
        preferences = storage
        for ((key, value) in storage.all) if (key.length == 64 && value is String) AppFailure.decode(value)?.let { reasons[key] = it }
    }

    fun reason(key: String): AppFailure? = reasons[key]

    @Synchronized fun begin(key: String) = Attempt(key).also {
        newest[key] = it
        // Imported pre-update crashes have no local attempt. Subsequent imports must match the exact launch.
        preferences?.edit()?.remove("event:$key")?.putBoolean("attempted:$key", true)?.apply()
    }

    @Synchronized fun bind(attempt: Attempt, event: String) {
        if (newest[attempt.key] !== attempt || attempt.event == event) return
        attempt.event = event
        preferences?.edit()?.putString("event:${attempt.key}", event)?.apply()
    }

    @Synchronized fun importCrash(key: String, row: JSONObject) {
        val storage = preferences ?: return
        val time = row.optLong("time_ms")
        val event = row.optString("event")
        if (!event.matches(Regex("[0-9a-f]{16}-[1-9][0-9]{0,18}")) || time <= 0 ||
            time <= storage.getLong("seen:$key", 0)) return
        // Even discarded old reports must not reappear after a later successful retry.
        val edit = storage.edit().putLong("seen:$key", time)
        if (newest[key]?.let { it.event == null } == true) return
        if (!storage.getBoolean("attempted:$key", false) || event == storage.getString("event:$key", null)) {
            val frames = row.optJSONArray("frames") ?: return
            val trace = mutableListOf<String>()
            var mesaFault = false
            var hasSdl = false
            for (i in 0 until minOf(16, frames.length())) {
                val frame = frames.optJSONArray(i) ?: continue
                val (index, module, pc) = List(3) { frame.optString(it) }
                if (!index.matches(Regex("[0-9]{2}")) || !pc.matches(Regex("[0-9a-f]{1,16}")) ||
                    module !in setOf("chocolate-doom", "libSDL2-2.0.so", "libEGL_mesa.so", "libc.so")) continue
                trace.add("#$index  $module  +0x${pc.trimStart('0').ifEmpty { "0" }}")
                if (index == "00" && module == "libEGL_mesa.so") mesaFault = true
                if (module == "libSDL2-2.0.so") hasSdl = true
            }
            if (trace.isEmpty()) return
            val presentation = mesaFault && hasSdl && row.optBoolean("render_present")
            val reason = if (presentation) "Chocolate Doom が SDL にフレームの表示を依頼し、Mesa の描画処理中に異常終了しました（SIGSEGV）。"
                else "Android がこの起動のメモリアクセス異常を記録しました（SIGSEGV）。"
            val log = "Android クラッシュログ · SIGSEGV\n" + trace.joinToString("\n") +
                if (presentation) "\n実行ファイルと照合した呼び出し · SDL_RenderPresent" else ""
            val failure = AppFailure(reason, log, event)
            reasons[key] = failure
            edit.putString(key, failure.encode())
        }
        edit.apply()
    }

    // A second copy of the same app may already have succeeded on another slot.
    @Synchronized fun record(attempt: Attempt, reason: AppFailure?) {
        if (newest[attempt.key] !== attempt || reasons[attempt.key] == reason) return
        // A later GUI exit receipt for the same crash must keep its symbolicated trace.
        if (reason?.event != null && reasons[attempt.key]?.event == reason.event &&
            reasons[attempt.key]?.log?.startsWith("Android クラッシュログ") == true) return
        if (reason == null) reasons.remove(attempt.key) else reasons[attempt.key] = reason
        preferences?.edit()?.putString(attempt.key, reason?.encode())?.apply()
    }

    @Synchronized fun finish(attempt: Attempt) {
        if (newest[attempt.key] === attempt) newest.remove(attempt.key)
    }
}

internal sealed interface AppObservation {
    data object Displayed : AppObservation
    data object Completed : AppObservation
    data object Cancelled : AppObservation
    data class Failed(val reason: String, val log: String, val event: String? = null) : AppObservation
}

private fun JSONArray?.rows(): Sequence<JSONObject> =
    if (this == null) emptySequence() else (0 until length()).asSequence().mapNotNull { optJSONObject(it) }

/** Receipts use a null request for start_app, and a command ID for launch_terminal. */
internal class TermuxAppObservation(val session: String, val nonce: String, val request: String?) {
    fun read(state: JSONObject): AppObservation? {
        val remote = remoteForLaunch(state) ?: return null
        val receipt = (remote.optJSONArray("gui_receipts").rows() +
            remote.optJSONObject("finished")?.optJSONArray("gui_receipts").rows()).firstOrNull {
            matches(it) && it.opt("nonce") == nonce && it.opt("session") == session
        }
        if (receipt != null) return exit(receipt)
        val launch = remote.optJSONArray("terminal_launches").rows().singleOrNull(::matches) ?: return null
        if (launch.opt("accepted") == false) {
            val code = remote.optJSONObject("last_terminal")?.takeIf(::matches)?.optString("error")
            return AppObservation.Failed(if (code == "GUI_NOT_VERIFIED") "Termux が GUI の起動条件を確認できませんでした。"
                else "Termux がこのアプリの起動を受け付けませんでした。", "Termux · terminal_launch.accepted=false" + if (code == "GUI_NOT_VERIFIED") " · GUI_NOT_VERIFIED" else "")
        }
        if (launch.opt("accepted") != true) return null
        val app = state.optJSONObject("app") ?: return null
        if (app.opt("session") != session || app.opt("nonce") != nonce) return null
        val prefix = when (launch.optString("mode")) {
            "gui" -> "io.github.tinmachine.eidolon.gui.g-"
            "terminal" -> "io.github.tinmachine.eidolon.foot.t-"
            else -> return null
        }
        val cookie = prefix + nonce + "-" + launch.optString("terminal_id")
        return if (app.optJSONArray("windows").rows().any { it.opt("app_id") == cookie && it.opt("life") == "Mapped" })
            AppObservation.Displayed else null
    }

    fun event(state: JSONObject): String? {
        val rows = remoteForLaunch(state)?.optJSONArray("terminal_launches").rows()
        val row = rows.singleOrNull { it.opt("mode") == "gui" && matches(it) } ?: return null
        return nonce + "-" + row.optString("terminal_id")
    }

    private fun matches(row: JSONObject) = if (request == null) row.isNull("request_id") else row.opt("request_id") == request

    private fun remoteForLaunch(state: JSONObject): JSONObject? {
        val tx = state.optJSONObject("transport") ?: return null
        val forward = tx.optJSONObject("app_forward") ?: return null
        if (tx.opt("session") != session || forward.opt("nonce") != nonce) return null
        return forward.optJSONObject("remote")
    }

    private fun exit(row: JSONObject): AppObservation? {
        val reason = row.optString("reason")
        val result = row.opt("child_returncode") as? Number
        val log = "Termux · gui_exited_v1\nreason=$reason" + (result?.let { "\nchild_returncode=$it" } ?: "")
        return when (reason) {
            "natural" -> when (val code = result?.toInt()) {
                null -> null
                0 -> AppObservation.Completed
                -11 -> AppObservation.Failed("アプリがメモリにアクセスした際に異常終了しました（SIGSEGV）。", log, nonce + "-" + row.optString("terminal_id"))
                in -255..-1 -> AppObservation.Failed("アプリがシグナル ${-code} を受けて終了しました。", log)
                else -> AppObservation.Failed("アプリが終了コード ${code} を返しました。", log)
            }
            "output_limit" -> AppObservation.Failed("アプリの出力量が上限を超えたため停止しました。", log)
            "unsupported" -> AppObservation.Failed("この環境ではアプリが要求した起動方式を利用できません。", log)
            "failed" -> AppObservation.Failed("Termux がアプリの起動または実行を完了できませんでした。", log)
            "signal", "cancel", "control_eof" -> AppObservation.Cancelled
            else -> null
        }
    }
}

/** Only an app-scoped fault from this launch can mark its card. Cleanup has its own UI. */
internal class LinuxAppObservation(private val session: String, private val request: String,
    private val attempt: LauncherFailures.Attempt) {
    private var wasReady = false

    fun read(runtime: Long, state: JSONObject, failures: LauncherFailures): Boolean {
        val transport = state.optJSONObject("transport") ?: return false
        val report = transport.optJSONObject("desktop_v3_transport") ?: return false
        if (transport.opt("session") != session || report.opt("session") != session || report.opt("launch_request") != request) return false
        val view = LinuxDesktopV3Status.observe(runtime, session, state)
        val code = view.firstFailure
        if (code != null && code.startsWith("APP_") && code !in setOf("APP_STARTUP_CANCELLED", "APP_STATE_RECOVERY_REQUIRED")) {
            failures.record(attempt, AppFailure(checkNotNull(LinuxDesktopV3Messages.reason(code)),
                "Ubuntu · Linux Desktop v3\nfirst_failure=" + code.take(64)))
            return true
        }
        if (view.ready && !wasReady) {
            wasReady = true
            failures.record(attempt, null)
        }
        if (!wasReady && report.optBoolean("terminal_observed") && report.opt("terminal_reason") == "process_exited" &&
            state.optJSONObject("desktop_v3_presentation")?.optBoolean("startup_completed") != true) {
            failures.record(attempt, AppFailure("アプリがウィンドウを表示する前に終了しました。接続先から終了コードは届いていません。",
                "Ubuntu · Linux Desktop v3\nterminal_reason=process_exited"))
        }
        return report.optBoolean("terminal_observed")
    }

    fun finish(failures: LauncherFailures) = failures.finish(attempt)
}
