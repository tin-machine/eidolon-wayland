package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class LinuxCredentialCaptureTest {
    private val installation="1".repeat(32)
    private val key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    private val profile=LinuxConnectionProfile.propose(LinuxConnectionDraft("Desktop","192.0.2.10","22","linux-user",key),installation,null)
    private fun envelope(changes:Map<String,Any?> = emptyMap())=mapOf("schema" to 1,"code" to 0,"scope" to
        (mapOf("session" to "3","request_id" to "2","profile_id" to "remote-linux","revision" to profile.revision,"binding" to profile.binding())+changes))
    @Test fun supplyIsBoundToCapturedProfileRuntimeRequestAndSessionAndIsOneShot() {
        val capture=LinuxCredentialCapture(1,"2",profile)
        val supply=capture.claim(1,"3","2","remote-linux",envelope())
        assertEquals(3L,supply.session);assertEquals(2L,supply.request);assertEquals(profile.binding(),supply.binding)
        assertThrows(IllegalArgumentException::class.java) {capture.claim(1,"3","2","remote-linux",envelope())}
        assertFalse(capture.toString().contains(profile.host));assertFalse(supply.toString().contains(profile.binding()))
    }
    @Test fun changedProfilePinRevisionRequestAndRuntimeCannotReceiveTheKey() {
        for(change in listOf(mapOf("binding" to "a".repeat(64)),mapOf("revision" to "2"),mapOf("session" to "4"),
            mapOf("request_id" to "4"),mapOf("profile_id" to "linux-probe"),mapOf("extra" to "field"))) {
            val capture=LinuxCredentialCapture(1,"2",profile)
            assertThrows(IllegalArgumentException::class.java) {capture.claim(1,"3","2","remote-linux",envelope(change))}
            assertThrows(IllegalArgumentException::class.java) {capture.claim(1,"3","2","remote-linux",envelope())}
        }
        for(event in listOf(listOf(2L,"3","2","remote-linux"),listOf(1L,"3","4","remote-linux"),listOf(1L,"3","2","termux-local"))) {
            assertThrows(IllegalArgumentException::class.java) {
                LinuxCredentialCapture(1,"2",profile).claim(event[0] as Long,event[1],event[2],event[3],envelope())
            }
        }
    }
    @Test fun cancellationAndProbeProfilesCannotSupplyNormalCredentials() {
        val capture=LinuxCredentialCapture(1,"2",profile);capture.close()
        assertThrows(IllegalArgumentException::class.java) {capture.claim(1,"3","2","remote-linux",envelope())}
        val probe=ConnectionProfileV2.parse(profile.toFields()+("profile_id" to "linux-probe"),installation)
        assertThrows(IllegalArgumentException::class.java) {LinuxCredentialCapture(1,"2",probe)}
        assertThrows(IllegalArgumentException::class.java) {LinuxCredentialCapture(1,"02",profile)}
    }
}
