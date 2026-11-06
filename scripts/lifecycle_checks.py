"""Phase6の停止結果。通常SDK/実footと全owner回収を別々の観測から照合する。"""
import re
from quest_checks import sdk_environment_result


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def number(value, lower=0, upper=2**63-1):
    return type(value) is int and lower <= value <= upper


def identifier(value):
    require(isinstance(value, str) and re.fullmatch('[1-9][0-9]*', value) is not None
            and int(value) <= 2**63-1, '世代IDが不正です')
    return int(value)


def empty_app(state):
    app = state.get('app', {})
    require(app.get('state') == 'stopped' and app.get('directory_cleaned') is True
            and app.get('windows') == [] and app.get('local', {}).get('waited') is True,
            'Wayland/helper/directoryの終了が未確認です')
    for key in ['clients', 'objects', 'surfaces', 'pixel_bytes', 'pool_bytes', 'pool_count', 'memfd_fds_after', 'memfd_maps_after']:
        require(type(app.get(key)) is int and app[key] == 0, 'Wayland資源が残っています: '+key)
    for key in ['workers', 'owned_groups']:
        require(type(app['local'].get(key)) is int and app['local'][key] == 0, 'helper回収が不正です')


def clean_transport(state, stopped, termux_uid):
    transport = state.get('transport', {})
    require(transport.get('state') == ('Stopped' if stopped else 'Disconnected')
            and transport.get('error') is None and transport.get('pending_cleanup') == [], 'SSH/remote回収未確認です')
    for key, expected in [('owned_connections', 0), ('forward_active', 0), ('tokio_threads', 0), ('workers', 0 if stopped else 1)]:
        require(type(transport.get(key)) is int and transport[key] == expected, 'SSH所有資源が不正です: '+key)
    report = transport.get('app_forward', {})
    require(report.get('uid') == termux_uid and report.get('remote_cleaned') is True
            and report.get('forward_cancelled') is True and report.get('pending_cleanup') == [], 'remote終了receiptが不足しています')


def live_foot(state, uid, termux_uid, sha):
    require(state.get('state') == 'Ready', '停止前にReadyでありません')
    app, transport = state.get('app', {}), state.get('transport', {})
    require(app.get('state') == 'running' and app.get('mode') == 'foot' and app.get('error') is None
            and transport.get('state') == 'Ready' and transport.get('owned_connections') == 1,
            '製品footとSSHの同時稼働を確認できません')
    local = app.get('local', {})
    require(local.get('uid') == uid and local.get('child_uid') == uid and local.get('owned_groups') == 1
            and local.get('executable_sha256') == sha and transport.get('app_forward', {}).get('uid') == termux_uid,
            'WaypipeのUID/同梱版が不一致です')
    windows, targets = app.get('windows', []), state.get('render', {}).get('targets', [])
    require(len(windows) == len(targets) == 1 and windows[0].get('life') == 'Mapped'
            and targets[0].get('role') == 'windows' and number(targets[0].get('frames'), 1)
            and targets[0].get('content_frame') == windows[0].get('frame')
            and targets[0].get('key', {}).get('window') == windows[0].get('window')
            and targets[0].get('key', {}).get('session') == app.get('session') == transport.get('session') == state.get('session'),
            '現在の一枚の実画像を確認できません')
    require(state.get('adapter', {}).get('bindings') == 1 and state['adapter'].get('listeners') == 1
            and state.get('shutdown', {}).get('sdk_bindings') == 1, 'SDK所有物の記帳が不一致です')
    return identifier(transport.get('session'))


