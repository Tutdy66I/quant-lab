"""Operations failure/recovery tests, using frozen public bytes and no network."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from deployment import archive_service as s
from research import archive_observer as o
from research import archive_normalizer as n


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 21, 12, tzinfo=n.UTC)

    def __call__(self):
        self.value += timedelta(milliseconds=10)
        return self.value


class Server:
    def __init__(self):
        self.calls = []
        self.status = None
        self.missing = set()

    def __call__(self, url, headers):
        self.calls.append(url)
        if self.status:
            return self.status[0], self.status[1], {}, url
        name = 'terms' if url == o.TERMS else url.rsplit('/', 1)[-1]
        path = o.APPROVED_TERMS if name == 'terms' else n.ROOT / 'data/archive_coverage/binance_um_btcusdt_v1/raw' / name
        if name in self.missing or not path.exists():
            return 404, b'not published', {}, url
        blob = path.read_bytes()
        etag = n.sha(blob)
        if headers.get('If-None-Match') == etag:
            return 304, b'', {'ETag': etag}, url
        return 200, blob, {'ETag': etag}, url


class ArchiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'observer'
        self.clock = Clock()
        self.server = Server()
        self.collect()
        s.initialize(self.root, self.clock)

    def collect(self):
        return o.collect(self.root, '2026-10-20', '2026-10-20', clock=self.clock, fetcher=self.server)

    def cycle(self):
        return s.run_cycle(self.root, clock=self.clock, fetcher=self.server)

    def later(self, hours=1):
        self.clock.value += timedelta(hours=hours)

    def test_adopts_without_rewriting_old_evidence_and_suppresses_early_logon(self):
        original = {p.name: p.read_bytes() for p in (self.root / 'runs').glob('*.json')}
        calls = len(self.server.calls)
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'not_due')
        self.assertEqual(len(self.server.calls), calls)
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / 'runs').glob('*.json')}, original)
        self.assertEqual(report['accepted_taker_windows'], 288)
        self.assertEqual(report['verified_metric_dates'], ['2026-10-20'])
        self.assertEqual(s.initialize(self.root, self.clock)['status'], 'already_initialized')

    def test_duplicate_start_does_not_write_or_fetch(self):
        calls = len(self.server.calls)
        with s.lease(s.directory(self.root)):
            receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'already_running')
        self.assertIsNone(report)
        self.assertEqual(calls, len(self.server.calls))

    def test_os_lock_is_released_after_child_process_death(self):
        manager = str(s.directory(self.root))
        code = ('from pathlib import Path; from deployment.archive_service import lease; import time; '
                f'lock=lease(Path({manager!r})); lock.__enter__(); print("ready",flush=True); time.sleep(20)')
        child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.assertEqual(self.cycle()[0]['result'], 'already_running')
        finally:
            child.terminate()
            child.wait(timeout=10)
            child.stdout.close()
        self.assertFalse(s.process_alive(child.pid))
        self.assertEqual(self.cycle()[0]['result'], 'not_due')

    def test_transient_network_failure_recovers_next_hour_without_same_cycle_retry(self):
        previous_success = s.health(self.root, self.clock)['latest_successful_collection_at_utc']
        self.later()
        self.server.status = (None, b'network timeout')
        before = len(self.server.calls)
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'failed')
        self.assertEqual(len(self.server.calls) - before, 1)
        self.assertEqual(report['runtime_state'], 'retry_pending')
        self.server.status = None
        self.later()
        receipt, report = self.cycle()
        self.assertTrue(receipt['automatic_transient_resume'])
        self.assertEqual(receipt['result'], 'completed')
        self.assertEqual(report['runtime_state'], 'healthy')
        self.assertGreater(report['latest_successful_collection_at_utc'], previous_success)

    def test_transient_retry_budget_is_bounded(self):
        self.server.status = (503, b'maintenance')
        for _ in range(3):
            self.later()
            self.cycle()
        calls = len(self.server.calls)
        self.later()
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'manual_review_required')
        self.assertEqual(calls, len(self.server.calls))
        self.assertEqual(report['runtime_state'], 'manual_review_required')

    def test_restriction_rate_limit_license_and_redirect_never_auto_resume(self):
        for status in (403, 451, 429, 401):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'observer'
                clock, server = Clock(), Server()
                o.collect(root, '2026-10-20', '2026-10-20', clock=clock, fetcher=server)
                s.initialize(root, clock)
                clock.value += timedelta(hours=1)
                server.status = (status, b'restricted')
                s.run_cycle(root, clock=clock, fetcher=server)
                calls = len(server.calls)
                server.status = None
                clock.value += timedelta(hours=1)
                receipt, report = s.run_cycle(root, clock=clock, fetcher=server)
                self.assertEqual(receipt['result'], 'manual_review_required')
                self.assertEqual(len(server.calls), calls)
                self.assertEqual(report['runtime_state'], 'manual_review_required')
        self.assertFalse(s.transient({'stop_reason': 'terms_not_approved', 'events': []}))
        self.assertFalse(s.transient({'stop_reason': 'unexpected_redirect', 'events': []}))

    def test_temporarily_missing_data_recovers_and_stays_retrospective(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'observer'
            name = 'BTCUSDT-metrics-2026-10-20.zip'
            server = Server()
            server.missing.add(name)
            o.collect(root, '2026-10-20', '2026-10-20', clock=self.clock, fetcher=server)
            s.initialize(root, self.clock)
            before = s.health(root, self.clock)
            self.assertEqual(len(before['unresolved_gaps']), 1)
            self.assertEqual(before['runtime_state'], 'healthy')
            server.missing.clear()
            self.later()
            receipt, after = s.run_cycle(root, clock=self.clock, fetcher=server)
            self.assertEqual(receipt['result'], 'completed')
            self.assertEqual(after['unresolved_gaps'], [])
            self.assertEqual(after['accepted_taker_windows'], 288)
            self.assertEqual(after['prospective_prediction_samples'], 0)
            self.assertIsNone(after['official_publication_at_utc'])
            self.assertTrue(after['isolated'])

    def test_shutdown_catchup_checks_all_missing_days_and_reports_real_intervals(self):
        self.later(48)
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'completed')
        self.assertEqual(report['verified_metric_dates'], ['2026-10-20', '2026-10-21'])
        self.assertEqual(report['unresolved_gaps'][0]['date'], '2026-10-22')
        self.assertGreater(report['longest_collection_interval_seconds'], 47 * 3600)
        self.assertEqual(report['long_run_acceptance'], 'not_established')

    def test_dead_collector_lock_recovered_orphan_bytes_remain_unadmitted(self):
        orphan = o.save_object(self.root, b'uncommitted response from crashed collector')
        (self.root / 'collector.lock').write_text('123456')
        s.atomic(s.directory(self.root) / 'running.json', {'pid': 123456, 'started_at_utc': n.iso(self.clock())})
        self.later()
        receipt, report = s.run_cycle(self.root, clock=self.clock, fetcher=self.server, alive=lambda pid: False)
        self.assertEqual(receipt['result'], 'completed')
        self.assertIn('interrupted_cycle', receipt)
        self.assertEqual(receipt['lock_recovery']['recovered_dead_collector_pid'], 123456)
        self.assertIn(orphan, report['orphan_objects'])
        self.assertEqual(report['accepted_taker_windows'], 288)
        self.assertEqual(o.object_bytes(self.root, orphan), b'uncommitted response from crashed collector')

    def test_live_collector_lock_and_unknown_pid_are_not_deleted(self):
        path = self.root / 'collector.lock'
        path.write_text('123456')
        receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=self.server, alive=lambda pid: True)
        self.assertEqual(receipt['result'], 'busy')
        self.assertTrue(path.exists())
        path.write_text('unknown')
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'error')
        self.assertTrue(path.exists())
        self.assertEqual(report['runtime_state'], 'manual_review_required')

    def test_corruption_stops_before_network_and_health_cannot_claim_validity(self):
        state = o.replay(self.root)
        digest = next(iter(state['current'].values()))['body_sha256']
        (self.root / 'objects' / (digest + '.bin')).write_bytes(b'corrupt')
        calls = len(self.server.calls)
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'error')
        self.assertEqual(calls, len(self.server.calls))
        self.assertEqual(report['integrity'], 'unverified')
        self.assertIsNone(report['latest_successful_collection_at_utc'])
        self.assertEqual(report['verified_metric_dates'], [])
        self.assertTrue((s.directory(self.root) / 'health.md').exists())

    def test_complete_tail_deletion_and_snapshot_tampering_are_detected(self):
        self.later()
        self.cycle()
        latest = sorted((self.root / 'runs').glob('*.json'))[-1]
        latest.unlink()
        self.assertEqual(s.health(self.root, self.clock)['integrity'], 'unverified')
        saved = s.directory(self.root) / 'snapshots/baseline/accepted.jsonl'
        saved.write_bytes(b'forged')
        self.assertEqual(s.health(self.root, self.clock)['integrity'], 'unverified')

    def test_receipt_corruption_writes_emergency_evidence_and_health(self):
        self.cycle()
        path = next((s.directory(self.root) / 'cycles').glob('*.json'))
        path.write_bytes(b'bad')
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'error')
        self.assertEqual(report['integrity'], 'unverified')
        self.assertTrue(list((s.directory(self.root) / 'emergency').glob('*.json')))

    def test_operations_error_requires_review_after_repair(self):
        path = self.root / 'collector.lock'
        path.write_text('invalid')
        self.cycle()
        path.unlink()
        self.assertEqual(self.cycle()[0]['result'], 'error')
        s.review(self.root, 'Removed malformed lock after manual process and integrity review', self.clock)
        self.assertEqual(self.cycle()[0]['result'], 'not_due')

    def test_health_is_offline_and_emits_json_and_markdown_with_stale_and_gap_alarms(self):
        self.later(96)
        calls = len(self.server.calls)
        report = s.health(self.root, self.clock)
        s.write_health(self.root, report)
        self.assertEqual(calls, len(self.server.calls))
        self.assertIn('no_completed_collection_for_over_3_hours', report['anomalies'])
        self.assertTrue(report['overdue_gaps'])
        self.assertEqual(json.loads((s.directory(self.root) / 'health.json').read_bytes()), report)
        self.assertIn('Long-run acceptance remains unestablished', (s.directory(self.root) / 'health.md').read_text())

    def test_clock_rollback_is_not_treated_as_not_due(self):
        self.clock.value -= timedelta(hours=1)
        receipt, report = self.cycle()
        self.assertEqual(receipt['result'], 'error')
        self.assertIn('Clock moved backwards', receipt['error'])
        self.assertEqual(report['runtime_state'], 'manual_review_required')

    def test_hourly_trigger_waits_for_full_cooldown_instead_of_skipping_an_hour(self):
        end = n.utc(o.replay(self.root)['runs'][-1]['finished_at_utc'])
        self.clock.value = end + timedelta(seconds=3594)
        calls = len(self.server.calls)
        waits = []
        def sleep(seconds):
            waits.append(seconds)
            self.assertEqual(len(self.server.calls), calls)
            self.clock.value += timedelta(seconds=seconds)
        receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=self.server, sleep=sleep)
        self.assertEqual(receipt['result'], 'completed')
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 5)
        run = o.replay(self.root)['runs'][-1]
        self.assertGreaterEqual(n.utc(run['started_at_utc']) - end, timedelta(hours=1))

    def test_early_trigger_does_not_wait_and_undersleep_does_not_fetch(self):
        end = n.utc(o.replay(self.root)['runs'][-1]['finished_at_utc'])
        calls = len(self.server.calls)
        def no_sleep(seconds):
            self.fail('Early logon must not wait')
        self.assertEqual(s.run_cycle(self.root, clock=self.clock, fetcher=self.server,
                                    sleep=no_sleep)[0]['result'], 'not_due')
        self.clock.value = end + timedelta(seconds=3594)
        self.assertEqual(s.run_cycle(self.root, clock=self.clock, fetcher=self.server,
                                    sleep=lambda seconds: None)[0]['result'], 'not_due')
        self.assertEqual(len(self.server.calls), calls)

    def test_interruption_keeps_phase_without_claiming_cause(self):
        manager = s.directory(self.root)
        s.atomic(manager / 'running.json', {'pid': 123456, 'started_at_utc': n.iso(self.clock()),
                                         'phase': 'collecting'})
        self.later()
        receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=self.server, alive=lambda pid: False)
        self.assertEqual(receipt['interrupted_cycle']['phase'], 'collecting')
        self.assertTrue(receipt['interruption_cause'].startswith('unknown'))
        self.assertEqual(receipt['trigger_kind'], 'unknown')

    def test_request_phase_is_persisted_before_fetch(self):
        self.later()
        phases = []
        def fetch(url, headers):
            intent = json.loads((s.directory(self.root)/'running.json').read_bytes())
            phases.append(intent['phase'])
            self.assertEqual(intent['source_url'], url)
            return self.server(url, headers)
        receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=fetch)
        self.assertEqual(receipt['result'], 'completed')
        self.assertTrue(phases)
        self.assertEqual(set(phases), {'request'})

    def test_diagnostic_write_failure_requires_review_without_network(self):
        self.later()
        original = s.atomic
        calls = len(self.server.calls)
        def atomic(path, value):
            if value.get('phase') == 'request':
                raise OSError('disk unavailable')
            return original(path, value)
        with mock.patch.object(s, 'atomic', side_effect=atomic):
            receipt, _ = self.cycle()
        self.assertEqual(receipt['result'], 'error')
        self.assertIn('Cannot persist request diagnostics', receipt['error'])
        self.later()
        self.assertEqual(self.cycle()[0]['result'], 'error')
        self.assertEqual(calls, len(self.server.calls))


if __name__ == '__main__':
    unittest.main()
