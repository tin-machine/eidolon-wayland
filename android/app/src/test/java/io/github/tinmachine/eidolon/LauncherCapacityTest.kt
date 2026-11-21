package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LauncherCapacityTest {
    @Test fun twentiethAppHoldsItsSlotUntilItsOwnerReleasesIt() {
        // Primary Dolphin, independent Termux and eighteen numbered apps, including KWrite.
        val apps = (2..19).toMutableSet()
        fun snapshot() = LauncherCapacity.observe(1, true, true, true, true) { slot ->
            if (slot in apps) LauncherCapacity.Workspace.APP else LauncherCapacity.Workspace.FREE
        }
        val closing = snapshot()
        assertEquals(20, closing.used)
        for (host in LauncherSource.entries) assertNotNull(closing.refusal(host))
        assertNull(closing.nextWorkspace)

        apps.remove(8) // Only after nativeDestroy has accepted that owner's handle.
        val released = snapshot()
        assertEquals(19, released.used)
        assertTrue(9 in apps)
        for (host in LauncherSource.entries) assertNull(released.refusal(host))
        assertEquals(8, released.nextWorkspace)
        apps.add(checkNotNull(released.nextWorkspace))
        assertEquals(closing, snapshot())
    }

    @Test fun reservedBrowsingWorkspaceDoesNotCountAsAnApp() {
        // Termux primary reserves workspace 2 for Ubuntu. Its browsing connection can launch in place.
        val capacity = LauncherCapacity.observe(0, false, false, false, true) { slot ->
            if (slot == 2) LauncherCapacity.Workspace.BROWSING else LauncherCapacity.Workspace.APP
        }
        assertEquals(19, capacity.used)
        assertNull(capacity.nextWorkspace)
        assertNull(capacity.refusal(LauncherSource.UBUNTU))
        assertNotNull(capacity.refusal(LauncherSource.TERMUX))
        val empty = LauncherCapacity.observe(0, false, false, false, false) { LauncherCapacity.Workspace.FREE }
        assertEquals(3, empty.nextWorkspace)
    }

    @Test fun primaryReservationLastsThroughLocalAndRemoteCleanup() {
        val primary = JSONObject()
        val app = JSONObject()
        val transport = JSONObject()
        primary.put("transport", transport)
        assertEquals(0, LauncherCapacity.linuxApps(primary, false))
        assertEquals(1, LauncherCapacity.linuxApps(primary, true))
        primary.put("app", app)
        for (state in listOf("preparing", "starting_remote", "running", "stopping")) {
            app.put("state", state)
            assertEquals(1, LauncherCapacity.linuxApps(primary, false))
        }
        app.put("state", "stopped")
        transport.put("desktop_pending", true)
        assertEquals(1, LauncherCapacity.linuxApps(primary, false))
        transport.put("desktop_pending", false)
        primary.put("desktop_v3_finalizer_pending", true)
        assertEquals(1, LauncherCapacity.linuxApps(primary, false))
        primary.put("desktop_v3_finalizer_pending", false)
        val reclamation = JSONObject()
        primary.put("reclamation", reclamation)
        for (key in listOf("local_cleanup_pending", "remote_cleanup_pending")) {
            reclamation.put(key, true)
            assertEquals(1, LauncherCapacity.linuxApps(primary, false))
            assertTrue(LauncherCapacity.appOccupiesRuntime(primary))
            reclamation.put(key, false)
        }
        assertEquals(0, LauncherCapacity.linuxApps(primary, false))
        primary.remove("app")
        reclamation.put("remote_cleanup_pending", true) // Initial journal scan on a browsing connection.
        assertFalse(LauncherCapacity.appOccupiesRuntime(primary))
    }

    @Test fun dialogsDoNotConsumeOrReleaseAPendingTerminalReservation() {
        val windows = JSONArray("""[{"window":"1"},{"window":"2"}]""")
        assertEquals(1, LauncherCapacity.terminalApps(null, true, null))
        assertEquals(3, LauncherCapacity.terminalApps(windows, true, setOf("1", "2")))
        windows.put(JSONObject("""{"window":"3","parent":"1"}"""))
        assertEquals(3, LauncherCapacity.terminalApps(windows, true, setOf("1", "2")))
        windows.put(JSONObject("""{"window":"4"}"""))
        assertEquals(3, LauncherCapacity.terminalApps(windows, true, setOf("1", "2")))
    }

    @Test fun recoveryCountsActualRetirementAndIgnoresResourcesOfAnActiveApp() {
        val reclamation = JSONObject().put("local_cleanup_pending", true).put("remote_cleanup_pending", true)
        val transport = JSONObject().put("state", "DesktopReady")
        val app = JSONObject().put("state", "running")
        val runtime = JSONObject().put("app", app).put("transport", transport).put("reclamation", reclamation)
        assertFalse(LauncherCapacity.recoveryPending(runtime))
        app.put("state", "stopping")
        assertTrue(LauncherCapacity.recoveryPending(runtime))
        app.put("state", "stopped")
        assertTrue(LauncherCapacity.recoveryPending(runtime))
        reclamation.put("local_cleanup_pending", false).put("remote_cleanup_pending", false)
        assertFalse(LauncherCapacity.recoveryPending(runtime))
        runtime.remove("app")
        transport.put("state", "CleanupPending")
        assertTrue(LauncherCapacity.recoveryPending(runtime))
        transport.put("state", "Recovering")
        assertTrue(LauncherCapacity.recoveryPending(runtime))
        transport.put("state", "DesktopReady")
        assertFalse(LauncherCapacity.recoveryPending(runtime))
    }
}
