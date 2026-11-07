package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Process
import android.system.Os
import android.system.OsConstants
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.LinkOption

/** operatorが確認した固定stagingだけを明示import。通常設定とTermux journalは変更しない。 */
class DebugLinuxGtkPinStore(context: Context) {
    private val directory = File(context.noBackupFilesDir, "remote-probe")
    private val staged = File(directory, "linux-gtk-pins.pending.json")
    private val saved = File(directory, "linux-gtk-pins.json")
    @Synchronized fun importConfirmed(profile: ConnectionProfileV2.Value): LinuxGtkPins {
        val dirStat = Os.lstat(directory.absolutePath)
        require(OsConstants.S_ISDIR(dirStat.st_mode) && dirStat.st_uid == Process.myUid() && dirStat.st_mode and 511 == 448) { "PROBE_DIRECTORY_INVALID" }
        val next = LinuxGtkPins.decode(read(staged))
        require(next.matches(profile)) { "PROBE_PIN_SCOPE_MISMATCH" }
        val bytes = next.encode()
        if (Files.exists(saved.toPath(), LinkOption.NOFOLLOW_LINKS) && LinuxGtkPins.decode(read(saved)).encode().contentEquals(bytes)) return next
        val temporary = File(directory, "linux-gtk-pins.tmp")
        val fd = Os.open(temporary.absolutePath, OsConstants.O_WRONLY or OsConstants.O_CREAT or OsConstants.O_EXCL or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 384)
        try {
            FileOutputStream(fd).use { it.write(bytes); it.fd.sync() }
            require(LinuxGtkPins.decode(read(temporary)).encode().contentEquals(bytes)) { "PROBE_READBACK_INVALID" }
            Os.rename(temporary.absolutePath, saved.absolutePath)
            val dir = Os.open(directory.absolutePath, OsConstants.O_RDONLY or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0)
            try { require(OsConstants.S_ISDIR(Os.fstat(dir).st_mode)); Os.fsync(dir) } finally { Os.close(dir) }
            return LinuxGtkPins.decode(read(saved)).also { require(it.matches(profile) && it.encode().contentEquals(bytes)) { "PROBE_READBACK_INVALID" } }
        } finally { if (temporary.exists()) temporary.delete() }
    }
    private fun read(file: File): ByteArray {
        val fd = Os.open(file.absolutePath, OsConstants.O_RDONLY or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0)
        return FileInputStream(fd).use { stream ->
            val before = Os.fstat(fd)
            require(OsConstants.S_ISREG(before.st_mode) && before.st_uid == Process.myUid() && before.st_nlink == 1L &&
                before.st_mode and 511 == 384 && before.st_size in 1..LinuxGtkPins.MAX_BYTES.toLong()) { "PROBE_FILE_INVALID" }
            val bytes = ByteArray(before.st_size.toInt())
            var offset = 0
            while (offset < bytes.size) { val n = stream.read(bytes, offset, bytes.size - offset); require(n > 0) { "PROBE_FILE_CHANGED" }; offset += n }
            val after = Os.fstat(fd)
            require(stream.read() == -1 && after.st_size == before.st_size && after.st_mtime == before.st_mtime && after.st_ctime == before.st_ctime) { "PROBE_FILE_CHANGED" }
            bytes
        }
    }
}
