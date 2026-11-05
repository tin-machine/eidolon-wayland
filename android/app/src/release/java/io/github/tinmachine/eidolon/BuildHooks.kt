package io.github.tinmachine.eidolon

import android.content.Intent
import android.content.Context
import com.meta.spatial.runtime.Scene

/** releaseはADB診断のintentを実行しない。 */
object BuildHooks {
    fun commandComplete(request: String, code: String) = Unit
    fun accept(intent: Intent?, adapter: SpatialPanelAdapter, context: Context, scene: () -> Scene, recreate: () -> Unit) = Unit
    fun sceneReady(adapter: SpatialPanelAdapter, scene: () -> Scene, recreate: () -> Unit) = Unit
    fun runtimeConfiguration(configuration: String): String = configuration
}
