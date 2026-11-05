"""SDK surfaceの部分検証を、SDK alpha/HMDの合格へ昇格させない。"""
import re


def theme_runtime_result(snapshot, nonce, expected, uid):
    from quest_checks import instrumentation_result
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "theme-runtime" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("theme runtime subprobe failed")
    if result.get("result") != "pass":
        return False
    if (any(result.get(k) is not False for k in ("sdk_verified", "hmd_verified", "physical_input_verified"))
            or result.get("private_fixture_removed") is not True or result.get("cleanup_failed")):
        raise ValueError("theme runtime scope/cleanup invalid")
    rows = result.get("cycles", [])
    names = ["builtin", "imported", "invalid_preserved", "reduced", "off", "restored_off", "restored_import"]
    if [row.get("name") for row in rows] != names:
        raise ValueError("theme runtime cycles missing")
    for row, selection, theme_id in zip(rows,
            ("builtin", "imported", "imported", "imported", "off", "off", "imported"),
            ("cyan-orbit", "probe-theme", "probe-theme", "probe-theme", None, None, "probe-theme")):
        state = row.get("runtime", {}); theme = state.get("theme", {}); render = state.get("render", {})
        if (state.get("state") != "Ready" or theme.get("loading") is not False or theme.get("selection") != selection
                or render.get("theme_id") != theme_id or theme.get("workers") != 2
                or theme.get("storage", {}).get("pending") is not False
                or theme.get("storage", {}).get("persistent") is not True
                or theme.get("storage", {}).get("error") is not None):
            raise ValueError("theme runtime application/storage invalid")
        if row["name"] != "invalid_preserved" and theme.get("error") is not None:
            raise ValueError("theme runtime unexpected error")
        if theme_id is not None and (render.get("contexts") != 1 or render.get("composition", {}).get("decoration_texture_bytes", 0) <= 0):
            raise ValueError("theme runtime missing actual GPU upload")
    if (not rows[2]["runtime"]["theme"].get("error")
            or rows[2]["runtime"]["theme"]["applied_revision"] != rows[1]["runtime"]["theme"]["applied_revision"]
            or any(rows[i]["runtime"]["theme"].get("reduced_motion") is not True for i in (3,4,5))
            or rows[5]["runtime"]["theme"].get("imported_available") is not True
            or rows[5]["runtime"]["theme"]["storage"].get("loaded") is not True):
        raise ValueError("theme runtime failure/recovery invalid")
    stopped = result.get("stopped", {})
    if (stopped.get("state") != "Stopped" or stopped.get("theme", {}).get("workers") != 0
            or stopped.get("render", {}).get("workers") != 0 or stopped["render"].get("contexts") != 0):
        raise ValueError("theme runtime stop incomplete")
    return instrumentation_result(snapshot, result, uid)


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
        content = row.get("content_input")
        uv = row.get("content_uv")
        if (row.get("mode") != mode or not all(isinstance(x,str) and re.fullmatch(r"[1-9][0-9]*",x) for x in (front,back))
                or front == back or row.get("alpha_mode") != "PREMULTIPLIED" or row.get("scene_texture") is not True
                or row.get("default_center_hit") != front or row.get("default_margin_hit") not in (front,back)
                or row.get("margin_blocks_back") is not (row.get("default_margin_hit") == front)
                or row.get("no_collision_center_hit") != back or row.get("no_collision_margin_hit") != back
                or row.get("removed_from_raycast") is not True):
            raise ValueError("theme probe SDK config/raycast invalid")
        if (not isinstance(content, str) or not re.fullmatch(r"[1-9][0-9]*", content)
                or content in (front, back) or row.get("content_margin_hit") != back
                or not isinstance(uv, list) or len(uv) != 2
                or any(type(v) not in (int, float) or not abs(v-w) < .005 for v,w in zip(uv,(.2,.35)))):
            raise ValueError("theme content-only input/margin/UV invalid")
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
