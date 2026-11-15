package com.meta.spatial.core

class Entity(val id: Long, private val destroyAction: () -> Unit = {}) {
    fun destroy() = destroyAction()
}
class Vector2
class DeleteListenerHolder(val callback: (Long) -> Unit)
class DataModel {
    private val listeners = mutableListOf<DeleteListenerHolder>()
    fun registerDeleteListener(callback: (Long) -> Unit) = DeleteListenerHolder(callback).also { listeners.add(it) }
    fun removeDeleteListener(holder: DeleteListenerHolder) { listeners.remove(holder) }
    fun deleted(id: Long) { listeners.toList().forEach { it.callback(id) } }
}
@RequiresOptIn annotation class SpatialSDKExperimentalAPI
open class SystemBaseWithLateExecute {
    fun getScene(): com.meta.spatial.runtime.Scene = error("fixture never drives SDK system scheduling")
    open fun execute() {}
    open fun lateExecute() {}
    open fun destroy() {}
}
