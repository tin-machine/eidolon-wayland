package io.github.tinmachine.eidolon

import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipFile

/** ZIPのentry名をfilesystemへ展開しない。Rustと同じ上限のflat packageへ変換する。 */
object ThemeArchive {
    const val MAX_SOURCE = 8 * 1024 * 1024
    const val MAX_ARCHIVE = MAX_SOURCE + 64 * 1024
    private fun path(name: String): Boolean = name.toByteArray(Charsets.UTF_8).size in 1..256 &&
        !name.startsWith('/') && name.none { it == '\\' || it == ':' || it.code < 32 || it.code == 127 } &&
        name.split('/').all { it.isNotEmpty() && it != "." && it != ".." }

    fun convert(file: File, checkActive: () -> Unit): ByteArray {
        require(file.length() in 1..MAX_ARCHIVE.toLong()) { "テーマZIPの容量上限を超えています" }
        val files = linkedMapOf<String, ByteArray>()
        var total = 0
        ZipFile(file).use { zip ->
            val entries = zip.entries()
            var count = 0
            while (entries.hasMoreElements()) {
                checkActive()
                require(++count <= 128) { "ZIP内の項目が多すぎます" }
                val entry = entries.nextElement()
                require(path(if (entry.isDirectory) entry.name.removeSuffix("/") else entry.name)) { "ZIP内のパスが不正です" }
                if (entry.isDirectory) continue
                require(files.size < 34 && !files.containsKey(entry.name)) { "ファイル数または名前の重複を確認してください" }
                require(entry.size in 0..(MAX_SOURCE - total).toLong()) { "展開後の容量上限を超えています" }
                val bytes = ByteArrayOutputStream()
                val checksum = java.util.zip.CRC32()
                zip.getInputStream(entry).use { input ->
                    val block = ByteArray(8192)
                    while (true) {
                        checkActive()
                        val n = input.read(block)
                        if (n < 0) break
                        total += n
                        require(total <= MAX_SOURCE) { "展開後の容量上限を超えています" }
                        bytes.write(block, 0, n)
                        checksum.update(block, 0, n)
                    }
                }
                require(bytes.size().toLong() == entry.size && checksum.value == entry.crc) { "ZIPが破損しています" }
                files[entry.name] = bytes.toByteArray()
            }
        }
        require(files.containsKey("theme.toml")) { "ZIPの直下にtheme.tomlが必要です" }
        val output = ByteArrayOutputStream()
        fun little(value: Int, width: Int) { repeat(width) { output.write(value ushr (it * 8) and 255) } }
        output.write("EIDTHEME".toByteArray(Charsets.US_ASCII)); output.write(1)
        little(files.size, 2)
        for ((name, bytes) in files) {
            val path = name.toByteArray(Charsets.UTF_8)
            little(path.size, 2); little(bytes.size, 4)
            output.write(path); output.write(bytes)
        }
        return output.toByteArray()
    }
}
