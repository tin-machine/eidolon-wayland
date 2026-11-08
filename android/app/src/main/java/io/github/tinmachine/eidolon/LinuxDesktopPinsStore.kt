package io.github.tinmachine.eidolon

import android.content.Context
import java.io.File

/** worker所有。C1と同じnofollow/CAS/fsync/readback境界で、設定専用pinsを保存する。 */
class LinuxDesktopPinsStore(context: Context) {
    private val repository = LinuxDesktopPinsRepository(
        LinuxProfileStore.AtomicFile(File(context.noBackupFilesDir, "linux-desktop"), "desktop-pins"))
    fun load(): LinuxDesktopPinsRepository.Saved? = repository.load()
    fun save(profile: ConnectionProfileV2.Value, raw: String, expectedToken: String?, confirmed: Boolean) =
        repository.save(profile, raw, expectedToken, confirmed)
}
