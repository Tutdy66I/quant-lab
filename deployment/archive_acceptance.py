"""Read-only, offline 7/28-day evidence report. Never declares automatic acceptance."""
import argparse
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import time

from deployment import archive_service as s
from deployment import archive_diagnostics as diagnostics
from deployment import archive_task_history
from research import archive_observer as o
from research import archive_normalizer as n


def inventory(path):
    files = [p for p in path.rglob('*') if p.is_file()]
    return {'files': len(files), 'bytes': sum(p.stat().st_size for p in files)}


def report(root=o.DEFAULT_ROOT, *, days=7, clock=o.now, scheduler_events=None):
    report_started = time.perf_counter()
    if days not in (7, 28):
        raise ValueError('Acceptance stage must be 7 or 28 days')
    instant = clock()
    root = Path(root).resolve()
    manager = s.directory(root)
    deployment_blob = (manager / 'deployment.json').read_bytes()
    deployment = json.loads(deployment_blob)
    if (deployment.get('schema_version') != 1
            or deployment.get('task_name') != 'quant-lab-archive-observer'
            or deployment.get('cadence_seconds') != 3600):
        raise ValueError('Unsupported deployment identity or cadence')
    enabled = n.utc(deployment['enabled_at_utc'])
    if enabled > instant:
        raise ValueError('Deployment is in the future')
    due = enabled + timedelta(days=days)
    result = {
        'schema_version': 1, 'stage_days': days, 'checked_at_utc': n.iso(instant),
        'operations_code_sha256': diagnostics.code_fingerprints(),
        'enabled_at_utc': deployment['enabled_at_utc'], 'due_at_utc': n.iso(due),
        'elapsed_seconds': (instant - enabled).total_seconds(),
        'stage': 'not_due' if instant < due else 'due_for_manual_review',
        'report_delay_seconds': max(0, (instant - due).total_seconds()),
        'long_run_acceptance': 'not_established',
        'deployment_sha256': n.sha(deployment_blob),
        'reporter_code_sha256': n.sha(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
        'integrity': 'unverified', 'evidence_limitations': [
            'Operation receipts do not establish a complete Task Scheduler execution denominator.',
            'Unknown trigger provenance is never inferred from clock alignment.',
            'Local hashes are not external proof against whole-directory rollback.',
            'First observation is not official publication; backfill is retrospective.',
            'Login/reboot recovery and unobserved failures remain unverified.',
        ],
        'task_execution_success_rate': None, 'schedule_punctuality_rate': None,
        'prospective_prediction_samples': 0, 'statistics_module_enabled': False,
    }
    # Do not diagnose a collector's in-progress publication as historical corruption.
    if (manager / 'running.json').exists() or (root / 'collector.lock').exists():
        result['report_state'] = 'pending_active_or_interrupted_cycle'
        return result
    started = time.perf_counter()
    state, history = s.checked(root)
    if (n.utc(state['all_runs_end']) > instant
            or any(n.utc(r['finished_at_utc']) > instant for r in history)):
        raise ValueError('Evidence is later than the report clock; clock review required')
    head = state['head']
    receipt_head = s.receipts(manager)[1]
    snapshots = sorted((manager / 'snapshots').iterdir())
    for snapshot in snapshots:
        s.verify_anchor(root, snapshot)
    summary = o.snapshot(root, str(instant.date() - timedelta(days=1)), n.iso(instant))
    result['offline_replay_seconds'] = time.perf_counter() - started
    if (manager / 'running.json').exists() or (root / 'collector.lock').exists():
        result['report_state'] = 'pending_concurrent_collection'
        return result
    if o.replay(root)['head'] != head or s.receipts(manager)[1] != receipt_head:
        result['report_state'] = 'pending_evidence_changed_during_read'
        return result
    cycles = [r for r in history if enabled <= n.utc(r['started_at_utc'])
              and n.utc(r['finished_at_utc']) <= instant]
    runs = [r for r in state['runs'] if enabled <= n.utc(r['started_at_utc'])
            and n.utc(r['finished_at_utc']) <= instant]
    outcomes = Counter(r['result'] for r in cycles)
    terminal = sum(outcomes[k] for k in ('completed', 'not_due', 'failed', 'error', 'manual_review_required'))
    normal = outcomes['completed'] + outcomes['not_due']
    successful_gaps = diagnostics.successful_intervals(state['runs'], enabled, instant)
    interruptions = [{'reported_at_utc': r['finished_at_utc'], **r['interrupted_cycle'],
                      'cause': r.get('interruption_cause', 'unknown')}
                     for r in cycles if r.get('interrupted_cycle')]
    trigger_counts = Counter(r.get('trigger_kind', 'unknown') for r in cycles)
    durations = [(n.utc(r['finished_at_utc']) - n.utc(r['started_at_utc'])).total_seconds() for r in runs]
    overdue = [g for g in summary['gaps'] if
               instant - n.utc(g['date'] + 'T00:00:00+00:00') - timedelta(days=1) > timedelta(hours=48)]
    monitor = root.with_name('archive_observer_monitor')
    legacy = monitor / 'baseline.json'
    conflict = None
    if legacy.exists():
        old = json.loads(legacy.read_bytes())
        old_start = old.get('operational_started_at_utc')
        if old_start and n.utc(old_start) != enabled:
            conflict = {'legacy_started_at_utc': old_start,
                        'difference_seconds': (enabled - n.utc(old_start)).total_seconds(),
                        'resolution': 'Use deployment enabled_at_utc; preserve the earlier manual baseline.'}
    recoveries = []
    missing = {}
    for run in state['runs']:
        if n.utc(run['finished_at_utc']) > instant:
            continue
        for event in run['events']:
            name = event['name']
            if not name.startswith('BTCUSDT-metrics-') or not name.endswith('.zip'):
                continue
            if event['status'] == 404:
                missing.setdefault(name, event['received_at_utc'])
            elif event['status'] == 200 and name in missing:
                version = summary['source_versions'].get(name, {}).get(event['body_sha256'], {})
                verified = version.get('first_publisher_verified_at_utc')
                if verified:
                    recoveries.append({'name': name, 'first_missing_observed_at_utc': missing.pop(name),
                                       'publisher_verified_at_utc': verified,
                                       'sha256': event['body_sha256']})
    result.update(
        report_state='ready_for_review', integrity='verified',
        observer_head_sha256=head, operations_head_sha256=receipt_head,
        verified_snapshots=len(snapshots), baseline_conflict=conflict,
        observed_operations=len(cycles), operation_outcomes=dict(outcomes),
        operation_normal_completion_rate=normal / terminal if terminal else None,
        operation_rate_denominator=terminal, trigger_counts=dict(trigger_counts),
        committed_collection_runs=len(runs), successful_collection_runs=sum(not r['stop_reason'] for r in runs),
        failed_collection_runs=sum(bool(r['stop_reason']) for r in runs),
        suppressed_duplicate_attempts='not fully observable; no receipt is written on OS lease rejection',
        interruptions=interruptions, successful_collection_intervals=successful_gaps,
        longest_successful_collection_interval_seconds=max(g['seconds'] for g in successful_gaps),
        collection_gaps_over_3_hours=[g for g in successful_gaps if g['seconds'] > 10800],
        collection_duration_seconds={'count': len(durations), 'max': max(durations, default=None)},
        unresolved_gaps=summary['gaps'], overdue_gaps=overdue, observed_gap_recoveries=recoveries,
        revisions=summary['revisions'], metric_rows=summary['metric_rows'],
        latest_collection_stop_reason=state['runs'][-1]['stop_reason'] if state['runs'] else None,
        latest_failure_diagnostic=diagnostics.failure(root, state['runs'][-1] if state['runs'] else None),
        operations_duration_seconds={'count': len(cycles), 'max': max([
            (n.utc(r['finished_at_utc']) - n.utc(r['started_at_utc'])).total_seconds()
            for r in cycles], default=None)},
        metric_grid_quality=[{'date': item['date'], 'quality': item['quality']} for item in summary['archives']],
        accepted_taker_windows=summary['accepted_taker_windows'], isolated=summary['isolated'],
        orphan_objects=summary['orphan_objects'], publisher_verification_failures=summary['publisher_verification_failures'],
        disk_measurement_at_utc=n.iso(instant),
        disk_inventory={p.name: inventory(p) for p in (root, manager, monitor)},
        deployment_disk_growth_bytes=None,
        disk_growth_limitation='No deployment-time byte inventory is established; this is a current measurement.',
    )
    result['review_criteria'] = diagnostics.criteria(result)
    result['scheduler_history'] = (archive_task_history.read(scheduler_events, enabled, instant)
                                   if scheduler_events else {'coverage': 'unavailable'})
    result['total_report_seconds'] = time.perf_counter() - report_started
    return result


def markdown(value):
    return '# Quant Lab archive observation review\n\n' + '\n'.join(
        '- **' + key + '**: ' + json.dumps(item, ensure_ascii=False)
        for key, item in value.items()) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=o.DEFAULT_ROOT)
    parser.add_argument('--days', type=int, choices=(7, 28), default=7)
    parser.add_argument('--scheduler-events', type=Path, help='Optional exported event XML; no system log changes')
    parser.add_argument('--output', type=Path, help='New report directory; stdout only when omitted')
    args = parser.parse_args()
    try:
        result = report(args.root, days=args.days, scheduler_events=args.scheduler_events)
        if args.output:
            output = args.output.resolve()
            if output.is_relative_to(args.root.resolve()) or output.is_relative_to(s.directory(args.root)):
                raise ValueError('Report output must be outside original observation/operations directories')
            output.mkdir(parents=True, exist_ok=False)
            (output / 'report.json').write_bytes(n.json_bytes(result))
            (output / 'report.md').write_text(markdown(result), encoding='utf-8', newline='\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['report_state'] == 'ready_for_review' else 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'report_state': 'unverified', 'error': str(error),
                          'long_run_acceptance': 'not_established'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
