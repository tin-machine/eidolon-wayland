package io.github.tinmachine.eidolon

import java.security.MessageDigest
import java.security.SecureRandom

/** v3 storageのnamespace。default/remote-linux credential installationとは別の名目型。 */
data class StorageInstallationId(val value: String) {
    init { require(LinuxStorageIdentity.hex(value, 32)) { "CONTRACT_INVALID" } }
    override fun toString(): String = "StorageInstallationId(<private>)"
    companion object {
        fun generate(random: SecureRandom = SecureRandom()): StorageInstallationId =
            StorageInstallationId(LinuxStorageIdentity.randomId(random))
    }
}

/** host key/userの組に属するstorage ID。revision、address、client鍵から導出しない。 */
data class ProfileStorageId(val value: String) {
    init { require(LinuxStorageIdentity.hex(value, 32)) { "CONTRACT_INVALID" } }
    override fun toString(): String = "ProfileStorageId(<private>)"
    companion object {
        fun generate(random: SecureRandom = SecureRandom()): ProfileStorageId =
            ProfileStorageId(LinuxStorageIdentity.randomId(random))
    }
}

/** B1の4 field capture。通常callerは検査済み既存profileからfromで作る。 */
data class StorageCapture(
    val legacyInstallation: String,
    val profileId: String,
    val profileBinding: String,
    val hostUserBinding: String,
) {
    init {
        require(LinuxStorageIdentity.hex(legacyInstallation, 32) && profileId == LinuxConnectionProfile.ID &&
            LinuxStorageIdentity.hex(profileBinding, 64) && LinuxStorageIdentity.hex(hostUserBinding, 64)) {
            "CONTRACT_INVALID"
        }
    }

    fun toFields(): Map<String, Any> = mapOf(
        "legacy_installation" to legacyInstallation, "profile_id" to profileId,
        "profile_binding" to profileBinding, "host_user_binding" to hostUserBinding,
    )
    fun canonical(): ByteArray = LinuxStorageIdentityCodec.encodeCapture(this)
    fun sha256(): String = LinuxStorageIdentity.sha256(canonical())
    override fun toString(): String = "StorageCapture(<private>)"

    companion object {
        fun from(currentDefaultInstallation: String, profile: ConnectionProfileV2.Value): StorageCapture {
            val checked = LinuxStorageIdentity.checkedProfile(currentDefaultInstallation, profile)
            return StorageCapture(currentDefaultInstallation, checked.profileId, checked.binding(),
                LinuxStorageIdentity.hostUserBinding(checked))
        }
    }
}

/** Android storage schema 1の7 fieldのみ。C1 remote owner recordではない。 */
data class LinuxStorageRecord(
    val legacyInstallation: String,
    val installationId: StorageInstallationId,
    val profileId: String,
    val profileBinding: String,
    val profileStorageId: ProfileStorageId,
    val hostUserBinding: String,
) {
    init { StorageCapture(legacyInstallation, profileId, profileBinding, hostUserBinding) }

    fun toFields(): Map<String, Any> = mapOf(
        "schema" to 1, "legacy_installation" to legacyInstallation,
        "installation_id" to installationId.value, "profile_id" to profileId,
        "profile_binding" to profileBinding, "profile_storage_id" to profileStorageId.value,
        "host_user_binding" to hostUserBinding,
    )
    fun canonical(): ByteArray = LinuxStorageIdentityCodec.encode(this)
    fun sha256(): String = LinuxStorageIdentity.sha256(canonical())
    fun matchesCapture(capture: StorageCapture) {
        require(legacyInstallation == capture.legacyInstallation && profileId == capture.profileId &&
            profileBinding == capture.profileBinding && hostUserBinding == capture.hostUserBinding) { "STORAGE_STALE" }
    }
    override fun toString(): String = "LinuxStorageRecord(<private>)"

    companion object {
        fun from(capture: StorageCapture, installationId: StorageInstallationId,
            profileStorageId: ProfileStorageId): LinuxStorageRecord = LinuxStorageRecord(
            capture.legacyInstallation, installationId, capture.profileId, capture.profileBinding,
            profileStorageId, capture.hostUserBinding,
        )
    }
}

/** 純粋なB1照合。true flagやhash一致だけで実回収・filesystem所有を認定しない。 */
object LinuxStorageIdentity {
    enum class Change(val wire: String) { RETAIN("retain"), REPLACE("replace") }

    fun validateTransition(old: LinuxStorageRecord, new: LinuxStorageRecord, expectedOldSha256: String,
        capture: StorageCapture, explicitSave: Boolean, fullyReclaimed: Boolean): Change {
        require(explicitSave && fullyReclaimed) { "RECOVERY_REQUIRED" }
        require(hex(expectedOldSha256, 64) && old.sha256() == expectedOldSha256) { "STORAGE_STALE" }
        new.matchesCapture(capture)
        require(old.legacyInstallation == new.legacyInstallation && old.installationId == new.installationId &&
            old.profileId == new.profileId) { "STORAGE_STALE" }
        val retained = old.hostUserBinding == new.hostUserBinding
        require((old.profileStorageId == new.profileStorageId) == retained) { "STORAGE_STALE" }
        return if (retained) Change.RETAIN else Change.REPLACE
    }

    fun hostUserInput(profile: ConnectionProfileV2.Value): Map<String, String> {
        val checked = checkedProfile(profile.installation, profile)
        return mapOf("domain" to "eidolon-linux-storage-host-user-v1",
            "host_key_sha256" to sha256(checked.hostKey.toByteArray(Charsets.UTF_8)), "username" to checked.username)
    }
    fun hostUserBinding(profile: ConnectionProfileV2.Value): String =
        sha256(LinuxStorageJson.canonical(hostUserInput(profile)))

    internal fun checkedProfile(installation: String, profile: ConnectionProfileV2.Value): ConnectionProfileV2.Value {
        // Reuse the existing Linux username and canonical host-key validators, without changing v2 wire/binding.
        val checked = ConnectionProfileV2.parse(profile.toFields(), installation)
        require(checked.profileId == LinuxConnectionProfile.ID && checked.backend == ConnectionProfileV2.Backend.LINUX_SSH &&
            checked.toFields() == profile.toFields()) { "LINUX_PROFILE_INVALID" }
        return checked
    }
    internal fun hex(value: String, size: Int): Boolean = value.length == size && value.all { it in "0123456789abcdef" }
    internal fun sha256(bytes: ByteArray): String = hexBytes(MessageDigest.getInstance("SHA-256").digest(bytes))
    internal fun randomId(random: SecureRandom): String = ByteArray(16).let { bytes ->
        random.nextBytes(bytes)
        hexBytes(bytes)
    }
    private fun hexBytes(bytes: ByteArray): String = buildString(bytes.size * 2) {
        for (byte in bytes) {
            val value = byte.toInt() and 255
            append("0123456789abcdef"[value ushr 4]); append("0123456789abcdef"[value and 15])
        }
    }
}
