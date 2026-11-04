"""端末snapshotの合否判定。nonce・版・終了条件をすべて満たすまで成功にしない。"""
import re


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
