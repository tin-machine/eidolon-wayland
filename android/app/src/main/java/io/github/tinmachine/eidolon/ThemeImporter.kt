package io.github.tinmachine.eidolon

import android.content.Context
import android.net.Uri
import android.os.CancellationSignal
import android.os.SystemClock
import android.system.Os
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/** Application scope、実行中一件＋待機最新一件。URIやentry名を永続設定には保存しない。 */
object ThemeImporter {
    private val worker = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(1),
        { task -> Thread(task, "eidolon-theme-import").apply { isDaemon = true } }, ThreadPoolExecutor.DiscardOldestPolicy())
    private val watchdog = java.util.concurrent.Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "eidolon-theme-import-cancel").apply { isDaemon = true }
    }
    fun read(context: Context, uri: Uri, token: String, active: () -> Boolean, done: (File?, String?) -> Unit) {
        val app = context.applicationContext
        worker.execute {
            val root = File(app.noBackupFilesDir, "themes")
            val archive = File(root, ".$token.zip")
            val staging = File(root, ".$token.part")
            val output = File(root, "$token.bundle")
            val signal = CancellationSignal()
            val deadline = SystemClock.elapsedRealtime() + 30_000
            var delivered = false
            val timeout = watchdog.schedule({ signal.cancel() }, 30, TimeUnit.SECONDS)
            fun checkActive() {
                check(active() && SystemClock.elapsedRealtime() < deadline && !signal.isCanceled) { "読み込みを取り消しました" }
            }
            try {
                checkActive()
                val directory = Os.lstat(root.absolutePath)
                check(android.system.OsConstants.S_ISDIR(directory.st_mode) && directory.st_uid == android.os.Process.myUid() && directory.st_mode and 511 == 448)
                check(archive.createNewFile()); Os.chmod(archive.absolutePath, 384)
                app.contentResolver.openAssetFileDescriptor(uri, "r", signal).use { descriptor ->
                    requireNotNull(descriptor) { "ファイルを開けません" }
                    descriptor.createInputStream().use { input ->
                        val poll = android.system.StructPollfd().apply { fd = input.fd; events = android.system.OsConstants.POLLIN.toShort() }
                        FileOutputStream(archive).use { target ->
                            val bytes = ByteArray(8192); var total = 0
                            while (true) {
                                checkActive()
                                if (Os.poll(arrayOf(poll), 100) == 0) continue
                                checkActive()
                                val n = input.read(bytes)
                                if (n < 0) break
                                total += n
                                require(total <= ThemeArchive.MAX_ARCHIVE) { "テーマZIPの容量上限を超えています" }
                                target.write(bytes, 0, n)
                            }
                        }
                    }
                }
                val bytes = ThemeArchive.convert(archive, ::checkActive)
                checkActive()
                check(staging.createNewFile()); Os.chmod(staging.absolutePath, 384)
                FileOutputStream(staging).use { it.write(bytes); it.fd.sync() }
                checkActive()
                check(!output.exists() && staging.renameTo(output))
                checkActive()
                delivered = true
                done(output, null)
            } catch (_: Exception) {
                done(null, if (active()) "テーマを読み込めません。ZIPの構成・容量・内容を確認してください。" else null)
            } finally {
                timeout.cancel(false)
                archive.delete(); staging.delete()
                if (!delivered) output.delete()
            }
        }
    }
}
