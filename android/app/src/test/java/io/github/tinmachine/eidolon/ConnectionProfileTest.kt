package io.github.tinmachine.eidolon

import java.util.Base64
import org.junit.Assert.*
import org.junit.Test

class ConnectionProfileTest {
    private fun key(byte:Byte):String = "ssh-ed25519 " + Base64.getEncoder().encodeToString(
        byteArrayOf(0,0,0,11)+"ssh-ed25519".toByteArray()+byteArrayOf(0,0,0,32)+ByteArray(32){byte})
    @Test fun malformedOrMultilineKeysAndShellSyntaxAreRejected() {
        for(value in listOf("ssh-rsa AAAA","ssh-ed25519 invalid",key(1)+"\n"+key(2),"x".repeat(4097))) {
            assertThrows(IllegalArgumentException::class.java) {ConnectionProfile.parse("u0_a123",value)}
        }
        for(user in listOf("", "u0;id", "user\nother", "u".repeat(65))) {
            assertThrows(IllegalArgumentException::class.java) {ConnectionProfile.parse(user,key(1))}
        }
        assertEquals(ConnectionProfile.parse("u0_a123",key(1)),ConnectionProfile.parse("u0_a123",key(1)+" termux\n"))
        assertTrue(ConnectionProfile.fingerprint(key(1)).matches(Regex("SHA256:[A-Za-z0-9+/]{43}")))
    }
    @Test fun changedPinsRequireConfirmationAndStaleFormsCannotOverwrite() {
        val old=ConnectionProfile.parse("u0_a123",key(1))
        assertThrows(IllegalArgumentException::class.java) {ConnectionProfile.replacement(old,old.token,"u0_a123",key(2),false)}
        assertThrows(IllegalArgumentException::class.java) {ConnectionProfile.replacement(old,"","u0_a123",key(2),true)}
        val next=ConnectionProfile.replacement(old,old.token,"u0_a123",key(2),true)
        assertNotEquals(old.fingerprint,next.fingerprint)
        assertThrows(IllegalArgumentException::class.java) {ConnectionProfile.replacement(next,old.token,"u0_a123",key(1),true)}
    }
}
