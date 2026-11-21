package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class WristLauncherTest {
    @Test fun poseNeedsDwellAndExitBeforeReopening() {
        val visibility = WristVisibility()
        assertFalse(visibility.update(WristVisibility.ENTER, 0, false))
        assertFalse(visibility.update(WristVisibility.ENTER, 249, false))
        assertTrue(visibility.update(WristVisibility.ENTER, 250, false))
        assertTrue(visibility.update(.76f, 500, false))
        assertTrue(visibility.update(WristVisibility.EXIT, 501, false))
        assertTrue(visibility.update(WristVisibility.EXIT, 650, false))
        assertFalse(visibility.update(WristVisibility.EXIT, 651, false))
        visibility.dismiss()
        assertFalse(visibility.update(1f, 1000, false))
        assertFalse(visibility.update(1f, 1500, false))
        assertFalse(visibility.update(0f, 1501, false))
        assertFalse(visibility.update(0f, 1651, false))
        assertFalse(visibility.update(1f, 1751, false))
        assertTrue(visibility.update(1f, 2001, false))
        assertTrue(visibility.update(0f, 2300, true))
        assertFalse(visibility.update(Float.NaN, 2301, true))
    }

    @Test fun releaseCommitsOneItemFromItsOriginalSource() {
        val press = WristPress()
        press.start(1L, WristAction.APPS)
        assertNull(press.release(2L, WristAction.APPS))
        assertTrue(press.held)
        assertNull(press.release(1L, WristAction.RECALL))
        press.start(1L, WristAction.APPS)
        press.clear()
        assertNull(press.release(1L, WristAction.APPS))
        press.start(1L, WristAction.SETTINGS)
        assertEquals(WristAction.SETTINGS, press.release(1L, WristAction.SETTINGS))
        assertNull(press.release(1L, WristAction.SETTINGS))
    }

    @Test fun bothShapesUseTheSameActionOrderAndDeadZones() {
        for (skin in WristSkin.entries) {
            assertNull(WristAction.hit(.5f, .5f, skin))
            assertEquals(WristAction.APPS, WristAction.hit(.5f, .1f, skin))
            assertNull(WristAction.hit(.5f, .9f, skin))
            assertNull(WristAction.hit(.7351f, .1764f, skin))
            assertNull(WristAction.hit(1f, 1f, skin))
        }
    }

    @Test fun termuxPagesNeverCombineDifferentRevisionsOrDuplicateEntries() {
        val pages = TermuxCatalogPages()
        fun app(i: Int) = LauncherEntry("app$i", "App $i", "builtin:terminal", true, null)
        val first = List(8, ::app)
        assertEquals(first, pages.append("original", 0, 10, first))
        assertNull(pages.append("new", 8, 10, listOf(app(8), app(9))))
        assertNull(pages.append("original", 8, 10, listOf(app(0), app(9))))
        assertEquals(List(10, ::app), pages.append("original", 8, 10, listOf(app(8), app(9))))
        pages.reset()
        assertNull(pages.append("original", 8, 10, listOf(app(8), app(9))))
    }
}
