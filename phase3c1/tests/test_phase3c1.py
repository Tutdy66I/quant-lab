"""Only generated data and explicit frozen protocol bytes are read."""
from copy import deepcopy
from datetime import date, timedelta, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import unittest
from unittest.mock import patch

import phase3c1 as p

FROZEN = Path(__file__).resolve().parents[2] / 'quant-lab-phase3b-v1-frozen-evidence' / 'protocol-v1.md'


def synthetic():
    start, stop = date(2018, 1, 1), date(2026, 9, 30)
    result = []
    for i in range((stop-start).days+1):
        close = 100 + i * .01 + math.sin(i * .17) * 2
        opening = close * (1 + .001 * math.sin(i * .11))
        result.append({'date': (start+timedelta(days=i)).isoformat(), 'open': opening,
                       'high': max(opening, close) + 1, 'low': min(opening, close) - 1, 'close': close})
    return result


class SyntheticProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = synthetic()
        cls.kw = dict(dataset_kind='synthetic', snapshot_collected_at='2027-01-01T00:00:00Z', dataset_version='generated-v1')
        cls.samples = p.build_samples(cls.rows, **cls.kw)

    def test_exact_ridge_reference_from_fraction_arithmetic(self):
        vector = json.loads((Path(__file__).parent/'reference_ridge.json').read_text())
        n = Fraction(len(vector['y']))
        x = [[Fraction(v) for v in row] for row in vector['x']]; y = [Fraction(v) for v in vector['y']]
        mu_y = sum(y)/n
        s11=sum(r[0]*r[0] for r in x)/n; s12=sum(r[0]*r[1] for r in x)/n; s22=sum(r[1]*r[1] for r in x)/n
        t1=sum(r[0]*(v-mu_y) for r,v in zip(x,y))/n; t2=sum(r[1]*(v-mu_y) for r,v in zip(x,y))/n
        a=Fraction(vector['alpha']); determinant=(s11+a)*(s22+a)-s12*s12
        w1=(t1*(s22+a)-t2*s12)/determinant; w2=(t2*(s11+a)-t1*s12)/determinant
        self.assertEqual([w1,w2],[Fraction(1),Fraction(1)])
        fit=p.fit_ridge(vector['x'],vector['y'],1)
        for observed,expected in zip([fit['intercept']]+fit['weights'],[1,1,1]):
            self.assertLessEqual(abs(observed-expected), 1e-12+1e-10*abs(expected))
        for xrow,expected in zip(vector['x'],vector['predictions']):
            self.assertAlmostEqual(p.predict(fit,xrow),expected,places=12)

    def test_correlated_fraction_reference_and_rescaling(self):
        # Independently solve [[2,1],[1,2]] w = [4,4] exactly.
        x=[[-1,-1],[-1,-1],[1,1],[1,1]]; y=[-3,-3,5,5]
        determinant=Fraction(2)*2-Fraction(1)*1
        expected=(Fraction(4)*2-Fraction(4)*1)/determinant
        self.assertEqual(expected,Fraction(4,3))
        shifted=[[2*r[0]+10,3*r[1]-7] for r in x]
        model=p.fit_ridge(shifted,y,1)
        for observed in model['weights']:
            self.assertLessEqual(abs(observed-float(expected)),1e-12+1e-10*abs(float(expected)))
        self.assertAlmostEqual(model['intercept'],1.0,places=12)
        self.assertEqual(model['mean'],[10,-7]); self.assertEqual(model['std'],[2,3])

    def test_partition_boundaries_and_precise_timing(self):
        for name,first,last in [('train','2018-01-04','2021-12-29'),('tune','2022-01-01','2023-12-29'),('evaluation','2024-01-01','2026-09-28')]:
            samples=self.samples[name]
            self.assertEqual(samples[0]['decision'][:10],first); self.assertEqual(samples[-1]['decision'][:10],last)
            self.assertNotIn(last[:8]+'30', [r['decision'][:10] for r in samples])
        s=self.samples['train'][0]
        from datetime import datetime
        d=datetime.fromisoformat(s['decision'])
        self.assertEqual(datetime.fromisoformat(s['entry'])-d,timedelta(hours=23))
        self.assertEqual(datetime.fromisoformat(s['label_end'])-d,timedelta(hours=47))
        self.assertEqual(datetime.fromisoformat(s['simulated_label_available'])-d,timedelta(hours=48))
        self.assertIsNone(s['actual_available_at']); self.assertEqual(s['availability_basis'],'retrospective_assumption')

    def test_training_does_not_depend_on_future_and_no_tune_refit(self):
        train,tune=self.samples['train'],self.samples['tune']
        model,scores=p.choose_ridge(train,tune)
        self.assertEqual(model,p.fit_ridge([r['x'] for r in train],[r['y'] for r in train],model['alpha']))
        changed=deepcopy(tune); changed[-1]['y'] += .2
        again,_=p.choose_ridge(train,changed)
        self.assertEqual(again,p.fit_ridge([r['x'] for r in train],[r['y'] for r in train],again['alpha']))
        self.assertEqual(model['mean'],again['mean']); self.assertEqual(model['std'],again['std'])
        self.assertEqual(len(scores),4)
        rows=deepcopy(self.rows); rows[-1]['close'] += .01
        future=p.build_samples(rows,**self.kw)
        self.assertEqual(future['train'],self.samples['train'])
        self.assertEqual(future['tune'],self.samples['tune'])

    def test_tie_picks_larger_alpha(self):
        train=[{'x':[-1,-1],'y':0},{'x':[-1,1],'y':0},{'x':[1,-1],'y':0},{'x':[1,1],'y':0}]
        model,_=p.choose_ridge(train,[{'x':[1,1],'y':0}])
        self.assertEqual(model['alpha'],10.0)

    def test_bootstrap_rng_and_quantiles_against_independent_reference(self):
        days=[(date(2024,1,1)+timedelta(days=i)).isoformat() for i in range(280)]
        values=[float(i%17) for i in range(280)]
        output=p.bootstrap(days,values); rng=random.Random(20261108); means=[]; starts=[]
        for _ in range(2000):
            indices=[rng.randrange(267) for _ in range(20)]; starts.append(indices)
            series=[]
            for index in indices: series.extend(values[index:index+14])
            means.append(math.fsum(series[:280])/280)
        means.sort()
        self.assertEqual(output['starts_sha256'],hashlib.sha256(json.dumps(starts,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        self.assertAlmostEqual(output['lower'],means[49]+.975*(means[50]-means[49]),places=12)
        self.assertAlmostEqual(output['upper'],means[1949]+.025*(means[1950]-means[1949]),places=12)
        self.assertEqual(output,p.bootstrap(days,values))
        with self.assertRaises(ValueError): p.bootstrap(days[:-1],values[:-1])
        bad=days.copy(); bad[-1]='2025-01-01'
        with self.assertRaises(ValueError): p.bootstrap(bad,values)
        with self.assertRaises(ValueError): p.bootstrap(days,values[:-1]+[float('inf')])
        days.append((date(2024,1,1)+timedelta(days=280)).isoformat()); values.append(12.0)
        nonmultiple=p.bootstrap(days,values); rng=random.Random(20261108)
        starts=[[rng.randrange(268) for _ in range(21)] for _ in range(2000)]
        self.assertEqual(nonmultiple['starts_sha256'],p.digest(starts))
        first=[v for i in starts[0] for v in values[i:i+14]][:281]
        self.assertEqual(len(first),281)
        replay=sorted(math.fsum([v for i in block for v in values[i:i+14]][:281])/281 for block in starts)
        self.assertAlmostEqual(nonmultiple['lower'],replay[49]+.975*(replay[50]-replay[49]),places=12)
        self.assertAlmostEqual(nonmultiple['upper'],replay[1949]+.025*(replay[1950]-replay[1949]),places=12)

    def test_bad_inputs_fail_closed(self):
        for mutation in ('missing','duplicate','nonfinite','invalid_time'):
            data=deepcopy(self.rows)
            if mutation=='missing': data.pop(400)
            elif mutation=='duplicate': data.append(data[-1])
            elif mutation=='nonfinite': data[12]['close']=float('nan')
            else: data[20]['date']='invalid'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError): p.build_samples(data,**self.kw)
        with self.assertRaises(ValueError): p.build_samples(self.rows,**dict(self.kw,dataset_kind='market'))
        with self.assertRaises(ValueError): p.fit_ridge([[1,2],[1,3]],[1,2],1)
        with self.assertRaises(ValueError): p.fit_ridge([[1,2],[3,4]],[1e308,-1e308],1)

    def test_run_binds_protocol_and_fails_without_partial_results(self):
        protocol=FROZEN.read_bytes(); self.assertEqual(hashlib.sha256(protocol).hexdigest(),p.PROTOCOL_SHA256)
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            result=p.run_synthetic(self.rows,protocol_bytes=protocol,**self.kw)
        self.assertEqual(result['outcome']['status'],'complete')
        self.assertEqual(result['research_admission'],'insufficient_evidence')
        self.assertFalse(result['execution_proven'])
        before=dict(result); recorded=before.pop('envelope_sha256'); self.assertEqual(recorded,p.digest(before))
        self.assertEqual(p.verify_synthetic_run(result,self.rows,protocol_bytes=protocol,**self.kw)['status'],'internally_recomputed')
        forged=deepcopy(result); forged['outcome']['metrics']['ridge_mse']+=1
        forged.pop('envelope_sha256'); forged['envelope_sha256']=p.digest(forged)
        with self.assertRaisesRegex(ValueError,'mismatch'):
            p.verify_synthetic_run(forged,self.rows,protocol_bytes=protocol,**self.kw)
        changed=deepcopy(self.rows); changed[100]['close']+=.01
        with self.assertRaises(ValueError):
            p.verify_synthetic_run(result,changed,protocol_bytes=protocol,**self.kw)
        failed=p.run_synthetic(self.rows,protocol_bytes=b'changed',**self.kw)
        self.assertEqual(failed['outcome']['status'],'failed')
        self.assertNotIn('metrics',failed['outcome']); self.assertNotIn('model',failed['outcome'])
        bad=deepcopy(self.rows); bad.pop(10)
        failed=p.run_synthetic(bad,protocol_bytes=protocol,**self.kw)
        self.assertEqual(failed['input_sha256'],p.digest(bad))
        self.assertEqual(failed['outcome']['status'],'failed')
        self.assertNotIn('metrics',failed['outcome'])

    def test_frozen_method_rejects_mutable_globals_and_configuration_drift(self):
        protocol=FROZEN.read_bytes()
        valid=p.run_synthetic(self.rows,protocol_bytes=protocol,**self.kw)
        self.assertEqual(valid['outcome']['status'],'complete')
        self.assertEqual(valid['config']['method'],p.frozen_method_v1())
        changed_partitions=deepcopy(p.PARTITIONS)
        changed_partitions['evaluation']=(date(2024,1,1),date(2025,12,31))
        for name,value in [('PARTITIONS',changed_partitions),('ALPHAS',(1.0,)),
                           ('UTC',timezone(timedelta(hours=8)))]:
            with self.subTest(global_name=name), patch.object(p,name,value):
                result=p.run_synthetic(self.rows,protocol_bytes=protocol,**self.kw)
                self.assertEqual(result['outcome']['status'],'failed')
                self.assertNotIn('metrics',result['outcome'])
                with self.assertRaises(ValueError):
                    p.verify_synthetic_run(valid,self.rows,protocol_bytes=protocol,**self.kw)
        for partition in ('train','tune'):
            modified=deepcopy(p.PARTITIONS)
            start,end=modified[partition]; modified[partition]=(start,end-timedelta(days=1))
            with self.subTest(partition=partition), patch.object(p,'PARTITIONS',modified):
                self.assertEqual(p.run_synthetic(self.rows,protocol_bytes=protocol,**self.kw)['outcome']['status'],'failed')
        changes=[('partitions','train',['2018-01-01','2020-12-31']),
                 ('partitions','tune',['2022-01-01','2022-12-31']),
                 ('partitions','evaluation',['2024-01-01','2025-12-31']),
                 ('ridge','alphas',[1.0]),('bootstrap','block_length',7),
                 ('bootstrap','resamples',100),('bootstrap','seed',1)]
        for section,key,value in changes:
            method=p.frozen_method_v1(); method[section][key]=value
            with self.subTest(section=section,key=key):
                result=p.run_synthetic(self.rows,protocol_bytes=protocol,method_config=method,**self.kw)
                self.assertEqual(result['outcome']['status'],'failed')
        method=p.frozen_method_v1(); method['max_age_seconds']=1
        with patch.object(p,'build_samples',side_effect=AssertionError('must fail before sample construction')) as builder:
            self.assertEqual(p.run_synthetic(self.rows,protocol_bytes=protocol,method_config=method,**self.kw)['outcome']['status'],'failed')
            builder.assert_not_called()
        forged=deepcopy(valid); forged['config']['method']['partitions']['evaluation'][1]='2025-12-31'
        forged['config_sha256']=p.digest(forged['config']); forged.pop('envelope_sha256')
        forged['envelope_sha256']=p.digest(forged)
        with self.assertRaises(ValueError):
            p.verify_synthetic_run(forged,self.rows,protocol_bytes=protocol,**self.kw)

    def test_snapshot_detaches_nested_rows_and_replays_exact_input(self):
        protocol=FROZEN.read_bytes(); caller=deepcopy(self.rows)
        snapshot=p.capture_synthetic_input(caller)
        self.assertIs(type(snapshot),bytes)
        self.assertEqual(hashlib.sha256(snapshot).hexdigest(),p.digest(caller))
        expected=p.run_synthetic(snapshot,protocol_bytes=protocol,**self.kw)
        original=p._samples_from_snapshot
        def mutation_between_hash_and_use(fixed, **kwargs):
            self.assertIs(type(fixed),bytes)
            caller[3000]['close'] += .01
            caller[100]['open'] += .01
            return original(fixed,**kwargs)
        with patch.object(p,'_samples_from_snapshot',side_effect=mutation_between_hash_and_use):
            result=p.run_synthetic(caller,protocol_bytes=protocol,**self.kw)
        self.assertEqual(result['outcome']['status'],'complete')
        self.assertEqual(result,expected)
        self.assertEqual(result['input_sha256'],hashlib.sha256(snapshot).hexdigest())
        self.assertNotEqual(result['input_sha256'],p.digest(caller))
        self.assertEqual(p.verify_synthetic_run(result,snapshot,protocol_bytes=protocol,**self.kw)['status'],'internally_recomputed')
        with self.assertRaises(ValueError):
            p.verify_synthetic_run(result,caller,protocol_bytes=protocol,**self.kw)
        with self.assertRaises(ValueError):
            p.capture_synthetic_input(b'[ {"x":1} ]')

    def test_mutating_list_subclass_has_one_serialization_source(self):
        class ChangesOnSecondIteration(list):
            calls=0
            def __iter__(self):
                self.calls+=1
                if self.calls==2:
                    self[3000]['close']+=.01
                return super().__iter__()
        rows=ChangesOnSecondIteration(deepcopy(self.rows)); protocol=FROZEN.read_bytes()
        expected=p.capture_synthetic_input(deepcopy(self.rows))
        result=p.run_synthetic(rows,protocol_bytes=protocol,**self.kw)
        self.assertEqual(rows.calls,1)
        self.assertEqual(result['input_sha256'],hashlib.sha256(expected).hexdigest())
        self.assertEqual(p.verify_synthetic_run(result,expected,protocol_bytes=protocol,**self.kw)['status'],'internally_recomputed')

    def test_ordinary_exception_returns_failure_without_rereading_inputs(self):
        class BrokenRows(list):
            calls=0
            def __iter__(self):
                self.calls+=1
                raise RuntimeError('snapshot failed')
        rows=BrokenRows([1]); protocol=FROZEN.read_bytes()
        result=p.run_synthetic(rows,protocol_bytes=protocol,**self.kw)
        self.assertEqual(rows.calls,1)
        self.assertEqual(result['outcome']['status'],'failed')
        self.assertEqual(result['outcome']['error_type'],'RuntimeError')
        self.assertIsNone(result['input_sha256'])
        self.assertEqual(result['input_hash_status'],'unavailable')
        self.assertIsNone(result['envelope_sha256'])
        self.assertEqual(result['receipt_integrity'],'unavailable')
        self.assertNotIn('metrics',result['outcome'])

        class UnsafeError(Exception):
            def __str__(self):
                raise AssertionError('exception details must not be evaluated')
        class UnsafeRows(list):
            def __iter__(self):
                raise UnsafeError()
        result=p.run_synthetic(UnsafeRows([1]),protocol_bytes=protocol,**self.kw)
        self.assertEqual(result['outcome']['status'],'failed')
        self.assertEqual(result['outcome']['error_type'],'UnsafeError')

    def test_ordinary_failures_across_stages_and_receipt_sealing(self):
        protocol=FROZEN.read_bytes(); expected=p.digest(self.rows)
        for stage in ('_samples_from_snapshot','fit_ridge','choose_ridge','predict','bootstrap','mse','_seal_success_envelope'):
            with self.subTest(stage=stage), patch.object(p,stage,side_effect=RuntimeError('injected')):
                result=p.run_synthetic(self.rows,protocol_bytes=protocol,**self.kw)
                self.assertEqual(result['outcome']['status'],'failed')
                self.assertEqual(result['outcome']['error_type'],'RuntimeError')
                self.assertEqual(result['input_sha256'],expected)
                self.assertEqual(result['input_hash_status'],'available')
                self.assertIsNone(result['envelope_sha256'])
                self.assertNotIn('metrics',result['outcome'])
                self.assertNotIn('model',result['outcome'])

    def test_control_signals_are_not_converted_to_failed_runs(self):
        protocol=FROZEN.read_bytes()
        for stage in ('capture_synthetic_input','_seal_success_envelope'):
            for signal in (KeyboardInterrupt,SystemExit):
                with self.subTest(stage=stage,signal=signal), patch.object(p,stage,side_effect=signal):
                    with self.assertRaises(signal):
                        p.run_synthetic(self.rows,protocol_bytes=protocol,**self.kw)


if __name__=='__main__': unittest.main()
