package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Process
import android.system.Os
import android.system.OsConstants
import android.system.StructStat
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.LinkOption

/** worker専用。秘密鍵を扱わず、既存credentials/debug profile/Termux journalは変更しない。 */
class LinuxProfileStore(context: Context, installation: String) {
    private val repository = LinuxProfileRepository(installation, AtomicFile(File(context.noBackupFilesDir, "linux-profile")))
    fun load(): ConnectionProfileV2.Value? = repository.load()
    fun save(draft: LinuxConnectionDraft, expectedBinding: String?, confirmed: Boolean): ConnectionProfileV2.Value =
        repository.save(draft, expectedBinding, confirmed)

    internal class AtomicFile(private val directory: File, name: String = "remote-linux") : LinuxProfileRepository.Storage {
        private val saved = File(directory, "$name.json")
        private val temporary = File(directory, "$name.tmp")
        init { require(name in setOf("remote-linux", "desktop-pins")) }
        // 別Store instanceも同じatomic境界に入る。UI/ネットワークcallbackはこのlock内で呼ばない。
        companion object { private val lock = LinuxStorageIo.lock }

        override fun read(): ByteArray? = synchronized(lock) {
            checkDirectory()
            if (!exists(saved)) null else readFile(saved)
        }

        override fun publish(expected: ByteArray?, replacement: ByteArray) = synchronized(lock) {
            require(replacement.size in 1..ConnectionProfileV2Codec.MAX_BYTES) { "PROFILE_FILE_INVALID" }
            val directoryIdentity = checkDirectory()
            val before = if (exists(saved)) readFile(saved) else null
            require(equal(before, expected)) { "PROFILE_CHANGED" }
            // 中断したtmpや別ownerを黙って削除/再利用しない。
            val descriptor = Os.open(temporary.absolutePath, OsConstants.O_WRONLY or OsConstants.O_CREAT or
                OsConstants.O_EXCL or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 384)
            val owned = Os.fstat(descriptor)
            var published = false
            try {
                FileOutputStream(descriptor).use { stream -> stream.write(replacement); stream.fd.sync() }
                require(readFile(temporary).contentEquals(replacement)) { "PROFILE_READBACK_INVALID" }
                require(sameFile(directoryIdentity, checkDirectory())) { "PROFILE_DIRECTORY_CHANGED" }
                val current = if (exists(saved)) readFile(saved) else null
                require(equal(current, expected)) { "PROFILE_CHANGED" }
                require(sameFile(owned, Os.lstat(temporary.absolutePath))) { "PROFILE_FILE_CHANGED" }
                Os.rename(temporary.absolutePath, saved.absolutePath)
                published = true
                val dir = Os.open(directory.absolutePath, OsConstants.O_RDONLY or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0)
                try {
                    require(sameFile(directoryIdentity, Os.fstat(dir))) { "PROFILE_DIRECTORY_CHANGED" }
                    Os.fsync(dir)
                } finally { Os.close(dir) }
                require(readFile(saved).contentEquals(replacement)) { "PROFILE_READBACK_INVALID" }
            } finally {
                // 成功publish後の失敗でも古いprofileへrollbackしない。次loadで保存済みrevisionを確認する。
                if (!published && exists(temporary) && sameFile(owned, Os.lstat(temporary.absolutePath))) Files.delete(temporary.toPath())
            }
        }

        private fun checkDirectory(): StructStat {
            if (!exists(directory)) Os.mkdir(directory.absolutePath, 448)
            return Os.lstat(directory.absolutePath).also {
                require(OsConstants.S_ISDIR(it.st_mode) && it.st_uid == Process.myUid() && it.st_mode and 511 == 448) {
                    "PROFILE_DIRECTORY_INVALID"
                }
            }
        }
        private fun readFile(file: File): ByteArray {
            val descriptor = Os.open(file.absolutePath, OsConstants.O_RDONLY or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0)
            return FileInputStream(descriptor).use { stream ->
                val before = Os.fstat(descriptor)
                require(OsConstants.S_ISREG(before.st_mode) && before.st_uid == Process.myUid() && before.st_nlink == 1L &&
                    before.st_mode and 511 == 384 && before.st_size in 1..ConnectionProfileV2Codec.MAX_BYTES.toLong()) { "PROFILE_FILE_INVALID" }
                val bytes = ByteArray(before.st_size.toInt())
                var offset = 0
                while (offset < bytes.size) {
                    val count = stream.read(bytes, offset, bytes.size - offset)
                    require(count > 0) { "PROFILE_FILE_CHANGED" }
                    offset += count
                }
                val after = Os.fstat(descriptor)
                require(stream.read() == -1 && sameFile(before, after) && before.st_size == after.st_size &&
                    before.st_mtime == after.st_mtime && before.st_ctime == after.st_ctime) { "PROFILE_FILE_CHANGED" }
                bytes
            }
        }
        private fun exists(file: File) = Files.exists(file.toPath(), LinkOption.NOFOLLOW_LINKS)
        private fun sameFile(a: StructStat, b: StructStat) = a.st_dev == b.st_dev && a.st_ino == b.st_ino
        private fun equal(a: ByteArray?, b: ByteArray?) = if (a == null || b == null) a == null && b == null else a.contentEquals(b)
    }
}
