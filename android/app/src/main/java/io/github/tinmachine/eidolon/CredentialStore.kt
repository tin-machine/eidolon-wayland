package io.github.tinmachine.eidolon

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.KeyStore
import java.security.GeneralSecurityException
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONObject

/** 秘密の平文はByteArrayで短時間だけ持つ。暗号文・公開情報を別fileへ保存する。workerから呼ぶ。 */
class CredentialStore(context: Context,
    private val alias: String = "eidolon.identity.aes.v1",
    private val directory: File = File(context.noBackupFilesDir, "credentials")) {
    class Failure(val code: String) : Exception(code)
    class Identity(val installation: String, val publicKey: String)
    private val encrypted = File(directory, "identity.json")
    val publicFile = File(directory, "public.json")

    @Synchronized fun ensureIdentity(): Identity = guarded {
        checkDirectory()
        if (!encrypted.exists()) {
            // 公開済み鍵を、秘密保存の消失時に黙って別鍵へ置き換えない。
            val aliases = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (publicFile.exists() || aliases.containsAlias(alias)) throw Failure("KEY_LOST")
            val installation = UUID.randomUUID().toString().replace("-", "")
            val plaintext = NativeBridge.nativeGenerateIdentity()
            try {
                val publicKey = NativeBridge.nativePublicKey(plaintext)
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.ENCRYPT_MODE, encryptionKey(create = true))
                cipher.updateAAD(aad(installation))
                val ciphertext = cipher.doFinal(plaintext)
                check(cipher.iv.size == 12)
                val envelope = JSONObject().put("schema", 1).put("installation", installation)
                    .put("iv", encode(cipher.iv)).put("ciphertext", encode(ciphertext))
                atomicWrite(encrypted, envelope.toString().toByteArray(Charsets.UTF_8))
                return@guarded publish(Identity(installation, publicKey))
            } finally { plaintext.fill(0) }
        }
        val envelope = envelope()
        val plaintext = decrypt(envelope)
        try { publish(Identity(envelope.getString("installation"), NativeBridge.nativePublicKey(plaintext))) }
        finally { plaintext.fill(0) }
    }

    /** 接続側はこのcallbackの外へ平文を保持せず、JNI受付後に元bufferを消去する。 */
    @Synchronized fun <T> withPrivateKey(action: (ByteArray) -> T): T = guarded {
        val plaintext = decrypt(envelope())
        try { action(plaintext) } finally { plaintext.fill(0) }
    }
    fun provide(handle: Long, session: Long, request: Long): Int = withPrivateKey {
        NativeBridge.nativeProvideCredential(handle, session, request, it)
    }
    fun profile(identity: Identity): JSONObject? = guarded {
        val file = File(directory, "profile.json")
        if (!file.exists()) return@guarded null
        if (!file.isFile || file.length() !in 1..16384) throw Failure("CONFIG_INVALID")
        val value = JSONObject(file.readText(Charsets.UTF_8))
        if (value.keys().asSequence().toSet() != setOf("schema", "profile_id", "installation", "username", "host_key") ||
            value.get("schema") != 1 || value.getString("profile_id") != "termux-local" ||
            value.getString("installation") != identity.installation) throw Failure("CONFIG_INVALID")
        value
    }
    private fun publish(identity: Identity): Identity {
        val value = JSONObject().put("schema", 1).put("installation", identity.installation)
            .put("public_key", identity.publicKey).toString().toByteArray(Charsets.UTF_8)
        if (!publicFile.exists() || !publicFile.readBytes().contentEquals(value)) atomicWrite(publicFile, value)
        return identity
    }
    private fun envelope(): JSONObject {
        if (!encrypted.isFile || encrypted.length() !in 1..65536) throw Failure("KEY_LOST")
        val value = JSONObject(encrypted.readText(Charsets.UTF_8))
        if (value.keys().asSequence().toSet() != setOf("schema", "installation", "iv", "ciphertext") || value.get("schema") != 1 ||
            listOf("installation", "iv", "ciphertext").any { value.get(it) !is String } ||
            !value.getString("installation").matches(Regex("[0-9a-f]{32}"))) throw Failure("CREDENTIAL_INVALID")
        return value
    }
    private fun decrypt(envelope: JSONObject): ByteArray {
        val iv = Base64.decode(envelope.getString("iv"), Base64.NO_WRAP)
        val ciphertext = Base64.decode(envelope.getString("ciphertext"), Base64.NO_WRAP)
        if (iv.size != 12 || ciphertext.size !in 17..16400) throw Failure("CREDENTIAL_INVALID")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, encryptionKey(create = false), GCMParameterSpec(128, iv))
        cipher.updateAAD(aad(envelope.getString("installation")))
        val result = cipher.doFinal(ciphertext)
        if (result.size !in 1..16384) { result.fill(0); throw Failure("CREDENTIAL_INVALID") }
        return result
    }
    private fun encryptionKey(create: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getKey(alias, null)
        if (existing != null) return existing as? SecretKey ?: throw Failure("KEY_LOST")
        if (!create) throw Failure("KEY_LOST")
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true).setUserAuthenticationRequired(false).build())
        return generator.generateKey()
    }
    private fun checkDirectory() { if (!directory.isDirectory && !directory.mkdirs()) throw Failure("CREDENTIAL_IO") }
    private fun atomicWrite(destination: File, bytes: ByteArray) {
        val temporary = File(directory, destination.name + ".tmp")
        FileOutputStream(temporary).use { it.write(bytes); it.fd.sync() }
        check(temporary.setReadable(false, false) && temporary.setWritable(false, false) &&
            temporary.setReadable(true, true) && temporary.setWritable(true, true))
        Files.move(temporary.toPath(), destination.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }
    private fun aad(installation: String): ByteArray = "eidolon/identity/v1/$installation".toByteArray(Charsets.UTF_8)
    private fun encode(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)
    private inline fun <T> guarded(action: () -> T): T {
        try { return action() }
        catch (failure: Failure) { throw failure }
        catch (_: GeneralSecurityException) { throw Failure("CREDENTIAL_INVALID") }
        catch (_: Exception) { throw Failure("CREDENTIAL_IO") }
    }
}
