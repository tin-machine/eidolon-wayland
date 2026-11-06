package io.github.tinmachine.eidolon

import java.security.MessageDigest
import java.util.Base64

/** 公開情報だけを扱う。UIと保存直前の両方で同じ形式・照合規則を使う。 */
object ConnectionProfile {
    data class Value(val username: String, val hostKey: String) {
        val token: String get() = "$username\n$hostKey"
        val fingerprint: String get() = ConnectionProfile.fingerprint(hostKey)
    }
    fun parse(username: String, hostKey: String): Value {
        require(username.matches(Regex("[A-Za-z0-9_]{1,64}"))) { "USERNAME_INVALID" }
        return Value(username, canonicalKey(hostKey))
    }
    fun canonicalKey(value: String): String {
        val clean = value.trim()
        require(value.length <= 4096 && '\n' !in clean && '\r' !in clean) { "PUBLIC_KEY_INVALID" }
        val fields = clean.split(Regex("\\s+"))
        require(fields.size >= 2 && fields[0] == "ssh-ed25519") { "PUBLIC_KEY_INVALID" }
        val wire = Base64.getDecoder().decode(fields[1])
        val prefix = byteArrayOf(0,0,0,11) + "ssh-ed25519".toByteArray(Charsets.US_ASCII) + byteArrayOf(0,0,0,32)
        require(wire.size == 51 && wire.copyOfRange(0,19).contentEquals(prefix)) { "PUBLIC_KEY_INVALID" }
        return "ssh-ed25519 ${Base64.getEncoder().encodeToString(wire)}"
    }
    fun fingerprint(value: String): String {
        val wire = Base64.getDecoder().decode(canonicalKey(value).split(' ')[1])
        return "SHA256:" + Base64.getEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256").digest(wire))
    }
    fun replacement(previous: Value?, expectedToken: String, username: String, hostKey: String, confirmed: Boolean): Value {
        require((previous?.token ?: "") == expectedToken) { "PROFILE_CHANGED" }
        require(confirmed) { "PIN_CONFIRMATION_REQUIRED" }
        return parse(username, hostKey)
    }
}
