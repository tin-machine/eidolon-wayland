package com.meta.spatial.runtime

import com.meta.spatial.core.Entity
import com.meta.spatial.core.Vector2

class Scene
class SceneObject(private val dispose: () -> Unit = {}) {
    var destroys = 0; private set
    fun destroy() { dispose(); destroys++ }
}
class HitInfo
interface InputListener {
    fun onInput(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity, changed: Int, buttonState: Int, downTime: Long) = false
    fun stopInput(receiver: SceneObject, sourceOfInput: Entity, downTime: Long) {}
    fun onClickDown(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity) {}
    fun onClick(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity) {}
    fun onHoverStart(receiver: SceneObject, sourceOfInput: Entity) {}
    fun onPointerEvent(receiver: SceneObject, hitInfo: HitInfo, type: Int, sourceOfInput: Entity, scrollInfo: Vector2, semanticType: Int) {}
    fun onHoverStop(receiver: SceneObject, sourceOfInput: Entity) {}
    fun onDragMove(receiver: SceneObject, hitInfo: HitInfo, sourceOfInput: Entity, buttonState: Int) {}
}
