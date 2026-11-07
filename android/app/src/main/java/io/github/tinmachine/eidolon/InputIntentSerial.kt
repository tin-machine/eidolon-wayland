package io.github.tinmachine.eidolon

internal data class InputRetirement(val runtime:Long,val target:String)

/** 比較からnative enqueueまでを同じ境界で直列化する。callbackから外部lockを保持して入らない。 */
internal class InputIntentSerial(private val clock:()->Long={System.nanoTime()/1_000_000}) {
    class Trial internal constructor(internal var expected:Long) {
        internal var lost=false
        internal var sealed=false
        internal val retire=mutableSetOf<InputRetirement>()
        internal var replacement:InputResizeReplacement?=null
        internal var replacementCount=0
        internal var lastReplacement=0L
    }
    private val lock=Any()
    private var value=0L
    private var active:Trial?=null
    fun <T> serial(action:()->T):T=synchronized(lock,action)
    fun get():Long=serial {value}
    fun invalidate()=serial {active?.lost=true}
    fun incrementAndGet():Long=serial {invalidate();++value}
    fun capture():Trial=serial {
        invalidate();++value;Trial(value).also {active=it}
    }
    fun current(trial:Trial):Boolean=serial {
        if(trial.replacement?.live(clock())==false) trial.lost=true
        active===trial && !trial.lost && trial.expected==value
    }
    fun <T> dispatch(trial:Trial,context:()->Boolean,action:()->T):T?=serial {
        if(!current(trial) || trial.sealed || trial.replacement!=null) null
        else if(!context()) {trial.lost=true;null}
        else action()
    }
    fun ownedIncrement(trial:Trial):Long=serial {
        check(current(trial));++value;trial.expected=value;value
    }
    fun seal(trial:Trial):Boolean=serial {
        (current(trial) && trial.replacement==null).also {if(it) trial.sealed=true}
    }
    fun isSealed(trial:Trial):Boolean=serial {current(trial) && trial.sealed}
    fun expectRetirement(trial:Trial,key:InputRetirement):Boolean=serial {
        (current(trial) && trial.sealed).also {if(it) trial.retire.add(key)}
    }
    /** 入力を再開できないsealed trialの事前指定targetだけ。外部resetは従来どおり永久失効。 */
    fun retiring(key:InputRetirement):Long=serial {
        val trial=active
        if(trial!=null && current(trial) && trial.replacement?.retiring(key,clock())==true) ownedIncrement(trial)
        else if(trial!=null && current(trial) && trial.sealed && trial.retire.remove(key)) ownedIncrement(trial)
        else incrementAndGet()
    }
    fun beginReplacement(trial:Trial,replacement:InputResizeReplacement):Boolean=serial {
        if(!current(trial) || trial.sealed || trial.replacement!=null || trial.replacementCount>=2 ||
            replacement.request<=trial.lastReplacement || !replacement.live(clock())) false
        else {trial.replacement=replacement;trial.replacementCount++;trial.lastReplacement=replacement.request;true}
    }
    fun completeReplacement(trial:Trial,replacement:InputResizeReplacement,proof:InputResizeReplacement.Proof):Boolean=serial {
        if(!current(trial) || trial.replacement!==replacement || !replacement.observe(proof,clock())) false
        else {trial.replacement=null;true}
    }
    fun release(trial:Trial)=serial {if(active===trial) {trial.lost=true;active=null}}
}
