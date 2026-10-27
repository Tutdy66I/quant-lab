"""Quant Lab 2.6: independent, offline evidence review; never enables research."""
import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import json
from pathlib import Path
import subprocess

from deployment import archive_acceptance as acceptance
from deployment import archive_diagnostics as diagnostics
from deployment import archive_service as service
from research import archive_coverage as coverage
from research import archive_normalizer as n
from research import archive_observer as observer


def fingerprints(paths):
    """Byte fingerprints, including every existing snapshot and original object."""
    result = {}
    for path in paths:
        path = Path(path).resolve()
        files = sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in files:
            if item.is_file():
                result[str(item)] = n.sha(item.read_bytes())
    return result


def code_version():
    def git(*args):
        value = subprocess.run(['git', *args], cwd=n.ROOT, capture_output=True,
                               text=True, check=True)
        return value.stdout.strip()
    try:
        return {'commit': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
                'working_tree_status': git('status', '--porcelain', '--untracked-files=normal')}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'branch': None, 'working_tree_status': 'unavailable'}


def historical_failures(root, runs):
    """A later successful collection is recovery evidence, never erasure."""
    result = []
    for index, run in enumerate(runs):
        if not run['stop_reason']:
            continue
        recovery = next((r for r in runs[index + 1:] if not r['stop_reason']), None)
        result.append({'started_at_utc': run['started_at_utc'],
                       'finished_at_utc': run['finished_at_utc'],
                       'diagnostic': diagnostics.failure(Path(root), run),
                       'next_successful_collection_at_utc': recovery['finished_at_utc'] if recovery else None,
                       'recovery_scope': 'Later committed collection only; failure cause is not proven resolved.'})
    return result


def archive_delays(summary, instant, enabled=None):
    """Preserve >48h observer data gaps even after the archive is obtained.

    Elapsed time is measured after the UTC source day ends. It is NOT publisher
    latency: no first-publication time is established by these observations.
    """
    archives = {row['date']: row for row in summary['archives']}
    dates = sorted(set(archives) | {row['date'] for row in summary['gaps']})
    result = []
    for date in dates:
        name = f'BTCUSDT-metrics-{date}.zip'
        times = [n.utc(v['first_publisher_verified_at_utc'])
                 for v in summary['source_versions'].get(name, {}).values()
                 if v.get('first_publisher_verified_at_utc')]
        first_verified = min(times) if times else None
        end = n.utc(date + 'T00:00:00Z') + timedelta(days=1)
        verified_or_now = first_verified or instant
        elapsed = (verified_or_now - end).total_seconds()
        observed_start = max(end, n.utc(summary['observer_started_at_utc']))
        observed_seconds = max(0, (verified_or_now - observed_start).total_seconds())
        operational_start = max(observed_start, enabled or observed_start)
        operational_seconds = max(0, (verified_or_now - operational_start).total_seconds())
        if elapsed > 48 * 3600:
            result.append({'date': date, 'seconds_after_day_end': elapsed,
                           'first_publisher_verified_at_utc': n.iso(first_verified) if first_verified else None,
                           'current_archive_available': date in archives,
                           'historical_delay_retained': first_verified is not None,
                           'observed_unverified_seconds': observed_seconds,
                           'operational_unverified_seconds': operational_seconds,
                           'observed_gap_over_48_hours': observed_seconds > 48 * 3600,
                           'operational_gap_over_48_hours': operational_seconds > 48 * 3600,
                           'pre_observation_availability': 'unknown' if observed_start > end else 'not_applicable',
                           'published_at_utc': None,
                           'scope': 'Day-end acquisition age includes retrospective backfill; observed/operational gaps are separate.'})
    return result


