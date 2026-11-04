"""端末snapshotの合否判定。nonce・版・終了条件をすべて満たすまで成功にしない。"""
import re
import json


def runtime_result(snapshot, nonce, expected):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "runtime" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("端末runtime caseが失敗しました")
    if result.get("result") != "pass":
        return False
    cycles = result.get("cycles", [])
    if len(cycles) != 10 or not result.get("panic_converted") or result.get("cleanup_failed"):
        raise ValueError("runtime caseの回数・panic変換・cleanupが不正です")
    previous = 0
    for index, cycle in enumerate(cycles, 1):
        handle = cycle.get("handle", "")
        if (cycle.get("cycle") != index or not isinstance(handle, str) or not re.fullmatch(r"[1-9][0-9]*", handle)
                or not previous < int(handle) <= 2**63 - 1 or cycle.get("owned_workers") != 0):
            raise ValueError("runtimeの世代・所有thread回収結果が不正です")
        previous = int(handle)
    # 試験後に新しい製品runtimeへ戻った結果も待つ。
    current = snapshot.get("runtime", {})
    handle = current.get("handle", "")
    return (current.get("code") == 0 and isinstance(handle, str) and handle.isdecimal()
            and previous < int(handle) <= 2**63 - 1
            and current.get("runtime", {}).get("state") == "Ready"
            and current.get("runtime", {}).get("activity_visible") is True
            and current.get("runtime", {}).get("xr_state") in {"VISIBLE", "FOCUSED"}
            and current.get("registry") == {"runtimes": 1, "workers": 1})


def helper_result(snapshot, nonce, expected):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "helper" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("helper caseが失敗しました")
    if result.get("result") != "pass":
        return False
    helper = result.get("helper", {})
    if (helper.get("error") is not None or helper.get("cancelled") or helper.get("cycles") != 10
            or helper.get("owned_children") != 0 or not helper.get("directory_removed")
            or helper.get("exit_code") != 0 or not result.get("stop_cleanup") or result.get("cleanup_failed")):
        raise ValueError("helper echo/exit/cleanup結果が不正です")
    if not isinstance(helper.get("uid"), int) or not helper.get("context"):
        raise ValueError("helperのUID/contextがありません")
    current = snapshot.get("runtime", {})
    state = current.get("runtime", {})
    return (current.get("code") == 0 and int(current.get("handle", "0")) > int(result.get("stopped_handle", "0")) > 0
            and current.get("registry") == {"runtimes": 1, "workers": 1}
            and state.get("state") == "Ready" and state.get("activity_visible") is True
            and state.get("xr_state") in {"VISIBLE", "FOCUSED"} and state.get("helper_workers") == 0)


def surface_result(snapshot, nonce, expected, case):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != case or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("Surface caseが失敗しました: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    count = {"surface": 10, "pattern": 1, "surface-cycle": 20}[case]
    cycles = result.get("cycles", [])
    if len(cycles) != count or not all(result.get(field) is True for field in
            ("paused_frames_stable", "stale_detach_safe", "duplicate_safe")):
        raise ValueError("Surface回数/停止/世代検査が不正です")
    previous = 0
    for target in cycles:
        key = target.get("key", {})
        generation = key.get("generation", "")
        if (not isinstance(generation, str) or not generation.isdecimal() or not previous < int(generation) <= 2**63 - 1
                or target.get("width") != 1024 or target.get("height") != 768 or target.get("frames", 0) < 3
                or target.get("readback") is not True or not target.get("renderer") or not target.get("vendor") or not target.get("version")):
            raise ValueError("Surface寸法/世代/frame/GL色検査が不正です")
        previous = int(generation)
    adapter = result.get("adapter", {})
    if (adapter.get("created", 0) - adapter.get("destroyed", 0) != 1 or adapter.get("bindings") != 1
            or adapter.get("listeners") != 1 or adapter.get("pending") != 0):
        raise ValueError("SDK Entity/listener回収数が不正です")
    state = snapshot.get("runtime", {}).get("runtime", {})
    render = state.get("render", {})
    targets = render.get("targets", [])
    return (state.get("state") == "Ready" and state.get("activity_visible") is True
            and state.get("xr_state") in {"VISIBLE", "FOCUSED"} and render.get("workers") == 1
            and not render.get("paused") and render.get("error") is None and len(targets) == 1
            and targets[0].get("key") == cycles[-1].get("key") and targets[0].get("frames", 0) > cycles[-1].get("frames", 0))


def credentials_result(snapshot, nonce, expected):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "credentials" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("credential case失敗: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    required = ("idempotent", "public_only", "file_mode_0600", "aes_nonexportable", "known_pin", "unknown_pin_rejected",
                "mismatch_pin_rejected", "unsolicited_credential_rejected", "tamper_rejected", "key_loss_rejected",
                "no_silent_regeneration", "production_unchanged", "save_loss_rejected")
    if not all(result.get(key) is True for key in required) or result.get("cleanup_failed"):
        raise ValueError("credential保護・pin拒否・回収結果が不正です")
    if "BEGIN OPENSSH PRIVATE KEY" in json.dumps(snapshot):
        raise ValueError("診断へ秘密鍵が混入しています")
    state = snapshot.get("runtime", {}).get("runtime", {})
    return (state.get("state") == "Ready" and state.get("activity_visible") is True
            and state.get("xr_state") in {"VISIBLE", "FOCUSED"} and state.get("render", {}).get("workers") == 1)
