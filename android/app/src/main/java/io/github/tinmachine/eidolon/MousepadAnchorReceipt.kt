package io.github.tinmachine.eidolon

import org.json.JSONObject

/** One product-private record. Runtime window and scene IDs have shorter lifetimes. */
internal sealed interface MousepadAnchorReceipt {
    data object Empty : MousepadAnchorReceipt
    class Pending(val operation: String) : MousepadAnchorReceipt
    class Saved(val uuid: String) : MousepadAnchorReceipt
    class Removing(val uuid: String) : MousepadAnchorReceipt

    fun encode(): ByteArray = JSONObject().apply {
        put("schema", 1)
        put("placement", "mousepad_main")
        when (val receipt = this@MousepadAnchorReceipt) {
            Empty -> put("state", "empty")
            is Pending -> put("state", "pending").put("operation_id", receipt.operation)
            is Saved -> put("state", "saved").put("anchor_uuid", receipt.uuid)
            is Removing -> put("state", "removing").put("anchor_uuid", receipt.uuid)
        }
    }.toString().toByteArray(Charsets.UTF_8)

    companion object {
        fun validUuid(uuid: String): Boolean =
            uuid.length == 32 && uuid.all { it in '0'..'9' || it in 'a'..'f' } && uuid.any { it != '0' }

        fun decode(bytes: ByteArray): MousepadAnchorReceipt {
            require(bytes.size <= 512)
            val record = JSONObject(bytes.toString(Charsets.UTF_8))
            require(record.getInt("schema") == 1 && record.getString("placement") == "mousepad_main")
            fun id(key: String): String = record.getString(key).also { require(validUuid(it)) }
            return when (record.getString("state")) {
                "empty" -> Empty
                "pending" -> Pending(id("operation_id"))
                "saved" -> Saved(id("anchor_uuid"))
                "removing" -> Removing(id("anchor_uuid"))
                else -> error("Unknown Mousepad Anchor receipt")
            }
        }
    }
}
