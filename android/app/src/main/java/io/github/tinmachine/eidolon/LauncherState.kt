package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject

data class LauncherEntry(
    val id: String, val label: String, val icon: String, val available: Boolean, val error: String?,
    val desktopId: String? = null,
)

internal fun LauncherEntry.matchesShelfQuery(query: String, linux: Boolean, host: String, source: String): Boolean =
    if (linux && query.endsWith(".desktop", ignoreCase = true)) desktopId == query
    else listOfNotNull(label, desktopId, source, host).any { it.contains(query, ignoreCase = true) }

data class LauncherUi(
    val session: String = "", val connected: Boolean = false, val busy: Boolean = false,
    val revision: String = "", val builtin: Boolean = true, val total: Int = 0, val offset: Int = 0,
    val entries: List<LauncherEntry> = emptyList(), val path: String = ".config/eidolon/launcher.toml",
    val message: String = "ターミナルを開くとアプリ一覧を読み込めます",
    val linux: Boolean = false, val canConnect: Boolean = false, val canInspect: Boolean = false,
    val canRecover: Boolean = false, val canStop: Boolean = false, val running: Boolean = false,
    val canRefresh: Boolean = false,
    val canDiscover: Boolean = false,
    val runtime: Long = 0, val capture: String = "", val v3: Boolean = false,
    val phase: String = "catalog_waiting", val firstFailure: String? = null, val cleanupFailure: String? = null,
    val menuRefreshing: Boolean = false,
    val menuRefreshFailed: Boolean = false,
    val companionRunning: Boolean = false,
    val companionCleanup: Boolean = false,
    val catalogComplete: Boolean = false,
    val recoveryWait: String? = null,
    val systemd: Boolean = false,
) {
    override fun toString() = "LauncherUi(<private>)"
}

internal object UbuntuSystemdLauncher {
    const val Revision = "ubuntu_systemd_v1"
    const val ChromeId = "google-chrome.desktop"
    const val Launch = "ubuntu_systemd_launch"
    const val NewChromeWindow = "ubuntu_systemd_chrome_new_window"

    fun report(runtime: JSONObject?) = runtime?.optJSONObject("transport")?.optJSONObject("systemd_chrome")

    fun readCatalog(runtime: Long, session: String, chromeAvailable: Boolean): List<LauncherEntry>? = runCatching {
        val reply = JSONObject(NativeBridge.nativeCatalogUi(runtime))
        val catalog = reply.optJSONObject("catalog") ?: return null
        if (reply.optInt("code") != NativeBridge.ACCEPTED || catalog.optString("session") != session) return null
        val entries = catalog.optJSONArray("ubuntu_systemd_installed") ?: return null
        buildList {
            if (chromeAvailable) add(LauncherEntry(ChromeId, "Google Chrome", "builtin:web", true, null, ChromeId))
            for (i in 0 until entries.length()) {
                val entry = entries.getJSONObject(i)
                val desktopId = entry.getString("id")
                if (desktopId == ChromeId) continue
                val error = entry.optString("disabled_reason").takeUnless { entry.isNull("disabled_reason") }
                add(LauncherEntry(entry.getString("revision"), entry.getString("name"),
                    "builtin:script", error == null, error, desktopId))
            }
        }
    }.getOrNull()

    fun launchArgs(session: String, id: String, policy: GuiSessionPolicy): JSONObject =
        JSONObject().put("session", session).put(GuiSessionPolicy.WireField, policy.seconds).also {
            if (id != ChromeId) it.put("desktop_revision", id)
        }

    fun canOpenChromeWindow(runtime: JSONObject): Boolean {
        val report = report(runtime)
        val slots = runtime.optJSONObject("window_slots") ?: return false
        return report?.optString("phase") == "running" && report.optString("app_id") == ChromeId &&
            slots.optInt("occupied") < slots.optInt("capacity")
    }

