package io.github.tinmachine.eidolon

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.io.File
import java.security.MessageDigest
import org.json.JSONObject

/** 固定寸法のSDK検査をnonce別設定で実行し、製品設定と通常UIの復帰後だけ結果を公開する。 */
object SdkCaseEnvironment {
    fun start(name:String,nonce:String,context:Context,launch:((JSONObject)->Unit)->Unit,complete:()->Unit) {
        val ui=Handler(Looper.getMainLooper())
        val deadline=SystemClock.elapsedRealtime()+15000
        ui.post(object:Runnable {
            override fun run() {
                val original=NativeSessionHost.currentHandle()
                if(original>0 && JSONObject(NativeBridge.nativeSnapshot(original)).optJSONObject("runtime")?.optString("state")=="Ready") {
                    startReady(name,nonce,context,original,launch,complete)
                } else if(SystemClock.elapsedRealtime()<deadline) ui.postDelayed(this,32)
                else {
                    NativeSessionHost.publishCheck(JSONObject().put("case",name).put("nonce",nonce).put("result","fail")
                        .put("environment_failure","INITIAL_RUNTIME_TIMEOUT"))
                    complete()
                }
            }
        })
    }
    private fun startReady(name:String,nonce:String,context:Context,original:Long,launch:((JSONObject)->Unit)->Unit,complete:()->Unit) {
        val ui=Handler(Looper.getMainLooper())
        var originalDigest=""
        var fixture=0L
        var finished=false
        fun digest():String {
            val file=File(context.noBackupFilesDir,"workspace/layout.json")
            if(!file.exists()) return "absent"
            check(file.length()<=65536)
            return MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") {"%02x".format(it.toInt() and 255)}
        }
        fun restore(value:JSONObject) {
            if(finished) return
            finished=true
            val result=JSONObject(value.toString())
            val last=NativeSessionHost.currentHandle()
            NativeSessionHost.withStoppedRuntime({BuildHooks.preferencesFixture=null},{
                val deadline=SystemClock.elapsedRealtime()+30000
                ui.post(object:Runnable {
                    override fun run() {
                        try {
                            check(SystemClock.elapsedRealtime()<deadline) {"PRODUCT_RESTORE_TIMEOUT"}
                            val current=NativeSessionHost.currentHandle()
                            if(current>last) {
                                val s=JSONObject(NativeBridge.nativeSnapshot(current)).getJSONObject("runtime")
                                check(s.getString("state")!="Failed")
                                val targets=s.getJSONObject("render").getJSONArray("targets")
                                if(s.getString("state")=="Ready" && s.getBoolean("activity_visible") && s.getString("xr_state")=="FOCUSED" &&
                                    s.getJSONObject("transport").getString("state")=="Idle" && targets.length()==1 &&
                                    targets.getJSONObject(0).optString("role")=="diagnostic" && targets.getJSONObject(0).getLong("frames")>=3) {
                                    check(s.getJSONObject("transport").getJSONArray("pending_cleanup").length()==0)
                                    check(originalDigest.isNotEmpty() && digest()==originalDigest)
                                    result.put("product_settings_untouched",true).put("product_restored",s)
                                        .put("preferences_environment",JSONObject().put("original_handle",original.toString()).put("fixture_handle",fixture.toString())
                                            .put("last_handle",last.toString()).put("product_handle",current.toString()))
                                    NativeSessionHost.publishCheck(result);complete();return
                                }
                            }
                            ui.postDelayed(this,32)
                        } catch(failure:Exception) {
                            result.put("result","fail").put("environment_failure",failure.javaClass.simpleName)
                                .put("environment_failure_site",failure.stackTrace.firstOrNull()?.toString())
                            NativeSessionHost.publishCheck(result);complete()
                        }
                    }
                })
            })
        }
        NativeSessionHost.withStoppedRuntime({
            originalDigest=digest()
            BuildHooks.preferencesFixture=nonce
        },{
            try {
                fixture=NativeSessionHost.currentHandle()
                check(originalDigest.isNotEmpty() && fixture>original && BuildHooks.preferencesFixture==nonce)
                launch(::restore)
            } catch(failure:Exception) {
                restore(JSONObject().put("case",name).put("nonce",nonce).put("result","fail").put("environment_failure",failure.javaClass.simpleName))
            }
        })
    }
}
