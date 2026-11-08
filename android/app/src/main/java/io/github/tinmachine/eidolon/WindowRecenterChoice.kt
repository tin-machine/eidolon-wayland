package io.github.tinmachine.eidolon

import kotlin.math.abs
import kotlin.math.sqrt

/** Caller must capture current full-target identities and applied geometry from one runtime/scene. */
data class WindowRecenterOccupant(val identity:String,val pose:WindowMotionPose,val physical:WindowPhysicalSize)

/** Pure prediction only. Neither chosen placement nor SAT separation is proof of SDK geometry. */
object WindowRecenterChoice {
    private val offsets=listOf(0.0,-.03,.03,-.06,.06,-.09,.09,-.12,.12)
    private const val HALF_DEPTH=.0125 // Two body boxes reserve a 25 mm normal separation.
    private const val EDGE_MARGIN=.005
    class Result internal constructor(val reason:String,val pose:WindowMotionPose?,val candidateIndex:Int?,val tested:Int) {
        override fun toString()="WindowRecenterChoice(reason=$reason,candidateIndex=$candidateIndex,tested=$tested)"
    }
    /** At most nine fixed distances, one viewer sample, at most eight other body boxes. No mutation/retry. */
    fun choose(viewer:WindowMotionPose,selectedIdentity:String,physical:WindowPhysicalSize,others:List<WindowRecenterOccupant>):Result {
        fun validIdentity(value:String)=value.isNotBlank() && value.length<=256 && value.none {it.isISOControl()}
        if(!validIdentity(selectedIdentity) || !physical.valid() || others.size>8 || others.any {
            !validIdentity(it.identity) || it.identity==selectedIdentity || !it.pose.valid(true) || !it.physical.valid()
        } || others.map {it.identity}.toSet().size!=others.size) return Result("invalid_scope",null,null,0)
        val initial=WindowRecenterPose.candidate(viewer) ?: return Result("viewer_unavailable",null,null,0)
        val forward=initial.p.zip(viewer.p).map {(a,b)->(a-b)/WindowRecenterPose.DISTANCE_M}
        val obstacles=others.map {box(it.pose,it.physical)}
        var tested=0
        for((index,offset) in offsets.withIndex()) {
            val next=initial.copy(p=initial.p.zip(forward).map {(at,direction)->at+direction*offset})
            tested++
            if(!next.valid(true)) continue // Never clamp the candidate or change its yaw/height.
            val candidate=box(next,physical)
            if(obstacles.none {overlaps(candidate,it)}) return Result("candidate",next,index,tested)
        }
        return Result("no_separated_candidate",null,null,tested)
    }
    private data class Box(val center:List<Double>,val axes:List<List<Double>>,val half:List<Double>)
    private fun box(pose:WindowMotionPose,size:WindowPhysicalSize):Box {
        val norm=sqrt(pose.q.sumOf {it*it});val unit=pose.copy(q=pose.q.map {it/norm})
        val axes=listOf(WindowMotionPose.at(1.0,0.0,0.0),WindowMotionPose.at(0.0,1.0,0.0),WindowMotionPose.at(0.0,0.0,1.0)).map {
            (unit*it).p.zip(unit.p).map {(a,b)->a-b}
        }
        return Box(pose.p,axes,listOf(size.width/2.0+EDGE_MARGIN,size.height/2.0+EDGE_MARGIN,HALF_DEPTH))
    }
    private fun dot(a:List<Double>,b:List<Double>)=a.zip(b).sumOf {(x,y)->x*y}
    private fun cross(a:List<Double>,b:List<Double>)=listOf(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
    private fun overlaps(a:Box,b:Box):Boolean {
        val delta=b.center.zip(a.center).map {(x,y)->x-y}
        // Fifteen separating axes for two oriented boxes; near-parallel cross axes carry no separation information.
        val axes=a.axes+b.axes+a.axes.flatMap {x->b.axes.map {y->cross(x,y)}}
        return axes.none {axis ->
            val norm=sqrt(dot(axis,axis))
            if(norm<1e-9) false else {
                val n=axis.map {it/norm}
                val radius=a.axes.zip(a.half).sumOf {(u,h)->abs(dot(n,u))*h}+b.axes.zip(b.half).sumOf {(u,h)->abs(dot(n,u))*h}
                abs(dot(delta,n))>radius+1e-6 // Touching/rounding is conservatively occupied.
            }
        }
    }
}
