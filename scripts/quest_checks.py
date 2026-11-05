"""端末snapshotの合否判定。nonce・版・終了条件をすべて満たすまで成功にしない。"""
import re
import json
import hashlib


def forwarded_shm_result(snapshot, nonce, expected, apk_uid, termux_uid, package, *, instrumentation=False):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "forwarded-shm" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("端末Waypipe転送case失敗: " + result.get("stage", "unknown"))
    if result.get("result") != "pass":
        return False
    session = result.get("session")
    def positive_id(value):
        return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) and int(value) <= 2**63-1
    if (not positive_id(session) or result.get("credential_requests") != 1 or result.get("stale_rejected") is not True
            or result.get("cleanup_failed") or type(apk_uid) is not int or type(termux_uid) is not int or apk_uid == termux_uid):
        raise ValueError("転送の認証/session/stale拒否が不正です")
    rows = result.get("cycles", [])
    if len(rows) != 2:
        raise ValueError("pattern/footの両方が必要です")
    waypipe_hash = package.get("dependencies", {}).get("waypipe", {}).get("sha256")
    fixture_hash = package.get("remote_fixture", {}).get("sha256")
    if not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in (waypipe_hash, fixture_hash)):
        raise ValueError("実APKのWaypipe/producer hashがありません")
    prior_ids = []
    for index, (row, mode) in enumerate(zip(rows, ("pattern", "foot"))):
        token = hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()[:16]
        app, transport = row.get("app", {}), row.get("transport", {})
        forward = transport.get("app_forward", {})
        local, tools = app.get("local", {}), forward.get("tools", {})
        if (row.get("cycle") != index+1 or row.get("mode") != mode or app.get("session") != session
                or app.get("nonce") != token or app.get("mode") != mode or app.get("error") is not None
                or app.get("pool_rejections") != 0 or app.get("last_rejected_pool_bytes") is not None
                or app.get("callback_clock") != "virtual" or app.get("ready") is not True or app.get("forward_sent") is not True
                or local.get("uid") != apk_uid or local.get("child_uid") != apk_uid or local.get("workers") != 1 or local.get("owned_groups") != 1
                or local.get("ready") is not True or local.get("error") is not None or local.get("version") != "0.11.3"
                or local.get("executable_sha256") != waypipe_hash or not local.get("child_context")
                or transport.get("session") != session or transport.get("state") != "Ready" or transport.get("owned_connections") != 1
                or transport.get("credential_requests") != 1 or transport.get("pending_cleanup") != ([] if mode=="pattern" else forward.get("pending_cleanup"))
                or forward.get("nonce") != token or forward.get("mode") != mode or forward.get("uid") != termux_uid
                or forward.get("error") is not None or forward.get("registered") is not True
                or tools.get("schema") != 1 or tools.get("nonce") != token or tools.get("uid") != termux_uid or tools.get("mode") != mode
                or tools.get("waypipe") != dict(version="0.11.3", features=["lz4", "zstd"], unavailable=["dmabuf", "video", "vaapi"])):
            raise ValueError("同じ認証済みsession/実UID/版/forward順序の証明が不正です")
        ids = app.get("window_ids", [])
        if not ids or any(not positive_id(value) for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("window IDの証明が不正です")
        remote = forward.get("remote", {})
        if mode == "pattern":
            finished = remote.get("finished", {})
            producer = finished.get("pattern", {}) or {}
            protocol = producer.get("protocol", {})
            if (app.get("state") != "pass" or app.get("verified_hashes") != 100 or app.get("copies") != 100
                    or any(app.get(key) != 100 for key in ("releases", "callbacks")) or any(app.get(key) != 1 for key in ("configures", "acks"))
                    or any(app.get(key) != 0 for key in ("clients", "objects", "surfaces", "pixel_bytes", "pool_bytes", "pool_count")) or app.get("windows") != []
                    or forward.get("state") != "pass" or forward.get("remote_cleaned") is not True or forward.get("forward_cancelled") is not True
                    or forward.get("pending_cleanup") != [] or forward.get("forwarding", {}).get("active") != 0
                    or remote.get("phase") != "finished" or remote.get("cleanup_pending") is not False
                    or finished.get("reason") != "Completed" or finished.get("exit_status") != 0 or finished.get("cleaned") is not True
                    or finished.get("group_alive") is not False or finished.get("pipes_closed") is not True
                    or producer.get("schema") != 1 or producer.get("uid") != termux_uid or producer.get("result") != "pass"
                    or producer.get("fixture_revision") != "eidolon-shm-client-v1" or protocol.get("protocol_error") is not False
                    or any(protocol.get(key) != 100 for key in ("frames", "releases", "callbacks"))
                    or any(protocol.get(key) != 1 for key in ("configures", "acks"))
                    or tools.get("fixture") != dict(schema=1,fixture_revision="eidolon-shm-client-v1",frames=100,sha256=fixture_hash)):
                raise ValueError("100frameの実転送/hash/producer/receipt回収が不正です")
            prior_ids = ids
        else:
            windows = app.get("windows", [])
            if (app.get("state") != "running" or type(app.get("copies")) is not int or app["copies"] < 1 or app.get("non_uniform") is not True
                    or len(windows) != 1 or windows[0].get("life") != "Mapped" or not positive_id(windows[0].get("window"))
                    or windows[0]["window"] in prior_ids or int(windows[0]["window"]) <= max(map(int, prior_ids))
                    or not positive_id(windows[0].get("frame")) or forward.get("state") != "running"
                    or tools.get("foot") != dict(version="1.28.0") or remote.get("phase") != "running" or not remote.get("child")):
                raise ValueError("実footのtoplevel/buffer/非一様描画/ID非再利用が不正です")
    after = result.get("after_stop", {})
    app, transport = after.get("app", {}), after.get("transport", {})
    local, forward = app.get("local", {}), transport.get("app_forward", {})
    remote = forward.get("remote", {})
    finished = remote.get("finished", {}) or {}
    if (after.get("state") != "Stopped" or app.get("state") != "stopped" or app.get("error") is not None or app.get("directory_cleaned") is not True
            or app.get("session") != session or app.get("nonce") != rows[1]["app"]["nonce"] or app.get("mode") != "foot"
            or any(type(app.get(key)) is not int or app[key] != 0 for key in ("clients", "objects", "surfaces", "pixel_bytes", "pool_bytes", "pool_count", "memfd_fds_after", "memfd_maps_after"))
            or app.get("windows") != [] or local.get("workers") != 0 or local.get("owned_groups") != 0 or local.get("waited") is not True
            or local.get("error") is not None or forward.get("state") != "stopped" or forward.get("cancelled") is not True
            or forward.get("remote_cleaned") is not True or forward.get("forward_cancelled") is not True or forward.get("error") is not None
            or remote.get("phase") != "finished" or remote.get("cleanup_pending") is not False
            or finished.get("reason") != "Stop" or finished.get("cleaned") is not True or finished.get("group_alive") is not False
            or finished.get("pipes_closed") is not True or finished.get("term_sent") is not True
            or forward.get("pending_cleanup") != [] or transport.get("pending_cleanup") != []
            or any(transport.get(key) != 0 for key in ("workers", "tokio_threads", "owned_connections")) or transport.get("forward", {}).get("active") != 0
            or any(after.get(key) != 0 for key in ("helper_workers", "dependency_workers", "compositor_workers")) or after.get("render", {}).get("workers") != 0
            or type(result.get("accept_ms")) is not int or not 0 <= result["accept_ms"] < 100
            or type(result.get("stop_ms")) is not int or not 0 <= result["stop_ms"] < 10000 or not positive_id(result.get("last_handle"))):
        raise ValueError("foot実行中の停止/全owner・SHM・bridge回収が不正です")
    if instrumentation:
        return instrumentation_result(snapshot, result, apk_uid)
    current = snapshot.get("runtime", {})
    state, handle = current.get("runtime", {}), current.get("handle")
    return (positive_id(handle) and int(handle) > int(result["last_handle"]) and current.get("code") == 0
            and current.get("registry") == dict(runtimes=1,workers=1) and state.get("state") == "Ready"
            and state.get("activity_visible") is True and state.get("xr_state") in {"VISIBLE","FOCUSED"}
            and state.get("transport", {}).get("state") == "Idle")


def wayland_state_result(snapshot, nonce, expected, uid, *, instrumentation=False):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "wayland-state" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("端末Wayland state caseが失敗しました")
    if result.get("result") != "pass":
        return False
    scenarios = [("lifecycle", "Lifecycle"), ("transforms", "Transforms"), ("frame_gates", "FrameGates"), ("tree", "Tree"),
                 ("bad_serial", "BadSerial"), ("stale_configure", "StaleConfigure"), ("premature_buffer", "PrematureBuffer"),
                 ("unconfigured_remap", "UnconfiguredRemap"), ("bad_scale", "BadScale"), ("positioner_overflow", "PositionerOverflow")]
    rows = result.get("cycles", [])
    if len(rows) != len(scenarios) or result.get("cleanup_failed"):
        raise ValueError("Wayland stateのscenario数・回収が不正です")
    for index, (row, (scenario, mode)) in enumerate(zip(rows, scenarios)):
        report = row.get("report", {})
        if (row.get("cycle") != index + 1 or row.get("scenario") != scenario
                or report.get("state") != "pass" or report.get("error") is not None
                or report.get("fixture_revision") != "eidolon-wayland-state-v1" or report.get("callback_clock") != "virtual" or report.get("scenario") != mode
                or report.get("uid") != uid or report.get("nonce") != hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()[:16]
                or any(type(report.get(field)) is not int or report[field] != 0 for field in ("surfaces_after", "clients_after", "objects_after", "windows_after",
                    "pixel_bytes_after", "pool_bytes_after", "pool_count_after", "fixture_fds_after", "fixture_maps_after"))
                or type(report.get("elapsed_ms")) is not int or not 0 <= report["elapsed_ms"] < 60000
                or type(row.get("fds")) is not int or row["fds"] <= 0
                or type(row.get("native_heap_bytes")) is not int or row["native_heap_bytes"] <= 0):
            raise ValueError("Wayland stateのnonce・UID・資源回収が不正です")
        primary = report.get("proof", {})
        if index >= 4:
            if primary.get("client", {}).get("protocol_error") is not True:
                raise ValueError("異常clientのprotocol errorがありません")
            proof = report.get("recovery") or {}
        else:
            proof = primary
        client = proof.get("client", {})
        if (client.get("protocol_error") is not False or type(proof.get("checkpoints")) is not int
                or client.get("checkpoints") != proof["checkpoints"]
                or type(proof.get("cadence_checks")) is not int or proof["cadence_checks"] <= 0
                or type(proof.get("immutable_checks")) is not int or proof["immutable_checks"] < 3):
            raise ValueError("正常client・immutable snapshot・callbackの証明が不正です")
        ids = proof.get("window_ids", [])
        if not ids or any(not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value) or int(value) > 2**63 - 1 for value in ids):
            raise ValueError("Wayland window IDが不正です")
        if scenario == "lifecycle":
            if (any(proof.get(field) is not True for field in ("mapped", "unmapped", "remapped", "resize_commit_only", "consecutive_configures", "recreated", "rejected_resize"))
                    or len(ids) != 2 or int(ids[1]) <= int(ids[0]) or client.get("withheld") != 1):
                raise ValueError("map/unmap/remap・configure・ID非再利用が不正です")
        elif scenario == "frame_gates":
            if proof.get("withheld") != 3 or client.get("withheld") != 3:
                raise ValueError("visible/target/budgetによるcallback抑制が不正です")
        elif scenario == "tree":
            if (proof.get("tree_flags") != 127 or proof.get("tree_immutable_checks", 0) < 5 or proof.get("clip_checks") != 2
                    or proof.get("content_reused") is not True or client.get("popup_done") != 3
                    or client.get("popup_positions") != [[10, 10, 8, 8], [6, 6, 6, 6], [24, 26, 8, 8]]):
                raise ValueError("同期・重なり・nested popup・親clipが不正です")
        elif (proof.get("transforms") != 255 or proof.get("scales") != 3 or proof.get("checkpoints") != 16 or proof.get("immutable_checks") != 15):
            raise ValueError("8transform/scaleと異常client後の回復が不正です")
    after = result.get("after_stop", {})
    if (after.get("state") != "Stopped" or any(after.get(field) != 0 for field in ("compositor_workers", "dependency_workers", "helper_workers"))
            or after.get("render", {}).get("workers") != 0 or after.get("transport", {}).get("workers") != 0
            or type(result.get("accept_ms")) is not int or not 0 <= result["accept_ms"] < 100
            or type(result.get("stop_ms")) is not int or not 0 <= result["stop_ms"] < 10000):
        raise ValueError("Wayland state workerの停止・受付期限が不正です")
    if instrumentation:
        return instrumentation_result(snapshot, result, uid)
    last, current = result.get("last_handle", ""), snapshot.get("runtime", {})
    handle, state = current.get("handle", ""), current.get("runtime", {})
    return (isinstance(last, str) and last.isdecimal() and isinstance(handle, str) and handle.isdecimal()
            and 0 < int(last) < int(handle) <= 2**63 - 1 and current.get("code") == 0
            and current.get("registry") == {"runtimes": 1, "workers": 1} and state.get("state") == "Ready"
            and state.get("activity_visible") is True and state.get("xr_state") in {"VISIBLE", "FOCUSED"}
            and state.get("compositor_workers") == 0 and state.get("transport", {}).get("state") == "Idle")


