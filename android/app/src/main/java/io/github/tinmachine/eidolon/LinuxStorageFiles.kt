package io.github.tinmachine.eidolon

import java.io.Closeable
import java.io.File

/** One process only. Legacy profile/pins and all E1 Store instances share this reentrant lock. */
internal object LinuxStorageIo {
    val lock = Any()
    fun <T> serialized(action: () -> T): T = synchronized(lock, action)
}

/** A verified noBackup root or one private direct child. Reads never create directories. */
internal interface LinuxStorageFiles {
    fun exists(): Boolean
    /** Exclusive creation; an already present child is not silently adopted. Root is only checked. */
    fun create()
    fun read(name: String): ByteArray?
    fun publish(name: String, expected: ByteArray?, replacement: ByteArray)
}

internal enum class LinuxStorageIoStage {
    DIRECTORY_CREATED, TEMP_CREATED, TEMP_WRITTEN, FILE_SYNCED, TEMP_VERIFIED,
    BEFORE_EXPECTED_RECHECK, BEFORE_RENAME, RENAMED, DIRECTORY_SYNCED, READBACK,
}

/** Narrow syscall boundary. The Android adapter uses real descriptors, never path-based fstat. */
internal interface LinuxStoragePosix {
    val uid: Int
    val gid: Int
    data class Stat(
        val device: Long, val inode: Long, val mode: Int, val uid: Int, val gid: Int,
        val links: Long, val size: Long, val mtime: Long, val mtimeNanos: Long,
        val ctime: Long, val ctimeNanos: Long,
    ) {
        fun sameIdentity(other: Stat) = device == other.device && inode == other.inode
        fun sameContents(other: Stat) = this == other
    }
    interface Descriptor : Closeable {
        fun stat(): Stat
        fun read(bytes: ByteArray, offset: Int, length: Int): Int
        fun write(bytes: ByteArray, offset: Int, length: Int): Int
        fun sync()
    }
    /** Null exclusively means ENOENT. Permission/errors/symlinks are not absence. */
    fun lstat(path: String): Stat?
    fun mkdir(path: String)
    fun openRead(path: String): Descriptor
    fun createExclusive(path: String): Descriptor
    fun openDirectory(path: String): Descriptor
    fun rename(source: String, destination: String)
    fun unlink(path: String)
}

/**
 * Common algorithm, tested with real host POSIX descriptors. This is atomic replacement of ONE
 * file, not a multi-file transaction or an OS-process CAS. Repository metadata supplies recovery.
 * The Context-provided noBackup path is the trusted anchor; every owned child is checked no-follow.
 */
