"""通常Linuxの本文なし観測。Androidの完了表示と独立にfull target/回収を照合する。"""
from sdl_lease_capture import need, decimal, hexadecimal
from gtk_interaction_evidence import key, resources

TRACE_FIELDS={'remote_nonce','main_target','dialog_targets','targets','input_baseline','input_last','input_delta',
    'pointer_baseline','pointer_last','pointer_delta','first_content_frame','last_content_frame',
    'frame_progress','dialog_parent_verified','main_closed','targets_retired','cleanup_complete','cleanup','observations'}
OBSERVATIONS={'main_mapped','frame_progress','input_delivered','dialog_mapped','main_closed','all_targets_retired','cleanup_complete'}
CHECK_FIELDS={'case','nonce','scope','result','phase','reason','runtime','session','scene','connect_request_id','inspect_request_id','launch_request_id',
    'catalog_verified','launched','stop_requested','physical_input_verified','visual_input_verified','ime_verified','trace'}


def number(value):return type(value) is int and 0<=value<=2**63-1


def binding(value,digest):
    need(type(value) is dict and value.get('schema')==1 and decimal(value.get('sequence')) and
        type(value.get('process_id')) is int and value['process_id']>0,'LINUX_SNAPSHOT_INVALID')
    build=value.get('build_info',{});envelope=value.get('runtime',{});runtime=envelope.get('runtime',{})
    need(type(build) is dict and type(build.get('schema')) is int and build['schema']==1 and
        build.get('source_digest')==digest and hexadecimal(digest,64) and hexadecimal(build.get('source_commit'),40) and
        type(build.get('source_dirty')) is bool and build.get('profile')=='debug' and build.get('target')=='aarch64-linux-android' and
        type(build.get('apk_version_code')) is int and build['apk_version_code']>0,'SOURCE_BUILD_MISMATCH')
    need(type(envelope) is dict and envelope.get('schema')==1 and type(envelope.get('code')) is int and envelope['code']==0 and
        decimal(envelope.get('handle')) and type(runtime) is dict and runtime.get('schema')==1 and decimal(runtime.get('scene')),'LINUX_RUNTIME_INVALID')
    return dict(process_id=value['process_id'],handle=envelope['handle'],scene=runtime['scene'],source_digest=digest,
        source_commit=build['source_commit'],source_dirty=build['source_dirty'],apk_version=build['apk_version_code'])