def scheduler_audit(history, cycles):
    """Group shared instance IDs inside XML; proximity to receipts is not proof."""
    groups = defaultdict(list)
    ungrouped = []
    for event in history.get('events', []):
        if event['instance_id']:
            groups[event['instance_id']].append(event)
        else:
            ungrouped.append(event['record_id'])
    instances = []
    for instance, events in sorted(groups.items()):
        ids = {e['event_id'] for e in events}
        times = [n.utc(e['at_utc']) for e in events]
        first, last = min(times), max(times)
        candidates = [n.sha(n.json_bytes(c)) for c in cycles
                      if n.utc(c['started_at_utc']) <= last
                      and n.utc(c['finished_at_utc']) >= first]
        instances.append({'instance_id': instance, 'record_ids': [e['record_id'] for e in events],
                          'event_ids': sorted(ids),
                          'trigger_kinds': sorted({e['trigger_kind'] for e in events} - {'unknown'}),
                          'task_start_observed': 100 in ids, 'action_start_observed': 200 in ids,
                          'action_return_observed': 201 in ids, 'task_completion_observed': 102 in ids,
                          'action_result_codes': [e['action_result_code'] for e in events if e['event_id'] == 201],
                          'overlapping_receipt_sha256_candidates': candidates,
                          'receipt_association': 'unverified',
                          'reason': 'Receipts lack shared scheduler instance/parent-process evidence.'})
    return {'coverage': history['coverage'], 'source_sha256': history.get('source_sha256'),
            'event_count': len(history.get('events', [])), 'instances': instances,
            'ungrouped_record_ids': ungrouped, 'proven_receipt_associations': 0,
            'receipt_outcomes': dict(Counter(c['result'] for c in cycles)),
            'task_execution_success_rate': None, 'schedule_punctuality_rate': None,
            'limitations': ['Task, action and trigger events are distinct observations.',
                            'Missing terminal events do not prove failure or interruption.',
                            'Exit 0 and not_due do not prove collection success.',
                            'Event coverage/retention and the execution denominator remain unknown.',
                            'Time overlap is a candidate only; no historical trigger_kind is changed.']}


def assess_rows(rows, contracts, instant, enabled, protocol):
    """Assess existing qualifications without creating or expanding any contract."""
    if protocol['train_days'] != 252 or protocol['validation_days'] != 63:
        raise ValueError('Frozen 252/63 protocol differs; manual review required')
    registry = {c['id']: c for c in contracts}
    issues = []
    known_rows, prospective = [], []
    for row in rows:
        provenance = row.get('provenance', [])
        ids = [p.get('contract_id') for p in provenance] or [row.get('semantic_contract_id')]
        sources = [(p.get('file'), p.get('zip_sha256')) for p in provenance]
        if not sources:
            sources = [(f"BTCUSDT-metrics-{row['source_label'][:10]}.zip", row.get('source_version_sha256'))]
        matched = all(cid in registry and registry[cid]['field'] == row['field'] for cid in ids)
        matched = matched and all(any(s['file'] == name and s['zip_sha256'] == digest
                                      for cid in ids for s in registry[cid]['sources'])
                                  for name, digest in sources)
        if not matched or row['field'] != n.TAKER:
            issues.append({'window_start_utc': row['window_start_utc'], 'reason': 'unverified_version_or_field'})
            continue
        event = n.utc(row['event_time_utc'])
        available = n.utc(row['research_available_at_utc'])
        required = [event, n.utc(row['collected_at_utc']),
                    *(n.utc(registry[cid]['verified_at_utc']) for cid in ids)]
        required += [n.utc(row[key]) for key in ('first_observed_at_utc', 'evidence_collected_at_utc',
                                               'normalized_at_utc') if row.get(key)]
        if available < max(required) or available > instant or row['published_at_utc'] is not None:
            issues.append({'window_start_utc': row['window_start_utc'], 'reason': 'availability_barrier_not_established'})
            continue
        known_rows.append(row)
        # Research known after collection is not information known at event time.
        if event >= enabled and available >= enabled:
            prospective.append(row)
    def continuity(values):
        return coverage.continuous_runs(values)
    segments = continuity(known_rows)
    forward_segments = continuity(prospective)
    longest = max((r['duration_days'] for r in segments), default=0)
    return {'decision': 'insufficient_evidence', 'formal_statistics_allowed': False,
            'statistics_module_enabled': False, 'prospective_prediction_samples': 0,
            'qualified_rows_audited': len(known_rows), 'issues': issues,
            'continuous_runs': segments, 'longest_continuous_days': longest,
            'post_deployment_event_windows': len(prospective), 'prospective_continuous_runs': forward_segments,
            'minimum_frozen_train_days': 252, 'minimum_frozen_validation_days': 63,
            'minimum_contiguous_days_for_one_split': 315,
            'coverage_gate': 'insufficient_evidence' if longest < 315 else 'manual_review_required',
            'published_at_utc': None, 'historical_availability_proven': False,
            'reasons': ['Backfilled windows are never treated as known at their event times.',
                        'Window counts are not independent prediction samples.',
                        'Coverage alone cannot establish field admission, targets or prospective sample eligibility.']}


