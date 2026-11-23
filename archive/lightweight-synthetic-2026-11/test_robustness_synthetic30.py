"""Tests of synthetic chronological comparisons, never the original holdout."""
import copy
import math
import unittest
import robustness_synthetic30 as r


class RobustnessSyntheticTests(unittest.TestCase):
    def test_repeatability_fixed_matrix(self):
        a=r.run_matrix(); b=r.run_matrix()
        self.assertEqual(a,b)
        self.assertEqual(len(a['results']),len(r.SCENARIOS)*len(r.TRAIN_SIZES)*len(r.STARTS))

    def test_boundary_purge_and_training_sizes(self):
        rows=r.generate('signal')
        for size in r.TRAIN_SIZES:
            for start in r.STARTS:
                v=r.evaluate_window(rows,scenario='signal',seed=r.SEED,validation_start=start,train_size=size)
                train=v['ranges']['train']; tune=v['ranges']['tune']; valid=v['ranges']['validation']
                self.assertEqual(train[1]-train[0],size-1)
                self.assertEqual(tune[1]-tune[0],r.TUNE_SIZE-1)
                self.assertEqual(valid,[start,start+r.VALID_SIZE])
                self.assertLess(rows[train[1]-1]['target_end_time'],rows[tune[0]]['decision_time'])
                self.assertLess(rows[tune[1]-1]['target_end_time'],rows[valid[0]]['decision_time'])

    def test_validation_changes_do_not_change_fit_or_alpha(self):
        rows=r.generate('signal'); changed=copy.deepcopy(rows)
        for row in changed[140:170]: row['target'] += 1000.0
        a=r.evaluate_window(rows,scenario='signal',seed=r.SEED,validation_start=140,train_size=80)
        b=r.evaluate_window(changed,scenario='signal',seed=r.SEED,validation_start=140,train_size=80)
        self.assertEqual(a['selected_alpha'],b['selected_alpha'])
        self.assertEqual(a['fitted_model'],b['fitted_model'])
        self.assertEqual(a['tune_mse'],b['tune_mse'])
        self.assertNotEqual(a['dataset_sha256'],b['dataset_sha256'])

    def test_future_feature_and_cross_boundary_rejected(self):
        rows=r.generate('signal'); rows[140]['feature_available_time']=281
        with self.assertRaises(ValueError):
            r.evaluate_window(rows,scenario='signal',seed=r.SEED,validation_start=140,train_size=35)
        rows=r.generate('signal'); rows[109]['target_end_time']+=2
        with self.assertRaises(ValueError):
            r.evaluate_window(rows,scenario='signal',seed=r.SEED,validation_start=140,train_size=35)

    def test_missing_duplicate_reorder_nonfinite_rejected(self):
        original=r.generate('signal')
        for case in ('missing','duplicate','reordered','nan'):
            rows=copy.deepcopy(original)
            if case=='missing': rows.pop(12)
            elif case=='duplicate': rows[12]=copy.deepcopy(rows[11])
            elif case=='reordered': rows[12],rows[13]=rows[13],rows[12]
            else: rows[12]['target']=float('nan')
            with self.subTest(case=case), self.assertRaises(ValueError):
                r.evaluate_window(rows,scenario='signal',seed=r.SEED,validation_start=140,train_size=35)

    def test_invalid_inputs_fail(self):
        with self.assertRaises(ValueError): r.generate('real-data')
        with self.assertRaises(ValueError): r.run_matrix(True)
        rows=r.generate('signal')
        for start,size in ((140,0),(42,35),(190,99)):
            with self.subTest(start=start,size=size), self.assertRaises(ValueError):
                r.evaluate_window(rows,scenario='signal',seed=r.SEED,validation_start=start,train_size=size)

    def test_paired_ids_metrics_and_predefined_candidates(self):
        v=r.evaluate_window(r.generate('signal'),scenario='signal',seed=r.SEED,validation_start=140,train_size=80)
        self.assertEqual(len(set(v['valid_sample_ids'])),r.VALID_SIZE)
        self.assertEqual(set(v['metrics']),set(r.MODELS))
        self.assertEqual([x[0] for x in v['tune_mse']],list(r.ALPHAS))
        self.assertEqual(v['selected_alpha'],min(v['tune_mse'],key=lambda x:(x[1],x[0]))[0])
        self.assertEqual(len(v['loss_differences']['mean']),r.VALID_SIZE)
        self.assertTrue(all(math.isfinite(v['metrics'][m]['rmse']) for m in r.MODELS))

    def test_generators_have_declared_shifts(self):
        a=r.generate('signal'); relation=r.generate('relation_shift'); feature=r.generate('feature_shift')
        self.assertEqual(a[:140],relation[:140]); self.assertEqual(a[:140],feature[:140])
        self.assertNotEqual(a[140]['target'],relation[140]['target'])
        self.assertNotEqual(a[140]['features'][1],feature[140]['features'][1])

    def test_zero_variance_feature_rejected(self):
        rows=r.generate('signal')
        for row in rows[:110]: row['features']=(0.0,row['features'][1])
        with self.assertRaises(ValueError):
            r.evaluate_window(rows,scenario='signal',seed=r.SEED,validation_start=140,train_size=80)


if __name__=='__main__': unittest.main()
