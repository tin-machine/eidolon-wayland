package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class LinuxGtkRecoveryBudgetTest {
    @Test fun sameCaptureHasThreeAttemptsAndCannotBecomeAnUnrestrictedCommand() {
        val budget=LinuxGtkRecoveryBudget();val nonce="a".repeat(32)
        repeat(3) {assertTrue(budget.claim("linux-gtk-input-recover",nonce))}
        assertFalse(budget.claim("linux-gtk-input-recover",nonce))
        assertTrue(budget.claim("linux-gtk-dialog-recover",nonce))
        for(case in listOf("linux-gtk-input","linux-shm-recover","shell","linux-gtk-resize-recover")) assertFalse(budget.claim(case,nonce))
        assertFalse(budget.claim("linux-gtk-input-recover","A".repeat(32)))
    }
    @Test fun fullHistoryDoesNotEvictAnExhaustedNonceToAllowReplay() {
        val budget=LinuxGtkRecoveryBudget()
        repeat(64) {assertTrue(budget.claim("linux-gtk-input-recover",it.toString(16).padStart(32,'0')))}
        assertFalse(budget.claim("linux-gtk-input-recover","f".repeat(32)))
        repeat(2) {assertTrue(budget.claim("linux-gtk-input-recover","0".repeat(32)))}
        assertFalse(budget.claim("linux-gtk-input-recover","0".repeat(32)))
    }
}
