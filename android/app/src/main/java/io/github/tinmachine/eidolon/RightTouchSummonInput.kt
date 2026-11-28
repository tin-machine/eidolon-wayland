package io.github.tinmachine.eidolon

import android.os.SystemClock
import com.meta.spatial.core.Entity
import com.meta.spatial.core.Query
import com.meta.spatial.core.SpatialSDKExperimentalAPI
import com.meta.spatial.core.Vector3
import com.meta.spatial.runtime.ButtonBits
import com.meta.spatial.runtime.ControllerPose
import com.meta.spatial.runtime.Scene
import com.meta.spatial.toolkit.AvatarAttachment
import com.meta.spatial.toolkit.Controller
import com.meta.spatial.toolkit.ControllerType
import com.meta.spatial.toolkit.Transform
import com.meta.spatial.toolkit.getAbsoluteTransform

/** SceneObject に当たっていない間も、SDK の実右 Touch のボタン状態を利用する。 */
internal class RightTouchSummonInput<T : Any> {
    private val controllers = Query.where { has(Controller.id, AvatarAttachment.id, Transform.id) }
    private val chord = SummonChord<T>()
    private var reservedSource = 0L
    private var consumedSecondary = false
    private var physicalSecondary = false
    private var trackedSource = 0L
    private var inputIntent: Long? = null
    private var buttonState = 0

    @OptIn(SpatialSDKExperimentalAPI::class)
    @Synchronized
    fun sample(scene: Scene, intent: Long, target: (Long) -> T?): SummonChord.Action<T>? {
        selectIntent(intent)
        val right = controllers.eval().filter(::isRightTouch).singleOrNull()
        if (right == null || !tracked(scene) || WristInputOwnership.owns(right)) {
            invalidate()
            return null
        }
        val controller = right.getComponent<Controller>()
        if (!controller.isActive) {
            invalidate()
            return null
        }
        trackedSource = right.id
        val state = controller.buttonState and (ButtonBits.ButtonA or ButtonBits.ButtonB)
        if (state == buttonState || state == 0) return accept(right, state, null, true).action
        val aim = getAbsoluteTransform(right)
        if (WindowMotionPose.fromSdk(aim) == null) {
            invalidate()
            return null
        }
        val hit = scene.lineSegmentIntersect(aim.t, aim * Vector3(0f, 0f, 8f))
        val selected = hit?.entity?.id?.let(target)
        return accept(right, state, selected, hit == null || selected != null).action
    }

    /** 既存の B 単独右クリックは同期的に通す。消費するのは右 Touch の chord だけ。 */
    @Synchronized
    fun applicationInput(source: Entity, changed: Int, state: Int, intent: Long, target: T?): SummonChord.Decision<T> {
        selectIntent(intent)
        if (source.id != trackedSource || !isRightTouch(source) || WristInputOwnership.owns(source) ||
            source.tryGetComponent<Controller>()?.isActive != true
        ) return SummonChord.Decision()
        val secondaryUp = changed and ButtonBits.ButtonB != 0 && state and ButtonBits.ButtonB == 0
        val decision = accept(source, state, target, true)
        if (secondaryUp) consumedSecondary = false
        return decision
    }

    private fun accept(source: Entity, state: Int, hit: T?, allowed: Boolean): SummonChord.Decision<T> {
        buttonState = state and (ButtonBits.ButtonA or ButtonBits.ButtonB)
        val pressedA = state and ButtonBits.ButtonA != 0
        val pressedB = state and ButtonBits.ButtonB != 0
        if (reservedSource != source.id || pressedB && !physicalSecondary) consumedSecondary = false
        if (!allowed) chord.invalidate()
        val decision = chord.sample(source.id, pressedA, pressedB, hit)
        reservedSource = source.id
        physicalSecondary = pressedB
        consumedSecondary = consumedSecondary || decision.consumeSecondary
        return SummonChord.Decision(decision.action.takeIf { allowed }, consumedSecondary)
    }

    @Synchronized
    fun invalidate() {
        trackedSource = 0
        buttonState = 0
        inputIntent = null
        chord.invalidate()
    }

    private fun selectIntent(next: Long) {
        if (inputIntent == next) return
        invalidate()
        inputIntent = next
    }

    companion object {
        @OptIn(SpatialSDKExperimentalAPI::class)
        fun tracked(scene: Scene): Boolean {
            val pose = scene.getControllerPoseAtTime(false, SystemClock.elapsedRealtimeNanos()) ?: return false
            return pose.flags and ControllerPose.TrackedBits == ControllerPose.TrackedBits
        }

        private fun isRightTouch(entity: Entity): Boolean =
            entity.tryGetComponent<AvatarAttachment>()?.type == "right_controller" &&
                entity.tryGetComponent<Controller>()?.type == ControllerType.CONTROLLER
    }
}
