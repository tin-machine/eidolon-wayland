package io.github.tinmachine.eidolon

import android.content.Intent
import android.content.Context
import com.meta.spatial.runtime.Scene

/** releaseはADB診断のintentを実行しない。 */
object BuildHooks {
    fun registerMeshes(activity: com.meta.spatial.toolkit.AppSystemActivity) = Unit
    fun additionalPanels(): List<com.meta.spatial.toolkit.PanelRegistration> = emptyList()
    fun sceneClosing() = Unit
    fun imeProbeActivity(activity: android.app.Activity) = Unit
    fun imeProbeActive(): Boolean = false
    fun imeProbePhysicalKey() = Unit
    fun holdPanelCreation(window: String): Boolean = false
    fun commandComplete(request: String, code: String) = Unit
    fun windowControlRequested(action:String,runtime:Long,target:String,revision:Long,request:String,code:Int) = Unit
    fun accept(intent: Intent?, adapter: SpatialPanelAdapter, context: Context, scene: () -> Scene, recreate: () -> Unit) = Unit
    fun sceneReady(adapter: SpatialPanelAdapter, scene: () -> Scene, recreate: () -> Unit) = Unit
    fun runtimeConfiguration(configuration: String): String = configuration
}
