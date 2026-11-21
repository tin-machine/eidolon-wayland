package io.github.tinmachine.eidolon

import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.Scene

/** Reads tracked joints and the system's index pinch in the SDK's LOCAL_FLOOR scene. */
internal class WristHandTracking : AutoCloseable {
    data class Hand(val palm: Pose, val aim: Pose?, val pinching: Boolean)
    data class Pair(val left: Hand?, val right: Hand?) {
        fun get(side: WristSide) = if (side.left) left else right
    }

    private var instance = 0L
    private var session = 0L
    private val output = FloatArray(32)
    var usable = false
        private set
    private var failed = false

    @OptIn(com.meta.spatial.core.SpatialSDKExperimentalAPI::class)
    fun sample(scene: Scene, unobstructed: Boolean): Pair {
        if (failed) return Pair(null, null)
        val requestedInstance = scene.getOpenXrInstanceHandle()
        val requestedSession = scene.getOpenXrSessionHandle()
        val dispatch = scene.getOpenXrGetInstanceProcAddrHandle()
        if (requestedInstance == 0L || requestedSession == 0L || dispatch == 0L) return Pair(null, null)
        instance = requestedInstance
        session = requestedSession
        usable = runCatching { NativeBridge.nativeSampleHands(instance, session, dispatch, unobstructed, output) }
            .getOrDefault(false)
        if (!usable) {
            android.util.Log.w("EidolonWrist", "hand_tracking_session_unavailable")
            failed = true
            return Pair(null, null)
        }
        if (output[0] != 1f && output[16] != 1f) return Pair(null, null)
        // The raw joints share LOCAL_FLOOR with MainActivity. View origin can move with locomotion.
        val origin = scene.getViewOrigin()
        val yaw = scene.getViewSceneRotation()
        val half = Math.toRadians(yaw.toDouble()).toFloat() / 2f
        val toScene = Pose(origin, Quaternion(kotlin.math.cos(half), 0f, kotlin.math.sin(half), 0f))
        return Pair(hand(0, toScene), hand(16, toScene))
    }

    private fun hand(offset: Int, toScene: Pose): Hand? {
        if (output[offset] != 1f) return null
        val palm = pose(offset + 1) ?: return null
        val aimState = output[offset + 8]
        val aim = if (aimState != 0f) pose(offset + 9)?.let { toScene * it } else null
        return Hand(toScene * palm, aim, aimState == 2f)
    }

    private fun pose(i: Int): Pose? {
        if ((i until i + 7).any { !output[it].isFinite() }) return null
        val rotation = Quaternion(output[i + 6], output[i + 3], output[i + 4], output[i + 5])
        if (rotation.norm() !in 0.9f..1.1f) return null
        return Pose(Vector3(output[i], output[i + 1], output[i + 2]), rotation.normalize())
    }

    override fun close() {
        if (instance != 0L && session != 0L) runCatching { NativeBridge.nativeCloseHands(instance, session) }
        instance = 0
        session = 0
        usable = false
        failed = false
    }
}
