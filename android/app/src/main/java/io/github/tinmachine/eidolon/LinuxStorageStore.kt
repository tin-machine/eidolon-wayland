package io.github.tinmachine.eidolon

import android.content.Context

/** Real E1 noBackup store. I5 composes this repository contract with settings activation. */
class LinuxStorageStore(context: Context, currentDefaultInstallation: String) {
    private val repository = LinuxStorageRepository(currentDefaultInstallation,
        AndroidLinuxStorageFiles(context.noBackupFilesDir),
        AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-profile"),
        AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-storage-v3"))

    fun load(currentDefaultInstallation: String): LinuxStorageRepository.State = repository.load(currentDefaultInstallation)
    fun currentState(): LinuxStorageRepository.State = repository.currentState()
    fun save(request: LinuxStorageRepository.SaveRequest, barrier: LinuxStorageRepository.ReclamationBarrier):
        LinuxStorageRepository.State.Matched = repository.save(request, barrier)
    fun resumePending(request: LinuxStorageRepository.ResumeRequest, barrier: LinuxStorageRepository.ReclamationBarrier):
        LinuxStorageRepository.State.Matched = repository.resumePending(request, barrier)
}
