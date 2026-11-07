package io.github.tinmachine.eidolon

/** 回収だけを同nonceで最大3回明示再試行可能。所有記録が別のprobeへ流用される入口にはしない。 */
internal class LinuxGtkRecoveryBudget {
    private val attempts=linkedMapOf<String,Int>()
    fun claim(case:String,nonce:String):Boolean {
        if(case !in setOf("linux-gtk-input-recover","linux-gtk-dialog-recover") || !nonce.matches(Regex("[0-9a-f]{32}"))) return false
        val key="$case:$nonce";val old=attempts[key]
        if(old==null && attempts.size==64 || old!=null && old>=3) return false
        attempts[key]=(old ?: 0)+1;return true
    }
}
