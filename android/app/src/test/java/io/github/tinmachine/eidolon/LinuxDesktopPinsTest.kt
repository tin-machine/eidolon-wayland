package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class LinuxDesktopPinsTest {
    private val installation="1".repeat(32)
    private val key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    private fun profile(host:String="192.0.2.10",previous:ConnectionProfileV2.Value?=null)=
        LinuxConnectionProfile.propose(LinuxConnectionDraft("Linux desktop",host,"22","linux-user",key),installation,previous)
    private fun pins(hash:String="a".repeat(64))="""{"schema":1,"backend":"linux_desktop","base_bundle_sha256":"$hash","desktop_bundle_sha256":"$hash","waypipe_sha256":"$hash","shm_fixture_sha256":"$hash","desktop_binary_sha256":"$hash"}"""
    private class Memory : LinuxProfileRepository.Storage {
        var bytes:ByteArray?=null
        var writes=0
        var failAfter=false
        override fun read()=bytes?.copyOf()
        override fun publish(expected:ByteArray?,replacement:ByteArray) {
            check(if(expected==null) bytes==null else bytes?.contentEquals(expected)==true)
            bytes=replacement.copyOf();writes++
            check(!failAfter) {"READBACK_FAILED"}
        }
    }
    @Test fun builderPinsAreStrictAndNeverAcceptProfileOrCommandInjection() {
        val raw=pins()
        assertEquals(raw,LinuxDesktopPins.parse(raw).json())
        for(value in listOf(raw.replace("\"schema\":1","\"schema\":1,\"schema\":1"),
            raw.replace("\"schema\":1","\"schema\":1.0"),raw.replace("\"schema\":1","\"schema\":\"1\""),
            raw.replace("linux_desktop","linux_probe"),pins("A".repeat(64)),pins("a".repeat(63)),
            raw.dropLast(1)+",\"command\":\"gtk3-demo\"}",raw+"{}"," ".repeat(4097))) {
            assertThrows(IllegalArgumentException::class.java) {LinuxDesktopPins.parse(value)}
        }
        assertFalse(LinuxDesktopPins.parse(raw).toString().contains("a".repeat(64)))
    }
    @Test fun savedPinsRequireConfirmationAndAreBoundToExactProfileRevision() {
        val memory=Memory();val repository=LinuxDesktopPinsRepository(memory);val first=profile()
        assertThrows(IllegalArgumentException::class.java) {repository.save(first,pins(),null,false)}
        assertEquals(0,memory.writes)
        val saved=repository.save(first,pins(),null,true)
        assertNotNull(saved.forProfile(first));assertNull(saved.forProfile(null))
        val changed=profile("192.0.2.11",first)
        assertNull(saved.forProfile(changed))
        assertThrows(IllegalArgumentException::class.java) {repository.save(changed,pins(),null,true)}
        val confirmed=repository.save(changed,pins(),saved.token,true)
        assertNotNull(confirmed.forProfile(changed));assertNull(confirmed.forProfile(first))
        assertFalse(confirmed.toString().contains(changed.binding()))
    }
    @Test fun lateSettingsCannotReplaceNewPinsAndUncertainPublicationRequiresReload() {
        val memory=Memory();val repository=LinuxDesktopPinsRepository(memory);val profile=profile()
        val first=repository.save(profile,pins(),null,true)
        memory.failAfter=true
        assertThrows(IllegalStateException::class.java) {repository.save(profile,pins("b".repeat(64)),first.token,true)}
        memory.failAfter=false
        assertThrows(IllegalArgumentException::class.java) {repository.save(profile,pins(),first.token,true)}
        val after=repository.load()!!
        assertEquals(pins("b".repeat(64)),after.pins.json())
        assertNotEquals(first.token,after.token)
        assertEquals(2,memory.writes)
    }
    @Test fun malformedSavedConfigurationCannotBeTreatedAsAbsent() {
        val memory=Memory();memory.bytes="{\"schema\":1,\"profile_binding\":\"bad\",\"pins\":\"{}\"}".toByteArray()
        val repository=LinuxDesktopPinsRepository(memory)
        assertThrows(IllegalArgumentException::class.java) {repository.load()}
        assertThrows(IllegalArgumentException::class.java) {repository.save(profile(),pins(),null,true)}
        assertEquals(0,memory.writes)
    }
}
