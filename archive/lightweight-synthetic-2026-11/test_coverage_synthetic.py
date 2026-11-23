"""Tests of identity-checked synthetic bootstrap and coverage diagnostics."""
from dataclasses import replace
import math
import unittest

import coverage_synthetic as c
from sample_lineage import Prediction, make_losses, prediction_id_for


class IdentityBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.samples, self.a, self.b, self.ctx = c.synthetic_pairs(50, phi=.7, effect=0, seed=7)

    def call(self, samples=None, a=None, b=None, **changes):
        return c.paired_window_bootstrap(self.samples if samples is None else samples,
                    self.a if a is None else a, self.b if b is None else b,
                    **self.ctx, spacing=10, replicates=120, seed=31, **changes)

    def test_reproducible_and_identity_retained(self):
        a = self.call(method='moving_block', block_length=5)
        self.assertEqual(a, self.call(method='moving_block', block_length=5))
        self.assertEqual(a['sample_ids'], tuple(s.sample_id for s in self.samples))
        self.assertEqual(a['n'], 50)
        self.assertEqual(a['adjacent_target_overlap_count'], 0)

    def test_no_silent_reorder_missing_or_wrong_context(self):
        for a, b in ((self.a[::-1],self.b),(self.a,self.b[:-1]),(self.a[1:]+self.a[:1],self.b)):
            with self.subTest(count=len(a)), self.assertRaises(ValueError):
                self.call(a=a,b=b)
        wrong = replace(self.b[0].sample, target_value=99)
        bad = (replace(self.b[0], sample=wrong),) + self.b[1:]
        with self.assertRaises(ValueError):
            self.call(b=bad)
        wrong_ctx = dict(self.ctx, expected_config_hash='0'*64)
        with self.assertRaises(ValueError):
            c.paired_window_bootstrap(self.samples,self.a,self.b,**wrong_ctx,spacing=10,replicates=100)

    def test_gap_rejected_after_valid_lineage(self):
        rows=list(self.samples)
        rows[5]=replace(rows[5], decision_time=rows[5].decision_time+1,
                        target_start_time=rows[5].target_start_time+1,
                        target_end_time=rows[5].target_end_time+1)
        common={k:v for k,v in self.ctx.items() if k not in ('model_a','model_b')}
        la=make_losses(rows,[x.prediction for x in self.a],model_id='a',**common)
        lb=make_losses(rows,[x.prediction for x in self.b],model_id='b',**common)
        with self.assertRaisesRegex(ValueError,'irregular'):
            self.call(samples=rows,a=la,b=lb)

    def test_degenerate_zero_difference_is_rejected(self):
        preds=[Prediction(prediction_id_for(row.sample_id,'coverage-window','a', row.config_hash), row.sample_id,
                          row.synthetic_dataset_id,'coverage-window','a', b.prediction.prediction_value)
               for row,b in zip(self.samples,self.b)]
        common={k:v for k,v in self.ctx.items() if k not in ('model_a','model_b')}
        la=make_losses(self.samples,preds,model_id='a',**common)
        with self.assertRaisesRegex(ValueError,'degenerate'):
            self.call(a=la)

    def test_predeclared_outlier_bound_is_rejection_not_clipping(self):
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.call(max_synthetic_loss=1.0)
        with self.assertRaises(ValueError):
            c.autocorrelation_lag1([math.nan]+[0]*19)
        with self.assertRaises(ValueError):
            c.autocorrelation_lag1([0]*19)

    def test_real_time_dependence_and_blocks(self):
        x,a,b,ctx=c.synthetic_pairs(160,phi=.85,effect=.1,seed=18)
        iid=c.paired_window_bootstrap(x,a,b,**ctx,spacing=10,method='iid',replicates=200,seed=71)
        blocks=c.checked_block_sensitivity(x,a,b,block_lengths=[1,5,10],**ctx,spacing=10,replicates=200,seed=71)
        self.assertGreater(iid['lag1_correlation'],.4)
        self.assertEqual([r['block_length'] for r in blocks],[1,5,10])
        self.assertEqual(iid['interval'], blocks[0]['interval'])
        self.assertNotEqual(iid['interval'], blocks[-1]['interval'])


class CoverageTests(unittest.TestCase):
    def test_zero_and_nonzero_known_truth_coverage_reproducible(self):
        for effect in (0.0,.3):
            with self.subTest(effect=effect):
                a=c.coverage_experiment(trials=20,n=50,phi=.5,effect=effect,
                                        bootstrap_replicates=100,block_length=5,seed=23)
                self.assertEqual(a,c.coverage_experiment(trials=20,n=50,phi=.5,effect=effect,
                                        bootstrap_replicates=100,block_length=5,seed=23))
                self.assertEqual(a['known_mean_difference'],effect)
                for result in a['methods'].values():
                    self.assertGreaterEqual(result['coverage'],0)
                    self.assertLessEqual(result['coverage'],1)
                    self.assertLessEqual(result['coverage_wilson_95'][0],result['coverage'])
                    self.assertGreaterEqual(result['coverage_wilson_95'][1],result['coverage'])

    def test_invalid_small_or_unsupported_configurations(self):
        for opts in ({'trials':19}, {'n':19}, {'phi':1}, {'phi':True}, {'effect':math.inf}, {'seed':False}):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                c.coverage_experiment(**opts)


if __name__ == '__main__':
    unittest.main(verbosity=2)