def shm_result(snapshot, nonce, expected, uid, case, *, instrumentation=False):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != case or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("端末SHM caseが失敗しました")
    if result.get("result") != "pass":
        return False
    modes = [("truncate", "Truncate")] if case == "shm-truncate" else [
        ("pattern", "Pattern"), ("shared_cache", "SharedCache"), ("invalid_dimensions", "InvalidDimensions"), ("invalid_stride", "InvalidStride"),
        ("invalid_offset", "InvalidOffset"), ("invalid_format", "InvalidFormat"), ("negative_resize", "NegativeResize"), ("oversized_pool", "OversizedPool"), ("pool_limit", "PoolLimit")]
    rows = result.get("cycles", [])
    if len(rows) != len(modes) or result.get("cleanup_failed"):
        raise ValueError("SHM scenario数・回収が不正です")
    globals_expected = sorted([[name, version] for name, version in [
        ("wl_compositor", 4), ("wl_subcompositor", 1), ("wl_shm", 1), ("wl_output", 3),
        ("wl_seat", 5), ("xdg_wm_base", 1), ("wl_data_device_manager", 3)]])
    for index, (row, (scenario, mode)) in enumerate(zip(rows, modes)):
        report = row.get("report", {})
        frames, configures, mapped, copies = (1, 2, 2, 2) if scenario == "shared_cache" else (100, 1, 1, 100)
        if (row.get("cycle") != index + 1 or row.get("scenario") != scenario
                or report.get("state") != "pass" or report.get("error") is not None
                or report.get("fixture_revision") != "eidolon-shm-client-v1" or report.get("mode") != mode
                or report.get("uid") != uid or report.get("nonce") != hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()[:16]
                or report.get("verified_hashes") != copies or report.get("bad_maps") != int(scenario == "truncate")
                or report.get("server_copies") != copies or any(report.get(field) != frames for field in ("server_releases", "server_callbacks"))
                or any(report.get(field) != 0 for field in ("surfaces_after", "clients_after", "objects_after", "pixel_bytes_after",
                    "pool_bytes_after", "pool_count_after", "fixture_fds_after", "fixture_maps_after"))
                or type(report.get("elapsed_ms")) is not int or not 0 <= report["elapsed_ms"] < 60000
                or type(row.get("fds")) is not int or row["fds"] <= 0
                or type(row.get("native_heap_bytes")) is not int or row["native_heap_bytes"] <= 0):
            raise ValueError("SHM hash・SIGBUS・所有資源の証明が不正です")
        first = report.get("client", {})
        normal = first if scenario in {"pattern", "shared_cache"} else report.get("recovery", {})
        if scenario not in {"pattern", "shared_cache"} and first.get("protocol_error") is not True:
            raise ValueError("悪性clientのprotocol errorがありません")
        if (normal.get("protocol_error") is not False or sorted(normal.get("globals", [])) != globals_expected
                or any(normal.get(field) != frames for field in ("frames", "releases", "callbacks"))
                or any(normal.get(field) != configures for field in ("configures", "acks"))
                or any(normal.get(field) != mapped for field in ("enters", "leaves")) or normal.get("output_scale") != 1
                or (scenario == "shared_cache" and normal.get("release_deferred") is not True)
                or normal.get("cancellations") != 2 or normal.get("empty_selection") is not True
                or normal.get("output_mode") != [1024, 768, 60000] or normal.get("output_geometry") is not True
                or normal.get("output_done") is not True or normal.get("seat_capabilities") != 0):
            raise ValueError("正常clientのprotocol・回復・100frameが不正です")
    after = result.get("after_stop", {})
    if (after.get("state") != "Stopped" or any(after.get(field) != 0 for field in ("compositor_workers", "dependency_workers", "helper_workers"))
            or after.get("render", {}).get("workers") != 0 or after.get("transport", {}).get("workers") != 0
            or type(result.get("accept_ms")) is not int or not 0 <= result["accept_ms"] < 100
            or type(result.get("stop_ms")) is not int or not 0 <= result["stop_ms"] < 10000):
        raise ValueError("SHM workerの停止・受付期限が不正です")
    if instrumentation:
        return instrumentation_result(snapshot, result, uid)
    last = result.get("last_handle", "")
    current = snapshot.get("runtime", {})
    handle = current.get("handle", "")
    state = current.get("runtime", {})
    return (isinstance(last, str) and last.isdecimal() and isinstance(handle, str) and handle.isdecimal()
            and 0 < int(last) < int(handle) <= 2**63 - 1 and current.get("code") == 0
            and current.get("registry") == {"runtimes": 1, "workers": 1} and state.get("state") == "Ready"
            and state.get("activity_visible") is True and state.get("xr_state") in {"VISIBLE", "FOCUSED"}
            and state.get("compositor_workers") == 0 and state.get("transport", {}).get("state") == "Idle")