internal class SecureLinuxStorageFiles(
    noBackupDirectory: File,
    directoryName: String?,
    private val posix: LinuxStoragePosix,
    private val maxBytes: Int = 16 * 1024,
    private val fault: (LinuxStorageIoStage, String) -> Unit = { _, _ -> },
) : LinuxStorageFiles {
    private val root = noBackupDirectory.absoluteFile
    private val directory = directoryName?.let { File(root, it) } ?: root
    private val isRoot = directoryName == null
    private val sharesLegacyProfile = directoryName == "linux-profile"

    init {
        require(directoryName == null || directoryName.matches(Regex("[a-z][a-z0-9-]{0,63}")))
        require(maxBytes in 1..16 * 1024)
    }

    override fun exists(): Boolean = LinuxStorageIo.serialized { checkedDirectory() != null }

    override fun create() = LinuxStorageIo.serialized {
        val parent = checkedRoot()
        if (isRoot) return@serialized
        require(posix.lstat(directory.path) == null) { "STORAGE_DIRECTORY_EXISTS" }
        posix.openDirectory(root.path).use { parentFd ->
            require(parent.sameIdentity(parentFd.stat())) { "STORAGE_DIRECTORY_CHANGED" }
            posix.mkdir(directory.path)
            fault(LinuxStorageIoStage.DIRECTORY_CREATED, directory.name)
            val created = checkNotNull(checkedDirectory())
            posix.openDirectory(directory.path).use { childFd ->
                require(created.sameIdentity(childFd.stat())) { "STORAGE_DIRECTORY_CHANGED" }
                childFd.sync()
            }
            require(parent.sameIdentity(checkedRoot())) { "STORAGE_DIRECTORY_CHANGED" }
            parentFd.sync()
            require(created.sameIdentity(checkNotNull(checkedDirectory()))) { "STORAGE_DIRECTORY_CHANGED" }
        }
    }

    override fun read(name: String): ByteArray? = LinuxStorageIo.serialized {
        val file = file(name)
        val identity = checkedDirectory() ?: return@serialized null
        requireNoInterruptedWrite(name)
        val bytes = readFileOrAbsent(file)
        require(identity.sameIdentity(checkNotNull(checkedDirectory()))) { "STORAGE_DIRECTORY_CHANGED" }
        bytes
    }

    override fun publish(name: String, expected: ByteArray?, replacement: ByteArray) = LinuxStorageIo.serialized {
        require(replacement.size in 1..maxBytes) { "STORAGE_FILE_INVALID" }
        val target = file(name)
        val temp = temporary(name)
        val identity = checkNotNull(checkedDirectory()) { "STORAGE_DIRECTORY_MISSING" }
        requireNoInterruptedWrite(name)
        require(equal(readFileOrAbsent(target), expected)) { "STORAGE_CHANGED" }
        posix.openDirectory(directory.path).use { directoryFd ->
            require(identity.sameIdentity(directoryFd.stat())) { "STORAGE_DIRECTORY_CHANGED" }
            var owned: LinuxStoragePosix.Stat? = null
            var published = false
            try {
                posix.createExclusive(temp.path).use { descriptor ->
                    owned = descriptor.stat().also { checkRegular(it, allowEmpty = true) }
                    fault(LinuxStorageIoStage.TEMP_CREATED, name)
                    var offset = 0
                    while (offset < replacement.size) {
                        val count = descriptor.write(replacement, offset, replacement.size - offset)
                        require(count > 0) { "STORAGE_WRITE_FAILED" }
                        offset += count
                    }
                    fault(LinuxStorageIoStage.TEMP_WRITTEN, name)
                    descriptor.sync()
                    fault(LinuxStorageIoStage.FILE_SYNCED, name)
                    checkRegular(descriptor.stat())
                }
                require(readFileOrAbsent(temp)?.contentEquals(replacement) == true) { "STORAGE_READBACK_INVALID" }
                fault(LinuxStorageIoStage.TEMP_VERIFIED, name)
                require(identity.sameIdentity(checkNotNull(checkedDirectory()))) { "STORAGE_DIRECTORY_CHANGED" }
                fault(LinuxStorageIoStage.BEFORE_EXPECTED_RECHECK, name)
                require(equal(readFileOrAbsent(target), expected)) { "STORAGE_CHANGED" }
                require(checkNotNull(owned).sameIdentity(checkNotNull(posix.lstat(temp.path)))) { "STORAGE_FILE_CHANGED" }
                fault(LinuxStorageIoStage.BEFORE_RENAME, name)
                // No callback or other process-local writer may run between final expected check and rename.
                require(equal(readFileOrAbsent(target), expected)) { "STORAGE_CHANGED" }
                require(identity.sameIdentity(checkNotNull(checkedDirectory()))) { "STORAGE_DIRECTORY_CHANGED" }
                require(checkNotNull(owned).sameIdentity(checkNotNull(posix.lstat(temp.path)))) { "STORAGE_FILE_CHANGED" }
                require(readFileOrAbsent(temp)?.contentEquals(replacement) == true) { "STORAGE_READBACK_INVALID" }
                posix.rename(temp.path, target.path)
                published = true
                fault(LinuxStorageIoStage.RENAMED, name)
                require(identity.sameIdentity(directoryFd.stat()) && identity.sameIdentity(checkNotNull(checkedDirectory()))) {
                    "STORAGE_DIRECTORY_CHANGED"
                }
                directoryFd.sync()
                fault(LinuxStorageIoStage.DIRECTORY_SYNCED, name)
                require(checkNotNull(owned).sameIdentity(checkNotNull(posix.lstat(target.path)))) { "STORAGE_FILE_CHANGED" }
                require(readFileOrAbsent(target)?.contentEquals(replacement) == true) { "STORAGE_READBACK_INVALID" }
                fault(LinuxStorageIoStage.READBACK, name)
                require(identity.sameIdentity(checkNotNull(checkedDirectory()))) { "STORAGE_DIRECTORY_CHANGED" }
            } finally {
                // Never remove an unrelated or pre-existing temp, and never roll back a published file.
                // If cleanup fails the leftover temp remains a durable fail-closed witness.
                if (!published && owned != null) {
                    val remaining = posix.lstat(temp.path)
                    if (remaining != null && checkNotNull(owned).sameIdentity(remaining) &&
                        identity.sameIdentity(checkNotNull(checkedDirectory()))) {
                        posix.unlink(temp.path)
                        directoryFd.sync()
                    }
                }
            }
        }
    }

    private fun checkedRoot(): LinuxStoragePosix.Stat = checkNotNull(posix.lstat(root.path)) {
        "STORAGE_ROOT_MISSING"
    }.also {
        // AOSP creates the framework-owned noBackup root with 0771. Its group is the app's group.
        require(it.mode and TYPE_MASK == DIRECTORY && it.uid == posix.uid &&
            (it.mode and PERMISSIONS == PRIVATE_DIRECTORY ||
                (it.mode and PERMISSIONS == FRAMEWORK_DIRECTORY && it.gid == posix.gid))) { "STORAGE_ROOT_INVALID" }
    }

    private fun checkedDirectory(): LinuxStoragePosix.Stat? {
        val anchor = checkedRoot()
        if (isRoot) return anchor
        val result = posix.lstat(directory.path)?.also {
            require(it.mode and TYPE_MASK == DIRECTORY && it.uid == posix.uid &&
                it.mode and PERMISSIONS == PRIVATE_DIRECTORY) { "STORAGE_DIRECTORY_INVALID" }
        }
        require(anchor.sameIdentity(checkedRoot())) { "STORAGE_DIRECTORY_CHANGED" }
        return result
    }

    private fun readFileOrAbsent(file: File): ByteArray? {
        val pathStat = posix.lstat(file.path) ?: return null
        checkRegular(pathStat)
        return posix.openRead(file.path).use { descriptor ->
            val before = descriptor.stat()
            checkRegular(before)
            require(pathStat.sameContents(before)) { "STORAGE_FILE_CHANGED" }
            val bytes = ByteArray(before.size.toInt())
            var offset = 0
            while (offset < bytes.size) {
                val count = descriptor.read(bytes, offset, bytes.size - offset)
                require(count > 0) { "STORAGE_FILE_CHANGED" }
                offset += count
            }
            require(descriptor.read(ByteArray(1), 0, 1) == 0 && before.sameContents(descriptor.stat()) &&
                before.sameContents(checkNotNull(posix.lstat(file.path)))) { "STORAGE_FILE_CHANGED" }
            bytes
        }
    }

    private fun checkRegular(stat: LinuxStoragePosix.Stat, allowEmpty: Boolean = false) {
        require(stat.mode and TYPE_MASK == REGULAR && stat.uid == posix.uid && stat.links == 1L &&
            stat.mode and PERMISSIONS == PRIVATE_FILE && stat.size in (if (allowEmpty) 0L else 1L)..maxBytes.toLong()) {
            "STORAGE_FILE_INVALID"
        }
    }

    private fun file(name: String): File {
        require(name.matches(Regex("[a-z0-9][a-z0-9.-]{0,95}")) && !name.contains("..") && !name.endsWith(".tmp"))
        return File(directory, name)
    }
    private fun temporary(name: String): File {
        val saved = file(name)
        // Preserve LinuxProfileStore.AtomicFile's O_EXCL boundary across legacy/E1 writes.
        return File(directory, if (sharesLegacyProfile && name == "remote-linux.json") "remote-linux.tmp" else saved.name + ".tmp")
    }
    private fun requireNoInterruptedWrite(name: String) {
        require(posix.lstat(temporary(name).path) == null) { "STORAGE_INTERRUPTED_WRITE" }
        // An interrupted earlier E1 write is also evidence, never an excuse to switch temp names.
        if (sharesLegacyProfile && name == "remote-linux.json") {
            require(posix.lstat(File(directory, "remote-linux.json.tmp").path) == null) { "STORAGE_INTERRUPTED_WRITE" }
        }
    }
    private fun equal(a: ByteArray?, b: ByteArray?) = if (a == null || b == null) a == null && b == null else a.contentEquals(b)

    private companion object {
        const val TYPE_MASK = 61440 // S_IFMT
        const val DIRECTORY = 16384 // S_IFDIR
        const val REGULAR = 32768 // S_IFREG
        const val PERMISSIONS = 4095 // Includes setuid/setgid/sticky; reject all special bits.
        const val PRIVATE_DIRECTORY = 448 // 0700
        const val FRAMEWORK_DIRECTORY = 505 // 0771
        const val PRIVATE_FILE = 384 // 0600
    }
}
