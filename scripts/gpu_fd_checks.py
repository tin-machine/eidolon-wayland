"""製品をloadしない比較実験の証拠を検査。FD増加の有無を成功条件へ混ぜない。"""


def require(value, message):
    if not value:
        raise ValueError(message)


def validate(proof, nonce, mode, build, uid):
    require(proof.get('schema') == 1 and proof.get('case') == 'gpu-fds' and proof.get('nonce') == nonce
            and proof.get('mode') == mode and proof.get('status') == 'observed' and proof.get('acceptance') == 'not_run', '実験が未完了です')
    require(proof.get('uid') == uid and type(proof.get('pid')) is int and proof['pid'] > 0
            and proof.get('build_info', {}).get('source_digest') == build['source_digest']
            and proof.get('build_info', {}).get('profile') == 'debug'
            and proof.get('build') == proof.get('build_info', {}).get('apk_version_code')
            and proof.get('cycles') == 10 and proof.get('hold_ms') == 2000, '対象版・UID・条件が違います')

    def sample(value):
        require(value.get('sdk_loaded') is (mode == 'sdk') and value.get('product_native_loaded') is False,
                '製品nativeが混入、またはSDK対照条件が違います')
        require(type(value.get('fds')) is int and value['fds'] > 0 and type(value.get('elapsed_ms')) is int,
                'FD/時刻が欠測です')
        gpu = value.get('gpu')
        require(isinstance(gpu, list), 'GPU FDの採取がありません')
        require(all(type(x.get('fd')) is int and x['fd'] >= 0 and type(x.get('tid')) is int
                    and x['tid'] > 0 and type(x.get('alive')) is bool for x in gpu), 'GPU FDの形式が違います')
        require(len({x['fd'] for x in gpu}) == len(gpu), 'FDが重複しています')
        return gpu

    for key in ['baseline', 'after_empty', 'after_fresh_wait', 'after_reuse_join', 'final']:
        sample(proof.get(key, {}))
    tids = {}
    for phase in ['fresh', 'reused']:
        rows = proof.get(phase, [])
        require(len(rows) == 10, 'EGL反復数が足りません')
        tids[phase] = set()
        for row in rows:
            tid = row.get('tid')
            require(type(tid) is int and tid > 0 and type(row.get('draws')) is int and row['draws'] > 0
                    and all(row.get(k) is True for k in ['unbound', 'surface_destroyed', 'context_destroyed', 'thread_released']),
                    'GL描画または所有者上の解放が未確認です')
            tids[phase].add(tid)
            sample(row.get('during', {}))
            if phase == 'fresh':
                gpu = sample(row.get('after_join', {}))
                require(not any(x['tid'] == tid and x['alive'] for x in gpu), 'join後もthreadが生きています')
        require(len(tids[phase]) == (10 if phase == 'fresh' else 1), 'thread再利用条件が違います')
    empty = proof.get('empty_threads', [])
    require(len(empty) == 3 and all(type(x.get('tid')) is int and x['tid'] > 0 for x in empty), '空thread対照が不正です')
    require(len({x['tid'] for x in empty}) == 3 and not tids['fresh'] & tids['reused'], '試験TIDの独立性がありません')
    require(proof['after_fresh_wait']['elapsed_ms'] - proof['fresh'][-1]['after_join']['elapsed_ms'] >= 30000
            and proof['final']['elapsed_ms'] - proof['after_reuse_join']['elapsed_ms'] >= 30000, '解放待機が短すぎます')
    final = sample(proof['final'])
    require(not any(x['alive'] for x in final if x['tid'] in tids['fresh'] | tids['reused']), '試験threadが終了していません')
    return dict(mode=mode, status='observed', acceptance='not_run',
                empty_retained=sum(x['tid'] in {t['tid'] for t in empty} for x in final),
                fresh_retained=sum(x['tid'] in tids['fresh'] for x in final),
                reused_retained=sum(x['tid'] in tids['reused'] for x in final),
                final_gpu_fds=len(final), baseline_gpu_fds=len(proof['baseline']['gpu']),
                baseline_fds=proof['baseline']['fds'], final_fds=proof['final']['fds'])