def waypipe_result(snapshot, nonce, expected, uid, dependency, *, instrumentation=False):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "waypipe" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("端末Waypipe caseが失敗しました")
    if result.get("result") != "pass":
        return False
    cycles = result.get("cycles", [])
    if len(cycles) != 3 or result.get("cleanup_failed"):
        raise ValueError("Waypipeの回数・回収が不正です")

    def proof(report, index, cancelled):
        value = report.get("waypipe", {})
        if (report.get("uid") != uid or report.get("version") != "waypipe 0.11.3" or report.get("error") is not None
                or report.get("cancelled") is not cancelled or report.get("directory_removed") is not True
                or report.get("owned_children") != 0 or value.get("owned_groups") != 0
                or value.get("clients_after") != 0 or value.get("objects_after") != 0 or value.get("socket_cleaned") is not True
                or value.get("nonce") != hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()[:16]
                or value.get("executable_sha256") != dependency.get("sha256")
                or value.get("child_uid") != uid or not isinstance(value.get("child_context"), str)
                or not 1 <= len(value["child_context"]) <= 1024
                or value.get("features") != ["lz4", "zstd"] or value.get("unavailable") != ["dmabuf", "video", "vaapi"]
                or type(value.get("stop_ms")) is not int or not 0 <= value["stop_ms"] < 5000):
            raise ValueError("Waypipeの版・実ELF hash・UID・停止・資源回収が不正です")
        if cancelled:
            if type(value.get("waited")) is not int or not 1 <= value["waited"] <= 3:
                raise ValueError("停止中Waypipeのwait証拠がありません")
        elif (report.get("cycles") != 3 or value.get("waited") != 3
                or type(value.get("compositor_connections")) is not int or value["compositor_connections"] < 3
                or value.get("transfer_connections") != 3):
            raise ValueError("Waypipeのclient起動・socket実接続・wait証拠がありません")
    fds = []
    for index, row in enumerate(cycles):
        if row.get("cycle") != index + 1 or type(row.get("fds")) is not int or row["fds"] <= 0:
            raise ValueError("Waypipeのcycle/FD記録が不正です")
        proof(row.get("report", {}), index, False)
        fds.append(row["fds"])
    if max(fds) - min(fds) > 2:
        raise ValueError("WaypipeのFDが継続増加しています")
    after = result.get("after_stop", {})
    proof(after.get("helper", {}), 3, True)
    if (after.get("state") != "Stopped" or any(after.get(field) != 0 for field in ("helper_workers", "dependency_workers", "compositor_workers"))
            or after.get("render", {}).get("workers") != 0 or after.get("transport", {}).get("workers") != 0
            or type(result.get("accept_ms")) is not int or not 0 <= result["accept_ms"] < 100
            or type(result.get("stop_ms")) is not int or not 0 <= result["stop_ms"] < 10000):
        raise ValueError("Waypipe実行中のruntime停止が不正です")
    last = result.get("last_handle", "")
    if not isinstance(last, str) or not re.fullmatch(r"[1-9][0-9]*", last):
        raise ValueError("Waypipeのhandleが不正です")
    if instrumentation:
        return instrumentation_result(snapshot, result, uid)
    current = snapshot.get("runtime", {})
    handle = current.get("handle", "")
    runtime = current.get("runtime", {})
    return (current.get("code") == 0 and isinstance(handle, str) and handle.isdecimal()
            and int(last) < int(handle) <= 2**63 - 1 and current.get("registry") == {"runtimes": 1, "workers": 1}
            and runtime.get("state") == "Ready" and runtime.get("activity_visible") is True
            and runtime.get("xr_state") in {"VISIBLE", "FOCUSED"} and runtime.get("helper_workers") == 0
            and runtime.get("transport", {}).get("state") == "Idle")


