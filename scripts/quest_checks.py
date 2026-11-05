"""端末snapshotの合否判定。nonce・版・終了条件をすべて満たすまで成功にしない。"""
import re
import json
import hashlib
import math


def _prepared_keyboard(app, package, layout='us'):
    expected = package.get('dependencies', {}).get('keymaps', {}).get(layout, {}).get('sha256')
    report = app.get('input', {})
    return (isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected) is not None
            and report.get('keyboard') is True and report.get('layout') == layout
            and report.get('include_paths') == 0 and report.get('keymap_sha256') == expected)


def _wire_time(value, origin):
    return (type(value) is int and type(origin) is int and 0<=value<2**32 and 0<=origin<2**32
            and ((value-origin)&0xffffffff)<30000)


def keyboard_wire_result(snapshot, nonce, expected, uid, package, *, instrumentation=False):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != 'keyboard-wire' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('keyboard wire検査が失敗しました')
    if result.get('result') != 'pass':
        return False
    rows = result.get('cycles', [])
    if len(rows) != 2 or result.get('cleanup_failed'):
        raise ValueError('US/JISのwire検査と回収が必要です')
    keys = [[42,1],[3,1],[3,0],[42,0],[30,1],[30,0],[29,1],[42,1],[30,1],[29,0],[30,0],[42,0]]
    for index, (row, layout) in enumerate(zip(rows, ['us','jis'])):
        report = row.get('report', {})
        keyboard, client = report.get('keyboard', {}), report.get('client', {})
        order, modifiers = client.get('order', []), client.get('modifiers', [])
        wire_hash = keyboard.get('wire_keymap_sha256')
        actual=client.get('keys',[]);origin=report.get('input_time_ms')
        trace_ok=(len(actual)==12 and all(isinstance(k,list) and len(k)==3 and _wire_time(k[2],origin) for k in actual)
                  and [k[:2] for k in actual]==keys and all(k[2]==origin for k in actual[:9])
                  and len({k[2] for k in actual[9:]})==1)
        if (row.get('cycle') != index+1 or row.get('scenario') != 'keyboard_'+layout
                or report.get('nonce') != hashlib.sha256(f'{nonce}:{index}'.encode()).hexdigest()[:16]
                or report.get('state') != 'pass' or report.get('error') is not None or report.get('uid') != uid
                or report.get('fixture_revision') != 'eidolon-keyboard-wire-fixture-v1' or report.get('layout') != layout
                or report.get('synthetic_input') is not True or report.get('physical_input_verified') is not False
                or report.get('checkpoints') != 5 or report.get('shifted_two') != ord('@' if layout=='us' else '"')
                or report.get('old_focus_rejected') is not True
                or not _prepared_keyboard(dict(input=keyboard), package, layout)
                or keyboard.get('focus_window') is not None or keyboard.get('pressed') != 0
                or keyboard.get('delivered') != 12 or keyboard.get('resets') != 2
                or not isinstance(wire_hash, str) or not re.fullmatch('[0-9a-f]{64}',wire_hash)
                or client.get('keymap_format') != 1 or client.get('keymap_sha256') != wire_hash
                or type(client.get('keymap_bytes')) is not int or not 1000 < client['keymap_bytes'] < 1024*1024
                or client.get('repeat') != [25,600] or client.get('enters') != [[],[]] or client.get('leaves') != 2
                or not trace_ok or client.get('error') is not None
                or not modifiers or modifiers[-1] != [0,0,0,0] or not any(m[0] != 0 for m in modifiers)
                or 'leave' not in order or order[:order.index('leave')].count('key') != 12
                or order.count('keymap') != 1 or order.count('enter') != 2 or order.count('leave') != 2
                or order.index('keymap') > order.index('enter')
                or any(report.get(k) != 0 for k in ['clients_after','objects_after','surfaces_after','windows_after',
                    'pixel_bytes_after','pool_bytes_after','fixture_fds_after','fixture_maps_after'])):
            raise ValueError('keymap FD/evdev/modifiers/repeat/reset/owner回収の証拠が不正です')
    after = result.get('after_stop', {})
    if (after.get('state') != 'Stopped' or any(after.get(k) != 0 for k in ['compositor_workers','dependency_workers','helper_workers'])
            or after.get('render',{}).get('workers') != 0 or after.get('transport',{}).get('workers') != 0
            or after.get('transport',{}).get('owned_connections') != 0
            or type(result.get('accept_ms')) is not int or not 0 <= result['accept_ms'] < 100
            or type(result.get('stop_ms')) is not int or not 0 <= result['stop_ms'] < 10000
            or not re.fullmatch('[1-9][0-9]*', str(result.get('last_handle','')))):
        raise ValueError('keyboard検査runtimeの停止が不正です')
    if instrumentation:
        return instrumentation_result(snapshot, result, uid)
    current = snapshot.get('runtime', {})
    runtime = current.get('runtime', {})
    return (current.get('code') == 0 and current.get('registry') == dict(runtimes=1,workers=1)
            and str(current.get('handle','')).isdecimal() and int(current['handle']) > int(result['last_handle'])
            and runtime.get('state') == 'Ready' and runtime.get('activity_visible') is True
            and runtime.get('xr_state') in {'VISIBLE','FOCUSED'} and runtime.get('transport',{}).get('state') == 'Idle')


def keyboard_result(snapshot, nonce, expected, uid, termux_uid, package):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != 'keyboard' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('製品keyboard検査失敗: '+result.get('stage','unknown'))
    if result.get('result') != 'pass':
        return False
    if (result.get('consumer') != 'SDK' or result.get('synthetic_input') is not True or result.get('synthetic_visibility') is not False
            or result.get('product_host_path') is not True
            or any(result.get(k) is not False for k in ['physical_input_verified','physical_entry_verified','text_verified'])):
        raise ValueError('合成keyの範囲と物理入力の未検証を区別してください')
    target = result.get('target', {})
    if (any(not isinstance(target.get(k),str) or not re.fullmatch('[1-9][0-9]*',target[k]) for k in ['session','window','scene','generation'])
            or type(uid) is not int or type(termux_uid) is not int or uid==termux_uid):
        raise ValueError('入力先tuple/UIDが不正です')
    rows = result.get('cycles', [])
    stages = ['before','focused','typed','held','reset','late','refocused','final']
    if len(rows) != len(stages) or [r.get('stage') for r in rows] != stages:
        raise ValueError('入力/解除/古epoch/再入力の記録がありません')
    base = rows[1].get('runtime',{}).get('app',{}).get('input',{}).get('delivered')
    epoch = rows[1].get('runtime',{}).get('input_resets')
    if type(base) is not int or type(epoch) is not int:
        raise ValueError('入力counter/epochが不正です')
    for index, row in enumerate(rows):
        state = row.get('runtime', {})
        app, transport, render = (state.get(k,{}) for k in ['app','transport','render'])
        keyboard = app.get('input', {})
        panels = render.get('targets', [])
        forward = transport.get('app_forward', {})
        expected_focus = None if index in [0,4,5] else target['window']
        token=keyboard.get('focus_token')
        if (state.get('state') != 'Ready' or state.get('activity_visible') is not True or state.get('xr_state') != 'FOCUSED'
                or state.get('scene') != target['scene'] or app.get('session') != target['session'] or app.get('mode') != 'foot'
                or app.get('callback_clock') != 'target-driven' or app.get('error') is not None or not _prepared_keyboard(app,package)
                or app.get('local',{}).get('uid') != uid or app.get('local',{}).get('executable_sha256') != package.get('dependencies',{}).get('waypipe',{}).get('sha256')
                or transport.get('session') != target['session'] or transport.get('state') != 'Ready' or transport.get('credential_requests') != 1
                or forward.get('uid') != termux_uid or forward.get('mode') != 'foot' or forward.get('state') != 'running' or forward.get('error') is not None
                or len(panels) != 1 or panels[0].get('key') != target or panels[0].get('role') != 'windows' or panels[0].get('frames',0) < 3
                or keyboard.get('focus_window') != expected_focus or keyboard.get('pressed') != (2 if index==3 else 0)
                or (token is not None if expected_focus is None else not isinstance(token,str) or not re.fullmatch('[1-9][0-9]*',token))
                or keyboard.get('delivered') != base+[0,0,4,6,8,8,8,10][index]
                or type(state.get('input_resets')) is not int or (state['input_resets'] != epoch if index<4 else state['input_resets'] <= epoch)):
            raise ValueError('製品Host/foot/SDKとkey/resetの結果が不正です')
    first_token=rows[1]['runtime']['app']['input']['focus_token']
    second_token=rows[6]['runtime']['app']['input']['focus_token']
    if (any(rows[i]['runtime']['app']['input']['focus_token'] != first_token for i in [2,3])
            or int(second_token)<=int(first_token) or rows[7]['runtime']['app']['input']['focus_token'] != second_token):
        raise ValueError('再フォーカスに古いkeyの識別子を再利用しています')
    if rows[2]['runtime']['app'].get('copies',0) <= rows[1]['runtime']['app'].get('copies',0):
        raise ValueError('入力中の実footフレーム更新がありません')
    stop, restored = result.get('stop', {}), result.get('restored', {})
    panels = restored.get('render',{}).get('targets',[])
    if (stop.get('old_runtime_destroyed') is not True or type(stop.get('stop_ms')) is not int or not 0 <= stop['stop_ms'] < 10000
            or not str(result.get('old_handle','')).isdecimal() or not str(result.get('new_handle','')).isdecimal()
            or int(result['new_handle']) <= int(result['old_handle'])
            or restored.get('state') != 'Ready' or restored.get('activity_visible') is not True or restored.get('xr_state') != 'FOCUSED'
            or restored.get('transport',{}).get('state') != 'Idle' or restored.get('transport',{}).get('pending_cleanup') != []
            or len(panels) != 1 or panels[0].get('role') != 'diagnostic' or panels[0].get('frames',0) < 3):
        raise ValueError('入力中の停止/通常UI復帰が不正です')
    return True


