package io.github.tinmachine.eidolon

import android.content.Context
import java.io.File

/** worker所有。C1と同じnofollow/CAS/fsync/readback境界で、設定専用pinsを保存する。 */
class LinuxDesktopPinsStore(context: Context) {
    private val repository = LinuxDesktopPinsRepository(
        LinuxProfileStore.AtomicFile(File(context.noBackupFilesDir, "linux-desktop"), "desktop-pins"))
    private val root = AndroidLinuxStorageFiles(context.noBackupFilesDir)
    private val storageFiles = AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-storage-v3")
    private val configurationFiles = AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-desktop-v3")
    private val desktopFiles = AndroidLinuxStorageFiles(context.noBackupFilesDir, "linux-desktop")
    fun load(): LinuxDesktopPinsRepository.Saved? = repository.load()
    fun save(profile: ConnectionProfileV2.Value, raw: String, expectedToken: String?, confirmed: Boolean) =
        LinuxStorageIo.serialized {
            // Managed settings must update the active marker under the same full Runtime barrier.
            // Any failed read or partial witness rejects, rather than becoming legacy absence.
            require(root.read(LinuxStorageRepository.ANCHOR) == null && !storageFiles.exists() &&
                !configurationFiles.exists() && desktopFiles.read(LinuxDesktopActiveBackendRepository.FILE) == null) {
                "SETTINGS_MANAGED_WRITE_REQUIRED"
            }
            repository.save(profile, raw, expectedToken, confirmed)
        }
}
