"""2.6 review gates: saved evidence only; no network, strategy or task changes."""
from copy import deepcopy
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from deployment import archive_audit as audit
from deployment import archive_diagnostics as diagnostics
from deployment import archive_service as service
from research import archive_normalizer as n
from research import archive_observer as observer
from test_archive_service import Clock, Server


class AuditProjectionTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 23, 3, 17, 28, tzinfo=n.UTC)

    def test_both_stages_not_due_until_exact_boundary(self):
        for days in (7, 28):
            for offset, expected in ((-1, 'not_due'), (0, 'due_for_manual_review')):
                result = audit.stages({'checked_at_utc': n.iso(self.start + timedelta(days=days, seconds=offset)),
                                       'enabled_at_utc': n.iso(self.start), 'integrity': 'unverified'})
                self.assertEqual(result[str(days)]['stage'], expected)
                self.assertEqual(result[str(days)]['long_run_acceptance'], 'not_established')

    def test_failed_run_and_recovery_preserve_closed_and_open_success_gaps(self):
        runs = [{'finished_at_utc': n.iso(self.start + timedelta(hours=h)), 'stop_reason': reason}
                for h, reason in ((0, None), (2, 'failure'), (5, None))]
        gaps = diagnostics.successful_intervals(runs, self.start, self.start + timedelta(hours=9))
        self.assertEqual([g['seconds'] for g in gaps if g['seconds'] > 10800], [18000, 14400])

    def test_recovered_archive_delay_is_retained_and_is_not_publication_latency(self):
        summary = {'archives': [{'date': '2026-10-20'}], 'gaps': [],
                   'observer_started_at_utc': '2026-10-21T00:00:00Z',
                   'source_versions': {'BTCUSDT-metrics-2026-10-20.zip': {'old': {
                       'first_publisher_verified_at_utc': '2026-10-24T00:00:01Z'}}}}
        delays = audit.archive_delays(summary, self.start + timedelta(days=7))
        self.assertEqual(len(delays), 1)
        self.assertTrue(delays[0]['historical_delay_retained'])
        self.assertTrue(delays[0]['current_archive_available'])
        self.assertTrue(delays[0]['operational_gap_over_48_hours'])
        self.assertIsNone(delays[0]['published_at_utc'])
        summary['archives'], summary['gaps'], summary['source_versions'] = [], [{'date': '2026-10-20'}], {}
        self.assertFalse(audit.archive_delays(summary, self.start)[0]['current_archive_available'])

    def test_backfill_acquisition_age_does_not_invent_pre_observation_outage(self):
        summary = {'observer_started_at_utc': n.iso(self.start),
                   'archives': [{'date': '2026-10-20'}], 'gaps': [],
                   'source_versions': {'BTCUSDT-metrics-2026-10-20.zip': {'hash': {
                       'first_publisher_verified_at_utc': n.iso(self.start + timedelta(seconds=20))}}}}
        value = audit.archive_delays(summary, self.start + timedelta(days=1), self.start + timedelta(minutes=5))[0]
        self.assertGreater(value['seconds_after_day_end'], 48 * 3600)
        self.assertEqual(value['observed_unverified_seconds'], 20)
        self.assertFalse(value['observed_gap_over_48_hours'])
        self.assertFalse(value['operational_gap_over_48_hours'])
        self.assertEqual(value['pre_observation_availability'], 'unknown')

    def test_missing_incomplete_and_zero_exit_scheduler_events_never_establish_success(self):
        receipt = {'started_at_utc': n.iso(self.start), 'finished_at_utc': n.iso(self.start + timedelta(seconds=2)),
                   'result': 'not_due'}
        event = {'instance_id': 'instance', 'event_id': 201, 'record_id': 1, 'at_utc': n.iso(self.start),
                 'trigger_kind': 'unknown', 'action_result_code': '0'}
        for events in ([], [event]):
            value = audit.scheduler_audit({'coverage': 'partial_or_unknown', 'events': events}, [receipt])
            self.assertIsNone(value['task_execution_success_rate'])
            self.assertEqual(value['proven_receipt_associations'], 0)
            self.assertEqual(value['receipt_outcomes'], {'not_due': 1})
            if events:
                instance = value['instances'][0]
                self.assertEqual(instance['receipt_association'], 'unverified')
                self.assertFalse(instance['task_start_observed'])
                self.assertFalse(instance['task_completion_observed'])
                self.assertTrue(instance['overlapping_receipt_sha256_candidates'])

    def test_semantic_version_and_research_availability_cannot_be_relaxed(self):
        contract = observer.contracts()[-1]
        output = observer.CONTRACT_ROOT
        row = json.loads((output / 'validated.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        protocol = {'train_days': 252, 'validation_days': 63}
        def assess(rows):
            return audit.assess_rows(rows, [contract], self.start, self.start, protocol)
        value = assess([row])
        self.assertEqual(value['qualified_rows_audited'], 1)
        self.assertEqual(value['decision'], 'insufficient_evidence')
        self.assertFalse(value['formal_statistics_allowed'])
        self.assertEqual(value['post_deployment_event_windows'], 0)
        changed = deepcopy(row)
        changed['provenance'][0]['zip_sha256'] = 'revised'
        self.assertEqual(assess([changed])['issues'][0]['reason'], 'unverified_version_or_field')
        changed = deepcopy(row)
        changed['research_available_at_utc'] = changed['event_time_utc']
        self.assertEqual(assess([changed])['issues'][0]['reason'], 'availability_barrier_not_established')
        changed = deepcopy(row)
        changed['field'] = 'sum_open_interest'
        self.assertEqual(assess([changed])['qualified_rows_audited'], 0)
        protocol['train_days'] = 16
        with self.assertRaisesRegex(ValueError, '252/63'):
            assess([row])

    def test_original_normalized_versions_replay_and_remain_insufficient(self):
        summary = {'accepted': [], 'isolated': [], 'revisions': [],
                   'first_observed_evidence_scope': 'committed receipts'}
        value = audit.research_audit(summary, self.start, self.start)
        self.assertTrue(all(v['integrity'] == 'verified' for v in value['source_verifications'].values()))
        historical = value['historical_coverage']
        self.assertEqual(historical['qualified_rows_audited'], 7776)
        self.assertEqual(historical['longest_continuous_days'], 16)
        self.assertEqual(historical['issues'], [])
        self.assertEqual(value['decision'], 'insufficient_evidence')
        self.assertEqual(value['current_observer']['post_deployment_event_windows'], 0)
        with mock.patch.object(audit.coverage, 'verify', side_effect=ValueError('source SHA-256 mismatch')):
            failed = audit.research_audit(summary, self.start, self.start)
        self.assertEqual(failed['source_verifications']['coverage_v1']['integrity'], 'unverified')
        self.assertFalse(failed['formal_statistics_allowed'])
        self.assertEqual(failed['historical_coverage']['qualified_rows_audited'], 0)

    def test_output_never_overwrites_inputs_or_existing_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root, output = Path(folder) / 'observer', Path(folder) / 'review'
            value = {'checked_at_utc': n.iso(self.start), 'report_state': 'unverified',
                     'code_version': {}, 'auditor_sha256': 'hash'}
            audit.export(value, output, root)
            before = (output / 'report.json').read_bytes()
            with self.assertRaises(FileExistsError):
                audit.export(value, output, root)
            self.assertEqual((output / 'report.json').read_bytes(), before)
            for target in (root, root / 'nested', Path(folder), service.directory(root) / 'nested', n.ROOT / 'data/new'):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    audit.export(value, target, root)


class AuditEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'observer'
        self.clock, self.server = Clock(), Server()
        observer.collect(self.root, '2026-10-20', '2026-10-20', clock=self.clock, fetcher=self.server)
        service.initialize(self.root, self.clock)
        service.atomic(service.directory(self.root) / 'deployment.json', {
            'schema_version': 1, 'task_name': 'quant-lab-archive-observer',
            'cadence_seconds': 3600, 'enabled_at_utc': n.iso(self.clock())})

    def review(self):
        research = {'source_verifications': {}, 'historical_coverage': {'issues': []}, 'current_observer': {'issues': []}}
        with mock.patch.object(audit, 'research_audit', return_value=research):
            return audit.report(self.root, clock=self.clock)

    def test_report_keeps_tls_failure_after_recovery_and_preserves_all_existing_bytes(self):
        self.clock.value += timedelta(hours=1)
        self.server.status = (None, b'CERTIFICATE_VERIFY_FAILED')
        service.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        self.clock.value += timedelta(hours=4)
        self.server.status = None
        service.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        before = audit.fingerprints([self.root, service.directory(self.root)])
        calls = len(self.server.calls)
        result = self.review()
        self.assertEqual(result['report_state'], 'ready_for_review')
        self.assertEqual(result['operations']['latest_collection_stop_reason'], None)
        failure = result['historical_collection_failures'][-1]
        self.assertEqual(failure['diagnostic']['category'], 'tls_certificate_validation')
        self.assertIsNotNone(failure['next_successful_collection_at_utc'])
        self.assertIn('historical_collection_failure', [v['kind'] for v in result['anomalies']])
        self.assertEqual(result['acceptance_stages']['7']['stage'], 'not_due')
        self.assertEqual(result['acceptance_stages']['7']['review_criteria']['decision'], 'blocked')
        self.assertEqual(before, audit.fingerprints([self.root, service.directory(self.root)]))
        self.assertEqual(calls, len(self.server.calls))

    def test_corrupt_source_object_cannot_produce_verified_report_or_research_admission(self):
        digest = observer.replay(self.root)['current']['BTCUSDT-metrics-2026-10-20.zip']['body_sha256']
        (self.root / 'objects' / (digest + '.bin')).write_bytes(b'corrupt source')
        value = self.review()
        self.assertEqual(value['report_state'], 'unverified')
        self.assertEqual(value['research_admission'], 'insufficient_evidence')
        self.assertEqual(value['verified_facts'], [])
        self.assertEqual(value['acceptance_stages']['7']['stage'], 'not_due')

    def test_concurrent_evidence_change_requires_fresh_report(self):
        real_fingerprints = audit.fingerprints
        count = 0
        def changing(paths):
            nonlocal count
            result = real_fingerprints(paths)
            count += 1
            if count == 2:
                result['new-concurrent-input'] = 'hash'
            return result
        with mock.patch.object(audit, 'fingerprints', side_effect=changing):
            value = self.review()
        self.assertEqual(value['report_state'], 'pending_evidence_changed_during_read')
        self.assertEqual(value['verified_facts'], [])

    def test_real_recovered_over_48_hour_gap_still_blocks_review(self):
        root = self.root.with_name('delayed-observer')
        name = 'BTCUSDT-metrics-2026-10-20.zip'
        self.server.missing.add(name)
        observer.collect(root, '2026-10-20', '2026-10-20', clock=self.clock, fetcher=self.server)
        service.initialize(root, self.clock)
        service.atomic(service.directory(root) / 'deployment.json', {
            'schema_version': 1, 'task_name': 'quant-lab-archive-observer',
            'cadence_seconds': 3600, 'enabled_at_utc': n.iso(self.clock())})
        self.clock.value += timedelta(days=3)
        self.server.missing.remove(name)
        service.run_cycle(root, clock=self.clock, fetcher=self.server)
        self.root = root
        value = self.review()
        delay = next(d for d in value['archive_delays_over_48_hours'] if d['date'] == '2026-10-20')
        self.assertTrue(delay['current_archive_available'])
        self.assertTrue(delay['operational_gap_over_48_hours'])
        for days in ('7', '28'):
            checks = value['acceptance_stages'][days]['review_criteria']
            self.assertEqual(checks['checks']['historical_archive_delays'], 'fail')
            self.assertEqual(checks['decision'], 'blocked')

    def test_publisher_revision_remains_quarantined_and_both_objects_survive(self):
        from test_archive_observer import Server as RevisableServer
        server = RevisableServer()
        name = 'BTCUSDT-metrics-2026-10-20.zip'
        old = observer.replay(self.root)['current'][name]['body_sha256']
        buffer = io.BytesIO(observer.object_bytes(self.root, old))
        with zipfile.ZipFile(buffer, 'a') as archive:
            archive.comment = b'revised publisher bytes'
        revised = buffer.getvalue()
        server.bodies[name] = revised
        server.bodies[name + '.CHECKSUM'] = (n.sha(revised) + '  ' + name + '\n').encode()
        self.clock.value += timedelta(hours=1)
        observer.collect(self.root, '2026-10-20', '2026-10-20', clock=self.clock, fetcher=server, refresh=True)
        result = self.review()
        self.assertEqual(result['operations']['accepted_taker_windows'], 0)
        self.assertEqual(len(result['operations']['revisions']), 2)
        self.assertIn(n.TAKER, result['operations']['isolated'][0]['fields'])
        self.assertTrue((self.root / 'objects' / (old + '.bin')).exists())
        self.assertTrue((self.root / 'objects' / (n.sha(revised) + '.bin')).exists())


if __name__ == '__main__':
    unittest.main()