def resize_result(snapshot, nonce, expected, uid, termux_uid, package):
    if snapshot.get('schema')!=1 or snapshot.get('build_info')!=expected: return False
    result=snapshot.get('check',{})
    if result.get('case')!='resize' or result.get('nonce')!=nonce: return False
    if result.get('result')=='fail': raise ValueError('製品resize検査失敗: '+result.get('stage','unknown'))
    if result.get('result')!='pass': return False
    if (result.get('consumer')!='SDK' or any(result.get(k) is not True for k in ['product_host_path','synthetic_input','source_destroyed'])
            or any(result.get(k) is not False for k in ['synthetic_visibility','physical_input_verified','text_verified']) or result.get('cleanup_failed')):
        raise ValueError('resizeのSDK/合成操作/物理未検証の範囲が不正です')
    target=result.get('target',{}); rows=result.get('cycles',[])
    positive=lambda n:isinstance(n,str) and re.fullmatch('[1-9][0-9]*',n) is not None and int(n)<=2**63-1
    if (any(not positive(target.get(k)) for k in ['session','window','scene','generation']) or type(uid) is not int or type(termux_uid) is not int or uid==termux_uid
            or [r.get('stage') for r in rows]!=['before','small','wide','physical','clicked','typed']):
        raise ValueError('resizeの入力先/stage/UIDが不正です')
    def near(a,b): return type(a) in {int,float} and math.isfinite(a) and abs(a-b)<1e-4
    for i,row in enumerate(rows):
        state=row.get('runtime',{});app=state.get('app',{});transport=state.get('transport',{})
        panels=state.get('render',{}).get('targets',[]);windows=app.get('windows',[])
        forward=transport.get('app_forward',{})
        if len(windows)!=1 or len(panels)!=1: raise ValueError('resize対象window/panelが一枚ではありません')
        window=windows[0];sizing=window.get('sizing',{});resize=window.get('resize',{})
        size=dict(width=1024,height=768) if i==0 else dict(width=800,height=500) if i==1 else dict(width=1280,height=720)
        physical=[1.2,.9] if i<3 else [1.44,1.08]
        if (state.get('state')!='Ready' or state.get('activity_visible') is not True or state.get('xr_state')!='FOCUSED'
                or state.get('scene')!=target['scene'] or app.get('session')!=target['session'] or app.get('mode')!='foot' or app.get('callback_clock')!='target-driven'
                or app.get('error') is not None or not _prepared_keyboard(app,package) or app.get('local',{}).get('uid')!=uid
                or app.get('local',{}).get('executable_sha256')!=package.get('dependencies',{}).get('waypipe',{}).get('sha256')
                or transport.get('state')!='Ready' or transport.get('session')!=target['session'] or transport.get('credential_requests')!=1
                or forward.get('uid')!=termux_uid or forward.get('state')!='running' or forward.get('mode')!='foot' or forward.get('error') is not None
                or panels[0].get('role')!='windows' or panels[0].get('key')!=target or panels[0].get('frames',0)<3
                or panels[0].get('width')!=1024 or panels[0].get('height')!=768
                or window.get('window')!=target['window'] or window.get('life')!='Mapped' or window.get('logical_size')!=size or resize.get('committed_size')!=size
                or resize.get('error') is not None or (i>0 and (resize.get('phase')!='applied' or resize.get('requested_logical_size')!=size
                    or not positive(panels[0].get('content_frame')) or not positive(window.get('frame')) or int(panels[0]['content_frame'])<int(window['frame'])))
                or sizing.get('producer_size_px')!=dict(width=1024,height=768)
                or any(not isinstance(sizing.get(k),dict) or not near(sizing[k].get(field),v) for k in ['physical_size_m','applied_physical_size_m'] for field,v in zip(['width_m','height_m'],physical))):
            raise ValueError('通常footのconfigure/画像/SDKメートル/実EGL寸法が不正です')
    apps=[r['runtime']['app'] for r in rows]
    if (apps[1]['configures']<=apps[0]['configures'] or not apps[1]['configures']<apps[2]['configures']<=apps[1]['configures']+2
            or apps[3]['configures']!=apps[2]['configures'] or any(apps[i]['configures']<apps[3]['configures'] for i in [4,5])
            or any(apps[i]['windows'][0]['resize']['configured_serial']!=apps[2]['windows'][0]['resize']['configured_serial'] for i in [3,4,5])
            or apps[5]['input']['delivered']!=apps[4]['input']['delivered']+4 or apps[5]['copies']<=apps[4]['copies']
            or apps[4]['input']['pointer']['buttons']!=apps[3]['input']['pointer']['buttons']+2
            or any(apps[i]['input'].get('focus_window')!=target['window'] or apps[i]['input'].get('pressed')!=0 or apps[i]['input']['pointer'].get('pressed')!=0 for i in [4,5])):
        raise ValueError('physical拡大がconfigureを変更したか、resize後の入力が配送されていません')
    hit=result.get('hit',{});uv=hit.get('uv',[]);world=hit.get('world',[])
    if (len(uv)!=2 or len(world)!=3 or any(type(a) not in {int,float} or not math.isfinite(a) or abs(a-b)>=.02 for a,b in zip(uv,[.53,.47]))
            or any(not near(a,b) for a,b in zip(world,[.03*1.44,1.4+.03*1.08,1.5]))):
        raise ValueError('拡大後の実SDK ray交差が不正です')
    stopped=result.get('stop',{});restored=result.get('restored',{});panels=restored.get('render',{}).get('targets',[])
    if (stopped.get('old_runtime_destroyed') is not True or type(stopped.get('stop_ms')) is not int or not 0<=stopped['stop_ms']<10000
            or not positive(result.get('old_handle')) or not positive(result.get('new_handle')) or int(result['new_handle'])<=int(result['old_handle'])
            or restored.get('state')!='Ready' or restored.get('activity_visible') is not True or restored.get('xr_state')!='FOCUSED'
            or restored.get('transport',{}).get('state')!='Idle' or restored.get('transport',{}).get('pending_cleanup')!=[]
            or len(panels)!=1 or panels[0].get('role')!='diagnostic' or panels[0].get('frames',0)<3):
        raise ValueError('resize後の停止・通常UI復帰が不正です')
    return True


def pointer_result(snapshot, nonce, expected, uid, termux_uid, package):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != 'pointer' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('製品pointer検査失敗: '+result.get('stage','unknown'))
    if result.get('result') != 'pass':
        return False
    if (result.get('consumer') != 'SDK' or result.get('dispatch') != 'Scene.onInput' or result.get('raycast') != 'Scene.lineSegmentIntersect'
            or any(result.get(k) is not True for k in ['synthetic_input','synthetic_menu','product_host_path','sources_destroyed'])
            or any(result.get(k) is not False for k in ['synthetic_visibility','physical_input_verified','os_menu_verified'])):
        raise ValueError('SDK合成入力/実OS menu/物理操作の範囲が不正です')
    target = result.get('target', {})
    positive = lambda value: isinstance(value,str) and re.fullmatch('[1-9][0-9]*',value) is not None
    sources = result.get('sources', [])
    points = result.get('points', [])
    if (any(not positive(target.get(k)) for k in ['session','window','scene','generation']) or type(uid) is not int
            or type(termux_uid) is not int or uid == termux_uid or len(sources) != 2 or any(not positive(s) for s in sources)
            or sources[0] == sources[1] or len(points) != 2):
        raise ValueError('pointer入力先/source/UIDが不正です')
    for row, uv in zip(points, [[.23,.31],[.73,.61]]):
        world = [(uv[0]-.5)*1.2,1.4+(.5-uv[1])*.9,1.5]
        if (row.get('expected') != uv or not positive(row.get('entity')) or row['entity'] in sources
                or any(not isinstance(row.get(k),list) or len(row[k]) != n for k,n in [('uv',2),('world',3),('hit_point',3)])
                or any(not math.isfinite(a) or abs(a-b) >= .02 for a,b in zip(row['uv'],uv))
                or any(not math.isfinite(a) or abs(a-b) >= 1e-4 for k in ['world','hit_point'] for a,b in zip(row[k],world))):
            raise ValueError('SDK実交差のUV/worldが既知quadと一致しません')
    if points[0]['entity'] != points[1]['entity']:
        raise ValueError('別panelのUVを混ぜています')
    cancelled = result.get('cancelled', {})
    before = cancelled.get('transport', {})
    credential_base = result.get('credential_base')
    if (cancelled.get('state') != 'Ready' or cancelled.get('app') is not None or before.get('state') != 'Disconnected'
            or before.get('owned_connections') != 0 or before.get('tokio_threads') != 0 or before.get('pending_cleanup') != []
            or type(credential_base) is not int or not 0 <= credential_base <= 1 or before.get('credential_requests') != credential_base):
        raise ValueError('開始直後の取消が接続/アプリを残しました')
    rows = result.get('cycles', [])
    stages = ['before','hover','pressed','foreign','drag','released','scroll','scroll_stop','held','reset','old_held','refocused']
    if [r.get('stage') for r in rows] != stages:
        raise ValueError('pointer操作/捕捉/解除/再入力の記録がありません')
    first = rows[0].get('runtime', {})
    base_input = first.get('app', {}).get('input', {})
    base_pointer = base_input.get('pointer', {})
    counters = [base_pointer.get('buttons'),base_pointer.get('axis_frames'),base_input.get('delivered'),first.get('input_resets')]
    if any(type(n) is not int or n < 0 for n in counters):
        raise ValueError('pointerの基準counterが不正です')
    buttons,axes,keys,epoch = counters
    capture = rows[2].get('runtime', {}).get('app', {}).get('input', {}).get('pointer', {}).get('capture_surface')
    token = rows[2].get('runtime', {}).get('app', {}).get('input', {}).get('focus_token')
    if not positive(capture) or not positive(token):
        raise ValueError('pointer捕捉/keyboard focus識別子がありません')
    for index,row in enumerate(rows):
        state = row.get('runtime', {})
        app,transport = state.get('app', {}),state.get('transport', {})
        inputs = app.get('input', {}); pointer = inputs.get('pointer', {})
        panels = state.get('render', {}).get('targets', [])
        forward = transport.get('app_forward', {})
        grabbed = index in [2,3,4,8]
        unfocused = index in [0,1,9,10]
        if (state.get('state') != 'Ready' or state.get('activity_visible') is not True or state.get('xr_state') != 'FOCUSED'
                or state.get('scene') != target['scene'] or app.get('session') != target['session'] or app.get('mode') != 'foot'
                or app.get('callback_clock') != 'target-driven' or app.get('error') is not None or not _prepared_keyboard(app,package)
                or app.get('local', {}).get('uid') != uid or app.get('local', {}).get('executable_sha256') != package.get('dependencies', {}).get('waypipe', {}).get('sha256')
                or transport.get('state') != 'Ready' or transport.get('session') != target['session'] or transport.get('credential_requests') != credential_base+1
                or forward.get('uid') != termux_uid or forward.get('mode') != 'foot' or forward.get('state') != 'running' or forward.get('error') is not None
                or len(panels) != 1 or panels[0].get('key') != target or panels[0].get('role') != 'windows' or panels[0].get('frames',0) < 3
                or pointer.get('ready') is not True or pointer.get('pressed') != int(grabbed)
                or pointer.get('capture_window') != (target['window'] if grabbed else None)
                or pointer.get('capture_surface') != (capture if grabbed else None)
                or pointer.get('buttons') != buttons+[0,0,1,1,1,2,2,2,3,4,4,6][index]
                or pointer.get('axis_frames') != axes+(0 if index<6 else 1 if index==6 else 2)
                or inputs.get('pressed') != int(index==8) or inputs.get('delivered') != keys+(0 if index<8 else 1 if index==8 else 2)
                or inputs.get('focus_window') != (None if unfocused else target['window'])
                or (inputs.get('focus_token') is not None if unfocused else not positive(inputs.get('focus_token')))
                or (not unfocused and index<9 and inputs.get('focus_token') != token)
                or type(state.get('input_resets')) is not int or (state['input_resets'] != epoch if index<9 else state['input_resets'] <= epoch)):
            raise ValueError('通常SDK/Host/footとpointer焦点・捕捉・解除の結果が不正です')
    if (int(rows[11]['runtime']['app']['input']['focus_token']) <= int(token)
            or rows[4]['runtime']['app']['input']['pointer']['motions'] <= rows[3]['runtime']['app']['input']['pointer']['motions']
            or rows[10]['runtime']['app']['input']['pointer']['motions'] <= rows[9]['runtime']['app']['input']['pointer']['motions']):
        raise ValueError('drag/古held後のmotion/新focusがありません')
    stop,restored = result.get('stop', {}),result.get('restored', {})
    panels = restored.get('render', {}).get('targets', [])
    if (stop.get('old_runtime_destroyed') is not True or type(stop.get('stop_ms')) is not int or not 0 <= stop['stop_ms'] < 10000
            or not positive(result.get('old_handle')) or not positive(result.get('new_handle')) or int(result['new_handle']) <= int(result['old_handle'])
            or restored.get('state') != 'Ready' or restored.get('activity_visible') is not True or restored.get('xr_state') != 'FOCUSED'
            or restored.get('transport', {}).get('state') != 'Idle' or restored.get('transport', {}).get('pending_cleanup') != []
            or len(panels) != 1 or panels[0].get('role') != 'diagnostic' or panels[0].get('frames',0) < 3):
        raise ValueError('pointer入力中の停止/通常UI復帰が不正です')
    return True


