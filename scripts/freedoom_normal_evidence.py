"""通常GUI固定caseの本文なしreceipt/全SDK資源の独立判定。"""
import importlib.machinery
import importlib.util
import re
from native_build import ROOT
from sdl_lease_capture import need,decimal,hexadecimal,identity
from gtk_interaction_evidence import resources,baseline_resources

loader=importlib.machinery.SourceFileLoader('freedoom_normal_sdk',str(ROOT/'scripts/quest-run-sdl-probe'))
spec=importlib.util.spec_from_loader(loader.name,loader);sdk=importlib.util.module_from_spec(spec);loader.exec_module(sdk)
CASES=('keys','close')
STEPS={'keys':[['mapped','focused','escape_delivered','up_delivered','enter_delivered','confirm_queued','retired']],
       'close':[['mapped','focused','first_close_queued','first_close_pending','second_close_queued','retired'],
                ['mapped','focused','first_close_queued','first_close_retired','retired']]}
FIELDS={'case','nonce','result','phase','reason','cleanup_reason','scope','physical_input_verified','ime_verified','host_physical_filter_verified',
    'runtime','session','scene','foot_windows','revision','request_id','launch_id','app_id','target','input_epoch','first_close_pending','natural_exit','cleanup_complete','session_stop_sent','focus_progress','close_progress','commands','steps'}
PHASES={'START','CATALOG','LAUNCH_REQUESTED','WAIT_WINDOW','WAIT_FOCUS','WAIT_KEY','FIRST_CLOSE','RETIRING','SESSION_STOP','COMPLETE','PENDING_CLEANUP'}


def number(value):return type(value) is int and 0<=value<=2**63-1
def full_key(value):return type(value) is dict and set(value)=={'session','window','scene','generation'} and all(decimal(v) for v in value.values())
def app_id(nonce,launch):
    need(hexadecimal(nonce,16) and decimal(launch),'GUI_BINDING_INVALID');return 'io.github.tinmachine.eidolon.gui.g-'+nonce+'-'+launch


def progress(value,close=False):
    if value is None:return
    fields=({'schema','state','reason','base_epoch','observed_epoch','close_requests','retirement_bound','all_acknowledged','elapsed_ms'} if close else
        {'schema','phase','state','reason','elapsed_ms','floor_keys','floor_buttons','expected_keys','expected_buttons','observed_keys','observed_buttons','keys_pressed','buttons_pressed'})
    reasons=({'WAIT_CLOSE','CLOSE_COMPLETE','CLOSE_CLOCK_OR_REQUEST','CLOSE_EPOCH_REGRESSED','CLOSE_RETIREMENT_REGRESSED','CLOSE_EPOCH_EXCESS','CLOSE_ACK_REJECTED','CLOSE_PROGRESS_TIMEOUT'} if close else
        {'WAIT_COUNTERS','COUNTERS_COMPLETE','CLOCK_REGRESSED','COUNTER_REGRESSED','COUNTER_EXCESS','UNEXPECTED_HELD','PROGRESS_TIMEOUT'})
    need(type(value) is dict and set(value)==fields and type(value['schema']) is int and value['schema']==1 and value['state'] in {'waiting','complete','rejected'} and value['reason'] in reasons and
        number(value['elapsed_ms']) and value['elapsed_ms']<=6000,'GUI_PROGRESS_SCHEMA')
    if close:
        need(all(number(value[k]) for k in ('base_epoch','observed_epoch','close_requests')) and value['close_requests']<=2 and
            all(type(value[k]) is bool for k in ('retirement_bound','all_acknowledged')),'GUI_PROGRESS_SCHEMA')
    else:need(value['phase'] in {'','WAIT_FOCUS'} and all(number(value[k]) for k in fields-{'schema','phase','state','reason'}),'GUI_PROGRESS_SCHEMA')