def trace(value,session,scene):
    need(type(value) is dict and set(value)==TRACE_FIELDS,'LINUX_TRACE_SCHEMA')
    need(value['remote_nonce'] is None or hexadecimal(value['remote_nonce'],16),'LINUX_NONCE_INVALID')
    targets=value['targets'];dialogs=value['dialog_targets'];main=value['main_target']
    need(type(targets) is list and len(targets)<=32 and type(dialogs) is list and len(dialogs)<=31,'LINUX_TARGET_LIMIT')
    for target in targets:
        key(target);need(target['session']==session and target['scene']==scene,'LINUX_TARGET_SCOPE')
    need(len({tuple(t[k] for k in ('session','window','scene','generation')) for t in targets})==len(targets),'LINUX_TARGET_DUPLICATE')
    if main is not None:key(main);need(main in targets,'LINUX_MAIN_TARGET')
    for dialog in dialogs:
        need(type(dialog) is dict and set(dialog)=={'target','parent'} and dialog['target'] in targets and main is not None and
            dialog['parent']==main['window'] and dialog['target']['window']!=main['window'],'LINUX_DIALOG_PARENT')
    need(len({tuple(d['target'][k] for k in ('session','window','scene','generation')) for d in dialogs})==len(dialogs),'LINUX_TARGET_DUPLICATE')
    for stem in ('input','pointer'):
        first,last,delta=(value[stem+'_'+suffix] for suffix in ('baseline','last','delta'))
        need((first is None or number(first)) and number(last) and number(delta) and
            (delta==0 if first is None else last>=first and delta==last-first),'LINUX_INPUT_COUNTER')
    first=value['first_content_frame'];last=value['last_content_frame']
    need((first is None or number(first) and first>0) and number(last) and (first is None or last>=first),'LINUX_CONTENT_COUNTER')
    for field in ('frame_progress','dialog_parent_verified','main_closed','targets_retired','cleanup_complete'):
        need(type(value[field]) is bool,'LINUX_TRACE_FLAG')
    need(value['frame_progress']==(first is not None and last>first) and value['dialog_parent_verified']==bool(dialogs),'LINUX_TRACE_INCONSISTENT')
    observations=value['observations']
    need(type(observations) is list and len(set(observations))==len(observations) and set(observations)<=OBSERVATIONS,'LINUX_OBSERVATIONS')
    cleanup=value['cleanup']
    need(type(cleanup) is dict,'LINUX_CLEANUP_SCHEMA')
    if cleanup:
        need(set(cleanup)=={'local_complete','remote_complete','connection_closed','desktop_pending','sdk_bindings','render_targets','panels','board_occupied'} and
            all(type(cleanup[k]) is bool for k in ('local_complete','remote_complete','connection_closed','desktop_pending')) and
            all(number(cleanup[k]) for k in ('sdk_bindings','render_targets','panels','board_occupied')),'LINUX_CLEANUP_SCHEMA')
    if value['cleanup_complete']:
        need(value['main_closed'] and value['targets_retired'] and cleanup and all(cleanup[k] for k in ('local_complete','remote_complete','connection_closed')) and
            not cleanup['desktop_pending'] and all(cleanup[k]==0 for k in ('sdk_bindings','render_targets','panels','board_occupied')),'LINUX_CLEANUP_INCONSISTENT')
    return value


def check(value,nonce,owner,previous=None):
    row=value.get('check')
    if type(row) is not dict or row.get('case')!='normal-linux-gtk' or row.get('nonce')!=nonce:return None
    need(set(row)==CHECK_FIELDS and row['scope']=='production_linux_manual_observation' and
        row['result'] in {'running','completed','failed','cancelled','incomplete'} and
        row['phase'] in {'START','CONNECTING','INSPECTING','LAUNCHING','OBSERVING','STOPPING','COMPLETE','PENDING_CLEANUP'},'LINUX_CHECK_SCHEMA')
    need(row['reason'] is None or type(row['reason']) is str and __import__('re').fullmatch('[A-Z][A-Z0-9_]{0,63}',row['reason']),'LINUX_CHECK_REASON')
    need(all(type(row[k]) is bool for k in ('catalog_verified','launched','stop_requested')) and
        all(row[k] is False for k in ('physical_input_verified','visual_input_verified','ime_verified')),'LINUX_CHECK_FLAG')
    for field in ('runtime','session','scene','connect_request_id','inspect_request_id','launch_request_id'):
        need(row[field] is None or decimal(row[field]),'LINUX_CHECK_BINDING')
        if previous is not None and previous[field] is not None:need(row[field]==previous[field],'LINUX_CHECK_CHANGED')
    if row['runtime'] is not None:need(row['runtime']==owner['handle'] and row['scene']==owner['scene'],'LINUX_CHECK_BINDING')
    if row['trace'] is not None:
        need(row['launched'] and row['catalog_verified'] and all(row[k] is not None for k in ('session','scene','connect_request_id','inspect_request_id','launch_request_id')),'LINUX_LAUNCH_BINDING')
        trace(row['trace'],row['session'],row['scene'])
        if previous is not None and previous['trace'] is not None:
            before=previous['trace'];after=row['trace']
            if before['remote_nonce'] is not None:need(before['remote_nonce']==after['remote_nonce'],'LINUX_NONCE_CHANGED')
            need(all(t in after['targets'] for t in before['targets']) and set(before['observations'])<=set(after['observations']),'LINUX_TRACE_REGRESSED')
            for field in ('input_last','pointer_last','last_content_frame'):
                need(after[field]>=before[field],'LINUX_TRACE_REGRESSED')
    if row['result']=='completed':
        need(row['phase']=='COMPLETE' and row['reason'] is None and not row['stop_requested'] and row['trace'] is not None and
            row['trace']['cleanup_complete'] and set(row['trace']['observations'])==OBSERVATIONS,'LINUX_FALSE_COMPLETION')
    return row


