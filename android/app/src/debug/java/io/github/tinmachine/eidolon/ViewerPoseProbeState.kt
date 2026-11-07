package io.github.tinmachine.eidolon

/** Frame counters are liveness evidence only; Android polling time is never reported as tracking sample age. */
data class ViewerPoseOwner(val runtime:Long,val scene:Long,val sdk:Long)
class ViewerPoseProbeState(val owner:ViewerPoseOwner,val start:Long,val initialFrames:Long) {
    enum class Phase { SAMPLING, COMPLETE, FAILED }
    var phase=Phase.SAMPLING;private set
    var reason="sampling";private set
    var samples=0;private set
    var candidates=0;private set
    var motionObserved=false;private set
    var renderAdvanced=false;private set
    var originObserved=false;private set
    private var calls=0
    private var lastTime=start
    private var sampledAt:Long?=null
    private var lastFrames=initialFrames
    private var first:WindowMotionPose?=null
    init {require(owner.runtime>0 && owner.scene>0 && owner.sdk!=0L && start>=0 && initialFrames>=0)}
    fun fail(value:String) {if(phase==Phase.SAMPLING) {phase=Phase.FAILED;reason=value}}
    fun observe(current:ViewerPoseOwner,visibleFocused:Boolean,now:Long,pose:WindowMotionPose,frames:Long,sdkMathMatches:Boolean) {
        if(phase!=Phase.SAMPLING) return
        calls++
        if(current!=owner) {fail("owner_changed");return}
        if(!visibleFocused) {fail("not_focused");return}
        if(now<lastTime || now<start) {fail("clock_reversed");return}
        lastTime=now
        if(now-start>=5000 || calls>128) {fail("timeout");return}
        if(frames<lastFrames) {fail("render_reversed");return}
        lastFrames=frames;renderAdvanced=frames>initialFrames
        if(!pose.valid()) {fail("invalid_pose");return}
        if(!sdkMathMatches) {fail("sdk_math_mismatch");return}
        if(sampledAt?.let {now-it<100}==true) return
        sampledAt=now
        if(samples>=20) {if(renderAdvanced) {phase=Phase.COMPLETE;reason="sampled"};return}
        samples++
        if(first==null) first=pose else if(!checkNotNull(first).near(pose,.0001)) motionObserved=true
        if(pose.p.all {kotlin.math.abs(it)<.000001}) originObserved=true
        if(WindowRecenterPose.candidate(pose)!=null) candidates++
        if(samples>=20 && now-start>=1900 && renderAdvanced) {phase=Phase.COMPLETE;reason="sampled"}
    }
}