def pointer_wire_result(snapshot, nonce, expected, uid, package, *, instrumentation=False):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result=snapshot.get('check', {})
    if result.get('case') != 'pointer-wire' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('pointer wire検査が失敗しました')
    if result.get('result') != 'pass':
        return False
    rows=result.get('cycles', [])
    if len(rows)!=1 or result.get('cleanup_failed'):
        raise ValueError('pointer wireの回数/回収が不正です')
    row=rows[0];report=row.get('report',{});client=report.get('client',{});events=client.get('events',[])
    phases=client.get('checkpoints',[]);wanted=['Mapped','HoverChild','Hole','Pressed','Released','FocusedB','Reset','UnmappedB','DestroyedChild']
    if ([p.get('stage') for p in phases]!=wanted or any(type(p.get('events')) is not int or not 0<=p['events']<=len(events) for p in phases)
            or [p['events'] for p in phases]!=sorted(p['events'] for p in phases)):
        raise ValueError('pointerの受信checkpointが不正です')
    reset=phases[6];normal=events[:reset['events']];unmapped=events[:phases[7]['events']]
    buttons=lambda trace:[(e.get('surface'),e.get('code'),e.get('state')) for e in trace if e.get('event')=='button']
    expected_buttons=[('child',272,1),('child',272,0),('root_b',273,1),('root_b',273,0),('root_b',272,1),('root_b',274,1),('root_b',272,0),('root_b',274,0)]
    inputs=report.get('input',{});pointer=inputs.get('pointer',{});keyboard=client.get('keyboard',{})
    fields=['letterbox_rejected','region_checked','source_capture_checked','off_panel_release','hover_keeps_keyboard',
            'focus_changed','duplicate_reset','late_source_ignored','unmap_released','destroy_released']
    ids=[client.get(k) for k in ['root_a','root_b','child']]
    if (any(type(i) is not int or not 0<i<2**32 for i in ids) or len(set(ids))!=3):
        raise ValueError('pointer clientのsurface IDが不正です')
    if (row.get('cycle')!=1 or row.get('scenario')!='pointer' or type(uid) is not int or report.get('uid')!=uid or report.get('state')!='pass' or report.get('error') is not None
            or report.get('nonce')!=hashlib.sha256(f'{nonce}:0'.encode()).hexdigest()[:16]
            or report.get('fixture_revision')!='eidolon-pointer-wire-fixture-v1' or report.get('checkpoints')!=9
            or report.get('synthetic_input') is not True or report.get('physical_input_verified') is not False
            or any(report.get(k) is not True for k in fields) or client.get('error') is not None
            or not _prepared_keyboard(dict(input=inputs),package) or inputs.get('focus_window') is not None or inputs.get('focus_token') is not None or inputs.get('pressed')!=0
            or pointer.get('ready') is not True or pointer.get('pressed')!=0
            or any(pointer.get(k) is not None for k in ['hover_window','hover_surface','capture_window','capture_surface'])
            or buttons(normal)!=expected_buttons or buttons(unmapped)!=expected_buttons+[('root_b',272,1),('root_b',272,0)]
            or [(e.get('axis'),e.get('value')) for e in normal if e.get('event')=='axis']!=[(1,2.5),(0,-15),(0,3)]
            or [e.get('axis') for e in normal if e.get('event')=='axis_stop']!=[1,0,0]
            or any(not _wire_time(e.get('time'),report.get('input_time_ms')) for e in events if e.get('event')=='button')
            or not any(e.get('event')=='axis_source' and e.get('source')==2 for e in normal)
            or not any(e.get('event')=='frame' for e in normal)
            or not any(e.get('event')=='motion' and e.get('surface')=='child' and e.get('position')==[-12,8] for e in normal)
            or not normal or normal[0]!=dict(event='enter',surface='child',position=[8,8])
            or reset.get('keyboard_enters')!=[client.get('root_a'),client.get('root_b')] or reset.get('keyboard_leaves')!=2 or reset.get('keys')!=2
            or [k[:2] for k in keyboard.get('keys',[])]!=[[42,1],[42,0]]
            or any(len(k)!=3 or not _wire_time(k[2],report.get('input_time_ms')) for k in keyboard.get('keys',[]))
            or keyboard.get('keymap_sha256')!=inputs.get('wire_keymap_sha256')
            or any(report.get(k)!=0 for k in ['clients_after','objects_after','surfaces_after','windows_after','pixel_bytes_after',
                    'pool_bytes_after','fixture_fds_after','fixture_maps_after'])):
        raise ValueError('pointerのregion/capture/axis/focus/reset/wire/回収の証明が不正です')
    after=result.get('after_stop',{})
    if (after.get('state')!='Stopped' or any(after.get(k)!=0 for k in ['compositor_workers','dependency_workers','helper_workers'])
            or after.get('render',{}).get('workers')!=0 or after.get('transport',{}).get('workers')!=0
            or type(result.get('accept_ms')) is not int or not 0<=result['accept_ms']<100
            or type(result.get('stop_ms')) is not int or not 0<=result['stop_ms']<10000
            or not re.fullmatch('[1-9][0-9]*',str(result.get('last_handle','')))):
        raise ValueError('pointer検査runtimeの停止が不正です')
    if instrumentation:
        return instrumentation_result(snapshot,result,uid)
    current=snapshot.get('runtime',{});runtime=current.get('runtime',{})
    return (current.get('code')==0 and current.get('registry')==dict(runtimes=1,workers=1)
            and str(current.get('handle','')).isdecimal() and int(current['handle'])>int(result['last_handle'])
            and runtime.get('state')=='Ready' and runtime.get('activity_visible') is True and runtime.get('xr_state') in {'VISIBLE','FOCUSED'}
            and runtime.get('transport',{}).get('state')=='Idle')


