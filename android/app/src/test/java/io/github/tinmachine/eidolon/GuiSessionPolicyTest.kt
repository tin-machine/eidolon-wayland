package io.github.tinmachine.eidolon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class GuiSessionPolicyTest {
    @Test fun absentAndCorruptPreferencesUseOneHour() {
        for (value in listOf(null, "60", 60L, 0, -5, 1441)) {
            assertEquals(3600, GuiSessionPolicy.fromSaved(value).seconds)
        }
    }

    @Test fun minutesAndWireBounds() {
        for ((input, seconds) in listOf("1" to 60, " 7 " to 420, "60" to 3600, "1440" to 86400)) {
            assertEquals(seconds, GuiSessionPolicy.parse(input)?.seconds)
        }
        for (input in listOf("", " ", "0", "-1", "+1", "2.5", "1441", "2147483648")) {
            assertNull(input, GuiSessionPolicy.parse(input))
        }
    }

    @Test fun saveOnlyPublishesAfterSuccessAndExistingLaunchRetainsItsSnapshot() {
        var stored: Int? = null
        var succeed = false
        val store = GuiSessionPolicyStore(stored) {
            if (succeed) stored = it
            succeed
        }
        val firstLaunch = store.current
        assertEquals(GuiSessionPolicyStore.SaveResult.InvalidInput, store.save("0"))
        assertEquals(GuiSessionPolicyStore.SaveResult.WriteFailed, store.save("7"))
        assertEquals(firstLaunch, store.current)
        succeed = true
        assertEquals(GuiSessionPolicyStore.SaveResult.Saved, store.save("7"))
        assertEquals(420, store.current.seconds)
        assertEquals(3600, firstLaunch.seconds)
        assertEquals(store.current, GuiSessionPolicyStore(stored) { false }.current)
    }
}
