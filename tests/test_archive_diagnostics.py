"""Operational projections must not create evidence or relax source policy."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from deployment import archive_diagnostics as d
from deployment import archive_task_history as h
from research import archive_normalizer as n


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 24, tzinfo=n.UTC)

    def test_execution_context_never_records_proxy_credentials_or_urls(self):
        with mock.patch.object(d, 'getproxies', return_value={'https': 'https://private:password@example.org'}):
            result = d.execution_context()
        self.assertEqual(result['proxy_schemes'], ['https'])
        self.assertNotIn('password', str(result))
        self.assertNotIn('example.org', str(result))
        self.assertIn('python_version', result)

    def test_certificate_inventory_failure_does_not_stop_collection(self):
        with mock.patch.object(d.ssl, 'create_default_context', side_effect=OSError('private-path')):
            result = d.execution_context()
        self.assertEqual(result['certificate_store_error_type'], 'OSError')
        self.assertNotIn('private-path', str(result))

    def test_failed_runs_do_not_reset_open_success_interval(self):
        runs = [{'finished_at_utc': n.iso(self.start), 'stop_reason': None},
                {'finished_at_utc': n.iso(self.start + timedelta(hours=4)), 'stop_reason': 'failure'}]
        gaps = d.successful_intervals(runs, self.start, self.start + timedelta(hours=5))
        self.assertEqual(gaps[-1]['seconds'], 18000)
        self.assertTrue(gaps[-1]['open_interval'])
        self.assertEqual(d.successful_intervals([], self.start, self.start + timedelta(hours=3))[-1]['seconds'], 10800)
        with self.assertRaises(ValueError):
            d.successful_intervals([], self.start, self.start - timedelta(seconds=1))

    def test_saved_failure_classification_does_not_expose_raw_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'objects').mkdir()
            for text, expected in [('CERTIFICATE_VERIFY_FAILED private-path', 'tls_certificate_validation'),
                                   ('getaddrinfo failed', 'dns_resolution'), ('timed out', 'network_timeout'),
                                   ('unknown', 'network_other')]:
                blob = text.encode()
                digest = n.sha(blob)
                (root / 'objects' / (digest + '.bin')).write_bytes(blob)
                run = {'stop_reason': 'access_or_network_failure', 'events': [{
                    'status': None, 'url': 'https://example.org/public',
                    'final_url': 'https://example.org/public', 'body_sha256': digest}]}
                result = d.failure(root, run)
                self.assertEqual(result['category'], expected)
                self.assertEqual(result['host'], 'example.org')
                self.assertNotIn('private-path', str(result))
            for status, expected in [(403, 'access_restriction'), (429, 'access_restriction'),
                                     (503, 'server_transient')]:
                run['events'][0]['status'] = status
                self.assertEqual(d.failure(root, run)['category'], expected)

    def event(self, record, event_id, result='', task=h.TASK, provider=h.PROVIDER):
        return f'''<Event xmlns="{h.NS['e']}"><System><Provider Name="{provider}"/>
        <EventID>{event_id}</EventID><EventRecordID>{record}</EventRecordID>
        <TimeCreated SystemTime="2026-10-24T01:00:00Z"/></System><EventData>
        <Data Name="TaskName">{task}</Data><Data Name="InstanceId">instance-1</Data>
        <Data Name="ResultCode">{result}</Data></EventData></Event>'''

    def read(self, xml):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.xml'
            path.write_text(xml, encoding='utf-8')
            original = path.read_bytes()
            result = h.read(path, self.start, self.start + timedelta(days=1))
            self.assertEqual(path.read_bytes(), original)
            return result

    def test_trigger_types_exit_codes_dedup_and_scope_are_evidence_only(self):
        xml = '<Events>' + ''.join(self.event(i, eid, '2') for i, eid in enumerate((107, 110, 119, 201), 1))
        xml += self.event(1, 107) + self.event(5, 107, task='\\other') + self.event(6, 107, provider='Other') + '</Events>'
        r = self.read(xml)
        self.assertEqual([e['trigger_kind'] for e in r['events']], ['time', 'user', 'logon', 'unknown'])
        self.assertEqual(r['events'][-1]['action_result_code'], '2')
        self.assertIsNone(r['task_execution_success_rate'])
        self.assertEqual(r['coverage'], 'partial_or_unknown')

    def test_conflicting_records_invalid_xml_and_entities_are_rejected(self):
        for xml in ('<Events>'+self.event(1,107)+self.event(1,119)+'</Events>',
                    '<broken>', '<Other/>', '<!DOCTYPE Events [<!ENTITY x "y">]><Events/>',
                    self.event(1,107).replace('<Data Name="ResultCode">', '<Data Name="TaskName">'),
                    self.event(1,107).replace('SystemTime=', 'Missing=')):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                self.read(xml)

    def test_empty_history_is_not_complete_and_future_events_are_excluded(self):
        self.assertEqual(self.read('<Events/>')['coverage'], 'partial_or_unknown')
        self.assertEqual(self.read('<Events>'+self.event(1,107).replace('2026-10-24', '2026-10-25')+'</Events>')['events'], [])
