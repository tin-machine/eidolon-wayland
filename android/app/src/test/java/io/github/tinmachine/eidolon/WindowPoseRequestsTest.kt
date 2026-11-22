package io.github.tinmachine.eidolon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WindowPoseRequestsTest {
    @Test fun onlyTheOwningWorkspaceCanCompleteTheMove() {
        val poses = WindowPoseRequests()
        poses.start("42", 3L)
        poses.complete(1L, "42", "STALE")
        assertEquals("pending", poses.result("42"))
        poses.complete(3L, "42", "ACCEPTED")
        assertEquals("ACCEPTED", poses.result("42"))
        assertNull(poses.result("42"))
    }
}
