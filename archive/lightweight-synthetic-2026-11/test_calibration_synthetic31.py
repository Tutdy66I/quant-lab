import unittest
import calibration_synthetic31 as c


class Calibration31Tests(unittest.TestCase):
    def test_predeclared_seed_sets_are_disjoint(self):
        self.assertFalse(set(c.CONFIG['development_seeds']) & set(c.CONFIG['report_seeds']))
        self.assertEqual(len(set(c.CONFIG['report_seeds'])),100)

    def test_reproducible_generation_and_change(self):
        a,t=c.generate('relation_strong',seed=41000,reference_size=40,window_size=20)
        b,_=c.generate('stable',seed=41000,reference_size=40,window_size=20)
        self.assertEqual(a[:t],b[:t]); self.assertNotEqual(a[t]['y'],b[t]['y'])
        self.assertEqual((a,t),c.generate('relation_strong',seed=41000,reference_size=40,window_size=20))

    def test_wilson_bounds_and_invalid(self):
        low,high=c.wilson(0,100); self.assertAlmostEqual(low,0,places=15); self.assertGreater(high,0)
        with self.assertRaises(ValueError): c.wilson(101,100)

    def test_detection_delay_censoring_and_alarm_accounting(self):
        none={'mean':False,'variance':False,'relation':False}; yes=dict(none,relation=True)
        records=[{'pre':[none]*4,'post':[none,yes,yes,none,none]},
                 {'pre':[none]*4,'post':[none]*5}]
        summary=c._summarize(records,'relation',20)
        self.assertEqual(summary['post_relevant_detection']['count'],1)
        self.assertEqual(summary['undetected']['count'],1)
        self.assertEqual(summary['detected_delay_ticks'],[40])
        self.assertEqual(summary['post_repeated_relevant_alarm']['count'],1)

    def test_threshold_sensitivity_is_monotone(self):
        values,_=c._stats(41000,'mean_strong',40,20)
        for v in values:
            a,b=c._alarm(v,.8),c._alarm(v,1.5)
            for key in a:self.assertFalse(b[key] and not a[key])

    def test_bad_configuration_rejected(self):
        with self.assertRaises(ValueError): c.generate('market',seed=1,reference_size=40,window_size=20)
        with self.assertRaises(ValueError): c.generate('stable',seed=1,reference_size=40,window_size=5)
        with self.assertRaises(ValueError): c._alarm({'variance_ratio':1,'relationship':{'status':'unavailable'},'mean_effect':0},0)


if __name__=='__main__':unittest.main()
