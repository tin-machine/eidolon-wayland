package io.github.tinmachine.eidolon

import org.json.JSONObject

/** The native, inspected B1 descriptor, captured for one Host runtime and settings revision. */
class LinuxDesktopV3Catalog private constructor(
    val runtime: Long,
    val session: String,
    val capture: String,
    val revision: String,
    private val scope: String,
    val entries: List<LauncherEntry>,
    val installedAvailable: Boolean,
) {
    fun current(runtime: Long, session: String, capture: String): Boolean =
        this.runtime == runtime && this.session == session && this.capture == capture

    fun accepts(runtime: Long, session: String, capture: String, revision: String, id: String): Boolean =
        current(runtime, session, capture) && this.revision == revision &&
            entries.any { it.id == id && it.available }

    fun selection(id: String): JSONObject {
        require(entries.any { it.id == id && it.available }) { "DESKTOP_CATALOG_INVALID" }
        return JSONObject().put("scope", scope).put("revision", revision).put("app_id", id)
    }

    internal fun claimKey(): String = listOf(runtime.toString(), session, capture, revision, scope).joinToString(":")
    override fun toString() = "LinuxDesktopV3Catalog(<private>)"

    companion object {
        const val REVISION = "eidolon-linux-desktop-v3"
        const val DEMO_ID = "gtk3-desktop"
        const val EDITOR_ID = "mousepad-editor-v1"
        private val FIXED_ENTRIES = listOf(
            mapOf("id" to DEMO_ID, "label" to "Linux アプリ", "mode" to "gui", "icon" to "builtin:script"),
            mapOf("id" to EDITOR_ID, "label" to "Mousepad", "mode" to "gui", "icon" to "builtin:editor"),
        )
        private val FIELDS = setOf("schema", "namespace", "protocol_revision", "scope", "revision", "entries",
            "arbitrary_exec", "terminal", "instance_limit")

        /** Parse original descriptor bytes; JSONObject round-trips must not hide duplicate keys. */
        fun decode(runtime: Long, session: String, capture: String, raw: ByteArray,
            installed: List<*>? = null, editorAvailable: Boolean = true): LinuxDesktopV3Catalog = try {
            require(runtime > 0 && positiveDecimal(session) && LinuxStorageIdentity.hex(capture, 64))
            val value = LinuxStorageJson.parse(raw) as? Map<*, *> ?: error("DESKTOP_CATALOG_INVALID")
            require(value.keys == FIELDS && value["schema"] == 1L && value["namespace"] == "linux-desktop" &&
                value["protocol_revision"] == REVISION && value["arbitrary_exec"] == false &&
                value["terminal"] == false && value["instance_limit"] == 1L)
            // List equality also rejects reordering, duplicate IDs, omitted entries and changed label/mode/icon.
            require(value["entries"] == FIXED_ENTRIES)
            val scope = value["scope"] as? String ?: error("DESKTOP_CATALOG_INVALID")
            val revision = value["revision"] as? String ?: error("DESKTOP_CATALOG_INVALID")
            require(LinuxStorageIdentity.hex(scope, 64) && LinuxStorageIdentity.hex(revision, 64))
            val fixed = FIXED_ENTRIES.filter { editorAvailable || it.getValue("id") != EDITOR_ID }.map {
                LauncherEntry(it.getValue("id"), it.getValue("label"), it.getValue("icon"), true, null)
            }
            LinuxDesktopV3Catalog(runtime, session, capture, revision, scope, fixed + decodeInstalled(installed),
                installedAvailable = installed != null)
        } catch (_: Exception) { throw IllegalArgumentException("DESKTOP_CATALOG_INVALID") }

        private val installedFields = setOf("id", "name", "revision", "disabled_reason")
        private val disabledReasons = setOf("terminal_required", "dbus_activation_required",
            "executable_unavailable", "exec_format_unsupported", "workdir_unavailable")

        internal fun decodeInstalled(installed: List<*>?): List<LauncherEntry> {
            if (installed == null) return emptyList()
            require(installed.size <= 1024)
            val desktopIds = mutableSetOf<String>()
            val revisions = mutableSetOf<String>()
            return installed.map { value ->
                val row = value as? Map<*, *> ?: error("DESKTOP_CATALOG_INVALID")
                require(row.keys == installedFields)
                val desktopId = row["id"] as? String ?: error("DESKTOP_CATALOG_INVALID")
                val label = row["name"] as? String ?: error("DESKTOP_CATALOG_INVALID")
                val revision = row["revision"] as? String ?: error("DESKTOP_CATALOG_INVALID")
                val reason = row["disabled_reason"]
                require(desktopId.endsWith(".desktop") && desktopIds.add(desktopId) &&
                    label.isNotBlank() && LinuxStorageIdentity.hex(revision, 64) && revisions.add(revision) &&
                    (reason == null || reason is String && reason in disabledReasons))
                LauncherEntry("desktop-entry:$revision", label, "", reason == null, reason as String?, desktopId)
            }.sortedBy { it.label.lowercase() }
        }

        internal fun positiveDecimal(value: String): Boolean =
            value.matches(Regex("[1-9][0-9]{0,18}")) && value.toLongOrNull() != null
    }
}

/**
 * Host-owned duplicate-click latch. Re-decoding a descriptor or timing out never restores its Start permission.
 * Native still checks the current catalog and lease; this latch grants neither runtime nor remote authority.
 */
class LinuxDesktopV3LaunchAdmission(private val capacity: Int = 256) {
    init { require(capacity > 0) }
    private val spent = linkedMapOf<String, Long>()
    var failure: String? = null
        private set

    @Synchronized fun canClaim(catalog: LinuxDesktopV3Catalog): Boolean =
        catalog.claimKey() !in spent && spent.size < capacity

    @Synchronized fun tryClaim(catalog: LinuxDesktopV3Catalog, runtime: Long, session: String,
        capture: String, revision: String, id: String): Boolean {
        if (!catalog.accepts(runtime, session, capture, revision, id) || catalog.claimKey() in spent) {
            failure = "CAPABILITY_STALE"
            return false
        }
        if (spent.size >= capacity) {
            failure = "STATE_LIMIT"
            return false
        }
        spent[catalog.claimKey()] = runtime
        failure = null
        return true
    }

    /** Call only after the Host has observed nativeDestroy Accepted for this runtime. */
    @Synchronized fun forgetDestroyedRuntime(runtime: Long) {
        require(runtime > 0)
        spent.entries.removeAll { it.value == runtime }
    }
}
