package io.github.tinmachine.eidolon

import org.json.JSONObject

/** 通常IMEの初期対象は、同sessionの認証済み起動receiptと一意なmarkerを持つfootに限る。 */
internal object ImeTerminalEligibility {
    fun allows(runtime:JSONObject,target:ImeSdkTarget):Boolean = try {
        check(target.valid() && runtime.getString("state")=="Ready" && runtime.getString("scene")==target.scene)
        fun matches(key:JSONObject)=key.optString("session")==target.session && key.optString("window")==target.window &&
            key.optString("scene")==target.scene && key.optString("generation")==target.generation
        val plans=runtime.getJSONArray("panels")
        check((0 until plans.length()).map {plans.getJSONObject(it)}.any {it.optString("action")=="create" && it.optBoolean("applied") && it.isNull("error") && matches(it.getJSONObject("target"))})
        val rendered=runtime.getJSONObject("render").getJSONArray("targets")
        check((0 until rendered.length()).map {rendered.getJSONObject(it)}.any {matches(it.getJSONObject("key")) && it.getLong("frames")>0 && !it.optBoolean("presentation_paused")})
        val app=runtime.getJSONObject("app");val transport=runtime.getJSONObject("transport")
        val nonce=app.getString("nonce")
        check(nonce.matches(Regex("[0-9a-f]{16}")) && app.getString("mode")=="foot" && app.getString("state")=="running" && app.isNull("error") &&
            app.getString("session")==target.session && transport.getString("session")==target.session && transport.getString("state")=="Ready")
        val forward=transport.getJSONObject("app_forward");val remote=forward.getJSONObject("remote")
        check(forward.getString("nonce")==nonce && forward.getString("state")=="running" && remote.getString("phase")=="running" &&
            !remote.getBoolean("eof") && !remote.getBoolean("exit_signal") && remote.isNull("exit_status") &&
            remote.isNull("finished") && remote.isNull("error"))
        // cleanup_pendingはlive ownerに回収責務が残る意味。running中もtrueなので終了開始の判定には使わない。
        val rows=app.getJSONArray("windows").let {a->(0 until a.length()).map {a.getJSONObject(it)}}
        val window=rows.single {it.getString("window")==target.window && it.getString("life")=="Mapped"}
        val appId=window.getString("app_id");val prefix="io.github.tinmachine.eidolon.foot.t-$nonce-"
        check(appId.startsWith(prefix))
        val terminal=appId.removePrefix(prefix)
        check(terminal.matches(Regex("[1-9][0-9]{0,18}")) && terminal.toLongOrNull()!=null &&
            rows.count {it.optString("app_id")==appId && it.optString("life")!="Destroyed"}==1)
        val launches=remote.getJSONArray("terminal_launches")
        val receipt=(0 until launches.length()).map {launches.getJSONObject(it)}.single {it.opt("terminal_id")==terminal}
        receipt.opt("accepted").let {it is Boolean && it}
    } catch(_:Exception) {false}
}
