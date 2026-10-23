"""Single-cycle operations for the frozen archive observer; no trading or research."""
import argparse
from contextlib import contextmanager
import ctypes
from datetime import timedelta
import json
import os
from pathlib import Path
from uuid import uuid4

from research import archive_observer as o
from research import archive_normalizer as n

INTERVAL = timedelta(hours=1)
MAX_TRANSIENT_FAILURES = 3


def directory(root):
    root = Path(root).resolve()
    return root.with_name(root.name + '_service')


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(n.json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def lease(manager):
    """OS releases the lock on process death; never delete a live lock file."""
    manager.mkdir(parents=True, exist_ok=True)
    with (manager / 'service.lock').open('a+b') as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise BlockingIOError('Another service cycle owns the operations lock') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def process_alive(pid):
    if pid <= 0:
        raise ValueError('Invalid collector PID; manual review required')
    if os.name == 'nt':
        # os.kill(pid, 0) is not a safe Windows process probe.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID absent
                return False
            raise PermissionError('Cannot prove collector process has exited')
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise PermissionError('Cannot query collector exit status')
            return code.value == 259  # STILL_ACTIVE; PID reuse is conservatively live
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def recover_collector_lock(root, alive=process_alive):
    """Only reclaim an unchanged lock whose PID is provably dead, after replay."""
    path = Path(root) / 'collector.lock'
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('Unsafe collector lock')
    original = path.read_bytes()
    pid = int(original)
    if alive(pid):
        raise BlockingIOError('Collector PID is alive; no lock removed')
    o.replay(root)
    if path.read_bytes() != original:
        raise BlockingIOError('Collector lock changed during recovery')
    path.unlink()
    return {'recovered_dead_collector_pid': pid, 'lock_bytes_sha256': n.sha(original)}


def receipts(manager):
    result, head = [], None
    for index, path in enumerate(sorted((manager / 'cycles').glob('*.json')), 1):
        blob = path.read_bytes()
        value = json.loads(blob)
        if path.name != f'{index:06d}-{n.sha(blob)}.json' or value['previous_sha256'] != head:
            raise ValueError('Operations receipt chain integrity failure')
        result.append(value)
        head = n.sha(blob)
    return result, head


def append_receipt(manager, value):
    previous, head = receipts(manager)
    value = {**value, 'previous_sha256': head}
    blob = n.json_bytes(value)
    folder = manager / 'cycles'
    folder.mkdir(exist_ok=True)
    o.publish(folder / f'{len(previous)+1:06d}-{n.sha(blob)}.json', blob)


def verify_anchor(root, output):
    """Verify a frozen snapshot, separating global uncommitted inventory from as-of facts.

    A later crash can leave new orphan bytes. They must not invalidate older committed
    evidence, and must never be imported as observations. Every orphan is still hashed
    by replay. The 2.3 snapshot verifier remains unchanged.
    """
    output = Path(output)
    manifest = json.loads((output / 'manifest.json').read_bytes())
    if manifest['observer_code_sha256'] != n.sha(Path(o.__file__).read_bytes().replace(b'\r\n', b'\n')):
        raise ValueError('Observer code differs from operations anchor')
    if manifest['semantic_registry_sha256'] != n.sha((o.CONTRACT_ROOT / 'contracts.json').read_bytes()):
        raise ValueError('Semantic registry differs from operations anchor')
    for name in ('summary.json', 'accepted.jsonl'):
        if n.sha((output / name).read_bytes()) != manifest['files'][name]:
            raise ValueError('Anchor output SHA-256 mismatch')
    saved = json.loads((output / 'summary.json').read_bytes())
    actual = o.snapshot(root, saved['coverage_end'], saved['as_of_utc'])
    rows = actual.pop('accepted')
    actual['orphan_objects'] = saved['orphan_objects']
    accepted = b''.join((json.dumps(row, ensure_ascii=False, sort_keys=True)+'\n').encode('utf-8') for row in rows)
    if (actual != saved or accepted != (output / 'accepted.jsonl').read_bytes()
            or actual['ledger_head_sha256'] != manifest['ledger_head_sha256']):
        raise ValueError('Committed anchor replay mismatch')


def checked(root):
    manager = directory(root)
    baseline = json.loads((manager / 'baseline.json').read_bytes())
    if baseline['version'] != 1 or baseline['root'] != str(Path(root).resolve()):
        raise ValueError('Operations baseline belongs to another root')
    anchor = json.loads((manager / 'snapshots/baseline/manifest.json').read_bytes())
    if baseline['ledger_head_sha256'] != anchor['ledger_head_sha256']:
        raise ValueError('Baseline/anchor head mismatch')
    verify_anchor(root, manager / 'snapshots' / 'baseline')
    history, _ = receipts(manager)
    checkpoint = next((r['snapshot'] for r in reversed(history) if r.get('snapshot')), None)
    if checkpoint:
        verify_anchor(root, manager / 'snapshots' / checkpoint)
    state = o.replay(root)
    return state, history


def initialize(root, clock=o.now):
    """Explicitly adopt an existing ledger offline, without resetting any facts."""
    root = Path(root).resolve()
    manager = directory(root)
    with lease(manager):
        if (manager / 'baseline.json').exists():
            checked(root)
            return {'status': 'already_initialized'}
        state = o.replay(root)
        end = state['runs'][-1]['requested_end_date'] if state['runs'] else state['config']['start_date']
        snapshot_path = manager / 'snapshots' / 'baseline'
        o.export_snapshot(root, end, snapshot_path)
        o.verify_snapshot(root, snapshot_path)
        o.publish(manager / 'baseline.json', n.json_bytes({
            'version': 1, 'root': str(root), 'adopted_at_utc': n.iso(clock()),
            'ledger_head_sha256': state['head'], 'cadence_seconds': 3600,
            'automatic_task_enabled': False,
            'note': 'Explicit local adoption; not evidence of historical publication or uptime.'}))
    return health(root, clock=clock)


def transient(run):
    if run['stop_reason'] != 'access_or_network_failure' or not run['events']:
        return False
    event = run['events'][-1]
    return event['status'] in (None, 500, 502, 503, 504) and event['final_url'] == event['url']


def health(root, clock=o.now):
    """Read-only, offline assessment. JSON/Markdown are projections, not ledgers."""
    instant = clock()
    result = {'schema_version': 1, 'checked_at_utc': n.iso(instant),
              'runtime_state': 'uninitialized', 'integrity': 'unknown',
              'latest_successful_collection_at_utc': None, 'latest_run_at_utc': None,
              'latest_run_result': None, 'verified_metric_dates': [], 'verified_coverage_segments': [],
              'unresolved_gaps': [], 'revisions': [], 'anomalies': [],
              'long_run_acceptance': 'not_established', 'prospective_prediction_samples': 0,
              'official_publication_at_utc': None, 'statistics_module_enabled': False}
    try:
        state, history = checked(root)
        runs = state['runs']
        end = str(instant.date() - timedelta(days=1))
        summary = o.snapshot(root, end)
        dates = [a['date'] for a in summary['archives']]
        segments = []
        for date in dates:
            if segments and o.day(date) == o.day(segments[-1]['end']) + timedelta(days=1):
                segments[-1]['end'] = date
                segments[-1]['days'] += 1
            else:
                segments.append({'start': date, 'end': date, 'days': 1})
        result.update(integrity='verified', runtime_state='healthy',
            verified_metric_dates=dates, verified_coverage_segments=segments,
            verified_metric_rows=summary['metric_rows'], accepted_taker_windows=summary['accepted_taker_windows'],
            metric_grid_quality=[{'date': a['date'], 'quality': a['quality']} for a in summary['archives']],
            unresolved_gaps=summary['gaps'], revisions=summary['revisions'], isolated=summary['isolated'],
            ledger_head_sha256=state['head'], observer_runs=len(runs), operations_cycles=len(history),
            orphan_objects=state['orphan_objects'], publisher_verification_failures=summary['publisher_verification_failures'])
        result['latest_operations_run'] = history[-1] if history else None
        result['latest_metric_version_first_observed_at_utc'] = max(
            [a['first_observed_at_utc'] for a in summary['archives']] or [None])
        result['metric_grid_issues'] = [item for item in result['metric_grid_quality'] if any(
            item['quality'][key] for key in ('invalid_label_lines', 'duplicate_label_rows',
                                            'off_grid_labels', 'missing_labels'))]
        if result['metric_grid_issues']:
            result['anomalies'].append('metric_grid_has_unresolved_issues')
        if runs:
            last = runs[-1]
            result.update(latest_run_at_utc=last['finished_at_utc'], latest_run_result=last['stop_reason'] or 'completed',
                          latest_request_statuses=[{'name': e['name'], 'status': e['status']} for e in last['events']])
            good = next((r for r in reversed(runs) if not r['stop_reason']), None)
            result['latest_successful_collection_at_utc'] = good['finished_at_utc'] if good else None
            if last['stop_reason']:
                result['runtime_state'] = 'retry_pending' if transient(last) else 'manual_review_required'
                failures = 0
                for previous in reversed(runs):
                    if not transient(previous):
                        break
                    failures += 1
                if failures >= MAX_TRANSIENT_FAILURES:
                    result['runtime_state'] = 'manual_review_required'
                    result['anomalies'].append('transient_retry_budget_exhausted')
                result['anomalies'].append(last['stop_reason'])
            if instant - n.utc(last['finished_at_utc']) > timedelta(hours=3):
                result['anomalies'].append('no_completed_collection_for_over_3_hours')
        else:
            result['anomalies'].append('no_collection_runs')
        result['overdue_gaps'] = [g for g in summary['gaps'] if
            instant - n.utc(g['date'] + 'T00:00:00+00:00') - timedelta(days=1) > timedelta(hours=48)]
        if result['overdue_gaps']:
            result['anomalies'].append('archive_gap_over_48_hours_after_day_end')
        if state['orphan_objects']:
            result['anomalies'].append('uncommitted_objects_preserved')
        if summary['publisher_verification_failures']:
            result['runtime_state'] = 'manual_review_required'
            result['anomalies'].append('publisher_verification_failure')
        result['longest_collection_interval_seconds'] = max([
            (n.utc(b['started_at_utc']) - n.utc(a['finished_at_utc'])).total_seconds()
            for a, b in zip(runs, runs[1:])] or [0])
        if history and history[-1]['result'] in ('error', 'manual_review_required'):
            result['runtime_state'] = 'manual_review_required'
            result['anomalies'].append(history[-1].get('error') or history[-1]['result'])
        if history and history[-1]['result'] == 'busy':
            result['runtime_state'] = 'busy'
            result['anomalies'].append(history[-1]['error'])
        if result['anomalies'] and result['runtime_state'] == 'healthy':
            result['runtime_state'] = 'degraded'
        if (directory(root) / 'running.json').exists():
            result['runtime_state'] = 'running_or_interrupted'
        if (Path(root) / 'collector.lock').exists():
            result['collector_lock_present'] = True
            if result['runtime_state'] in ('healthy', 'degraded'):
                result['runtime_state'] = 'collector_running_or_stale_lock'
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(runtime_state='manual_review_required', integrity='unverified',
                      anomalies=[type(error).__name__ + ': ' + str(error)])
    return result


def write_health(root, report):
    manager = directory(root)
    atomic(manager / 'health.json', report)
    text = '# Quant Lab archive observer health\n\n' + '\n'.join(
        '- **' + key + '**: ' + json.dumps(value, ensure_ascii=False)
        for key, value in report.items()) + '\n\n'
    text += 'First observation is not official publication. Backfill is retrospective. Long-run acceptance remains unestablished.\n'
    path = manager / 'health.md'
    temporary = path.with_name('.' + uuid4().hex + '.tmp')
    temporary.write_text(text, encoding='utf-8', newline='\n')
    os.replace(temporary, path)


def run_cycle(root, *, clock=o.now, fetcher=o.fetch, alive=process_alive):
    root = Path(root).resolve()
    manager = directory(root)
    try:
        with lease(manager):
            started = n.iso(clock())
            receipt = {'started_at_utc': started, 'result': 'error', 'snapshot': None}
            active = manager / 'running.json'
            owns_intent = False
            try:
                state, history = checked(root)
                # An operations exception requires explicit human repair/adoption, never silent retry.
                if history and history[-1]['result'] == 'error':
                    raise ValueError('Previous operations error requires manual review')
                receipt['lock_recovery'] = recover_collector_lock(root, alive)
                if active.exists():
                    abandoned = json.loads(active.read_bytes())
                    if alive(abandoned['pid']):
                        raise BlockingIOError('Previous service PID is still live')
                    receipt['interrupted_cycle'] = abandoned
                atomic(active, {'pid': os.getpid(), 'started_at_utc': started})
                owns_intent = True
                last = state['runs'][-1] if state['runs'] else None
                resume = False
                if last and n.utc(started) < n.utc(last['finished_at_utc']):
                    raise ValueError('Clock moved backwards')
                if last and n.utc(started) - n.utc(last['finished_at_utc']) < INTERVAL:
                    receipt['result'] = 'not_due'
                else:
                    failures = 0
                    for previous in reversed(state['runs']):
                        if not transient(previous):
                            break
                        failures += 1
                    if last and last['stop_reason']:
                        resume = transient(last) and failures < MAX_TRANSIENT_FAILURES
                        if not resume:
                            receipt['result'] = 'manual_review_required'
                    if receipt['result'] not in ('manual_review_required', 'not_due'):
                        receipt['collection'] = o.collect(root, state['config']['start_date'],
                            str(clock().date() - timedelta(days=1)), resume=resume,
                            recheck_hours=24, max_requests=25, fetcher=fetcher, clock=clock)
                        receipt['automatic_transient_resume'] = resume
                        receipt['result'] = 'failed' if receipt['collection']['stop_reason'] else 'completed'
                # Anchor even failed HTTP receipts; the failed response remains auditable.
                after = o.replay(root)
                label = uuid4().hex
                o.export_snapshot(root, after['runs'][-1]['requested_end_date'], manager / 'snapshots' / label)
                o.verify_snapshot(root, manager / 'snapshots' / label)
                receipt['snapshot'] = label
                receipt['ledger_head_sha256'] = after['head']
            except BlockingIOError as error:
                receipt.update(result='busy', error=str(error))
            except Exception as error:
                receipt.update(result='error', error=type(error).__name__ + ': ' + str(error))
            finally:
                receipt['finished_at_utc'] = n.iso(clock())
                try:
                    append_receipt(manager, receipt)
                except (OSError, ValueError, KeyError) as error:
                    receipt.update(result='error', error='Cannot append operations receipt: ' + str(error))
                    emergency = manager / 'emergency'
                    emergency.mkdir(exist_ok=True)
                    o.publish(emergency / (uuid4().hex + '.json'), n.json_bytes(receipt))
                # Only remove our own intent; an interrupted previous intent is evidence.
                if owns_intent and active.exists() and json.loads(active.read_bytes()).get('pid') == os.getpid():
                    active.unlink()
            report = health(root, clock=clock)
            write_health(root, report)
            return receipt, report
    except BlockingIOError:
        return {'result': 'already_running'}, None


def review(root, reason, clock=o.now):
    """Human-only acknowledgement after offline repair; does not bypass source blocks."""
    if not reason.strip():
        raise ValueError('A review reason is required')
    with lease(directory(root)):
        checked(root)
        append_receipt(directory(root), {'started_at_utc': n.iso(clock()),
            'finished_at_utc': n.iso(clock()), 'result': 'reviewed', 'snapshot': None,
            'reason': reason})
    return health(root, clock=clock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('initialize', 'run', 'health', 'review'))
    parser.add_argument('--root', type=Path, default=o.DEFAULT_ROOT)
    parser.add_argument('--reason', default='')
    args = parser.parse_args()
    if args.command == 'initialize':
        result = initialize(args.root)
    elif args.command == 'health':
        result = health(args.root)
        write_health(args.root, result)
    elif args.command == 'review':
        result = review(args.root, args.reason)
    else:
        receipt, report = run_cycle(args.root)
        print(json.dumps({'receipt': receipt, 'health': report}, ensure_ascii=False))
        raise SystemExit(2 if receipt['result'] in ('error', 'failed', 'manual_review_required') else 0)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(2 if result.get('runtime_state') == 'manual_review_required' else 0)


if __name__ == '__main__':
    main()
