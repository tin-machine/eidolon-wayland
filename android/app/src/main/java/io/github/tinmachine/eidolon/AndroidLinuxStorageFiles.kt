package io.github.tinmachine.eidolon

import android.os.Process
import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import android.system.StructStat
import java.io.File
import java.io.FileDescriptor

/** The factory must pass Context.noBackupFilesDir, never a remote or user-selected pathname. */
internal class AndroidLinuxStorageFiles(
    noBackupDirectory: File,
    directoryName: String? = null,
    maxBytes: Int = 16 * 1024,
    fault: (LinuxStorageIoStage, String) -> Unit = { _, _ -> },
) : LinuxStorageFiles by SecureLinuxStorageFiles(noBackupDirectory, directoryName, AndroidStoragePosix, maxBytes, fault)

private object AndroidStoragePosix : LinuxStoragePosix {
    override val uid: Int get() = Process.myUid()
    override val gid: Int get() = Os.getgid()
    override fun lstat(path: String): LinuxStoragePosix.Stat? = try { stat(Os.lstat(path)) }
        catch (failure: ErrnoException) { if (failure.errno == OsConstants.ENOENT) null else throw failure }
    override fun mkdir(path: String) = Os.mkdir(path, 448)
    override fun openRead(path: String): LinuxStoragePosix.Descriptor = Descriptor(Os.open(path,
        OsConstants.O_RDONLY or OsConstants.O_NONBLOCK or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0))
    override fun createExclusive(path: String): LinuxStoragePosix.Descriptor = Descriptor(Os.open(path,
        OsConstants.O_WRONLY or OsConstants.O_CREAT or OsConstants.O_EXCL or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 384))
    override fun openDirectory(path: String): LinuxStoragePosix.Descriptor {
        // O_DIRECTORY is not public Android SDK API. Verify the opened descriptor instead;
        // O_NONBLOCK prevents a raced FIFO from blocking before its type can be rejected.
        val descriptor = Os.open(path,
            OsConstants.O_RDONLY or OsConstants.O_NONBLOCK or OsConstants.O_NOFOLLOW or OsConstants.O_CLOEXEC, 0)
        try {
            require(OsConstants.S_ISDIR(Os.fstat(descriptor).st_mode)) { "STORAGE_DIRECTORY_INVALID" }
            return Descriptor(descriptor)
        } catch (failure: Throwable) {
            try { Os.close(descriptor) } catch (closeFailure: Throwable) { failure.addSuppressed(closeFailure) }
            throw failure
        }
    }
    override fun rename(source: String, destination: String) = Os.rename(source, destination)
    override fun unlink(path: String) = Os.remove(path)

    private class Descriptor(private val descriptor: FileDescriptor) : LinuxStoragePosix.Descriptor {
        override fun stat() = stat(Os.fstat(descriptor))
        override fun read(bytes: ByteArray, offset: Int, length: Int) = Os.read(descriptor, bytes, offset, length)
        override fun write(bytes: ByteArray, offset: Int, length: Int) = Os.write(descriptor, bytes, offset, length)
        override fun sync() = Os.fsync(descriptor)
        override fun close() = Os.close(descriptor)
    }
    private fun stat(s: StructStat) = LinuxStoragePosix.Stat(s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
        s.st_nlink, s.st_size, s.st_mtim.tv_sec, s.st_mtim.tv_nsec, s.st_ctim.tv_sec, s.st_ctim.tv_nsec)
}
