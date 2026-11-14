package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LinuxDesktopV3CatalogTest {
    private val fixture = checkNotNull(javaClass.classLoader?.getResourceAsStream("linux-desktop-v3-contract.json"))
        .bufferedReader().use { JSONObject(it.readText()) }
    private val raw = fixture.getJSONArray("records").let { rows ->
        (0 until rows.length()).map(rows::getJSONObject).single { it.getString("id") == "catalog" }.getString("raw")
    }
    private val capture = "c".repeat(64)
    private fun decode(value: String = raw, runtime: Long = 11, session: String = "7", token: String = capture) =
        LinuxDesktopV3Catalog.decode(runtime, session, token, value.toByteArray(Charsets.UTF_8))

    @Test fun fixedGoldenDescriptorHasTwoTypedSelectionsUnderOneLease() {
        val catalog = decode()
        assertEquals(listOf("gtk3-desktop", "mousepad-editor-v1"), catalog.entries.map { it.id })
        assertEquals(listOf("Linux アプリ", "Mousepad"), catalog.entries.map { it.label })
        assertEquals(listOf("builtin:script", "builtin:editor"), catalog.entries.map { it.icon })
        for (id in listOf("gtk3-desktop", "mousepad-editor-v1")) {
            val selection = catalog.selection(id)
            assertEquals(setOf("scope", "revision", "app_id"), selection.keys().asSequence().toSet())
            assertEquals(JSONObject(raw).getString("scope"), selection.getString("scope"))
            assertEquals(JSONObject(raw).getString("revision"), selection.getString("revision"))
            assertEquals(id, selection.getString("app_id"))
        }
        assertEquals("LinuxDesktopV3Catalog(<private>)", catalog.toString())
    }

    @Test fun clicksCheckEveryCapturedIdentityAndNeverTreatWaylandAppIdAsSelection() {
        val catalog = decode()
        assertTrue(catalog.accepts(11, "7", capture, catalog.revision, "mousepad-editor-v1"))
        assertFalse(catalog.accepts(12, "7", capture, catalog.revision, "mousepad-editor-v1"))
        assertFalse(catalog.accepts(11, "8", capture, catalog.revision, "mousepad-editor-v1"))
        assertFalse(catalog.accepts(11, "7", "d".repeat(64), catalog.revision, "mousepad-editor-v1"))
        assertFalse(catalog.accepts(11, "7", capture, "e".repeat(64), "mousepad-editor-v1"))
        for (id in listOf("mousepad", "shell", "terminal", "")) {
            assertFalse(catalog.accepts(11, "7", capture, catalog.revision, id))
            assertThrows(IllegalArgumentException::class.java) { catalog.selection(id) }
        }
    }

    @Test fun descriptorRejectionsIncludeRawDuplicateKeysEntriesAndChangedLabels() {
        val variants = mutableListOf<String>()
        fun change(action: (JSONObject) -> Unit) { variants += JSONObject(raw).also(action).toString() }
        change { it.put("protocol_revision", "eidolon-linux-desktop-v2") }
        change { it.put("instance_limit", 2) }
        change { it.put("instance_limit", "1") }
        change { it.put("terminal", true) }
        change { it.put("arbitrary_exec", true) }
        change { it.put("nonce", "operator-supplied") }
        change { it.put("scope", "A".repeat(64)) }
        change { it.put("revision", "broken") }
        change { it.put("entries", JSONArray().put(it.getJSONArray("entries").get(0))) }
        change { it.getJSONArray("entries").put(1, it.getJSONArray("entries").get(0)) }
        change { it.getJSONArray("entries").getJSONObject(1).put("id", "mousepad") }
        change { it.getJSONArray("entries").getJSONObject(1).put("label", "Shell") }
        change { it.getJSONArray("entries").getJSONObject(1).put("mode", "terminal") }
        change { it.getJSONArray("entries").getJSONObject(1).put("icon", "builtin:game") }
        change { it.getJSONArray("entries").getJSONObject(1).put("argv", JSONArray()) }
        variants += raw.replaceFirst("{", "{\"schema\":1,")
        variants += raw.replaceFirst("\"mousepad-editor-v1\"", "\"mousepad-editor-v1\",\"id\":\"mousepad-editor-v1\"")
        for (invalid in variants) {
            val error = assertThrows(IllegalArgumentException::class.java) { decode(invalid) }
            assertEquals("DESKTOP_CATALOG_INVALID", error.message)
        }
        for (runtime in listOf(0L, -1L)) assertThrows(IllegalArgumentException::class.java) { decode(runtime = runtime) }
        for (session in listOf("", "0", "07", "9223372036854775808"))
            assertThrows(IllegalArgumentException::class.java) { decode(session = session) }
        assertThrows(IllegalArgumentException::class.java) { decode(token = "") }
    }

    @Test fun singleFlightPersistsAcrossDecodeAndTimeoutUntilActualRuntimeDestruction() {
        val admission = LinuxDesktopV3LaunchAdmission()
        val first = decode()
        assertTrue(admission.canClaim(first))
        assertTrue(admission.tryClaim(first, 11, "7", capture, first.revision, "mousepad-editor-v1"))
        // A second UI snapshot and the other fixed ID are still the same one-shot catalog.
        val later = decode()
        assertFalse(admission.canClaim(later))
        assertFalse(admission.tryClaim(later, 11, "7", capture, later.revision, "gtk3-desktop"))
        assertEquals("CAPABILITY_STALE", admission.failure)
        admission.forgetDestroyedRuntime(12)
        assertFalse(admission.canClaim(later))
        admission.forgetDestroyedRuntime(11)
        val newRuntime = decode(runtime = 12)
        assertTrue(admission.tryClaim(newRuntime, 12, "7", capture, newRuntime.revision, "gtk3-desktop"))
    }

    @Test fun staleClickDoesNotConsumeCurrentCatalogAndCapacityNeverEvictsPriorClaims() {
        val admission = LinuxDesktopV3LaunchAdmission(capacity = 1)
        val first = decode()
        assertFalse(admission.tryClaim(first, 12, "7", capture, first.revision, "mousepad-editor-v1"))
        assertTrue(admission.canClaim(first))
        assertTrue(admission.tryClaim(first, 11, "7", capture, first.revision, "mousepad-editor-v1"))
        val second = decode(session = "8")
        assertFalse(admission.tryClaim(second, 11, "8", capture, second.revision, "mousepad-editor-v1"))
        assertEquals("STATE_LIMIT", admission.failure)
        assertFalse(admission.canClaim(first))
    }
}