def research_audit(summary, instant, enabled):
    checks, rows = {}, []
    for name, source, output, verifier, row_file in (
            ('normalization_v1', n.SOURCE, n.ROOT / 'data/archive_normalized/binance_um_btcusdt_v1',
             n.verify, None),
            ('coverage_v1', coverage.DEFAULT_SOURCE, observer.CONTRACT_ROOT, coverage.verify, 'validated.jsonl')):
        try:
            checks[name] = {'integrity': 'verified', **verifier(source, output),
                            'manifest_sha256': n.sha((output / 'manifest.json').read_bytes())}
            if row_file:
                rows = [json.loads(line) for line in (output / row_file).read_text(encoding='utf-8').splitlines()]
        except (OSError, ValueError, KeyError, TypeError) as error:
            checks[name] = {'integrity': 'unverified', 'error': str(error)}
    contracts = observer.contracts()
    protocol = json.loads((n.ROOT / 'docs/derivatives_protocol_v1.json').read_bytes())
    # Assess the historical version separately from the current observer version.
    # Combining them would conceal revisions or conflicting observations.
    historical = assess_rows(rows, contracts, instant, enabled, protocol)
    current = assess_rows(summary['accepted'], contracts, instant, enabled, protocol)
    observed_versions = []
    for archive in summary.get('archives', []):
        accepted = [r for r in summary['accepted'] if r['source_version_sha256'] == archive['sha256']]
        observed_versions.append({
            'date': archive['date'], 'zip_sha256': archive['sha256'],
            'first_observed_at_utc': archive['first_observed_at_utc'],
            'first_publisher_verified_at_utc': archive['first_publisher_verified_at_utc'],
            'last_confirmed_at_utc': archive['last_confirmed_at_utc'], 'published_at_utc': None,
            'accepted_taker_windows': len(accepted),
            'earliest_taker_research_available_at_utc': min((r['research_available_at_utc'] for r in accepted), default=None),
            'other_fields': 'quarantined; no independent semantic evidence'})
    versions = [{'date': c['date'], 'contract_id': c['id'], 'offset_minutes': c['offset_minutes'],
                 'verified_at_utc': c['verified_at_utc'],
                 'sources': [{'file': s['file'], 'zip_sha256': s['zip_sha256'],
                              'checksum_sha256': s['checksum_sha256']} for s in c['sources']]}
                for c in contracts]
    return {'decision': 'insufficient_evidence', 'formal_statistics_allowed': False,
            'statistics_module_enabled': False, 'source_verifications': checks,
            'historical_coverage': historical, 'current_observer': current,
            'exact_version_contracts': versions, 'current_isolation': summary['isolated'],
            'observed_archive_versions': observed_versions,
            'source_revisions': summary['revisions'],
            'independent_oi_long_short_status': 'insufficient_evidence; remain quarantined',
            'time_semantics': {'first_observation': summary['first_observed_evidence_scope'],
                               'actual_publication': 'unknown',
                               'research_availability': 'Existing per-version evidence barrier; not historical publication.'},
            'limitations': ['No new independent field validation or new semantic contracts are created.',
                            'Historical offset differences are version scoped, not a located transition date.']}


def stages(base):
    result = {}
    instant, enabled = n.utc(base['checked_at_utc']), n.utc(base['enabled_at_utc'])
    for days in (7, 28):
        due = enabled + timedelta(days=days)
        value = {**base, 'stage_days': days,
                 'stage': 'not_due' if instant < due else 'due_for_manual_review'}
        result[str(days)] = {'stage': value['stage'], 'due_at_utc': n.iso(due),
                             'long_run_acceptance': 'not_established',
                             'review_criteria': diagnostics.criteria(value) if base.get('integrity') == 'verified' else None}
    return result


