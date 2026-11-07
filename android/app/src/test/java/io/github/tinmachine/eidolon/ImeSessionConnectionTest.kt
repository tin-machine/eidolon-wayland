package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

class ImeSessionConnectionTest {
    private val target = ImeSdkTarget("1", "2", "3", "4")
    private fun frame(epoch: Long = 2, serial: Long = 7, hasClient: Boolean = true,
        available: Boolean = true, focusTarget: ImeSdkTarget = target): ImeSessionFrame {
        val focus = ImeSessionFocus(10, epoch, focusTarget, "5")
        val snapshot = ImeBridgeSnapshot(ImeNativeTarget("1", "2", "6", "5", epoch, 1, serial), 1, 0)
        return ImeSessionFrame(10, epoch, available, focus, ImeDecodedSnapshot(true, snapshot.takeIf { hasClient }))
    }
    private inner class Harness {
        var now = 0L
        var order = 0L
        var request = 0L
        var nextFrame = frame(0, hasClient = false)
        var rejected = false
        var immediateDelivery = false
        var closed = 0
        val sent = mutableListOf<Pair<String, ImeSessionCommand>>()
        lateinit var connection: ImeSessionConnection
        init {
            connection = ImeSessionConnection(10, target, 0, { (++request).toString() }, { id, command ->
                sent.add(id to command)
                if (immediateDelivery && command is ImeSessionCommand.Transaction)
                    connection.observe(++order, nextFrame, listOf(ImeSessionCompletion(id, ImeDeliveryResult.APPLIED)))
                if (rejected) ImeEnqueueResult.BUSY else ImeEnqueueResult.ACCEPTED
            }, { now }, { closed++ }, 100)
        }
        fun observe(value: ImeSessionFrame = nextFrame, acks: List<ImeSessionCompletion> = emptyList()) {
            nextFrame = value; connection.observe(++order, value, acks)
        }
        fun ack(index: Int, value: ImeSessionFrame = nextFrame, result: ImeDeliveryResult = ImeDeliveryResult.APPLIED) =
            observe(value, listOf(ImeSessionCompletion(sent[index].first, result)))
        fun bind(hasClient: Boolean = true) {
            connection.openEditor(1)
            assertTrue(sent.last().second is ImeSessionCommand.Backend)
            ack(0, frame(1, hasClient = false))
            assertSame(ImeSessionCommand.Reset, sent.last().second)
            ack(1, frame(2, hasClient = false))
            assertTrue(sent.last().second is ImeSessionCommand.Focus)
            ack(2, frame(2, hasClient = hasClient))
        }
        fun payloads() = sent.count { it.second is ImeSessionCommand.Transaction }
    }

