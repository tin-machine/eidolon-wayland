package io.github.tinmachine.eidolon

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class MousepadAnchorTest {
    @Test fun onlyPrivateDurableIdentifiersAreWritten() {
        val uuid = "a".repeat(32)
        val pending = MousepadAnchorReceipt.Pending(uuid)
        val saved = MousepadAnchorReceipt.Saved(uuid)
        val deleting = MousepadAnchorReceipt.Removing(uuid)
        assertEquals(uuid, (MousepadAnchorReceipt.decode(pending.encode()) as MousepadAnchorReceipt.Pending).operation)
        assertEquals(uuid, (MousepadAnchorReceipt.decode(saved.encode()) as MousepadAnchorReceipt.Saved).uuid)
        assertEquals(uuid, (MousepadAnchorReceipt.decode(deleting.encode()) as MousepadAnchorReceipt.Removing).uuid)
        val fields = JSONObject(saved.encode().toString(Charsets.UTF_8)).keys().asSequence().toSet()
        assertEquals(setOf("schema", "state", "placement", "anchor_uuid"), fields)
        assertFalse(saved.toString().contains(uuid))
        assertFalse(MousepadAnchorReceipt.validUuid("0".repeat(32)))
        assertFalse(MousepadAnchorReceipt.validUuid("A".repeat(32)))
    }

    @Test fun poseReflectsRotationAndPositionForTheSdk() {
        val decoded = panelPoseFromXr(JSONArray(listOf(.2, 1.5, -1.8, 0.0, .6, 0.0, .8)))!!
        assertEquals(1.8, decoded.getJSONArray("position_m").getDouble(2), 1e-8)
        assertEquals(-.6, decoded.getJSONArray("rotation_xyzw").getDouble(1), 1e-8)
        assertNull(panelPoseFromXr(JSONArray(listOf(.2, 1.5, -1.8, 0, 0, 0, 0))))
    }

    @Test fun replacementGenerationHasItsOwnPoseAndInputGate() {
        fun key(generation: String) = JSONObject().put("session", "7").put("window", "8")
            .put("scene", "9").put("generation", generation)
        val current = AnchorWindow.from(1, key("2"))
        val older = AnchorWindow.from(1, key("1"))
        val foreign = AnchorWindow.from(2, key("2"))
        val gate = MousepadAnchorGate(required = true, resolved = older)
        assertFalse(gate.allows(current, current))
        assertTrue(gate.allows(foreign, current))
        assertTrue(MousepadAnchorGate(required = true, resolved = current).allows(current, current))
        val pose = panelPoseFromXr(JSONArray(listOf(.2, 1.5, -1.8, 0, 0, 0, 1)))!!
        fun applied(key: JSONObject) = JSONObject().put("target", key).put("pose_command", "11").put("pose", pose)
        assertFalse(anchorPoseApplied(current, "11", pose, applied(key("1")), pose))
        assertFalse(anchorPoseApplied(current, "12", pose, applied(key("2")), pose))
        assertFalse(anchorPoseApplied(current, "11", pose, applied(key("2")), null))
        assertTrue(anchorPoseApplied(current, "11", pose, applied(key("2")), pose))
    }
}
