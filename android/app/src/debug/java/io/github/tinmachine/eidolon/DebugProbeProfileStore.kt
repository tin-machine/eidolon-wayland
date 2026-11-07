package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Process
import android.system.Os
import android.system.OsConstants
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream

/** rootが確認した同UIDの固定staging fileだけを明示import。v1設定/journalは扱わない。 */
class DebugProbeProfileStore(context: Context) {
    private val directory = File(context.noBackupFilesDir, "remote-probe")
    private val staged = File(directory, "linux-probe.pending.json")
    private val saved = File(directory, "linux-probe.json")
    @Synchronized fun importConfirmed(installation: String): ConnectionProfileV2.Value {
        checkDirectory()
        val next = ConnectionProfileV2Codec.decode(read(staged), installation)
        require(next.profileId == "linux-probe" && next.backend == ConnectionProfileV2.Backend.LINUX_SSH &&
            next.host.split('.').let { it.size == 4 && it.all { part -> part.toIntOrNull() in 0..255 } }) { "PROBE_PROFILE_INVALID" }
        val previous = if (java.nio.file.Files.exists(saved.toPath(), java.nio.file.LinkOption.NOFOLLOW_LINKS)) ConnectionProfileV2Codec.decode(read(saved), installation) else null
        if (previous != null && previous.toFields() == next.toFields()) return previous
        if (previous != null) previous.replacement(previous.binding(), next.toFields(), confirmed = true)
        else require(next.revision == "1") { "PROBE_REVISION_INVALID" }
        val bytes = ConnectionProfileV2Codec.encode(next)
        val temporary = File(directory, "linux-probe.tmp")
        // 古いtmpを黙って削除しない。O_EXCLで他owner/symlinkを拒否。
        val fd = Os.open(temporary.absolutePath, OsConstants.O_WRONLY or OsConstants.O_CREAT or OsConstants.O_EXCL or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 384)
        try {
            FileOutputStream(fd).use { it.write(bytes); it.fd.sync() }
            require(ConnectionProfileV2Codec.decode(read(temporary), installation).toFields() == next.toFields()) { "PROBE_READBACK_INVALID" }
            Os.rename(temporary.absolutePath, saved.absolutePath)
            val dir = Os.open(directory.absolutePath, OsConstants.O_RDONLY or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0)
            try { require(OsConstants.S_ISDIR(Os.fstat(dir).st_mode)) { "PROBE_DIRECTORY_INVALID" }; Os.fsync(dir) } finally { Os.close(dir) }
            return ConnectionProfileV2Codec.decode(read(saved), installation).also { require(it.toFields() == next.toFields()) { "PROBE_READBACK_INVALID" } }
        } finally { if (temporary.exists()) temporary.delete() }
    }
    private fun checkDirectory() {
        // mkdirのみ。既存directoryの権限を無条件に書き換えない。
        if (!directory.exists()) Os.mkdir(directory.absolutePath, 448)
        val stat = Os.lstat(directory.absolutePath)
        require(OsConstants.S_ISDIR(stat.st_mode) && stat.st_uid == Process.myUid() && stat.st_mode and 511 == 448) { "PROBE_DIRECTORY_INVALID" }
    }
    private fun read(file: File): ByteArray {
        val fd = Os.open(file.absolutePath, OsConstants.O_RDONLY or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0)
        return FileInputStream(fd).use { stream ->
            val stat = Os.fstat(fd)
            require(OsConstants.S_ISREG(stat.st_mode) && stat.st_uid == Process.myUid() && stat.st_nlink == 1L &&
                stat.st_mode and 511 == 384 && stat.st_size in 1..ConnectionProfileV2Codec.MAX_BYTES.toLong()) { "PROBE_FILE_INVALID" }
            val bytes = ByteArray(stat.st_size.toInt())
            var offset = 0
            while (offset < bytes.size) { val count = stream.read(bytes, offset, bytes.size - offset); require(count > 0) { "PROBE_FILE_CHANGED" }; offset += count }
            require(stream.read() == -1 && Os.fstat(fd).let { it.st_size == stat.st_size && it.st_mtime == stat.st_mtime && it.st_ctime == stat.st_ctime }) { "PROBE_FILE_CHANGED" }
            bytes
        }
    }
}
