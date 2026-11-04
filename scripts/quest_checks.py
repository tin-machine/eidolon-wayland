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
            and state.get("xr_state") in {"VISIBLE", "FOCUSED"} and state.get("render", {}).get("workers") == 1
            and int(snapshot.get("runtime", {}).get("handle", "0")) > int(result.get("stopped_handle", "0")) > 0)


def ssh_result(snapshot, nonce, expected, termux_uid):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "ssh" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("SSH case失敗: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    modes = {row.get("mode"): row for row in result.get("cases", [])}
    if (set(modes) != {"normal", "mismatch", "auth_failed", "cancel_credential"} or len(result.get("cases", [])) != 4
            or not result.get("profile_unchanged") or result.get("cleanup_failed")):
        raise ValueError("SSH scenario/profile回収が不正です")
    previous = 0
    for case in result["cases"]:
        handle = case.get("handle", "")
        if not isinstance(handle, str) or not re.fullmatch(r"[1-9][0-9]*", handle) or not previous < int(handle) <= 2**63 - 1:
            raise ValueError("SSH runtime世代が不正です")
        previous = int(handle)
    if str(previous) != result.get("last_handle"):
        raise ValueError("SSH最後のruntimeが不正です")
    for name, case in modes.items():
        after = case.get("after_stop", {})
        if (case.get("result") != "pass" or after.get("workers") != 0 or after.get("tokio_threads") != 0
                or after.get("owned_connections") != 0):
            raise ValueError("SSH worker/connection回収が不正です")
        requests = 0 if name == "mismatch" else 1
        provided = 0 if name in {"mismatch", "cancel_credential"} else 1
        if case.get("credential_requests") != requests or case.get("credentials_provided") != provided:
            raise ValueError("host pin前/取消後にcredentialが提供されています")
    normal = modes["normal"]
    probe = normal.get("transport", {}).get("probe", {})
    if (probe.get("uid") != termux_uid or not probe.get("token_matched") or not probe.get("eof")
            or probe.get("exit_status") != 0 or probe.get("exit_signal") or not normal.get("stale_disconnect_safe")):
        raise ValueError("APK→TermuxのUID/token/EOF/exit/世代検査が不正です")
    for name, error in [("mismatch", "HOST_KEY_MISMATCH"), ("auth_failed", "AUTH_FAILED")]:
        if modes[name].get("transport", {}).get("error") != error:
            raise ValueError("pinと認証失敗を区別できていません")
    state = snapshot.get("runtime", {}).get("runtime", {})
    return (state.get("state") == "Ready" and state.get("activity_visible") is True
            and state.get("xr_state") in {"VISIBLE", "FOCUSED"} and state.get("transport", {}).get("state") == "Idle"
            and int(snapshot.get("runtime", {}).get("handle", "0")) > int(result.get("last_handle", "0")) > 0)


def instrumentation_result(snapshot, result, apk_uid):
    """描画を伴わない再生成・停止・全runtime回収の証拠。UI復帰の代用にはしない。"""
    proof = result.get("instrumentation", {})
    if proof.get("result") == "fail":
        raise ValueError("instrumentationのruntime再生成・回収が失敗しました")
    if proof.get("result") != "pass":
        return False
    ready, stopped, destroyed = (proof.get(name, {}) for name in ("ready", "stopped", "after_destroy"))
    handle = proof.get("recovery_handle", "")
    state, after = ready.get("runtime", {}), stopped.get("runtime", {})
    transport = state.get("transport", {})
    if (proof.get("mode") != "instrumentation" or proof.get("case") != result.get("case") or proof.get("nonce") != result.get("nonce")
            or proof.get("cleanup_failed") or type(apk_uid) is not int or proof.get("uid") != apk_uid
            or type(proof.get("pid")) is not int or proof["pid"] <= 0 or snapshot.get("process_id") != proof["pid"]
            or not isinstance(handle, str) or not re.fullmatch(r"[1-9][0-9]*", handle)
            or not 0 < int(result.get("last_handle", "0")) < int(handle) <= 2**63 - 1
            or ready.get("code") != 0 or ready.get("handle") != handle or ready.get("registry") != {"runtimes": 1, "workers": 1}
            or state.get("state") != "Ready" or state.get("error") is not None or state.get("activity_visible") is not False or state.get("xr_state") != "IDLE"
            or transport.get("state") != "Idle" or transport.get("journal_ready") is not True or transport.get("pending_cleanup") != []
            or stopped.get("code") != 0 or stopped.get("handle") != handle or after.get("state") != "Stopped"
            or after.get("helper_workers") != 0 or after.get("dependency_workers", 0) != 0
            or after.get("render", {}).get("workers") != 0
            or any(after.get("transport", {}).get(field) != 0 for field in ("workers", "tokio_threads", "owned_connections"))
            or after.get("transport", {}).get("forward", {}).get("active") != 0
            or destroyed.get("code") != 1 or destroyed.get("registry") != {"runtimes": 0, "workers": 0}
            or snapshot.get("runtime") != destroyed
            or type(proof.get("accept_ms")) is not int or not 0 <= proof["accept_ms"] < 100
            or type(proof.get("stop_ms")) is not int or not 0 <= proof["stop_ms"] < 10000):
        raise ValueError("instrumentationのUID/PID/新handle/所有資源/期限が不正です")
    return True


def forward_result(snapshot, nonce, expected, termux_uid, *, instrumentation=False, apk_uid=None):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "forward" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("StreamLocal case失敗: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    report = result.get("transport", {}).get("forward_probe", {})
    forwarding = report.get("forwarding", {})
    total = 1 + 65536 + 4 * 8 * 1024 * 1024
    after = result.get("after_stop", {})
    if (result.get("cleanup_failed") or result.get("credential_requests") != 1
            or report.get("state") != "pass" or report.get("nonce") != nonce[:16] or report.get("uid") != termux_uid
            or report.get("channels") != 6 or report.get("bytes") != total or report.get("error") is not None
            or not all(report.get(key) for key in ["hashes_matched", "half_close", "forward_cancelled", "local_cleaned", "remote_cleaned"])
            or forwarding.get("accepted") != 6 or forwarding.get("active") != 0 or not 4 <= forwarding.get("peak", 0) <= 8
            or forwarding.get("failed") != 0 or forwarding.get("remote_to_local") != total or forwarding.get("local_to_remote") != total + 54
            or forwarding.get("copy_buffer_limit") != 512 * 1024
            or any(after.get(key) != 0 for key in ["workers", "tokio_threads", "owned_connections"])
            or after.get("forward", {}).get("active") != 0):
        raise ValueError("転送のbyte/hash/EOF/上限/回収検査が不正です")
    remote = report.get("remote", {})
    finished = remote.get("finished", {})
    child = remote.get("child", {})
    if (remote.get("phase") != "finished" or remote.get("cleanup_pending") is not False
            or remote.get("eof") is not True or remote.get("exit_status") != 0 or remote.get("exit_signal") is not False
            or remote.get("error") is not None or finished.get("schema") != 1 or finished.get("nonce") != nonce[:16]
            or finished.get("uid") != termux_uid or finished.get("reason") != "Completed" or finished.get("exit_status") != 0
            or finished.get("exit_signal") is not None or finished.get("cleaned") is not True
            or finished.get("pipes_closed") is not True or finished.get("group_alive") is not False
            or child != finished.get("child") or not isinstance(child.get("pid"), int) or child["pid"] <= 0
            or child.get("group") != child["pid"] or not re.fullmatch(r"[0-9]{1,32}", child.get("start", ""))):
        raise ValueError("launcherのPID identity/EOF/exit/回収ackが不正です")
    if instrumentation:
        return instrumentation_result(snapshot, result, apk_uid)
    state = snapshot.get("runtime", {}).get("runtime", {})
    return (state.get("state") == "Ready" and state.get("activity_visible") is True
            and state.get("xr_state") in {"VISIBLE", "FOCUSED"} and state.get("transport", {}).get("state") == "Idle"
            and int(snapshot.get("runtime", {}).get("handle", "0")) > int(result.get("last_handle", "0")) > 0)


def remote_cleanup_result(snapshot, nonce, expected, termux_uid, *, instrumentation=False, apk_uid=None):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "remote-cleanup" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("remote回収case失敗: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    rows = result.get("rows", [])
    modes = ["cancel_before_exec", "prepare_eof", "stop", "control_eof", "ssh_drop", "ssh_drop_recreate", "lease_expire"]
    if result.get("cleanup_failed") or [row.get("mode") for row in rows] != modes:
        raise ValueError("回収scenarioが不完全です")
    previous_handle = 0
    previous_session = 0
    used = set()
    for row in rows:
        mode, report, stop = row["mode"], row.get("report", {}), row.get("stop", {})
        first_handle = int(row.get("first_handle", "0"))
        first_session = int(row.get("first_session", "0"))
        remote_nonce = row.get("nonce", "")
        if (row.get("result") != "pass" or first_handle <= previous_handle or first_session <= previous_session
                or not re.fullmatch(r"[0-9a-f]{16}", remote_nonce) or remote_nonce in used
                or report.get("nonce") != remote_nonce or report.get("uid") != termux_uid
                or report.get("scenario") != row.get("scenario")):
            raise ValueError("回収のhandle/session/nonce/UID境界が不正です")
        used.add(remote_nonce)
        after = stop.get("after", {})
        if (any(after.get(key) != 0 for key in ["workers", "tokio_threads", "owned_connections"])
                or after.get("forward", {}).get("active") != 0 or after.get("pending_cleanup") != []
                or not 0 <= stop.get("accept_ms", 100) < 100 or not 0 <= stop.get("stop_ms", 10000) < 10000
                or not report.get("local_cleaned") or report.get("forwarding", {}).get("active") != 0):
            raise ValueError("Stop受付/所有thread・FD・socket・pending回収が不正です")
        previous_handle = int(stop.get("handle", "0"))
        previous_session = first_session
        if previous_handle < first_handle:
            raise ValueError("最後のruntimeが過去へ戻っています")
        if mode == "cancel_before_exec":
            if (not report.get("remote_not_started") or report.get("remote_cleaned") or report.get("remote") is not None
                    or report.get("pending_cleanup") != [] or report.get("state") != "pass"):
                raise ValueError("exec前取消でremoteを起動・回収成功扱いしています")
            continue
        remote = report.get("remote", {})
        child = remote.get("child")
        if mode.startswith("ssh_drop"):
            recovered = row.get("recovered", {})
            audits = recovered.get("cleanup_audits", [])
            if (report.get("remote_cleaned") or remote.get("cleanup_pending") is not True or report.get("error") != "SSH_DROPPED"
                    or len(row.get("after_loss", {}).get("pending_cleanup", [])) != 1
                    or recovered.get("state") != "Ready" or recovered.get("pending_cleanup") != [] or len(audits) != 1):
                raise ValueError("SSH断の未確認状態または再接続auditが不正です")
            audit = audits[0]
            next_session = int(row.get("next_session", "0"))
            if (next_session <= first_session or audit.get("nonce") != remote_nonce or audit.get("verified") is not True
                    or audit.get("origin_session") != str(first_session) or audit.get("current_session") != str(next_session)
                    or audit.get("child") != child or any(audit.get(key) for key in ["active", "orphaned", "child_alive", "error"])):
                raise ValueError("新接続のreceiptと旧child identityが一致しません")
            if mode == "ssh_drop_recreate" and (int(row.get("next_handle", "0")) <= first_handle or len(row.get("old_stop", {}).get("after", {}).get("pending_cleanup", [])) != 1):
                raise ValueError("runtime再生成後に未確認記録を保持していません")
            previous_session = next_session
        else:
            finished = remote.get("finished", {})
            reason = {"prepare_eof": "EOF", "stop": "Stop", "control_eof": "EOF", "lease_expire": "LeaseExpired"}[mode]
            if (not report.get("remote_cleaned") or remote.get("phase") != "finished" or remote.get("cleanup_pending")
                    or not remote.get("eof") or remote.get("exit_status") != 0 or remote.get("exit_signal")
                    or finished.get("nonce") != remote_nonce or finished.get("uid") != termux_uid or finished.get("reason") != reason
                    or finished.get("child") != child or finished.get("cleaned") is not True or finished.get("group_alive") is not False
                    or finished.get("pipes_closed") is not True):
                raise ValueError("remote ownerのEOF/exit/回収ackが不正です")
            if mode == "stop" and not row.get("observed_live"):
                raise ValueError("稼働中Stopの根拠がありません")
            if mode == "lease_expire" and report.get("elapsed_ms", 0) < 59000:
                raise ValueError("実60秒leaseを検証していません")
        if mode == "prepare_eof":
            if child is not None:
                raise ValueError("子の開始前EOFでchildが存在します")
        elif (not isinstance(child, dict) or not isinstance(child.get("pid"), int) or child["pid"] <= 0
              or child.get("group") != child["pid"] or not re.fullmatch(r"[0-9]{1,32}", child.get("start", ""))):
            raise ValueError("所有child identityが不正です")
    if str(previous_handle) != result.get("last_handle"):
        raise ValueError("最後のruntime handleが一致しません")
    if instrumentation:
        return instrumentation_result(snapshot, result, apk_uid)
    current = snapshot.get("runtime", {})
    state = current.get("runtime", {})
    return (int(current.get("handle", "0")) > previous_handle > 0 and state.get("state") == "Ready"
            and state.get("activity_visible") is True and state.get("xr_state") in {"VISIBLE", "FOCUSED"}
            and state.get("transport", {}).get("pending_cleanup") == [] and state.get("transport", {}).get("state") == "Idle")


def product_ssh_result(snapshot, nonce, expected, termux_uid):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "ssh-product" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("製品SSH case失敗: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    probe = result.get("transport", {}).get("probe", {})
    if (not result.get("product_host_path") or not result.get("render_frames_advanced")
            or not result.get("stop", {}).get("old_runtime_destroyed") or probe.get("uid") != termux_uid
            or not probe.get("token_matched") or not probe.get("eof") or probe.get("exit_status") != 0 or probe.get("exit_signal")):
        raise ValueError("製品Host/描画/SSH/停止の検査が不正です")
    current = snapshot.get("runtime", {})
    state = current.get("runtime", {})
    return (int(result.get("new_handle", "0")) > int(result.get("old_handle", "0")) > 0
            and current.get("handle") == result.get("new_handle") and state.get("state") == "Ready"
            and state.get("activity_visible") is True and state.get("xr_state") in {"VISIBLE", "FOCUSED"}
            and state.get("transport", {}).get("state") == "Idle"
            and state.get("transport", {}).get("owned_connections") == 0 and len(state.get("render", {}).get("targets", [])) == 1)
