package io.github.tinmachine.eidolon

/** 通常Linux用の入力。UI専用であり、値をdiagnosticsへ渡さない。 */
class LinuxConnectionDraft(
    val displayName: String, val host: String, val port: String,
    val username: String, val hostKey: String,
) {
    override fun toString(): String = "LinuxConnectionDraft(<private>)"
}

/** 初版はTermuxとLinux一台を選択する。debug linux-probeと保存場所・IDを共有しない。 */
object LinuxConnectionProfile {
    const val ID = "remote-linux"

    fun decode(bytes: ByteArray, installation: String): ConnectionProfileV2.Value =
        ConnectionProfileV2Codec.decode(bytes, installation).also {
            require(it.profileId == ID && it.backend == ConnectionProfileV2.Backend.LINUX_SSH) {
                "LINUX_PROFILE_INVALID"
            }
        }

    fun propose(draft: LinuxConnectionDraft, installation: String, previous: ConnectionProfileV2.Value?): ConnectionProfileV2.Value {
        require(previous == null || (previous.profileId == ID && previous.installation == installation &&
            previous.backend == ConnectionProfileV2.Backend.LINUX_SSH)) { "LINUX_PROFILE_INVALID" }
        val oldRevision = previous?.revision?.toLong() ?: 0L
        require(oldRevision < Long.MAX_VALUE) { "REVISION_EXHAUSTED" }
        require(draft.port.matches(Regex("[1-9][0-9]{0,4}"))) { "LINUX_PROFILE_INVALID" }
        return ConnectionProfileV2.parse(linkedMapOf(
            "schema" to 2, "installation" to installation, "profile_id" to ID,
            "revision" to (oldRevision + 1L).toString(), "display_name" to draft.displayName,
            "backend" to "linux_ssh", "host" to draft.host, "port" to draft.port.toInt(),
            "username" to draft.username, "host_key" to draft.hostKey,
        ), installation)
    }

    /** 保存可能なDNS名と、現transportで接続できるIPv4を混同しない。DNS解決はしない。 */
    fun endpointSupport(profile: ConnectionProfileV2.Value): EndpointSupport {
        require(profile.profileId == ID && profile.backend == ConnectionProfileV2.Backend.LINUX_SSH) {
            "LINUX_PROFILE_INVALID"
        }
        val ipv4 = profile.host.split('.').let { parts ->
            parts.size == 4 && parts.all { it.toIntOrNull() in 0..255 && it.all(Char::isDigit) }
        }
        return if (ipv4) EndpointSupport.IPV4 else EndpointSupport.DNS_UNSUPPORTED
    }
    enum class EndpointSupport { IPV4, DNS_UNSUPPORTED }
}

/** I/Oを分離したCAS。保存は接続・pin受入れ・GUI capabilityの付与を一切行わない。 */
class LinuxProfileRepository(private val installation: String, private val storage: Storage) {
    interface Storage {
        fun read(): ByteArray?
        /** same-owner直列化内でexpected bytesを再検査してからatomic publishする。 */
        fun publish(expected: ByteArray?, replacement: ByteArray)
    }

    @Synchronized fun load(): ConnectionProfileV2.Value? = storage.read()?.let {
        LinuxConnectionProfile.decode(it, installation)
    }

    @Synchronized fun save(draft: LinuxConnectionDraft, expectedBinding: String?, confirmed: Boolean): ConnectionProfileV2.Value {
        require(confirmed) { "PIN_CONFIRMATION_REQUIRED" }
        val bytes = storage.read()
        val previous = bytes?.let { LinuxConnectionProfile.decode(it, installation) }
        require(previous?.binding() == expectedBinding) { "PROFILE_CHANGED" }
        val next = LinuxConnectionProfile.propose(draft, installation, previous)
        // 表示名だけでもrevisionが進む。古い認証要求・journalを新設定へ付け替えない。
        if (previous != null) previous.replacement(checkNotNull(expectedBinding), next.toFields(), true)
        val encoded = ConnectionProfileV2Codec.encode(next)
        storage.publish(bytes, encoded)
        val saved = storage.read()?.let { LinuxConnectionProfile.decode(it, installation) }
        require(saved?.toFields() == next.toFields()) { "PROFILE_READBACK_INVALID" }
        return checkNotNull(saved)
    }
}
