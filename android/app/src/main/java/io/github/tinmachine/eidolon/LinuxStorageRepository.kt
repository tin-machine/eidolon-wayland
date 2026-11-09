package io.github.tinmachine.eidolon

import java.security.MessageDigest
import java.security.SecureRandom

/** E1 only: a matched local snapshot is not permission to Inspect/Launch or advertise availability. */
class LinuxStorageRepository internal constructor(
    private val installation: String,
    private val root: LinuxStorageFiles,
    private val profiles: LinuxStorageFiles,
    private val storage: LinuxStorageFiles,
    private val random: SecureRandom = SecureRandom(),
) {
    companion object {
        internal const val ANCHOR = "linux-storage-v3-anchor.json"
        internal const val PROFILE = "remote-linux.json"
        internal const val RECORD = "record.json"
        internal const val TRANSACTION = "transaction.json"
        internal const val COMMIT = "commit.json"
        private const val ANCHOR_KIND = "eidolon-linux-storage-anchor-v1"
        private const val TRANSACTION_KIND = "eidolon-linux-storage-transaction-v1"
        private const val COMMIT_KIND = "eidolon-linux-storage-commit-v1"
        internal fun hash(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }
        private fun hashOrNull(bytes: ByteArray?) = bytes?.let(::hash)
        private fun equal(a: ByteArray?, b: ByteArray?): Boolean =
            if (a == null || b == null) a == null && b == null else a.contentEquals(b)
    }

    init { require(installation.matches(Regex("[0-9a-f]{32}"))) { "STORAGE_INSTALLATION_INVALID" } }

    /** No successful default or production implementation. E2 must hold its runtime admission gate
     * through this callback and supply the actual shutdown/destroy evidence for this capture. */
    interface ReclamationBarrier {
        fun <T> withReclaimed(captureToken: String, action: (ReclamationEvidence) -> T): T
    }

    class ReclamationEvidence(
        val captureToken: String,
        val stoppedRuntime: Long,
        val shutdownComplete: Boolean,
        val nativeDestroyAccepted: Boolean,
        val ownedConnections: Int,
        val pendingWorkers: Int,
        val nativeTargets: Int,
        val panels: Int,
        val sdkBindings: Int,
        val boardWindows: Int,
        val pendingOperations: Int,
        val remoteCleanupPending: Boolean,
    ) {
        val fullyReclaimed: Boolean get() = stoppedRuntime > 0 && shutdownComplete && nativeDestroyAccepted &&
            ownedConnections == 0 && pendingWorkers == 0 && nativeTargets == 0 && panels == 0 &&
            sdkBindings == 0 && boardWindows == 0 && pendingOperations == 0 && !remoteCleanupPending
        override fun toString() = "StorageReclamationEvidence(<private>)"
    }

    /** Immutable, repository-produced capture. Includes exact on-disk byte digests, not only binding. */
    class Capture internal constructor(
        val currentDefaultInstallation: String,
        val profile: ConnectionProfileV2.Value?,
        val record: LinuxStorageRecord?,
        internal val profileDigest: String?,
        internal val recordDigest: String?,
        internal val anchorDigest: String?,
        internal val transactionDigest: String?,
        internal val commitDigest: String?,
    ) {
        val oldHostUserBinding: String? = profile?.let { StorageCapture.from(currentDefaultInstallation, it).hostUserBinding }
        val expectedRecordDigest: String? = record?.sha256()
        val token: String = hash(LinuxStorageJson.canonical(mapOf(
            "domain" to "eidolon-linux-storage-save-capture-v1", "legacy_installation" to currentDefaultInstallation,
            "profile_sha256" to profileDigest, "record_sha256" to recordDigest,
            "anchor_sha256" to anchorDigest, "transaction_sha256" to transactionDigest, "commit_sha256" to commitDigest,
        )))
        override fun toString() = "LinuxStorageCapture(<private>)"
    }

    sealed class State {
        /** This is first enrollment, possibly alongside an existing legacy v2 profile. */
        class Uninitialized(val capture: Capture) : State()
        /** Local capture consistency only; no v3 runtime, source, pins or recovery capability. */
        class Matched(val capture: Capture) : State()
        class Pending(val capture: PendingCapture) : State()
        class Blocked(val reason: String) : State()
    }

    class PendingCapture internal constructor(
        val currentDefaultInstallation: String,
        val transactionDigest: String,
        val previousProfile: ConnectionProfileV2.Value?,
        val nextProfile: ConnectionProfileV2.Value,
        val previousRecord: LinuxStorageRecord?,
        val nextRecord: LinuxStorageRecord,
        internal val anchorDigest: String,
        internal val profileDigest: String?,
        internal val recordDigest: String?,
        internal val commitDigest: String?,
    ) {
        val token: String = hash(LinuxStorageJson.canonical(mapOf(
            "domain" to "eidolon-linux-storage-resume-capture-v1", "legacy_installation" to currentDefaultInstallation,
            "transaction_sha256" to transactionDigest, "anchor_sha256" to anchorDigest,
            "profile_sha256" to profileDigest, "record_sha256" to recordDigest, "commit_sha256" to commitDigest,
        )))
        override fun toString() = "LinuxStoragePendingCapture(<private>)"
    }

    /** Every precondition is mandatory. nextProfile follows existing v2 propose/replacement rules. */
    class SaveRequest(
        val currentDefaultInstallation: String,
        val captured: Capture,
        val capturedProfile: ConnectionProfileV2.Value?,
        val nextProfile: ConnectionProfileV2.Value,
        val oldHostUserBinding: String?,
        val newHostUserBinding: String,
        val expectedOldRecordDigest: String?,
        val explicitSave: Boolean,
    ) {
        override fun toString() = "LinuxStorageSaveRequest(<private>)"
    }

    class ResumeRequest(
        val currentDefaultInstallation: String,
        val captured: PendingCapture,
        val capturedPreviousProfile: ConnectionProfileV2.Value?,
        val capturedNextProfile: ConnectionProfileV2.Value,
        val oldHostUserBinding: String?,
        val newHostUserBinding: String,
        val expectedOldRecordDigest: String?,
        val expectedTransactionDigest: String,
        val explicitSave: Boolean,
    ) {
        override fun toString() = "LinuxStorageResumeRequest(<private>)"
    }

    /** The attempted write still failed even if a subsequent reload finds a fully committed pair. */
    class SaveFailure internal constructor(val reloaded: State) : Exception("STORAGE_SAVE_FAILED_RELOADED")

    @Volatile private var cached: State = State.Blocked("STORAGE_RELOAD_REQUIRED")
    /** A second Store may have changed disk since the last call; never export a stale cached pair. */
    fun currentState(): State = load(installation)

    fun load(currentDefaultInstallation: String): State = LinuxStorageIo.serialized {
        cached = State.Blocked("STORAGE_RELOAD_REQUIRED")
        reload(currentDefaultInstallation).also { cached = it }
    }

    fun save(request: SaveRequest, barrier: ReclamationBarrier): State.Matched = attempt(request.currentDefaultInstallation) {
        barrier.withReclaimed(request.captured.token) { evidence -> LinuxStorageIo.serialized {
            cached = State.Blocked("STORAGE_RELOAD_REQUIRED")
            try {
                checkBarrier(request.currentDefaultInstallation, request.captured.token, request.explicitSave, evidence)
                val current = reload(request.currentDefaultInstallation)
                val capture = when (current) {
                    is State.Uninitialized -> current.capture
                    is State.Matched -> current.capture
                    else -> throw IllegalStateException("STORAGE_RELOAD_REQUIRED")
                }
                require(capture.token == request.captured.token &&
                    sameProfile(capture.profile, request.capturedProfile) &&
                    sameProfile(capture.profile, request.captured.profile) &&
                    capture.expectedRecordDigest == request.expectedOldRecordDigest &&
                    capture.oldHostUserBinding == request.oldHostUserBinding) { "STORAGE_STALE" }
                val next = checkedNext(capture.profile, request.nextProfile)
                val nextCapture = StorageCapture.from(installation, next)
                require(nextCapture.hostUserBinding == request.newHostUserBinding) { "STORAGE_STALE" }

                // First enrollment is marked durably BEFORE generating either ID. Any partial marker
                // or directory is evidence of attempted enrollment, never another random-ID opportunity.
                if (current is State.Uninitialized) {
                    root.publish(ANCHOR, null, anchor(0, null, pending = true, intent = null))
                    storage.create()
                } else {
                    val before = checkNotNull(root.read(ANCHOR))
                    require(hash(before) == capture.anchorDigest) { "STORAGE_STALE" }
                    val oldAnchor = decodeAnchor(before)
                    require(!oldAnchor.pending) { "STORAGE_RELOAD_REQUIRED" }
                    // Reserve every update before allocating a replacement profile ID. A failed
                    // intent write must not silently discard an already-generated namespace.
                    root.publish(ANCHOR, before, anchor(oldAnchor.generation, oldAnchor.commitDigest, pending = true, intent = null))
                }
                val anchorBytes = checkNotNull(root.read(ANCHOR))
                val previousAnchor = decodeAnchor(anchorBytes)
                require(previousAnchor.pending && previousAnchor.generation < Long.MAX_VALUE) { "STORAGE_GENERATION_EXHAUSTED" }
                val installationId = capture.record?.installationId ?: StorageInstallationId.generate(random)
                val retained = capture.record?.hostUserBinding == nextCapture.hostUserBinding
                val profileId = if (retained) checkNotNull(capture.record).profileStorageId else ProfileStorageId.generate(random)
                val record = LinuxStorageRecord.from(nextCapture, installationId, profileId)
                if (capture.record != null) LinuxStorageIdentity.validateTransition(capture.record, record,
                    checkNotNull(request.expectedOldRecordDigest), nextCapture, request.explicitSave, evidence.fullyReclaimed)
                val transaction = Transaction(previousAnchor.generation + 1, hash(anchorBytes),
                    capture.commitDigest, capture.profile, capture.profileDigest, capture.record, capture.recordDigest, next, record)
                val transactionBytes = transaction.encode()
                // Check the complete captured state immediately before durable intent publication.
                require(hashOrNull(profiles.read(PROFILE)) == capture.profileDigest &&
                    hashOrNull(storage.read(RECORD)) == capture.recordDigest &&
                    hashOrNull(storage.read(COMMIT)) == capture.commitDigest &&
                    hashOrNull(storage.read(TRANSACTION)) == capture.transactionDigest) { "STORAGE_STALE" }
                val oldTransaction = storage.read(TRANSACTION)
                storage.publish(TRANSACTION, oldTransaction, transactionBytes)
                // Bind the exact intent in the independently placed anchor before changing either
                // data file. An unanchored intent is not adopted after a crash or write failure.
                val preparedAnchor = preparedAnchor(transaction, transactionBytes)
                root.publish(ANCHOR, anchorBytes, preparedAnchor)
                finish(transaction, transactionBytes, preparedAnchor)
                requireMatched(request.currentDefaultInstallation, next, record)
            } catch (_: Exception) {
                val reloaded = reload(request.currentDefaultInstallation)
                cached = reloaded
                throw SaveFailure(reloaded)
            }
        } }
    }

    /** Explicit roll-forward of exactly one durable intent. No rollback, new IDs or remote data work. */
    fun resumePending(request: ResumeRequest, barrier: ReclamationBarrier): State.Matched = attempt(request.currentDefaultInstallation) {
        barrier.withReclaimed(request.captured.token) { evidence -> LinuxStorageIo.serialized {
            cached = State.Blocked("STORAGE_RELOAD_REQUIRED")
            try {
                checkBarrier(request.currentDefaultInstallation, request.captured.token, request.explicitSave, evidence)
                val pending = (reload(request.currentDefaultInstallation) as? State.Pending)?.capture
                    ?: throw IllegalStateException("STORAGE_RELOAD_REQUIRED")
                require(pending.token == request.captured.token &&
                    pending.transactionDigest == request.expectedTransactionDigest &&
                    sameProfile(pending.previousProfile, request.capturedPreviousProfile) &&
                    sameProfile(pending.nextProfile, request.capturedNextProfile) &&
                    pending.previousRecord?.sha256() == request.expectedOldRecordDigest &&
                    pending.previousProfile?.let { StorageCapture.from(installation, it).hostUserBinding } == request.oldHostUserBinding &&
                    StorageCapture.from(installation, pending.nextProfile).hostUserBinding == request.newHostUserBinding) { "STORAGE_STALE" }
                val bytes = checkNotNull(storage.read(TRANSACTION))
                require(hash(bytes) == pending.transactionDigest) { "STORAGE_STALE" }
                val transaction = decodeTransaction(bytes)
                finish(transaction, bytes, checkNotNull(root.read(ANCHOR)))
                requireMatched(request.currentDefaultInstallation, transaction.nextProfile, transaction.nextRecord)
            } catch (_: Exception) {
                val reloaded = reload(request.currentDefaultInstallation)
                cached = reloaded
                throw SaveFailure(reloaded)
            }
        } }
    }

    private fun <T> attempt(currentInstallation: String, action: () -> T): T {
        LinuxStorageIo.serialized { cached = State.Blocked("STORAGE_RELOAD_REQUIRED") }
        try { return action() }
        catch (failure: SaveFailure) { throw failure }
        catch (_: Exception) {
            // Includes barrier failures before its callback and after publication in its finally.
            val reloaded = LinuxStorageIo.serialized { reload(currentInstallation).also { cached = it } }
            throw SaveFailure(reloaded)
        }
    }

    private fun checkBarrier(currentInstallation: String, token: String, explicit: Boolean, evidence: ReclamationEvidence) {
        require(currentInstallation == installation) { "STORAGE_INSTALLATION_CHANGED" }
        require(explicit) { "STORAGE_EXPLICIT_SAVE_REQUIRED" }
        require(evidence.captureToken == token && evidence.fullyReclaimed) { "STORAGE_RECLAMATION_REQUIRED" }
    }

    private fun finish(transaction: Transaction, transactionBytes: ByteArray, anchorBytes: ByteArray) {
        require(equal(anchorBytes, preparedAnchor(transaction, transactionBytes)) &&
            equal(root.read(ANCHOR), anchorBytes) && equal(storage.read(TRANSACTION), transactionBytes)) { "STORAGE_STALE" }
        val commit = commit(transaction, transactionBytes)
        val existingCommit = storage.read(COMMIT)
        require(hashOrNull(existingCommit) == transaction.previousCommitDigest || equal(existingCommit, commit)) { "STORAGE_STALE" }
        publishEither(profiles, PROFILE, transaction.previousProfileDigest, transaction.nextProfileBytes)
        publishEither(storage, RECORD, transaction.previousRecordDigest, transaction.nextRecordBytes)
        // Several atomic renames are not a transaction. Validate the whole pair before sealing it.
        require(equal(profiles.read(PROFILE), transaction.nextProfileBytes) &&
            equal(storage.read(RECORD), transaction.nextRecordBytes) &&
            equal(storage.read(TRANSACTION), transactionBytes) && equal(root.read(ANCHOR), anchorBytes)) { "STORAGE_READBACK_INVALID" }
        if (!equal(existingCommit, commit)) storage.publish(COMMIT, existingCommit, commit)
        root.publish(ANCHOR, anchorBytes, anchor(transaction.generation, hash(commit), pending = false, intent = null))
    }

    private fun publishEither(files: LinuxStorageFiles, name: String, previousDigest: String?, target: ByteArray) {
        val bytes = files.read(name)
        if (equal(bytes, target)) return
        require(hashOrNull(bytes) == previousDigest) { "STORAGE_STALE" }
        if (!files.exists()) files.create()
        files.publish(name, bytes, target)
    }

    private fun requireMatched(currentInstallation: String, profile: ConnectionProfileV2.Value, record: LinuxStorageRecord): State.Matched {
        val state = reload(currentInstallation)
        require(state is State.Matched && sameProfile(state.capture.profile, profile) &&
            state.capture.record?.sha256() == record.sha256()) { "STORAGE_READBACK_INVALID" }
        cached = state
        return state
    }

    private fun reload(currentInstallation: String): State = try {
        require(currentInstallation == installation) { "STORAGE_INSTALLATION_CHANGED" }
        val anchorBytes = root.read(ANCHOR)
        val directoryExists = storage.exists()
        val profileBytes = profiles.read(PROFILE)
        val profile = profileBytes?.let { LinuxConnectionProfile.decode(it, installation) }
        if (anchorBytes == null) {
            require(!directoryExists) { "STORAGE_ENROLLMENT_LOST" }
            State.Uninitialized(capture(profile, profileBytes, null, null, null, null, null))
        } else {
            val owner = decodeAnchor(anchorBytes)
            require(directoryExists) { "STORAGE_ENROLLMENT_LOST" }
            val transactionBytes = checkNotNull(storage.read(TRANSACTION)) { "STORAGE_TRANSACTION_LOST" }
            val transaction = decodeTransaction(transactionBytes)
            val recordBytes = storage.read(RECORD)
            val commitBytes = storage.read(COMMIT)
            val wantedCommit = commit(transaction, transactionBytes)
            if (!owner.pending && owner.generation == transaction.generation && owner.commitDigest == hash(wantedCommit)) {
                // Once finalized, a lost commit/record/profile is never a pending first publication.
                require(equal(commitBytes, wantedCommit) && equal(profileBytes, transaction.nextProfileBytes) &&
                    equal(recordBytes, transaction.nextRecordBytes)) { "STORAGE_COMMITTED_DATA_LOST" }
                val record = LinuxStorageIdentityCodec.decode(checkNotNull(recordBytes))
                record.matchesCapture(StorageCapture.from(installation, checkNotNull(profile)))
                State.Matched(capture(profile, profileBytes, record, recordBytes, anchorBytes, transactionBytes, commitBytes))
            } else {
                require(owner.pending && owner.intentDigest == hash(transactionBytes) &&
                    equal(anchorBytes, preparedAnchor(transaction, transactionBytes)) && owner.generation == transaction.generation - 1 &&
                    owner.commitDigest == transaction.previousCommitDigest &&
                    (hashOrNull(commitBytes) == transaction.previousCommitDigest || equal(commitBytes, wantedCommit)) &&
                    (hashOrNull(profileBytes) == transaction.previousProfileDigest || equal(profileBytes, transaction.nextProfileBytes)) &&
                    (hashOrNull(recordBytes) == transaction.previousRecordDigest || equal(recordBytes, transaction.nextRecordBytes))) {
                    "STORAGE_PARTIAL_PUBLISH_INVALID"
                }
                if (hashOrNull(profileBytes) == transaction.previousProfileDigest)
                    require(sameProfile(profile, transaction.previousProfile)) { "STORAGE_STALE" }
                if (hashOrNull(recordBytes) == transaction.previousRecordDigest)
                    require(recordBytes?.let { LinuxStorageIdentityCodec.decode(it).sha256() } ==
                        transaction.previousRecord?.sha256()) { "STORAGE_STALE" }
                State.Pending(PendingCapture(installation, hash(transactionBytes), transaction.previousProfile,
                    transaction.nextProfile, transaction.previousRecord, transaction.nextRecord, hash(anchorBytes),
                    hashOrNull(profileBytes), hashOrNull(recordBytes), hashOrNull(commitBytes)))
            }
        }
    } catch (_: Exception) { State.Blocked("STORAGE_RELOAD_REQUIRED") }

    private fun capture(profile: ConnectionProfileV2.Value?, profileBytes: ByteArray?, record: LinuxStorageRecord?,
                        recordBytes: ByteArray?, anchorBytes: ByteArray?, transactionBytes: ByteArray?, commitBytes: ByteArray?) =
        Capture(installation, profile, record, hashOrNull(profileBytes), hashOrNull(recordBytes),
            hashOrNull(anchorBytes), hashOrNull(transactionBytes), hashOrNull(commitBytes))

    private fun sameProfile(a: ConnectionProfileV2.Value?, b: ConnectionProfileV2.Value?) =
        if (a == null || b == null) a == null && b == null else a.toFields() == b.toFields()

    private fun checkedNext(previous: ConnectionProfileV2.Value?, next: ConnectionProfileV2.Value): ConnectionProfileV2.Value {
        val value = LinuxConnectionProfile.decode(ConnectionProfileV2Codec.encode(next), installation)
        if (previous == null) require(value.revision == "1") { "PROFILE_INVALID" }
        else previous.replacement(previous.binding(), value.toFields(), true)
        return value
    }

    private data class Anchor(val generation: Long, val commitDigest: String?, val pending: Boolean, val intentDigest: String?)
    private fun anchor(generation: Long, commitDigest: String?, pending: Boolean, intent: String?): ByteArray = metadata(mapOf(
        "schema" to 1, "kind" to ANCHOR_KIND, "legacy_installation" to installation,
        "generation" to generation.toString(), "last_commit_sha256" to commitDigest, "pending" to pending,
        "pending_transaction_sha256" to intent,
    ))
    private fun preparedAnchor(transaction: Transaction, bytes: ByteArray): ByteArray =
        anchor(transaction.generation - 1, transaction.previousCommitDigest, pending = true, intent = hash(bytes))
    private fun decodeAnchor(bytes: ByteArray): Anchor {
        val value = fields(bytes, setOf("schema", "kind", "legacy_installation", "generation", "last_commit_sha256", "pending",
            "pending_transaction_sha256"), ANCHOR_KIND)
        val generation = decimal(value["generation"], zero = true)
        val digest = nullableHash(value["last_commit_sha256"])
        val pending = value["pending"] as? Boolean ?: throw IllegalArgumentException("STORAGE_METADATA_INVALID")
        val intent = nullableHash(value["pending_transaction_sha256"])
        require((generation == 0L) == (digest == null)) { "STORAGE_METADATA_INVALID" }
        require(generation != 0L || pending) { "STORAGE_METADATA_INVALID" }
        require(pending || intent == null) { "STORAGE_METADATA_INVALID" }
        return Anchor(generation, digest, pending, intent)
    }

    private inner class Transaction(
        val generation: Long,
        val previousAnchorDigest: String,
        val previousCommitDigest: String?,
        val previousProfile: ConnectionProfileV2.Value?,
        val previousProfileDigest: String?,
        val previousRecord: LinuxStorageRecord?,
        val previousRecordDigest: String?,
        val nextProfile: ConnectionProfileV2.Value,
        val nextRecord: LinuxStorageRecord,
    ) {
        val nextProfileBytes: ByteArray = ConnectionProfileV2Codec.encode(nextProfile)
        val nextRecordBytes: ByteArray = LinuxStorageIdentityCodec.encode(nextRecord)
        init {
            require(generation > 0 && validHash(previousAnchorDigest) &&
                (previousProfile == null) == (previousProfileDigest == null) &&
                (previousRecord == null) == (previousRecordDigest == null) &&
                (generation == 1L) == (previousCommitDigest == null) &&
                (previousRecord == null) == (generation == 1L)) { "STORAGE_METADATA_INVALID" }
            listOf(previousProfileDigest, previousRecordDigest, previousCommitDigest).filterNotNull().forEach {
                require(validHash(it)) { "STORAGE_METADATA_INVALID" }
            }
            previousProfile?.let { StorageCapture.from(installation, it) }
            previousRecord?.matchesCapture(StorageCapture.from(installation, checkNotNull(previousProfile)))
            require(previousAnchorDigest == hash(anchor(generation - 1, previousCommitDigest, pending = true, intent = null))) {
                "STORAGE_METADATA_INVALID"
            }
            if (previousRecord != null) require(previousRecord.sha256() == previousRecordDigest &&
                hash(ConnectionProfileV2Codec.encode(checkNotNull(previousProfile))) == previousProfileDigest) { "STORAGE_METADATA_INVALID" }
            checkedNext(previousProfile, nextProfile)
            val nextCapture = StorageCapture.from(installation, nextProfile)
            nextRecord.matchesCapture(nextCapture)
            if (previousRecord != null) require(previousRecord.legacyInstallation == nextRecord.legacyInstallation &&
                previousRecord.installationId == nextRecord.installationId &&
                ((previousRecord.profileStorageId == nextRecord.profileStorageId) ==
                    (previousRecord.hostUserBinding == nextRecord.hostUserBinding))) { "STORAGE_STALE" }
            // Stored metadata checks only value relations, never fabricates live reclamation evidence.
        }
        fun encode(): ByteArray = metadata(mapOf(
            "schema" to 1, "kind" to TRANSACTION_KIND, "legacy_installation" to installation,
            "generation" to generation.toString(), "previous_anchor_sha256" to previousAnchorDigest,
            "previous_commit_sha256" to previousCommitDigest,
            "previous_profile" to previousProfile?.toFields(), "previous_profile_sha256" to previousProfileDigest,
            "previous_record" to previousRecord?.toFields(), "previous_record_sha256" to previousRecordDigest,
            "next_profile" to nextProfile.toFields(), "next_record" to nextRecord.toFields(),
        ))
    }

    private fun decodeTransaction(bytes: ByteArray): Transaction {
        val value = fields(bytes, setOf("schema", "kind", "legacy_installation", "generation", "previous_anchor_sha256",
            "previous_commit_sha256", "previous_profile", "previous_profile_sha256", "previous_record", "previous_record_sha256",
            "next_profile", "next_record"), TRANSACTION_KIND)
        fun profile(v: Any?) = LinuxConnectionProfile.decode(LinuxStorageJson.canonical(v), installation)
        fun record(v: Any?) = LinuxStorageIdentityCodec.decode(LinuxStorageJson.canonical(v))
        return Transaction(decimal(value["generation"], zero = false), checkNotNull(nullableHash(value["previous_anchor_sha256"])),
            nullableHash(value["previous_commit_sha256"]), value["previous_profile"]?.let(::profile),
            nullableHash(value["previous_profile_sha256"]), value["previous_record"]?.let(::record),
            nullableHash(value["previous_record_sha256"]), profile(value["next_profile"]), record(value["next_record"]))
    }

    private fun commit(transaction: Transaction, bytes: ByteArray): ByteArray = metadata(mapOf(
        "schema" to 1, "kind" to COMMIT_KIND, "legacy_installation" to installation,
        "generation" to transaction.generation.toString(), "transaction_sha256" to hash(bytes),
        "profile_sha256" to hash(transaction.nextProfileBytes), "record_sha256" to hash(transaction.nextRecordBytes),
    ))

    private fun metadata(value: Map<String, Any?>): ByteArray = LinuxStorageJson.canonical(value).also {
        require(it.size in 1..LinuxStorageIdentityCodec.MAX_BYTES) { "STORAGE_METADATA_INVALID" }
    }
    private fun fields(bytes: ByteArray, keys: Set<String>, kind: String): Map<*, *> {
        val value = LinuxStorageJson.parse(bytes) as? Map<*, *> ?: throw IllegalArgumentException("STORAGE_METADATA_INVALID")
        require(value.keys == keys && value["schema"] == 1L && value["kind"] == kind &&
            value["legacy_installation"] == installation) { "STORAGE_METADATA_INVALID" }
        return value
    }
    private fun validHash(value: String) = value.matches(Regex("[0-9a-f]{64}"))
    private fun nullableHash(value: Any?): String? {
        if (value == null) return null
        require(value is String && validHash(value)) { "STORAGE_METADATA_INVALID" }
        return value
    }
    private fun decimal(value: Any?, zero: Boolean): Long {
        require(value is String && value.matches(if (zero) Regex("0|[1-9][0-9]{0,18}") else Regex("[1-9][0-9]{0,18}"))) {
            "STORAGE_METADATA_INVALID"
        }
        return checkNotNull(value.toLongOrNull()) { "STORAGE_METADATA_INVALID" }
    }
}
