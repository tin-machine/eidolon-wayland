package io.github.tinmachine.eidolon

import java.security.MessageDigest
import java.util.Locale

/** NX18aの保存用契約。ネットワーク、Android、永続fileへ依存しない。 */
object ConnectionProfileV2 {
    private val fields = setOf("schema", "profile_id", "installation", "revision", "display_name",
        "backend", "host", "port", "username", "host_key")
    private val legacyFields = setOf("schema", "profile_id", "installation", "username", "host_key")

    enum class Backend(val wire: String) { TERMUX_LOCAL("termux_local"), LINUX_SSH("linux_ssh") }

    class Value internal constructor(
        val profileId: String, val installation: String, val revision: String, val displayName: String,
        val backend: Backend, val host: String, val port: Int, val username: String, val hostKey: String,
    ) {
        /** 設定専用経路でのみ使う。通常diagnosticsには含めない。 */
        fun toFields(): Map<String, Any> = linkedMapOf("schema" to 2, "profile_id" to profileId,
            "installation" to installation, "revision" to revision, "display_name" to displayName,
            "backend" to backend.wire, "host" to host, "port" to port,
            "username" to username, "host_key" to hostKey)

        fun binding(): String = digest(listOf("eidolon-profile-v2", installation, profileId, revision,
            backend.wire, host, port.toString(), username, hostKey))

        /** Linux未実装をTermuxへfallbackしない。 */
        fun legacyRuntimeFields(): Map<String, Any>? = if (backend == Backend.TERMUX_LOCAL)
            linkedMapOf("schema" to 1, "profile_id" to profileId, "installation" to installation,
                "username" to username, "host_key" to hostKey) else null

        fun replacement(expected: String, nextFields: Map<String, Any?>, confirmed: Boolean): Value {
            require(expected == binding()) { "PROFILE_CHANGED" }
            require(confirmed) { "PIN_CONFIRMATION_REQUIRED" }
            val next = parse(nextFields, installation)
            val current = revision.toLong()
            require(current != Long.MAX_VALUE) { "REVISION_EXHAUSTED" }
            require(next.profileId == profileId && next.revision == (current + 1).toString()) { "PROFILE_INVALID" }
            return next
        }

        override fun toString(): String = "ProfileV2(<private>)"
    }

    /** v2自己申告flagでは作れない移行receipt。設定ownerはatomic write前に生成する。 */
    class LegacyMigration internal constructor(val profile: Value, private val legacyBinding: String) {
        /** 候補に過ぎない。UID/nonce/PID starttime等の通常回収検査を省略しない。 */
        fun legacyCleanupCandidate(current: Value): String? =
            if (profile.toFields() == current.toFields()) legacyBinding else null
        override fun toString(): String = "LegacyTermuxMigration(<private>)"
    }

    /** JSON codec ownerは16KiB上限・duplicate key拒否を別途行う。値型の丸めはしない。 */
    fun parse(value: Map<String, Any?>, installation: String): Value {
        require(value.keys == fields) { "PROFILE_INVALID" }
        fun string(key: String) = value[key] as? String ?: throw IllegalArgumentException("PROFILE_INVALID")
        require(integer(value["schema"]) == 2L && installation.matches(Regex("[0-9a-f]{32}")) &&
            string("installation") == installation) { "PROFILE_INVALID" }
        val id = string("profile_id")
        require(id.matches(Regex("[a-z][a-z0-9-]{0,63}"))) { "PROFILE_INVALID" }
        val revision = string("revision")
        require(revision.matches(Regex("[1-9][0-9]{0,18}")) && revision.toLongOrNull() != null) { "PROFILE_INVALID" }
        val name = string("display_name")
        require(name.isNotBlank() && name.toByteArray(Charsets.UTF_8).size <= 160 &&
            name.none { it.isISOControl() } && validUnicode(name)) { "PROFILE_INVALID" }
        val backend = Backend.entries.firstOrNull { it.wire == string("backend") }
            ?: throw IllegalArgumentException("PROFILE_INVALID")
        val host = canonicalHost(string("host"))
        val port = integer(value["port"])
        require(port != null && port in 1..65535) { "PROFILE_INVALID" }
        val user = string("username")
        require(user.matches(Regex("[A-Za-z0-9_][A-Za-z0-9_-]{0,63}"))) { "PROFILE_INVALID" }
        val rawKey = string("host_key")
        require(rawKey.toByteArray(Charsets.UTF_8).size <= 4096 && validUnicode(rawKey)) { "PROFILE_INVALID" }
        val key = ConnectionProfile.canonicalKey(rawKey)
        when (backend) {
            Backend.TERMUX_LOCAL -> require(id == "termux-local" && host == "127.0.0.1" && port == 8022L &&
                user.matches(Regex("[A-Za-z0-9_]{1,64}"))) { "PROFILE_INVALID" }
            Backend.LINUX_SSH -> require(id != "termux-local") { "PROFILE_INVALID" }
        }
        return Value(id, installation, revision, name, backend, host, port.toInt(), user, key)
    }

    /** 明示操作のみ。v1fileを自動上書きせず、既存runtime経路もそのまま。 */
    fun migrateLegacyTermux(value: Map<String, Any?>, installation: String): LegacyMigration {
        require(value.keys == legacyFields && integer(value["schema"]) == 1L &&
            value["profile_id"] == "termux-local") { "PROFILE_INVALID" }
        val next = value.toMutableMap().apply {
            put("schema", 2); put("revision", "1"); put("display_name", "Quest / Termux")
            put("backend", "termux_local"); put("host", "127.0.0.1"); put("port", 8022)
        }
        val profile = parse(next, installation)
        return LegacyMigration(profile, digest(listOf(profile.installation, profile.profileId, profile.username, profile.hostKey)))
    }

    private fun integer(value: Any?): Long? = when (value) { is Int -> value.toLong(); is Long -> value; else -> null }
    private fun digest(parts: List<String>): String = MessageDigest.getInstance("SHA-256")
        .digest(parts.joinToString("\u0000").toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

    private fun validUnicode(value: String): Boolean {
        var index = 0
        while (index < value.length) {
            val c = value[index++]
            if (c.isHighSurrogate()) {
                if (index == value.length || !value[index++].isLowSurrogate()) return false
            } else if (c.isLowSurrogate()) return false
        }
        return true
    }

    private fun canonicalHost(value: String): String {
        require(value.length in 1..253 && value.all { it.code < 128 }) { "PROFILE_INVALID" }
        if (value.all { it in '0'..'9' || it == '.' }) {
            val parts = value.split('.')
            require(parts.size == 4 && parts.all { part -> part.isNotEmpty() &&
                !(part.length > 1 && part.startsWith('0')) && part.toIntOrNull() in 0..255 }) { "PROFILE_INVALID" }
            return value
        }
        val host = value.lowercase(Locale.ROOT)
        val labels = host.split('.')
        require((labels.size == 1 || labels.last().matches(Regex("[a-z]+"))) &&
            !host.matches(Regex("0x[0-9a-f]+")) && labels.all { label ->
            label.length in 1..63 && !label.startsWith('-') && !label.endsWith('-') &&
                label.all { it in 'a'..'z' || it in '0'..'9' || it == '-' }
        }) { "PROFILE_INVALID" }
        return host
    }
}
