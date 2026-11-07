package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ImeTransactionBridgeTest {
    private val target = ImeNativeTarget("9223372036854775807", "2", "3", "4", 8, 9, 10)
    private fun snapshot(serial: Long = 10, edit: Long = 7, floor: Long? = 0) = ImeBridgeSnapshot(target.copy(serial = serial), edit, floor)
    private fun transaction(generation: Long = 1, sequence: Long = 1, text: String = "日本語") =
        ImeEditTransaction(generation, sequence, text, ImePreedit("", 0, 0))
    private class Fixture {
        val payloads = mutableListOf<ImeNativeTransaction>()
        val receipts = mutableListOf<ImeBridgeReceipt>()
        val invalidated = mutableListOf<ImeBridgeFailure>()
        var response = ImeEnqueueResult.ACCEPTED
        val bridge = ImeTransactionBridge({ payload, receipt ->
            payloads.add(payload); receipts.add(receipt); response
        }, { _, _, reason -> invalidated.add(reason) })
    }

    @Test fun enqueueIsNotDeliveryAndPayloadUsesExactNativeIdEncoding() {
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(transaction(text = "日\"\\\n😀")))
        assertEquals(1L, f.bridge.metadata().enqueued)
        assertEquals(0L, f.bridge.metadata().applied)
        val json = f.payloads.single().toPayloadJson()
        assertTrue(json.contains("\"session\":\"9223372036854775807\""))
        assertTrue(json.contains("\"input_epoch\":8"))
        assertTrue(json.contains("\"serial\":10"))
        assertTrue(json.contains("\"edit_revision\":7"))
        assertTrue(json.contains("\"commit\":\"日\\\"\\\\\\n😀\""))
        assertTrue(json.endsWith("\"delete_before\":0,\"delete_after\":0}"))
        assertTrue(f.bridge.acknowledge(f.receipts.single(), ImeDeliveryResult.APPLIED))
        assertEquals(1L, f.bridge.metadata().applied)
        assertEquals(0, f.bridge.metadata().pendingAcks)
    }

    @Test fun geometrySerialUpdateOnlyAppliesToNewCallbacks() {
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(transaction()))
        val oldPayload = f.payloads.single().toPayloadJson()
        assertTrue(f.bridge.updateSnapshot(snapshot(serial = 11)))
        assertTrue(binding.submit(transaction(sequence = 2, text = "次")))
        assertEquals(oldPayload, f.payloads.first().toPayloadJson())
        assertEquals(10L, f.payloads.first().target.serial)
        assertEquals(11L, f.payloads.last().target.serial)
        assertEquals(listOf(1L, 2L), f.payloads.map { it.sequence })
        // Any native rejection (including an unrecognized serial) must cancel without restamping.
        assertTrue(f.bridge.acknowledge(f.receipts.first(), ImeDeliveryResult.STALE))
        assertFalse(f.bridge.metadata().active)
        assertEquals(2, f.payloads.size) // No restamp/retry was sent.
        assertFalse(binding.submit(transaction(sequence = 3)))
    }

    @Test fun geometryRaceCanBeAcknowledgedWithoutEditingTheQueuedPayload() {
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(transaction()))
        val original = f.payloads.single().toPayloadJson()
        assertTrue(f.bridge.updateSnapshot(snapshot(serial = 11, floor = 1)))
        // Native determines that serial 10..11 changed geometry only, then applies with done(11).
        assertTrue(f.bridge.acknowledge(f.receipts.single(), ImeDeliveryResult.APPLIED))
        assertTrue(f.bridge.metadata().active)
        assertEquals(original, f.payloads.single().toPayloadJson())
        assertTrue(binding.submit(transaction(sequence = 2)))
        assertEquals(7L, f.payloads.last().editRevision)
        assertEquals(11L, f.payloads.last().target.serial)
        assertEquals(2L, f.payloads.last().sequence)
    }

    @Test fun newNativeFloorDoesNotPretendAnAckWasReceived() {
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(transaction()))
        assertTrue(f.bridge.updateSnapshot(snapshot(serial = 11, floor = 50)))
        assertEquals(0L, f.bridge.metadata().applied)
        assertEquals(1, f.bridge.metadata().pendingAcks)
        assertTrue(binding.submit(transaction(sequence = 2)))
        assertEquals(51L, f.payloads.last().sequence)
    }

    @Test fun semanticChangeInvalidatesEvenWithTheSameActivation() {
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertFalse(f.bridge.updateSnapshot(snapshot(serial = 11, edit = 8)))
        assertEquals(ImeBridgeFailure.EDIT_STATE_CHANGED, f.invalidated.single())
        assertFalse(binding.submit(transaction()))
        assertTrue(f.payloads.isEmpty())
    }

    @Test fun everyNativeOwnershipChangeAndMissingTargetInvalidates() {
        for (replacement in listOf(
            target.copy(window = "5"), target.copy(surface = "5"), target.copy(focusToken = "5"),
            target.copy(inputEpoch = 9), target.copy(activation = 10), target.copy(session = "5"),
        )) {
            val f = Fixture()
            val binding = f.bridge.bind(1, snapshot())!!
            assertFalse(f.bridge.updateSnapshot(ImeBridgeSnapshot(replacement, 7, 0)))
            assertFalse(binding.submit(transaction()))
            assertTrue(f.payloads.isEmpty())
            assertEquals(ImeBridgeFailure.TARGET_CHANGED, f.invalidated.single())
        }
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertFalse(f.bridge.updateSnapshot(null))
        assertFalse(binding.submit(transaction()))
        assertEquals(ImeBridgeFailure.TARGET_UNAVAILABLE, f.invalidated.single())
    }

    @Test fun oldBindingCannotTargetANewViewWithTheSameLocalGeneration() {
        val f = Fixture()
        val old = f.bridge.bind(1, snapshot())!!
        assertTrue(old.submit(transaction()))
        val current = f.bridge.bind(1, snapshot())!!
        assertFalse(old.submit(transaction(sequence = 2)))
        old.invalidate()
        assertTrue(f.bridge.metadata().active)
        assertTrue(current.submit(transaction(text = "新")))
        assertEquals(listOf(1L, 2L), f.payloads.map { it.sequence })
        assertFalse(f.bridge.acknowledge(f.receipts.first(), ImeDeliveryResult.STALE))
        assertTrue(f.bridge.metadata().active)
        assertTrue(f.bridge.acknowledge(f.receipts.last(), ImeDeliveryResult.APPLIED))
    }

    @Test fun unknownFloorOrInvalidNativeIdsCannotBeGuessed() {
        val f = Fixture()
        assertNull(f.bridge.bind(1, snapshot(floor = null)))
        assertEquals(ImeBridgeFailure.UNKNOWN_SEQUENCE_FLOOR, f.bridge.metadata().lastFailure)
        for (id in listOf("0", "01", "-1", "9223372036854775808", "1.0")) {
            assertNull(f.bridge.bind(1, ImeBridgeSnapshot(target.copy(session = id), 7, 0)))
        }
        assertNull(f.bridge.bind(1, snapshot(serial = 0x1_0000_0000L)))
        assertNull(f.bridge.bind(1, snapshot(edit = 0)))
        assertTrue(f.payloads.isEmpty())
        val current = f.bridge.bind(1, snapshot(floor = 40))!!
        assertTrue(current.submit(transaction()))
        assertEquals(41L, f.payloads.single().sequence)
    }

    @Test fun nativeLimitsRejectOversizeInvalidUnicodeAndBrokenUtf8Cursor() {
        for (bad in listOf(
            ImeEditTransaction(1, 1, "a".repeat(4001), null),
            ImeEditTransaction(1, 1, "\uD800", null),
            ImeEditTransaction(1, 1, "x\u0000y", null),
            ImeEditTransaction(1, 1, null, ImePreedit("😀", 1, 4)),
            ImeEditTransaction(1, 1, null, ImePreedit("日", -1, 0)),
        )) {
            val f = Fixture()
            val binding = f.bridge.bind(1, snapshot())!!
            assertFalse(binding.submit(bad))
            assertTrue(f.payloads.isEmpty())
            assertEquals(ImeBridgeFailure.INVALID_PAYLOAD, f.invalidated.single())
        }
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(ImeEditTransaction(1, 1, "a".repeat(4000), ImePreedit("😀", -1, -1))))
    }

    @Test fun enqueueRejectionOrExceptionNeverRetriesAndDropsQueuedReceipts() {
        val f = Fixture()
        f.response = ImeEnqueueResult.BUSY
        val binding = f.bridge.bind(1, snapshot())!!
        assertFalse(binding.submit(transaction()))
        assertFalse(binding.submit(transaction()))
        assertEquals(1, f.payloads.size)
        assertEquals(0, f.bridge.metadata().pendingAcks)
        assertFalse(f.bridge.acknowledge(f.receipts.single(), ImeDeliveryResult.APPLIED))
        val thrown = ImeTransactionBridge({ _, _ -> throw IllegalStateException("synthetic") })
        assertFalse(thrown.bind(1, snapshot())!!.submit(transaction()))
        assertFalse(thrown.metadata().active)
    }

    @Test fun queueLimitCancelsWholeBindingAndSequenceExhaustionDoesNotWrap() {
        var calls = 0
        val bridge = ImeTransactionBridge({ _, _ -> calls++; ImeEnqueueResult.ACCEPTED }, maxPendingAcks = 1)
        val binding = bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(transaction()))
        assertFalse(binding.submit(transaction(sequence = 2)))
        assertEquals(1, calls)
        assertEquals(ImeBridgeFailure.QUEUE_FULL, bridge.metadata().lastFailure)
        assertEquals(0, bridge.metadata().pendingAcks)
        val exhausted = bridge.bind(2, snapshot(floor = Long.MAX_VALUE))!!
        assertFalse(exhausted.submit(transaction(generation = 2)))
        assertEquals(ImeBridgeFailure.SEQUENCE_EXHAUSTED, bridge.metadata().lastFailure)
    }

    @Test fun duplicateOrSkippedLocalSequenceCannotReplayText() {
        val f = Fixture()
        val binding = f.bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(transaction()))
        assertFalse(binding.submit(transaction()))
        assertEquals(1, f.payloads.size)
        val newBinding = f.bridge.bind(2, snapshot(floor = 1))!!
        assertFalse(newBinding.submit(transaction(generation = 2, sequence = 2)))
        assertEquals(1, f.payloads.size)
    }

    @Test fun synchronousAckIsRecognizedAndDiagnosticsNeverIncludePayload() {
        lateinit var bridge: ImeTransactionBridge
        var privatePayload: ImeNativeTransaction? = null
        bridge = ImeTransactionBridge({ payload, receipt ->
            privatePayload = payload
            assertTrue(bridge.acknowledge(receipt, ImeDeliveryResult.APPLIED))
            ImeEnqueueResult.ACCEPTED
        })
        val binding = bridge.bind(1, snapshot())!!
        assertTrue(binding.submit(transaction(text = "秘密")))
        assertEquals(1L, bridge.metadata().enqueued)
        assertEquals(1L, bridge.metadata().applied)
        assertFalse(privatePayload.toString().contains("秘密"))
        assertFalse(bridge.metadata().toString().contains("秘密"))
    }

    @Test fun ackRequiresTheReceiptReservedByTheBridgeAndIsAcceptedOnce() {
        val f = Fixture()
        assertTrue(f.bridge.bind(1, snapshot())!!.submit(transaction()))
        val receipt = f.receipts.single()
        val unrelated = ImeBridgeReceipt(receipt.bindingId, receipt.editorGeneration, receipt.sourceSequence, receipt.nativeSequence)
        assertFalse(f.bridge.acknowledge(unrelated, ImeDeliveryResult.APPLIED))
        assertEquals(1, f.bridge.metadata().pendingAcks)
        assertTrue(f.bridge.acknowledge(receipt, ImeDeliveryResult.APPLIED))
        assertFalse(f.bridge.acknowledge(receipt, ImeDeliveryResult.APPLIED))
        assertEquals(1L, f.bridge.metadata().applied)
    }

    @Test fun localModelBatchUsesThePreparedBridgeWithoutLosingMultipleCommits() {
        val f = Fixture()
        var binding: ImeEditorBinding? = null
        val editor = ImeEditorState(onTransaction = { binding!!.submit(it) })
        val generation = editor.openConnection()
        binding = f.bridge.bind(generation, snapshot())
        assertTrue(editor.beginBatchEdit(generation))
        assertTrue(editor.commitText(generation, "日本", 1))
        assertTrue(editor.commitText(generation, "語", 1))
        assertTrue(editor.setComposingText(generation, "です", 1))
        assertFalse(editor.endBatchEdit(generation))
        assertEquals("日本語", f.payloads.single().commit)
        assertEquals("です", f.payloads.single().preedit?.text)
        assertEquals(6, f.payloads.single().preedit?.cursorEndUtf8)
    }
}
