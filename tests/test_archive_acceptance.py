"""Offline acceptance reporting over real-format evidence, with a controlled clock."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from deployment import archive_acceptance as a
from deployment import archive_service as s
from research import archive_observer as o
from research import archive_normalizer as n
from test_archive_service import Clock, Server


class ArchiveAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'observer'
        self.clock, self.server = Clock(), Server()
        o.collect(self.root, '2026-10-20', '2026-10-20', clock=self.clock, fetcher=self.server)
        s.initialize(self.root, self.clock)
        self.enabled = self.clock()
        s.atomic(s.directory(self.root) / 'deployment.json', {
            'schema_version': 1, 'task_name': 'quant-lab-archive-observer',
            'cadence_seconds': 3600, 'enabled_at_utc': n.iso(self.enabled)})

    def report(self, days=7):
        return a.report(self.root, days=days, clock=self.clock)

    def files(self):
        return {str(p.relative_to(self.root.parent)): p.read_bytes()
                for p in self.root.parent.rglob('*') if p.is_file()}

    def test_read_only_report_separates_skips_from_collection_and_never_infers_trigger(self):
        s.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        self.clock.value += timedelta(hours=1)
        s.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        original, calls = self.files(), len(self.server.calls)
        r = self.report()
        self.assertEqual(r['stage'], 'not_due')
        self.assertEqual(r['operation_outcomes'], {'not_due': 1, 'completed': 1})
        self.assertEqual(r['committed_collection_runs'], 1)
        self.assertEqual(r['trigger_counts'], {'unknown': 2})
        self.assertIsNone(r['task_execution_success_rate'])
        self.assertIsNone(r['schedule_punctuality_rate'])
        self.assertEqual(original, self.files())
        self.assertEqual(calls, len(self.server.calls))

    def test_exact_due_dates_use_deployment_not_earlier_manual_baseline(self):
        monitor = self.root.with_name('archive_observer_monitor')
        monitor.mkdir()
        s.atomic(monitor / 'baseline.json', {'operational_started_at_utc': n.iso(self.enabled-timedelta(minutes=30))})
        for days in (7, 28):
            self.clock.value = self.enabled + timedelta(days=days, seconds=-1)
            self.assertEqual(self.report(days)['stage'], 'not_due')
            self.clock.value = self.enabled + timedelta(days=days, seconds=2)
            r = self.report(days)
            self.assertEqual(r['stage'], 'due_for_manual_review')
            self.assertEqual(r['long_run_acceptance'], 'not_established')
            self.assertEqual(r['baseline_conflict']['difference_seconds'], 1800)
            self.assertIsNone(r['deployment_disk_growth_bytes'])

    def test_network_failure_does_not_reset_successful_collection_gap(self):
        self.clock.value += timedelta(hours=1)
        self.server.status = (None, b'network timeout')
        s.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        self.clock.value += timedelta(hours=3)
        r = self.report()
        self.assertEqual(r['failed_collection_runs'], 1)
        self.assertEqual(r['successful_collection_runs'], 0)
        self.assertEqual(r['operation_normal_completion_rate'], 0)
        self.assertGreater(r['longest_successful_collection_interval_seconds'], 3*3600)
        self.assertTrue(r['collection_gaps_over_3_hours'])

    def test_corrupt_old_snapshot_cannot_hide_behind_latest_healthy_snapshot(self):
        s.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        first_receipt = s.receipts(s.directory(self.root))[0][0]
        self.clock.value += timedelta(hours=1)
        s.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        first = s.directory(self.root)/'snapshots'/first_receipt['snapshot']
        (first/'accepted.jsonl').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.report()

    def test_active_or_interrupted_writer_is_pending_not_corrupt(self):
        (self.root/'collector.lock').write_text('123456')
        r = self.report()
        self.assertEqual(r['report_state'], 'pending_active_or_interrupted_cycle')
        self.assertEqual(r['integrity'], 'unverified')
        self.assertNotIn('metric_rows', r)

    def test_report_clock_before_latest_receipt_is_rejected(self):
        self.clock.value += timedelta(hours=1)
        s.run_cycle(self.root, clock=self.clock, fetcher=self.server)
        self.clock.value = self.enabled + timedelta(minutes=30)
        with self.assertRaisesRegex(ValueError, 'report clock'):
            self.report()

    def test_invalid_deployment_and_future_start_are_rejected(self):
        self.clock.value = self.enabled - timedelta(hours=1)
        with self.assertRaises(ValueError):
            self.report()
        self.clock.value = self.enabled
        s.atomic(s.directory(self.root)/'deployment.json', {
            'schema_version': 1, 'task_name': 'other', 'cadence_seconds': 3600,
            'enabled_at_utc': n.iso(self.enabled)})
        with self.assertRaises(ValueError):
            self.report()
        with self.assertRaises(ValueError):
            self.report(14)


if __name__ == '__main__':
    unittest.main()