def ray_result(snapshot, nonce, expected):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != 'ray' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('SDK ray校正失敗: ' + result.get('stage', 'unknown'))
    if result.get('result') != 'pass':
        return False
    key, origin, source = result.get('target', {}), result.get('uv_origin'), result.get('source')
    if (result.get('synthetic_input') is not True or result.get('synthetic_visibility') is not False
            or result.get('physical_input_verified') is not False or result.get('dispatch') != 'Scene.onInput'
            or result.get('raycast') != 'Scene.lineSegmentIntersect' or result.get('capture_detached') is not True
            or result.get('source_destroyed') is not True or result.get('cleanup_failed') or origin not in {'top_left','bottom_left'}
            or not isinstance(source,str) or not re.fullmatch(r'[1-9][0-9]*',source)
            or any(not isinstance(key.get(k),str) or not re.fullmatch(r'[1-9][0-9]*',key[k]) for k in ('session','window','scene','generation'))
            or type(result.get('ui_thread')) is not int or result['ui_thread'] < 1):
        raise ValueError('ray検査の範囲/世代/回収が不正です')
    rows, inputs = result.get('cycles', []), result.get('inputs', [])
    points = [('top_left',.01,.01),('top_right',.99,.01),('bottom_left',.01,.99),('bottom_right',.99,.99),('center',.5,.5)]
    if len(rows) != 5 or len(inputs) != 11:
        raise ValueError('四隅/中央の交差とpress/up/stopが必要です')
    def near(value, expected, tolerance=.02):
        return type(value) in {int,float} and math.isfinite(value) and abs(value-expected) < tolerance
    entity = None
    for index, (row, (label,u,v)) in enumerate(zip(rows,points)):
        uv, world = row.get('uv', []), row.get('world', [])
        hit, start, end = (row.get(k,[]) for k in ('hit_point','ray_start','ray_end'))
        if (row.get('label') != label or len(uv) != 2 or len(world) != 3
                or not near(uv[0],u) or not near(uv[1],v if origin=='top_left' else 1-v)
                or not near(world[0],-.8+(u-.5)*1.2,1e-4) or not near(world[1],1.4+(.5-v)*.9,1e-4) or not near(world[2],1.6,1e-4)
                or len(hit) != 3 or any(not near(hit[axis],world[axis],1e-4) for axis in range(3))
                or len(start) != 3 or any(not near(start[axis],[0,1.4,.2][axis],1e-4) for axis in range(3))
                or len(end) != 3 or not near(end[2],2.5,1e-4)
                or any(not near(start[axis]+(end[axis]-start[axis])*(1.6-.2)/(2.5-.2),world[axis],1e-4) for axis in range(2))
                or not isinstance(row.get('entity'),str) or not re.fullmatch(r'[1-9][0-9]*',row['entity'])
                or row['entity'] == source or entity is not None and row['entity'] != entity):
            raise ValueError('実quadの交差/UV原点/fixture位置が不正です')
        entity = row['entity']
        for phase in range(2):
            event = inputs[index*2+phase]
            observed = event.get('uv', [])
            if (event.get('event') != 'input' or event.get('target') != key or event.get('source') != source
                    or event.get('changed') != ['trigger_right'] or event.get('state') != (['trigger_right'] if phase==0 else [])
                    or len(observed) != 2 or any(not near(observed[axis],uv[axis],1e-6) for axis in range(2))):
                raise ValueError('SDK listenerのchanged/state/UVが不正です')
    for event in inputs:
        if (type(event.get('callback_thread')) is not int or event['callback_thread'] < 1
                or event.get('delivery_thread') != result['ui_thread'] or event.get('target') != key or event.get('source') != source):
            raise ValueError('SDK listenerのthread/世代が不正です')
    if inputs[-1].get('event') != 'stop':
        raise ValueError('SDK stopInputがありません')
    for wire in [dict(code=0, runtime=result.get('ready',{})), result.get('after',{})]:
        state = wire.get('runtime', {})
        targets = state.get('render',{}).get('targets',[])
        if (wire.get('code') != 0 or state.get('state') != 'Ready' or state.get('activity_visible') is not True
                or state.get('xr_state') not in {'VISIBLE','FOCUSED'} or state.get('scene') != key['scene']
                or len(targets) != 1 or targets[0].get('key') != key or targets[0].get('role') != 'diagnostic' or targets[0].get('frames',0) < 3):
            raise ValueError('実SDK可視性/描画の継続が不正です')
    return True


