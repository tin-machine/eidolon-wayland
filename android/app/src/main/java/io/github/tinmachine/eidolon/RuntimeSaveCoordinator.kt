package io.github.tinmachine.eidolon

import org.json.JSONObject

/** A census read from the still-live, captured native owner, never from a default snapshot. */
internal data class NativeReclamation(
    val runtime: Long,
    val originRetired: Boolean,
    val recoveryConnectAllowed: Boolean,
    val recoveryCommandAllowed: Boolean,
    val shutdownComplete: Boolean,
    val connections: Int,
    val workers: Int,
    val nativeTargets: Int,
    val panels: Int,
    val sdkBindings: Int,
    val boardWindows: Int,
    val pendingOperations: Int,
    val remoteCleanupPending: Boolean,
    val localCleanupPending: Boolean,
) {
    val fullyStopped: Boolean get() = runtime > 0 && shutdownComplete && originRetired &&
        connections == 0 && workers == 0 && nativeTargets == 0 && panels == 0 && sdkBindings == 0 &&
        boardWindows == 0 && pendingOperations == 0 && !remoteCleanupPending && !localCleanupPending

    companion object {
        fun decode(expectedRuntime: Long, envelope: JSONObject): NativeReclamation {
            require(expectedRuntime > 0 && envelope.opt("schema") in setOf<Any?>(1, 1L) && envelope.opt("code") in setOf<Any?>(0, 0L) &&
                envelope.get("handle") == expectedRuntime.toString()) { "SAVE_NATIVE_SNAPSHOT_INVALID" }
            val runtime = envelope.getJSONObject("runtime")
            val value = runtime.getJSONObject("reclamation")
            require(value.get("runtime") == expectedRuntime.toString()) { "SAVE_NATIVE_SNAPSHOT_STALE" }
            fun count(key: String): Int {
                val raw = value.get(key)
                require(raw is Int || raw is Long) { "SAVE_NATIVE_COUNT_INVALID" }
                val number = (raw as Number).toLong()
                require(number in 0..Int.MAX_VALUE.toLong()) { "SAVE_NATIVE_COUNT_INVALID" }
                return number.toInt()
            }
            fun flag(key: String): Boolean = (value.get(key) as? Boolean)
                ?: throw IllegalArgumentException("SAVE_NATIVE_FLAG_INVALID")
            val complete = flag("shutdown_complete")
            if (complete) require(runtime.getString("state") == "Stopped" &&
                runtime.getJSONObject("shutdown").get("complete") == true) { "SAVE_NATIVE_SHUTDOWN_INVALID" }
            return NativeReclamation(expectedRuntime, flag("origin_retired"), flag("recovery_connect_allowed"),
                flag("recovery_command_allowed"), complete, count("connections"), count("workers"),
                count("native_targets"), count("panels"), count("sdk_bindings"), count("board_windows"),
                count("pending_operations"), flag("remote_cleanup_pending"), flag("local_cleanup_pending"))
        }
    }
}

/** This object joins a pre-Destroy native census to the Host's actual return value and SDK ticket. */
internal class StopReceipt private constructor(
    val runtime: Long,
    val admissionEpoch: Long,
    val captureToken: String,
    private val native: NativeReclamation,
    private val sdk: SdkEvidenceBridge.Progress,
) {
    fun evidence(token: String): LinuxStorageRepository.ReclamationEvidence =
        LinuxStorageRepository.ReclamationEvidence(token, runtime, native.shutdownComplete, true,
            native.connections, native.workers, native.nativeTargets, native.panels, native.sdkBindings,
            native.boardWindows, Math.addExact(native.pendingOperations, sdk.pendingCallbacks),
            native.remoteCleanupPending || native.localCleanupPending)

    companion object {
        fun accept(owner: RuntimeAdmission.Owner, runtime: Long, native: NativeReclamation,
            ticket: SdkRetirementHub.Ticket, sdk: SdkEvidenceBridge.Progress, actualDestroyResult: Int): StopReceipt {
            require(runtime > 0 && native.runtime == runtime && native.fullyStopped && actualDestroyResult == 0 &&
                ticket.runtime == runtime && ticket.admissionEpoch == owner.epoch && ticket.captureToken == owner.captureToken &&
                sdk.sealed && sdk.allRetired && sdk.failures == 0 && sdk.pendingCallbacks == 0 && sdk.issued == sdk.complete) {
                "SAVE_RECLAMATION_INCOMPLETE"
            }
            return StopReceipt(runtime, owner.epoch, owner.captureToken, native, sdk)
        }
    }
}

