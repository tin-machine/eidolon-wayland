package io.github.tinmachine.eidolon

import org.json.JSONObject

internal data class UbuntuSystemdPin(val path: String, val sha256: String) {
    init {
        require(path.startsWith('/') && path.toByteArray().size <= 1024 &&
            path.none(Char::isISOControl) && path.drop(1).split('/').all { it !in setOf("", ".", "..") })
        require(LinuxStorageIdentity.hex(sha256, 64))
    }

    fun encode() = JSONObject().put("waypipe_path", path).put("waypipe_sha256", sha256)

    companion object {
        fun decode(raw: String): UbuntuSystemdPin {
            val fields = JSONObject(raw)
            require(fields.keys().asSequence().toSet() == setOf("waypipe_path", "waypipe_sha256"))
            return UbuntuSystemdPin(fields.getString("waypipe_path"), fields.getString("waypipe_sha256"))
        }

        fun key(storage: LinuxStorageRecord) = "${storage.hostUserBinding}.${storage.profileStorageId}"
    }
}

internal data class UbuntuSystemdSettingsUi(
    val supported: Boolean = false,
    val pin: UbuntuSystemdPin? = null,
    val storage: String? = null,
    val message: String? = null,
)