def foot_sdk_result(snapshot, nonce, expected, apk_uid, termux_uid, package):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != 'foot' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('通常SDK foot検査失敗: ' + result.get('stage', 'unknown'))
    if result.get('result') != 'pass':
        return False
    if (result.get('consumer') != 'SDK' or result.get('synthetic_visibility') is not False
            or result.get('product_host_path') is not True or result.get('hmd_verified') is not False
            or type(apk_uid) is not int or type(termux_uid) is not int or apk_uid == termux_uid):
        raise ValueError('通常SDK/Host/可視性の検査範囲が不正です')
    rows = result.get('cycles', [])
    if len(rows) != 2:
        raise ValueError('SDKパネル再生成の証明がありません')
    waypipe_hash = package.get('dependencies', {}).get('waypipe', {}).get('sha256')
    if not isinstance(waypipe_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', waypipe_hash):
        raise ValueError('WaypipeのAPK同梱hashが不正です')
    previous, first_key = 0, None
    previous_adapter = None
    for row in rows:
        state, adapter = row.get('runtime', {}), row.get('adapter', {})
        app, transport, render = (state.get(k, {}) for k in ('app','transport','render'))
        local, forward = app.get('local', {}), transport.get('app_forward', {})
        targets, windows, plans = render.get('targets', []), app.get('windows', []), state.get('panels', [])
        key = targets[0].get('key', {}) if len(targets) == 1 else {}
        if (any(not isinstance(key.get(k), str) or not re.fullmatch(r'[1-9][0-9]*', key[k]) or int(key[k]) > 2**63-1 for k in ('session','window','scene','generation'))
                or int(key.get('generation','0')) <= previous):
            raise ValueError('SDKパネルのtuple/世代が不正です')
        if first_key is not None and any(key[k] != first_key[k] for k in ('session','window','scene')):
            raise ValueError('同じlogical windowの再生成ではありません')
        if (state.get('state') != 'Ready' or state.get('activity_visible') is not True or state.get('xr_state') not in {'VISIBLE','FOCUSED'}
                or state.get('scene') != key['scene'] or app.get('session') != key['session'] or app.get('mode') != 'foot'
                or app.get('callback_clock') != 'target-driven' or app.get('error') is not None or app.get('non_uniform') is not True
                or app.get('callbacks',0) < 1 or app.get('copies',0) < 1 or app.get('pool_rejections') != 0 or not _prepared_keyboard(app, package)
                or local.get('uid') != apk_uid or local.get('child_uid') != apk_uid or local.get('executable_sha256') != waypipe_hash
                or transport.get('state') != 'Ready' or transport.get('session') != key['session'] or transport.get('credential_requests') != 1
                or forward.get('mode') != 'foot' or forward.get('state') != 'running' or forward.get('uid') != termux_uid or forward.get('error') is not None
                or forward.get('tools',{}).get('foot') != dict(version='1.28.0')
                or render.get('mode') != 'windows' or render.get('error') is not None or render.get('contexts') != 1 or render.get('paused') is not False
                or targets[0].get('role') != 'windows' or targets[0].get('frames',0) < 3 or targets[0].get('width') != 1024 or targets[0].get('height') != 768
                or len(windows) != 1 or windows[0].get('window') != key['window'] or windows[0].get('life') != 'Mapped'
                or not isinstance(targets[0].get('content_frame'),str) or not re.fullmatch(r'[1-9][0-9]*', targets[0]['content_frame'])
                or len(plans) != 1 or plans[0].get('action') != 'create' or plans[0].get('target') != key
                or plans[0].get('applied') is not True or plans[0].get('error') is not None
                or adapter.get('bindings') != 1 or adapter.get('listeners') != 1):
            raise ValueError('通常SDKでの実foot/実可視性/適用済みの証明が不正です')
        if previous_adapter and (adapter.get('created',0) <= previous_adapter.get('created',0) or adapter.get('destroyed',0) <= previous_adapter.get('destroyed',0)):
            raise ValueError('旧SDK Entityの回収がありません')
        previous, first_key, previous_adapter = int(key['generation']), key, adapter
    stop, restored = result.get('stop', {}), result.get('restored', {})
    render, transport = restored.get('render', {}), restored.get('transport', {})
    targets = render.get('targets', [])
    handles = [result.get(k) for k in ('old_handle','new_handle')]
    if (any(not isinstance(v,str) or not re.fullmatch(r'[1-9][0-9]*',v) or int(v)>2**63-1 for v in handles)
            or int(handles[1]) <= int(handles[0]) or stop.get('old_runtime_destroyed') is not True
            or type(stop.get('stop_ms')) is not int or not 0 <= stop['stop_ms'] < 10000
            or restored.get('state') != 'Ready' or restored.get('activity_visible') is not True or restored.get('xr_state') not in {'VISIBLE','FOCUSED'}
            or transport.get('state') != 'Idle' or transport.get('pending_cleanup') != []
            or render.get('mode') != 'diagnostic' or len(targets) != 1 or targets[0].get('role') != 'diagnostic'
            or targets[0].get('readback') is not True or targets[0].get('frames',0) < 3):
        raise ValueError('通常SDK runtime/UI復帰の証明が不正です')
    return True


def foot_result(snapshot, nonce, expected, apk_uid, termux_uid, package, *, case='foot'):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != case or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('foot描画検査失敗: ' + result.get('stage', 'unknown'))
    if result.get('result') != 'pass':
        return False
    session, window = result.get('session'), result.get('window')
    token = hashlib.sha256(nonce.encode()).hexdigest()[:16]
    if (not all(isinstance(v, str) and re.fullmatch(r'[1-9][0-9]*', v) and int(v) <= 2**63-1 for v in (session, window))
            or result.get('token') != token or result.get('credential_requests') != 1
            or type(apk_uid) is not int or type(termux_uid) is not int or apk_uid == termux_uid
            or result.get('consumer') != 'ImageReader' or result.get('synthetic_visibility') is not True
            or result.get('consumer_worker_stopped') is not True or result.get('cleanup_failed')):
        raise ValueError('foot認証/世代/consumerの証拠が不正です')
    waypipe_hash = package.get('dependencies', {}).get('waypipe', {}).get('sha256')
    if not isinstance(waypipe_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', waypipe_hash):
        raise ValueError('APK内Waypipeのhashがありません')

    def app_state(state):
        app, transport, render = (state.get(key, {}) for key in ('app', 'transport', 'render'))
        local, forward = app.get('local', {}), transport.get('app_forward', {})
        if (state.get('state') != 'Ready' or app.get('session') != session or app.get('nonce') != token
                or app.get('mode') != 'foot_fixture' or app.get('callback_clock') != 'target-driven'
                or app.get('ready') is not True or app.get('error') is not None or app.get('pool_rejections') != 0 or not _prepared_keyboard(app, package)
                or local.get('uid') != apk_uid or local.get('child_uid') != apk_uid or local.get('executable_sha256') != waypipe_hash
                or local.get('workers') != 1 or local.get('owned_groups') != 1 or local.get('error') is not None
                or transport.get('state') != 'Ready' or transport.get('session') != session or transport.get('credential_requests') != 1
                or forward.get('uid') != termux_uid or forward.get('nonce') != token or forward.get('mode') != 'foot_fixture'
                or forward.get('state') != 'running' or forward.get('error') is not None or forward.get('registered') is not True
                or forward.get('tools', {}).get('foot') != dict(version='1.28.0')
                or render.get('mode') != 'windows' or render.get('error') is not None or render.get('latest_images') != 1):
            raise ValueError('実foot/SSH/SHM/target駆動の証明が不正です')
        return app, render

    before, render = app_state(result.get('before_target', {}))
    if before.get('callbacks') != 0 or render.get('targets') != []:
        raise ValueError('targetなしでcallbackが進んでいます')
    rows = result.get('cycles', [])
    if len(rows) != 2:
        raise ValueError('Surface再生成の証明が必要です')
    callbacks, distinct, previous_generation = 0, 0, 0
    for generation, row in enumerate(rows, 1):
        app, render = app_state(row.get('runtime', {}))
        targets, windows, pixels = render.get('targets', []), app.get('windows', []), row.get('pixels', {})
        target_generation = row.get('target_generation')
        if (not isinstance(target_generation, str) or not re.fullmatch(r'[1-9][0-9]*', target_generation)
                or not previous_generation < int(target_generation) <= 2**63-1):
            raise ValueError('Surface世代が進んでいません')
        key = dict(session=session, window=window, scene='1', generation=target_generation)
        plans = row.get('runtime', {}).get('panels', [])
        if (row.get('generation') != generation or render.get('paused') is not False or render.get('contexts') != 1
                or len(targets) != 1 or targets[0].get('role') != 'windows'
                or targets[0].get('key') != key or len(plans) != 1 or plans[0].get('target') != key
                or plans[0].get('action') != 'create' or plans[0].get('applied') is not True or plans[0].get('error') is not None
                or targets[0].get('width') != (1024 if generation == 1 else 800) or targets[0].get('height') != (768 if generation == 1 else 600)
                or len(windows) != 1 or windows[0].get('window') != window or windows[0].get('life') != 'Mapped'
                or not windows[0].get('frame') or windows[0].get('frame') != targets[0].get('content_frame')
                or app.get('non_uniform') is not True or app.get('callbacks', 0) < callbacks+5
                or render.get('composition', {}).get('updates', 0) < 1 or render.get('composition', {}).get('textures', 0) < 1
                or pixels.get('distinct_images', 0) < distinct+4 or pixels.get('non_uniform_images', 0) < 1
                or pixels.get('sample_step') != 4 or len(set(pixels.get('hashes', []))) != pixels.get('distinct_images')
                or any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-f]{64}', h) for h in pixels.get('hashes', []))):
            raise ValueError('footの実pixel/更新/present世代の証明が不正です')
        callbacks, distinct, previous_generation = app['callbacks'], pixels['distinct_images'], int(target_generation)
    paused, paused_render = app_state(result.get('pause', {}))
    held, held_render = app_state(result.get('pause_held', {}))
    resumed, resumed_render = app_state(result.get('resumed', {}))
    if (paused_render.get('paused') is not True or held_render.get('paused') is not True
            or paused.get('callbacks') != held.get('callbacks') or resumed_render.get('paused') is not False
            or resumed.get('callbacks', 0) < held.get('callbacks', 0)+3):
        raise ValueError('非表示のcallback停止/表示復帰が不正です')
    scroll = result.get('scroll', {})
    scrolling, scroll_render = app_state(scroll.get('runtime', {}))
    pixels = scroll.get('pixels', {})
    if (type(scroll.get('observed_ms')) is not int or scroll['observed_ms'] < 4000
            or type(scroll.get('callbacks_before')) is not int or scrolling.get('callbacks',0) < scroll['callbacks_before']+8
            or pixels.get('lower_distinct_images',0) < 5 or pixels.get('lower_non_uniform_images',0) < 1
            or pixels.get('lower_distinct_images') != len(set(pixels.get('lower_hashes',[])))
            or any(not isinstance(h,str) or not re.fullmatch(r'[0-9a-f]{64}',h) for h in pixels.get('lower_hashes',[]))
            or len(scroll_render.get('targets',[])) != 1 or scroll_render['targets'][0].get('key') != key):
        raise ValueError('連続出力と画面下部の更新の証明が不正です')
    after = result.get('after_stop', {})
    app, transport, render = (after.get(key, {}) for key in ('app', 'transport', 'render'))
    forward, local = transport.get('app_forward', {}), app.get('local', {})
    finished = forward.get('remote', {}).get('finished', {})
    if (after.get('state') != 'Stopped' or app.get('state') != 'stopped' or app.get('directory_cleaned') is not True
            or app.get('error') is not None or app.get('input', {}).get('keyboard') is not False
            or any(app.get(key) != 0 for key in ('clients','objects','surfaces','pool_bytes','pixel_bytes','pool_count'))
            or local.get('workers') != 0 or local.get('owned_groups') != 0 or local.get('waited') is not True
            or forward.get('state') != 'stopped' or forward.get('error') is not None or forward.get('remote_cleaned') is not True
            or forward.get('forward_cancelled') is not True or finished.get('reason') != 'Stop' or finished.get('cleaned') is not True
            or finished.get('group_alive') is not False or finished.get('pipes_closed') is not True
            or transport.get('pending_cleanup') != [] or forward.get('pending_cleanup') != []
            or any(transport.get(key) != 0 for key in ('workers','tokio_threads','owned_connections'))
            or render.get('targets') != [] or any(render.get(key) != 0 for key in ('workers','contexts','latest_images','queued'))
            or any(render.get('composition', {}).get(key) != 0 for key in ('textures','texture_bytes'))
            or type(result.get('accept_ms')) is not int or not 0 <= result['accept_ms'] < 100
            or type(result.get('stop_ms')) is not int or not 0 <= result['stop_ms'] < 10000):
        raise ValueError('foot描画中の全owner/remote停止が不正です')
    if case=='resize-native':
        resize_rows=result.get('resize_cycles',[])
        if (result.get('synthetic_input') is not True or result.get('physical_input_verified') is not False or result.get('text_verified') is not False
                or result.get('stale_resize_rejected') is not True or [r.get('stage') for r in resize_rows]!=['first','lost','recreated','input']):
            raise ValueError('resize/Surface loss/古世代/合成入力のscopeが不正です')
        for i,row in enumerate(resize_rows):
            state=row.get('runtime',{});a,r=app_state(state)
            w=a.get('windows',[])
            if len(w)!=1: raise ValueError('resize対象が失われました')
            size=dict(width=800,height=500) if i==0 else dict(width=1280,height=720)
            resize=w[0].get('resize',{})
            if resize.get('requested_logical_size')!=size or w[0].get('window')!=window:
                raise ValueError('Surface loss中にresize要求が失われました')
            if i==1:
                if r.get('targets')!=[]: raise ValueError('旧Surfaceが残っています')
            else:
                targets=r.get('targets',[])
                expected_generation=rows[0]['target_generation'] if i==0 else rows[1]['target_generation']
                if (resize.get('phase')!='applied' or resize.get('committed_size')!=size or w[0].get('logical_size')!=size or len(targets)!=1
                        or targets[0].get('key')!=dict(session=session,window=window,scene='1',generation=expected_generation)
                        or targets[0].get('content_frame')!=w[0].get('frame')):
                    raise ValueError('新世代に新しいlogical画像がpresentされていません')
        before=result.get('input_before',{}); inputs=resize_rows[3]['runtime']['app'].get('input',{})
        if (inputs.get('focus_window')!=window or inputs.get('pressed')!=0 or inputs.get('delivered')!=before.get('delivered',-99)+6
                or inputs.get('pointer',{}).get('pressed')!=0 or inputs['pointer'].get('buttons')!=before.get('pointer',{}).get('buttons',-99)+2
                or inputs['pointer'].get('axis_frames')!=before.get('pointer',{}).get('axis_frames',-99)+2):
            raise ValueError('resize後のclick/修飾key/scroll/解除がありません')
    return instrumentation_result(snapshot, result, apk_uid)


def render_result(snapshot, nonce, expected, apk_uid):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != 'render' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('GPU検査失敗: ' + result.get('stage', 'unknown'))
    if result.get('result') != 'pass':
        return False
    if (result.get('consumer') != 'ImageReader' or result.get('synthetic_visibility') is not True
            or result.get('consumer_worker_stopped') is not True or result.get('cleanup_failed')
            or type(result.get('consumer_images')) is not int or result['consumer_images'] < 1):
        raise ValueError('実GPU consumerの証拠が不正です')
    pattern = result.get('pattern', {})
    targets = pattern.get('targets', [])
    if (pattern.get('error') is not None or pattern.get('contexts') != 1 or pattern.get('latest_images') != 0
            or pattern.get('composition', {}).get('pattern_readback_samples') != 4 or len(targets) != 1
            or targets[0].get('readback') is not True or targets[0].get('content_frame') is not None
            or targets[0].get('key') != dict(session='1',window='1',scene='1',generation='1')):
        raise ValueError('Smithay経由の四色patternのreadbackがありません')
    rows = result.get('cycles', [])
    if len(rows) != 36:
        raise ValueError('全18stageとSurface再生成が必要です')
    samples = 0
    prior = {}
    for index, row in enumerate(rows):
        generation, stage = index//18+1, index%18
        render = row.get('render', {})
        target = render.get('targets', [])
        composition = render.get('composition', {})
        count = 16 if stage >= 16 else (8 if generation == 1 else 6)
        if (row.get('cycle') != index+1 or row.get('generation') != generation or row.get('stage') != stage or row.get('frame') != str(100+index)
                or render.get('workers') != 1 or render.get('contexts') != 1 or render.get('latest_images') != 1
                or render.get('paused') is not False or render.get('error') is not None or len(target) != 1
                or target[0].get('key') != dict(session='1',window='1',scene='1',generation=str(generation))
                or target[0].get('content_frame') != str(100+index) or target[0].get('readback') is not True
                or target[0].get('width') != (64 if generation == 1 else 128) or target[0].get('height') != (64 if generation == 1 else 96)
                or any(not target[0].get(key) for key in ('vendor','renderer','version'))
                or len(target[0].get('rgba_bits', [])) != 4 or any(value < 8 for value in target[0]['rgba_bits'])
                or type(composition.get('readback_samples')) is not int or composition['readback_samples'] < samples+count
                or composition.get('textures') != (1 if stage < 16 else 3)
                or type(composition.get('texture_bytes')) is not int or not 0 < composition['texture_bytes'] <= 128*1024*1024
                or type(composition.get('uploads')) is not int or composition['uploads'] < 1):
            raise ValueError('transform/scale/alpha/clip/readback/一contextの証明が不正です')
        if stage == 17 and (composition.get('updates',0) < prior.get('updates',0)+3 or composition['uploads'] != prior['uploads']):
            raise ValueError('同寸法textureのupdate再利用がありません')
        samples, prior = composition['readback_samples'], composition
    after = result.get('after_stop', {})
    render = after.get('render', {})
    if (after.get('state') != 'Stopped' or render.get('targets') != []
            or any(render.get(key) != 0 for key in ('workers','contexts','latest_images','queued'))
            or any(render.get('composition',{}).get(key) != 0 for key in ('textures','texture_bytes'))
            or type(result.get('accept_ms')) is not int or not 0 <= result['accept_ms'] < 100
            or type(result.get('stop_ms')) is not int or not 0 <= result['stop_ms'] < 10000):
        raise ValueError('GL所有資源の停止が不正です')
    return instrumentation_result(snapshot, result, apk_uid)


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


def wayland_state_result(snapshot, nonce, expected, uid, *, instrumentation=False, case='wayland-state'):
    if snapshot.get("schema") != 1 or snapshot.get("build_info") != expected:
        return False
    result = snapshot.get("check", {})
    if result.get("case") != case or result.get("nonce") != nonce:
        return False
    if result.get("result") == "fail":
        raise ValueError("端末Wayland state caseが失敗しました")
    if result.get("result") != "pass":
        return False
    scenarios = [("lifecycle", "Lifecycle"), ("transforms", "Transforms"), ("frame_gates", "FrameGates"), ("tree", "Tree"),
                 ("bad_serial", "BadSerial"), ("stale_configure", "StaleConfigure"), ("premature_buffer", "PrematureBuffer"),
                 ("unconfigured_remap", "UnconfiguredRemap"), ("bad_scale", "BadScale"), ("positioner_overflow", "PositionerOverflow")]
    if case=='resize-wire': scenarios=[('logical_resize','LogicalResize')]
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
                or type(proof.get("immutable_checks")) is not int or proof["immutable_checks"] < (2 if scenario=='logical_resize' else 3)):
            raise ValueError("正常client・immutable snapshot・callbackの証明が不正です")
        ids = proof.get("window_ids", [])
        if not ids or any(not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value) or int(value) > 2**63 - 1 for value in ids):
            raise ValueError("Wayland window IDが不正です")
        if scenario == 'logical_resize':
            serials=proof.get('resize_serials',[])
            if (proof.get('logical_resize_flags')!=1023 or proof.get('checkpoints')!=11
                    or len(serials)!=3 or any(type(s) is not int or not 0<=s<2**32 for s in serials) or len(set(serials))!=3
                    or client.get('protocol',{}).get('configures')!=4):
                raise ValueError('resizeのcoalesce/ack/画像/timeout/unmapの検証がありません')
        elif scenario == "lifecycle":
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


