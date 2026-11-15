package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test

/** Characterizes current model composition, not acceptance of the proposed IME retirement contract.
 * Host UI posting/owner clearing and Entity disposal are explicit doubles, not the Android classes.
 */
class SaveImeBoundaryTest {
    private class Fixture(bound: Boolean) : RuntimeSaveCoordinator.Port {
        val admission = RuntimeAdmission()
        val hub = SdkRetirementHub()
        val coordinator = RuntimeSaveCoordinator(admission, hub, this)
        val target = ImeSdkTarget("7", "9", "2", "1")
        val ui = ArrayDeque<() -> Unit>()
        val trace = mutableListOf<String>()
        val sent = mutableListOf<Pair<String, ImeSessionCommand>>()
        var runtime = 11L
        var ticketValid = true
        var ownerPresent = true
        var entityAlive = true
        var failUiCleanup = false
        var publications = 0
        var order = 0L
        var requests = 0L
        val connection = ImeSessionConnection(11, target, 0, { (++requests).toString() }, { request, command ->
            val userEffect = command is ImeSessionCommand.Transaction || command is ImeSessionCommand.Focus ||
                command is ImeSessionCommand.Backend && command.enabled
            if (runtime != 11L || !ownerPresent || userEffect && !ticketValid) ImeEnqueueResult.UNAVAILABLE
            else { sent += request to command; ImeEnqueueResult.ACCEPTED }
        }, { 0L }, { trace += "editor-close" })

        init {
            if (bound) {
                connection.openEditor(1)
                ack(0, 1, false)
                ack(1, 2, false)
                ack(2, 2, true)
                check(connection.metadata().phase == ImeSessionPhase.BOUND)
            } else connection.start()
        }
        private fun ack(index: Int, epoch: Long, client: Boolean) {
            val snapshot = ImeBridgeSnapshot(ImeNativeTarget("7", "9", "6", "5", epoch, 1, 7), 1, 0)
            connection.observe(++order, ImeSessionFrame(11, epoch, true,
                ImeSessionFocus(11, epoch, target, "5"), ImeDecodedSnapshot(true, snapshot.takeIf { client })),
                listOf(ImeSessionCompletion(sent[index].first, ImeDeliveryResult.APPLIED)))
        }
        fun begin() {
            coordinator.begin(11, "c".repeat(64)) { barrier ->
                barrier.withReclaimed("c".repeat(64)) { evidence ->
                    check(evidence.fullyReclaimed)
                    trace += "publish"; publications++
                }
            }
            // Models NativeSessionHost.invalidateRuntimeInput: revoke first, post close to UI.
            ticketValid = false
            ui.addLast { if (ownerPresent) connection.close() }
        }
        fun drainUi() { while (ui.isNotEmpty()) ui.removeFirst().invoke() }
        fun controllerTick() {
            // Models the owner-unavailable branch of TerminalImeController.Run.finish.
            if (!ownerPresent && entityAlive) {
                trace += "ui-dispose-attempt"
                if (failUiCleanup) return
                entityAlive = false; trace += "ui-disposed"
            }
        }
        override fun now() = 0L
        override fun currentRuntime() = runtime
        override fun observe(runtime: Long) = NativeReclamation(runtime, true, false, false, true,
            0, 0, 0, 0, 0, 0, 0, false, false)
        override fun stopOrigin(runtime: Long) = true
        override fun connectForRecovery(runtime: Long) = false
        override fun recoverOrigin(runtime: Long) = false
        override fun requestRuntimeStop(runtime: Long): Int { trace += "stop"; return 0 }
        override fun destroyRuntime(runtime: Long): Int { trace += "destroy"; return 0 }
        override fun runtimeDestroyed(runtime: Long) {
            check(this.runtime == runtime)
            this.runtime = 0
            // Mirrors the asynchronous boundary; there is no UI ACK in Coordinator.Port.
            ui.addLast { connection.close(); ownerPresent = false; trace += "owner-cleared" }
        }
    }

    @Test fun currentPublicationDoesNotWaitForPreparingOrBoundImeUi() {
        for (bound in listOf(false, true)) {
            val f = Fixture(bound)
            f.begin(); f.coordinator.poll(); f.coordinator.poll()
            assertEquals(1, f.publications)
            assertTrue(f.ownerPresent)
            assertTrue(f.entityAlive)
            assertFalse(f.ticketValid)
            f.drainUi(); f.controllerTick()
            assertFalse(f.entityAlive)
            assertTrue(f.trace.indexOf("publish") < f.trace.indexOf("ui-disposed"))
            assertFalse(f.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        }
    }

    @Test fun currentPublicationHasNoFeedbackFromLateUiDisposalFailure() {
        val f = Fixture(true)
        f.begin(); f.coordinator.poll(); f.coordinator.poll()
        f.failUiCleanup = true
        f.drainUi(); f.controllerTick()
        assertTrue(f.entityAlive)
        assertTrue(f.connection.metadata().rawBlocked)
        assertEquals(RuntimeSaveCoordinator.Phase.COMMITTED, f.coordinator.operation!!.phase)
        assertEquals(1, f.publications)
        f.failUiCleanup = false; f.controllerTick()
        assertFalse(f.entityAlive)
        assertEquals(1, f.publications)
    }

    @Test fun currentInputCloseCanPrecedeDestroyWhileUiStillOutlivesPublication() {
        val f = Fixture(true)
        f.begin(); f.drainUi()
        assertEquals(ImeSessionPhase.CLOSING, f.connection.metadata().phase)
        assertFalse(f.connection.submit(ImeEditTransaction(1, 1, "fixture", null)))
        assertTrue(f.entityAlive)
        f.coordinator.poll(); f.coordinator.poll()
        assertEquals(1, f.publications)
        assertTrue(f.entityAlive)
        assertTrue(f.connection.metadata().rawBlocked) // ACK/Android/UI completion are independent.
        f.drainUi(); f.controllerTick()
        assertFalse(f.entityAlive)
    }
}
