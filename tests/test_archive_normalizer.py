"""Hermetic checks of offline normalization, isolation, provenance and time barriers."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from research import archive_normalizer as normalizer


class ArchiveNormalizerTests(unittest.TestCase):
    AT = '2026-10-22T09:10:00+00:00'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory/'source'
        shutil.copytree(normalizer.SOURCE, self.source)

    def derive(self):
        return normalizer.derive(self.source, self.AT)

    def rewrite_receipt(self, name, blob):
        (self.source/name).write_bytes(blob)
        path = self.source/'receipts.json'
        receipts = json.loads(path.read_bytes())
        for r in receipts:
            if r['file'] == name:
                r.update(sha256=normalizer.sha(blob), bytes=len(blob))
        path.write_bytes(normalizer.json_bytes(receipts))

    def test_only_exact_verified_field_versions_accepted_and_unknowns_isolated(self):
        payload = self.derive()
        rows = [json.loads(line) for line in payload['accepted.jsonl'].splitlines()]
        quarantine = [json.loads(line) for line in payload['quarantine.jsonl'].splitlines()]
        self.assertEqual(len(rows), 576)
        self.assertEqual({r['field'] for r in rows}, {normalizer.TAKER})
        self.assertEqual({r['source_label'][:10] for r in rows}, {'2026-06-24','2026-06-25'})
        self.assertEqual(len(quarantine), 2484)
        funding = [r for r in quarantine if 'fundingRate' in r['file']]
        self.assertEqual(len(funding), 180)
        self.assertTrue(all(r['research_available_at_utc'] is None for r in quarantine))
        self.assertTrue(all('sum_open_interest' in r['raw_values'] for r in quarantine if 'metrics' in r['file']))

    def test_every_time_is_separate_no_backfilled_research_availability(self):
        rows = [json.loads(line) for line in self.derive()['accepted.jsonl'].splitlines()]
        for row in rows:
            start = normalizer.utc(row['window_start_utc'])
            self.assertEqual(normalizer.utc(row['event_time_utc']),start+timedelta(minutes=5))
            self.assertEqual(row['window_end_utc'],row['event_time_utc'])
            self.assertIsNone(row['published_at_utc'])
            self.assertEqual(row['research_available_at_utc'],self.AT)
            self.assertGreaterEqual(normalizer.utc(row['evidence_collected_at_utc']),normalizer.utc(row['collected_at_utc']))
            self.assertEqual(row['provenance'][0]['raw_value'],row['value'])
        with self.assertRaisesRegex(ValueError,'semantic verification'):
            normalizer.derive(self.source,'2026-06-26T00:00:00+00:00')
        receipts_path = self.source/'receipts.json'
        receipts = json.loads(receipts_path.read_bytes())
        receipts[0]['collected_at_utc'] = '2026-10-23T00:00:00+00:00'
        receipts_path.write_bytes(normalizer.json_bytes(receipts))
        with self.assertRaisesRegex(ValueError,'precedes collection'):
            self.derive()

    def test_publisher_checksum_required_even_if_receipt_consistent(self):
        blob = b'0'*64+b'  BTCUSDT-metrics-2026-06-24.zip\n'
        self.rewrite_receipt('raw/BTCUSDT-metrics-2026-06-24.zip.CHECKSUM',blob)
        with self.assertRaisesRegex(ValueError,'Publisher SHA-256'):
            self.derive()

    def test_revised_zip_with_valid_publisher_checksum_stays_quarantined(self):
        name = 'BTCUSDT-metrics-2026-06-24.zip'
        path = self.source/'raw'/name
        with zipfile.ZipFile(path,'a') as archive:
            archive.comment = b'new publisher revision, same CSV'
        blob = path.read_bytes()
        self.rewrite_receipt('raw/'+name,blob)
        self.rewrite_receipt('raw/'+name+'.CHECKSUM',(normalizer.sha(blob)+'  '+name+'\n').encode())
        result = self.derive()
        self.assertEqual(json.loads(result['statistics.json'])['count'],288)
        quarantine = [json.loads(line) for line in result['quarantine.jsonl'].splitlines()]
        revised = [r for r in quarantine if r['file'] == name]
        self.assertEqual(len(revised),288)
        self.assertTrue(all(r['reason'] == 'unverified_date_or_version' for r in revised))
        self.assertTrue(all(normalizer.TAKER in r['raw_values'] for r in revised))
        self.assertEqual(len(json.loads(result['quality.json'])['accepted_scope_missing_windows']),288)

    def test_raw_corruption_rejected_before_any_output(self):
        (self.source/'raw/BTCUSDT-metrics-2026-06-24.zip').write_bytes(b'broken')
        output = self.directory/'output'
        with self.assertRaisesRegex(ValueError,'Receipt SHA-256'):
            normalizer.build(self.source,output,self.AT)
        self.assertFalse(output.exists())

    def test_duplicate_receipt_and_traversal_rejected(self):
        path = self.source/'receipts.json'
        receipts = json.loads(path.read_bytes())
        receipts.append(receipts[0])
        path.write_bytes(normalizer.json_bytes(receipts))
        with self.assertRaisesRegex(ValueError,'Unsafe or duplicate'):
            self.derive()
        receipts[-1] = dict(receipts[-1],file='../private.txt')
        path.write_bytes(normalizer.json_bytes(receipts))
        with self.assertRaisesRegex(ValueError,'Unsafe or duplicate'):
            self.derive()

    def test_missing_reference_never_degrades_to_unchecked_acceptance(self):
        path = self.source/'receipts.json'
        receipts = json.loads(path.read_bytes())
        receipts = [r for r in receipts if r['file'] != 'raw/BTCUSDT-5m-2026-06-24.zip']
        path.write_bytes(normalizer.json_bytes(receipts))
        with self.assertRaisesRegex(ValueError,'Missing or changed semantic reference'):
            self.derive()

    def test_sort_identical_duplicates_and_conflicts_keep_provenance(self):
        rows = [json.loads(line) for line in self.derive()['accepted.jsonl'].splitlines()][:3]
        twin = copy.deepcopy(rows[0])
        twin['value'] = twin['value']+'0'
        twin['provenance'][0]['line'] = 999
        twin['collected_at_utc'] = self.AT
        accepted, conflicts, removed = normalizer.deduplicate([rows[2],twin,rows[1],rows[0]])
        self.assertEqual(removed,1)
        self.assertEqual(conflicts,[])
        self.assertEqual([r['window_start_utc'] for r in accepted],sorted(r['window_start_utc'] for r in rows))
        self.assertEqual(len(accepted[0]['provenance']),2)
        self.assertEqual(accepted[0]['collected_at_utc'],self.AT)
        twin['value'] = '999'
        accepted, conflicts, removed = normalizer.deduplicate([rows[0],twin,rows[1]])
        self.assertEqual(len(accepted),1)
        self.assertEqual(len(conflicts),1)
        self.assertEqual(len(conflicts[0]['observations']),2)
        self.assertEqual(removed,0)

    def test_gap_and_off_grid_checks_never_round_or_fill(self):
        rows = [{'create_time':f'2026-06-24 00:{m}:00'} for m in ('10','00','00')]
        rows.append({'create_time':'2026-06-24 00:05:02'})
        result = normalizer.grid_quality('BTCUSDT-metrics-2026-06-24.zip',rows)
        self.assertEqual(result['duplicate_label_rows'],1)
        self.assertEqual(result['backward_steps'],1)
        self.assertEqual(len(result['missing_labels']),286)
        self.assertIn('2026-06-24T00:05:02+00:00',result['off_grid_labels'])
        self.assertIn('2026-06-24T00:05:00+00:00',result['missing_labels'])

    def test_immutable_outputs_and_byte_replay_detect_tampering(self):
        output = self.directory/'output'
        normalizer.build(self.source,output,self.AT)
        self.assertEqual(normalizer.verify(self.source,output)['accepted_records'],576)
        with self.assertRaisesRegex(ValueError,'new output directory'):
            normalizer.build(self.source,output,self.AT)
        (output/'statistics.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,'replay mismatch'):
            normalizer.verify(self.source,output)
        with self.assertRaisesRegex(ValueError,'outside source'):
            normalizer.build(self.source,self.source/'derived',self.AT)

    def test_numeric_invalids_and_naive_timestamps_rejected(self):
        for value in ('NaN','Infinity','-1','no-number','1e1000'):
            with self.assertRaises(ValueError):
                normalizer.decimal(value)
        for value in ('2026-10-22T09:00:00','2026-10-22T09:00:00+08:00',None):
            with self.assertRaises(ValueError):
                normalizer.utc(value)

    def test_committed_dataset_replays_without_network_or_strategy_imports(self):
        output = normalizer.ROOT/'data/archive_normalized/binance_um_btcusdt_v1'
        self.assertEqual(normalizer.verify(normalizer.SOURCE,output),{'verified_files':7,'accepted_records':576})