def report(root=observer.DEFAULT_ROOT, *, clock=observer.now, scheduler_events=None):
    instant = clock()
    root = Path(root).resolve()
    protected = [root, service.directory(root), root.with_name('archive_observer_monitor'), n.ROOT / 'data',
                 *(n.ROOT / 'docs').glob('*protocol*.json'),
                 *(Path(module.__file__) for module in (acceptance, diagnostics, service, coverage, n, observer)),
                 Path(__file__)]
    if scheduler_events:
        protected.append(Path(scheduler_events).resolve())
    result = {'schema_version': 1, 'quant_lab_version': '2.6', 'checked_at_utc': n.iso(instant),
              'code_version': code_version(), 'auditor_sha256': n.sha(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
              'source_fingerprints': {}, 'report_state': 'unverified',
              'long_run_acceptance': 'not_established', 'research_admission': 'insufficient_evidence',
              'statistics_module_enabled': False, 'verified_facts': [], 'anomalies': [],
              'unverified_items': ['Real login/reboot/sleep and network-recovery causes.',
                                   'Independent OI and long/short ratio timing and licensing evidence.',
                                   'External assurance against whole-directory rollback.'],
              'not_assessable': ['Complete scheduler execution denominator and success rate.',
                                 'Actual publisher first-publication times.', 'Deployment-time disk growth.'],
              'manual_actions': ['Review historical gaps/failures without resetting the deployment start.',
                                 'Preserve further natural scheduler/collector evidence through the due dates.',
                                 'Establish shared instance/process evidence before claiming scheduler/receipt association.',
                                 'Obtain permitted independent field timing evidence before a new admission review.']}
    try:
        before = fingerprints(protected)
        result['source_fingerprints'] = before
        deployment = json.loads((service.directory(root) / 'deployment.json').read_bytes())
        if (deployment.get('schema_version') != 1 or deployment.get('cadence_seconds') != 3600
                or deployment.get('task_name') != 'quant-lab-archive-observer'
                or n.utc(deployment['enabled_at_utc']) > instant):
            raise ValueError('Unsupported deployment identity/cadence or future start')
        # Time eligibility is still visible when subsequent integrity checks fail.
        result['acceptance_stages'] = stages({'checked_at_utc': n.iso(instant),
                                              'enabled_at_utc': deployment['enabled_at_utc'],
                                              'integrity': 'unverified'})
        base = acceptance.report(root, days=7, clock=lambda: instant, scheduler_events=scheduler_events)
        result.update(operations=base, acceptance_stages=stages(base))
        if base['report_state'] != 'ready_for_review':
            result['report_state'] = base['report_state']
            return result
        state, cycles = service.checked(root)
        enabled = n.utc(base['enabled_at_utc'])
        cycles = [c for c in cycles if enabled <= n.utc(c['started_at_utc'])
                  and n.utc(c['finished_at_utc']) <= instant]
        summary = observer.snapshot(root, str(instant.date() - timedelta(days=1)), n.iso(instant))
        failures = historical_failures(root, state['runs'])
        delays = archive_delays(summary, instant, enabled)
        operational_delays = [d for d in delays if d['operational_gap_over_48_hours']]
        result.update(historical_collection_failures=failures, archive_delays_over_48_hours=delays,
                      scheduler=scheduler_audit(base['scheduler_history'], cycles),
                      research=research_audit(summary, instant, n.utc(base['enabled_at_utc'])))
        for key, items in (('collection_gap_over_3_hours', base['collection_gaps_over_3_hours']),
                           ('historical_collection_failure', failures),
                           ('observed_operations_interruption', base['interruptions']),
                           ('operational_archive_gap_over_48_hours', operational_delays),
                           ('source_revision', summary['revisions']),
                           ('publisher_verification_failure', summary['publisher_verification_failures']),
                           ('uncommitted_object', summary['orphan_objects'])):
            if items:
                result['anomalies'].append({'kind': key, 'count': len(items)})
        source_checks = result['research']['source_verifications']
        if any(c['integrity'] != 'verified' for c in source_checks.values()):
            result['anomalies'].append({'kind': 'normalized_source_integrity_unverified'})
        for scope in ('historical_coverage', 'current_observer'):
            if result['research'][scope]['issues']:
                result['anomalies'].append({'kind': 'research_availability_or_version_issue', 'scope': scope})
        # A recovered operational >48h gap remains failed. Pre-observation
        # backfill age is retained, but cannot establish an operational outage.
        for value in result['acceptance_stages'].values():
            if operational_delays:
                value['review_criteria']['checks']['historical_archive_delays'] = 'fail'
                value['review_criteria']['decision'] = 'blocked'
        result['verified_facts'] = [
            {'kind': 'observer_objects_receipts_and_snapshots_replayed', 'snapshots': base['verified_snapshots']},
            {'kind': 'committed_collection_outcomes', 'successful': base['successful_collection_runs'],
             'failed': base['failed_collection_runs'], 'normal_skips': base['operation_outcomes'].get('not_due', 0)},
            {'kind': 'long_run_and_formal_research_not_established'}]
        result['retrospective_acquisition_delays'] = [d for d in delays if not d['observed_gap_over_48_hours']]
        after = fingerprints(protected)
        changed = sorted(p for p in before if before[p] != after.get(p))
        added = sorted(set(after) - set(before))
        result['read_consistency'] = {'changed_or_missing_inputs': changed, 'added_inputs': added}
        if changed or added:
            result['report_state'] = 'pending_evidence_changed_during_read'
            result['verified_facts'] = []
        else:
            result['report_state'] = 'ready_for_review'
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['anomalies'].append({'kind': 'evidence_verification_failed', 'error': str(error)})
    return result


def markdown(value):
    lines = ['# Quant Lab 2.6 离线审计', '',
             f"检查时间：{value['checked_at_utc']}；报告状态：{value['report_state']}。", '',
             '长期验收：not_established；正式统计研究准入：insufficient_evidence。', '']
    for title, key in (('长期验收时间门槛', 'acceptance_stages'), ('已验证事实', 'verified_facts'),
                       ('已发现异常', 'anomalies'), ('未验证事项', 'unverified_items'),
                       ('无法评估事项', 'not_assessable'), ('需要人工处理', 'manual_actions')):
        lines += ['## ' + title, '', '```json', json.dumps(value.get(key), ensure_ascii=False, indent=2), '```', '']
    if 'research' in value:
        research = value['research']
        lines += ['## 数据准入与运行证据', '',
                  f"历史已验证 taker 窗口 {research['historical_coverage']['qualified_rows_audited']}，"
                  f"最长连续 {research['historical_coverage']['longest_continuous_days']} 天；"
                  f"当前观察器接纳 {research['current_observer']['qualified_rows_audited']} 窗口；"
                  f"部署后事件的合格窗口 {research['current_observer']['post_deployment_event_windows']}。", '',
                  '固定 252/63 天协议不变。OI、多空比继续隔离；首次观察不等于实际发布时间，历史回补不能用于当时决策。', '',
                  '```json', json.dumps({'historical_collection_failures': value['historical_collection_failures'],
                                        'operations_interruptions': value['operations']['interruptions'],
                                        'archive_delays_over_48_hours': value['archive_delays_over_48_hours'],
                                        'source_verifications': research['source_verifications'],
                                        'scheduler_coverage': value['scheduler']['coverage'],
                                        'proven_receipt_associations': value['scheduler']['proven_receipt_associations'],
                                        'task_execution_success_rate': None}, ensure_ascii=False, indent=2), '```', '']
    lines += ['## 代码版本', '', '```json', json.dumps(value['code_version'], ensure_ascii=False, indent=2),
              '```', '', f"审计器 LF SHA-256：{value['auditor_sha256']}", '']
    lines += ['## 完整证据与指纹', '', '详见同目录 report.json；来源 Binance Vision，CC BY-NC-SA 4.0。', '']
    return '\n'.join(lines)


def export(value, output, root=observer.DEFAULT_ROOT, scheduler_events=None):
    output = Path(output).resolve()
    inputs = [Path(root).resolve(), service.directory(root), n.ROOT / 'data',
              Path(root).resolve().with_name('archive_observer_monitor'), n.ROOT / 'docs']
    if scheduler_events:
        inputs.append(Path(scheduler_events).resolve())
    if any(output == p or output.is_relative_to(p) or p.is_relative_to(output) for p in inputs):
        raise ValueError('Use a new output directory outside all original evidence and source inputs')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'report.json').write_bytes(n.json_bytes(value))
    (output / 'report.md').write_text(markdown(value), encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=observer.DEFAULT_ROOT)
    parser.add_argument('--scheduler-events', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New directory; never overwrites evidence')
    args = parser.parse_args()
    value = report(args.root, scheduler_events=args.scheduler_events)
    try:
        export(value, args.output, args.root, args.scheduler_events)
    except (OSError, ValueError) as error:
        print(json.dumps({'report_state': 'output_rejected', 'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({k: value[k] for k in ('report_state', 'checked_at_utc', 'long_run_acceptance',
                                          'research_admission', 'anomalies')}, ensure_ascii=False, indent=2))
    return 0 if value['report_state'] == 'ready_for_review' else 2


if __name__ == '__main__':
    raise SystemExit(main())