def android(value,nonce,case,binding,baseline,revision,scene):
    row=value.get('check')
    if type(row) is not dict or row.get('case')!='freedoom-'+case or row.get('nonce')!=nonce:return None
    need(set(row)==FIELDS and row['scope']=='production_registered_target_bound_synthetic_native' and
        row['result'] in {'running','completed','failed','cancelled'} and row['phase'] in PHASES and
        all(row[k] is False for k in ('physical_input_verified','ime_verified','host_physical_filter_verified')),'GUI_ANDROID_SCHEMA')
    need(all(row[k] is None or type(row[k]) is str and re.fullmatch('FREEDOOM_[A-Z_]+',row[k]) for k in ('reason','cleanup_reason')) and
        all(type(row[k]) is bool for k in ('first_close_pending','natural_exit','cleanup_complete','session_stop_sent')) and number(row['input_epoch']),'GUI_ANDROID_SCHEMA')
    progress(row['focus_progress']);progress(row['close_progress'],True)
    need(all(row[k] is None or decimal(row[k]) for k in ('runtime','session','scene','request_id','launch_id')) and
        type(row['foot_windows']) is list and len(row['foot_windows'])<=1 and all(decimal(v) for v in row['foot_windows']) and
        (row['revision'] is None or row['revision']==revision) and (row['target'] is None or full_key(row['target'])),'GUI_ANDROID_BINDING')
    if row['runtime'] is not None:
        need(row['runtime']==binding['handle'] and row['session']==binding['session'] and row['scene']==scene and set(row['foot_windows'])==set(baseline['mapped']),'GUI_ANDROID_BINDING')
    if row['launch_id'] is not None:need(row['app_id']==app_id(binding['nonce'],row['launch_id']) and row['request_id'] is not None,'GUI_ANDROID_BINDING')
    else:need(row['app_id'] is None,'GUI_ANDROID_BINDING')
    if row['target'] is not None:need(row['target']['session']==binding['session'] and row['target']['scene']==scene and row['target']['window'] not in baseline['mapped'],'GUI_ANDROID_BINDING')
    need(type(row['commands']) is list and len(row['commands'])<=16 and type(row['steps']) is list and len(row['steps'])<=16,'GUI_ANDROID_SCHEMA')
    seen=set()
    for command in row['commands']:
        need(type(command) is dict and set(command)=={'request_id','kind','completion'} and decimal(command['request_id']) and command['request_id'] not in seen and
            command['kind'] in {'pointer','key','close_window_target'} and command['completion'] in {None,'ACCEPTED','NOT_READY','STALE','INVALID_ARGUMENT','BUSY','JNI_FAILURE','FAILED'},'GUI_ANDROID_COMMAND')
        seen.add(command['request_id'])
    for step in row['steps']:
        need(type(step) is dict and set(step)=={'stage','target','input_epoch','delivered'} and step['target']==row['target'] and number(step['input_epoch']) and number(step['delivered']),'GUI_ANDROID_STEP')
    stages=[step['stage'] for step in row['steps']]
    need(any(stages==allowed[:len(stages)] for allowed in STEPS[case]),'GUI_ANDROID_SEQUENCE')
    if row['result']=='completed':
        need(row['phase']=='COMPLETE' and row['natural_exit'] and row['cleanup_complete'] and not row['session_stop_sent'] and row['reason'] is None and
             any(stages==allowed for allowed in STEPS[case]),'GUI_ANDROID_SEQUENCE')
        need(row['focus_progress'] is not None and row['focus_progress']['state']=='complete' and row['focus_progress']['reason']=='COUNTERS_COMPLETE' and
            row['focus_progress']['keys_pressed']==row['focus_progress']['buttons_pressed']==0,'GUI_PROGRESS_SCHEMA')
        if case=='close':
            gate=row['close_progress'];count=2 if row['first_close_pending'] else 1
            need(gate is not None and gate['state']=='complete' and gate['reason']=='CLOSE_COMPLETE' and gate['base_epoch']==row['input_epoch'] and gate['close_requests']==count and
                gate['observed_epoch']==row['steps'][-1]['input_epoch'] and row['input_epoch']+count<=gate['observed_epoch']<=row['input_epoch']+count+1 and
                gate['all_acknowledged'] and gate['retirement_bound'],'GUI_PROGRESS_SCHEMA')
    return row


def receipt(runtime,android_row,binding):
    if android_row is None or android_row['request_id'] is None or android_row['launch_id'] is None:return None
    forward=runtime.get('transport',{}).get('app_forward') or {};remote=forward.get('remote') or {}
    candidates=list(remote.get('gui_receipts') or [])+list((remote.get('finished') or {}).get('gui_receipts') or [])
    matches=[r for r in candidates if r.get('request_id')==android_row['request_id']]
    if not matches:return None
    need(all(r==matches[0] for r in matches),'GUI_RECEIPT_AMBIGUOUS');row=matches[0]
    fields={'schema','nonce','uid','session','request_id','terminal_id','app_id','worker','child','child_returncode','worker_returncode','reason',
        'cleanup_complete','pipes_closed','assets_runtime_unchanged','term_signals','kill_signals','reaped','open_pidfds'}
    need(type(row) is dict and set(row)==fields and type(row['schema']) is int and row['schema']==1 and row['nonce']==binding['nonce'] and row['session']==binding['session'] and
        type(row['uid']) is int and row['uid']==10158 and row['terminal_id']==android_row['launch_id'] and row['app_id']==app_id(binding['nonce'],row['terminal_id']),'GUI_RECEIPT_BINDING')
    need(row['reason'] in {'natural','signal','control_eof','cancel','output_limit','failed','unsupported'} and
        all(type(row[k]) is bool for k in ('cleanup_complete','pipes_closed','assets_runtime_unchanged')) and
        all(number(row[k]) and row[k]<=65536 for k in ('term_signals','kill_signals','reaped','open_pidfds')) and
        (row['child_returncode'] is None or type(row['child_returncode']) is int and -255<=row['child_returncode']<=255) and
        type(row['worker_returncode']) is int and -255<=row['worker_returncode']<=255,'GUI_RECEIPT_SCHEMA')
    for key in ('worker','child'):
        if row[key] is not None:
            need(set(row[key])=={'pid','start','group'},'GUI_RECEIPT_SCHEMA');identity(row[key],leader=True)
    return row


