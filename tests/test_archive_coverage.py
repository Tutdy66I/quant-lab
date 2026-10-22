"""Coverage/time alignment checks; all market evidence is local and immutable."""
from datetime import datetime,timedelta
from pathlib import Path
import io
import json
import random
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from research import archive_coverage as c
from research import archive_coverage_collect as collector
from research import archive_normalizer as n


class ArchiveCoverageTests(unittest.TestCase):
    def setUp(self):
        self.protocol=json.loads((c.DEFAULT_SOURCE/'protocol.json').read_bytes())

    def synthetic(self,offset=0):
        randomizer=random.Random(17)
        day=datetime(2026,6,24,tzinfo=n.UTC)
        reference={int((day+timedelta(minutes=5*i)).timestamp()*1000):1+randomizer.random()*2 for i in range(-1,288)}
        rows=[]
        for i in range(288):
            stamp=day+timedelta(minutes=5*i)
            key=int((stamp+timedelta(minutes=offset)).timestamp()*1000)
            rows.append({'create_time':stamp.strftime('%Y-%m-%d %H:%M:%S'),'symbol':'BTCUSDT',n.TAKER:str(reference[key])})
        return rows,reference

    def test_start_and_end_conventions_are_measured_not_inferred_from_date(self):
        for offset in (-5,0):
            rows,reference=self.synthetic(offset)
            result=c.classify(rows,reference,self.protocol)
            self.assertTrue(result['qualified'])
            self.assertEqual(result['offset_minutes'],offset)
            self.assertTrue(all(x['chosen']['pairs']==72 for x in result['six_hour_checks']))

    def test_intraday_convention_change_is_not_hidden_by_daily_correlation(self):
        rows,reference=self.synthetic()
        for i,row in enumerate(rows):
            key=int(n.label_time(row['create_time']).timestamp()*1000)
            reference[key]=(reference[key]*1000 if i<216 else reference[key]/1000)
        for i,row in enumerate(rows):
            key=int(n.label_time(row['create_time']).timestamp()*1000)
            row[n.TAKER]=str(reference[key] if i<216 else reference[key-300000])
        result=c.classify(rows,reference,self.protocol)
        self.assertGreater(result['scores']['0']['r'],.99)
        self.assertFalse(result['qualified'])
        self.assertEqual(result['reason'],'alignment_gate_failed')

    def test_missing_boundary_reference_and_duplicate_labels_are_not_accepted(self):
        rows,reference=self.synthetic()
        reference.pop(min(reference))
        self.assertFalse(c.classify(rows,reference,self.protocol)['qualified'])
        rows[-1]=dict(rows[0])
        self.assertEqual(c.classify(rows,reference,self.protocol)['reason'],'incomplete_or_duplicate_grid')

    def test_constant_reference_is_insufficient_evidence(self):
        rows,reference=self.synthetic()
        reference={key:1.0 for key in reference}
        result=c.classify(rows,reference,self.protocol)
        self.assertFalse(result['qualified'])
        self.assertEqual(result['reason'],'insufficient_reference_variation')

    def test_real_old_headerless_files_preserve_first_data_row(self):
        records,_=n.load_evidence(c.DEFAULT_SOURCE)
        rows,info=c.read_reference('BTCUSDT-5m-2021-12-01.zip',records)
        self.assertEqual(len(rows),288)
        self.assertEqual(rows[0]['open_time'],'1638316800000')
        self.assertIn('headerless',info['schema_variant'])

    def test_collector_stops_and_preserves_restriction_body_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'raw-version'
            error=HTTPError('https://data.binance.vision/',451,'restricted',{},io.BytesIO(b'restricted body'))
            with patch.object(collector,'urlopen',side_effect=error) as opener:
                with self.assertRaisesRegex(RuntimeError,'Stop on access restriction'):
                    collector.collect(output,n.ROOT/'docs/archive_coverage_protocol_v1.json')
            self.assertEqual(opener.call_count,1)
            receipt=json.loads((output/'receipts.json').read_bytes())[0]
            self.assertEqual(receipt['status'],451)
            self.assertEqual((output/receipt['file']).read_bytes(),b'restricted body')

    def test_coverage_does_not_claim_continuity_across_disjoint_samples(self):
        rows=[{'window_start_utc':f'2026-06-24T00:{m}:00+00:00'} for m in ('00','05','15')]
        result=c.continuous_runs(rows)
        self.assertEqual([r['windows'] for r in result],[2,1])

    def test_frozen_real_dataset_replays_and_never_admits_oi_or_prediction(self):
        output=n.ROOT/'data/archive_normalized/binance_um_btcusdt_coverage_v1'
        self.assertEqual(c.verify(c.DEFAULT_SOURCE,output),{'verified_files':6})
        summary=json.loads((output/'coverage.json').read_bytes())
        self.assertEqual(summary['dates_qualified'],27)
        self.assertEqual(summary['accepted_windows'],7776)
        self.assertEqual(summary['longest_continuous_days'],16)
        self.assertFalse(summary['historical_publication_proven'])
        self.assertFalse(summary['statistics_module_enabled'])
        data=[json.loads(line) for line in (output/'validated.jsonl').read_bytes().splitlines()]
        self.assertEqual({r['field'] for r in data},{n.TAKER})
        self.assertTrue(all(r['published_at_utc'] is None for r in data))
        self.assertTrue(all(n.utc(r['research_available_at_utc'])>=n.utc(r['evidence_collected_at_utc']) for r in data))
        old=next(x for x in summary['daily_diagnostics'] if x['date']=='2021-12-01')
        self.assertEqual(old['offset_minutes'],-5)