    @Test fun startResetsBeforeAnyInputConnectionButNeverFocusesOrDeliversWithoutOne() {
        val h = Harness()
        h.connection.start()
        assertTrue((h.sent.single().second as ImeSessionCommand.Backend).enabled)
        assertNull(h.connection.metadata().editorGeneration)
        assertTrue(h.connection.metadata().rawBlocked)
        h.ack(0, frame(1, hasClient = false))
        assertSame(ImeSessionCommand.Reset, h.sent.last().second)
        h.ack(1, frame(2))
        assertEquals(ImeSessionPhase.NEEDS_FOCUS, h.connection.metadata().phase)
        assertEquals(0, h.connection.metadata().pendingAcks)
        assertFalse(h.connection.acceptsEditor(1))
        assertFalse(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        h.observe(frame(3))
        assertEquals(2, h.sent.size)
        assertEquals(0, h.payloads())
        h.now = 101; h.connection.expire()
        assertEquals(ImeSessionPhase.CLOSING, h.connection.metadata().phase)
        assertSame(ImeSessionCommand.Reset, h.sent[h.sent.lastIndex - 1].second)
        assertFalse((h.sent.last().second as ImeSessionCommand.Backend).enabled)
        h.ack(h.sent.lastIndex - 1, frame(4, hasClient = false))
        assertTrue(h.connection.metadata().rawBlocked)
        h.ack(h.sent.lastIndex, frame(4, hasClient = false))
        assertEquals(ImeSessionPhase.CLOSED, h.connection.metadata().phase)
        assertFalse(h.connection.metadata().rawBlocked)
    }

    @Test fun firstInputConnectionMergesWithPreparationBeforeOrAfterEveryAckAndFreshEpoch() {
        for (arrives in 0..3) {
            val h = Harness()
            h.connection.start()
            if (arrives == 0) h.connection.openEditor(1)
            h.ack(0, frame(1, hasClient = false))
            if (arrives == 1) h.connection.openEditor(1)
            h.ack(1, frame(1, hasClient = false)) // ACKだけではまだfresh epochではない。
            if (arrives == 2) h.connection.openEditor(1)
            assertEquals(2, h.sent.size)
            assertEquals(ImeSessionPhase.NEEDS_FOCUS, h.connection.metadata().phase)
            h.observe(frame(2, hasClient = false))
            if (arrives == 3) {
                assertEquals(2, h.sent.size)
                h.connection.openEditor(1)
            }
            assertEquals(3, h.sent.size)
            assertTrue(h.sent[2].second is ImeSessionCommand.Focus)
            h.ack(2, frame(2))
            assertEquals(ImeSessionPhase.BOUND, h.connection.metadata().phase)
            assertTrue(h.connection.acceptsEditor(1))
            assertEquals(0, h.connection.metadata().pendingAcks)
        }
    }

    @Test fun startIsIdempotentAndLateFirstInputConnectionDoesNotExtendPreparationDeadline() {
        val h = Harness()
        h.connection.start(); h.connection.start()
        assertEquals(1, h.sent.size)
        h.now = 90
        h.connection.openEditor(1); h.connection.start()
        assertEquals(1, h.sent.size)
        h.now = 101; h.connection.expire()
        assertEquals(ImeSessionPhase.CLOSING, h.connection.metadata().phase)
        assertEquals("ack_timeout", h.connection.metadata().failure)
        val count = h.sent.size
        h.connection.start(); h.connection.openEditor(2)
        h.ack(0, frame(2)) // 古いENABLE ACKはcloseを取り消せない。
        assertEquals(count, h.sent.size)
        assertEquals(ImeSessionPhase.CLOSING, h.connection.metadata().phase)
        assertFalse(h.connection.acceptsEditor(1))
    }

    @Test fun failedPreparationDoesNotOpenRawGateOrStartAnEditor() {
        val h = Harness(); h.rejected = true
        h.connection.start()
        assertEquals(ImeSessionPhase.CLOSED, h.connection.metadata().phase)
        assertEquals("cleanup_unconfirmed", h.connection.metadata().failure)
        assertTrue(h.connection.metadata().rawBlocked)
        val count = h.sent.size
        h.connection.openEditor(1); h.connection.start()
        assertEquals(count, h.sent.size)
        assertFalse(h.connection.acceptsEditor(1))
        assertEquals(0, h.payloads())
    }

    @Test fun enqueueAndAppliedAreSeparateAndReceiptExistsBeforeFastCompletion() {
        val h = Harness(); h.bind()
        assertEquals(ImeSessionPhase.BOUND, h.connection.metadata().phase)
        assertTrue(h.connection.acceptsEditor(1))
        assertFalse(h.connection.acceptsEditor(2))
        assertTrue(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        assertEquals(1, h.connection.metadata().pendingAcks)
        assertEquals(0L, h.connection.metadata().applied)
        h.ack(3)
        assertEquals(1L, h.connection.metadata().applied)
        h.immediateDelivery = true
        assertTrue(h.connection.submit(ImeEditTransaction(1, 2, "fixture", null)))
        assertEquals(0, h.connection.metadata().pendingAcks)
        assertEquals(2L, h.connection.metadata().applied)
    }

    @Test fun resetCompletionWithoutFreshEpochCannotBindOrFocus() {
        val h = Harness(); h.connection.openEditor(1)
        h.ack(0, frame(1, hasClient = false))
        h.ack(1, frame(1))
        assertEquals(ImeSessionPhase.NEEDS_FOCUS, h.connection.metadata().phase)
        assertEquals(2, h.sent.size)
        h.observe(frame(2))
        assertTrue(h.sent.last().second is ImeSessionCommand.Focus)
    }

    @Test fun disabledOrNoClientNeverDeliversAndEventuallyTimesOut() {
        val h = Harness()
        assertFalse(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        h.bind(hasClient = false)
        assertEquals(ImeSessionPhase.WAITING_CLIENT, h.connection.metadata().phase)
        assertFalse(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        h.now = 101; h.observe()
        assertEquals("ack_timeout", h.connection.metadata().failure)
        assertEquals(0, h.payloads())
    }

    @Test fun newInputConnectionNeedsNewResetAndOldGenerationCannotSend() {
        val h = Harness(); h.bind()
        assertTrue(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        h.connection.editorInvalidated(1)
        h.connection.openEditor(2)
        val reset = h.sent.lastIndex
        assertSame(ImeSessionCommand.Reset, h.sent[reset].second)
        assertFalse(h.connection.submit(ImeEditTransaction(1, 2, "fixture", null)))
        h.ack(reset, frame(3, hasClient = false))
        h.ack(h.sent.lastIndex, frame(3))
        assertEquals(ImeSessionPhase.BOUND, h.connection.metadata().phase)
        h.ack(3, frame(3)) // Old payload ACK cannot update the new binding.
        assertEquals(0L, h.connection.metadata().applied)
        assertTrue(h.connection.submit(ImeEditTransaction(2, 1, "fixture", null)))
        assertEquals(2, h.payloads())
    }

    @Test fun focusAndTargetLossInvalidateWithoutRetargetOrReplay() {
        for (next in listOf(frame(available = false), frame(focusTarget = target.copy(window = "9")), frame().copy(runtime = 11))) {
            val h = Harness(); h.bind()
            assertTrue(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
            h.observe(next)
            assertEquals(ImeSessionPhase.CLOSING, h.connection.metadata().phase)
            assertNull(h.connection.keyFocus(1))
            assertFalse(h.connection.submit(ImeEditTransaction(1, 2, "fixture", null)))
            assertEquals(1, h.payloads())
        }
    }

    @Test fun lateCleanupCompletionCannotReopenRawGate() {
        for (time in listOf(100L, 101L)) {
            val h = Harness(); h.bind(); h.connection.close()
            val cleanup = h.sent.takeLast(2).map { ImeSessionCompletion(it.first, ImeDeliveryResult.APPLIED) }
            h.now = time; h.observe(frame(3, hasClient = false), cleanup)
            assertEquals(ImeSessionPhase.CLOSED, h.connection.metadata().phase)
            assertTrue(h.connection.metadata().rawBlocked)
            assertEquals("cleanup_unconfirmed", h.connection.metadata().failure)
        }
    }

    @Test fun oldPrivateEpochCannotBindAfterFreshPublicFocus() {
        val h = Harness(); h.connection.openEditor(1)
        assertFalse(h.connection.acceptsEditor(1))
        h.ack(0, frame(1, hasClient = false))
        h.ack(1, frame(2, hasClient = false))
        val mismatch = frame(2).copy(privateSnapshot = frame(1).privateSnapshot)
        h.ack(2, mismatch)
        assertEquals(ImeSessionPhase.CLOSING, h.connection.metadata().phase)
        assertFalse(h.connection.acceptsEditor(1))
        assertEquals(0, h.payloads())
    }

    @Test fun watchdogExpiresWithoutAnotherWorkerPacket() {
        val h = Harness(); h.bind()
        assertTrue(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        h.now = 101; h.connection.expire()
        assertEquals(ImeSessionPhase.CLOSING, h.connection.metadata().phase)
        h.now = 202; h.connection.expire()
        assertEquals(ImeSessionPhase.CLOSED, h.connection.metadata().phase)
        assertEquals("cleanup_unconfirmed", h.connection.metadata().failure)
        assertTrue(h.connection.metadata().rawBlocked)
        assertEquals(1, h.payloads())
    }

    @Test fun timeoutAndSinkRejectionNeverResendPayload() {
        for (reject in listOf(false, true)) {
            val h = Harness(); h.bind(); h.rejected = reject
            h.connection.submit(ImeEditTransaction(1, 1, "fixture", null))
            h.now = 101; h.observe()
            h.now = 202; h.observe()
            assertEquals(1, h.payloads())
            assertTrue(h.connection.metadata().rawBlocked)
            assertNull(h.connection.keyFocus(1))
        }
    }

    @Test fun closeDoesNotReleaseRawGateUntilBothCleanupAcksAndEpochAdvance() {
        val h = Harness(); h.bind(); h.connection.close()
        val reset = h.sent.lastIndex - 1; val disable = h.sent.lastIndex
        assertEquals(1, h.closed)
        h.ack(reset, frame(2, hasClient = false))
        h.ack(disable, frame(2, hasClient = false))
        assertTrue(h.connection.metadata().rawBlocked)
        h.observe(frame(3, hasClient = false))
        assertEquals(ImeSessionPhase.CLOSED, h.connection.metadata().phase)
        assertFalse(h.connection.metadata().rawBlocked)
        h.connection.openEditor(2)
        assertEquals(ImeSessionPhase.CLOSED, h.connection.metadata().phase)
    }

    @Test fun failedCleanupKeepsRawGateClosedAndIsNotRetried() {
        val h = Harness(); h.bind(); h.connection.close()
        val count = h.sent.size
        h.ack(h.sent.lastIndex, result = ImeDeliveryResult.NOT_READY)
        h.observe(); h.observe()
        assertEquals(count, h.sent.size)
        assertEquals("cleanup_unconfirmed", h.connection.metadata().failure)
        assertTrue(h.connection.metadata().rawBlocked)
    }

    @Test fun pendingAckCountIsBoundedAndDuplicateOldPacketsCannotAcknowledge() {
        val h = Harness(); h.bind()
        repeat(32) { assertTrue(h.connection.submit(ImeEditTransaction(1, it + 1L, "x", null))) }
        assertEquals(32, h.connection.metadata().pendingAcks)
        assertFalse(h.connection.submit(ImeEditTransaction(1, 33, "x", null)))
        assertEquals(32, h.payloads())
        assertTrue(h.connection.metadata().pendingAcks <= 32)
        val before = h.connection.metadata()
        h.connection.observe(h.order, h.nextFrame, listOf(ImeSessionCompletion(h.sent[3].first, ImeDeliveryResult.APPLIED)))
        assertEquals(before, h.connection.metadata())
    }

    @Test fun geometrySerialUpdatePreservesBindingButSemanticRevisionDoesNot() {
        val h = Harness(); h.bind(); h.observe(frame(serial = 8))
        assertTrue(h.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        val payload = (h.sent.last().second as ImeSessionCommand.Transaction).payload
        assertEquals(8L, payload.target.serial)
        val changed = h.nextFrame.privateSnapshot.snapshot!!.copy(editRevision = 2)
        h.observe(h.nextFrame.copy(privateSnapshot = ImeDecodedSnapshot(true, changed)))
        assertEquals(ImeSessionPhase.CLOSING, h.connection.metadata().phase)
        assertFalse(h.connection.submit(ImeEditTransaction(1, 2, "fixture", null)))
    }

    private fun envelope(): MutableMap<String, Any?> = mutableMapOf("schema" to 1, "code" to 0, "ime" to mutableMapOf(
        "target" to mutableMapOf<String, Any?>("session" to "9223372036854775807", "window" to "2", "surface" to "3",
            "focus_token" to "4", "input_epoch" to 0, "activation" to 1, "serial" to 0xffff_ffffL),
        "edit_revision" to 1L, "last_sequence" to 0L, "surrounding" to "private fixture must never be imported"))
    @Test fun parserUsesStrictIntegerAndStringTypesAndOmitsSurrounding() {
        val value = ImePrivateSnapshot.decode(envelope())
        assertTrue(value.valid)
        assertEquals("9223372036854775807", value.snapshot!!.target.session)
        assertFalse(value.toString().contains("private fixture"))
        assertTrue(ImePrivateSnapshot.decode(mapOf("schema" to 1, "code" to 0, "ime" to null)).valid)
        for ((field, bad) in listOf("schema" to "1", "schema" to 1.0, "code" to "0", "code" to 4, "ime" to "null")) {
            assertFalse(ImePrivateSnapshot.decode(envelope().apply { put(field, bad) }).valid)
        }
        assertFalse(ImePrivateSnapshot.decode(mapOf("schema" to 1, "code" to 0)).valid)
    }
    @Suppress("UNCHECKED_CAST")
    @Test fun parserRejectsMissingSequenceFloorAndCoercedOrOutOfRangeIds() {
        for ((field, bad) in listOf("session" to 1L, "session" to "01", "session" to "9223372036854775808",
            "serial" to 4294967296L, "serial" to 1.0, "input_epoch" to -1L, "activation" to "1")) {
            val map = envelope(); val ime = map["ime"] as MutableMap<String, Any?>
            (ime["target"] as MutableMap<String, Any?>)[field] = bad
            assertFalse(ImePrivateSnapshot.decode(map).valid)
        }
        val map = envelope(); (map["ime"] as MutableMap<String, Any?>).remove("last_sequence")
        assertFalse(ImePrivateSnapshot.decode(map).valid)
    }
}
