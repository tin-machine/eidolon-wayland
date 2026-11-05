package io.github.tinmachine.eidolon

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class ThemeArchiveTest {
    private fun archive(entries: List<Pair<String, ByteArray>>, action: (File) -> Unit) {
        val file = File.createTempFile("eidolon-theme-", ".zip")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                entries.forEach { (name, bytes) -> zip.putNextEntry(ZipEntry(name)); zip.write(bytes); zip.closeEntry() }
            }
            action(file)
        } finally { file.delete() }
    }
    @Test fun validPackageHasExactUtf8NamesAndContents() {
        archive(listOf("theme.toml" to "name='例'".toByteArray(), "assets/" to byteArrayOf(), "assets/ring.svg" to byteArrayOf(1,2,3))) { file ->
            val bytes = ThemeArchive.convert(file) {}
            val input = java.io.ByteArrayInputStream(bytes)
            assertArrayEquals("EIDTHEME".toByteArray()+byteArrayOf(1), input.readNBytes(9))
            fun little(n: Int): Int = (0 until n).sumOf { input.read() shl (8*it) }
            assertEquals(2,little(2))
            assertEquals(10,little(2)); val length=little(4)
            assertEquals("theme.toml",input.readNBytes(10).toString(Charsets.UTF_8))
            assertEquals("name='例'",input.readNBytes(length).toString(Charsets.UTF_8))
            assertEquals(15,little(2)); assertEquals(3,little(4))
            assertEquals("assets/ring.svg",input.readNBytes(15).toString(Charsets.UTF_8))
            assertArrayEquals(byteArrayOf(1,2,3),input.readNBytes(3)); assertEquals(-1,input.read())
        }
    }
    @Test fun pathsCountsBombsAndCancellationAreRejected() {
        for (bad in listOf("../escape", "/absolute", "assets/../../escape", "a\\b", "a//b", "C:/file", "./file")) {
            archive(listOf("theme.toml" to byteArrayOf(),bad to byteArrayOf(1))) { file ->
                assertThrows(Exception::class.java) { ThemeArchive.convert(file) {} }
            }
        }
        archive(listOf("theme.toml" to ByteArray(ThemeArchive.MAX_SOURCE+1))) { file ->
            assertTrue(file.length() < ThemeArchive.MAX_ARCHIVE)
            assertThrows(Exception::class.java) { ThemeArchive.convert(file) {} }
        }
        archive(listOf("theme.toml" to byteArrayOf())+(1..34).map { "assets/$it.svg" to byteArrayOf() }) { file ->
            assertThrows(Exception::class.java) { ThemeArchive.convert(file) {} }
        }
        archive(listOf("theme.toml" to byteArrayOf())) { file ->
            assertThrows(Exception::class.java) { ThemeArchive.convert(file) { error("cancelled") } }
            val bytes=file.readBytes(); file.writeBytes(bytes.copyOf(bytes.size-20))
            assertThrows(Exception::class.java) { ThemeArchive.convert(file) {} }
        }
    }
}