def native_deps_result(snapshot, nonce, expected, uid, dependencies, *, instrumentation=False):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != "native-deps" or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("端末native dependency caseが失敗しました")
    if result.get("result") != "pass":
        return False
    cycles = result.get("cycles", [])
    if len(cycles) != 10 or result.get("cleanup_failed"):
        raise ValueError("native dependencyの回数・回収が不正です")
    keymaps = dependencies["xkeyboard-config"]["keymaps"]
    for index, row in enumerate(cycles):
        report = row.get("report", {})
        token = hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()[:16]
        if (row.get("cycle") != index + 1 or report.get("state") != "pass" or report.get("error") is not None
                or report.get("nonce") != token or report.get("uid") != uid
                or any(report.get(field) is not True for field in ("display_created", "shm_global", "sync_done", "sync_delete_id", "fd_cloexec", "same_memfd", "owned_fds_closed", "socket_cleaned"))
                or report.get("received_fds") != 1 or report.get("mapped_bytes") != 4096
                or report.get("mapped_regions_after") != 0 or report.get("audited_fds") != 8
                or type(report.get("elapsed_ms")) is not int or not 0 <= report["elapsed_ms"] < 10000
                or type(row.get("fds")) is not int or row["fds"] <= 0
                or type(row.get("native_heap_bytes")) is not int or row["native_heap_bytes"] <= 0):
            raise ValueError("native dependencyのIPC・mapping・所有資源回収が不正です")
        layouts = report.get("keymaps", [])
        if len(layouts) != 2 or [entry.get("layout") for entry in layouts] != ["us", "jis"]:
            raise ValueError("US/JIS keymapの結果が不正です")
        for entry in layouts:
            wanted = keymaps[entry["layout"]]
            if (entry.get("sha256") != wanted["sha256"] or entry.get("bytes") != wanted["bytes"]
                    or entry.get("include_paths") != 0 or entry.get("base") != "a" or entry.get("shifted") != "A"
                    or entry.get("shifted_two") != ('"' if entry["layout"] == "jis" else "@")):
                raise ValueError("keymapのsource hash・自己完結性・意味が不正です")
    after = result.get("after_stop", {})
    if (after.get("state") != "Stopped" or after.get("dependency_workers") != 0 or after.get("helper_workers") != 0
            or after.get("render", {}).get("workers") != 0 or after.get("transport", {}).get("workers") != 0
            or after.get("transport", {}).get("owned_connections") != 0
            or type(result.get("accept_ms")) is not int or not 0 <= result["accept_ms"] < 100
            or type(result.get("stop_ms")) is not int or not 0 <= result["stop_ms"] < 10000):
        raise ValueError("native dependencyの停止・受付時間が不正です")
    last = result.get("last_handle", "")
    if not isinstance(last, str) or not re.fullmatch(r"[1-9][0-9]*", last):
        raise ValueError("native dependencyのhandleが不正です")
    if instrumentation:
        return instrumentation_result(snapshot, result, uid)
    current = snapshot.get("runtime", {})
    handle = current.get("handle", "")
    runtime = current.get("runtime", {})
    return (current.get("code") == 0 and isinstance(handle, str) and handle.isdecimal()
            and int(last) < int(handle) <= 2**63 - 1 and current.get("registry") == {"runtimes": 1, "workers": 1}
            and runtime.get("state") == "Ready" and runtime.get("activity_visible") is True
            and runtime.get("xr_state") in {"VISIBLE", "FOCUSED"} and runtime.get("dependency_workers") == 0
            and runtime.get("transport", {}).get("state") == "Idle")


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
            or state.get("app") is not None or after.get("app") is not None
            or transport.get("state") != "Idle" or transport.get("journal_ready") is not True or transport.get("pending_cleanup") != []
            or stopped.get("code") != 0 or stopped.get("handle") != handle or after.get("state") != "Stopped"
            or after.get("helper_workers") != 0 or after.get("dependency_workers", 0) != 0 or after.get("compositor_workers", 0) != 0
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
