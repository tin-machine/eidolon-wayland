package io.github.tinmachine.eidolon

import android.content.Context

/** Fixed noBackup paths only. All mutation runs inside the Host's reclaimed admission epoch. */
class LinuxDesktopSettingsStore(context: Context, installation: String) {
    private val root = AndroidLinuxStorageFiles(context.noBackupFilesDir)
    private val profiles = AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-profile")
    private val storageFiles = AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-storage-v3")
    private val configurationFiles = AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-desktop-v3")
    private val desktopFiles = AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-desktop")
    private val repository = LinuxDesktopSettingsRepository(installation,
        LinuxStorageRepository(installation, root, profiles, storageFiles), profiles, configurationFiles, desktopFiles)

    fun load(): LinuxDesktopSettingsRepository.Snapshot = repository.load()
    fun prepare(captureToken: String, draft: LinuxConnectionDraft?, backend: LinuxDesktopBackend, raw: String,
                confirmed: Boolean): LinuxDesktopSettingsRepository.SaveRequest =
        repository.prepare(captureToken, draft, backend, raw, confirmed)
    fun prepareProfile(captureToken: String, draft: LinuxConnectionDraft, confirmed: Boolean) =
        repository.prepareProfile(captureToken, draft, confirmed)
    fun save(request: LinuxDesktopSettingsRepository.SaveRequest, barrier: LinuxStorageRepository.ReclamationBarrier) =
        repository.save(request, barrier)
    fun resume(expectedCaptureToken: String, barrier: LinuxStorageRepository.ReclamationBarrier) =
        repository.resume(expectedCaptureToken, barrier)
}