def sample(value):
    runtime=value['runtime']['runtime'];transport=runtime['transport'];app=runtime.get('app') or {};desktop=transport.get('desktop') or {}
    nonce=desktop.get('nonce');main_id='io.github.tinmachine.eidolon.linuxdesktop.r'+nonce*2 if hexadecimal(nonce,16) else None
    windows=[]
    for window in app.get('windows',[]):
        if window.get('life')!='Mapped':continue
        need(decimal(window.get('window')) and (window.get('parent') is None or decimal(window['parent'])),'LINUX_WINDOW_SCHEMA')
        app_id=window.get('app_id');role='main' if main_id is not None and app_id==main_id else 'dialog' if main_id is not None and app_id==main_id+'.dialog' else 'other'
        windows.append(dict(window=window['window'],parent=window.get('parent'),role=role))
    targets=[]
    for target in runtime['render']['targets']:
        need(number(target['frames']) and (target.get('content_frame') is None or decimal(target['content_frame'])),'LINUX_FRAME_SCHEMA')
        targets.append(dict(key=key(target['key']),frames=target['frames'],content_frame=target.get('content_frame')))
    remote=desktop.get('remote') or {};local=app.get('local') or {}
    startup_clean=app.get('startup_failed') is True and app.get('startup_cleanup_complete') is True
    local_complete=app.get('state')=='stopped' and app.get('directory_cleaned') is True and local.get('workers')==0 and local.get('owned_groups')==0 and (local.get('waited') is True or startup_clean) and all(app.get(k)==0 for k in ('clients','objects','surfaces','pool_bytes','pool_count','pixel_bytes'))
    remote_proof={name:remote.get(name) for name in ('reason','process_complete','pipes_eof','remote_cleaned','receipt_written','protocol_valid','terminal_event','channel_closed','eof','exit_status','exit_signal')}
    forward_proof={name:desktop.get(name) for name in ('finished','channel_complete','router_drained','journal_pending','forward_requested','forward_cancelled')}
    remote_complete=all(desktop.get(k) is True for k in ('finished','channel_complete','router_drained')) and desktop.get('journal_pending') is False and all(remote.get(k) is True for k in
        ('process_complete','pipes_eof','remote_cleaned','receipt_written','protocol_valid','terminal_event','channel_closed','eof')) and type(remote.get('exit_status')) is int and remote['exit_status']==0 and remote.get('exit_signal') is False and (desktop.get('forward_requested') is False or desktop.get('forward_cancelled') is True)
    delivered=(app.get('input') or {}).get('delivered',0);need(number(delivered),'LINUX_INPUT_COUNTER')
    return dict(sequence=value['sequence'],session=transport.get('session'),profile=transport.get('profile_id'),nonce=nonce,
        request_id=desktop.get('request_id'),desktop_session=desktop.get('session'),app_session=app.get('session'),app_mode=app.get('mode'),started=desktop.get('started') is True,
        start_requested=desktop.get('start_requested'),startup_cleanup_complete=startup_clean,
        state=transport['state'],owned_connections=transport['owned_connections'],desktop_pending=transport['desktop_pending'],error=desktop.get('error') is not None,cancelled=desktop.get('cancelled') is True,
        termux_pending=len(transport['pending_cleanup']),gui_verified=app.get('remote_gui_verified') is True,windows=windows,targets=targets,
        delivered=delivered,local_complete=local_complete,remote_complete=remote_complete,remote_proof=remote_proof,forward_proof=forward_proof,resources=resources(value))


