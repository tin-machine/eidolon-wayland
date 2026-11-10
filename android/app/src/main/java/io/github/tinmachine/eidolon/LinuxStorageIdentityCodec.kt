package io.github.tinmachine.eidolon

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/** B1のstrict JSONと7-field storage schema。legacy profile codecの挙動は変更しない。 */
object LinuxStorageIdentityCodec {
    const val MAX_BYTES = 16 * 1024
    private val captureFields = setOf("legacy_installation", "profile_id", "profile_binding", "host_user_binding")
    private val recordFields = captureFields + setOf("schema", "installation_id", "profile_storage_id")

    fun decode(bytes: ByteArray): LinuxStorageRecord = guarded {
        val fields = objectFields(bytes, recordFields)
        require(fields["schema"] == 1L)
        LinuxStorageRecord(fields.string("legacy_installation"), StorageInstallationId(fields.string("installation_id")),
            fields.string("profile_id"), fields.string("profile_binding"), ProfileStorageId(fields.string("profile_storage_id")),
            fields.string("host_user_binding"))
    }
    fun decodeCapture(bytes: ByteArray): StorageCapture = guarded {
        val fields = objectFields(bytes, captureFields)
        StorageCapture(fields.string("legacy_installation"), fields.string("profile_id"),
            fields.string("profile_binding"), fields.string("host_user_binding"))
    }
    fun encode(record: LinuxStorageRecord): ByteArray = bounded(LinuxStorageJson.canonical(record.toFields()))
    fun encodeCapture(capture: StorageCapture): ByteArray = bounded(LinuxStorageJson.canonical(capture.toFields()))

    private fun bounded(bytes: ByteArray): ByteArray = bytes.also { require(it.size in 1..MAX_BYTES) { "CONTRACT_INVALID" } }
    private fun objectFields(bytes: ByteArray, expected: Set<String>): Map<*, *> {
        val value = LinuxStorageJson.decode(bytes) as? Map<*, *> ?: throw IllegalArgumentException("CONTRACT_INVALID")
        require(value.keys == expected)
        return value
    }
    private fun Map<*, *>.string(name: String): String = this[name] as? String ?: throw IllegalArgumentException("CONTRACT_INVALID")
    private inline fun <T> guarded(action: () -> T): T = try { action() }
    catch (_: Exception) { throw IllegalArgumentException("CONTRACT_INVALID") }
}

/** B1限定domain。recursive sorted keys / ensure_ascii / integer i64 / depth<=32。JCSではない。 */
internal object LinuxStorageJson {
    const val MAX_DEPTH = 32
    fun decode(bytes: ByteArray): Any? = parse(bytes)
    fun parse(bytes: ByteArray, maxBytes: Int = LinuxStorageIdentityCodec.MAX_BYTES): Any? = try {
        // Storage callers keep the 16 KiB default; the local configuration envelope permits 32 KiB.
        require(maxBytes in 1..32768 && bytes.size in 1..maxBytes)
        val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        Reader(text).read()
    } catch (_: Exception) { throw IllegalArgumentException("CONTRACT_INVALID") }

    fun canonical(value: Any?): ByteArray = buildString { appendValue(value, 0) }.toByteArray(Charsets.UTF_8)

    private fun StringBuilder.appendValue(value: Any?, depth: Int) {
        require(depth <= MAX_DEPTH) { "CONTRACT_INVALID" }
        when (value) {
            null -> append("null")
            is Boolean -> append(if (value) "true" else "false")
            is Int -> append(value)
            is Long -> append(value)
            is String -> appendQuoted(value)
            is List<*> -> {
                append('[')
                value.forEachIndexed { index, item -> if (index != 0) append(','); appendValue(item, depth + 1) }
                append(']')
            }
            is Map<*, *> -> {
                require(value.keys.all { it is String }) { "CONTRACT_INVALID" }
                val keys = value.keys.map { it as String }.sortedWith(::compareCodePoints)
                append('{')
                keys.forEachIndexed { index, key ->
                    if (index != 0) append(',')
                    appendValue(key, depth + 1); append(':'); appendValue(value[key], depth + 1)
                }
                append('}')
            }
            else -> throw IllegalArgumentException("CONTRACT_INVALID")
        }
    }

