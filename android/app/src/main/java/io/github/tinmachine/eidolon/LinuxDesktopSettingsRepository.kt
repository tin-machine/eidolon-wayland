package io.github.tinmachine.eidolon

/** I5's file owner. Runtime/SDK reclamation is supplied by the Host, never inferred from disk. */
class LinuxDesktopSettingsRepository internal constructor(
    private val installation: String,
    private val storage: LinuxStorageRepository,
    private val profileFiles: LinuxStorageFiles,
    private val configurationFiles: LinuxStorageFiles,
    private val desktopFiles: LinuxStorageFiles,
) {
    enum class Status { UNCONFIGURED, LEGACY_V2, READY_V2, READY_V3, SAVE_PENDING, BLOCKED }

    class Snapshot internal constructor(
        val status: Status,
        val captureToken: String?,
        val profile: ConnectionProfileV2.Value? = null,
        val v2Pins: LinuxDesktopPinsRepository.Saved? = null,
        val v3Configuration: LinuxDesktopV3ConfigRepository.Saved? = null,
        val storageRecord: LinuxStorageRecord? = null,
        val backend: LinuxDesktopBackend? = null,
        val reason: String? = null,
        val canResume: Boolean = false,
        val legacyAllowed: Boolean = false,
        /** Exact durable settings intent identity; never an old Runtime's retirement authority. */
        val intentToken: String? = null,
    ) {
        val ready: Boolean get() = status in setOf(Status.LEGACY_V2, Status.READY_V2, Status.READY_V3)
        fun activeRawConfig(): String? = if (!ready) null else when (backend) {
            LinuxDesktopBackend.V2 -> v2Pins?.pins?.json()
            LinuxDesktopBackend.V3 -> v3Configuration?.configuration?.json()
            null -> null
        }
        override fun toString() = "LinuxDesktopSettingsSnapshot($status)"
    }

    class SaveRequest internal constructor(
        val captureToken: String,
        val nextProfile: ConnectionProfileV2.Value,
        val backend: LinuxDesktopBackend,
        internal val expectedSnapshotToken: String,
        internal val expectedIntentToken: String?,
        internal val intent: Intent,
    ) {
        val intentToken: String = LinuxStorageRepository.hash(intent.encode())
        override fun toString() = "LinuxDesktopSettingsSaveRequest(<private>)"
    }

    class SaveFailure internal constructor(val reloaded: Snapshot) : Exception("SETTINGS_SAVE_FAILED")

    private val configurations = LinuxDesktopV3ConfigRepository(configurationFiles)
    private val markers = LinuxDesktopActiveBackendRepository(desktopFiles)

    fun load(): Snapshot = LinuxStorageIo.serialized { reload() }

    /** Parse imported raw bytes before any object reserialization can discard duplicate keys. */
    fun prepare(captureToken: String, draft: LinuxConnectionDraft?, backend: LinuxDesktopBackend,
                raw: String, confirmed: Boolean): SaveRequest = LinuxStorageIo.serialized {
        require(confirmed) { "PIN_CONFIRMATION_REQUIRED" }
        val config = if (backend == LinuxDesktopBackend.V3) LinuxDesktopV3Config.parse(raw) else null
        val pins = if (backend == LinuxDesktopBackend.V2) LinuxDesktopPins.parse(raw).let {
            LinuxDesktopPins.parse(it.canonical().toString(Charsets.UTF_8))
        } else null
        val data = readData()
        val snapshot = classify(data)
        require(snapshot.status != Status.BLOCKED && snapshot.status != Status.SAVE_PENDING &&
            snapshot.captureToken == captureToken) { "SETTINGS_STALE" }
        val previous = stableCapture(data.storageState)
        val enroll = backend == LinuxDesktopBackend.V3 && previous.record == null
        val next = if (draft != null || enroll) LinuxConnectionProfile.propose(
            draft ?: draftOf(checkNotNull(previous.profile) { "PROFILE_REQUIRED" }), installation, previous.profile)
        else checkNotNull(previous.profile) { "PROFILE_REQUIRED" }
        val updateProfile = previous.profile?.toFields() != next.toFields()
        val updateStorage = enroll || (previous.record != null && updateProfile)
        val targetConfig = config?.let { LinuxStorageJson.parse(it.canonical(), LinuxDesktopV3Config.MAX_BYTES) }
            ?: checkNotNull(pins).let { LinuxStorageJson.parse(it.canonical()) }
        val requestToken = LinuxStorageRepository.hash(boundedSettingsBytes(mapOf(
            "domain" to "eidolon-linux-settings-request-v1", "snapshot" to captureToken,
            "profile" to next.toFields(), "backend" to backend.protocolRevision, "configuration" to targetConfig)))
        val intent = Intent(installation, requestToken, previous.token, previous.profile, previous.profileDigest,
            previous.record, next, backend, config, pins, updateStorage,
            data.config?.token, data.pins?.token, data.marker?.token)
        // Check the complete durable envelope before requesting Runtime side effects.
        intent.encode()
        SaveRequest(requestToken, next, backend, captureToken, data.intentToken, intent)
    }

    fun prepareProfile(captureToken: String, draft: LinuxConnectionDraft, confirmed: Boolean): SaveRequest =
        LinuxStorageIo.serialized {
            val snapshot = load()
            require(snapshot.captureToken == captureToken && snapshot.ready) { "SETTINGS_STALE" }
            prepare(captureToken, draft, checkNotNull(snapshot.backend), checkNotNull(snapshot.activeRawConfig()), confirmed)
        }

    /** The callback is entered only after the Host has its actual same-owner StopReceipt. */
    fun save(request: SaveRequest, barrier: LinuxStorageRepository.ReclamationBarrier): Snapshot = attempt {
        barrier.withReclaimed(request.captureToken) { evidence -> LinuxStorageIo.serialized {
            checkEvidence(request.captureToken, evidence)
            val data = readData()
            val snapshot = classify(data)
            require(snapshot.captureToken == request.expectedSnapshotToken && snapshot.status != Status.BLOCKED &&
                snapshot.status != Status.SAVE_PENDING && data.intentToken == request.expectedIntentToken) { "SETTINGS_STALE" }
            val bytes = request.intent.encode()
            publishSettingsFile(configurationFiles, TRANSACTION, data.intentBytes, request.expectedIntentToken, bytes)
            finish(request.intent, request.intentToken, evidence)
        } }
    }

    /** Explicit roll-forward of one durable intent. No propose(), fresh IDs, or revision increment. */
    fun resume(expectedCaptureToken: String, barrier: LinuxStorageRepository.ReclamationBarrier): Snapshot = attempt {
        barrier.withReclaimed(expectedCaptureToken) { evidence -> LinuxStorageIo.serialized {
            checkEvidence(expectedCaptureToken, evidence)
            val data = readData()
            val snapshot = classify(data)
            require(snapshot.captureToken == expectedCaptureToken && data.intent != null &&
                (snapshot.canResume || snapshot.ready)) { "SETTINGS_STALE" }
            finish(data.intent, checkNotNull(data.intentToken), evidence)
        } }
    }

    private fun <T> attempt(action: () -> T): T = try { action() }
        catch (failure: SaveFailure) { throw failure }
        catch (_: Exception) { throw SaveFailure(load()) }

    private fun checkEvidence(token: String, evidence: LinuxStorageRepository.ReclamationEvidence) {
        require(evidence.captureToken == token && evidence.fullyReclaimed) { "STORAGE_RECLAMATION_REQUIRED" }
    }

    private fun finish(intent: Intent, intentToken: String, evidence: LinuxStorageRepository.ReclamationEvidence): Snapshot {
        require(configurationFiles.read(TRANSACTION)?.let { LinuxStorageRepository.hash(it) } == intentToken) { "SETTINGS_STALE" }
        val initial = readData()
        require(initial.intentToken == intentToken && compatible(initial, intent)) { "SETTINGS_STALE" }
        val pair = completeProfile(intent, initial.storageState, evidence)
        val profile = checkNotNull(pair.profile)
        val record = pair.record
        require(profile.toFields() == intent.nextProfile.toFields()) { "SETTINGS_READBACK_INVALID" }
        if (intent.backend == LinuxDesktopBackend.V3) {
            configurations.publish(profile, checkNotNull(record), checkNotNull(intent.configuration), intent.expectedConfigToken)
        } else {
            val target = LinuxDesktopPinsRepository.encode(profile, checkNotNull(intent.pins))
            publishSettingsFile(desktopFiles, PINS, desktopFiles.read(PINS), intent.expectedPinsToken, target)
        }
        val digest = intent.configuration?.sha256() ?: checkNotNull(intent.pins).sha256()
        markers.publish(intent.backend, profile, digest,
            record.takeIf { intent.backend == LinuxDesktopBackend.V3 }, intent.expectedMarkerToken)
        val finalData = readData()
        require(finalData.intentToken == intentToken) { "SETTINGS_READBACK_INVALID" }
        val result = classify(finalData)
        require(result.ready && result.backend == intent.backend && result.profile?.toFields() == intent.nextProfile.toFields()) {
            "SETTINGS_READBACK_INVALID"
        }
        return result
    }

    private fun completeProfile(intent: Intent, state: LinuxStorageRepository.State,
                                evidence: LinuxStorageRepository.ReclamationEvidence): LinuxStorageRepository.Capture {
        when (state) {
            is LinuxStorageRepository.State.Pending -> {
                require(intent.updateStorage && pendingMatches(state.capture, intent)) { "SETTINGS_STALE" }
                val pending = state.capture
                val request = LinuxStorageRepository.ResumeRequest(installation, pending, pending.previousProfile, pending.nextProfile,
                    pending.previousProfile?.let { StorageCapture.from(installation, it).hostUserBinding },
                    pending.nextRecord.hostUserBinding, pending.previousRecord?.sha256(), pending.transactionDigest, true)
                return storage.resumePending(request, storageAlias(pending.token, evidence)).capture
            }
            is LinuxStorageRepository.State.Blocked -> error("SETTINGS_INCONSISTENT")
            else -> {
                val capture = stableCapture(state)
                if (matchesNext(capture, intent)) return capture
                require(capture.token == intent.oldStorageToken && sameProfile(capture.profile, intent.previousProfile) &&
                    capture.record?.sha256() == intent.previousRecord?.sha256()) { "SETTINGS_STALE" }
                if (intent.updateStorage) {
                    val request = LinuxStorageRepository.SaveRequest(installation, capture, capture.profile, intent.nextProfile,
                        capture.oldHostUserBinding, StorageCapture.from(installation, intent.nextProfile).hostUserBinding,
                        capture.expectedRecordDigest, true)
                    return storage.save(request, storageAlias(capture.token, evidence)).capture
                }
                require(state is LinuxStorageRepository.State.Uninitialized && intent.previousRecord == null) { "SETTINGS_STALE" }
                val bytes = profileFiles.read(LinuxStorageRepository.PROFILE)
                require(bytes?.let { LinuxStorageRepository.hash(it) } == intent.previousProfileDigest) { "SETTINGS_STALE" }
                publishSettingsFile(profileFiles, LinuxStorageRepository.PROFILE, bytes, intent.previousProfileDigest,
                    ConnectionProfileV2Codec.encode(intent.nextProfile))
                return stableCapture(storage.load(installation)).also { require(matchesNext(it, intent)) { "SETTINGS_READBACK_INVALID" } }
            }
        }
    }

    /** Explicit subordinate capture authority, available only while the enclosing barrier is held.
     * The caller has matched old/next durable intent above; arbitrary caller tokens are rejected. */
    private fun storageAlias(expectedToken: String, outer: LinuxStorageRepository.ReclamationEvidence) =
        object : LinuxStorageRepository.ReclamationBarrier {
            override fun <T> withReclaimed(captureToken: String, action: (LinuxStorageRepository.ReclamationEvidence) -> T): T {
                require(captureToken == expectedToken && outer.fullyReclaimed) { "STORAGE_RECLAMATION_REQUIRED" }
                return action(LinuxStorageRepository.ReclamationEvidence(captureToken, outer.stoppedRuntime,
                    outer.shutdownComplete, outer.nativeDestroyAccepted, outer.ownedConnections, outer.pendingWorkers,
                    outer.nativeTargets, outer.panels, outer.sdkBindings, outer.boardWindows, outer.pendingOperations,
                    outer.remoteCleanupPending))
            }
        }

    private fun reload(): Snapshot = try { classify(readData()) }
        catch (_: Exception) { Snapshot(Status.BLOCKED, null, reason = "SETTINGS_INCONSISTENT") }

    private class Data(
        val storageState: LinuxStorageRepository.State,
        val configurationDirectory: Boolean,
        val config: LinuxDesktopV3ConfigRepository.Saved?,
        val pins: LinuxDesktopPinsRepository.Saved?,
        val marker: LinuxDesktopActiveBackendRepository.Marker?,
        val intentBytes: ByteArray?,
        val intent: Intent?,
    ) {
        val intentToken = intentBytes?.let { LinuxStorageRepository.hash(it) }
        val token: String = LinuxStorageRepository.hash(boundedSettingsBytes(mapOf(
            "domain" to "eidolon-linux-settings-capture-v1", "storage" to when (storageState) {
                is LinuxStorageRepository.State.Uninitialized -> storageState.capture.token
                is LinuxStorageRepository.State.Matched -> storageState.capture.token
                is LinuxStorageRepository.State.Pending -> storageState.capture.token
                is LinuxStorageRepository.State.Blocked -> "blocked"
            }, "configuration_directory" to configurationDirectory, "configuration" to config?.token,
            "pins" to pins?.token, "marker" to marker?.token, "intent" to intentToken)))
    }

    private fun readData(): Data {
        val state = storage.load(installation)
        require(state !is LinuxStorageRepository.State.Blocked) { "SETTINGS_INCONSISTENT" }
        val exists = configurationFiles.exists()
        val config = configurations.load()
        val pins = desktopFiles.read(PINS)?.let(LinuxDesktopPinsRepository::decode)
        val marker = markers.load()
        val intentBytes = configurationFiles.read(TRANSACTION)
        return Data(state, exists, config, pins, marker, intentBytes, intentBytes?.let { Intent.decode(it, installation) })
    }

    private fun classify(data: Data): Snapshot {
        val profile = when (val state = data.storageState) {
            is LinuxStorageRepository.State.Uninitialized -> state.capture.profile
            is LinuxStorageRepository.State.Matched -> state.capture.profile
            is LinuxStorageRepository.State.Pending -> state.capture.nextProfile
            is LinuxStorageRepository.State.Blocked -> null
        }
        val record = (data.storageState as? LinuxStorageRepository.State.Matched)?.capture?.record
        if (data.intent == null) {
            requireLegacyWritesAllowed(data.storageState, data.configurationDirectory, data.marker != null)
            val ready = data.pins?.forProfile(profile) != null
            return Snapshot(if (ready) Status.LEGACY_V2 else Status.UNCONFIGURED, data.token, profile, data.pins,
                data.config, record, LinuxDesktopBackend.V2, if (ready) null else "DESKTOP_PINS_REQUIRED", legacyAllowed = true)
        }
        require(compatible(data, data.intent)) { "SETTINGS_INCONSISTENT" }
        val next = (data.storageState as? LinuxStorageRepository.State.Matched)?.capture
            ?: (data.storageState as? LinuxStorageRepository.State.Uninitialized)?.capture
        val finished = next != null && matchesNext(next, data.intent) && targetFilesMatch(data, data.intent, next)
        return Snapshot(if (finished) {
            if (data.intent.backend == LinuxDesktopBackend.V3) Status.READY_V3 else Status.READY_V2
        } else Status.SAVE_PENDING, data.token, profile, data.pins, data.config, record, data.intent.backend,
            if (finished) null else "SETTINGS_SAVE_PENDING", canResume = !finished, intentToken = data.intentToken)
    }

    private fun compatible(data: Data, intent: Intent): Boolean {
        val capture = when (val state = data.storageState) {
            is LinuxStorageRepository.State.Pending -> {
                if (!intent.updateStorage || !pendingMatches(state.capture, intent)) return false
                null
            }
            is LinuxStorageRepository.State.Blocked -> return false
            else -> stableCapture(state)
        }
        if (capture != null && capture.token != intent.oldStorageToken && !matchesNext(capture, intent)) return false
        val nextRecord = when (val state = data.storageState) {
            is LinuxStorageRepository.State.Matched -> state.capture.record.takeIf { matchesNext(state.capture, intent) }
            is LinuxStorageRepository.State.Pending -> state.capture.nextRecord
            else -> null
        }
        val configTarget = if (intent.backend == LinuxDesktopBackend.V3 && nextRecord != null)
            LinuxStorageRepository.hash(LinuxDesktopV3ConfigRepository.encode(intent.nextProfile, nextRecord, checkNotNull(intent.configuration))) else null
        val pinsTarget = if (intent.backend == LinuxDesktopBackend.V2)
            LinuxStorageRepository.hash(LinuxDesktopPinsRepository.encode(intent.nextProfile, checkNotNull(intent.pins))) else null
        val markerTarget = if (intent.backend == LinuxDesktopBackend.V2 || nextRecord != null)
            LinuxStorageRepository.hash(LinuxDesktopActiveBackendRepository.encode(intent.backend, intent.nextProfile,
                intent.configuration?.sha256() ?: checkNotNull(intent.pins).sha256(),
                nextRecord.takeIf { intent.backend == LinuxDesktopBackend.V3 })) else null
        return allowed(data.config?.token, intent.expectedConfigToken, configTarget) &&
            allowed(data.pins?.token, intent.expectedPinsToken, pinsTarget) &&
            allowed(data.marker?.token, intent.expectedMarkerToken, markerTarget)
    }

    private fun targetFilesMatch(data: Data, intent: Intent, capture: LinuxStorageRepository.Capture): Boolean {
        val profile = checkNotNull(capture.profile)
        val marker = data.marker ?: return false
        val configDigest = if (intent.backend == LinuxDesktopBackend.V3) {
            val config = data.config?.forProfile(profile, capture.record) ?: return false
            if (config.sha256() != intent.configuration?.sha256()) return false
            config.sha256()
        } else {
            val pins = data.pins?.forProfile(profile) ?: return false
            if (pins.sha256() != intent.pins?.sha256()) return false
            pins.sha256()
        }
        return marker.backend == intent.backend && marker.profileBinding == profile.binding() &&
            marker.configurationDigest == configDigest &&
            marker.storageRecordDigest == capture.record?.sha256().takeIf { intent.backend == LinuxDesktopBackend.V3 }
    }

    private fun matchesNext(capture: LinuxStorageRepository.Capture, intent: Intent): Boolean {
        if (!sameProfile(capture.profile, intent.nextProfile)) return false
        val record = capture.record
        if (!intent.updateStorage) return record?.sha256() == intent.previousRecord?.sha256()
        if (record == null) return false
        return try {
            record.matchesCapture(StorageCapture.from(installation, intent.nextProfile))
            intent.previousRecord?.let { old ->
                require(record.installationId == old.installationId &&
                    (record.profileStorageId == old.profileStorageId) == (record.hostUserBinding == old.hostUserBinding))
            }
            true
        } catch (_: Exception) { false }
    }

    private fun pendingMatches(pending: LinuxStorageRepository.PendingCapture, intent: Intent) =
        sameProfile(pending.previousProfile, intent.previousProfile) && sameProfile(pending.nextProfile, intent.nextProfile) &&
            pending.previousRecord?.sha256() == intent.previousRecord?.sha256()

    internal class Intent(
        val installation: String,
        val sourceCaptureToken: String,
        val oldStorageToken: String,
        val previousProfile: ConnectionProfileV2.Value?,
        val previousProfileDigest: String?,
        val previousRecord: LinuxStorageRecord?,
        val nextProfile: ConnectionProfileV2.Value,
        val backend: LinuxDesktopBackend,
        val configuration: LinuxDesktopV3Config?,
        val pins: LinuxDesktopPins?,
        val updateStorage: Boolean,
        val expectedConfigToken: String?,
        val expectedPinsToken: String?,
        val expectedMarkerToken: String?,
    ) {
        init {
            settingsHash(sourceCaptureToken); settingsHash(oldStorageToken)
            listOf(previousProfileDigest, expectedConfigToken, expectedPinsToken, expectedMarkerToken).filterNotNull().forEach(::settingsHash)
            require((previousProfile == null) == (previousProfileDigest == null)) { "SETTINGS_INCONSISTENT" }
            previousRecord?.matchesCapture(StorageCapture.from(installation, checkNotNull(previousProfile)))
            LinuxStorageIdentity.checkedProfile(installation, nextProfile)
            if (!sameProfile(previousProfile, nextProfile)) {
                if (previousProfile == null) require(nextProfile.revision == "1") { "PROFILE_INVALID" }
                else previousProfile.replacement(previousProfile.binding(), nextProfile.toFields(), true)
            }
            require((backend == LinuxDesktopBackend.V3) == (configuration != null) &&
                (backend == LinuxDesktopBackend.V2) == (pins != null)) { "SETTINGS_INCONSISTENT" }
            require(updateStorage == ((backend == LinuxDesktopBackend.V3 && previousRecord == null) ||
                (previousRecord != null && !sameProfile(previousProfile, nextProfile)))) { "SETTINGS_INCONSISTENT" }
        }
        fun encode(): ByteArray = boundedSettingsBytes(mapOf(
            "schema" to 1, "kind" to KIND, "installation" to installation, "source_capture_token" to sourceCaptureToken,
            "old_storage_token" to oldStorageToken, "previous_profile" to previousProfile?.toFields(),
            "previous_profile_sha256" to previousProfileDigest, "previous_record" to previousRecord?.toFields(),
            "next_profile" to nextProfile.toFields(), "protocol_revision" to backend.protocolRevision,
            "configuration" to (configuration?.let { LinuxStorageJson.parse(it.canonical(), LinuxDesktopV3Config.MAX_BYTES) }
                ?: LinuxStorageJson.parse(checkNotNull(pins).canonical())), "update_storage" to updateStorage,
            "expected_configuration_sha256" to expectedConfigToken, "expected_pins_sha256" to expectedPinsToken,
            "expected_marker_sha256" to expectedMarkerToken))
        companion object {
            private const val KIND = "eidolon-linux-settings-transaction-v1"
            fun decode(bytes: ByteArray, installation: String): Intent {
                val fields = settingsFields(bytes, setOf("schema", "kind", "installation", "source_capture_token", "old_storage_token",
                    "previous_profile", "previous_profile_sha256", "previous_record", "next_profile", "protocol_revision", "configuration",
                    "update_storage", "expected_configuration_sha256", "expected_pins_sha256", "expected_marker_sha256"))
                require(fields["kind"] == KIND && fields["installation"] == installation) { "SETTINGS_INCONSISTENT" }
                fun profile(value: Any?) = LinuxConnectionProfile.decode(LinuxStorageJson.canonical(value), installation)
                val backend = LinuxDesktopBackend.parse(fields["protocol_revision"])
                val raw = LinuxStorageJson.canonical(fields["configuration"])
                return Intent(installation, settingsHash(fields["source_capture_token"]), settingsHash(fields["old_storage_token"]),
                    fields["previous_profile"]?.let(::profile), fields["previous_profile_sha256"]?.let(::settingsHash),
                    fields["previous_record"]?.let { LinuxStorageIdentityCodec.decode(LinuxStorageJson.canonical(it)) },
                    profile(fields["next_profile"]), backend,
                    if (backend == LinuxDesktopBackend.V3) LinuxDesktopV3Config.parse(raw) else null,
                    if (backend == LinuxDesktopBackend.V2) LinuxDesktopPins.parse(raw.toString(Charsets.UTF_8)) else null,
                    fields["update_storage"] as? Boolean ?: error("SETTINGS_INCONSISTENT"),
                    fields["expected_configuration_sha256"]?.let(::settingsHash), fields["expected_pins_sha256"]?.let(::settingsHash),
                    fields["expected_marker_sha256"]?.let(::settingsHash))
            }
        }
    }

    companion object {
        internal const val TRANSACTION = "transaction.json"
        internal const val PINS = "desktop-pins.json"
        internal fun requireLegacyWritesAllowed(state: LinuxStorageRepository.State, configurationDirectory: Boolean, markerPresent: Boolean) {
            require(state is LinuxStorageRepository.State.Uninitialized && !configurationDirectory && !markerPresent) { "SETTINGS_MANAGED_WRITE_REQUIRED" }
        }
        private fun stableCapture(state: LinuxStorageRepository.State): LinuxStorageRepository.Capture = when (state) {
            is LinuxStorageRepository.State.Uninitialized -> state.capture
            is LinuxStorageRepository.State.Matched -> state.capture
            else -> error("SETTINGS_INCONSISTENT")
        }
        private fun allowed(actual: String?, previous: String?, target: String?) = actual == previous || (target != null && actual == target)
        private fun sameProfile(a: ConnectionProfileV2.Value?, b: ConnectionProfileV2.Value?) =
            if (a == null || b == null) a == null && b == null else a.toFields() == b.toFields()
        private fun draftOf(profile: ConnectionProfileV2.Value) = LinuxConnectionDraft(profile.displayName, profile.host,
            profile.port.toString(), profile.username, profile.hostKey)
    }
}
