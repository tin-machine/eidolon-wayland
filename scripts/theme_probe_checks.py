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


def theme_performance(rows):
    """累積計数の差分から求める。SDK/GPU timer/通常運用CPUの測定へ昇格しない。"""
    if [row.get("mode") for row in rows] != ["off", "reduced", "animated", "animated_metrics"]:
        raise ValueError("theme performance modes missing")
    reports=[]
    for row in rows:
        elapsed=row.get("elapsed_ms",0)
        if not 5900<=elapsed<=15000 or not 0<=row.get("process_cpu_ms",-1)<=elapsed*16:
            raise ValueError("theme performance interval invalid")
        mode=row['mode']; before=row.get('before',{}); after=row.get('after',{})
        first={t['key']['window']:t for t in before.get('render',{}).get('targets',[])}
        last={t['key']['window']:t for t in after.get('render',{}).get('targets',[])}
        if len(first)!=2 or first.keys()!=last.keys():raise ValueError('theme performance needs two targets')
        theme=after.get('theme',{});render=after.get('render',{})
        if (after.get('state')!='Ready' or render.get('error') is not None or theme.get('error') is not None
                or theme.get('loading') is not False or theme.get('selection')!=('off' if mode=='off' else 'builtin')
                or theme.get('reduced_motion') is not (mode=='reduced')
                or render.get('theme_id')!=(None if mode=='off' else 'cyan-orbit')
                or (render.get('composition',{}).get('decoration_texture_bytes',0)==0) is not (mode=='off')):
            raise ValueError('theme performance setting/texture mismatch')
        targets=[]
        for window,start in first.items():
            end=last[window]
            if end['key']!=start['key']:raise ValueError('target generation changed inside measurement')
            n=end['frames']-start['frames'];dec=end['decoration_frames']-start['decoration_frames']
            a,b=start.get('frame_timing',{}),end.get('frame_timing',{})
            bins=[y-x for x,y in zip(a.get('histogram_ms',[]),b.get('histogram_ms',[]))]
            total=b.get('total_us',0)-a.get('total_us',0)
            if (n<0 or len(bins)!=32 or any(x<0 for x in bins) or sum(bins)!=n
                    or b.get('samples',0)-a.get('samples',0)!=n or total<0
                    or (mode=='off' and dec!=0) or (mode!='off' and dec!=n)
                    or (mode in {'animated','animated_metrics'} and n<10)):
                raise ValueError('theme performance counters invalid')
            def quantile(p):
                seen=0
                for i,count in enumerate(bins):
                    seen+=count
                    if n and seen>=n*p:return i+1 if i<31 else 'over_31'
                return None
            targets.append(dict(window=window,frames=n,fps=n*1000/elapsed,draw_mean_ms=total/1000/n if n else None,
                                draw_p50_upper_ms=quantile(.5),draw_p95_upper_ms=quantile(.95),draw_p99_upper_ms=quantile(.99),
                                content_changed=start.get('content_frame')!=end.get('content_frame')))
        workloads=after.get('app',{}).get('workloads',[])
        observing=mode=='animated_metrics'
        if after.get('workspace',{}).get('workload_observation') is not observing:
            raise ValueError('theme performance observation setting mismatch')
        if not observing:
            remote=after.get('transport',{}).get('app_forward',{}).get('remote',{}).get('workload',{})
            if remote.get('capability')!='unavailable' or remote.get('scan_seconds')!=0:
                raise ValueError('disabled observer still scans or publishes measurements')
        if (len(workloads)!=2 or {w.get('window') for w in workloads}!=set(last)
                or len({w.get('terminal_id') for w in workloads})!=2
                or any((observing and (w.get('state')!='registered' or w.get('display',{}).get('coverage')!='complete'
                           or w.get('display',{}).get('stale') is not False or w.get('display',{}).get('scope')!='shell_tree'
                           or w.get('display',{}).get('rss_bytes',0)<=0))
                       or (not observing and w.get('display',{}).get('stale') is not True) for w in workloads)):
            raise ValueError('theme performance workload association incomplete')
        reports.append(dict(mode=mode,elapsed_ms=elapsed,process_cpu_cores=row['process_cpu_ms']/elapsed,
                            targets=targets,native_heap_after=row.get('native_heap_after')))
    return reports


def theme_windows_result(snapshot, nonce, expected, apk_uid, termux_uid, package):
    from quest_checks import two_windows_result
    if not two_windows_result(snapshot,nonce,expected,apk_uid,termux_uid,package,themed=True):return False
    result=snapshot['check']
    measurement=result.get('instrumentation',{})
    if any(measurement.get(key) is not True for key in ('measurement_cpu_awake','measurement_lock_held_until_end','measurement_lock_released')):
        raise ValueError('theme performance CPU wake lock scope/release is unverified')
    if result.get('cleanup_failed') or result.get('after_stop',{}).get('theme',{}).get('workers')!=0:
        raise ValueError('theme windows worker cleanup failed')
    theme_performance(result.get('performance',[]))
    fallback=result.get('fallback',{})
    if (result.get('synthetic_adapter_failure') is not True or fallback.get('state')!='Ready'
            or fallback.get('theme',{}).get('selection')!='off'
            or fallback.get('theme',{}).get('fallback_reason')!='SDK_DECORATION_FAILED'
            or fallback.get('render',{}).get('theme_id') is not None
            or fallback.get('render',{}).get('composition',{}).get('decoration_texture_bytes')!=0
            or len(fallback.get('render',{}).get('targets',[]))!=2
            or fallback.get('app',{}).get('session')!=result.get('session')
            or fallback.get('app',{}).get('error') is not None
            or any(p.get('decoration') is not None for p in fallback.get('panels',[]))):
        raise ValueError('decoration fallback did not preserve two windows/session')
    return True
