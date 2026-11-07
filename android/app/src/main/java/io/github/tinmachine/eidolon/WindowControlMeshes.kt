package io.github.tinmachine.eidolon

import android.graphics.Color
import com.meta.spatial.core.Entity
import com.meta.spatial.runtime.*
import com.meta.spatial.toolkit.AppSystemActivity
import kotlin.math.cos
import kotlin.math.sin

/** Fixed native meshes: no script, VirtualDisplay, Surface or large transparent hit rectangle. */
object WindowControlMeshes {
    const val OUTLINE="mesh://eidolon-control-outline"
    const val FOCUS="mesh://eidolon-control-focus"
    const val CLOSE="mesh://eidolon-control-close"
    const val FOCUS_ICON="mesh://eidolon-control-focus-icon"
    const val CLOSE_ICON="mesh://eidolon-control-close-icon"
    private fun material(color: Int)=SceneMaterial(null,AlphaMode.OPAQUE,SceneMaterial.UNLIT_SHADER).apply {
        setAlbedoColor(Color.valueOf(color));setUnlit(true);setSidedness(MaterialSidedness.DOUBLE_SIDED)
    }
    fun register(activity: AppSystemActivity) {
        for((uri,color) in listOf(OUTLINE to 0xff27354b.toInt(),FOCUS to 0xffb6efd9.toInt(),CLOSE to 0xffffb2d0.toInt()))
            activity.registerMeshCreator(uri) { _:Entity -> SceneMesh.sphere(.5f,material(color)) }
        activity.registerMeshCreator(FOCUS_ICON) { _:Entity -> icon(false) }
        activity.registerMeshCreator(CLOSE_ICON) { _:Entity -> icon(true) }
    }
    private fun icon(close: Boolean): SceneMesh {
        val vertices=mutableListOf<Float>();val indices=mutableListOf<Int>()
        fun quad(points: List<Pair<Float,Float>>) {
            val start=vertices.size/3
            for((x,y) in points) vertices.addAll(listOf(x,y,0f))
            indices.addAll(listOf(start,start+1,start+2,start,start+2,start+3))
        }
        if(close) {
            for(sign in listOf(1f,-1f)) {
                val x=.19f;val w=.035f
                quad(listOf((-x-sign*w) to (-sign*x+w),(-x+sign*w) to (-sign*x-w),(x+sign*w) to (sign*x-w),(x-sign*w) to (sign*x+w)))
            }
        } else {
            for(i in 0 until 24) {
                val a=i*Math.PI*2/24;val b=(i+1)*Math.PI*2/24
                quad(listOf((cos(a)*.13).toFloat() to (sin(a)*.13).toFloat(),(cos(b)*.13).toFloat() to (sin(b)*.13).toFloat(),
                    (cos(b)*.18).toFloat() to (sin(b)*.18).toFloat(),(cos(a)*.18).toFloat() to (sin(a)*.18).toFloat()))
            }
            for(sign in listOf(1f,-1f)) {
                quad(listOf(.20f*sign to -.025f,.30f*sign to -.025f,.30f*sign to .025f,.20f*sign to .025f))
                quad(listOf(-.025f to .20f*sign,.025f to .20f*sign,.025f to .30f*sign,-.025f to .30f*sign))
            }
        }
        val count=vertices.size/3
        return SceneMesh.meshWithMaterials(vertices.toFloatArray(),FloatArray(count*3){if(it%3==2) -1f else 0f},
            FloatArray(count*2),IntArray(count){-1},indices.toIntArray(),intArrayOf(0,indices.size),arrayOf(material(0xff27354b.toInt())),true)
    }
}