def two_windows_result(snapshot, nonce, expected, apk_uid, termux_uid, package):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    proof = snapshot.get('check', {})
    if proof.get('case') != 'two-windows' or proof.get('nonce') != nonce:
        return False
    if proof.get('result') == 'fail':
        raise ValueError('二枚の実foot検査失敗: ' + proof.get('stage', 'unknown'))
    if proof.get('result') != 'pass':
        return False
    if (proof.get('consumer') != 'ImageReader' or proof.get('synthetic_visibility') is not True
            or proof.get('synthetic_input') is not True or proof.get('physical_input_verified') is not False
            or proof.get('text_verified') is not False or proof.get('sdk_layers_verified') is not False
            or proof.get('consumer_worker_stopped') is not True or proof.get('stale_resize_rejected') is not True):
        raise ValueError('二枚検査の可視性・合成入力scopeが不正です')
    session, first, second, third = (proof.get(key) for key in ('session', 'first', 'second', 'third'))
    if (any(not isinstance(v, str) or not re.fullmatch(r'[1-9][0-9]*', v) for v in (session, first, second, third))
            or len({first, second, third}) != 3 or proof.get('credential_requests') != 1
            or proof.get('token') != hashlib.sha256(nonce.encode()).hexdigest()[:16]
            or proof.get('running_observed_ms', 0) < 72000):
        raise ValueError('二枚検査のsession/window/接続証明が不正です')
    rows = proof.get('cycles', [])
    if [r.get('stage') for r in rows] != ['two', 'one_surface_missing', 'resized', 'third_hidden', 'switched', 'closed_one', 'long_running']:
        raise ValueError('二枚/Surface喪失/三枚目切替/個別closeの証明が必要です')
    previous_second_images = 0
    keys = []
    for index, row in enumerate(rows):
        runtime=row.get('runtime', {}); app=runtime.get('app', {}); render=runtime.get('render', {}); transport=runtime.get('transport', {})
        windows={w.get('window'):w for w in app.get('windows', [])}; targets=render.get('targets', [])
        logical={first,second,third} if index in (3,4) else {first,second}
        visible={second} if index==1 else ({third,second} if index==4 else {first,second})
        actual={t.get('key',{}).get('window') for t in targets}
        selected={second,third} if index==4 else {first,second}
        forward=transport.get('app_forward', {}); local=app.get('local', {})
        if (runtime.get('state')!='Ready' or runtime.get('error') is not None or runtime.get('xr_state')!='FOCUSED' or runtime.get('activity_visible') is not True
                or app.get('session')!=session or app.get('mode')!='foot_fixture' or app.get('callback_clock')!='target-driven' or app.get('error') is not None
                or set(windows)!=logical or any(w.get('life')!='Mapped' for w in windows.values()) or set(app.get('visible_windows',[]))!=selected
                or actual!=visible or len(targets)!=len(visible) or render.get('mode')!='windows' or render.get('error') is not None or render.get('contexts')!=1
                or local.get('uid')!=apk_uid or local.get('child_uid')!=apk_uid or local.get('executable_sha256')!=package['dependencies']['waypipe']['sha256']
                or transport.get('state')!='Ready' or transport.get('session')!=session or transport.get('credential_requests')!=1
                or forward.get('state')!='running' or forward.get('error') is not None or forward.get('uid')!=termux_uid
                or forward.get('tools',{}).get('foot')!=dict(version='1.28.0') or forward.get('nonce')!=proof['token']):
            raise ValueError('独立した二枚と非表示のlogical windowが一致しません')
        for target in targets:
            key=target.get('key',{}); window=key.get('window'); pixels=row.get('pixels',{}).get(window,{})
            plan=next((p for p in runtime.get('panels',[]) if p.get('target')==key),{})
            if (key.get('session')!=session or key.get('scene')!='1' or target.get('role')!='windows' or target.get('frames',0)<3
                    or target.get('content_frame')!=windows[window].get('frame') or target.get('width')!=1024 or target.get('height')!=768
                    or plan.get('action')!='create' or plan.get('applied') is not True or plan.get('error') is not None or plan.get('slot') not in (0,1)
                    or pixels.get('non_uniform_images',0)<1 or pixels.get('distinct_images',0)<3 or pixels.get('sample_step')!=4
                    or len(set(pixels.get('hashes',[])))!=pixels.get('distinct_images')):
                raise ValueError('二枚の世代・SDK適用ack・実pixel/present証明が不正です')
        if index==1:
            input_state=app.get('input',{})
            if (row['pixels'][second]['distinct_images']<previous_second_images+5 or input_state.get('focus_window')!=second
                    or input_state.get('delivered',0)<2 or input_state.get('pressed')!=0):
                raise ValueError('一枚のSurface喪失中に他方の描画・入力が進んでいません')
        if index==2 and (windows[first].get('logical_size')!=dict(width=1280,height=720) or windows[first].get('resize',{}).get('phase')!='applied'):
            raise ValueError('片側resize後の画像がありません')
        if index==4 and (app.get('input',{}).get('focus_window')!=third or app.get('input',{}).get('pressed')!=0 or app.get('input',{}).get('pointer',{}).get('pressed')!=0):
            raise ValueError('表示切替前の修飾キー解除と新windowへの入力がありません')
        previous_second_images=row['pixels'][second]['distinct_images']
        keys.append({t['key']['window']:t['key']['generation'] for t in targets})
    if not int(keys[2][first])>int(keys[0][first]) or not int(keys[5][first])>int(keys[2][first]):
        raise ValueError('表示枠再割当てで旧Surface世代を再利用しています')
    after=proof.get('after_stop',{}); app=after.get('app',{}); transport=after.get('transport',{}); render=after.get('render',{})
    forward=transport.get('app_forward',{}); finished=forward.get('remote',{}).get('finished',{}); local=app.get('local',{})
    if (after.get('state')!='Stopped' or app.get('state')!='stopped' or app.get('directory_cleaned') is not True or app.get('error') is not None
            or any(app.get(k)!=0 for k in ('clients','objects','surfaces','pool_bytes','pixel_bytes','pool_count'))
            or app.get('windows')!=[] or local.get('workers')!=0 or local.get('owned_groups')!=0 or local.get('waited') is not True
            or forward.get('state')!='stopped' or forward.get('error') is not None or forward.get('remote_cleaned') is not True or forward.get('forward_cancelled') is not True
            or finished.get('reason')!='Stop' or finished.get('cleaned') is not True or finished.get('group_alive') is not False or finished.get('pipes_closed') is not True
            or transport.get('pending_cleanup')!=[] or any(transport.get(k)!=0 for k in ('workers','tokio_threads','owned_connections'))
            or render.get('targets')!=[] or any(render.get(k)!=0 for k in ('workers','contexts','latest_images','queued'))
            or not 0<=proof.get('accept_ms',999)<100 or not 0<=proof.get('stop_ms',99999)<10000):
        raise ValueError('二枚の停止後にowner/socket/画像/FDの回収漏れがあります')
    return instrumentation_result(snapshot,proof,apk_uid)


