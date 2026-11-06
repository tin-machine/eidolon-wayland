"""L03/L04の採取値。画面・入力・clipboard・process command lineは保存しない。"""
import re


def pick(source, fields):
    source = source if isinstance(source, dict) else {}
    return {key: source.get(key) for key in fields}


def battery(text):
    result = {}
    for field in ['AC powered', 'USB powered', 'Wireless powered', 'status', 'level', 'scale', 'temperature', 'voltage']:
        match = re.search(r'^\s*' + re.escape(field) + r':\s*(true|false|[0-9]+)\s*$', text, re.M)
        result[field] = None if match is None else ({'true': True, 'false': False}.get(match[1], match[1]))
        if isinstance(result[field], str): result[field] = int(result[field])
    return result


def pss_kib(text):
    for pattern in [r'\bTOTAL PSS:\s*(\d+)', r'^\s*TOTAL\s+(\d+)\s']:
        match = re.search(pattern, text, re.M)
        if match: return int(match[1])
    return None


def proc_stat(text):
    # commには空白/括弧があり得る。本文は保存しない。
    fields = text[text.rindex(')')+2:].split()
    return dict(cpu_ticks=int(fields[11])+int(fields[12]), start_ticks=int(fields[19]))


def compact(snapshot):
    runtime = snapshot.get('runtime', {}).get('runtime', {})
    if (not isinstance(snapshot.get('sequence'), str) or not snapshot['sequence'].isdigit()
            or not isinstance(runtime.get('state'), str) or not isinstance(snapshot.get('build_info'), dict)):
        raise ValueError('診断の必須項目がありません')
    app, render, transport = runtime.get('app') or {}, runtime.get('render', {}), runtime.get('transport', {})
    value = pick(runtime, ['state', 'scene', 'xr_state', 'activity_visible', 'commands_pending', 'events_pending', 'input_resets',
                           'helper_workers', 'dependency_workers', 'compositor_workers', 'error'])
    value['shutdown'] = pick(runtime.get('shutdown'), ['requested', 'complete', 'elapsed_ms', 'timed_out', 'sdk_bindings', 'pending_owners'])
    value['app'] = pick(app, ['state', 'session', 'mode', 'clients', 'objects', 'surfaces', 'pixel_bytes', 'pool_bytes', 'pool_count', 'copies', 'callbacks', 'error'])
    value['render'] = pick(render, ['mode', 'paused', 'queued', 'workers', 'contexts', 'latest_images', 'attaches', 'detaches', 'error'])
    value['render']['targets'] = [pick(t, ['key', 'role', 'frames', 'content_frame', 'frame_timing', 'max_swap_us']) for t in render.get('targets', [])]
    value['transport'] = pick(transport, ['state', 'session', 'workers', 'tokio_threads', 'owned_connections', 'error'])
    value['transport']['pending_cleanup_count'] = len(transport.get('pending_cleanup', []))
    value['transport']['forward_active'] = transport.get('forward', {}).get('active')
    value['adapter'] = pick(snapshot.get('adapter'), ['bindings', 'listeners', 'decoration_entities'])
    return dict(process_id=snapshot.get('process_id'), sequence=snapshot.get('sequence'),
                build_info=snapshot.get('build_info'), runtime=value)


def summarize(samples, duration, interval, scenario):
    successful = [s for s in samples if s.get('snapshot') and not s.get('errors')]
    coverage = (len(successful) == len(samples) and len(samples) >= 2
                and samples[-1]['elapsed_seconds'] >= duration
                and all(0 < b['elapsed_seconds']-a['elapsed_seconds'] <= interval*1.5+5 for a,b in zip(samples,samples[1:])))
    identities = {(s.get('pid'), s.get('process', {}).get('start_ticks')) for s in successful}
    fresh = len(successful) >= 2 and all(a['snapshot']['sequence'] != b['snapshot']['sequence'] for a,b in zip(successful,successful[1:]))
    battery_start = samples[0].get('battery', {}) if samples else {}
    battery_end = samples[-1].get('battery', {}) if samples else {}
    levels = [battery_start.get('level'), battery_end.get('level')]
    delta = levels[1]-levels[0] if all(type(n) is int for n in levels) and battery_start.get('scale') == battery_end.get('scale') == 100 else None
    violations = []
    limits = [('commands_pending', 256), ('events_pending', 128)]
    for index, sample in enumerate(successful):
        runtime = sample['snapshot']['runtime']
        metrics = [(name, runtime.get(name), bound) for name,bound in limits]
        metrics += [('render_queue', runtime['render'].get('queued'), 32),
                    ('pixel_bytes', runtime['app'].get('pixel_bytes'), 128*1024*1024),
                    ('pool_bytes', runtime['app'].get('pool_bytes'), 128*1024*1024)]
        for name, value, bound in metrics:
            if type(value) is int and value > bound: violations.append(dict(sample=index, metric=name, value=value, limit=bound))
    return dict(acceptance='not_run', scenario=scenario, samples=len(samples), complete_samples=len(successful),
                duration_seconds=duration, sampling_complete=coverage,
                same_process=len(identities)==1 and None not in next(iter(identities), (None,None)),
                diagnostics_advancing=fresh, battery_delta_percentage_points=delta,
                battery_non_decreasing=(delta >= 0) if delta is not None else None,
                resource_violations=violations,
                remaining=['Menu/sleep/reboot/装着の実操作', 'クラッシュ/ANR・温度停止の照合',
                           '100回再接続と切断後FD/PSSの照合', '30Hz fixtureと入力遅延の測定', 'HMDの最終視認'])
