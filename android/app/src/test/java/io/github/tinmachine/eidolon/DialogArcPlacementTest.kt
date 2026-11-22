package io.github.tinmachine.eidolon

import com.meta.spatial.core.Vector3
import kotlin.math.hypot
import kotlin.math.sign
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DialogArcPlacementTest {
    @Test fun bothSidesKeepTheParentDistanceAndFaceTheCapturedEye() {
        val parent = Vector3(2f, 1.6f, 1f)
        for (height in listOf(1.0f, parent.y, 2.3f)) {
            val eye = Vector3(2f, height, -1f)
            val parentDistance = hypot(2.0, parent.y.toDouble() - eye.y)
            for (side in listOf(-1, 1)) {
                val pose = requireNotNull(DialogArcPlacement.pose(eye, parent, 1.2, 0.8, side))
                val p = pose.getJSONArray("position_m")
                val q = pose.getJSONArray("rotation_xyzw")
                val dx = p.getDouble(0) - eye.x
                val dy = p.getDouble(1) - eye.y
                val dz = p.getDouble(2) - eye.z
                assertEquals(side.toDouble(), sign(dx), 0.0)
                assertEquals(parentDistance, hypot(hypot(dx, dz), dy), 0.00001)
                assertEquals(parent.y.toDouble(), p.getDouble(1), 0.0)
                val x = q.getDouble(0)
                val y = q.getDouble(1)
                val z = q.getDouble(2)
                val w = q.getDouble(3)
                // Rotating local +Z gives the unit ray from eye to panel center.
                assertEquals(dx / parentDistance, 2 * (x * z + w * y), 0.00001)
                assertEquals(dy / parentDistance, 2 * (y * z - w * x), 0.00001)
                assertEquals(dz / parentDistance, 1 - 2 * (x * x + y * y), 0.00001)
            }
        }
    }

    @Test fun independentWindowKeepsItsSlotAndFacesTheOpeningView() {
        val eye = Vector3(1f, 2f, 1f)
        val position = org.json.JSONArray(listOf(3.0, 1.0, 3.0))
        val pose = requireNotNull(DialogArcPlacement.facing(eye, position))
        assertEquals(position.toString(), pose.getJSONArray("position_m").toString())
        val q = pose.getJSONArray("rotation_xyzw")
        val (x, y, z, w) = (0..3).map { q.getDouble(it) }
        assertEquals(2.0 / 3, 2 * (x * z + w * y), 0.00001)
        assertEquals(-1.0 / 3, 2 * (y * z - w * x), 0.00001)
        assertEquals(2.0 / 3, 1 - 2 * (x * x + y * y), 0.00001)
    }

    @Test fun missingHorizontalDistanceDoesNotOverrideTheExistingPlacement() {
        val eye = Vector3(0f, 1.4f, 0f)
        assertNull(DialogArcPlacement.pose(eye, eye, 1.2, 0.8, 1))
    }
}