def natural(row):
    return row is not None and row['reason']=='natural' and row['child_returncode']==0 and row['worker_returncode']==0 and row['child'] is not None and row['worker'] is not None and all(row[k] for k in
        ('cleanup_complete','pipes_closed','assets_runtime_unchanged')) and row['open_pidfds']==row['term_signals']==row['kill_signals']==0 and row['reaped']>=1


def reclaimed(row):
    return row is not None and row['worker_returncode']==0 and row['cleanup_complete'] and row['pipes_closed'] and row['open_pidfds']==0


def sample(value,baseline,expected_app_id):
    runtime=value['runtime']['runtime'];app=runtime.get('app') or {};mapped=app.get('windows',[])
    if type(runtime.get('app')) is dict:row=sdk.sdk_sample(value)
    else:row=dict(sequence=value['sequence'],mapped=[],targets=[],native_targets=[],resources=resources(value))
    row.update(gui_windows=sorted(w['window'] for w in mapped if w.get('life')=='Mapped' and w['window'] not in baseline['mapped'] and w.get('app_id')==expected_app_id),
        foreign_windows=[] if expected_app_id is None else sorted(w['window'] for w in mapped if w.get('life')=='Mapped' and w['window'] not in baseline['mapped'] and w.get('app_id')!=expected_app_id),
        session_copies=app.get('copies',0),transport_state=runtime['transport']['state'],pending_cleanup=len(runtime['transport']['pending_cleanup']))
    need(number(row['session_copies']),'GUI_COPY_COUNTER');return row


def stopped(baseline,samples):
    if not samples:return False
    last=samples[-1];resource=last['resources'];board=resource['board']
    if last['transport_state']!='Idle' or last['pending_cleanup'] or last['mapped'] or last['targets'] or last['native_targets'] or resource['targets'] or resource['panels'] or resource['sdk_bindings'] or any(resource['owners'].values()):return False
    if board['entries'] or board['cleanup_required'] or board['unassigned']:return False
    owned={tuple(t['key'][k] for k in ('session','window','scene','generation')):t['key'] for row in [baseline,*samples] for t in row['resources']['targets']}
    return bool(owned) and all(any(r['key']==key and r['runtime']==resource['handle'] and r['sdk_disposed'] and r['sdk_binding_released'] and not r['disposal_failed'] for r in resource['retired']) for key in owned.values())


def summarize(baseline,samples,android_row,child_receipt,case):
    result=sdk.summarize_sdk(baseline,samples)
    result['baseline_windows_preserved']=bool(samples) and all(set(baseline['mapped'])<=set(row['mapped']) for row in samples)
    result['resources_retired']=sdk.sdk_resources_retired(baseline,samples)
    result['cookie_matched']=(bool(samples) and bool(android_row) and android_row['target'] is not None and set(result['additional_windows'])=={android_row['target']['window']} and
        any(row['gui_windows']==result['additional_windows'] for row in samples) and not any(row['foreign_windows'] for row in samples))
    result['natural_child_exit']=natural(child_receipt)
    result['session_stop_cleanup']=stopped(baseline,samples)
    result['session_shm_copy_delta']=max([row['session_copies'] for row in samples]+[baseline['session_copies']])-baseline['session_copies']
    result['wire_trace']='not_run';result['copy_counter_scope']='whole_session'
    delivered=False;acked=False
    if android_row is not None and android_row['result']=='completed':
        steps=android_row['steps'];counts=[r['delivered'] for r in steps];commands=android_row['commands']
        acked=bool(commands) and all(c['completion']=='ACCEPTED' for c in commands)
        if case=='keys':delivered=counts[1]-counts[0]==0 and counts[2:5]==[counts[0]+2,counts[0]+4,counts[0]+6] and counts[-1]==counts[0]+8 and sum(c['kind']=='key' for c in commands)==8
        else:delivered=len(set(counts))==1 and sum(c['kind']=='close_window_target' for c in commands)==(2 if android_row['first_close_pending'] else 1)
    result['fixed_input_delivered']=delivered;result['all_commands_acknowledged']=acked
    result['status']='observed' if result['status']=='observed' and all(result[k] for k in ('baseline_windows_preserved','resources_retired','cookie_matched','natural_child_exit','fixed_input_delivered','all_commands_acknowledged')) and result['session_shm_copy_delta']>0 and android_row['cleanup_complete'] else 'not_verified'
    return result
