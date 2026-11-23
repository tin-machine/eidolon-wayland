package io.github.tinmachine.eidolon

import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.Vector3
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.math.sqrt

/** Panel front is local -Z. Keep the horizon level while looking from the center to the eye. */
internal object ViewerPanelPlacement {
    fun facing(eye: Vector3, center: Vector3): Pose? {
        val x = (center.x - eye.x).toDouble()
        val y = (center.y - eye.y).toDouble()
        val z = (center.z - eye.z).toDouble()
        val horizontal = hypot(x, z)
        if (!hypot(horizontal, y).let { it.isFinite() && it > .001 }) return null
        val yaw = atan2(x, z) / 2
        val pitch = atan2(-y, horizontal) / 2
        return Pose(center, Quaternion((cos(yaw) * cos(pitch)).toFloat(), (cos(yaw) * sin(pitch)).toFloat(),
            (sin(yaw) * cos(pitch)).toFloat(), (-sin(yaw) * sin(pitch)).toFloat()))
    }

    /** A fixed depth axis makes the preview reversible and independent of head motion or frame rate. */
    class Drag private constructor(private val start: Vector3, private val outward: Vector3) {
        fun pose(raw: Vector3, eye: Vector3): Pose? {
            val dx = raw.x - start.x
            val dy = raw.y - start.y
            val dz = raw.z - start.z
            val depth = (dx * outward.x + dy * outward.y + dz * outward.z).coerceAtLeast(0f)
            val extra = 4f * depth // the unmodified position already contains one copy of the depth
            return facing(eye, Vector3(raw.x + extra * outward.x, raw.y + extra * outward.y, raw.z + extra * outward.z))
        }

        companion object {
            fun start(center: Vector3, eye: Vector3): Drag? {
                val delta = center - eye
                val length = sqrt(delta.x * delta.x + delta.y * delta.y + delta.z * delta.z)
                if (!length.isFinite() || length < .001f) return null
                return Drag(center, Vector3(delta.x / length, delta.y / length, delta.z / length))
            }
        }
    }
}
