"""Isolated-instance tests: no real network and no production paths."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from deployment import archive_instance as i
from deployment import archive_acceptance as acceptance
from deployment import archive_service as s
from research import archive_normalizer as n
from research import archive_observer as o


class IsolatedInstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.gate = self.base / 'global-source.json'
        self.root = self.base / 'new-observer'
        self.clock = lambda: datetime(2026, 11, 6, 4, tzinfo=n.UTC)
        i.initialize_source(self.gate)

    def initialize(self, root=None, task='quant-lab-archive-new'):
        return i.initialize_empty(root or self.root, self.gate, task, '2026-11-05', self.clock)

    def test_empty_repeat_and_independent_identity(self):
        one = self.initialize()
        again = self.initialize()
        self.assertEqual(one['instance_id'], again['instance_id'])
        other = self.base / 'another'
        two = self.initialize(other, 'quant-lab-archive-other')
        self.assertNotEqual(one['instance_id'], two['instance_id'])
        self.assertNotEqual(s.directory(self.root), s.directory(other))
        state, history = s.checked(self.root)
        self.assertEqual(state['runs'], [])
        self.assertEqual(history, [])
        self.assertIsNone(state['head'])
        info = i.metadata(self.root)
        self.assertIsNone(info['enabled_at_utc'])
        self.assertIsNone(info['first_observed_at_utc'])
        self.assertEqual(info['research_admission'], 'insufficient_evidence')
        self.assertIsNone(o.snapshot(self.root, '2026-11-05')['observer_started_at_utc'])

    def test_partial_and_legacy_task_rejected(self):
        self.root.mkdir()
        with self.assertRaises(ValueError):
            self.initialize()
        with self.assertRaises(ValueError):
            i.initialize_empty(self.base / 'other', self.gate,
                               'quant-lab-archive-observer', '2026-11-05', self.clock)

    def test_task_identity_cannot_be_reused_and_missing_reservation_blocks(self):
        self.initialize()
        with self.assertRaises(ValueError):
            self.initialize(self.base / 'other', 'quant-lab-archive-new')
        reservation = Path(str(self.gate) + '.tasks') / 'quant-lab-archive-new.json'
        reservation.unlink()
        fetch = mock.Mock(side_effect=AssertionError('request forbidden'))
        receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=fetch)
        self.assertEqual(receipt['result'], 'error')
        fetch.assert_not_called()

    def test_existing_old_instance_evidence_is_never_modified(self):
        old = self.base / 'old-observer'
        old.mkdir()
        old_service = s.directory(old)
        old_service.mkdir()
        (old / 'collector.lock').write_bytes(b'old lock')
        (old_service / 'running.json').write_bytes(b'old interrupted evidence')
        before = {str(p.relative_to(self.base)): p.read_bytes()
                  for folder in (old, old_service) for p in folder.rglob('*') if p.is_file()}
        self.initialize()
        receipt, _ = s.run_cycle(self.root, clock=self.clock,
                                 fetcher=mock.Mock(side_effect=AssertionError('network forbidden')))
        self.assertEqual(receipt['result'], 'manual_review_required')
        after = {str(p.relative_to(self.base)): p.read_bytes()
                 for folder in (old, old_service) for p in folder.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_absent_or_changed_gate_never_fetches(self):
        self.initialize()
        for replacement in (None, b'{}', json.dumps({**i.BLOCKED, 'state': 'allowed'}).encode()):
            if self.gate.exists():
                self.gate.unlink()
            if replacement is not None:
                self.gate.write_bytes(replacement)
            fetch = mock.Mock(side_effect=AssertionError('real request forbidden'))
            receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=fetch)
            self.assertIn(receipt['result'], ('error', 'manual_review_required'))
            fetch.assert_not_called()

    def test_intact_gate_blocks_new_budget_without_network(self):
        self.initialize()
        fetch = mock.Mock(side_effect=AssertionError('real request forbidden'))
        receipt, health = s.run_cycle(self.root, clock=self.clock, fetcher=fetch)
        self.assertEqual(receipt['result'], 'manual_review_required')
        self.assertEqual(health['source_access'], 'blocked')
        self.assertEqual(s.checked(self.root)[0]['runs'], [])
        fetch.assert_not_called()

    def test_baseline_identity_tampering_is_rejected(self):
        self.initialize()
        path = s.directory(self.root) / 'instance.json'
        info = json.loads(path.read_bytes())
        info['task_name'] = 'quant-lab-archive-other'
        path.write_bytes(n.json_bytes(info))
        with self.assertRaises(ValueError):
            s.checked(self.root)

    def test_simulated_admission_change_stops_before_second_request(self):
        self.initialize()
        fetch = mock.Mock(return_value=(200, o.APPROVED_TERMS.read_bytes(), {}, o.TERMS))
        # Mock only: the shipped admission has no permit path.
        with mock.patch.object(i, 'prohibit_network', side_effect=[None, None, ValueError('changed')]):
            receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=fetch)
        self.assertEqual(receipt['result'], 'error')
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(s.checked(self.root)[0]['runs'], [])

    def test_mocked_historical_archive_is_marked_retrospective(self):
        from test_archive_service import Server
        i.initialize_empty(self.root, self.gate, 'quant-lab-archive-new', '2026-10-20', self.clock)
        fake = Server()
        with mock.patch.object(i, 'prohibit_network', return_value=None):
            receipt, _ = s.run_cycle(self.root, clock=self.clock, fetcher=fake)
        self.assertEqual(receipt['result'], 'completed')
        summary = o.snapshot(self.root, '2026-10-20')
        self.assertTrue(summary['archives'])
        self.assertEqual(summary['archives'][0]['acquisition_kind'], 'retrospective_archive_download')
        self.assertIsNotNone(summary['observer_started_at_utc'])

    def test_acceptance_requires_instance_specific_activation_event(self):
        info = self.initialize()
        task = 'quant-lab-archive-new'
        events = self.base / 'events.xml'
        events.write_text('''<Events xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
<Event><System><Provider Name="Microsoft-Windows-TaskScheduler"/><EventID>107</EventID>
<EventRecordID>17</EventRecordID><TimeCreated SystemTime="2026-11-06T04:00:00Z"/></System>
<EventData><Data Name="TaskName">\\quant-lab-archive-new</Data></EventData></Event></Events>''', encoding='utf-8')
        deployment = {'schema_version': 1, 'task_name': task, 'instance_id': info['instance_id'],
                      'cadence_seconds': 3600, 'enabled_at_utc': '2026-11-06T04:00:00Z',
                      'activation_evidence': {'record_id': 17, 'scheduler_sha256': n.sha(events.read_bytes())}}
        (s.directory(self.root) / 'deployment.json').write_bytes(n.json_bytes(deployment))
        now = lambda: datetime(2026, 11, 7, 4, tzinfo=n.UTC)
        with self.assertRaises(ValueError):
            acceptance.report(self.root, days=7, clock=now)
        for days in (7, 28):
            report = acceptance.report(self.root, days=days, clock=now, scheduler_events=events)
            self.assertEqual(report['stage'], 'not_due')
            self.assertEqual(report['successful_collection_runs'], 0)
            self.assertEqual(report['long_run_acceptance'], 'not_established')
        deployment['task_name'] = 'quant-lab-archive-other'
        (s.directory(self.root) / 'deployment.json').write_bytes(n.json_bytes(deployment))
        with self.assertRaises(ValueError):
            acceptance.report(self.root, days=7, clock=now, scheduler_events=events)

    @unittest.skipUnless(os.name == 'nt', 'Windows task plan')
    def test_new_task_plan_only_no_install(self):
        script = Path(__file__).resolve().parents[1] / 'deployment' / 'archive-service.ps1'
        args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                '-PythonExe', sys.executable, '-DataRoot', str(self.root),
                '-TaskName', 'quant-lab-archive-new']
        plan = subprocess.run(args + ['-Action', 'Plan'], capture_output=True, text=True, timeout=30)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn('quant-lab-archive-new', plan.stdout)
        denied = subprocess.run(args + ['-Action', 'Install', '-ConfirmEnable', '-PreviousCollectorStopped'],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('preview only', denied.stderr)


if __name__ == '__main__':
    unittest.main()
