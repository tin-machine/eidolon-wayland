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
        val center = Vector3(position.getDouble(0).toFloat(), position.getDouble(1).toFloat(), position.getDouble(2).toFloat())
        return ViewerPanelPlacement.facing(eye, center)?.let(PanelPoseCodec::fromSdk)
    }
}
