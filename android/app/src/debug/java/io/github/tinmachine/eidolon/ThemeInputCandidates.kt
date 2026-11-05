package io.github.tinmachine.eidolon

import com.meta.spatial.core.Entity
import com.meta.spatial.runtime.*
import com.meta.spatial.toolkit.AppSystemActivity
import org.json.JSONArray
import org.json.JSONObject

/** 製品meshが実交差しない場合だけ、同じfront planeの形状を比較する。 */
internal object ThemeInputCandidates {
    val names = listOf("flat_always", "thick", "box")
    val metadata = mutableMapOf<String, JSONObject>()
    fun uri(name: String) = "mesh://eidolon-probe-$name"
    fun register(activity: AppSystemActivity) {
        for (name in names) activity.registerMeshCreator(uri(name)) { _: Entity ->
            val material=SceneMaterial.depthOnly(false).apply {
                setColorWrite(0);setDepthWrite(DepthWrite.DISABLE);setDepthTest(DepthTest.ALWAYS)
                setSidedness(MaterialSidedness.DOUBLE_SIDED)
            }
            val mesh=if(name=="box") SceneMesh.box(-.6f,-.45f,0f,.6f,.45f,.001f,material) else {
                val front=floatArrayOf(-.6f,.45f,0f, .6f,.45f,0f, .6f,-.45f,0f, -.6f,-.45f,0f)
                val normal=floatArrayOf(0f,0f,-1f, 0f,0f,-1f, 0f,0f,-1f, 0f,0f,-1f)
                val uv=floatArrayOf(0f,0f,1f,0f,1f,1f,0f,1f)
                val thick=name=="thick"
                val vertices=if(thick) front+front.mapIndexed { i,v -> if(i%3==2) .001f else v }.toFloatArray() else front
                val indices=if(thick) intArrayOf(0,1,2,0,2,3,4,6,5,4,7,6) else intArrayOf(0,1,2,0,2,3)
                SceneMesh.meshWithMaterials(vertices,if(thick) normal+normal else normal,if(thick) uv+uv else uv,
                    IntArray(vertices.size/3){-1},indices,intArrayOf(0,indices.size),arrayOf(material),true)
            }
            val bounds=mesh.computeCombinedBounds()
            metadata[name]=JSONObject().put("min",JSONArray(listOf(bounds.min.x,bounds.min.y,bounds.min.z)))
                .put("max",JSONArray(listOf(bounds.max.x,bounds.max.y,bounds.max.z)))
            mesh
        }
    }
}
