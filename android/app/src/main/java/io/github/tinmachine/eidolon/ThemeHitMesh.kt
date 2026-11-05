package io.github.tinmachine.eidolon

import com.meta.spatial.runtime.*
import com.meta.spatial.toolkit.MeshCollision

/** 描画もdepth書込みも行わないcontent専用の薄いbox。SDKの標準形状で交差とUVを保つ。 */
object ThemeHitMesh {
    // SDK MeshManagerは登録済みcreatorにもmesh schemeを要求する。
    const val URI = "mesh://eidolon-theme-content-hit"
    // 非描画meshを入力面にする。非表示windowではadapterがentity自体を退役させる。
    val COLLISION = MeshCollision.LineTest_IgnoreVisible
    fun create(): SceneMesh {
        val material = SceneMaterial.depthOnly(false).apply {
            setColorWrite(0); setDepthWrite(DepthWrite.DISABLE); setDepthTest(DepthTest.ALWAYS)
            setSidedness(MaterialSidedness.DOUBLE_SIDED)
        }
        // 厚み0の自作BVHでは実rayが交差しなかった。標準boxの手前面はz=0、UVは左上原点。
        return SceneMesh.box(-.6f,-.45f,0f,.6f,.45f,.001f,material)
    }
}
