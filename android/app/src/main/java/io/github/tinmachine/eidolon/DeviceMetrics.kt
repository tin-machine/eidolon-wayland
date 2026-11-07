package io.github.tinmachine.eidolon

import android.app.ActivityManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.os.BatteryManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.util.Locale
import org.json.JSONObject

data class DeviceMetricsSnapshot(
    val sampledMs: Long = 0,
    val batteryPercent: Int? = null,
    val charging: Boolean? = null,
    val availableRam: Long? = null,
    val totalRam: Long? = null,
    val addresses: List<String> = emptyList(),
    val network: String = "unknown",
    val processCpu: ProcessCpuReading = ProcessCpuReading(),
) {
    fun summary(): String {
        val battery = batteryPercent?.let { "$it%" } ?: "不明"
        val charge = when(charging) { true -> "充電中"; false -> "放電中"; null -> "状態不明" }
        fun gib(v: Long?) = v?.let { String.format(Locale.ROOT, "%.1f", it / 1073741824.0) } ?: "?"
        return "電池 $battery · $charge / RAM空き ${gib(availableRam)} / ${gib(totalRam)} GiB"
    }
    fun networkSummary(): String = when(network) {
        "connected" -> "Quest IP: ${addresses.joinToString(", ").ifEmpty { "取得待ち" }}"
        "disconnected" -> "Questネットワーク: 未接続"
        else -> "Quest IP: 取得できません"
    }
    fun processCpuSummary(): String = when(processCpu.status) {
        ProcessCpuStatus.AVAILABLE -> String.format(Locale.ROOT, "Eidolon CPU %.1f%%（1コア=100%%、直近%.1f秒平均）",
            processCpu.percent, checkNotNull(processCpu.intervalMs) / 1000.0)
        ProcessCpuStatus.WARMING_UP -> "Eidolon CPU 計測中（初回待ち）"
        ProcessCpuStatus.UNAVAILABLE -> "Eidolon CPU 取得できません"
        ProcessCpuStatus.STALE -> "Eidolon CPU 欠測・再計測中"
        ProcessCpuStatus.STOPPED -> "Eidolon CPU 計測停止"
    }
    fun json(): JSONObject = JSONObject().put("sampled_monotonic_ms",sampledMs)
        .put("battery_percent",batteryPercent ?: JSONObject.NULL).put("charging",charging ?: JSONObject.NULL)
        .put("available_ram_bytes",availableRam ?: JSONObject.NULL).put("total_ram_bytes",totalRam ?: JSONObject.NULL)
        .put("network",network).put("addresses",org.json.JSONArray(addresses))
        .put("process_cpu_percent",processCpu.percent ?: JSONObject.NULL)
        .put("process_cpu_status",processCpu.status.wireValue)
        .put("process_cpu_sampled_monotonic_ms",processCpu.sampledMonotonicMs ?: JSONObject.NULL)
        .put("process_cpu_interval_ms",processCpu.intervalMs ?: JSONObject.NULL)
        .put("process_cpu_basis","one_core_100_percent")
}

/** Activityの可視期間だけ購読する。自プロセスCPUは既存5秒tickで採り、重いPSSは扱わない。 */
class DeviceMetrics(context: Context, private val publish: (DeviceMetricsSnapshot) -> Unit,
    private val elapsedMillis: () -> Long = SystemClock::elapsedRealtime,
    private val processCpuMillis: () -> Long? = { android.os.Process.getElapsedCpuTime() },
) {
    private val app = context.applicationContext
    private val handler = Handler(Looper.getMainLooper())
    private var active = false
    private var battery: Intent? = null
    private val cpu = ProcessCpuTracker()
    private var latest = DeviceMetricsSnapshot()
    private val receiver = object: BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) { battery=intent; if(active) sample() }
    }
    private val tick = object: Runnable {
        override fun run() { if(active) {sample();handler.postDelayed(this,ProcessCpuTracker.INTERVAL_MS)} }
    }
    fun start() {
        if(active) return
        active=true;cpu.start()
        battery=app.registerReceiver(receiver,IntentFilter(Intent.ACTION_BATTERY_CHANGED),Context.RECEIVER_NOT_EXPORTED)
        handler.post(tick)
    }
    fun stop() {
        if(!active) return
        active=false;handler.removeCallbacks(tick)
        latest=latest.copy(processCpu=cpu.stop())
        // CPU is stopped; do not refresh the timestamps of unmeasured battery/RAM/IP.
        try { publish(latest) } finally { app.unregisterReceiver(receiver) }
    }
    private fun sample() {
        val level=battery?.getIntExtra(BatteryManager.EXTRA_LEVEL,-1) ?: -1
        val scale=battery?.getIntExtra(BatteryManager.EXTRA_SCALE,-1) ?: -1
        val percent=if(level>=0 && scale>0 && level<=scale) (level.toLong()*100/scale).toInt() else null
        val status=battery?.getIntExtra(BatteryManager.EXTRA_STATUS,-1)
        val charging=when(status) {
            BatteryManager.BATTERY_STATUS_CHARGING, BatteryManager.BATTERY_STATUS_FULL -> true
            BatteryManager.BATTERY_STATUS_DISCHARGING, BatteryManager.BATTERY_STATUS_NOT_CHARGING -> false
            else -> null
        }
        val memory=runCatching { ActivityManager.MemoryInfo().also {app.getSystemService(ActivityManager::class.java).getMemoryInfo(it)} }.getOrNull()
        val network=runCatching {
            val manager=app.getSystemService(ConnectivityManager::class.java)
            val current=manager.activeNetwork
            if(current==null) "disconnected" to emptyList<String>() else {
                val links=manager.getLinkProperties(current)
                (if(links==null) "unknown" else "connected") to (links?.linkAddresses?.mapNotNull { link ->
                    link.address.takeUnless {it.isLoopbackAddress || it.isLinkLocalAddress}?.hostAddress?.substringBefore('%')
                }?.distinct()?.take(8) ?: emptyList())
            }
        }.getOrElse {"unknown" to emptyList()}
        val now=elapsedMillis()
        latest=DeviceMetricsSnapshot(now,percent,charging,
            memory?.availMem?.takeIf {it>=0},memory?.totalMem?.takeIf {it>0},network.second,network.first,
            cpu.sample(now,processCpuMillis))
        publish(latest)
    }
}
