package io.github.tinmachine.eidolon

import com.meta.spatial.runtime.*

/** 描画もdepth書込みも行わないcontent専用quad。SDKのray判定は明示的なBVHを使う。 */
object ThemeHitMesh {
    const val URI = "eidolon://theme-content-hit"
    fun create(): SceneMesh {
        val material = SceneMaterial.depthOnly(false).apply {
            setColorWrite(0); setDepthWrite(DepthWrite.DISABLE); setDepthTest(DepthTest.NEVER)
            setSidedness(MaterialSidedness.DOUBLE_SIDED)
        }
        return SceneMesh.meshWithMaterials(
            floatArrayOf(-.6f,.45f,0f, .6f,.45f,0f, .6f,-.45f,0f, -.6f,-.45f,0f),
            floatArrayOf(0f,0f,-1f, 0f,0f,-1f, 0f,0f,-1f, 0f,0f,-1f),
            floatArrayOf(0f,0f, 1f,0f, 1f,1f, 0f,1f),
            intArrayOf(-1,-1,-1,-1), intArrayOf(0,1,2,0,2,3), intArrayOf(0,6), arrayOf(material), true)
    }
}
