package io.github.tinmachine.eidolon

import com.meta.spatial.core.Pose
import com.meta.spatial.core.Vector3
import org.junit.Assert.*
import org.junit.Test
import kotlin.math.sqrt

class ViewerPanelPlacementTest {
    private fun faces(eye: Vector3, panel: Pose) {
        val delta = panel.t - eye
        val distance = sqrt(delta.x * delta.x + delta.y * delta.y + delta.z * delta.z)
        val q = panel.q
        assertEquals(delta.x / distance, 2 * (q.x * q.z + q.w * q.y), .00001f)
        assertEquals(delta.y / distance, 2 * (q.y * q.z - q.w * q.x), .00001f)
        assertEquals(delta.z / distance, 1 - 2 * (q.x * q.x + q.y * q.y), .00001f)
    }

    @Test fun angledWindowUsesItsOwnDepthAxisAndTheCurrentEyeForFacing() {
        val eye = Vector3(1f, 2f, 3f)
        val start = Vector3(4f, 2f, 7f) // radial unit vector (0.6, 0, 0.8)
        val drag = requireNotNull(ViewerPanelPlacement.Drag.start(start, eye))
        val movedEye = Vector3(1f, 1.7f, 2.8f)
        val raw = Vector3(4.14f, 2.25f, 7.02f) // 10 cm outward and 10 cm to the side
        val panel = requireNotNull(drag.pose(raw, movedEye))
        assertEquals(4.38f, panel.t.x, .00001f)
        assertEquals(2.25f, panel.t.y, .00001f)
        assertEquals(7.34f, panel.t.z, .00001f)
        faces(movedEye, panel)
        for (position in listOf(start, Vector3(3.94f, 2f, 6.92f), Vector3(4.08f, 2.3f, 6.94f))) {
            val next = requireNotNull(drag.pose(position, eye))
            assertEquals(position.x, next.t.x, .00001f)
            assertEquals(position.y, next.t.y, .00001f)
            assertEquals(position.z, next.t.z, .00001f)
            faces(eye, next)
        }
    }

    @Test fun aboveAndBelowTheEyeAreValidButCoincidentOrNonfinitePositionsAreNot() {
        val eye = Vector3(0f, 1.5f, 0f)
        for (y in listOf(0f, 3f)) faces(eye, requireNotNull(ViewerPanelPlacement.facing(eye, Vector3(0f, y, 0f))))
        assertNull(ViewerPanelPlacement.facing(eye, eye))
        assertNull(ViewerPanelPlacement.Drag.start(eye, eye))
        assertNull(ViewerPanelPlacement.facing(eye, Vector3(Float.NaN, 1f, 2f)))
    }
}