    // Python sorts Unicode code points, not Java's UTF-16 code units.
    private fun compareCodePoints(a: String, b: String): Int {
        var i = 0; var j = 0
        while (i < a.length && j < b.length) {
            val x = a.codePointAt(i); val y = b.codePointAt(j)
            if (x != y) return x.compareTo(y)
            i += Character.charCount(x); j += Character.charCount(y)
        }
        return (a.length - i).compareTo(b.length - j)
    }

    private fun unicode(value: String) {
        var index = 0
        while (index < value.length) {
            val c = value[index++]
            if (c.isHighSurrogate()) require(index < value.length && value[index++].isLowSurrogate()) { "CONTRACT_INVALID" }
            else require(!c.isLowSurrogate()) { "CONTRACT_INVALID" }
        }
    }
    private fun StringBuilder.appendQuoted(value: String) {
        unicode(value)
        append('"')
        for (c in value) when (c) {
            '"' -> append("\\\"")
            '\\' -> append("\\\\")
            '\b' -> append("\\b")
            '\u000c' -> append("\\f")
            '\n' -> append("\\n")
            '\r' -> append("\\r")
            '\t' -> append("\\t")
            else -> if (c.code < 32 || c.code >= 127) append("\\u").append(c.code.toString(16).padStart(4, '0')) else append(c)
        }
        append('"')
    }

    private class Reader(private val text: String) {
        private var index = 0
        fun read(): Any? {
            val result = value(0)
            whitespace(); require(index == text.length)
            return result
        }
        private fun whitespace() { while (index < text.length && text[index] in " \r\n\t") index++ }
        private fun consume(c: Char): Boolean {
            whitespace()
            return if (index < text.length && text[index] == c) { index++; true } else false
        }
        private fun value(depth: Int): Any? {
            require(depth <= MAX_DEPTH)
            whitespace(); require(index < text.length)
            return when (text[index]) {
                '{' -> objectValue(depth)
                '[' -> arrayValue(depth)
                '"' -> string()
                't' -> { literal("true"); true }
                'f' -> { literal("false"); false }
                'n' -> { literal("null"); null }
                else -> integer()
            }
        }
        private fun literal(token: String) { require(text.startsWith(token, index)); index += token.length }
        private fun objectValue(depth: Int): Map<String, Any?> {
            require(consume('{'))
            val result = linkedMapOf<String, Any?>()
            if (consume('}')) return result
            do {
                require(depth + 1 <= MAX_DEPTH)
                val key = string()
                require(key !in result && consume(':'))
                result[key] = value(depth + 1)
                if (consume('}')) return result
                require(consume(','))
            } while (true)
        }
        private fun arrayValue(depth: Int): List<Any?> {
            require(consume('['))
            val result = mutableListOf<Any?>()
            if (consume(']')) return result
            do {
                result.add(value(depth + 1))
                if (consume(']')) return result
                require(consume(','))
            } while (true)
        }
        private fun integer(): Long {
            val start = index
            if (index < text.length && text[index] == '-') index++
            require(index < text.length && text[index] in '0'..'9')
            if (text[index] == '0') index++ else while (index < text.length && text[index] in '0'..'9') index++
            require(index == text.length || text[index] in ",]} \r\n\t")
            val token = text.substring(start, index)
            require(token != "-0")
            return token.toLong()
        }
        private fun string(): String {
            require(consume('"'))
            val result = StringBuilder()
            while (index < text.length) {
                val c = text[index++]
                if (c == '"') return result.toString().also { unicode(it) }
                require(c.code >= 32)
                if (c != '\\') { result.append(c); continue }
                require(index < text.length)
                when (val escaped = text[index++]) {
                    '"', '\\', '/' -> result.append(escaped)
                    'b' -> result.append('\b')
                    'f' -> result.append('\u000c')
                    'n' -> result.append('\n')
                    'r' -> result.append('\r')
                    't' -> result.append('\t')
                    'u' -> {
                        require(index + 4 <= text.length)
                        val hex = text.substring(index, index + 4)
                        require(hex.all { it in "0123456789abcdefABCDEF" })
                        result.append(hex.toInt(16).toChar()); index += 4
                    }
                    else -> throw IllegalArgumentException("CONTRACT_INVALID")
                }
            }
            throw IllegalArgumentException("CONTRACT_INVALID")
        }
    }
}
