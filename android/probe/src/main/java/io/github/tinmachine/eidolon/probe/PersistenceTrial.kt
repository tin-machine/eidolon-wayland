package io.github.tinmachine.eidolon.probe

import android.os.Handler
import android.os.SystemClock
import android.system.Os
import android.system.OsConstants
import android.util.AtomicFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.UUID

/** Journal and independent fault-injection witness live only in this debug APK's local storage. */
internal class PersistenceTrial(
    private val directory: File,
    private val handler: Handler,
    private val action: String,
    private val native: (String, String) -> JSONObject,
    private val record: (String, JSONObject) -> Unit,
    private val finish: (String) -> Unit,
    private val showLocatedPose: ((JSONArray) -> Unit)? = null,
) {
    private val journal = AtomicFile(File(directory, "sa0-persistence.json"))
    private val witness = AtomicFile(File(directory, "sa0-fault-witness.json"))
    private var command = ""
    private var running = false
    private var stopped = false
    private var deadline = 0L
    private lateinit var state: JSONObject
    private val poll = Runnable { guarded { accept(native("poll", "")) } }

    private fun write(file: AtomicFile, data: JSONObject) {
        val stream = file.startWrite()
        try {
            stream.write(data.toString().toByteArray(Charsets.UTF_8))
            stream.fd.sync()
        } catch (e: IOException) {
            file.failWrite(stream)
            throw e
        }
        file.finishWrite(stream)
        // Detect rename failure and make the directory update durable before advancing.
        check(JSONObject(String(file.readFully(), Charsets.UTF_8)).toString() == data.toString())
        syncDirectory()
    }
    private fun syncDirectory() {
        val fd = Os.open(directory.path, OsConstants.O_RDONLY, 0)
        try { Os.fsync(fd) } finally { Os.close(fd) }
    }

    fun start() = guarded {
        when (action) {
            "save", "save_interrupt" -> {
                check(!journal.baseFile.exists() && !witness.baseFile.exists()) { "prior_case_pending" }
                state = JSONObject().put("case", UUID.randomUUID().toString()).put("phase", "baseline")
                launch("list")
            }
            "recover", "rescue", "lookup", "erase", "absent" -> {
                state = JSONObject(String(journal.readFully(), Charsets.UTF_8))
                val phase = state.getString("phase")
                when (action) {
                    "recover" -> { check(phase == "pending"); launch("list") }
                    // Explicit cleanup fallback for this injected fault only, after the blind test.
                    "rescue" -> { check(phase == "pending"); promote(readWitness()); finish("witness_restored") }
                    "lookup" -> { check(phase == "saved"); launch("find") }
                    "erase" -> { check(phase in setOf("saved", "erasing")); state.put("phase","erasing"); write(journal,state); launch("erase") }
                    "absent" -> { check(phase == "erased"); launch("find") }
                }
            }
            else -> finish("unknown_action")
        }
    }

    private fun guarded(body: () -> Unit) {
        if (stopped) return
        try { body() } catch (e: Exception) {
            stop()
            record("trial_failure", JSONObject().put("type", e.javaClass.simpleName))
            finish("trial_error")
        }
    }

    private fun launch(next: String) {
        command = next
        deadline = SystemClock.elapsedRealtime() + 25_000
        accept(native(next, state.optString("uuid")))
    }

    private fun accept(reply: JSONObject) {
        val stage = reply.getString("stage")
        running = stage == "pending"
        if (running) {
            if (SystemClock.elapsedRealtime() >= deadline) { stop(); finish("trial_timeout") }
            else handler.postDelayed(poll, 50)
            return
        }
        // The native JSON may contain UUIDs. Keep them out of Android logs.
        val errors = reply.optJSONArray("release_errors")
        record("trial_step", JSONObject().put("operation", command).put("stage", stage)
            .put("release_errors", errors).put("xr_error", reply.optString("error")))
        // Persist successful mutation receipts even if subsequent runtime handle cleanup failed.
        if (command == "save" && stage == "saved") {
            val id = reply.getString("uuid")
            if (action == "save_interrupt") {
                write(witness, JSONObject().put("case", state.getString("case")).put("uuid", id))
                if (errors?.length() != 0) { finish("release_failed"); return }
                // Driver force-stops this process now, before the primary journal gets its UUID.
                finish("interrupt_ready")
            } else { promote(id); finish(if (errors?.length() == 0) "saved" else "release_failed") }
            return
        }
        if (errors?.length() != 0 || stage == "error") { finish("trial_error"); return }
        when (command) {
            "list" -> {
                check(stage == "snapshot")
                val rows = reply.getJSONArray("rows")
                val ids = (0 until rows.length()).map { rows.getJSONObject(it) }
                    .filter { it.getString("persist") == "LOADED" }.map { it.getString("uuid") }.toSet()
                if (action == "recover") {
                    val baseline = state.getJSONArray("baseline")
                    val previous = (0 until baseline.length()).map { baseline.getString(it) }.toSet()
                    val delta = ids - previous
                    record("recovery_candidates", JSONObject().put("count",delta.size))
                    // Discovery supplies the candidate. The separately recorded witness only scores it.
                    val candidate = delta.singleOrNull()
                    if (candidate == null || candidate != readWitness()) { finish("unattributed"); return }
                    promote(candidate); finish("recovered")
                } else {
                    state.put("phase","pending").put("baseline",JSONArray(ids.toList()))
                    write(journal,state)
                    launch("save")
                }
            }
            "find" -> {
                check(stage == "snapshot")
                val rows = reply.getJSONArray("rows")
                val row = (0 until rows.length()).map { rows.getJSONObject(it) }.singleOrNull { it.getString("uuid") == state.getString("uuid") }
                if (row == null) { finish("undetermined"); return }
                val loaded = row.getString("persist")
                val tracking = row.getString("tracking")
                record("discovery_state",JSONObject().put("persist",loaded).put("tracking",tracking).put("pose_valid",row.getBoolean("pose_valid")))
                if (action == "absent" && loaded == "NOT_FOUND" && tracking == "STOPPED") {
                    witness.delete()
                    journal.delete()
                    syncDirectory()
                    check(!witness.baseFile.exists() && !journal.baseFile.exists())
                    finish("absence_verified")
                } else if (action == "lookup" && loaded == "LOADED" && tracking == "TRACKING" && row.getBoolean("pose_valid")) {
                    showLocatedPose?.invoke(row.getJSONArray("stage_pose"))
                    finish("located")
                } else { finish("unresolved") }
            }
            "erase" -> {
                check(stage in setOf("erased","already_absent"))
                state.put("phase","erased"); write(journal,state); finish(stage)
            }
        }
    }

    private fun readWitness(): String {
        val proof = JSONObject(String(witness.readFully(), Charsets.UTF_8))
        check(proof.getString("case") == state.getString("case"))
        return proof.getString("uuid")
    }
    private fun promote(id: String) {
        check(id.matches(Regex("[0-9a-f]{32}")) && id.any { it != '0' })
        state.put("phase","saved").put("uuid",id)
        write(journal,state)
    }
    fun stop() {
        stopped = true
        handler.removeCallbacks(poll)
        if (running) {
            val cancelled=native("cancel","")
            record("trial_cancel",JSONObject().put("stage",cancelled.optString("stage")).put("release_errors",cancelled.optJSONArray("release_errors")))
            running = false
        }
    }
}
