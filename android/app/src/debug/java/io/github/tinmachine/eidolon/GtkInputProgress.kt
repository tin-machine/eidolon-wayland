package io.github.tinmachine.eidolon

/** ACK/focusとcounterの公開順は別。固定入力の範囲内だけ待ち、最新値を新baselineにしない。 */
internal class GtkInputProgress(private val baseKeys:Long,private val baseButtons:Long,private val dialog:Boolean) {
    data class Observed(val keys:Long,val buttons:Long,val keysPressed:Int,val buttonsPressed:Int)
    enum class State { WAITING, COMPLETE, REJECTED }
    private var phase=""
    private var started=0L
    private var previousTime=0L
    private var previous=Observed(baseKeys,baseButtons,0,0)
    private var observed=previous
    private var floorKeys=baseKeys;private var floorButtons=baseButtons
    private var expectedKeys=baseKeys;private var expectedButtons=baseButtons
    var state=State.WAITING;private set
    var reason="WAIT_COUNTERS";private set
    init {require(baseKeys in 0..Long.MAX_VALUE-4 && baseButtons in 0..Long.MAX_VALUE-4)}
    fun observe(stage:String,now:Long,value:Observed):State {
        if(state==State.REJECTED) return state
        val bounds=when(stage) {
            "WAIT_FOCUS","WAIT_DIALOG"->listOf(0,0,0,2)
            "WAIT_KEYS"->listOf(0,2,4,2)
            "WAIT_CLICK"->listOf(4,2,4,4)
            "WAIT_DIALOG_FOCUS"->listOf(0,2,0,4)
            "DIALOG_READY"->listOf(0,4,0,4)
            "WAIT_DIALOG_CLOSED"->listOf(0,4,2,4)
            "OPERATIONS_COMPLETE","CLOSING","WAIT_RETIRED"->listOf(if(dialog) 2 else 4,4,if(dialog) 2 else 4,4)
            else->error("GTK_PROGRESS_PHASE")
        }
        observed=value
        if(now<0 || (phase.isNotEmpty() && now<previousTime)) return reject("CLOCK_REGRESSED")
        previousTime=now
        if(stage!=phase) {
            phase=stage;started=now;state=State.WAITING
            floorKeys=baseKeys+bounds[0];floorButtons=baseButtons+bounds[1]
            expectedKeys=baseKeys+bounds[2];expectedButtons=baseButtons+bounds[3]
        }
        if(value.keys<floorKeys || value.buttons<floorButtons || value.keys<previous.keys || value.buttons<previous.buttons) return reject("COUNTER_REGRESSED")
        if(value.keys>expectedKeys || value.buttons>expectedButtons) return reject("COUNTER_EXCESS")
        if(value.keysPressed !in 0..1 || value.buttonsPressed !in 0..1 ||
            (state==State.COMPLETE && (value.keysPressed!=0 || value.buttonsPressed!=0)) ||
            (expectedKeys==floorKeys && value.keysPressed!=0) || (expectedButtons==floorButtons && value.buttonsPressed!=0)) return reject("UNEXPECTED_HELD")
        // 完了後のoperator待ちはcase全体のdeadlineで制限。遅れて到着した完了は成功へ変更しない。
        if(state!=State.COMPLETE && now-started>=6000) return reject("PROGRESS_TIMEOUT")
        previous=value
        state=if(value.keys==expectedKeys && value.buttons==expectedButtons && value.keysPressed==0 && value.buttonsPressed==0) State.COMPLETE else State.WAITING
        reason=if(state==State.COMPLETE) "COUNTERS_COMPLETE" else "WAIT_COUNTERS"
        return state
    }
    private fun reject(value:String):State {state=State.REJECTED;reason=value;return state}
    fun metadata():Map<String,Any> = linkedMapOf("schema" to 1,"phase" to phase,"state" to state.name.lowercase(),"reason" to reason,
        "elapsed_ms" to (previousTime-started).coerceIn(0,6000),"floor_keys" to floorKeys,"floor_buttons" to floorButtons,
        "expected_keys" to expectedKeys,"expected_buttons" to expectedButtons,"observed_keys" to observed.keys,"observed_buttons" to observed.buttons,
        "keys_pressed" to observed.keysPressed,"buttons_pressed" to observed.buttonsPressed)
}