/** Nonblocking, worker-confined save actor. UI/SDK progress never waits for this actor. */
internal class RuntimeSaveCoordinator(
    private val admission: RuntimeAdmission,
    private val sdk: SdkRetirementHub,
    private val port: Port,
    private val timeoutMillis: Long = 30_000,
) {
    interface Port {
        fun now(): Long
        fun currentRuntime(): Long
        fun observe(runtime: Long): NativeReclamation
        /** Disconnect/cancel the session, keeping the transport Controller alive. */
        fun stopOrigin(runtime: Long): Boolean
        fun connectForRecovery(runtime: Long): Boolean
        fun recoverOrigin(runtime: Long): Boolean
        fun requestRuntimeStop(runtime: Long): Int
        fun destroyRuntime(runtime: Long): Int
        fun runtimeDestroyed(runtime: Long)
    }
    enum class Phase { IDLE, ORIGIN_STOPPING, RECOVERY_REQUIRED, RECOVERING, RUNTIME_STOPPING, SAVE_PENDING, COMMITTED }
    class Operation internal constructor(
        val owner: RuntimeAdmission.Owner,
        val runtime: Long,
        internal var publish: (LinuxStorageRepository.ReclamationBarrier) -> Unit,
        internal var publicationToken: String,
        internal var deadline: Long,
    ) {
        var phase: Phase = Phase.ORIGIN_STOPPING; internal set
        var firstFailure: String? = null; internal set
        var cleanupFailure: String? = null; internal set
        internal var originStopSent = false
        internal var recoveryConnectSent = false
        internal var recoverySent = false
        internal var runtimeStopSent = false
        internal var sdkTicket: SdkRetirementHub.Ticket? = null
        internal var receipt: StopReceipt? = null
        internal var observation: NativeReclamation? = null
        val canRecover: Boolean get() = phase in setOf(Phase.RECOVERY_REQUIRED, Phase.RECOVERING) &&
            observation?.let { it.recoveryConnectAllowed || it.recoveryCommandAllowed } == true
        val canResumePublication: Boolean get() = phase == Phase.SAVE_PENDING && receipt != null
        val canRetryReclamation: Boolean get() = receipt == null && phase == Phase.SAVE_PENDING &&
            listOf(firstFailure, cleanupFailure).none { it in setOf("SAVE_SDK_DISPOSAL_FAILED", "SAVE_DESTROY_REJECTED", "SAVE_CAPTURE_STALE") }
        override fun toString() = "RuntimeSaveOperation(<private>)"
    }
    var operation: Operation? = null; private set
    val active: Boolean get() = operation?.phase?.let { it != Phase.COMMITTED } == true

    fun begin(runtime: Long, captureToken: String, publish: (LinuxStorageRepository.ReclamationBarrier) -> Unit): Operation {
        require(!active && runtime > 0 && port.currentRuntime() == runtime) { "SAVE_OWNER_UNKNOWN" }
        val owner = admission.begin(captureToken)
        return Operation(owner, runtime, publish, captureToken, port.now() + timeoutMillis).also { operation = it }
    }

    fun poll() {
        val op = operation ?: return
        if (op.phase in setOf(Phase.COMMITTED, Phase.SAVE_PENDING)) return
        if (!admission.owns(op.owner) || port.currentRuntime() != op.runtime) {
            fail(op, "SAVE_CAPTURE_STALE", Phase.SAVE_PENDING); return
        }
        try {
            val observed = port.observe(op.runtime)
            require(observed.runtime == op.runtime) { "SAVE_CAPTURE_STALE" }
            op.observation = observed
            when (op.phase) {
                Phase.ORIGIN_STOPPING -> {
                    if (observed.originRetired) beginRuntimeStop(op)
                    else if (!op.originStopSent) op.originStopSent = port.stopOrigin(op.runtime)
                    if (op.phase == Phase.ORIGIN_STOPPING && port.now() >= op.deadline)
                        fail(op, if (observed.remoteCleanupPending) "SAVE_REMOTE_PENDING" else "SAVE_ORIGIN_PENDING", Phase.RECOVERY_REQUIRED)
                }
                Phase.RECOVERY_REQUIRED -> {
                    // Keep observing late retirement, but require a new explicit continuation.
                    if (observed.originRetired) op.cleanupFailure = null
                }
                Phase.RECOVERING -> {
                    if (observed.originRetired) beginRuntimeStop(op)
                    else if (!op.recoverySent && observed.recoveryCommandAllowed) op.recoverySent = port.recoverOrigin(op.runtime)
                    else if (!op.recoveryConnectSent && !op.recoverySent && observed.recoveryConnectAllowed)
                        op.recoveryConnectSent = port.connectForRecovery(op.runtime)
                    else if (op.recoverySent && !observed.remoteCleanupPending && !observed.localCleanupPending) {
                        if (port.stopOrigin(op.runtime)) { op.phase = Phase.ORIGIN_STOPPING; op.originStopSent = true }
                    }
                    if (op.phase == Phase.RECOVERING && port.now() >= op.deadline)
                        fail(op, "SAVE_RECOVERY_PENDING", Phase.RECOVERY_REQUIRED)
                }
                Phase.RUNTIME_STOPPING -> finishRuntimeStop(op, observed)
                else -> Unit
            }
        } catch (_: Exception) {
            fail(op, "SAVE_OBSERVATION_FAILED", if (op.runtimeStopSent) Phase.SAVE_PENDING else Phase.RECOVERY_REQUIRED)
        }
    }

    /** Only the explicit UI entry calls this; retry does not clear the first failure. */
    fun continueReclamation(): Boolean {
        val op = operation ?: return false
        if (!admission.owns(op.owner) || port.currentRuntime() != op.runtime ||
            (op.phase != Phase.RECOVERY_REQUIRED && !op.canRetryReclamation) || op.receipt != null) return false
        val current = try { port.observe(op.runtime) } catch (_: Exception) { return false }
        if (current.runtime != op.runtime) return false
        op.observation = current
        op.deadline = port.now() + timeoutMillis
        if (op.runtimeStopSent) { op.phase = Phase.RUNTIME_STOPPING; return true }
        if (current.originRetired) { beginRuntimeStop(op); return true }
        if (!current.recoveryConnectAllowed && !current.recoveryCommandAllowed) return false
        op.phase = Phase.RECOVERING
        op.recoveryConnectSent = false; op.recoverySent = false
        return true
    }

    /** Host verifies the same durable intent before granting the new readback token alias. */
    fun resumePublication(originalCapture: String, token: String,
        action: (LinuxStorageRepository.ReclamationBarrier) -> Unit): Boolean {
        val op = operation ?: return false
        if (!op.canResumePublication || !admission.owns(op.owner) || op.owner.captureToken != originalCapture ||
            port.currentRuntime() != 0L || token.isBlank()) return false
        op.publicationToken = token; op.publish = action
        publish(op)
        return true
    }

    private fun beginRuntimeStop(op: Operation) {
        if (op.sdkTicket == null) op.sdkTicket = sdk.retireRuntime(op.runtime, op.owner.epoch, op.owner.captureToken)
        val result = port.requestRuntimeStop(op.runtime)
        if (result != 0) { fail(op, "SAVE_STOP_REJECTED", Phase.SAVE_PENDING); return }
        op.runtimeStopSent = true
        op.phase = Phase.RUNTIME_STOPPING
        op.deadline = port.now() + timeoutMillis
    }

    private fun finishRuntimeStop(op: Operation, observed: NativeReclamation) {
        val ticket = checkNotNull(op.sdkTicket)
        val progress = sdk.progress(ticket)
        if (progress.failures != 0) { fail(op, "SAVE_SDK_DISPOSAL_FAILED", Phase.SAVE_PENDING); return }
        if (observed.fullyStopped && progress.allRetired && progress.pendingCallbacks == 0) {
            // The actual return is collected after, never synthesized in, the living snapshot.
            val result = port.destroyRuntime(op.runtime)
            if (result == 0) {
                op.receipt = StopReceipt.accept(op.owner, op.runtime, observed, ticket, progress, result)
                port.runtimeDestroyed(op.runtime)
                publish(op)
            } else if (result != 2) fail(op, "SAVE_DESTROY_REJECTED", Phase.SAVE_PENDING)
        }
        if (op.phase == Phase.RUNTIME_STOPPING && port.now() >= op.deadline)
            fail(op, "SAVE_RUNTIME_PENDING", Phase.SAVE_PENDING)
    }

    private fun publish(op: Operation) {
        try {
            val receipt = checkNotNull(op.receipt)
            val barrier = object : LinuxStorageRepository.ReclamationBarrier {
                override fun <T> withReclaimed(captureToken: String, action: (LinuxStorageRepository.ReclamationEvidence) -> T): T {
                    fun current() = admission.owns(op.owner) && port.currentRuntime() == 0L &&
                        receipt.runtime == op.runtime && receipt.admissionEpoch == op.owner.epoch &&
                        receipt.captureToken == op.owner.captureToken && captureToken == op.publicationToken
                    check(current()) { "SAVE_CAPTURE_STALE" }
                    val evidence = receipt.evidence(captureToken)
                    check(evidence.fullyReclaimed) { "SAVE_RECLAMATION_INCOMPLETE" }
                    val result = action(evidence)
                    check(current()) { "SAVE_CAPTURE_STALE" }
                    return result
                }
            }
            op.publish(barrier)
            check(admission.owns(op.owner) && port.currentRuntime() == 0L) { "SAVE_CAPTURE_STALE" }
            op.phase = Phase.COMMITTED
            admission.finish(op.owner)
        } catch (_: Exception) { fail(op, "SAVE_PUBLICATION_FAILED", Phase.SAVE_PENDING) }
    }

    private fun fail(op: Operation, reason: String, phase: Phase) {
        if (op.firstFailure == null) op.firstFailure = reason else op.cleanupFailure = reason
        op.phase = phase
    }
}
