package io.github.tinmachine.eidolon

import android.content.Context
import android.view.inputmethod.EditorInfo
import com.meta.spatial.runtime.Scene

/** 固定nonceと合成fixtureだけをdebug sourceSetへ残し、通常と同じcontrollerを通る。 */
object ImeTerminalCase {
    fun start(nonce:String,scene:()->Scene,done:()->Unit) = TerminalImeController.startProbe(
        TerminalImeController.Probe(nonce,::verifyClosedEditor,NativeSessionHost::publishCheck,done),scene)
    fun command(name:String,nonce:String)=TerminalImeController.command(name,nonce)
    private fun verifyClosedEditor(context:Context):Boolean=runCatching {
        val sample=ImeEditorView(context)
        val connection=checkNotNull(sample.onCreateInputConnection(EditorInfo()))
        sample.closeEditor()
        check(sample.isPermanentlyClosed() && !sample.onCheckIsTextEditor())
        check(sample.onCreateInputConnection(EditorInfo())==null)
        check(!connection.commitText("closed-editor-fixture",1))
        sample.resetEditor(restartInput=false)
        check(sample.onCreateInputConnection(EditorInfo())==null && !sample.metadata().active)
        true
    }.getOrDefault(false)
}
