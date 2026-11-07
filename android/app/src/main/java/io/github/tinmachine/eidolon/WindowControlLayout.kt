package io.github.tinmachine.eidolon

import kotlin.math.sqrt

/** Metres in the body's plane; controls never inherit the ring canvas or animation transform. */
data class WindowControlLayout(val anchor: String="top-right", val offsetX: Double=0.0, val offsetY: Double=0.0,
    val diameter: Double=0.08, val gap: Double=0.025) {
    fun valid()=anchor in setOf("top-left","top-right","bottom-left","bottom-right") &&
        listOf(offsetX,offsetY).all {it.isFinite() && it in 0.0..0.2} && diameter.isFinite() && diameter in 0.06..0.12 && gap.isFinite() && gap in 0.02..0.08
    data class Placement(val action: String,val x: Double,val y: Double,val diameter: Double)
    fun positions(width: Double,height: Double): List<Placement> {
        require(valid() && listOf(width,height).all {it.isFinite() && it in 0.25..3.0})
        val side=if(anchor.endsWith("right")) 1 else -1
        val vertical=if(anchor.startsWith("top")) 1 else -1
        val x=side*(width/2+offsetX)
        val y=vertical*(height/2+gap+diameter/2+offsetY)
        return listOf(Placement("focus",x-side*(diameter+gap),y,diameter),Placement("close",x,y,diameter))
    }
}

/** Bounded button gestures. Each source/up belongs to its original gesture and layout owner. */
class WindowControlPress {
    private data class Press(val owner: String,val downTime: Long,val point: List<Double>,var cancelled: Boolean=false)
    private val presses=linkedMapOf<Long,Press>()
    fun contains(source: Long,downTime: Long)=presses[source]?.downTime==downTime
    fun down(source: Long,downTime: Long,owner: String,point: List<Double>): Boolean {
        if(!valid(point) || downTime<0 || (source !in presses && presses.size>=8)) return false
        if(presses[source]?.let {downTime<=it.downTime}==true) return false
        presses[source]=Press(owner,downTime,point.toList());return true
    }
    fun move(source: Long,point: List<Double>,threshold: Double) {
        val press=presses[source] ?: return
        if(!valid(point) || !threshold.isFinite() || threshold<=0 ||
            sqrt(point.zip(press.point).sumOf {(a,b)->(a-b)*(a-b)})>threshold) press.cancelled=true
    }
    fun up(source: Long,downTime: Long,owner: String): Boolean {
        val press=presses[source] ?: return false
        if(press.downTime!=downTime) return false // A late old up cannot release a new down.
        presses.remove(source)
        return !press.cancelled && press.owner==owner
    }
    fun cancel(source: Long) {presses.remove(source)}
    fun clear() {presses.clear()}
    private fun valid(point: List<Double>)=point.size==3 && point.all {it.isFinite()}
}
