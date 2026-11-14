package io.github.tinmachine.eidolon

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LinuxDesktopV3StatusTest {
    /** Independent native-contract example. Every successful evidence bit is explicit in the fixture. */
    private fun ready() = JSONObject("""{
      "transport":{"session":"7","state":"DesktopReady","desktop_pending":true,
        "desktop_v3_transport":{"protocol_revision":"eidolon-linux-desktop-v3","session":"7",
          "launch_request":"3","transport":{"phase":"started","launch_accepted":true,"journal_pending":true},
          "started_observed":true,"terminal_observed":false,"cleanup_pending":false,"error":null,
          "faults":{"first":null,"cleanup":null}}},
      "desktop_v3_presentation":{"state":"ready","main":"9","startup_completed":true,
        "first_failure":null,"cleanup_pending":false},
      "desktop_v3_sdk_target":{"runtime":"11","target":{"session":"7","window":"9","scene":"2","generation":"4"},
        "create_command":"5","action":"create","acknowledged":true,"binding_present":true,"renderer_frame_seen":true},
      "desktop_v3_finalizer_pending":false,"desktop_v3_cleanup_error":null
    }""")
    private fun report(value: JSONObject) = value.getJSONObject("transport").getJSONObject("desktop_v3_transport")
    private fun presentation(value: JSONObject) = value.getJSONObject("desktop_v3_presentation")
    private fun view(value: JSONObject) = LinuxDesktopV3Status.observe(11, "7", value)

    @Test fun nativeReadyIsDistinctFromAcceptedCatalogAndWaitingStates() {
        assertTrue(view(ready()).ready)
        assertEquals("ready", view(ready()).phase)
        for (phase in listOf("awaiting_main", "awaiting_sdk", "stopping", "pending")) {
            val state = ready().also { presentation(it).put("state", phase) }
            assertEquals(phase, view(state).phase)
            assertFalse(view(state).ready)
        }
        val catalog = ready().also {
            it.remove("desktop_v3_presentation"); it.remove("desktop_v3_sdk_target")
            report(it).getJSONObject("transport").put("phase", "catalog").put("launch_accepted", false)
        }
        assertEquals("catalog_waiting", view(catalog).phase)
        // nativeCommand ACCEPTED alone cannot make any display claim.
        assertEquals("catalog_waiting", view(JSONObject().put("command_code", 0)).phase)
        assertEquals("save_pending", LinuxDesktopV3Status.settings(true).phase)
        assertEquals("configuration_mismatch", LinuxDesktopV3Status.settings(false).phase)
    }

    @Test fun readyRequiresCurrentRuntimeSessionFullTargetAndActualSdkBits() {
        val changes = listOf<(JSONObject) -> Unit>(
            { it.getJSONObject("desktop_v3_sdk_target").put("runtime", "10") },
            { it.getJSONObject("desktop_v3_sdk_target").put("runtime", 11) },
            { it.getJSONObject("desktop_v3_sdk_target").getJSONObject("target").put("session", "6") },
            { it.getJSONObject("desktop_v3_sdk_target").getJSONObject("target").put("window", "8") },
            { it.getJSONObject("desktop_v3_sdk_target").getJSONObject("target").put("scene", "0") },
            { it.getJSONObject("desktop_v3_sdk_target").getJSONObject("target").put("generation", "04") },
            { it.getJSONObject("desktop_v3_sdk_target").put("acknowledged", false) },
            { it.getJSONObject("desktop_v3_sdk_target").put("acknowledged", "true") },
            { it.getJSONObject("desktop_v3_sdk_target").put("binding_present", false) },
            { it.getJSONObject("desktop_v3_sdk_target").put("renderer_frame_seen", false) },
            { it.getJSONObject("desktop_v3_sdk_target").put("action", "destroy") },
            { it.getJSONObject("desktop_v3_sdk_target").put("create_command", JSONObject.NULL) },
            { report(it).put("session", "6") },
            { report(it).put("protocol_revision", "eidolon-linux-desktop-v2") },
            { report(it).put("launch_request", JSONObject.NULL) },
            { report(it).put("started_observed", false) },
            { report(it).put("terminal_observed", true) },
            { report(it).getJSONObject("transport").put("launch_accepted", false) },
            { presentation(it).put("startup_completed", false) },
            { presentation(it).put("cleanup_pending", true) },
            { it.remove("desktop_v3_sdk_target") },
            { it.remove("desktop_v3_presentation") },
        )
        changes.forEachIndexed { index, change -> assertFalse("case $index", view(ready().also(change)).ready) }
    }

    @Test fun firstFailureAndLaterCleanupFailureStaySeparateAndStateLimitIsNotMissingApp() {
        val snapshot = ready().also {
            presentation(it).put("state", "pending").put("first_failure", "STATE_LIMIT")
            report(it).put("cleanup_pending", true)
            report(it).getJSONObject("faults").put("cleanup", "router_drain")
        }
        val status = view(snapshot)
        assertEquals("pending", status.phase)
        assertEquals("STATE_LIMIT", status.firstFailure)
        assertEquals("router_drain", status.cleanupFailure)
        assertEquals("状態・ウィンドウ履歴の上限に達しました。回収後にRuntimeを作り直してください", LinuxDesktopV3Messages.reason(status.firstFailure))
        assertEquals("前回の通信経路の終了確認が残っています", LinuxDesktopV3Messages.reason(status.cleanupFailure))
        assertNotEquals(LinuxDesktopV3Messages.reason("APP_NOT_INSTALLED"), LinuxDesktopV3Messages.reason("STATE_LIMIT"))
        val retained = LinuxDesktopV3Status.observe(11, "7", snapshot, "APP_LAUNCH_TIMEOUT")
        assertEquals("APP_LAUNCH_TIMEOUT", retained.firstFailure)
        assertEquals("router_drain", retained.cleanupFailure)
        assertFalse(LinuxDesktopV3Status.observe(11, "7", ready(), "APP_LAUNCH_TIMEOUT").ready)
        assertFalse(view(ready().put("desktop_v3_cleanup_error", "SDK_DISPOSE_FAILED")).ready)
        assertFalse(LinuxDesktopV3Messages.reason("/private/remote-stderr")!!.contains("/private/remote-stderr"))
        assertFalse(LinuxDesktopV3Messages.settingsReason("secret-token")!!.contains("secret-token"))
        assertEquals("LauncherUi(<private>)", LauncherUi(capture = "private-capture").toString())
    }

    @Test fun activeJournalIsNotMistakenForCleanupAndFinalizerPendingIsNotReady() {
        assertTrue(view(ready()).ready)
        val finalizer = ready().put("desktop_v3_finalizer_pending", true)
        assertEquals("pending", view(finalizer).phase)
        val outerPending = ready().also { it.getJSONObject("transport").put("state", "CleanupPending") }
        assertEquals("pending", view(outerPending).phase)
        val terminal = ready().also { report(it).put("terminal_observed", true) }
        assertEquals("stopping", view(terminal).phase)
    }
}
