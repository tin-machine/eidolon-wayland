package io.github.tinmachine.eidolon

/** The saved backend is explicit. No parser fallback from one revision to the other. */
enum class LinuxDesktopBackend(val protocolRevision: String) {
    V2("eidolon-linux-desktop-v2"), V3("eidolon-linux-desktop-v3");

    companion object {
        internal fun parse(value: Any?): LinuxDesktopBackend = entries.firstOrNull { it.protocolRevision == value }
            ?: throw IllegalArgumentException("SETTINGS_INCONSISTENT")
    }
}

/** Bound local configuration. Its existence or successful readback is not Inspect/Ready evidence. */
class LinuxDesktopV3ConfigRepository internal constructor(private val files: LinuxStorageFiles) {
    class Saved internal constructor(
        val profileBinding: String,
        val storageRecordDigest: String,
        val configuration: LinuxDesktopV3Config,
        val token: String,
    ) {
        fun forProfile(profile: ConnectionProfileV2.Value?, record: LinuxStorageRecord?): LinuxDesktopV3Config? {
            if (profile == null || record == null || profile.binding() != profileBinding ||
                record.sha256() != storageRecordDigest) return null
            return try {
                record.matchesCapture(StorageCapture.from(profile.installation, profile))
                configuration
            } catch (_: IllegalArgumentException) { null }
        }
        override fun toString() = "SavedLinuxDesktopV3Configuration(<private>)"
    }

    internal fun load(): Saved? = files.read(FILE)?.let(::decode)

    internal fun publish(profile: ConnectionProfileV2.Value, record: LinuxStorageRecord,
                         configuration: LinuxDesktopV3Config, expectedToken: String?): Saved {
        val before = files.read(FILE)
        val replacement = encode(profile, record, configuration)
        publishSettingsFile(files, FILE, before, expectedToken, replacement)
        return decode(checkNotNull(files.read(FILE))).also {
            require(it.forProfile(profile, record)?.sha256() == configuration.sha256()) { "SETTINGS_READBACK_INVALID" }
        }
    }

    companion object {
        internal const val FILE = "connection.json"
        internal fun encode(profile: ConnectionProfileV2.Value, record: LinuxStorageRecord,
                            configuration: LinuxDesktopV3Config): ByteArray {
            record.matchesCapture(StorageCapture.from(profile.installation, profile))
            return boundedSettingsBytes(mapOf("schema" to 1, "kind" to "linux-desktop-v3-saved",
                "profile_binding" to profile.binding(), "storage_record_sha256" to record.sha256(),
                "configuration" to LinuxStorageJson.parse(configuration.canonical(), LinuxDesktopV3Config.MAX_BYTES)))
        }

        internal fun decode(bytes: ByteArray): Saved {
            val fields = settingsFields(bytes, setOf("schema", "kind", "profile_binding", "storage_record_sha256", "configuration"))
            require(fields["kind"] == "linux-desktop-v3-saved") { "SETTINGS_INCONSISTENT" }
            val config = LinuxDesktopV3Config.parse(LinuxStorageJson.canonical(fields["configuration"]))
            return Saved(settingsHash(fields["profile_binding"]), settingsHash(fields["storage_record_sha256"]),
                config, LinuxStorageRepository.hash(bytes))
        }
    }
}

/** This is the last publication in one settings attempt, not a multi-file atomic commit. */
class LinuxDesktopActiveBackendRepository internal constructor(private val files: LinuxStorageFiles) {
    class Marker internal constructor(
        val backend: LinuxDesktopBackend,
        val profileBinding: String,
        val configurationDigest: String,
        val storageRecordDigest: String?,
        val token: String,
    ) {
        override fun toString() = "LinuxDesktopActiveBackend(<private>)"
    }

    internal fun load(): Marker? = files.read(FILE)?.let(::decode)
    internal fun publish(backend: LinuxDesktopBackend, profile: ConnectionProfileV2.Value,
                         configurationDigest: String, record: LinuxStorageRecord?, expectedToken: String?): Marker {
        val before = files.read(FILE)
        val replacement = encode(backend, profile, configurationDigest, record)
        publishSettingsFile(files, FILE, before, expectedToken, replacement)
        return decode(checkNotNull(files.read(FILE)))
    }

    companion object {
        internal const val FILE = "active-backend.json"
        internal fun encode(backend: LinuxDesktopBackend, profile: ConnectionProfileV2.Value,
                            configurationDigest: String, record: LinuxStorageRecord?): ByteArray {
            LinuxStorageIdentity.checkedProfile(profile.installation, profile)
            settingsHash(configurationDigest)
            require((backend == LinuxDesktopBackend.V3) == (record != null)) { "SETTINGS_INCONSISTENT" }
            record?.matchesCapture(StorageCapture.from(profile.installation, profile))
            return boundedSettingsBytes(mapOf("schema" to 1, "protocol_revision" to backend.protocolRevision,
                "profile_binding" to profile.binding(), "configuration_sha256" to configurationDigest,
                "storage_record_sha256" to record?.sha256()))
        }
        internal fun decode(bytes: ByteArray): Marker {
            val fields = settingsFields(bytes, setOf("schema", "protocol_revision", "profile_binding",
                "configuration_sha256", "storage_record_sha256"))
            val backend = LinuxDesktopBackend.parse(fields["protocol_revision"])
            val record = fields["storage_record_sha256"]?.let(::settingsHash)
            require((backend == LinuxDesktopBackend.V3) == (record != null)) { "SETTINGS_INCONSISTENT" }
            return Marker(backend, settingsHash(fields["profile_binding"]), settingsHash(fields["configuration_sha256"]),
                record, LinuxStorageRepository.hash(bytes))
        }
    }
}

internal fun settingsHash(value: Any?): String = (value as? String)?.also {
    require(LinuxStorageIdentity.hex(it, 64)) { "SETTINGS_INCONSISTENT" }
} ?: throw IllegalArgumentException("SETTINGS_INCONSISTENT")

internal fun settingsFields(bytes: ByteArray, keys: Set<String>): Map<*, *> {
    val fields = LinuxStorageJson.parse(bytes) as? Map<*, *> ?: error("SETTINGS_INCONSISTENT")
    require(fields.keys == keys && fields["schema"] == 1L) { "SETTINGS_INCONSISTENT" }
    return fields
}

internal fun boundedSettingsBytes(value: Any?): ByteArray = LinuxStorageJson.canonical(value).also {
    require(it.size in 1..LinuxStorageIdentityCodec.MAX_BYTES) { "SETTINGS_TOO_LARGE" }
}

internal fun publishSettingsFile(files: LinuxStorageFiles, name: String, before: ByteArray?,
                                expectedToken: String?, replacement: ByteArray) {
    if (before?.contentEquals(replacement) == true) return
    require(before?.let(LinuxStorageRepository::hash) == expectedToken) { "SETTINGS_STALE" }
    if (!files.exists()) files.create()
    files.publish(name, before, replacement)
    require(files.read(name)?.contentEquals(replacement) == true) { "SETTINGS_READBACK_INVALID" }
}