def layout_native_result(snapshot, nonce, expected, apk_uid, termux_uid, package):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    proof=snapshot.get('check',{})
    if proof.get('case')!='layout' or proof.get('nonce')!=nonce:
        return False
    if proof.get('result')=='fail':
        raise ValueError('配置保存の実foot検査失敗: '+proof.get('stage','unknown'))
    if proof.get('result')!='pass':
        return False
    if (proof.get('consumer')!='ImageReader' or any(proof.get(k) is not True for k in ('synthetic_visibility','synthetic_input','synthetic_pose_ack','synthetic_scene','product_settings_untouched','consumer_worker_stopped'))
            or proof.get('physical_input_verified') is not False or proof.get('sdk_pose_verified') is not False or proof.get('cleanup_failed')):
        raise ValueError('配置検査のSDK/合成/製品設定scopeが不正です')
    first,second=proof.get('first_session'),proof.get('second_session')
    if (any(not isinstance(v,str) or not re.fullmatch(r'[1-9][0-9]*',v) for v in (first,second)) or not int(second)>int(first)):
        raise ValueError('設定復元に旧session IDを流用しています')
    rows=proof.get('cycles',[])
    if [r.get('stage') for r in rows]!=['before','pose','size','background_virtual','background_passthrough','background_virtual','keyboard','duplicate','restored','new_scene']:
        raise ValueError('配置/背景/配列/同app/再生成の証明が不足しています')
    def pose_near(a,b):
        try:
            p,q=a['position_m'],a['rotation_xyzw'];other,r=b['position_m'],b['rotation_xyzw']
            return (len(p)==len(other)==3 and len(q)==len(r)==4 and all(abs(x-y)<.0001 for x,y in zip(p,other))
                    and any(all(abs(x-sign*y)<.0001 for x,y in zip(q,r)) for sign in (1,-1)))
        except (KeyError,TypeError,ValueError): return False
    desired=proof.get('saved_pose',{})
    if not pose_near(desired,dict(position_m=[.2,1.65,1.9],rotation_xyzw=[0,math.sin(math.pi/24),0,math.cos(math.pi/24)])):
        raise ValueError('固定poseの移動証明がありません')
    settings=proof.get('settings',{});saved=settings.get('placements',[])
    if (settings.get('schema')!=1 or settings.get('background')!='virtual' or settings.get('keyboard_layout')!='jis' or len(saved)!=1
            or saved[0].get('slot')!=0 or not saved[0].get('app_id') or not pose_near(saved[0].get('pose',{}),desired)
            or saved[0].get('logical_size')!=dict(width=800,height=500)
            or abs(saved[0].get('physical_size_m',{}).get('width_m',0)-1.44)>.0001
            or abs(saved[0].get('physical_size_m',{}).get('height_m',0)-1.08)>.0001):
        raise ValueError('保存したschema/配置/寸法/背景/配列が不正です')
    def no_runtime_ids(value):
        if isinstance(value,dict): return not {'session','window','generation','entity'}.intersection(value) and all(no_runtime_ids(v) for v in value.values())
        if isinstance(value,list): return all(no_runtime_ids(v) for v in value)
        return True
    if not no_runtime_ids(settings) or len(json.dumps(settings).encode())>65536:
        raise ValueError('保存DTOに寿命付きIDまたは上限超過があります')
    target_keys=[]
    for i,row in enumerate(rows):
        state=row.get('runtime',{});app=state.get('app',{});transport=state.get('transport',{});render=state.get('render',{});workspace=state.get('workspace',{})
        windows=app.get('windows',[]);count=2 if i==7 else 1;session=first if i<8 else second
        local=app.get('local',{});forward=transport.get('app_forward',{})
        if (state.get('state')!='Ready' or state.get('error') is not None or state.get('activity_visible') is not True or state.get('xr_state')!='FOCUSED'
                or app.get('session')!=session or app.get('mode')!='foot_fixture' or app.get('error') is not None or app.get('callback_clock')!='target-driven'
                or len(windows)!=count or any(w.get('life')!='Mapped' for w in windows)
                or transport.get('session')!=session or transport.get('state')!='Ready' or transport.get('credential_requests')!=1
                or local.get('uid')!=apk_uid or local.get('child_uid')!=apk_uid or local.get('executable_sha256')!=package['dependencies']['waypipe']['sha256']
                or forward.get('state')!='running' or forward.get('error') is not None or forward.get('uid')!=termux_uid or forward.get('tools',{}).get('foot')!=dict(version='1.28.0')
                or render.get('mode')!='windows' or render.get('error') is not None or render.get('contexts')!=1 or len(render.get('targets',[]))!=count
                or len(workspace.get('windows',[]))!=count or workspace.get('storage',{}).get('persistent') is not True or workspace.get('storage',{}).get('error') is not None):
            raise ValueError('配置検査の実SSH/foot/保存先の証明が不正です')
        for target in render['targets']:
            key=target.get('key',{});window=next((w for w in windows if w.get('window')==key.get('window')),{});pixels=row.get('pixels',{}).get(key.get('window'),{})
            plan=next((p for p in state.get('panels',[]) if p.get('target')==key),{})
            if (key.get('session')!=session or key.get('scene')!=('2' if i==9 else '1') or target.get('frames',0)<3 or target.get('content_frame')!=window.get('frame')
                    or plan.get('action')!='create' or plan.get('applied') is not True or plan.get('error') is not None
                    or pixels.get('distinct_images',0)<3 or pixels.get('non_uniform_images',0)<1):
                raise ValueError('配置再生成後の実画像・Surface世代が不正です')
        first_window=workspace['windows'][0];placement=first_window.get('placement',{})
        if i in (1,2,3,4,5,6,8,9) and not pose_near(placement.get('pose',{}),desired):
            raise ValueError('modelの確定poseが失われました')
        if i==1 and (placement.get('applied_pose',{}).get('pose_command')!=placement.get('pose_command') or placement.get('applied_pose',{}).get('target')!=render['targets'][0]['key'] or not pose_near(placement.get('applied_pose',{}).get('pose',{}),desired)):
            raise ValueError('pose ackがtarget/希望値へ束縛されていません')
        if 3<=i<=5 and (workspace.get('background')!=('passthrough' if i==4 else 'virtual') or workspace.get('applied_background',{}).get('scene')!='1'
                or workspace.get('applied_background',{}).get('background_command')!=workspace.get('background_command') or workspace.get('applied_background',{}).get('background')!=workspace.get('background')):
            raise ValueError('背景の希望値とscene/command ackが不一致です')
        if i>=2 and (windows[0].get('logical_size')!=dict(width=800,height=500) or windows[0].get('resize',{}).get('phase')!='applied'):
            raise ValueError('配置変更後の新logical画像がありません')
        if i>=6:
            inputs=app.get('input',{})
            if (workspace.get('keyboard_layout')!='jis' or not _prepared_keyboard(app,package,'jis') or inputs.get('pressed')!=0):
                raise ValueError('JIS切替/修飾キー解除/保存配列が不正です')
        if i==7 and (not all(w.get('ambiguous') is True for w in workspace['windows']) or app.get('input',{}).get('delivered',0)<4):
            raise ValueError('同app二枚の既定復旧と入力がありません')
        if i in (8,9) and (workspace.get('background')!='virtual' or workspace.get('storage',{}).get('loaded') is not True or windows[0].get('logical_size')!=dict(width=800,height=500)
                or abs(windows[0].get('sizing',{}).get('physical_size_m',{}).get('width_m',0)-1.44)>.0001):
            raise ValueError('再起動で保存した背景/サイズが復元されていません')
        if i==9 and (placement.get('applied_pose') is not None or workspace.get('applied_background') is not None):
            raise ValueError('旧scene/世代の適用ackが残っています')
        target_keys.append(render['targets'][0]['key'])
    if not int(target_keys[9]['generation'])>int(target_keys[8]['generation']):
        raise ValueError('scene再生成で旧Surfaceを再利用しています')
    malformed=proof.get('corruption',[])
    if [r.get('stage') for r in malformed]!=['unknown_schema','invalid_pose','truncated']:
        raise ValueError('設定破損からの復旧検査がありません')
    for row in malformed:
        ws=row.get('workspace',{})
        if ws.get('background')!='passthrough' or ws.get('keyboard_layout')!='us' or ws.get('windows')!=[] or ws.get('storage',{}).get('error')!=('LAYOUT_SCHEMA' if row['stage']=='unknown_schema' else 'LAYOUT_INVALID'):
            raise ValueError('破損原因を保持して既定値へ復旧していません')
    stops=proof.get('stops',[])
    if [r.get('stage') for r in stops]!=['first','restored','unknown_schema','invalid_pose','truncated']:
        raise ValueError('再生成/破損検査の全runtime停止証明が不足しています')
    for row in stops:
        state=row.get('runtime',{});render=state.get('render',{});transport=state.get('transport',{});app=state.get('app')
        if (state.get('state')!='Stopped' or any(render.get(k)!=0 for k in ('workers','contexts','latest_images','queued')) or render.get('targets')!=[]
                or any(transport.get(k)!=0 for k in ('workers','tokio_threads','owned_connections')) or transport.get('pending_cleanup')!=[]
                or not 0<=row.get('accept_ms',999)<100 or not 0<=row.get('stop_ms',99999)<10000):
            raise ValueError('保存検査後にowner/画像/forwardが残っています')
        if app is not None and (app.get('directory_cleaned') is not True or app.get('error') is not None
                or any(app.get(k)!=0 for k in ('clients','objects','surfaces','pixel_bytes','pool_bytes','pool_count'))
                or transport.get('app_forward',{}).get('remote_cleaned') is not True):
            raise ValueError('配置検査のremote/Wayland回収が未完了です')
    return instrumentation_result(snapshot,proof,apk_uid)


