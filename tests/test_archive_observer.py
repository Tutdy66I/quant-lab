"""Failure/integrity tests for the independent archive observer; no live network."""
from datetime import datetime,timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from research import archive_observer as o
from research import archive_normalizer as n


class Clock:
    def __init__(self):
        self.value=datetime(2026,10,23,12,tzinfo=n.UTC)

    def __call__(self):
        self.value+=timedelta(milliseconds=10)
        return self.value


class Server:
    def __init__(self):
        self.calls=[]
        self.overrides={}
        self.bodies={}

    def __call__(self,url,headers):
        self.calls.append((url,headers))
        name='terms' if url==o.TERMS else url.rsplit('/',1)[-1]
        if name in self.overrides:
            value=self.overrides[name]
            if isinstance(value,Exception):
                raise value
            return value[0],value[1],{},url
        if name in self.bodies:
            body=self.bodies[name]
        elif name=='terms':
            body=o.APPROVED_TERMS.read_bytes()
        else:
            body=(n.ROOT/'data/archive_coverage/binance_um_btcusdt_v1/raw'/name).read_bytes()
        etag='"'+n.sha(body)+'"'
        if headers.get('If-None-Match')==etag:
            return 304,b'',{'ETag':etag},url
        return 200,body,{'ETag':etag},url


class ArchiveObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'observer'
        self.clock=Clock()
        self.server=Server()

    def collect(self,end='2026-10-20',**kwargs):
        return o.collect(self.root,'2026-10-20',end,clock=self.clock,fetcher=self.server,**kwargs)

    def test_incremental_collection_deduplication_first_seen_and_publication_unknown(self):
        self.assertIsNone(self.collect()['stop_reason'])
        first=o.snapshot(self.root,'2026-10-20')
        first_seen=first['archives'][0]['first_observed_at_utc']
        objects=len(list((self.root/'objects').glob('*.bin')))
        result=self.collect()
        self.assertEqual(result['network_requests'],1)  # conditional terms check only
        self.assertEqual(len(list((self.root/'objects').glob('*.bin'))),objects+1) # retained empty 304 body
        self.collect('2026-10-21')
        result=o.snapshot(self.root,'2026-10-21')
        self.assertEqual(result['successful_metric_archives'],2)
        self.assertEqual(result['accepted_taker_windows'],576)
        self.assertEqual(result['archives'][0]['first_observed_at_utc'],first_seen)
        self.assertIsNone(result['official_publication_evidence'])
        self.assertEqual(result['prospective_prediction_samples'],0)
        self.assertFalse(result['statistics_module_enabled'])
        self.assertTrue(all(n.utc(r['research_available_at_utc'])>=n.utc(r['first_observed_at_utc']) for r in result['accepted']))

    def test_unchanged_conditional_responses_never_reset_first_seen(self):
        self.collect()
        before=o.snapshot(self.root,'2026-10-20')
        self.collect(refresh=True)
        after=o.snapshot(self.root,'2026-10-20')
        self.assertEqual(after['archives'][0]['first_observed_at_utc'],before['archives'][0]['first_observed_at_utc'])
        self.assertEqual(after['revisions'],[])
        self.assertEqual(after['accepted_taker_windows'],288)
        self.assertTrue(any(v==304 for v in after['last_attempt_status'].values()))

    def test_revision_is_retained_and_new_metric_version_is_not_admitted(self):
        self.collect()
        name='BTCUSDT-metrics-2026-10-20.zip'
        original=(n.ROOT/'data/archive_coverage/binance_um_btcusdt_v1/raw'/name).read_bytes()
        buffer=io.BytesIO(original)
        with zipfile.ZipFile(buffer,'a') as archive:
            archive.comment=b'publisher revision'
        revised=buffer.getvalue()
        self.server.bodies[name]=revised
        self.server.bodies[name+'.CHECKSUM']=(n.sha(revised)+'  '+name+'\n').encode()
        self.collect(refresh=True)
        result=o.snapshot(self.root,'2026-10-20')
        self.assertEqual(result['successful_metric_archives'],1)
        self.assertEqual(result['accepted_taker_windows'],0)
        self.assertEqual(len(result['source_versions'][name]),2)
        self.assertEqual(len(result['revisions']),2) # zip and publisher checksum
        self.assertEqual(o.object_bytes(self.root,n.sha(original)),original)
        self.assertIn(n.TAKER,result['isolated'][0]['fields'])

    def test_missing_archive_is_a_gap_and_can_arrive_later(self):
        name='BTCUSDT-metrics-2026-10-20.zip'
        self.server.overrides[name]=(404,b'not yet published')
        self.assertIsNone(self.collect()['stop_reason'])
        result=o.snapshot(self.root,'2026-10-20')
        self.assertEqual(len(result['gaps']),1)
        self.assertIn(name,result['unavailable_objects'])
        del self.server.overrides[name]
        self.collect(refresh=True)
        self.assertEqual(o.snapshot(self.root,'2026-10-20')['gaps'],[])

    def test_restrictions_and_network_failures_stop_without_automatic_retry(self):
        for status in (403,451,429,None):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)/'observer'
                server=Server()
                server.overrides['terms']=(status,b'access failure')
                result=o.collect(root,'2026-10-20','2026-10-20',fetcher=server,clock=self.clock)
                self.assertEqual(result['stop_reason'],'access_or_network_failure')
                self.assertEqual(len(server.calls),1)
                again=o.collect(root,'2026-10-20','2026-10-20',fetcher=server,clock=self.clock)
                self.assertEqual(again['network_requests'],0)
                self.assertEqual(len(server.calls),1)
                state=o.replay(root)
                self.assertEqual(o.object_bytes(root,state['runs'][0]['events'][0]['body_sha256']),b'access failure')

    def test_changed_terms_stop_before_market_data(self):
        self.server.overrides['terms']=(200,b'new unreviewed license')
        self.assertEqual(self.collect()['stop_reason'],'terms_not_approved')
        self.assertEqual(len(self.server.calls),1)

    def test_publisher_mismatch_is_preserved_but_never_admitted(self):
        name='BTCUSDT-metrics-2026-10-20.zip.CHECKSUM'
        self.server.bodies[name]=b'0'*64+b'  BTCUSDT-metrics-2026-10-20.zip\n'
        self.assertEqual(self.collect()['stop_reason'],'publisher_integrity_failure')
        result=o.snapshot(self.root,'2026-10-20')
        self.assertEqual(result['accepted_taker_windows'],0)
        self.assertTrue(result['publisher_verification_failures'])
        self.assertEqual(len(result['gaps']),1)

    def test_invalid_304_is_a_preserved_failure_not_a_success(self):
        self.server.overrides['terms']=(304,b'')
        self.assertEqual(self.collect()['stop_reason'],'invalid_conditional_response')
        self.assertEqual(o.replay(self.root)['current'],{})

    def test_as_of_and_export_do_not_change_after_future_observations(self):
        self.collect()
        cutoff=o.replay(self.root)['runs'][-1]['finished_at_utc']
        before=o.snapshot(self.root,'2026-10-21',cutoff)
        output=Path(self.temp.name)/'snapshot'
        o.export_snapshot(self.root,'2026-10-21',output)
        self.collect('2026-10-21')
        self.assertEqual(o.snapshot(self.root,'2026-10-21',cutoff),before)
        self.assertEqual(o.verify_snapshot(self.root,output)['verified_snapshot_files'],3)
        (output/'accepted.jsonl').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'Snapshot replay mismatch'):
            o.verify_snapshot(self.root,output)

    def test_corrupt_objects_and_run_chain_are_rejected(self):
        self.collect()
        path=next((self.root/'objects').glob('*.bin'))
        original=path.read_bytes()
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
            o.replay(self.root)
        path.write_bytes(original)
        run=next((self.root/'runs').glob('*.json'))
        run.write_bytes(run.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'Run sequence or SHA-256 mismatch'):
            o.replay(self.root)

    def test_single_writer_request_budget_and_future_dates(self):
        with o.writer(self.root):
            with self.assertRaises(FileExistsError):
                self.collect()
        result=self.collect(max_requests=3)
        self.assertEqual(result['network_requests'],3)
        self.assertEqual(o.snapshot(self.root,'2026-10-20')['accepted_taker_windows'],0)
        self.collect()
        self.assertEqual(o.snapshot(self.root,'2026-10-20')['accepted_taker_windows'],288)
        with self.assertRaises(ValueError):
            self.collect('2026-10-23')

    def test_orphans_are_detected_and_do_not_establish_first_observation(self):
        self.collect()
        digest=o.save_object(self.root,b'crash orphan')
        state=o.replay(self.root)
        self.assertEqual(state['orphan_objects'],[digest])
        self.assertFalse(any(digest in versions for versions in state['versions'].values()))

    def test_reversion_keeps_original_first_observed_time_and_both_transitions(self):
        self.collect()
        name='BTCUSDT-metrics-2026-10-20.zip'
        initial=o.snapshot(self.root,'2026-10-20')['archives'][0]['first_observed_at_utc']
        original=(n.ROOT/'data/archive_coverage/binance_um_btcusdt_v1/raw'/name).read_bytes()
        buffer=io.BytesIO(original)
        with zipfile.ZipFile(buffer,'a') as archive:
            archive.comment=b'temporary publisher revision'
        self.server.bodies[name]=buffer.getvalue()
        self.server.bodies[name+'.CHECKSUM']=(n.sha(buffer.getvalue())+'  '+name+'\n').encode()
        self.collect(refresh=True)
        self.server.bodies.clear()
        self.collect(refresh=True)
        result=o.snapshot(self.root,'2026-10-20')
        self.assertEqual(result['archives'][0]['first_observed_at_utc'],initial)
        self.assertEqual(len(result['revisions']),4)
        self.assertEqual(result['accepted_taker_windows'],288)

    def test_unexpected_fetch_failure_is_recorded_and_redirects_are_not_followed(self):
        self.server.overrides['terms']=ConnectionError('connection interrupted')
        self.assertEqual(self.collect()['stop_reason'],'access_or_network_failure')
        self.assertEqual(len(o.replay(self.root)['runs']),1)
        self.assertIsNone(o.NoRedirect().redirect_request(None,None,302,'redirect',{},'https://other.invalid'))
        with self.assertRaises(ValueError):
            o.url_for('../private-account')

    def test_clock_rollback_is_rejected_without_a_new_network_observation(self):
        self.collect()
        requests=len(self.server.calls)
        self.clock.value-=timedelta(days=1)
        with self.assertRaisesRegex(ValueError,'clock moved backwards'):
            self.collect()
        self.assertEqual(len(self.server.calls),requests)
        self.assertEqual(len(o.replay(self.root)['runs']),1)

    def test_missing_object_is_retried_after_one_hour_without_refetching_known_archives(self):
        name='BTCUSDT-metrics-2026-10-20.zip'
        self.server.overrides[name]=(404,b'not published')
        self.collect()
        del self.server.overrides[name]
        self.clock.value+=timedelta(hours=2)
        result=self.collect()
        self.assertEqual(result['network_requests'],2) # terms and the absent ZIP
        self.assertEqual(o.snapshot(self.root,'2026-10-20')['accepted_taker_windows'],288)

    def test_real_committed_observations_and_snapshot_replay_offline(self):
        root=n.ROOT/'data/archive_observation/binance_um_btcusdt_v1'
        output=n.ROOT/'data/archive_observation/binance_um_btcusdt_v1_snapshot'
        self.assertEqual(o.verify_snapshot(root,output)['verified_snapshot_files'],3)
        result=o.snapshot(root,'2026-10-22')
        self.assertEqual(result['runs_visible'],2)
        self.assertEqual(result['successful_metric_archives'],2)
        self.assertEqual(result['accepted_taker_windows'],576)
        self.assertEqual([x['date'] for x in result['gaps']],['2026-10-22'])
        self.assertEqual(result['publisher_verification_failures'],{})
        self.assertEqual(result['revisions'],[])
        self.assertEqual(result['orphan_objects'],[])
        self.assertTrue(all(r['first_observed_at_utc'].startswith('2026-10-23') for r in result['archives']))
