package io.github.tinmachine.eidolon

/**
 * Activity-independent retirement tickets. Holds only owner identifiers and
 * immutable capture references; never retains a Scene, Surface or Activity.
 *
 * I5 may join this SDK receipt with the native StopReceipt, but must not treat
 * this port on its own as storage ReclamationBarrier approval.
 */
internal class SdkRetirementHub(val evidence: SdkEvidenceBridge = SdkEvidenceBridge()) {
    data class Ticket internal constructor(
        val runtime: Long,
        val admissionEpoch: Long,
        val captureToken: String,
    )
    private val lock = Any()
    private val tickets = linkedMapOf<Triple<Long, Long, String>, Ticket>()

    fun retireRuntime(runtime: Long, admissionEpoch: Long, captureToken: String): Ticket {
        require(runtime > 0 && admissionEpoch >= 0 && captureToken.isNotBlank())
        val id = Triple(runtime, admissionEpoch, captureToken)
        return synchronized(lock) {
            // Sealing and ticket issuance share the ledger admission boundary.
            evidence.sealRuntime(runtime)
            tickets.getOrPut(id) { Ticket(runtime, admissionEpoch, captureToken) }
        }
    }

    fun progress(ticket: Ticket): SdkEvidenceBridge.Progress {
        val id = Triple(ticket.runtime, ticket.admissionEpoch, ticket.captureToken)
        synchronized(lock) { require(tickets[id] === ticket) { "retirement capture is stale" } }
        return evidence.runtimeProgress(ticket.runtime)
    }
}
