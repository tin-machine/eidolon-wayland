package io.github.tinmachine.eidolon

import com.meta.spatial.core.Vector3
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

internal object DialogArcPlacement {
    fun pose(
        eye: Vector3,
        parent: Vector3,
        parentWidth: Double,
        dialogWidth: Double,
        side: Int,
    ): JSONObject? {
        if (!parentWidth.isFinite() || !dialogWidth.isFinite() ||
            parentWidth <= 0.0 || dialogWidth <= 0.0 || side !in listOf(-1, 1)
        ) return null
        val dx = (parent.x - eye.x).toDouble()
        val dz = (parent.z - eye.z).toDouble()
        val distance = hypot(dx, dz)
        if (!distance.isFinite() || distance < 0.25) return null
        val separation = atan2(parentWidth / 2, distance) + atan2(dialogWidth / 2, distance)
            + atan2(0.08, distance)
        val yaw = atan2(dx, dz) + side * separation
        val position = listOf(eye.x + distance * sin(yaw), parent.y.toDouble(), eye.z + distance * cos(yaw))
        return facing(eye, JSONArray(position))
    }

    fun facing(eye: Vector3, position: JSONArray): JSONObject? {
        val dx = position.getDouble(0) - eye.x
        val dy = position.getDouble(1) - eye.y
        val dz = position.getDouble(2) - eye.z
        val distance = hypot(dx, dz)
        if (!distance.isFinite() || distance < 0.25) return null
        val yaw = atan2(dx, dz)
        // The forward ray toward the panel center is its local +Z axis.
        // The front of the panel is local -Z and faces the captured eye.
        val pitch = atan2(-dy, distance)
        val sinYaw = sin(yaw / 2)
        val cosYaw = cos(yaw / 2)
        val sinPitch = sin(pitch / 2)
        val cosPitch = cos(pitch / 2)
        val rotation = listOf(
            cosYaw * sinPitch,
            sinYaw * cosPitch,
            -sinYaw * sinPitch,
            cosYaw * cosPitch,
        )
        return JSONObject().put("position_m", position)
            .put("rotation_xyzw", JSONArray(rotation)).takeIf(PanelPoseCodec::valid)
    }
}
