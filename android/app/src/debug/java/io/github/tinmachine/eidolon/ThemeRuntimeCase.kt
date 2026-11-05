package io.github.tinmachine.eidolon

import android.content.Context
import android.os.SystemClock
import android.system.Os
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.json.JSONArray
import org.json.JSONObject

/** APK UIDの実EGL upload/取消/設定保存。SDK/HMD/物理入力の成立は主張しない。 */
object ThemeRuntimeCase {
    fun run(nonce: String, context: Context, store: DiagnosticsStore) {
        val result = JSONObject().put("case","theme-runtime").put("nonce",nonce).put("result","running")
            .put("sdk_verified",false).put("hmd_verified",false).put("physical_input_verified",false)
        val rows = JSONArray()
        val root = File(context.noBackupFilesDir,"theme-check-$nonce")
        var handle = 0L
        var lastHandle = 0L
        var request = 0L
        fun snapshot(): JSONObject = JSONObject(NativeBridge.nativeSnapshot(handle)).getJSONObject("runtime")
        fun waitFor(predicate: () -> Boolean) {
            val deadline = SystemClock.elapsedRealtime()+15_000
            while (!predicate()) {
                check(SystemClock.elapsedRealtime()<deadline) { "THEME_RUNTIME_TIMEOUT" }
                Thread.sleep(5)
            }
        }
        fun command(type: String, values: JSONObject=JSONObject()) {
            values.put("schema",1).put("type",type).put("request_id",(++request).toString())
            check(NativeBridge.nativeCommand(handle,values.toString())==NativeBridge.ACCEPTED)
            var complete=false
            waitFor {
                val events=JSONObject(NativeBridge.nativePoll(handle,64)).getJSONArray("events")
                for(i in 0 until events.length()) {
                    val event=events.getJSONObject(i)
                    if(event.optString("type")=="command_complete" && event.optString("request_id")==request.toString()) {
                        check(event.getString("code")=="ACCEPTED");complete=true
                    }
                }
                complete
            }
        }
        fun settled(selection: String): JSONObject {
            waitFor { val theme=snapshot().getJSONObject("theme");
                theme.getString("selection")==selection && !theme.getBoolean("loading") && !theme.getJSONObject("storage").getBoolean("pending") }
            return snapshot()
        }
        fun record(name: String) { rows.put(JSONObject().put("name",name).put("runtime",snapshot())); result.put("cycles",rows);store.publishCheck(result) }
        fun select(selection: String,reduced: Boolean) { command("set_theme",JSONObject().put("selection",selection).put("reduced_motion",reduced)); settled(selection) }
        fun create() {
            handle=NativeBridge.nativeCreate(JSONObject().put("schema",1).put("preferences_dir",root.absolutePath).toString())
            check(handle>lastHandle)
            waitFor { snapshot().getString("state")=="Ready" && snapshot().getJSONObject("theme").getJSONObject("storage").getBoolean("persistent") }
        }
        fun stop() {
            check(NativeBridge.nativeRequestStop(handle)==NativeBridge.ACCEPTED)
            waitFor { snapshot().getString("state")=="Stopped" }
            val stopped=snapshot()
            check(stopped.getJSONObject("theme").getInt("workers")==0 && stopped.getJSONObject("render").getInt("contexts")==0)
            result.put("stopped",stopped)
            waitFor { NativeBridge.nativeDestroy(handle)==NativeBridge.ACCEPTED }
            lastHandle=handle;handle=0
        }
        try {
            check(root.mkdir());Os.chmod(root.absolutePath,448)
            store.publishCheck(result)
            create();select("builtin",false);record("builtin")
            check(snapshot().getJSONObject("render").getString("theme_id")=="cyan-orbit")
            check(snapshot().getJSONObject("render").getJSONObject("composition").getLong("decoration_texture_bytes")>0)
            val token="11111111111111111111111111111111"
            val zip=File(root,"fixture.zip")
            ZipOutputStream(zip.outputStream()).use { archive ->
                val files=mapOf("theme.toml" to "format_version=1\nid='probe-theme'\nname='Probe'\nstyle='style.css'\n[parts.a]\nkind='svg'\nslot='window-backdrop'\nsrc='a.svg'\nsize=[8.0,8.0]",
                    "style.css" to "#a {opacity:1;}","a.svg" to "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"8\" height=\"8\"><rect width=\"8\" height=\"8\" fill=\"cyan\"/></svg>")
                files.forEach { (name,text) -> archive.putNextEntry(ZipEntry(name));archive.write(text.toByteArray());archive.closeEntry() }
            }
            command("begin_theme_import",JSONObject().put("token",token))
            val imported=File(root,"themes/$token.bundle")
            imported.writeBytes(ThemeArchive.convert(zip) {});Os.chmod(imported.absolutePath,384)
            command("apply_theme_import",JSONObject().put("token",token));settled("imported");record("imported")
            check(snapshot().getJSONObject("render").getString("theme_id")=="probe-theme")
            val revision=snapshot().getJSONObject("theme").getLong("applied_revision")
            val badToken="22222222222222222222222222222222"
            command("begin_theme_import",JSONObject().put("token",badToken))
            val broken=File(root,"themes/$badToken.bundle")
            broken.writeText("broken package");Os.chmod(broken.absolutePath,384)
            command("apply_theme_import",JSONObject().put("token",badToken))
            waitFor { !snapshot().getJSONObject("theme").getBoolean("loading") }
            val failed=snapshot().getJSONObject("theme")
            check(!failed.isNull("error") && failed.getLong("applied_revision")==revision && failed.getString("selection")=="imported")
            record("invalid_preserved")
            select("imported",true);record("reduced")
            command("set_theme",JSONObject().put("selection","builtin").put("reduced_motion",false))
            select("off",true);Thread.sleep(300);record("off")
            check(snapshot().getJSONObject("render").isNull("theme_id"))
            stop();create();settled("off");record("restored_off")
            check(snapshot().getJSONObject("theme").getBoolean("imported_available"))
            check(snapshot().getJSONObject("theme").getBoolean("reduced_motion"))
            select("imported",false);record("restored_import")
            check(snapshot().getJSONObject("render").getString("theme_id")=="probe-theme")
            stop()
            check(root.deleteRecursively())
            result.put("last_handle",lastHandle.toString()).put("private_fixture_removed",true).put("result","pass")
        } catch (failure: Exception) {
            if(handle>0) result.put("failure_snapshot",runCatching {snapshot()}.getOrNull())
            result.put("result","fail").put("failure_class",failure.javaClass.simpleName).put("failure_site",failure.stackTrace.firstOrNull()?.toString())
        } finally {
            if(handle>0) runCatching {stop()}.onFailure {result.put("cleanup_failed",true)}
            store.publishCheck(result.put("cycles",rows))
        }
    }
}
