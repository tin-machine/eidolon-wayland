package io.github.tinmachine.eidolon

import kotlin.math.sqrt

/** 正面候補のpure計算だけ。trackingの有効性・時刻を推測せず、永続化/SDK操作もしない。 */
object WindowRecenterPose {
    const val DISTANCE_M=1.5
    fun candidate(viewer:WindowMotionPose):WindowMotionPose? {
        if(!viewer.valid()) return null
        val q=viewer.q;val norm=sqrt(q.sumOf {it*it});val unit=q.map {it/norm}
        // SDK0.15 Pose.removePitchAndRoll(): quaternionのw/yだけを再正規化する。
        val yawNorm=sqrt(unit[1]*unit[1]+unit[3]*unit[3])
        val fx=2*(unit[0]*unit[2]+unit[3]*unit[1]);val fz=unit[3]*unit[3]+unit[2]*unit[2]-unit[1]*unit[1]-unit[0]*unit[0]
        if(yawNorm<.1 || sqrt(fx*fx+fz*fz)<.1) return null // 上下を向く特異点で任意の正面を作らない。
        val y=unit[1]/yawNorm;val w=unit[3]/yawNorm
        val forwardX=2*y*w;val forwardZ=w*w-y*y
        return WindowMotionPose(listOf(viewer.p[0]+forwardX*DISTANCE_M,viewer.p[1],viewer.p[2]+forwardZ*DISTANCE_M),
            listOf(0.0,y,0.0,w)).takeIf {it.valid(window=true)}
    }
}
