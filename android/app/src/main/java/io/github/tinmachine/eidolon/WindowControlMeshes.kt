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
    const val MOVE="mesh://eidolon-control-move"
    const val MENU="mesh://eidolon-control-menu"
    const val PIN_ACTIVE="mesh://eidolon-control-pin-active"
    const val MENU_ICON="mesh://eidolon-control-menu-icon"
    const val SIZE_UP_ICON="mesh://eidolon-control-size-up-icon"
    const val SIZE_DOWN_ICON="mesh://eidolon-control-size-down-icon"
    const val MENU_BACK_ICON="mesh://eidolon-control-menu-back-icon"
    const val MOVE_ICON="mesh://eidolon-control-move-icon"
    const val FOCUS_ICON="mesh://eidolon-control-focus-icon"
    const val PIN_ICON="mesh://eidolon-control-pin-icon"
    const val CLOSE_ICON="mesh://eidolon-control-close-icon"
    const val MOUSE_CURSOR = "mesh://eidolon-mouse-cursor"
    private fun material(color: Int)=SceneMaterial(SceneTexture(Color.valueOf(Color.WHITE)),AlphaMode.OPAQUE,SceneMaterial.UNLIT_SHADER).apply {
        setAlbedoColor(Color.valueOf(color));setUnlit(true);setSidedness(MaterialSidedness.DOUBLE_SIDED)
    }
    fun register(activity: AppSystemActivity) {
        for((uri,color) in listOf(OUTLINE to 0xff27354b.toInt(),FOCUS to 0xffb6efd9.toInt(),CLOSE to 0xffffb2d0.toInt(),MOVE to 0xffffdf9e.toInt(),MENU to 0xffd2c5f6.toInt(),PIN_ACTIVE to 0xff76dfa0.toInt()))
            activity.registerMeshCreator(uri) { _:Entity -> SceneMesh.sphere(.5f,material(color)) }
        activity.registerMeshCreator(MOVE_ICON) { _:Entity -> icon(false,true) }
        activity.registerMeshCreator(FOCUS_ICON) { _:Entity -> icon(false) }
        activity.registerMeshCreator(CLOSE_ICON) { _:Entity -> icon(true) }
        activity.registerMeshCreator(MOUSE_CURSOR) { _: Entity -> mouseCursor() }
        for((uri,kind) in listOf(MENU_ICON to "menu",SIZE_UP_ICON to "larger",SIZE_DOWN_ICON to "smaller",MENU_BACK_ICON to "back",PIN_ICON to "pin"))
            activity.registerMeshCreator(uri) { _:Entity -> smallIcon(kind) }
    }
    private fun mouseCursor(): SceneMesh {
        // The tip is the local origin. A dark outline keeps it visible on white editor pages.
        val vertices = floatArrayOf(
            0f, 0f, 0f, .042f, -.032f, 0f, .012f, -.051f, 0f,
            .005f, -.010f, -.001f, .034f, -.032f, -.001f, .015f, -.044f, -.001f,
        )
        return SceneMesh.meshWithMaterials(
            vertices, FloatArray(18) { if (it % 3 == 2) -1f else 0f }, FloatArray(12), IntArray(6) { -1 },
            intArrayOf(0, 1, 2, 3, 4, 5), intArrayOf(0, 3, 3, 3),
            arrayOf(material(Color.BLACK), material(Color.WHITE)), true,
        )
    }
    private fun smallIcon(kind:String):SceneMesh {
        val vertices=mutableListOf<Float>();val indices=mutableListOf<Int>()
        fun rect(x:Float,y:Float,w:Float,h:Float) {val n=vertices.size/3;vertices.addAll(listOf(x-w/2,y-h/2,0f,x+w/2,y-h/2,0f,x+w/2,y+h/2,0f,x-w/2,y+h/2,0f));indices.addAll(listOf(n,n+1,n+2,n,n+2,n+3))}
        when(kind) {
            "menu" -> for(x in listOf(-.21f,0f,.21f)) rect(x,0f,.09f,.09f)
            "pin" -> {
                rect(0f,.22f,.34f,.07f)
                rect(0f,.085f,.19f,.21f)
                rect(0f,-.055f,.40f,.075f)
                rect(0f,-.19f,.055f,.21f)
            }
            "back" -> {rect(.03f,0f,.46f,.065f);rect(-.18f,.10f,.065f,.22f);rect(-.18f,-.10f,.065f,.22f)}
            else -> {rect(0f,0f,.48f,.07f);if(kind=="larger") rect(0f,0f,.07f,.48f)}
        }
        val count=vertices.size/3
        return SceneMesh.meshWithMaterials(vertices.toFloatArray(),FloatArray(count*3){if(it%3==2) -1f else 0f},FloatArray(count*2),IntArray(count){-1},
            indices.toIntArray(),intArrayOf(0,indices.size),arrayOf(material(0xff27354b.toInt())),true)
    }
    private fun icon(close: Boolean,move:Boolean=false): SceneMesh {
        val vertices=mutableListOf<Float>();val indices=mutableListOf<Int>()
        fun quad(points: List<Pair<Float,Float>>) {
            val start=vertices.size/3
            for((x,y) in points) vertices.addAll(listOf(x,y,0f))
            indices.addAll(listOf(start,start+1,start+2,start,start+2,start+3))
        }
        if(move) {
            quad(listOf(-.035f to -.29f,.035f to -.29f,.035f to .29f,-.035f to .29f))
            quad(listOf(-.29f to -.035f,.29f to -.035f,.29f to .035f,-.29f to .035f))
            for(sign in listOf(1f,-1f)) {
                quad(listOf(-.11f to .18f*sign,0f to .32f*sign,.11f to .18f*sign,0f to .21f*sign))
                quad(listOf(.18f*sign to -.11f,.32f*sign to 0f,.18f*sign to .11f,.21f*sign to 0f))
            }
        } else if(close) {
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
