"""One-shot mechanism tests use a distinct disposable synthetic plan only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import holdout_synthetic as h

class HoldoutPreparationTests(unittest.TestCase):
    def setUp(self):
        self.plan=deepcopy(h.FINAL_PLAN)
        self.plan.update(dataset_id='throwaway-holdout-test',seed=314159,total=45,
                         train=[0,10],tune=[10,20],development_validation=[20,30],holdout=[30,45])
        self.rows=h.generate(self.plan)
        self.seal=h.make_seal(self.plan,self.rows)
        self.anchor=h._sha(self.seal)
    def test_seal_only_contains_metadata_and_no_scores(self):
        self.assertFalse(self.seal['metrics_computed'])
        self.assertEqual(self.seal['status'],'unopened')
        self.assertEqual(self.seal['holdout_manifest']['count'],15)
        self.assertNotIn('metrics',self.seal)
    def test_tampered_data_and_joint_seal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rows=deepcopy(self.rows); rows[-1]['target']+=1
            with self.assertRaises(ValueError): h.evaluate_once(self.plan,rows,self.seal,self.anchor,Path(d)/'used',h.APPROVAL)
            fake=deepcopy(self.seal); fake['dataset_sha256']=h._sha(rows)
            with self.assertRaises(ValueError): h.evaluate_once(self.plan,rows,fake,self.anchor,Path(d)/'used',h.APPROVAL)
            self.assertFalse((Path(d)/'used').exists())
    def test_missing_anchor_or_approval_does_not_consume(self):
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/'used'
            for anchor,approval in [('',h.APPROVAL),(self.anchor,'')]:
                with self.assertRaises(ValueError): h.evaluate_once(self.plan,self.rows,self.seal,anchor,receipt,approval)
            self.assertFalse(receipt.exists())
    def test_once_and_no_second_open(self):
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/'used'
            result=h.evaluate_once(self.plan,self.rows,self.seal,self.anchor,receipt,h.APPROVAL)
            self.assertEqual(result['count'],15)
            self.assertEqual(json.loads(receipt.read_text())['status'],'consumed')
            with self.assertRaises(FileExistsError): h.evaluate_once(self.plan,self.rows,self.seal,self.anchor,receipt,h.APPROVAL)
    def test_fault_after_consumption_stays_consumed(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/'used'
            with mock.patch.object(h.ridge_synthetic,'fit_ridge',side_effect=RuntimeError('synthetic injected')):
                with self.assertRaises(RuntimeError): h.evaluate_once(self.plan,self.rows,self.seal,self.anchor,receipt,h.APPROVAL)
            self.assertTrue(receipt.is_file())
            with self.assertRaises(FileExistsError): h.evaluate_once(self.plan,self.rows,self.seal,self.anchor,receipt,h.APPROVAL)
    def test_wrong_time_boundaries_and_configuration_rejected(self):
        for key,value in [('holdout',[19,45]),('gap_ticks',0),('alphas',[1.0])]:
            plan=deepcopy(self.plan);plan[key]=value
            with self.assertRaises(ValueError):h.generate(plan)
    def test_preregistered_method_and_metrics_tampering_rejected(self):
        for key,value in [('target','other'),('features','future'),('metrics',['accuracy']),
                          ('alpha_selection','select on holdout'),('success','ridge wins'),
                          ('target_interval','untrusted')]:
            plan=deepcopy(self.plan);plan[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): h.generate(plan)

    def test_holdout_targets_do_not_affect_training_slice(self):
        rows=deepcopy(self.rows)
        rows[-1]['target']+=42
        self.assertEqual(rows[:self.plan['tune'][1]],self.rows[:self.plan['tune'][1]])
        with self.assertRaises(ValueError):h.make_seal(self.plan,rows)

if __name__=='__main__': unittest.main()

