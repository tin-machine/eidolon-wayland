package io.github.tinmachine.eidolon

import android.os.Process
import android.os.SystemClock
import java.io.IOException
import kotlin.concurrent.thread

internal data class QuestFpsSample(val fps: Int, val at: Long)

/** Meta emits one VrApi FPS report per second from our own XR process. */
internal class QuestXrFrameRate : AutoCloseable {
    @Volatile private var closed = false
    @Volatile private var latest: QuestFpsSample? = null
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
                // OS builds which restrict the own-process stream keep the FPS field unavailable.
            } finally {
                close()
            }
        }
    }

    fun fps(now: Long): Int? = latest?.takeIf { !closed && now - it.at in 0..2_500 }?.fps

    @Synchronized override fun close() {
        closed = true
        child?.destroy()
        child = null
    }

    companion object {
        private val report = Regex("""^\s*(\d{10})\.(\d{3})\d*\s+\d+\s+\d+\s+I\s+VrApi\s*:\s*FPS=(\d{1,3})/\d{1,3}(?:,|\s|$)""")

        internal fun parse(line: String, since: Long, wallMs: Long, elapsedMs: Long): QuestFpsSample? {
            val match = report.find(line) ?: return null
            val (seconds, millis, fps) = match.destructured
            val sampled = seconds.toLong() * 1000 + millis.toLong()
            val age = wallMs - sampled
            if (sampled < since || age !in 0..2_500) return null
            return QuestFpsSample(fps.toInt(), elapsedMs - age)
        }
    }
}