def empty(sample):
    resource=sample['resources'];board=resource['board']
    return not (sample['windows'] or sample['targets'] or resource['targets'] or resource['panels'] or resource['sdk_bindings'] or
        any(resource['owners'].values()) or board['entries'] or board['cleanup_required'] or board['unassigned'])


def reclaimed(sample,owned,session,nonce,request):
    no_start=not owned and sample['startup_cleanup_complete'] and sample['start_requested'] is False and sample['request_id'] is None
    if not (decimal(session) and hexadecimal(nonce,16) and decimal(request) and sample['session']==sample['desktop_session']==sample['app_session']==session and
        sample['profile']=='remote-linux' and sample['app_mode']=='linux_desktop' and sample['nonce']==nonce and (sample['request_id']==request or no_start)):return False
    if sample['state']!='Disconnected' or sample['owned_connections'] or sample['desktop_pending'] or sample['termux_pending'] or not sample['local_complete'] or not sample['remote_complete'] or not empty(sample):return False
    resource=sample['resources']
    return no_start or bool(owned) and all(any(r['key']==target and r['runtime']==resource['handle'] and r['sdk_disposed'] and r['sdk_binding_released'] and not r['disposal_failed'] for r in resource['retired']) for target in owned)


def summarize(samples,value,session,scene,request):
    row=trace(value,session,scene);main=row['main_target'];owned=row['targets']
    main_seen=False;dialog_seen=False;content=set();delivered=False
    for observation in samples:
        if observation['session']!=session:continue
        need(observation['profile']=='remote-linux','LINUX_PROFILE_CHANGED')
        if observation['nonce'] is not None:need(observation['nonce']==row['remote_nonce'],'LINUX_NONCE_CHANGED')
        if observation['request_id'] is not None:need(observation['request_id']==request,'LINUX_REQUEST_CHANGED')
        need(not any(w['role']=='other' for w in observation['windows']),'LINUX_FOREIGN_WINDOW')
        if main is None or not observation['gui_verified'] or not observation['started']:continue
        need(observation['app_session']==observation['desktop_session']==session and observation['app_mode']=='linux_desktop','LINUX_APP_CHANGED')
        for target in observation['targets']:
            if target['key'] not in owned:continue
            present=any(s['key']==target['key'] and s['listener_owned'] and not s['retiring'] and not s['sdk_disposed'] and not s['disposal_failed'] for s in observation['resources']['targets'])
            if not present or target['frames']<1 or target['content_frame'] is None:continue
            window=next((w for w in observation['windows'] if w['window']==target['key']['window']),None)
            if window is None:continue
            if target['key']['window']==main['window'] and window['role']=='main' and window['parent'] is None:
                main_seen=True;content.add(int(target['content_frame']))
            if target['key'] in [d['target'] for d in row['dialog_targets']] and window['role']=='dialog' and window['parent']==main['window']:dialog_seen=True
        delivered |= row['input_baseline'] is not None and observation['delivered']>row['input_baseline']
    cleaned=bool(samples) and reclaimed(samples[-1],owned,session,row['remote_nonce'],request)
    observed=(main_seen and dialog_seen and len(content)>=2 and delivered and row['input_delta']>0 and row['frame_progress'] and
        row['dialog_parent_verified'] and row['main_closed'] and row['targets_retired'] and row['cleanup_complete'] and cleaned and
        not samples[-1]['error'] and not samples[-1]['cancelled'] and samples[-1]['remote_proof']['reason']=='process_exited' and set(row['observations'])==OBSERVATIONS)
    return dict(status='observed' if observed else 'not_verified',main_sdk_observed=main_seen,dialog_sdk_observed=dialog_seen,
        content_progress=len(content)>=2,input_delivery_observed=delivered,all_resources_reclaimed=cleaned,
        physical_input='not_verified',visual_input='not_verified',close_action='not_verified',hmd='not_run',ime='not_run')
