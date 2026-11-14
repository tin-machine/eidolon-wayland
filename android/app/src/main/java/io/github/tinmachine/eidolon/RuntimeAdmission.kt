package io.github.tinmachine.eidolon

/** The Host worker owns transitions; queued product work captures a thread-safe read ticket. */
internal class RuntimeAdmission {
    class Owner internal constructor(val epoch: Long, val captureToken: String)
    private val lock = Any()
    private var epoch = 0L
    private var owner: Owner? = null
    private var unknownOwner = false

    fun ticket(): Long? = synchronized(lock) { if (owner == null && !unknownOwner) epoch else null }
    fun permits(ticket: Long): Boolean = synchronized(lock) { owner == null && !unknownOwner && epoch == ticket }
    fun open(): Boolean = ticket() != null
    fun activeOwner(): Owner? = synchronized(lock) { owner.takeUnless { unknownOwner } }
    fun begin(captureToken: String): Owner = synchronized(lock) {
        check(owner == null && !unknownOwner && captureToken.isNotBlank()) { "SAVE_ADMISSION_CLOSED" }
        epoch = Math.addExact(epoch, 1)
        Owner(epoch, captureToken).also { owner = it }
    }
    fun owns(value: Owner): Boolean = synchronized(lock) { owner === value && epoch == value.epoch && !unknownOwner }
    fun finish(value: Owner) = synchronized(lock) {
        check(owns(value)) { "SAVE_CAPTURE_STALE" }
        // Work queued during a previous open epoch can never replay in this one.
        epoch = Math.addExact(epoch, 1)
        owner = null
    }
    /** Missing prior authority is distinct from a first, never allocated Runtime. */
    fun retainUnknownOwner() = synchronized(lock) {
        epoch = Math.addExact(epoch, 1)
        unknownOwner = true
    }
}
