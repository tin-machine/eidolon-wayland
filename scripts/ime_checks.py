"""IME wireの固定fixtureを検査する。実IME・物理入力の受入れは含まない。"""
import hashlib
import re

from quest_checks import instrumentation_result


def ime_wire_result(snapshot, nonce, expected, uid):
    if snapshot.get('schema') != 1 or snapshot.get('build_info') != expected:
        return False
    result = snapshot.get('check', {})
    if result.get('case') != 'ime-wire' or result.get('nonce') != nonce:
        return False
    if result.get('result') == 'fail':
        raise ValueError('IME wire検査が失敗しました')
    if result.get('result') != 'pass':
        return False
    rows = result.get('cycles', [])
    if len(rows) != 1 or result.get('cleanup_failed'):
        raise ValueError('IME wireの実行数・回収が不正です')
    row = rows[0]
    report = row.get('report', {})
    flags = ['disabled_default_safe', 'all_objects_entered', 'stale_sent_no_events',
             'active_destroy_recovered', 'enable_reset_pending', 'geometry_race_preserved',
             'semantic_changes_rejected', 'connection_invalidated', 'serial_wrap_verified',
             'seeded_serial_wrap', 'client_quota_isolated']
    cleanup = ['clients_after', 'objects_after', 'surfaces_after', 'ime_objects_after']
    if (row.get('cycle') != 1 or row.get('scenario') != 'text_input'
            or report.get('nonce') != hashlib.sha256(f'{nonce}:0'.encode()).hexdigest()[:16]
            or report.get('state') != 'pass' or report.get('error') is not None
            or type(report.get('uid')) is not int or report.get('uid') != uid
            or report.get('fixture_revision') != 'eidolon-text-input-v3-wire-v2'
            or report.get('synthetic_input') is not True or report.get('physical_input_verified') is not False
            or type(report.get('manager_version')) is not int or report.get('manager_version') != 1
            or type(report.get('checkpoints')) is not int or report.get('checkpoints') != 13
            or type(report.get('invalid_clients_isolated')) is not int or report.get('invalid_clients_isolated') != 8
            or any(report.get(key) is not True for key in flags)
            or any(type(report.get(key)) is not int or report[key] != 0 for key in cleanup)
            or type(report.get('delivered')) is not int or report.get('delivered') != 19
            or type(report.get('rejected')) is not int or report.get('rejected') != 34):
        raise ValueError('IME transaction・世代・不正client隔離・回収の証拠が不正です')
    after = result.get('after_stop', {})
    if (after.get('state') != 'Stopped'
            or any(after.get(key) != 0 for key in ['compositor_workers', 'dependency_workers', 'helper_workers'])
            or after.get('render', {}).get('workers') != 0 or after.get('transport', {}).get('workers') != 0
            or after.get('transport', {}).get('owned_connections') != 0
            or type(result.get('accept_ms')) is not int or not 0 <= result['accept_ms'] < 100
            or type(result.get('stop_ms')) is not int or not 0 <= result['stop_ms'] < 10000
            or not re.fullmatch('[1-9][0-9]*', str(result.get('last_handle', '')))):
        raise ValueError('IME wire runtimeの停止が不正です')
    return instrumentation_result(snapshot, result, uid)
