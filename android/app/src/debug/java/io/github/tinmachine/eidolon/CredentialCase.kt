package io.github.tinmachine.eidolon

import android.content.Context
import android.os.SystemClock
import android.util.Base64
import java.io.File
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermission
import java.security.KeyStore
import org.json.JSONObject

/** productionの暗号文・aliasを破壊せず、専用fixtureで改変/鍵喪失を検査する。 */
object CredentialCase {
    fun run(nonce: String, context: Context, store: DiagnosticsStore) {
        val result = JSONObject().put("case", "credentials").put("nonce", nonce).put("result", "running")
        val alias = "eidolon.fixture.$nonce"
        val base = File(context.noBackupFilesDir, "credential-fixtures").apply { mkdirs() }
        val fixtureDirectory = File(base, nonce)
        var owned = false
        var handle = 0L
        var stage = "production"
        store.publishCheck(result)
        try {
            val production = CredentialStore(context)
            val first = production.ensureIdentity()
            val productionFile = File(context.noBackupFilesDir, "credentials/identity.json")
            val productionBefore = productionFile.readBytes()
            val second = production.ensureIdentity()
            check(first.publicKey == second.publicKey && first.installation == second.installation)
            check(productionBefore.contentEquals(productionFile.readBytes()))
            val publicInfo = JSONObject(production.publicFile.readText())
            check(publicInfo.keys().asSequence().toSet() == setOf("schema", "installation", "public_key"))
            check(Files.getPosixFilePermissions(productionFile.toPath()) == setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE))
            result.put("idempotent", true).put("public_only", true).put("file_mode_0600", true)
            stage = "fixture"
            check(fixtureDirectory.mkdir()); owned = true
            val fixture = CredentialStore(context, alias, fixtureDirectory)
            val identity = fixture.ensureIdentity()
            fixture.withPrivateKey { check(NativeBridge.nativePublicKey(it) == identity.publicKey) }
            val encoded = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.getKey(alias, null).encoded
            try { check(encoded == null); result.put("aes_nonexportable", true) } finally { encoded?.fill(0) }
            stage = "pin"
            val otherPrivate = NativeBridge.nativeGenerateIdentity()
            val otherPublic = try { NativeBridge.nativePublicKey(otherPrivate) } finally { otherPrivate.fill(0) }
            check(DebugNativeBridge.nativeVerifyPin(identity.publicKey, identity.publicKey) == "ACCEPTED")
            check(DebugNativeBridge.nativeVerifyPin(null, identity.publicKey) == "HOST_KEY_UNKNOWN")
            check(DebugNativeBridge.nativeVerifyPin(otherPublic, identity.publicKey) == "HOST_KEY_MISMATCH")
            result.put("known_pin", true).put("unknown_pin_rejected", true).put("mismatch_pin_rejected", true)
            stage = "native_slot"
            handle = NativeBridge.nativeCreate("{\"schema\":1}")
            waitFor { JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime").getString("state") == "Ready" }
            fixture.withPrivateKey { check(NativeBridge.nativeProvideCredential(handle, 1, 1, it) == NativeBridge.STALE) }
            check(NativeBridge.nativeRequestStop(handle) == NativeBridge.ACCEPTED)
            waitFor { NativeBridge.nativeDestroy(handle) == NativeBridge.ACCEPTED }
            fixture.withPrivateKey { check(NativeBridge.nativeProvideCredential(handle, 1, 1, it) == NativeBridge.STALE) }
            handle = 0
            result.put("unsolicited_credential_rejected", true)
            stage = "tamper"
            val encrypted = File(fixtureDirectory, "identity.json")
            val original = encrypted.readBytes()
            val changed = JSONObject(encrypted.readText())
            val bytes = Base64.decode(changed.getString("ciphertext"), Base64.NO_WRAP)
            bytes[bytes.lastIndex] = (bytes.last().toInt() xor 1).toByte()
            changed.put("ciphertext", Base64.encodeToString(bytes, Base64.NO_WRAP))
            encrypted.writeText(changed.toString())
            var tamper = false
            try { fixture.ensureIdentity() } catch (failure: CredentialStore.Failure) { tamper = failure.code == "CREDENTIAL_INVALID" }
            check(tamper); result.put("tamper_rejected", true)
            stage = "save_loss"
            val originalPublic = fixture.publicFile.readBytes()
            check(encrypted.delete() && fixture.publicFile.delete())
            var saveLost = false
            try { fixture.ensureIdentity() } catch (failure: CredentialStore.Failure) { saveLost = failure.code == "KEY_LOST" }
            check(saveLost && !encrypted.exists() && !fixture.publicFile.exists())
            result.put("save_loss_rejected", true)
            fixture.publicFile.writeBytes(originalPublic)
            stage = "key_loss"
            encrypted.writeBytes(original)
            val keys = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            keys.deleteEntry(alias)
            var lost = false
            try { fixture.ensureIdentity() } catch (failure: CredentialStore.Failure) { lost = failure.code == "KEY_LOST" }
            check(lost && !keys.containsAlias(alias)); result.put("key_loss_rejected", true).put("no_silent_regeneration", true)
            check(productionBefore.contentEquals(productionFile.readBytes()) && production.ensureIdentity().publicKey == first.publicKey)
            result.put("production_unchanged", true).put("result", "pass")
        } catch (failure: Exception) {
            result.put("result", "fail").put("stage", stage).put("failure_class", failure.javaClass.simpleName)
        } finally {
            if (handle != 0L) {
                NativeBridge.nativeRequestStop(handle)
                try { waitFor { NativeBridge.nativeDestroy(handle) != NativeBridge.BUSY } } catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            if (owned) {
                try {
                    KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry(alias) }
                    if (!fixtureDirectory.deleteRecursively()) result.put("cleanup_failed", true)
                } catch (_: Exception) { result.put("cleanup_failed", true) }
            }
            store.publishCheck(result)
        }
    }
    private fun waitFor(test: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + 5000
        while (!test()) { check(SystemClock.elapsedRealtime() < deadline); Thread.sleep(2) }
    }
}
