package io.github.tinmachine.eidolon

import android.content.Intent
import com.meta.spatial.runtime.Scene

/** releaseはADB診断のintentを実行しない。 */
object BuildHooks {
    fun accept(intent: Intent?, adapter: SpatialPanelAdapter, scene: () -> Scene) = Unit
}
