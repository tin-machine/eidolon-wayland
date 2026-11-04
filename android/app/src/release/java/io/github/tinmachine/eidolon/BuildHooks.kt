package io.github.tinmachine.eidolon

import android.content.Intent

/** releaseはADB診断のintentを実行しない。 */
object BuildHooks {
    fun accept(intent: Intent?, adapter: SpatialPanelAdapter) = Unit
}
