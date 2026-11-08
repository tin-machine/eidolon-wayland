package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ConnectionAdmissionTest {
    /** 通常起動後。Adapterが初期診断SDKとnative Surfaceを所有し、まだアプリは接続していない。 */
    private fun startup() = JSONObject("""{
        "state":"Ready","scene":"3","app":null,"app_sdk_bindings":0,
        "transport":{"state":"Idle","owned_connections":0,"pending_cleanup":[],"desktop_pending":false},
        "shutdown":{"sdk_bindings":1,"requested":false,"complete":false},
        "render":{"mode":"diagnostic","workers":1,"contexts":1,"detached":[],"targets":[
            {"key":{"session":"1","window":"1","scene":"3","generation":"7"},"role":"diagnostic","frames":5}
        ]},
        "panels":[],"window_slots":{"occupied":0,"cleanup_required":false,"entries":[],"unassigned":[]}
    }""")

    private fun recovery(runtime: JSONObject) = runtime.apply {
        getJSONObject("transport").put("state", "CleanupPending").put("desktop_pending", true)
    }

    private fun assertAppBlocked(runtime: JSONObject) {
        assertFalse(ConnectionAdmission.localTargetsRetired(runtime))
        assertFalse(ConnectionAdmission.canChange(runtime, false))
        assertFalse(ConnectionAdmission.canConnectLinux(runtime, false))
        assertFalse(ConnectionAdmission.canSelectRecovery(recovery(runtime), false))
    }

    @Test fun initialDiagnosticAllowsPairingProfileSelectionAndLinuxConnection() {
        val runtime = startup()
        assertEquals(1, runtime.getJSONObject("shutdown").getInt("sdk_bindings"))
        assertEquals(1, runtime.getJSONObject("render").getJSONArray("targets").length())
        assertTrue(ConnectionAdmission.localTargetsRetired(runtime))
        // Termux pairing、Linux設定/pins保存、通常接続先選択が使う同じgate。
        assertTrue(ConnectionAdmission.canChange(runtime, false))
        assertTrue(ConnectionAdmission.canConnectLinux(runtime, false))
        assertFalse(ConnectionAdmission.canSelectRecovery(runtime, false))
    }

    @Test fun diagnosticSdkBeforeAttachAndAfterDetachDoesNotBlockAdmission() {
        for (detached in listOf(false, true)) {
            val runtime = startup()
            val render = runtime.getJSONObject("render")
            val key = render.getJSONArray("targets").getJSONObject(0).getJSONObject("key")
            render.put("targets", JSONArray())
            if (detached) render.getJSONArray("detached").put(key)
            // SDKの解放前でも、nativeが明示的に診断所有と分類したtupleはアプリ回収待ちではない。
            assertEquals(1, runtime.getJSONObject("shutdown").getInt("sdk_bindings"))
            assertTrue(ConnectionAdmission.canChange(runtime, false))
            assertTrue(ConnectionAdmission.canConnectLinux(runtime, false))
            assertTrue(ConnectionAdmission.canSelectRecovery(recovery(runtime), false))
        }
    }

    @Test fun startupWithDurableDesktopPendingAllowsRecoverySelectionAndReconnectButNoConfigurationChange() {
        val runtime = recovery(startup())
        assertTrue(ConnectionAdmission.canSelectRecovery(runtime, false))
        assertTrue(ConnectionAdmission.canConnectLinux(runtime, false))
        assertFalse(ConnectionAdmission.canChange(runtime, false))
        assertTrue(runtime.getJSONObject("transport").getBoolean("desktop_pending"))
        assertEquals(1, runtime.getJSONObject("shutdown").getInt("sdk_bindings"))
    }

    @Test fun oldAppSdkOwnerBlocksEvenAfterNativeTargetAndPanelHaveGone() {
        val runtime = startup().put("app_sdk_bindings", 1).put("app", JSONObject().put("state", "stopped"))
        // 現sceneの診断と、前sceneのアプリSDKが共存。render/panelsの現在値から旧SDKを推測しない。
        runtime.getJSONObject("shutdown").put("sdk_bindings", 2)
        assertAppBlocked(runtime)
    }

    @Test fun fullyRetiredAppCanReconnectWhileRendererRemainsInWindowsMode() {
        val runtime = startup().put("app", JSONObject().put("state", "stopped"))
        runtime.getJSONObject("shutdown").put("sdk_bindings", 0)
        runtime.getJSONObject("render").put("mode", "windows").put("targets", JSONArray())
        assertTrue(ConnectionAdmission.canChange(runtime, false))
        assertTrue(ConnectionAdmission.canConnectLinux(runtime, false))
    }

    @Test fun windowsTargetCannotBeExemptedByDiagnosticSessionAndWindowIds() {
        val runtime = startup()
        val targets = runtime.getJSONObject("render").getJSONArray("targets")
        val window = JSONObject(targets.getJSONObject(0).toString()).put("role", "windows")
        window.getJSONObject("key").put("generation", "8")
        targets.put(window)
        assertAppBlocked(runtime)
    }

    @Test fun appPanelAndEveryBoardRetirementSignalStillBlockAdmission() {
        val changes = listOf<(JSONObject) -> Unit>(
            { it.put("app", JSONObject().put("state", "stopping")) },
            { it.put("app", JSONObject().put("state", "queued")) },
            { it.getJSONArray("panels").put(JSONObject().put("action", "destroy")) },
            { it.getJSONObject("window_slots").put("occupied", 1) },
            { it.getJSONObject("window_slots").put("cleanup_required", true) },
            { it.getJSONObject("window_slots").getJSONArray("entries").put(JSONObject().put("state", "retiring")) },
            { it.getJSONObject("window_slots").getJSONArray("unassigned").put("2") },
        )
        for (change in changes) assertAppBlocked(startup().also(change))
    }

    @Test fun missingInvalidOrUnclassifiedOwnershipFailsClosed() {
        val missing = startup().apply { remove("app_sdk_bindings") }
        assertAppBlocked(missing)
        for (invalid in listOf(JSONObject.NULL, "0", false, -1, 0.0))
            assertAppBlocked(startup().put("app_sdk_bindings", invalid))
        for (role in listOf(JSONObject.NULL, "unknown", "")) {
            val runtime = startup()
            runtime.getJSONObject("render").getJSONArray("targets").getJSONObject(0).put("role", role)
            assertAppBlocked(runtime)
        }
    }

    @Test fun pendingOperationAndLiveTransportKeepTheirExistingGuards() {
        val runtime = startup()
        assertFalse(ConnectionAdmission.canChange(runtime, true))
        assertFalse(ConnectionAdmission.canConnectLinux(runtime, true))
        assertFalse(ConnectionAdmission.canSelectRecovery(recovery(startup()), true))
        for (phase in listOf("Connecting", "NeedCredential", "Ready", "ProbeReady", "DesktopReady")) {
            val connected = startup()
            connected.getJSONObject("transport").put("state", phase)
            assertFalse(ConnectionAdmission.canChange(connected, false))
            assertFalse(ConnectionAdmission.canConnectLinux(connected, false))
            connected.getJSONObject("transport").put("desktop_pending", true)
            assertFalse(ConnectionAdmission.canSelectRecovery(connected, false))
        }
        val owned = recovery(startup())
        owned.getJSONObject("transport").put("owned_connections", 1)
        assertFalse(ConnectionAdmission.canChange(owned, false))
        assertFalse(ConnectionAdmission.canConnectLinux(owned, false))
        assertFalse(ConnectionAdmission.canSelectRecovery(owned, false))
        val journal = startup()
        journal.getJSONObject("transport").getJSONArray("pending_cleanup").put(JSONObject())
        assertFalse(ConnectionAdmission.canChange(journal, false))
        assertFalse(ConnectionAdmission.canSelectRecovery(recovery(journal), false))
    }
}