    fun chromeWindowArgs(runtime: JSONObject): JSONObject? {
        if (!canOpenChromeWindow(runtime)) return null
        val launch = report(runtime)?.opt("launch_request") as? String ?: return null
        val session = runtime.optJSONObject("transport")?.opt("session") as? String ?: return null
        return JSONObject().put("session", session).put("launch", launch)
    }
}

object LauncherMessages {
    fun error(code: String?): String = when(code) {
        null -> ""
        "PATH_MISSING" -> "指定したファイルが見つかりません"
        "CATALOG_CHANGED" -> "設定が変更されています。再読込してください"
        "CATALOG_DUPLICATE_ID" -> "同じ登録IDが複数あります"
        "CATALOG_INVALID", "CATALOG_SCHEMA" -> "設定ファイルの形式を確認してください"
        "CATALOG_LIMIT" -> "設定・項目・引数の上限を超えています"
        "CATALOG_FILE_UNSAFE" -> "設定ファイルの所有者と書込権限を確認してください"
        "CATALOG_UNAVAILABLE" -> "設定の読込にはTermuxのPython 3.11以降が必要です"
        "EXECUTABLE_DENIED", "INTERPRETER_UNAVAILABLE" -> "実行ファイルの権限・インタープリターを確認してください"
        "EXECUTABLE_NOT_FILE" -> "通常の実行ファイルを指定してください"
        "CWD_UNAVAILABLE" -> "作業ディレクトリを利用できません"
        "GUI_NOT_VERIFIED" -> "GUIの起動は準備中です"
        "APP_NOT_FOUND" -> "登録が見つかりません。再読込してください"
        else -> "操作できませんでした。接続状態とファイルを確認してください"
    }
}

/** 引数欄は一つずつ保持し、空白で分割せずnativeへ渡す。本文は診断へ保存しない。 */
data class LauncherForm(val id: String = "my-app", val label: String = "マイアプリ", val executable: String = "",
    val interpreter: String = "", val cwd: String = ".", val args: List<String> = emptyList()) {
    fun valid(): Boolean = id.matches(Regex("[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}")) &&
        label.isNotBlank() && label.toByteArray().size <= 160 && executable.isNotBlank() &&
        listOf(executable,interpreter,cwd).all { it.toByteArray().size <= 1024 && it.none { c -> c.code < 32 || c.code in 127..159 } } &&
        label.none { it.code < 32 || it.code in 127..159 } && args.size <= 64 &&
        args.all { it.toByteArray().size <= 2048 && '\u0000' !in it } && args.sumOf { it.toByteArray().size } <= 6000
    fun definition(): JSONObject {
        require(valid())
        return JSONObject().put("id",id).put("label",label).put("mode","terminal").put("executable",executable)
            .put("args",JSONArray(args)).put("cwd",cwd.ifEmpty { "." }).put("icon","builtin:script").also {
                if(interpreter.isNotEmpty()) it.put("interpreter",interpreter)
            }
    }
}


internal data class ShelfCatalog(val entries: List<LauncherEntry>, val savedAtMs: Long, val failure: String? = null)

/** Termux replies are paged and carry a single immutable revision. */
internal class TermuxCatalogPages {
    private var revision: String? = null
    private var expectedTotal = 0
    private val collected = ArrayList<LauncherEntry>()

    fun reset() {
        revision = null
        expectedTotal = 0
        collected.clear()
    }

    fun append(revision: String, offset: Int, total: Int, entries: List<LauncherEntry>): List<LauncherEntry>? {
        if (total !in 0..64 || entries.size != minOf(8, total - offset)) return null
        if (offset == 0) {
            reset()
            this.revision = revision
            expectedTotal = total
        }
        if (this.revision != revision || expectedTotal != total || offset != collected.size ||
            entries.any { entry -> collected.any { it.id == entry.id } } ||
            entries.map { it.id }.distinct().size != entries.size) return null
        collected.addAll(entries)
        return collected.toList()
    }
}
