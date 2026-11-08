package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LinuxDesktopCatalogTest {
    private fun value()=JSONObject().put("namespace","linux-desktop").put("label","Linux アプリ").put("mode","gui").put("instance_limit",1)
        .put("selection",JSONObject().put("scope","a".repeat(64)).put("revision","b".repeat(64)).put("app_id","gtk3-desktop"))
    @Test fun nativeInspectedSelectionIsRetainedAndStaleClicksCannotLaunch() {
        val catalog=LinuxDesktopCatalog.decode("7",value())
        assertTrue(catalog.accepts("7","b".repeat(64),"gtk3-desktop"))
        assertFalse(catalog.accepts("8","b".repeat(64),"gtk3-desktop"))
        assertFalse(catalog.accepts("7","c".repeat(64),"gtk3-desktop"))
        assertFalse(catalog.accepts("7","b".repeat(64),"terminal"))
        assertEquals("a".repeat(64),catalog.selection().getString("scope"))
        assertFalse(catalog.toString().contains("a".repeat(64)))
    }
    @Test fun authenticationOrUnverifiedArbitraryCatalogCannotGrantGuiCapability() {
        val arbitrary=value().also {it.getJSONObject("selection").put("app_id","shell")}
        val injected=value().also {it.getJSONObject("selection").put("argv","gtk3-demo")}
        for(value in listOf(JSONObject(),value().put("mode","terminal"),value().put("namespace","termux"),
            value().put("instance_limit",2),value().put("instance_limit","1"),arbitrary,injected,
            value().also {it.getJSONObject("selection").put("scope","bad")})) {
            assertThrows(Exception::class.java) {LinuxDesktopCatalog.decode("7",value)}
        }
        assertThrows(IllegalArgumentException::class.java) {LinuxDesktopCatalog.decode("07",value())}
    }
}
