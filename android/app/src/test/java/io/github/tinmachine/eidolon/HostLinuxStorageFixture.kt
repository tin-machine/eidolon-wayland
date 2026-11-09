package io.github.tinmachine.eidolon

import java.io.Closeable
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions

/** Real Linux filesystem/syscalls for the shared algorithm. This is not Android instrumentation. */
internal class HostLinuxStorageFixture : Closeable {
    val directory: Path = Files.createTempDirectory("eidolon-storage-e1-").also {
        Files.setPosixFilePermissions(it, PosixFilePermissions.fromString("rwx------"))
    }
    val root: LinuxStorageFiles get() = files(null)
    fun files(child: String?, fault: (LinuxStorageIoStage, String) -> Unit = { _, _ -> }): LinuxStorageFiles =
        SecureLinuxStorageFiles(directory.toFile(), child, HostLinuxStoragePosix, fault = fault)

    override fun close() {
        // Only this randomly generated fixture directory is owned; never follow symlinks.
        Files.walk(directory).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach { Files.delete(it) } }
    }
}

internal object HostLinuxStoragePosix : LinuxStoragePosix {
    override val uid: Int get() = HostLinuxStorageNative.uid()
    override val gid: Int get() = HostLinuxStorageNative.gid()
    override fun lstat(path: String) = HostLinuxStorageNative.lstat(path)?.let(::stat)
    override fun mkdir(path: String) = HostLinuxStorageNative.mkdir(path)
    override fun openRead(path: String): LinuxStoragePosix.Descriptor = Descriptor(HostLinuxStorageNative.open(path, 0))
    override fun createExclusive(path: String): LinuxStoragePosix.Descriptor = Descriptor(HostLinuxStorageNative.open(path, 1))
    override fun openDirectory(path: String): LinuxStoragePosix.Descriptor = Descriptor(HostLinuxStorageNative.open(path, 2))
    override fun rename(source: String, destination: String) = HostLinuxStorageNative.rename(source, destination)
    override fun unlink(path: String) = HostLinuxStorageNative.unlink(path)
    private class Descriptor(private val fd: Int) : LinuxStoragePosix.Descriptor {
        override fun stat() = stat(HostLinuxStorageNative.fstat(fd))
        override fun read(bytes: ByteArray, offset: Int, length: Int) = HostLinuxStorageNative.read(fd, bytes, offset, length)
        override fun write(bytes: ByteArray, offset: Int, length: Int) = HostLinuxStorageNative.write(fd, bytes, offset, length)
        override fun sync() = HostLinuxStorageNative.sync(fd)
        override fun close() = HostLinuxStorageNative.close(fd)
    }
    private fun stat(v: LongArray) = LinuxStoragePosix.Stat(v[0], v[1], v[2].toInt(), v[3].toInt(), v[4].toInt(),
        v[5], v[6], v[7], v[8], v[9], v[10])
}

/** Test-only JNI adapter is built from the checked-in C resource using the CI JDK and host cc. */
internal object HostLinuxStorageNative {
    init {
        check(System.getProperty("os.name") == "Linux") { "E1 real filesystem tests require Linux" }
        val build = Files.createTempDirectory("eidolon-storage-jni-")
        val source = build.resolve("storage.c")
        val library = build.resolve("libstorage-test.so")
        val include = System.getProperty("eidolon.test.jni.include")?.let(Path::of)
            ?: Path.of(System.getProperty("java.home"), "include")
        check(Files.isRegularFile(include.resolve("jni.h"))) { "E1 filesystem tests require JDK JNI headers" }
        checkNotNull(javaClass.getResourceAsStream("/linux-storage-posix-test.c")).use { Files.copy(it, source) }
        val compiler = ProcessBuilder("cc", "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror",
            "-fPIC", "-shared", "-I$include", "-I${include.resolve("linux")}", source.toString(), "-o", library.toString())
            .redirectErrorStream(true).start()
        val output = compiler.inputStream.bufferedReader().readText()
        check(compiler.waitFor() == 0) { "E1 filesystem JNI test adapter compile failed: $output" }
        System.load(library.toString())
        Files.delete(source)
        // Linux keeps the loaded mapping alive after unlink; no reusable build artifact is needed.
        Files.delete(library)
        Files.delete(build)
    }
    external fun uid(): Int
    external fun gid(): Int
    external fun lstat(path: String): LongArray?
    external fun fstat(fd: Int): LongArray
    external fun mkdir(path: String)
    external fun open(path: String, kind: Int): Int
    external fun rename(source: String, destination: String)
    external fun unlink(path: String)
    external fun read(fd: Int, bytes: ByteArray, offset: Int, length: Int): Int
    external fun write(fd: Int, bytes: ByteArray, offset: Int, length: Int): Int
    external fun sync(fd: Int)
    external fun close(fd: Int)
}
