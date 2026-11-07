package io.github.tinmachine.eidolon

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/** Profileのflat object専用。Android JSONObjectの型変換や重複key上書きに依存しない。 */
object ConnectionProfileV2Codec {
    const val MAX_BYTES = 16 * 1024
    fun decode(bytes: ByteArray, installation: String): ConnectionProfileV2.Value = try {
        ConnectionProfileV2.parse(decodeFields(bytes), installation)
    } catch (_: Exception) { throw IllegalArgumentException("PROFILE_JSON_INVALID") }

    internal fun decodeFields(bytes: ByteArray, maxBytes: Int = MAX_BYTES): Map<String, Any> {
        require(maxBytes in 1..MAX_BYTES && bytes.size in 1..maxBytes)
        val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        return Reader(text).fields()
    }

    fun encode(profile: ConnectionProfileV2.Value): ByteArray {
        val value = ConnectionProfileV2.parse(profile.toFields(), profile.installation)
        val bytes = value.toFields().entries.joinToString(",", "{", "}") { (key, field) ->
            quote(key) + ":" + if (field is String) quote(field) else field.toString()
        }.toByteArray(Charsets.UTF_8)
        require(bytes.size <= MAX_BYTES) { "PROFILE_JSON_INVALID" }
        return bytes
    }

    private fun quote(value: String): String = buildString {
        append('"')
        for (c in value) when {
            c == '"' -> append("\\\"")
            c == '\\' -> append("\\\\")
            c.code < 32 -> append("\\u" + c.code.toString(16).padStart(4, '0'))
            else -> append(c)
        }
        append('"')
    }

    private class Reader(private val text: String) {
        private var index = 0
        private fun whitespace() { while (index < text.length && text[index] in " \r\n\t") index++ }
        private fun expect(c: Char) { whitespace(); require(index < text.length && text[index++] == c) }
        fun fields(): Map<String, Any> {
            val result = linkedMapOf<String, Any>()
            expect('{'); whitespace()
            if (index < text.length && text[index] == '}') { index++; whitespace(); require(index == text.length); return result }
            while (true) {
                val key = string()
                require(key !in result && result.size < 10)
                expect(':'); whitespace()
                val field: Any = if (index < text.length && text[index] == '"') string() else integer()
                result[key] = field
                whitespace(); require(index < text.length)
                when (text[index++]) { '}' -> break; ',' -> Unit; else -> error("invalid") }
            }
            whitespace(); require(index == text.length)
            return result
        }
        private fun integer(): Long {
            whitespace(); val first = index
            if (index < text.length && text[index] == '-') index++
            require(index < text.length && text[index] in '0'..'9')
            if (text[index] == '0') index++ else while (index < text.length && text[index] in '0'..'9') index++
            require(index == text.length || text[index] in ",} \r\n\t")
            return text.substring(first, index).toLong()
        }
        private fun string(): String {
            expect('"'); val result = StringBuilder()
            while (index < text.length) {
                val c = text[index++]
                if (c == '"') {
                    val value = result.toString()
                    var i = 0
                    while (i < value.length) {
                        val unit = value[i++]
                        if (unit.isHighSurrogate()) require(i < value.length && value[i++].isLowSurrogate())
                        else require(!unit.isLowSurrogate())
                    }
                    return value
                }
                require(c.code >= 32)
                if (c != '\\') result.append(c) else {
                    require(index < text.length)
                    when (val escaped = text[index++]) {
                        '"', '\\', '/' -> result.append(escaped)
                        'b' -> result.append('\b'); 'f' -> result.append('\u000c')
                        'n' -> result.append('\n'); 'r' -> result.append('\r'); 't' -> result.append('\t')
                        'u' -> {
                            require(index + 4 <= text.length)
                            val hex = text.substring(index, index + 4)
                            require(hex.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' })
                            result.append(hex.toInt(16).toChar()); index += 4
                        }
                        else -> error("invalid")
                    }
                }
            }
            error("invalid")
        }
    }
}