def stop_result(snapshot, nonce, expected, uid, termux_uid, package):
    if not sdk_environment_result(snapshot, nonce, expected, 'stop'):
        return False
    proof = snapshot['check']
    require(type(uid) is int and type(termux_uid) is int and uid != termux_uid and proof.get('uid') == uid
            and number(proof.get('pid'), 1) and proof.get('consumer') == 'SDK'
            and all(proof.get(key) is False for key in ['synthetic_visibility', 'physical_input_verified', 'hmd_verified']),
            '実行範囲・UIDまたは手動受入れの主張が不正です')
    sha = package.get('dependencies', {}).get('waypipe', {}).get('sha256')
    require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha) is not None, '同梱Waypipe hashがありません')
    rows = proof.get('cycles', [])
    require(len(rows) == 20, '停止反復は20回必要です')
    previous_session = 0
    previous_handle = identifier(proof['preferences_environment']['fixture_handle'])
    owner_tid = None
    completed_jobs = None
    disconnected_fds = []
    for i, row in enumerate(rows):
        require(row.get('cycle') == i+1 and row.get('old_handle_rejected') is True, '反復/旧handle拒否が不正です')
        before, reopened = row.get('before_disconnect', {}), row.get('before_stop', {})
        require(identifier(before.get('handle')) == identifier(reopened.get('handle')) == previous_handle, '稼働中runtimeが変わりました')
        first = live_foot(before, uid, termux_uid, sha)
        second = live_foot(reopened, uid, termux_uid, sha)
        require(previous_session < first < second, 'SessionIdを再利用しています')
        previous_session = second
        disconnected = row.get('after_disconnect', {})
        require(number(row.get('disconnect_settle_ms'), 1000, 10000), '切断後の固定待機が未確認です')
        require(disconnected.get('state') == 'Ready' and disconnected.get('session') == str(first)
                and disconnected.get('handle') == before.get('handle') and number(row.get('disconnect_ms'), 0, 10000),
                '切断がruntimeまで破棄したか、期限/世代が不正です')
        empty_app(disconnected); clean_transport(disconnected, False, termux_uid)
        require(disconnected.get('render', {}).get('targets') == []
                and disconnected.get('shutdown', {}).get('sdk_bindings') == 0
                and all(disconnected.get('adapter', {}).get(k) == 0 for k in ['bindings', 'listeners', 'decoration_entities']),
                '切断後のSDK資源が残っています')
        stop = row.get('stop', {}); stopped = stop.get('runtime', {}); shutdown = stopped.get('shutdown', {})
        require(stop.get('handle') == reopened.get('handle') and number(stop.get('accept_ms'), 0, 99)
                and number(stop.get('stop_ms'), 0, 10000) and stopped.get('state') == 'Stopped', '停止受付/完了期限が不正です')
        require(shutdown.get('requested') is True and shutdown.get('complete') is True and shutdown.get('timed_out') is False
                and shutdown.get('pending_owners') == [] and shutdown.get('sdk_bindings') == 0
                and shutdown.get('deadline_ms') == 10000 and number(shutdown.get('elapsed_ms'), 0, 10000), '未回収ownerまたは停止timeoutがあります')
        empty_app(stopped); clean_transport(stopped, True, termux_uid)
        require(all(stopped.get(k) == 0 for k in ['helper_workers', 'dependency_workers', 'compositor_workers'])
                and stopped.get('render', {}).get('workers') == 0 and stopped['render'].get('contexts') == 0
                and stopped['render'].get('latest_images') == 0 and stopped['render'].get('targets') == [], '停止後のnative資源が残っています')
        restored = row.get('after_restart', {})
        next_handle = identifier(restored.get('handle'))
        require(next_handle > previous_handle and restored.get('state') == 'Ready'
                and restored.get('transport', {}).get('state') == 'Idle'
                and restored['transport'].get('pending_cleanup') == [], '新runtimeの復帰が未確認です')
        targets = restored.get('render', {}).get('targets', [])
        require(len(targets) == 1 and targets[0].get('role') == 'diagnostic' and number(targets[0].get('frames'), 3)
                and restored.get('adapter', {}).get('bindings') == 1 and restored['adapter'].get('listeners') == 1,
                '新しい通常SDK表示を確認できません')
        previous_handle = next_handle
        for state, active in [(before, 1), (reopened, 1), (disconnected, 1), (stopped, 0), (restored, 1)]:
            render = state.get('render', {})
            executor = render.get('executor') or {}
            tid = render.get('owner_tid')
            require(number(tid, 1) and executor.get('tid') == tid and executor.get('threads') == 1
                    and executor.get('active_jobs') == active and executor.get('failed') is False,
                    'GL executorの寿命・leaseが未確認です')
            if owner_tid is None:
                owner_tid = tid
            require(tid == owner_tid, '停止/再起動でGL threadを作り直しています')
        jobs = stopped['render']['executor'].get('completed_jobs')
        require(number(jobs, 1) and (completed_jobs is None or jobs == completed_jobs + 1),
                'GL jobの回収が反復と一致しません')
        completed_jobs = jobs
        for state in [before, reopened, disconnected, restored]:
            gpu = state.get('gpu_fds')
            require(isinstance(gpu, list) and all(number(x.get('fd')) and number(x.get('tid'), 1)
                    and type(x.get('alive')) is bool for x in gpu), 'GPU FDの内訳が未採取です')
            own = [x for x in gpu if x['tid'] == owner_tid]
            require(len(own) <= 1 and all(x['alive'] for x in own), '再利用GL threadのGPU FDが不正です')
        require(number(disconnected.get('fds'), 1), '切断後FDが未採取です')
        kinds = disconnected.get('fd_kinds')
        require(isinstance(kinds, dict) and kinds and all(number(v) for v in kinds.values())
                and sum(kinds.values()) == disconnected['fds'], 'FD分類が総数と一致しません')
        disconnected_fds.append(disconnected['fds'])
    # 初回2回のSDK/driver初期化を分け、以降18回の同じ切断状態で比較する。
    # 2時間soak/L04の代替ではなく、今回の反復増加を拒否する短い回帰検査。
    require(max(disconnected_fds[2:]) - min(disconnected_fds[2:]) <= 2, 'warmup後も切断後FDが増減し続けています')
    require(identifier(proof['preferences_environment']['last_handle']) == previous_handle, '最後のruntime世代が不一致です')
    return True
