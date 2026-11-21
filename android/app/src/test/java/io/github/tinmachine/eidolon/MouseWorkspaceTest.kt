package io.github.tinmachine.eidolon

import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class MouseWorkspaceTest {
    @Test fun headsetPoseChangesDoNotMoveOrRotateThePointer() {
        val viewer = Pose(Vector3(0f, 1.4f, 0f))
        val workspace = MouseWorkspace(viewer)
        val initialRay = workspace.ray()
        val initialRotation = workspace.cursorPose(initialRay.first).q.copy()

        viewer.t.x = .25f
        viewer.q.y = .3f
        assertEquals(initialRay, workspace.ray())
        assertEquals(initialRotation, workspace.cursorPose(initialRay.first).q)

        workspace.move(40f, -24f)
        val movedRay = workspace.ray()
        assertEquals(initialRay.first, movedRay.first)
        assertNotEquals(initialRay.second, movedRay.second)
    }
}
