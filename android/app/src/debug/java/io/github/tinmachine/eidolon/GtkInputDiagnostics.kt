package io.github.tinmachine.eidolon

import org.json.JSONObject

/** 本文/key列は記録せず、最後に判定したnative snapshotを固定型で保存する。 */
internal object GtkInputDiagnostics {
    fun observe(gate:GtkInputProgress,phase:String,now:Long,runtime:JSONObject,epoch:Long,focus:String?,token:String?):JSONObject {
        val input=runtime.getJSONObject("app").getJSONObject("input");val pointer=input.getJSONObject("pointer")
        gate.observe(phase,now,GtkInputProgress.Observed(input.getLong("delivered"),pointer.getLong("buttons"),input.getInt("pressed"),pointer.getInt("pressed")))
        return JSONObject(gate.metadata()).put("expected_epoch",epoch).put("observed_epoch",runtime.getLong("input_resets"))
            .put("expected_focus_window",focus ?: JSONObject.NULL).put("observed_focus_window",input.opt("focus_window") ?: JSONObject.NULL)
            .put("focus_token_matches",token?.let {input.optString("focus_token")==it} ?: JSONObject.NULL)
    }
}