def layout_sdk_result(snapshot, nonce, expected, apk_uid, termux_uid, package):
    if snapshot.get('schema')!=1 or snapshot.get('build_info')!=expected:
        return False
    p=snapshot.get('check',{})
    if p.get('case')!='layout' or p.get('nonce')!=nonce:
        return False
    if p.get('result')=='fail':
        raise ValueError('SDK配置検査失敗: '+p.get('stage','unknown'))
    if p.get('result')!='pass':
        return False
    if (p.get('consumer')!='SDK' or p.get('synthetic_visibility') is not False or p.get('synthetic_pose_ack') is not False or p.get('synthetic_scene') is not False
            or p.get('synthetic_stale_callback') is not True or p.get('synthetic_input') is not True or p.get('sdk_pose_verified') is not True or p.get('activity_recreated') is not True or p.get('source_destroyed') is not True
            or any(p.get(k) is not False for k in ('physical_input_verified','hmd_verified','text_verified','background_optical_verified'))
            or p.get('uid')!=apk_uid or type(p.get('pid')) is not int or p['pid']<=0 or p.get('product_settings_untouched') is not True):
        raise ValueError('SDK配置検査の可視性/実再生成/入力/保存scopeが不正です')
    rows=p.get('cycles',[])
    if [r.get('stage') for r in rows]!=['before','changed','two','recreated','restored']:
        raise ValueError('SDKの配置/二枚/Activity再生成/保存復元が不足しています')
    def pose_near(a,b):
        try:
            return (len(a['position_m'])==len(b['position_m'])==3 and len(a['rotation_xyzw'])==len(b['rotation_xyzw'])==4
                and all(abs(x-y)<.0001 for x,y in zip(a['position_m'],b['position_m']))
                and any(all(abs(x-sign*y)<.0001 for x,y in zip(a['rotation_xyzw'],b['rotation_xyzw'])) for sign in (1,-1)))
        except (KeyError,TypeError,ValueError):return False
    desired=p.get('saved_pose',{})
    if not pose_near(desired,dict(position_m=[.2,1.5,1.75],rotation_xyzw=[0,math.sin(math.pi/24),0,math.cos(math.pi/24)])):
        raise ValueError('SDKで固定の配置変更を確認できません')
    layer_window=p.get('layer_window',{})
    if (type(layer_window.get('start_wall_ms')) is not int or type(layer_window.get('end_wall_ms')) is not int or layer_window['end_wall_ms']-layer_window['start_wall_ms']<2000):
        raise ValueError('SDK二枚を維持したlayer計測の時間窓がありません')
    original_session=None;old_scene=None;old_keys={}
    for index,row in enumerate(rows):
        s=row.get('runtime',{});a=s.get('app',{});t=s.get('transport',{});r=s.get('render',{});ws=s.get('workspace',{});adapter=row.get('adapter',{})
        count=2 if index in (2,3) else 1;windows=a.get('windows',[]);targets=r.get('targets',[])
        if (s.get('state')!='Ready' or s.get('activity_visible') is not True or s.get('xr_state')!='FOCUSED' or s.get('error') is not None
                or a.get('mode')!='foot' or a.get('callback_clock')!='target-driven' or a.get('error') is not None or len(windows)!=count or any(w.get('life')!='Mapped' for w in windows)
                or a.get('local',{}).get('uid')!=apk_uid or a.get('local',{}).get('child_uid')!=apk_uid or a.get('local',{}).get('executable_sha256')!=package['dependencies']['waypipe']['sha256']
                or t.get('state')!='Ready' or t.get('session')!=a.get('session') or t.get('app_forward',{}).get('uid')!=termux_uid or t.get('app_forward',{}).get('state')!='running'
                or t.get('app_forward',{}).get('tools',{}).get('foot')!=dict(version='1.28.0') or r.get('error') is not None or r.get('mode')!='windows' or len(targets)!=count
                or adapter.get('bindings')!=count or adapter.get('listeners')!=count or len(ws.get('windows',[]))!=count
                or ws.get('storage',{}).get('persistent') is not True or ws.get('storage',{}).get('error') is not None):
            raise ValueError('通常Host/SDK二枚/SSH/footの証明が不正です')
        for target in targets:
            key=target.get('key',{});w=next((v for v in windows if v.get('window')==key.get('window')),{});placement=next((v.get('placement',{}) for v in ws['windows'] if v.get('window')==key.get('window')),{})
            ack=placement.get('applied_pose') or {};plan=next((v for v in s.get('panels',[]) if v.get('target')==key),{})
            if (key.get('session')!=a.get('session') or key.get('scene')!=s.get('scene') or target.get('frames',0)<3 or target.get('content_frame')!=w.get('frame')
                    or plan.get('action')!='create' or plan.get('applied') is not True or ack.get('target')!=key or ack.get('pose_command')!=placement.get('pose_command') or not pose_near(ack.get('pose',{}),placement.get('pose',{}))
                    or w.get('sizing',{}).get('applied_physical_size_m')!=w.get('sizing',{}).get('physical_size_m')):
                raise ValueError('実SDK geometryの適用ackと描画targetが不一致です')
        bg=ws.get('applied_background') or {}
        if bg.get('scene')!=s.get('scene') or bg.get('background')!=ws.get('background') or bg.get('background_command')!=ws.get('background_command'):
            raise ValueError('背景が新しいsceneへ再適用されていません')
        if index==0:original_session=a['session']
        elif index<4 and a['session']!=original_session:
            raise ValueError('背景/Activity再生成でSSH接続を作り直しています')
        if index in (1,4):
            if (ws.get('background')!='virtual' or not _prepared_keyboard(a,package,'jis') or not pose_near(ws['windows'][0]['placement']['pose'],desired)
                    or windows[0].get('logical_size')!=dict(width=800,height=500) or abs(windows[0].get('sizing',{}).get('physical_size_m',{}).get('width_m',0)-1.44)>.0001):
                raise ValueError('SDKの配置/サイズ/配列/背景変更または復元がありません')
        if index==1 and (a.get('input',{}).get('delivered',0)<4 or a['input'].get('pressed')!=0):
            raise ValueError('移動後のSDK ray→key入力を確認できません')
        if index==2:
            if a.get('input',{}).get('delivered',0)<8 or not all(v.get('ambiguous') is True for v in ws['windows']):
                raise ValueError('SDK二枚のfocus/入力/同app復旧がありません')
            old_scene=int(s['scene']);old_keys={v['key']['window']:v['key'] for v in targets}
        if index==3 and (int(s['scene'])<=old_scene or any(int(v['key']['generation'])<=int(old_keys[v['key']['window']]['generation']) for v in targets)):
            raise ValueError('実Activity再生成後の新scene/Surface世代がありません')
        if index==4 and (int(a['session'])<=int(original_session) or ws.get('storage',{}).get('loaded') is not True):
            raise ValueError('保存設定を新しい接続へ復元していません')
    restored=p.get('product_restored',{});render=restored.get('render',{});transport=restored.get('transport',{})
    if (restored.get('state')!='Ready' or restored.get('activity_visible') is not True or restored.get('xr_state')!='FOCUSED'
            or transport.get('state')!='Idle' or transport.get('pending_cleanup')!=[] or render.get('mode')!='diagnostic'
            or len(render.get('targets',[]))!=1 or render['targets'][0].get('frames',0)<3 or render['targets'][0].get('role')!='diagnostic'
            or not int(p.get('new_handle','0'))>int(p.get('last_handle','0'))>=int(p.get('fixture_handle','0'))>int(p.get('original_handle','0'))>0):
        raise ValueError('検査用設定とownerを回収して通常UIへ復帰していません')
    return True


def compositor_layer_stats(proof, logs, apk_uid):
    """APK UIDで採取したepoch logから、SDK二枚の時間窓とPIDが一致するLCntだけを返す。"""
    window=proof.get('layer_window',{});start=window.get('start_wall_ms');end=window.get('end_wall_ms');pid=proof.get('pid')
    report=dict(status='not_observed',scope='app-uid-and-pid-two-sdk-panels',uid=apk_uid,pid=pid,samples=[])
    if proof.get('uid')!=apk_uid or type(pid) is not int or type(start) is not int or type(end) is not int or not 2000<=end-start<=60000:
        return report
    pattern=r'^\s*(\d+\.\d{3,9})\s+(\d+)\s+\d+\s+[VDIWEF]\s+(?:VrApi|crcs)\s*:.*?(?<![A-Za-z])LCnt=(\d+)'
    for stamp,source,count in re.findall(pattern,logs,re.M):
        seconds,fraction=stamp.split('.');milliseconds=int(seconds)*1000+int(fraction.ljust(3,'0')[:3])
        if int(source)==pid and start<=milliseconds<=end:
            report['samples'].append(dict(wall_ms=milliseconds,count=int(count)))
    if report['samples']:
        counts=[r['count'] for r in report['samples']];report.update(status='observed',minimum=min(counts),maximum=max(counts))
    return report
