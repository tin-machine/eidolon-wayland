package io.github.tinmachine.eidolon

import com.meta.spatial.core.Pose
import com.meta.spatial.core.Quaternion
import com.meta.spatial.core.Vector3
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/** JSONはxyzw、SDK Quaternionの4引数はwxyz。SDK objectをHostへ保管しない。 */
object PanelPoseCodec {
    fun valid(value: JSONObject): Boolean {
        val p=value.optJSONArray("position_m") ?: return false
        val q=value.optJSONArray("rotation_xyzw") ?: return false
        if(p.length()!=3 || q.length()!=4) return false
        val position=(0..2).map {p.optDouble(it,Double.NaN)}
        val rotation=(0..3).map {q.optDouble(it,Double.NaN)}
        return position.all {it.isFinite()} && rotation.all {it.isFinite()} && position[0] in -10.0..10.0 && position[1] in 0.0..4.0 && position[2] in -10.0..10.0 && abs(rotation.sumOf {it*it}-1.0)<=.001
    }
    fun toSdk(value: JSONObject): Pose {
        check(valid(value))
        val p=value.getJSONArray("position_m");val q=value.getJSONArray("rotation_xyzw")
        return Pose(Vector3(p.getDouble(0).toFloat(),p.getDouble(1).toFloat(),p.getDouble(2).toFloat()),Quaternion(q.getDouble(3).toFloat(),q.getDouble(0).toFloat(),q.getDouble(1).toFloat(),q.getDouble(2).toFloat()))
    }
    fun fromSdk(value: Pose): JSONObject? {
        val p=listOf(value.t.x.toDouble(),value.t.y.toDouble(),value.t.z.toDouble())
        val q=listOf(value.q.x.toDouble(),value.q.y.toDouble(),value.q.z.toDouble(),value.q.w.toDouble())
        val norm=sqrt(q.sumOf {it*it})
        if(p.any {!it.isFinite()} || !norm.isFinite() || norm<1e-6) return null
        return JSONObject().put("position_m",JSONArray(p)).put("rotation_xyzw",JSONArray(q.map {it/norm})).takeIf {valid(it)}
    }
    fun near(a: JSONObject,b: JSONObject,epsilon: Double=.0001): Boolean {
        if(!valid(a) || !valid(b)) return false
        val p=a.getJSONArray("position_m");val other=b.getJSONArray("position_m")
        if((0..2).any {abs(p.getDouble(it)-other.getDouble(it))>epsilon}) return false
        val q=a.getJSONArray("rotation_xyzw");val r=b.getJSONArray("rotation_xyzw")
        return listOf(1.0,-1.0).any {sign -> (0..3).all {abs(q.getDouble(it)-sign*r.getDouble(it))<=epsilon} }
    }
    fun nudge(value: JSONObject,x: Double,y: Double,z: Double,yaw: Double): JSONObject {
        val p=value.getJSONArray("position_m");val q=value.getJSONArray("rotation_xyzw")
        val c=cos(Math.toRadians(yaw)/2);val s=sin(Math.toRadians(yaw)/2)
        val rotation=listOf(c*q.getDouble(0)+s*q.getDouble(2),c*q.getDouble(1)+s*q.getDouble(3),c*q.getDouble(2)-s*q.getDouble(0),c*q.getDouble(3)-s*q.getDouble(1))
        return JSONObject().put("position_m",JSONArray(listOf(p.getDouble(0)+x,p.getDouble(1)+y,p.getDouble(2)+z))).put("rotation_xyzw",JSONArray(rotation))
    }
}
