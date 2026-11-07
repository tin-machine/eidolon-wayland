package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ImeEditorStateTest {
    private class Fixture(limit: Int = 4000, accept: Boolean = true) {
        val transactions = mutableListOf<ImeEditTransaction>()
        val invalidated = mutableListOf<Pair<Long, String>>()
        val state = ImeEditorState(
            onTransaction = { if (accept) transactions.add(it); accept },
            onInvalidated = { generation, reason -> invalidated.add(generation to reason) },
            maxUtf8Bytes = limit,
        )
        val token = state.openConnection()
    }

    @Test fun japaneseConversionCommitsOnceAndFinishDoesNotDuplicate() {
        val f = Fixture()
        assertTrue(f.state.setComposingText(f.token, "にほん", 1))
        assertEquals("にほん", f.transactions.single().preedit?.text)
        assertEquals(9, f.transactions.single().preedit?.cursorEndUtf8)
        assertTrue(f.state.commitText(f.token, "日本語", 1))
        assertEquals("日本語", f.transactions.last().commit)
        assertEquals("", f.transactions.last().preedit?.text)
        assertTrue(f.state.finishComposingText(f.token))
        assertEquals(2, f.transactions.size)
        assertEquals("", f.state.snapshot().text)
        assertNull(f.state.textBeforeCursor(f.token, 1))
    }

    @Test fun nestedBatchPreservesEveryCommitAndFinalPreedit() {
        val f = Fixture()
        assertTrue(f.state.beginBatchEdit(f.token))
        assertTrue(f.state.commitText(f.token, "日本", 1))
        assertTrue(f.state.beginBatchEdit(f.token))
        assertTrue(f.state.commitText(f.token, "語", 1))
        assertTrue(f.state.setComposingText(f.token, "です", 1))
        assertTrue(f.state.endBatchEdit(f.token))
        assertTrue(f.transactions.isEmpty())
        assertFalse(f.state.endBatchEdit(f.token))
        assertEquals("日本語", f.transactions.single().commit)
        assertEquals("です", f.transactions.single().preedit?.text)
        assertEquals(1L, f.transactions.single().sequence)
    }

    @Test fun finishKeepsUnsentCompositionButResetAndCloseCancelIt() {
        val f = Fixture()
        assertTrue(f.state.setComposingText(f.token, "漢字", 1))
        assertTrue(f.state.finishComposingText(f.token))
        assertEquals("漢字", f.transactions.last().commit)
        assertTrue(f.state.setComposingText(f.token, "取消", 1))
        val delivered = f.transactions.size
        f.state.closeConnection(f.token)
        assertEquals(delivered, f.transactions.size)
        assertEquals(listOf(f.token to "close"), f.invalidated)
        assertFalse(f.state.commitText(f.token, "古い", 1))
        assertFalse(f.state.metadata().active)
    }

    @Test fun replacementConnectionRejectsOldCallbacksWithoutDamagingNewOne() {
        val f = Fixture()
        f.state.beginBatchEdit(f.token)
        f.state.commitText(f.token, "未配送", 1)
        f.state.reset()
        val replacement = f.state.openConnection()
        assertTrue(replacement > f.token)
        assertFalse(f.state.endBatchEdit(f.token))
        f.state.closeConnection(f.token)
        assertTrue(f.state.isCurrent(replacement))
        assertTrue(f.state.commitText(replacement, "新", 1))
        assertEquals("新", f.transactions.single().commit)
        assertEquals(2L, f.state.metadata().staleOperations)
    }

    @Test fun utf16SelectionAndUtf8CursorDoNotSplitSupplementaryCharacters() {
        val f = Fixture()
        val value = "A𠮷😀e\u0301"
        assertTrue(f.state.setComposingText(f.token, value, 1))
        assertEquals(12, f.transactions.last().preedit?.cursorEndUtf8)
        assertFalse(f.state.setSelection(f.token, 2, 2))
        assertFalse(f.state.setComposingRegion(f.token, 3, 4))
        assertTrue(f.state.setSelection(f.token, 3, 5))
        assertEquals("😀", f.state.selectedText(f.token))
        assertEquals(5, f.transactions.last().preedit?.cursorBeginUtf8)
        assertEquals(9, f.transactions.last().preedit?.cursorEndUtf8)
        assertEquals("𠮷", f.state.textBeforeCursor(f.token, 2))
    }

    @Test fun deletionPreservesSelectionAndRemapsComposition() {
        val f = Fixture()
        assertTrue(f.state.setComposingText(f.token, "a😀日b", 1))
        assertTrue(f.state.setSelection(f.token, 3, 4))
        assertFalse(f.state.deleteSurroundingText(f.token, 1, 0))
        assertEquals("a😀日b", f.state.snapshot().text)
        assertTrue(f.state.deleteSurroundingText(f.token, 1, 1, codePoints = true))
        val result = f.state.snapshot()
        assertEquals("a日", result.text)
        assertEquals(1, result.selectionStart)
        assertEquals(2, result.selectionEnd)
        assertEquals(0, result.composingStart)
        assertEquals(2, result.composingEnd)
        assertFalse(f.state.deleteSurroundingText(f.token, 3, 0, codePoints = true))
        assertEquals("surrounding_unavailable", f.state.metadata().lastFailure)
    }

    @Test fun combiningMarksAreCodePointsNotInventedGraphemeBoundaries() {
        val f = Fixture()
        assertTrue(f.state.setComposingText(f.token, "e\u0301", 1))
        assertTrue(f.state.deleteSurroundingText(f.token, 1, 0, codePoints = true))
        assertEquals("e", f.state.snapshot().text)
        assertTrue(f.state.finishComposingText(f.token))
        assertEquals("e", f.transactions.last().commit)
    }

    @Test fun invalidTextAndOversizeNeverPartiallyUpdateOrDispatch() {
        val f = Fixture(limit = 9)
        assertTrue(f.state.setComposingText(f.token, "日本", 1))
        val initial = f.transactions.size
        for (value in listOf("\uD800", "\uDC00", "a\u0000b", "日本語文")) {
            assertFalse(f.state.setComposingText(f.token, value, 1))
            assertEquals("日本", f.state.snapshot().text)
            assertEquals(initial, f.transactions.size)
        }
        assertTrue(f.state.beginBatchEdit(f.token))
        assertTrue(f.state.commitText(f.token, "日本", 1))
        assertFalse(f.state.commitText(f.token, "語文", 1))
        assertTrue(f.state.commitText(f.token, "語", 1))
        assertFalse(f.state.endBatchEdit(f.token))
        assertEquals("日本語", f.transactions.last().commit)
    }

    @Test fun defaultProfileMatchesNative4000Utf8BytesRatherThanUtf16Characters() {
        val f = Fixture()
        assertTrue(f.state.commitText(f.token, "a".repeat(4000), 1))
        assertFalse(f.state.commitText(f.token, "a".repeat(4001), 1))
        assertFalse(f.state.commitText(f.token, "日".repeat(1334), 1))
        assertTrue(f.state.commitText(f.token, "😀".repeat(1000), 1))
        assertFalse(f.state.commitText(f.token, "😀".repeat(1001), 1))
        assertEquals(2, f.transactions.size)
    }

    @Test fun sinkRejectionInvalidatesAndCannotBeAutomaticallyRetried() {
        val f = Fixture(accept = false)
        assertFalse(f.state.commitText(f.token, "日本", 1))
        assertFalse(f.state.metadata().active)
        assertEquals("sink_rejected", f.state.metadata().lastFailure)
        assertEquals(listOf(f.token to "sink_rejected"), f.invalidated)
        assertFalse(f.state.commitText(f.token, "日本", 1))
        assertEquals(0L, f.state.metadata().acceptedTransactions)
        assertTrue(f.transactions.isEmpty())
    }

    @Test fun sinkCannotMutateAReplacementConnectionAfterSynchronousReset() {
        lateinit var state: ImeEditorState
        var replacement = 0L
        state = ImeEditorState(onTransaction = {
            state.reset()
            replacement = state.openConnection()
            true
        })
        val old = state.openConnection()
        assertFalse(state.commitText(old, "old", 1))
        assertTrue(state.isCurrent(replacement))
        assertEquals(0L, state.metadata().sequence)
        assertEquals("", state.snapshot().text)
    }

    @Test fun committedCursorAndUnavailableSurroundingAreExplicitlyUnsupported() {
        val f = Fixture()
        assertTrue(f.state.setComposingText(f.token, "日本", 1))
        assertFalse(f.state.commitText(f.token, "日本語", 0))
        assertEquals("日本", f.state.snapshot().text)
        assertTrue(f.state.setSelection(f.token, 1, 1))
        assertFalse(f.state.finishComposingText(f.token))
        assertFalse(f.state.canForwardKey(f.token))
        assertNull(f.state.textBeforeCursor(f.token, 2))
        assertNull(f.state.textAfterCursor(f.token, 2))
    }

    @Test fun batchLimitAndDiagnosticStringsDoNotLeakText() {
        val f = Fixture()
        repeat(32) { assertTrue(f.state.beginBatchEdit(f.token)) }
        assertFalse(f.state.beginBatchEdit(f.token))
        assertTrue(f.state.setComposingText(f.token, "秘密", 1))
        repeat(31) { assertTrue(f.state.endBatchEdit(f.token)) }
        assertFalse(f.state.endBatchEdit(f.token))
        assertFalse(f.transactions.single().toString().contains("秘密"))
        assertFalse(f.transactions.single().preedit.toString().contains("秘密"))
        assertFalse(f.state.snapshot().toString().contains("秘密"))
        assertFalse(f.state.metadata().toString().contains("秘密"))
    }
}
