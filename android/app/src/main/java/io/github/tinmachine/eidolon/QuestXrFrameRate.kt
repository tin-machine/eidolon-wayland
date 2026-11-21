package io.github.tinmachine.eidolon

import android.os.Process
import android.os.SystemClock
import java.io.IOException
import kotlin.concurrent.thread

internal data class QuestFrameSample(val fps: Int, val at: Long, val stale: Int?, val gpuMs: Double?)

/** Meta emits a VrApi frame report about once a second from our own XR process. */
internal class QuestXrFrameRate : AutoCloseable {
    @Volatile private var closed = false
    @Volatile private var latest: QuestFrameSample? = null
    private var child: java.lang.Process? = null

    fun start() {
        val since = System.currentTimeMillis()
        thread(name = "eidolon-hud-fps", isDaemon = true) {
            try {
                val reader = synchronized(this) {
                    if (closed) return@thread
                    ProcessBuilder("/system/bin/logcat", "-b", "main", "--pid=${Process.myPid()}",
                        "-T", "1", "-v", "epoch", "-s", "VrApi:I").redirectErrorStream(true).start()
                        .also { child = it }
                }
                reader.inputStream.bufferedReader().useLines { lines ->
                    for (line in lines) {
                        if (closed) break
                        parse(line, since, System.currentTimeMillis(), SystemClock.elapsedRealtime())?.let { latest = it }
                    }
                }
            } catch (_: IOException) {
                // Closing the pipe is also the normal stop path.
            } catch (_: SecurityException) {
                // OS builds which restrict the own-process stream keep the frame metrics unavailable.
            } finally {
                close()
            }
        }
    }

    fun sample(now: Long): QuestFrameSample? = latest?.takeIf { !closed && now - it.at in 0..2_500 }

    @Synchronized override fun close() {
        closed = true
        child?.destroy()
        child = null
    }

    companion object {
        private val report = Regex("""^\s*(\d{10})\.(\d{3})\d*\s+\d+\s+\d+\s+I\s+VrApi\s*:\s*FPS=(\d{1,3})/\d{1,3}(?=,|\s|$)(.*)$""")

        internal fun parse(line: String, since: Long, wallMs: Long, elapsedMs: Long): QuestFrameSample? {
            val match = report.find(line) ?: return null
            val (seconds, millis, fps, fields) = match.destructured
            val sampled = seconds.toLong() * 1000 + millis.toLong()
            val age = wallMs - sampled
            if (sampled < since || age !in 0..2_500) return null
            var stale: Int? = null
            var gpuMs: Double? = null
            for (field in fields.split(',')) {
                val token = field.trim()
                when {
                    token.startsWith("Stale=") ->
                        stale = token.substringAfter('=').toIntOrNull()?.takeIf { it >= 0 }
                    token.startsWith("App=") && token.endsWith("ms") ->
                        gpuMs = token.substringAfter('=').removeSuffix("ms").toDoubleOrNull()
                            ?.takeIf { it.isFinite() && it >= 0 }
                }
            }
            return QuestFrameSample(fps.toInt(), elapsedMs - age, stale, gpuMs)
        }
    }
}
