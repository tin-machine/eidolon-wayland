"""SDK surfaceの部分検証を、SDK alpha/HMDの合格へ昇格させない。"""
import re


def theme_surface_result(snapshot, nonce, expected):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "theme-surface" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("theme surface subprobe failed: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    if (any(result.get(k) is not False for k in ("sdk_alpha_verified", "hmd_verified", "physical_input_verified", "synthetic_visibility"))
            or result.get("cleanup_failed")
            or result.get("synthetic_raycast") is not True or result.get("probe_entities_remaining") != 0
            or type(result.get("ui_thread")) is not int or result["ui_thread"] < 1):
        raise ValueError("theme probe scope/cleanup invalid")
    ready, after = result.get("ready", {}), result.get("after", {})
    current = snapshot.get("runtime", {}).get("runtime", {})
    for state in (ready, after, current):
        if (state.get("state") != "Ready" or state.get("activity_visible") is not True
                or state.get("xr_state") not in {"VISIBLE", "FOCUSED"}
                or not re.fullmatch(r"[1-9][0-9]*", str(state.get("scene", "")))
                or state.get("scene") != ready.get("scene")):
            raise ValueError("theme probe actual visibility/scene invalid")
    rows = result.get("cycles", [])
    if len(rows) != 2:
        raise ValueError("theme probe needs mesh and layer")
    for row, mode in zip(rows, ("mesh", "layer")):
        front, back = row.get("front"), row.get("back")
        if (row.get("mode") != mode or not all(isinstance(x,str) and re.fullmatch(r"[1-9][0-9]*",x) for x in (front,back))
                or front == back or row.get("alpha_mode") != "PREMULTIPLIED" or row.get("scene_texture") is not True
                or row.get("default_center_hit") != front or row.get("default_margin_hit") not in (front,back)
                or row.get("margin_blocks_back") is not (row.get("default_margin_hit") == front)
                or row.get("no_collision_center_hit") != back or row.get("no_collision_margin_hit") != back
                or row.get("removed_from_raycast") is not True):
            raise ValueError("theme probe SDK config/raycast invalid")
        producer = row.get("producer", {})
        if (producer.get("result") != "pass" or producer.get("owned_egl_released") is not True
                or type(producer.get("thread")) is not int or producer["thread"] < 1 or producer["thread"] == result["ui_thread"]
                or not producer.get("renderer") or len(producer.get("surfaces", [])) != 2):
            raise ValueError("theme probe producer owner/release invalid")
        for index, surface in enumerate(producer["surfaces"]):
            samples = [[0,0,255,255]]*6 if index == 0 else [[0,0,0,0],[128,0,0,128]]*3
            if (surface.get("index") != index or surface.get("front") is not bool(index)
                    or len(surface.get("pixels", [])) != len(samples)):
                raise ValueError("theme probe producer samples missing")
            for rgba, expected_rgba in zip(surface["pixels"], samples):
                if (not isinstance(rgba, list) or len(rgba) != 4
                        or any(type(v) is not int or not 0 <= v <= 255 or abs(v-w)>1 for v,w in zip(rgba,expected_rgba))):
                    raise ValueError("theme probe producer RGBA mismatch")
    return True
