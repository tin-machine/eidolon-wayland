package io.github.tinmachine.eidolon

import android.content.Context
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import org.json.JSONObject

/** 呼出元はhostの単一worker。チェック結果は周期snapshot更新中も保持する。 */
class DiagnosticsStore(context: Context) {
    private val directory = File(context.noBackupFilesDir, "diagnostics").apply { mkdirs() }
    private var runtime = JSONObject()
    private var check: JSONObject? = null
    private var sequence = 0L

    fun publishRuntime(snapshot: JSONObject) { runtime = snapshot; write() }
    fun publishCheck(result: JSONObject) { check = result; write() }

    private fun write() {
        val value = JSONObject().put("schema", 1).put("sequence", (++sequence).toString())
            .put("build_info", JSONObject(NativeBridge.status.info ?: "{}"))
            .put("runtime", runtime)
        check?.let { value.put("check", it) }
        val bytes = value.toString().toByteArray(Charsets.UTF_8)
        require(bytes.size <= 1024 * 1024) { "RESOURCE_LIMIT" }
        val temporary = File(directory, "latest.json.tmp")
        FileOutputStream(temporary).use { it.write(bytes); it.fd.sync() }
        Files.move(temporary.toPath(), File(directory, "latest.json").toPath(),
            StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }
}
