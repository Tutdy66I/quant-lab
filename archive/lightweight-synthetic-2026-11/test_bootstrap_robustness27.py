"""Targeted independent-stream and paired/bootstrap regressions."""
import unittest
from dataclasses import replace
import bootstrap_robustness27 as r
from coverage_synthetic import paired_window_bootstrap

class BootstrapRobustness27(unittest.TestCase):
    def test_nonzero_effect_uses_distinct_innovation_stream(self):
        z=r.derive_seed('dgp',20261113,40,.8,0.0,.1,0)
        d=r.derive_seed('dgp',20261113,40,.8,.2,.1,0)
        self.assertNotEqual(z,d)
        a,la,lb,ctx,_=r.make_synthetic_case(n=40,phi=.8,effect=0,noise=.1,seed=z)
        b,lc,ld,ctx2,_=r.make_synthetic_case(n=40,phi=.8,effect=.2,noise=.1,seed=d)
        self.assertNotEqual(ctx['expected_dataset_fingerprint'],ctx2['expected_dataset_fingerprint'])
        self.assertNotEqual([x.loss_value-y.loss_value for x,y in zip(la,lb)],[x.loss_value-y.loss_value-.2 for x,y in zip(lc,ld)])
    def test_identity_is_checked_before_bootstrap(self):
        s,a,b,c,_=r.make_synthetic_case(n=40,phi=.8,effect=0,noise=.1,seed=7)
        with self.assertRaises(ValueError):
            paired_window_bootstrap(s,a,b[::-1],**c,spacing=10,replicates=100)
    def test_reproducible_cell_and_mean_stats(self):
        kw=dict(n=40,phi=.8,effect=.2,noise=.1,seed=123,trials=20,replicates=100,block_lengths=(3,8))
        a=r.evaluate_cell(**kw); self.assertEqual(a,r.evaluate_cell(**kw))
        self.assertEqual(a['methods']['iid']['total'],20)
        self.assertEqual(a['nominal_confidence'],.95)
        self.assertTrue(a['methods']['iid']['wilson_95'][0] <= a['methods']['iid']['coverage'] <= a['methods']['iid']['wilson_95'][1])
        self.assertEqual(len(a['trials']),20)
    def test_invalid_parameters_fail(self):
        for kw in [dict(n=10,phi=0,effect=0,noise=.1,seed=1),dict(n=40,phi=.95,effect=0,noise=.1,seed=1),dict(n=40,phi=0,effect=0,noise=0,seed=1)]:
            with self.subTest(kw=kw),self.assertRaises(ValueError): r.make_synthetic_case(**kw)
        with self.assertRaises(ValueError): r.evaluate_cell(n=40,phi=0,effect=0,noise=.1,seed=1,block_lengths=(3,9))
        with self.assertRaises(ValueError): r.execute_matrix({'version':'coverage-v2','trials':20,'replicates':100,'sample_lengths':[100000000]})

if __name__=='__main__': unittest.main()
