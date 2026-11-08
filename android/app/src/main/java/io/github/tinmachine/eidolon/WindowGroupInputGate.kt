package io.github.tinmachine.eidolon

/** nativeの群だけで配送可否を決める。グローバル選択から別targetを補完しない。 */
internal class WindowGroupInputGate(val runtime:Long,private val active:Boolean,private val blockedSession:String?,
    members:List<Member>,candidates:List<Window>,private val failed:Boolean=false) {
    data class Window(val session:String,val window:String)
    data class Member(val target:InputWindowKey,val inputAllowed:Boolean)
    private val members=members.associateBy {Window(it.target.session,it.target.window)}
    private val candidates=candidates.toSet()
    init {
        require(runtime>=0 && members.size<=9 && candidates.size<=9)
        require(this.members.size==members.size && this.candidates.size==candidates.size)
        require(members.all {valid(it.target) && it.target.runtime==runtime})
        require(candidates.all {id(it.session) && id(it.window)})
        require(blockedSession==null || id(blockedSession))
        require(active || members.isEmpty())
    }
    fun allows(target:InputWindowKey):Boolean {
        if(failed || runtime<=0 || target.runtime!=runtime || !valid(target) || blockedSession==target.session) return false
        val key=Window(target.session,target.window)
        members[key]?.let {return it.target==target && it.inputAllowed}
        return !active || key !in candidates
    }
    companion object {
        fun unavailable()=WindowGroupInputGate(0,false,null,emptyList(),emptyList(),true)
        fun rejected(runtime:Long)=WindowGroupInputGate(runtime,false,null,emptyList(),emptyList(),true)
        private fun id(value:String)=value.matches(Regex("[1-9][0-9]{0,18}")) && value.toLongOrNull()!=null
        private fun valid(value:InputWindowKey)=value.runtime>0 && listOf(value.session,value.window,value.scene,value.generation).all(::id)
    }
}
